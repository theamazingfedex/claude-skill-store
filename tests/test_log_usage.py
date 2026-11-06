"""log-usage: the PostToolUse hook entry point must always exit 0."""

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from skillstore import cli

ROOT = Path(__file__).resolve().parent.parent


def _run(monkeypatch, capsys, stdin: str, *args):
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    rc = cli.main(["log-usage", *args])
    out = capsys.readouterr()
    return rc, out.out, out.err


def _usage(env):
    return [json.loads(x) for x in env.store.paths.usage.read_text().splitlines() if x.strip()]


def test_logs_skill_call_and_updates_last_used(env, monkeypatch, capsys):
    env.add_skill("alpha")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(env.project))
    payload = {"session_id": "sess-1", "cwd": "/x", "hook_event_name": "PostToolUse",
               "tool_name": "Skill", "tool_input": {"skill": "alpha"}}
    rc, out, err = _run(monkeypatch, capsys, json.dumps(payload))
    assert rc == 0 and out == "" and err == ""
    recs = _usage(env)
    assert len(recs) == 1
    assert recs[0]["skill"] == "alpha" and recs[0]["session"] == "sess-1"
    assert recs[0]["project"] == str(env.project) and recs[0]["time"].endswith("Z")
    # store.json is folded in on the next command that writes it, not by the hook
    env.run("approve", "alpha")
    assert env.store.load()["skills"]["alpha"]["last_used"] == recs[0]["time"]


def test_hook_never_writes_store_json(env, monkeypatch, capsys):
    """Parallel hooks must not read-modify-write store.json (lost updates vs approve/build)."""
    env.add_skill("alpha", approved=False)
    before = env.store.paths.store_json.read_bytes()
    stale = env.store.load()  # what a racing hook would have loaded
    for _ in range(3):
        rc, _, _ = _run(monkeypatch, capsys, json.dumps({"tool_name": "Skill", "tool_input": {"skill": "alpha"}}))
        assert rc == 0
    assert env.store.paths.store_json.read_bytes() == before
    env.run("approve", "alpha")  # an approve interleaved with hooks survives them
    _run(monkeypatch, capsys, json.dumps({"tool_name": "Skill", "tool_input": {"skill": "alpha"}}))
    data = env.store.load()
    assert data["skills"]["alpha"]["approved"] is True
    assert stale["skills"]["alpha"]["approved"] is False
    rc, r, _ = env.json("status")
    assert r["skills"][0]["last_used"] is not None  # recency still comes from usage.jsonl


def test_command_form_and_unknown_skill(env, monkeypatch, capsys):
    env.store.init()
    payload = {"tool_name": "Skill", "tool_input": {"command": "/some-plugin:thing arg1"}}
    rc, _, _ = _run(monkeypatch, capsys, json.dumps(payload))
    assert rc == 0
    assert _usage(env)[0]["skill"] == "some-plugin:thing"
    assert env.store.load()["skills"] == {}  # unknown skills are logged, not registered


@pytest.mark.parametrize("stdin", ["", "not json", "[1,2]", '{"tool_name": "Read"}',
                                   '{"tool_name": "Skill"}', '{"tool_name": "Skill", "tool_input": {"skill": 5}}'])
def test_garbage_and_irrelevant_input_exit_0(env, monkeypatch, capsys, stdin):
    env.store.init()
    rc, out, _ = _run(monkeypatch, capsys, stdin)
    assert rc == 0 and out == ""
    assert _usage(env) == []


def test_no_store_exit_0_and_creates_nothing(env, monkeypatch, capsys):
    rc, _, _ = _run(monkeypatch, capsys, json.dumps({"tool_name": "Skill", "tool_input": {"skill": "a"}}))
    assert rc == 0
    assert not env.store_root.exists()


def test_corrupt_store_json_exit_0(env, monkeypatch, capsys):
    env.store.init()
    env.store.paths.store_json.write_text("{broken")
    rc, _, _ = _run(monkeypatch, capsys, json.dumps({"tool_name": "Skill", "tool_input": {"skill": "a"}}))
    assert rc == 0


def test_bad_arguments_exit_0(env, monkeypatch, capsys):
    rc, _, _ = _run(monkeypatch, capsys, "", "--no-such-flag")
    assert rc == 0


def test_subprocess_exit_0(env, tmp_path):
    env.add_skill("alpha")
    payload = json.dumps({"tool_name": "Skill", "tool_input": {"skill": "alpha"}})
    envvars = dict(os.environ, PYTHONPATH=str(ROOT))
    for stdin in (payload, "garbage"):
        res = subprocess.run([sys.executable, "-m", "skillstore", "log-usage"], input=stdin,
                             text=True, capture_output=True, env=envvars, cwd=str(tmp_path))
        assert res.returncode == 0, res.stderr
    assert [r["skill"] for r in _usage(env)] == ["alpha"]


class _NoRead(io.StringIO):
    def read(self, *a):
        raise AssertionError("stdin must not be read for a non-log-usage command")


def test_skill_named_log_usage_is_not_the_hook(env, monkeypatch):
    """`log-usage` is a valid skill name: link/approve/unlink/check of it must run normally."""
    monkeypatch.setattr(sys, "stdin", _NoRead())
    env.add_skill("log-usage", approved=False)
    rc, _, err = env.run("link", "log-usage")
    assert rc == 1 and "skill-store approve log-usage" in err  # real error, not a silent 0
    assert env.run("approve", "log-usage")[0] == 0
    assert env.store.load()["skills"]["log-usage"]["approved"] is True
    rc, out, _ = env.run("link", "log-usage")
    assert rc == 0 and "linked" in out
    assert (env.skills_dir / "log-usage" / "SKILL.md").is_file()
    assert env.run("check", "log-usage")[0] == 0
    rc, out, _ = env.run("unlink", "log-usage")
    assert rc == 0 and "unlinked" in out
    assert _usage(env) == []


def test_project_named_log_usage_is_not_the_hook(env, monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(sys, "stdin", _NoRead())
    env.store.init()
    rc = cli.main(["--project", str(tmp_path / "log-usage"), "link", "ghost"])
    assert rc == 1 and "not in the store" in capsys.readouterr().err
    rc = cli.main(["--project", "log-usage", "link", "ghost"])
    assert rc == 1


def test_log_usage_after_global_options(env, monkeypatch, capsys):
    env.add_skill("alpha")
    rc, _, _ = _run(monkeypatch, capsys, json.dumps({"tool_name": "Skill", "tool_input": {"skill": "alpha"}}))
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"tool_name": "Skill", "tool_input": {"skill": "alpha"}})))
    rc = cli.main(["--store", str(env.store_root), "log-usage"])
    assert rc == 0
    assert [r["skill"] for r in _usage(env)] == ["alpha", "alpha"]
    rc = cli.main(["--store", str(env.store_root), "log-usage", "--bogus"])
    assert rc == 0
