"""check: admission rules."""

import json

import pytest

from skillstore.admission import check_skill


def _skill(tmp_path, desc_line='description: "Use when testing: a thing."', body="# B\n", name="demo"):
    d = tmp_path / name
    (d / "references").mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\n{desc_line}\n---\n\n{body}", encoding="utf-8")
    return d


def test_valid_skill_passes(tmp_path):
    r = check_skill(_skill(tmp_path), "demo")
    assert r.ok, r.errors


def test_single_quotes_accepted(tmp_path):
    assert check_skill(_skill(tmp_path, "description: 'Use when x: y.'"), "demo").ok


@pytest.mark.parametrize("line,needle", [
    ("description: Use when x happens.", "quoted"),
    ("description: Use when x: y", "not valid YAML"),
    ('description: "Use when working with demo"', "generic"),
    ("description: " + json.dumps("Use when " + "x" * 600), "500"),
    ("", "description"),
])
def test_description_rules(tmp_path, line, needle):
    r = check_skill(_skill(tmp_path, line), "demo")
    assert not r.ok
    assert any(needle in e for e in r.errors), r.errors


def test_name_mismatch(tmp_path):
    r = check_skill(_skill(tmp_path), "other")
    assert any("does not match" in e for e in r.errors)


def test_skill_md_token_limit(tmp_path):
    ok = check_skill(_skill(tmp_path, body="x" * (15_900)), "demo")
    assert ok.ok
    big = check_skill(_skill(tmp_path / "b", body="x" * 16_100), "demo")
    assert any("SKILL.md is" in e for e in big.errors)


def test_reference_limits(tmp_path):
    d = _skill(tmp_path)
    (d / "references" / "big.md").write_text("# Big\n\n## Table of Contents\n\n" + "y" * 100_100)
    (d / "references" / "long.md").write_text("# Long\n\n" + "line\n" * 150)
    (d / "references" / "short.md").write_text("# Short\n\n" + "line\n" * 50)
    (d / "references" / "toc.md").write_text("# T\n\n## Contents\n\n" + "line\n" * 300)
    r = check_skill(d, "demo")
    errs = "\n".join(r.errors)
    assert "references/big.md is" in errs
    assert "references/long.md has" in errs
    assert "short.md" not in errs and "toc.md" not in errs


def test_missing_skill_md(tmp_path):
    (tmp_path / "x").mkdir()
    assert not check_skill(tmp_path / "x").ok


def test_junk_is_warned(tmp_path):
    d = _skill(tmp_path)
    (d / "quality_report.json").write_text("{}")
    r = check_skill(d, "demo")
    assert r.ok and r.warnings


def test_check_cli_exit_codes(env):
    d = env.add_skill("alpha")
    assert env.run("check", "alpha")[0] == 0
    assert env.run("check", str(d))[0] == 0
    (d / "SKILL.md").write_text("---\nname: alpha\ndescription: unquoted\n---\n")
    rc, out, _ = env.run("check", "alpha")
    assert rc == 1 and "FAIL" in out
