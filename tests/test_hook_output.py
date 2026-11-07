"""sync --hook-output: the SessionStart hook entry point.

It prints one Claude Code hook JSON object when sync created links (project or global scope), and
nothing otherwise. It always exits 0. HOME/USERPROFILE/CLAUDE_CONFIG_DIR are temp dirs (conftest).
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from skillstore import links

ROOT = Path(__file__).resolve().parent.parent


def _hook_json(out: str) -> dict:
    """stdout must be exactly one JSON object and nothing else."""
    assert out.strip(), "expected hook JSON on stdout"
    assert out.count("\n") == 1 and out.endswith("\n"), "expected one line"
    obj = json.loads(out)
    assert isinstance(obj, dict)
    return obj


def _assert_reload_message(obj: dict, names):
    assert set(obj) == {"systemMessage", "hookSpecificOutput"}
    msg = obj["systemMessage"]
    assert "/reload-skills" in msg
    for n in names:
        assert n in msg
    hso = obj["hookSpecificOutput"]
    assert hso["hookEventName"] == "SessionStart"
    assert hso["reloadSkills"] is True
    assert "/reload-skills" in hso["additionalContext"]
    for n in names:
        assert n in hso["additionalContext"]


def test_project_links_created_emit_message(env):
    env.add_skill("alpha")
    env.add_skill("beta")
    env.write_manifest(["alpha", "beta"])
    rc, out, _ = env.run("sync", "--quiet", "--hook-output")
    assert rc == 0
    obj = _hook_json(out)
    _assert_reload_message(obj, ["alpha", "beta"])
    assert "this checkout" in obj["systemMessage"]
    for n in ("alpha", "beta"):
        assert links.same_target(env.skills_dir / n, env.store.paths.skills / n)


def test_hook_output_implies_quiet(env):
    env.add_skill("alpha")
    env.write_manifest(["alpha"])
    rc, out, _ = env.run("sync", "--hook-output")
    assert rc == 0
    _assert_reload_message(_hook_json(out), ["alpha"])


def test_silent_when_nothing_changed(env):
    env.add_skill("alpha")
    env.write_manifest(["alpha"])
    env.run("sync")
    rc, out, err = env.run("sync", "--quiet", "--hook-output")
    assert (rc, out, err) == (0, "", "")


def test_silent_without_manifest(env):
    env.add_skill("alpha")
    rc, out, err = env.run("sync", "--quiet", "--hook-output")
    assert (rc, out, err) == (0, "", "")
    assert not os.path.lexists(env.skills_dir)


def test_silent_without_store(env):
    env.write_manifest(["alpha"])
    rc, out, _ = env.run("sync", "--quiet", "--hook-output")
    assert (rc, out) == (0, "")


def test_silent_when_only_removals(env):
    env.add_skill("alpha")
    env.add_skill("beta")
    env.write_manifest(["alpha", "beta"])
    env.run("sync")
    env.write_manifest(["alpha"])
    rc, out, _ = env.run("sync", "--quiet", "--hook-output")
    assert (rc, out) == (0, "")
    assert not os.path.lexists(env.skills_dir / "beta")


def test_silent_when_nothing_linkable(env):
    env.add_skill("alpha", approved=False)
    env.write_manifest(["alpha", "ghost"])
    rc, out, err = env.run("sync", "--quiet", "--hook-output")
    assert (rc, out) == (0, "")
    assert "needs approval" in err and "not in store" in err  # notes stay on stderr


def test_relinked_counts_as_created(env):
    env.add_skill("alpha")
    env.add_skill("beta")
    env.write_manifest(["alpha"])
    env.skills_dir.mkdir(parents=True, exist_ok=True)
    links.create_link(env.skills_dir / "alpha", env.store.paths.skills / "beta")  # wrong target
    rc, out, _ = env.run("sync", "--quiet", "--hook-output")
    assert rc == 0
    _assert_reload_message(_hook_json(out), ["alpha"])
    assert links.same_target(env.skills_dir / "alpha", env.store.paths.skills / "alpha")


def test_global_links_created_emit_message(env):
    env.add_skill("alpha")
    env.global_manifest_path.write_text(json.dumps({"version": 1, "skills": ["alpha"]}))
    rc, out, _ = env.run_raw("--global", "sync", "--quiet", "--hook-output")
    assert rc == 0
    obj = _hook_json(out)
    _assert_reload_message(obj, ["alpha"])
    assert "global skills" in obj["systemMessage"]
    assert links.same_target(env.global_dir / "alpha", env.store.paths.skills / "alpha")
    # second run: nothing changed, nothing printed
    rc, out, err = env.run_raw("--global", "sync", "--quiet", "--hook-output")
    assert (rc, out, err) == (0, "", "")


def test_global_silent_without_manifest(env):
    env.add_skill("alpha")
    rc, out, _ = env.run_raw("--global", "sync", "--quiet", "--hook-output")
    assert (rc, out) == (0, "")
    assert not os.path.lexists(env.global_dir / "alpha")


def test_json_flag_does_not_add_stdout(env):
    env.add_skill("alpha")
    env.write_manifest(["alpha"])
    rc, out, _ = env.run("sync", "--quiet", "--hook-output", "--json")
    assert rc == 0
    _assert_reload_message(_hook_json(out), ["alpha"])
    rc, out, _ = env.run("sync", "--quiet", "--hook-output", "--json")
    assert (rc, out) == (0, "")


def test_many_names_are_truncated(env):
    names = [f"skill-{i:02d}" for i in range(12)]
    for n in names:
        env.add_skill(n)
    env.write_manifest(names, cap=20)
    rc, out, _ = env.run("sync", "--quiet", "--hook-output")
    assert rc == 0
    obj = _hook_json(out)
    assert "and 2 more" in obj["systemMessage"]
    assert len(obj["systemMessage"]) < 10_000


@pytest.mark.parametrize("argv", [
    ["sync", "--quiet", "--hook-output", "--bogus"],
    ["--global", "--project", ".", "sync", "--quiet", "--hook-output"],
    ["--store", "x", "sync", "--hook-output", "--cap", "3"],
    ["sync", "--hook", "--bogus"],  # argparse abbreviation of --hook-output
    ["sync", "--ho", "--bogus"],
])
def test_bad_arguments_still_exit_0(env, argv):
    rc, out, _ = env.run_raw(*argv)
    assert (rc, out) == (0, "")


def test_abbreviated_flag_emits_message(env):
    env.add_skill("alpha")
    env.write_manifest(["alpha"])
    rc, out, _ = env.run("sync", "--hook")
    assert rc == 0
    _assert_reload_message(_hook_json(out), ["alpha"])


def test_errors_exit_0_and_print_no_json(env):
    env.add_skill("alpha")
    env.write_manifest(["alpha"])
    # .claude/skills is a file: sync can't create links there
    env.skills_dir.parent.mkdir(parents=True, exist_ok=True)
    env.skills_dir.write_text("not a dir")
    rc, out, err = env.run("sync", "--quiet", "--hook-output")
    assert (rc, out) == (0, "")
    assert err


def test_unreadable_manifest_exits_0(env):
    env.add_skill("alpha")
    p = env.project / ".claude" / "skills.manifest.json"
    p.parent.mkdir(parents=True)
    p.write_text("{not json")
    rc, out, err = env.run("sync", "--quiet", "--hook-output")
    assert (rc, out) == (0, "")
    assert err


def test_plain_quiet_sync_unchanged(env):
    env.add_skill("alpha")
    env.write_manifest(["alpha"])
    rc, out, err = env.run("sync", "--quiet")
    assert (rc, out, err) == (0, "", "")
    assert links.same_target(env.skills_dir / "alpha", env.store.paths.skills / "alpha")


def test_subprocess_stdout_is_valid_json(env, tmp_path):
    """The real process: stdout bytes are a single ASCII JSON object, exit code 0."""
    env.add_skill("alpha")
    env.write_manifest(["alpha"])
    proc = subprocess.run(
        [sys.executable, "-m", "skillstore", "--project", str(env.project), "sync", "--quiet",
         "--hook-output"],
        cwd=str(ROOT), stdin=subprocess.DEVNULL, capture_output=True, env=os.environ.copy(),
        timeout=60)
    assert proc.returncode == 0, proc.stderr
    _assert_reload_message(_hook_json(proc.stdout.decode("ascii").replace("\r\n", "\n")), ["alpha"])
