"""link / unlink / approve: approval gate, admission gate, unlink never deletes."""

import json
import os

from skillstore import links


def test_link_requires_approval(env):
    env.add_skill("alpha", approved=False)
    rc, out, err = env.run("link", "alpha")
    assert rc == 1
    assert "skill-store approve alpha" in err
    assert not os.path.lexists(env.skills_dir / "alpha")

    rc, _, _ = env.run("approve", "alpha")
    assert rc == 0
    data = json.loads(env.store.paths.store_json.read_text())
    assert data["skills"]["alpha"]["approved"] is True
    assert data["skills"]["alpha"]["approved_at"]

    rc, out, _ = env.run("link", "alpha")
    assert rc == 0, out
    assert links.same_target(env.skills_dir / "alpha", env.store.paths.skills / "alpha")
    assert env.manifest()["skills"] == ["alpha"]
    gi = (env.skills_dir / ".gitignore").read_text().splitlines()
    assert "*" in gi and "!.gitignore" in gi


def test_relink_of_approved_skill_is_automatic(env):
    env.add_skill("alpha", approved=True)
    assert env.run("link", "alpha")[0] == 0
    assert env.run("unlink", "alpha")[0] == 0
    rc, out, _ = env.run("link", "alpha")
    assert rc == 0 and "linked" in out


def test_link_refuses_skill_failing_admission(env):
    d = env.add_skill("alpha", approved=True)
    (d / "SKILL.md").write_text("---\nname: alpha\ndescription: Use when x: y\n---\n\nbody\n")
    rc, _, err = env.run("link", "alpha")
    assert rc == 1 and "not valid YAML" in err
    assert not os.path.lexists(env.skills_dir / "alpha")


def test_link_missing_skill(env):
    env.store.init()
    rc, _, err = env.run("link", "ghost")
    assert rc == 1 and "not in the store" in err


def test_link_is_idempotent(env):
    env.add_skill("alpha")
    assert env.run("link", "alpha")[0] == 0
    rc, out, _ = env.run("link", "alpha")
    assert rc == 0 and "already linked" in out
    assert env.manifest()["skills"] == ["alpha"]


def test_link_refuses_name_taken_by_real_dir(env):
    env.add_skill("alpha")
    real = env.skills_dir / "alpha"
    real.mkdir(parents=True)
    (real / "SKILL.md").write_text("mine")
    rc, _, err = env.run("link", "alpha")
    assert rc == 1 and "leaving it alone" in err
    assert (real / "SKILL.md").read_text() == "mine"


def test_unlink_never_deletes_store_copy(env):
    store_copy = env.add_skill("alpha")
    before = sorted(p.relative_to(store_copy).as_posix() for p in store_copy.rglob("*"))
    env.run("link", "alpha")
    rc, out, _ = env.run("unlink", "alpha")
    assert rc == 0 and "unlinked" in out
    assert not os.path.lexists(env.skills_dir / "alpha")
    after = sorted(p.relative_to(store_copy).as_posix() for p in store_copy.rglob("*"))
    assert before == after and (store_copy / "SKILL.md").is_file()
    assert env.manifest()["skills"] == []


def test_unlink_refuses_real_directory(env):
    env.add_skill("alpha")
    env.write_manifest(["alpha"])
    real = env.skills_dir / "alpha"
    (real / "references").mkdir(parents=True)
    (real / "SKILL.md").write_text("project-owned")
    (real / "references" / "r.md").write_text("keep me")
    rc, _, err = env.run("unlink", "alpha")
    assert rc == 1 and "real directory" in err
    assert (real / "SKILL.md").read_text() == "project-owned"
    assert (real / "references" / "r.md").read_text() == "keep me"
    assert env.manifest()["skills"] == ["alpha"]  # nothing changed


def test_unlink_refuses_foreign_link(env, tmp_path):
    env.add_skill("alpha")
    foreign = tmp_path / "agents" / "alpha"
    foreign.mkdir(parents=True)
    (foreign / "SKILL.md").write_text("vercel")
    links.create_link(env.skills_dir / "alpha", foreign)
    rc, _, err = env.run("unlink", "alpha")
    assert rc == 1 and "not a link into the skill store" in err
    assert links.is_link(env.skills_dir / "alpha")
    assert (foreign / "SKILL.md").read_text() == "vercel"


def test_unlink_when_not_linked(env):
    env.add_skill("alpha")
    rc, out, _ = env.run("unlink", "alpha")
    assert rc == 0 and "not linked" in out


def test_invalid_names_rejected(env):
    env.store.init()
    for bad in ("../x", "Foo", "a/b", "x-", "a--b"):
        rc, _, err = env.run("link", bad)
        assert rc == 1 and "invalid skill name" in err


def test_approve_unknown_skill(env):
    env.store.init()
    rc, _, err = env.run("approve", "ghost")
    assert rc == 1 and "not in the store" in err
