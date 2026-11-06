"""unlink --all: the AO --pre-remove step. Removes store links only; manifest untouched."""

import os
import shutil

from skillstore import links


def _store_files(env):
    return sorted(p.relative_to(env.store_root).as_posix() for p in env.store_root.rglob("*"))


def test_unlink_all_then_recursive_worktree_delete_keeps_store(env):
    for n in ("alpha", "beta"):
        env.add_skill(n)
    env.write_manifest(["alpha", "beta"])
    assert env.run("sync")[0] == 0
    (env.project / "src").mkdir()
    (env.project / "src" / "main.py").write_text("print(1)")
    before = _store_files(env)
    manifest_before = env.manifest()

    rc, r, _ = env.json("unlink", "--all")
    assert rc == 0 and sorted(r["removed"]) == ["alpha", "beta"]
    assert r["remaining_store_links"] == []
    assert env.manifest() == manifest_before  # manifest untouched
    assert not any(links.is_link(p) for p in env.skills_dir.iterdir())

    shutil.rmtree(env.project)  # what deleting the worktree does
    assert not env.project.exists()
    assert _store_files(env) == before
    for n in ("alpha", "beta"):
        assert (env.store_root / "skills" / n / "SKILL.md").is_file()
        assert (env.store_root / "skills" / n / "references" / "notes.md").is_file()


def test_unlink_all_is_idempotent_and_exits_0(env):
    rc, out, _ = env.run("unlink", "--all")  # no store, no .claude at all
    assert rc == 0 and "nothing to unlink" in out
    env.add_skill("alpha")
    env.write_manifest(["alpha"])
    env.run("sync")
    assert env.run("unlink", "--all")[0] == 0
    rc, r, _ = env.json("unlink", "--all")
    assert rc == 0 and r["removed"] == []
    assert env.json("sync")[1]["linked"] == ["alpha"]  # sync re-links from the manifest


def test_unlink_all_leaves_foreign_links_and_real_dirs(env, tmp_path):
    env.add_skill("alpha")
    env.write_manifest(["alpha"])
    env.run("sync")
    foreign = tmp_path / "agents" / "find-skills"
    foreign.mkdir(parents=True)
    (foreign / "SKILL.md").write_text("foreign")
    links.create_link(env.skills_dir / "find-skills", foreign)
    real = env.skills_dir / "own"
    real.mkdir()
    (real / "SKILL.md").write_text("own")
    rc, r, _ = env.json("unlink", "--all")
    assert rc == 0 and r["removed"] == ["alpha"]
    assert links.is_link(env.skills_dir / "find-skills")
    assert (foreign / "SKILL.md").read_text() == "foreign"
    assert (real / "SKILL.md").read_text() == "own"
    assert (env.skills_dir / ".gitignore").is_file()


def test_unlink_requires_name_or_all(env):
    env.store.init()
    assert env.run("unlink")[0] == 1
    assert env.run("unlink", "alpha", "--all")[0] == 1


def test_sync_without_manifest_creates_nothing(env):
    env.store.init()
    rc, _, _ = env.run("sync", "--quiet")
    assert rc == 0
    assert not os.path.exists(env.project / ".claude")
