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
    assert env.store.load()["skills"]["alpha"]["last_used"] == recs[0]["time"]


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
