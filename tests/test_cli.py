"""CLI plumbing: init, init-project, status, path resolution."""

import json
import os
import subprocess
import sys
from pathlib import Path

from skillstore.paths import resolve_project, resolve_store

ROOT = Path(__file__).resolve().parent.parent


def test_init_is_idempotent(env):
    rc, r, _ = env.json("init")
    assert rc == 0 and r["created"]
    for sub in ("skills", "build"):
        assert (env.store_root / sub).is_dir()
    assert json.loads((env.store_root / "store.json").read_text()) == {"version": 1, "skills": {}}
    rc, r, _ = env.json("init")
    assert rc == 0 and r["created"] == []


def test_init_project(env):
    rc, r, _ = env.json("init-project")
    assert rc == 0
    assert env.manifest() == {"version": 1, "skills": []}
    gi = (env.skills_dir / ".gitignore").read_text().splitlines()
    assert "*" in gi and "!.gitignore" in gi
    (env.skills_dir / ".gitignore").write_text("custom\n*\n")
    env.write_manifest(["x"])
    rc, r, _ = env.json("init-project")
    assert r["created"] == []
    assert env.manifest()["skills"] == ["x"]
    assert (env.skills_dir / ".gitignore").read_text() == "custom\n*\n"


def test_status(env, tmp_path):
    env.add_skill("alpha")
    env.add_skill("beta", approved=False)
    env.write_manifest(["alpha", "ghost"])
    env.run("sync")
    (env.skills_dir / "handmade").mkdir()
    rc, r, _ = env.json("status")
    assert rc == 0
    by = {s["skill"]: s for s in r["skills"]}
    assert by["alpha"]["linked"] and by["alpha"]["approved"] and by["alpha"]["in_manifest"]
    assert not by["beta"]["linked"] and not by["beta"]["approved"]
    assert r["missing_from_store"] == ["ghost"]
    assert r["unmanaged_entries"] == [{"name": "handmade", "kind": "directory"}]
    assert r["linked_count"] == 1 and r["cap"] == 8
    rc, out, _ = env.run("status")
    assert rc == 0 and "alpha" in out


def test_store_flag_after_subcommand(env, tmp_path):
    other = tmp_path / "other-store"
    rc, r, _ = env.json("init", "--store", str(other))
    assert rc == 0 and Path(r["store"]) == other
    assert (other / "store.json").is_file()


def test_resolution_order(tmp_path, monkeypatch):
    monkeypatch.setenv("SKILL_STORE_HOME", str(tmp_path / "env-store"))
    assert resolve_store() == (tmp_path / "env-store").absolute()
    assert resolve_store(str(tmp_path / "flag")) == (tmp_path / "flag").absolute()
    monkeypatch.delenv("SKILL_STORE_HOME")
    assert resolve_store() == (Path.home() / ".skill-store").absolute()
    assert Path.home() == tmp_path / "home"  # never the real home in tests

    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path / "cpd"))
    assert resolve_project() == (tmp_path / "cpd").absolute()
    assert resolve_project(str(tmp_path / "flag")) == (tmp_path / "flag").absolute()


def test_git_toplevel_is_project(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "sub").mkdir(parents=True)
    if subprocess.run(["git", "init", "-q", str(repo)], capture_output=True).returncode != 0:
        return
    got = resolve_project(cwd=repo / "sub")
    assert os.path.samefile(got, repo)


def test_module_entry_point(tmp_path):
    res = subprocess.run([sys.executable, "-m", "skillstore", "--version"], capture_output=True,
                         text=True, env=dict(os.environ, PYTHONPATH=str(ROOT)), cwd=str(tmp_path))
    assert res.returncode == 0 and "skill-store" in res.stdout
