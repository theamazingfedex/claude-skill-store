"""Test isolation.

Every test runs with HOME, USERPROFILE and SKILL_STORE_HOME redirected into its own temp
dir, CLAUDE_PROJECT_DIR unset, and ``claude plugin validate`` disabled. A session-level guard
also snapshots the *real* ``~/.claude/skills``, ``~/.agents`` and ``~/.skill-store`` before the
run and fails the session if any of them changed.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from skillstore import cli, postprocess  # noqa: E402
from skillstore.store import Store  # noqa: E402

_REAL_HOME = Path(os.path.expanduser("~"))
_GUARDED = [_REAL_HOME / ".claude" / "skills", _REAL_HOME / ".agents", _REAL_HOME / ".skill-store"]


def _snapshot():
    snap = {}
    for p in _GUARDED:
        if os.path.lexists(p):
            try:
                snap[str(p)] = sorted(os.listdir(p))
            except OSError:
                snap[str(p)] = "<unreadable>"
        else:
            snap[str(p)] = None
    return snap


def pytest_sessionstart(session):
    session.config._real_home_snapshot = _snapshot()


def pytest_sessionfinish(session, exitstatus):
    before = getattr(session.config, "_real_home_snapshot", None)
    if before is not None and before != _snapshot():
        sys.stderr.write("\nFATAL: the real ~/.claude/skills, ~/.agents or ~/.skill-store changed during tests\n")
        session.exitstatus = 3


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("SKILL_STORE_HOME", str(tmp_path / "store"))
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.delenv("SKILL_STORE_SKILL_SEEKERS", raising=False)
    monkeypatch.setattr(postprocess, "find_claude", lambda: None)
    assert Path.home() == home, "HOME redirect failed; refusing to run"
    return home


class Env:
    def __init__(self, tmp_path: Path, capsys):
        self.tmp = tmp_path
        self.store_root = tmp_path / "store"
        self.project = tmp_path / "project"
        self.project.mkdir()
        self.capsys = capsys
        self.store = Store(self.store_root)

    @property
    def skills_dir(self) -> Path:
        return self.project / ".claude" / "skills"

    def run(self, *args: str):
        self.capsys.readouterr()
        rc = cli.main(["--project", str(self.project), *args])
        out = self.capsys.readouterr()
        return rc, out.out, out.err

    def json(self, *args: str):
        rc, out, err = self.run(*args, "--json")
        return rc, (json.loads(out) if out.strip() else None), err

    def manifest(self) -> dict:
        return json.loads((self.project / ".claude" / "skills.manifest.json").read_text())

    def write_manifest(self, skills, cap=None):
        d = {"version": 1, "skills": list(skills)}
        if cap is not None:
            d["cap"] = cap
        p = self.project / ".claude" / "skills.manifest.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(d))

    def add_skill(self, name: str, approved: bool = True, description: str | None = None,
                  body: str = "# Skill\n\nBody.\n") -> Path:
        """Put a valid, admissible skill into the store and register it."""
        self.store.init()
        d = self.store.paths.skills / name
        (d / "references").mkdir(parents=True, exist_ok=True)
        desc = description or f"Use when testing {name}: a fixture skill."
        (d / "SKILL.md").write_text(f'---\nname: {name}\ndescription: {json.dumps(desc)}\n---\n\n{body}',
                                    encoding="utf-8")
        (d / "references" / "notes.md").write_text("# Notes\n\nShort reference.\n", encoding="utf-8")
        data = self.store.load()
        e = self.store.entry(data, name, create=True)
        e["approved"] = approved
        self.store.save(data)
        return d

    def log_use(self, name: str, time: str):
        with open(self.store.paths.usage, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"skill": name, "session": "s", "project": None, "time": time}) + "\n")


@pytest.fixture
def env(tmp_path, capsys):
    return Env(tmp_path, capsys)
