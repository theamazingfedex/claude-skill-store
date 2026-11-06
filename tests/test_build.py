"""build: the skill-seekers wrapper, driven against a mock skill-seekers (no network)."""

import json
from pathlib import Path

import pytest

from skillstore import admission, links

MOCK = Path(__file__).resolve().parent / "mock_skill_seekers.py"
DESC = "Use when importing example assets: suffixes, import options and retargeting."


@pytest.fixture
def recipe(tmp_path, monkeypatch):
    monkeypatch.setenv("SKILL_STORE_SKILL_SEEKERS", str(MOCK))
    r = {
        "recipe_version": 1,
        "name": "demo-skill",
        "description": DESC,
        "source": "https://docs.example.invalid/en/1.0/",
        "version": "1.0",
        "pinned_url": "https://docs.example.invalid/en/1.0/index.html",
        "skill_seekers": {"version": "3.1.3", "config": {
            "base_url": "https://docs.example.invalid/en/1.0/",
            "start_urls": ["https://docs.example.invalid/en/1.0/index.html"],
            "selectors": {"main_content": "div[role='main']"},
            "categories": {"importing": ["import"]},
        }},
    }
    p = tmp_path / "recipe.json"
    p.write_text(json.dumps(r))
    return p


def _calls(env):
    f = env.store.paths.build / "demo-skill" / "mock_calls.jsonl"
    return [json.loads(x) for x in f.read_text().splitlines()]


def test_build_end_to_end(env, recipe):
    env.store.init()
    rc, out, err = env.run("build", "demo-skill", "--recipe", str(recipe))
    assert rc == 0, err + out
    skill = env.store.paths.skills / "demo-skill"
    assert admission.check_skill(skill, "demo-skill").ok
    assert not (skill / "quality_report.json").exists()
    e = env.store.load()["skills"]["demo-skill"]
    assert e["approved"] is False and e["built_at"] and e["version"] == "1.0"
    assert e["source"] == "https://docs.example.invalid/en/1.0/"
    bdir = env.store.paths.build / "demo-skill"
    cfg = json.loads((bdir / "config.json").read_text())
    assert cfg["name"] == "demo-skill" and cfg["sources"][0]["base_url"] == cfg["base_url"]
    assert (bdir / "recipe.json").is_file() and (bdir / "build.log").is_file()
    call = _calls(env)[0]
    assert Path(call["cwd"]).resolve() == bdir.resolve()
    assert "--fresh" not in call["argv"] and "--skip-scrape" not in call["argv"]
    assert "next: skill-store approve demo-skill" in out
    # never-approved: link refuses
    assert env.run("link", "demo-skill")[0] == 1


def test_rebuild_uses_fresh_keeps_approval_and_links(env, recipe):
    env.store.init()
    assert env.run("build", "demo-skill", "--recipe", str(recipe))[0] == 0
    env.run("approve", "demo-skill")
    assert env.run("link", "demo-skill")[0] == 0
    lp = env.skills_dir / "demo-skill"

    assert env.run("build", "demo-skill", "--recipe", str(recipe))[0] == 0
    assert "--fresh" in _calls(env)[-1]["argv"]
    assert env.store.load()["skills"]["demo-skill"]["approved"] is True
    assert links.is_link(lp) and (lp / "SKILL.md").is_file()

    assert env.run("build", "demo-skill", "--recipe", str(recipe), "--skip-scrape")[0] == 0
    assert "--skip-scrape" in _calls(env)[-1]["argv"]
    assert (env.store.paths.build / "demo-skill" / "previous" / "SKILL.md").is_file()


def test_skip_scrape_without_cache_fails(env, recipe):
    env.store.init()
    rc, _, err = env.run("build", "demo-skill", "--recipe", str(recipe), "--skip-scrape")
    assert rc == 1 and "no cached pages" in err


def test_wrong_skill_seekers_version(env, recipe, monkeypatch):
    env.store.init()
    monkeypatch.setenv("MOCK_SS_VERSION", "3.2.0")
    rc, _, err = env.run("build", "demo-skill", "--recipe", str(recipe))
    assert rc == 1 and "3.1.3 is required" in err
    rc, _, _ = env.run("build", "demo-skill", "--recipe", str(recipe), "--any-skill-seekers-version")
    assert rc == 0


def test_silent_no_references_is_an_error(env, recipe, monkeypatch):
    env.store.init()
    monkeypatch.setenv("MOCK_SS_NO_REFS", "1")
    rc, _, err = env.run("build", "demo-skill", "--recipe", str(recipe))
    assert rc == 1 and "no reference files" in err
    assert not (env.store.paths.skills / "demo-skill").exists()


def test_skill_seekers_failure(env, recipe, monkeypatch):
    env.store.init()
    monkeypatch.setenv("MOCK_SS_FAIL", "1")
    rc, _, err = env.run("build", "demo-skill", "--recipe", str(recipe))
    assert rc == 1 and "exit code 1" in err


def test_recipe_name_mismatch(env, recipe):
    env.store.init()
    rc, _, err = env.run("build", "other-skill", "--recipe", str(recipe))
    assert rc == 1 and "not 'other-skill'" in err


def test_postprocess_command_reruns_from_build_output(env, recipe):
    env.store.init()
    env.run("build", "demo-skill", "--recipe", str(recipe))
    rc, out, err = env.run("postprocess", "demo-skill")
    assert rc == 0, err
    assert admission.check_skill(env.store.paths.skills / "demo-skill", "demo-skill").ok


def test_sources_only_config_is_made_hybrid():
    from skillstore.build import hybrid_config
    cfg, warns = hybrid_config({"sources": [{"type": "documentation", "base_url": "u", "max_pages": 3}]},
                               "n", "d")
    assert cfg["base_url"] == "u" and cfg["max_pages"] == 3 and cfg["name"] == "n"
    assert any("categories" in w for w in warns)
