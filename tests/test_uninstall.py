"""skillstore/uninstall.py: summary, restore, removal of every trace, safety.

Everything runs in the temp HOME/USERPROFILE/CLAUDE_CONFIG_DIR and temp store from conftest.
Subprocesses (pip, pipx, uv, git, py), PATH lookups, the Windows user environment, prompts and
the system temp dir are all replaced by fakes here, so no test can touch the real machine.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from skillstore import links, uninstall

ROOT = Path(__file__).resolve().parent.parent


class Fakes:
    def __init__(self):
        self.calls: list[list[str]] = []
        self.responses: list[tuple[list[str], tuple[int, str]]] = []
        self.which: dict[str, str] = {}
        self.answers: list[str] = []
        self.prompts: list[str] = []

    def run(self, cmd, timeout=120):
        cmd = [str(c) for c in cmd]
        self.calls.append(cmd)
        for prefix, res in self.responses:
            if cmd[:len(prefix)] == prefix:
                return res
        return 1, ""

    def input(self, prompt):
        self.prompts.append(prompt)
        if not self.answers:
            raise EOFError
        return self.answers.pop(0)


class FakeUserEnv:
    def __init__(self, values=None):
        self.values = dict(values or {})

    def get(self, name):
        return (self.values[name], 2) if name in self.values else None

    def set(self, name, value, kind):
        self.values[name] = value

    def delete(self, name):
        del self.values[name]


@pytest.fixture(autouse=True)
def fakes(monkeypatch, tmp_path):
    f = Fakes()
    monkeypatch.setattr(uninstall, "_run", f.run)
    monkeypatch.setattr(uninstall, "_which", lambda n: f.which.get(n))
    monkeypatch.setattr(uninstall, "_input", f.input)
    monkeypatch.setattr(uninstall, "_user_env", lambda: None)
    systemp = tmp_path / "systemp"
    systemp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(systemp))
    monkeypatch.setenv("APPDATA", str(tmp_path / "home" / "AppData" / "Roaming"))
    f.systemp = systemp
    return f


def run(*args):
    return uninstall.main(list(args))


def snapshot(root: Path) -> dict:
    """Every path under root with its kind, link target or content, never following links."""
    out = {}
    stack = [root]
    while stack:
        d = stack.pop()
        for e in sorted(os.scandir(d), key=lambda e: e.name):
            p = Path(e.path)
            rel = p.relative_to(root).as_posix()
            if links.is_link(p) or uninstall._is_reparse_or_link(p):
                out[rel] = ("link", str(links.link_target(p)))
            elif e.is_dir(follow_symlinks=False):
                out[rel] = ("dir",)
                stack.append(p)
            else:
                out[rel] = ("file", p.read_bytes())
    return out


def make_checkout(tmp_path: Path, name: str, desc: str = "Use when testing a checkout skill.") -> Path:
    co = tmp_path / "checkouts" / name
    (co / "references").mkdir(parents=True)
    (co / "SKILL.md").write_text(f'---\nname: {name}\ndescription: {json.dumps(desc)}\n---\n\n# Body\n',
                                 encoding="utf-8")
    (co / "references" / "ref.md").write_text("# Ref\n", encoding="utf-8")
    (co / ".git").mkdir()
    (co / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    return co


def add_checkout_skill(env, tmp_path, name: str) -> Path:
    env.store.init()
    co = make_checkout(tmp_path, name)
    links.create_link(env.store.paths.skills / name, co)
    data = env.store.load()
    env.store.entry(data, name, create=True)["approved"] = True
    env.store.save(data)
    return co


def glink(env, name):
    rc, r, err = env.gjson("link", name)
    assert rc == 0, err


# ---------------------------------------------------------------------------------------
# summary and tokens
# ---------------------------------------------------------------------------------------
def test_token_figures_and_totals(env, capsys):
    env.add_skill("alpha", description="a" * 40, body="b" * 399)  # body = blank line + 399 chars: 10 / 100
    env.add_skill("beta", approved=False, description="c" * 41, body="d" * 7)  # 11 / ceil(8/4)=2
    st = uninstall.discover()
    by = {s.name: s for s in st.skills}
    assert (by["alpha"].listing_tokens, by["alpha"].body_tokens) == (10, 100)
    assert (by["beta"].listing_tokens, by["beta"].body_tokens) == (11, 2)
    assert by["alpha"].approved and not by["beta"].approved
    text = uninstall.format_summary(st)
    assert "Skills to be restored: 2 of 2" in text
    assert "listing 21 tokens" in text and "SKILL.md bodies 102 tokens" in text


def test_totals_exclude_skills_that_will_be_skipped(env):
    env.add_skill("alpha", description="a" * 40, body="b" * 399)
    env.add_skill("beta", description="c" * 80, body="d" * 80)
    (env.global_dir / "beta").mkdir(parents=True)  # foreign entry with the same name
    text = uninstall.format_summary(uninstall.discover())
    assert "Skills to be restored: 1 of 2" in text
    assert "listing 10 tokens" in text and "SKILL.md bodies 100 tokens" in text
    assert "will be skipped" in text


def test_description_parse_without_pyyaml(monkeypatch):
    monkeypatch.setitem(sys.modules, "yaml", None)  # import yaml -> ImportError
    assert uninstall._description('name: x\ndescription: "quoted: \\"v\\""\nother: 1') == 'quoted: "v"'
    assert uninstall._description("description: 'it''s'") == "it's"
    assert uninstall._description("description: >\n  folded one\n  two\nother: 1") == "folded one two"
    assert uninstall._description("description: plain\n  continued\n") == "plain continued"


def test_summary_shows_link_state_and_projects(env, tmp_path):
    env.add_skill("alpha")
    co = add_checkout_skill(env, tmp_path, "beta")
    glink(env, "alpha")
    env.write_manifest(["alpha", "beta"])
    env.run("sync")
    env.log_use("alpha", "2026-01-01T00:00:00Z")
    with open(env.store.paths.usage, "a") as fh:
        fh.write(json.dumps({"skill": "alpha", "project": str(env.project), "time": "2026-01-02T00:00:00Z"}) + "\n")
    st = uninstall.discover()
    by = {s.name: s for s in st.skills}
    assert by["alpha"].global_link is not None and by["alpha"].projects == [str(env.project)]
    assert by["beta"].external == Path(os.path.realpath(co)) and by["beta"].projects == [str(env.project)]
    text = uninstall.format_summary(st)
    assert "global + 1 project" in text and f"link to checkout {os.path.realpath(co)}" in text


# ---------------------------------------------------------------------------------------
# dry run
# ---------------------------------------------------------------------------------------
def full_setup(env, tmp_path, fakes):
    env.add_skill("alpha")
    co = add_checkout_skill(env, tmp_path, "beta")
    glink(env, "alpha")
    glink(env, "beta")
    env.write_manifest(["alpha"])
    env.run("init-project")
    env.run("sync")
    with open(env.store.paths.usage, "a") as fh:
        fh.write(json.dumps({"skill": "alpha", "project": str(env.project), "time": "2026-01-02T00:00:00Z"}) + "\n")
    settings = Path(os.environ["CLAUDE_CONFIG_DIR"]) / "settings.json"
    settings.write_text(json.dumps(MIXED_SETTINGS, indent=2))
    home = Path.home()
    (home / ".bashrc").write_text('export SKILL_STORE_HOME="$HOME/.skill-store"\nalias ll="ls -l"\n')
    venv = fakes.systemp / "skill-store-test-venv"
    venv.mkdir()
    (venv / "pyvenv.cfg").write_text("home = x\n")
    return co


MIXED_SETTINGS = {
    "model": "opus",
    "permissions": {"allow": ["Bash(ls:*)"]},
    "hooks": {
        "PostToolUse": [
            {"matcher": "Skill", "hooks": [
                {"type": "command", "command": "powershell.exe",
                 "args": ["-NoProfile", "-File", "C:/Users/me/src/claude-skill-store/hooks/skill-store-hook.ps1",
                          "log-usage"], "timeout": 10}]},
            {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo bash-ran"}]},
        ],
        "SessionStart": [
            {"hooks": [
                {"type": "command", "command": "skill-store sync --quiet --hook-output", "timeout": 60},
                {"type": "command", "command": "echo other-session-hook"}]},
        ],
        "Stop": [
            {"hooks": [{"type": "command",
                        "command": "\"$HOME/src/claude-skill-store/hooks/skill-store-hook.sh\" log-usage"}]},
        ],
        "Notification": [{"hooks": [{"type": "command", "command": "notify-send hi"}]}],
    },
}


def test_dry_run_changes_nothing(env, tmp_path, fakes, capsys):
    full_setup(env, tmp_path, fakes)
    before = snapshot(tmp_path)
    capsys.readouterr()
    assert run("--dry-run", "--restore") == 0
    out = capsys.readouterr().out
    assert snapshot(tmp_path) == before
    assert "dry run" in out.lower() and "nothing was changed" in out
    assert "beta: link" in out and "alpha: copy the store copy" in out
    assert f"delete {env.store_root}" in out
    assert not any(c[:3] == [sys.executable, "-m", "pip"] and "uninstall" in c for c in fakes.calls)


def test_dry_run_lists_package_actions_without_running_them(env, fakes, capsys):
    env.add_skill("alpha")
    fakes.responses.append(([sys.executable, "-m", "pip", "show", "claude-skill-store"],
                            (0, "Name: claude-skill-store\nLocation: /x\n")))
    assert run("--dry-run", "--no-restore") == 0
    out = capsys.readouterr().out
    assert "pip uninstall claude-skill-store" in out
    assert not any("uninstall" in c for c in fakes.calls for c in c)


# ---------------------------------------------------------------------------------------
# restore
# ---------------------------------------------------------------------------------------
def test_restore_copies_store_skills_and_relinks_checkouts(env, tmp_path, fakes, capsys):
    co = full_setup(env, tmp_path, fakes)
    co_before = snapshot(co)
    assert run("--yes", "--restore") == 0
    g = env.global_dir
    # alpha: a real, standalone directory (no longer a link), with its files
    assert (g / "alpha").is_dir() and not links.is_link(g / "alpha")
    assert (g / "alpha" / "SKILL.md").is_file() and (g / "alpha" / "references" / "notes.md").is_file()
    # beta: a link straight to the checkout, not to the (now deleted) store
    assert links.is_link(g / "beta")
    assert os.path.normcase(os.path.realpath(g / "beta")) == os.path.normcase(os.path.realpath(co))
    assert (g / "beta" / "SKILL.md").is_file()
    assert snapshot(co) == co_before  # the checkout survives untouched
    assert not env.store_root.exists()
    assert not os.path.lexists(env.skills_dir / "alpha")  # project link removed
    out = capsys.readouterr().out
    assert "Restored (2)" in out


def test_yes_without_restore_defaults_to_no(env, fakes, capsys):
    env.add_skill("alpha")
    glink(env, "alpha")
    assert run("--yes") == 0
    assert not os.path.lexists(env.global_dir / "alpha")
    assert not env.store_root.exists()
    assert fakes.prompts == []  # --yes never prompts


def test_interactive_restore_question_and_abort(env, fakes, capsys):
    env.add_skill("alpha")
    env.add_skill("beta")
    glink(env, "alpha")
    before = snapshot(env.tmp)
    fakes.answers = ["y", "n"]  # restore: yes; proceed: no
    assert run() == 1
    assert fakes.prompts[0].startswith(
        f"Restore all 2 cached skills into your global Claude skills ({env.global_dir})?")
    assert "[y/N]" in fakes.prompts[0]
    assert "Aborted: nothing was changed." in capsys.readouterr().out
    assert snapshot(env.tmp) == before


def test_foreign_same_name_entry_is_skipped(env, tmp_path, fakes, capsys):
    env.add_skill("alpha")
    env.add_skill("beta")
    glink(env, "beta")
    g = env.global_dir
    (g / "alpha").mkdir(parents=True)
    (g / "alpha" / "SKILL.md").write_text("mine")
    agents = tmp_path / "agents" / "find-skills"
    agents.mkdir(parents=True)
    (agents / "SKILL.md").write_text("vercel")
    links.create_link(g / "find-skills", agents)
    for d in ("synced", ".trash"):
        (g / d / "x").mkdir(parents=True)
        (g / d / "x" / "f.txt").write_text(d)
    foreign_before = {n: snapshot(g / n) for n in ("alpha", "synced", ".trash")}
    assert run("--yes", "--restore") == 0
    assert (g / "alpha" / "SKILL.md").read_text() == "mine"
    assert {n: snapshot(g / n) for n in foreign_before} == foreign_before
    assert links.is_link(g / "find-skills") and (agents / "SKILL.md").read_text() == "vercel"
    assert (g / "beta" / "SKILL.md").is_file() and not links.is_link(g / "beta")
    out = capsys.readouterr().out
    assert "alpha: not restored" in out and "not a skill-store link" in out


# ---------------------------------------------------------------------------------------
# settings.json
# ---------------------------------------------------------------------------------------
def test_strip_store_hooks_keeps_everything_else():
    new, removed = uninstall.strip_store_hooks(MIXED_SETTINGS)
    assert len(removed) == 3
    assert new["model"] == "opus" and new["permissions"] == MIXED_SETTINGS["permissions"]
    assert new["hooks"]["PostToolUse"] == [MIXED_SETTINGS["hooks"]["PostToolUse"][1]]
    assert new["hooks"]["SessionStart"] == [{"hooks": [{"type": "command", "command": "echo other-session-hook"}]}]
    assert "Stop" not in new["hooks"]
    assert new["hooks"]["Notification"] == MIXED_SETTINGS["hooks"]["Notification"]
    assert list(new) == list(MIXED_SETTINGS)  # key order kept
    assert MIXED_SETTINGS["hooks"]["Stop"]  # input not mutated


def test_settings_hooks_removed_with_backup(env, capsys):
    env.add_skill("alpha")
    settings = Path(os.environ["CLAUDE_CONFIG_DIR"]) / "settings.json"
    original = json.dumps(MIXED_SETTINGS, indent=4)
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(original)
    assert run("--yes") == 0
    data = json.loads(settings.read_text(encoding="utf-8"))  # valid JSON
    assert data == uninstall.strip_store_hooks(MIXED_SETTINGS)[0]
    backups = list(settings.parent.glob("settings.json.skill-store-backup-*"))
    assert len(backups) == 1 and backups[0].read_text() == original


def test_unreadable_settings_left_alone(env, capsys):
    settings = Path(os.environ["CLAUDE_CONFIG_DIR"]) / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text("{ not json skill-store")
    env.add_skill("alpha")
    assert run("--yes") == 0
    assert settings.read_text() == "{ not json skill-store"
    assert "check it for skill-store hooks yourself" in capsys.readouterr().out


# ---------------------------------------------------------------------------------------
# projects
# ---------------------------------------------------------------------------------------
def test_project_links_removed_committed_files_listed(env, tmp_path, capsys):
    env.add_skill("alpha")
    env.run("init-project")
    env.run("link", "alpha")
    (env.project / "CLAUDE.md").write_text("# Proj\n\n## Skills store: unlink safety (hard rule)\n")
    psettings = env.project / ".claude" / "settings.json"
    psettings.write_text(json.dumps({"hooks": {"SessionStart": [{"hooks": [
        {"type": "command", "command": "skill-store sync --quiet --hook-output"}]}]}}))
    gone = tmp_path / "deleted-worktree"
    with open(env.store.paths.usage, "a") as fh:
        for p in (env.project, gone):
            fh.write(json.dumps({"skill": "alpha", "project": str(p), "time": "2026-01-02T00:00:00Z"}) + "\n")
    ps_before = psettings.read_text()
    assert run("--yes") == 0
    assert not os.path.lexists(env.skills_dir / "alpha")
    for f in (env.project / ".claude" / "skills.manifest.json", env.skills_dir / ".gitignore",
              env.project / "CLAUDE.md"):
        assert f.is_file()
    assert psettings.read_text() == ps_before  # reported, never edited
    out = capsys.readouterr().out
    assert f"committed in {env.project}" in out and "skills.manifest.json" in out
    assert "Skills store: unlink safety" in out
    assert f"project settings with skill-store hooks (not edited): {psettings}" in out
    assert f"project not found: {gone}" in out
    assert "Find them with:" in out


def test_extra_project_flag(env, tmp_path):
    env.add_skill("alpha")
    env.run("link", "alpha")  # no usage records at all
    assert run("--yes", "--project", str(env.project)) == 0
    assert not os.path.lexists(env.skills_dir / "alpha")


def test_legacy_home_files_removed(env):
    env.add_skill("alpha")
    claude = Path.home() / ".claude"
    (claude / "skills").mkdir(parents=True)
    (claude / "skills.manifest.json").write_text('{"version": 1, "skills": ["alpha"]}')
    from skillstore.manifest import GITIGNORE_TEXT
    (claude / "skills" / ".gitignore").write_text(GITIGNORE_TEXT)
    assert run("--yes") == 0
    assert not (claude / "skills.manifest.json").exists()
    assert not (claude / "skills" / ".gitignore").exists()
    assert (claude / "skills").is_dir()


# ---------------------------------------------------------------------------------------
# store deletion safety
# ---------------------------------------------------------------------------------------
def test_store_delete_removes_inner_links_first_and_keeps_targets(env, tmp_path, capsys):
    env.add_skill("alpha")
    co = add_checkout_skill(env, tmp_path, "beta")
    outside = tmp_path / "outside"
    (outside / "deep").mkdir(parents=True)
    (outside / "deep" / "keep.txt").write_text("keep")
    (env.store_root / "build" / "beta").mkdir(parents=True)
    links.create_link(env.store_root / "build" / "beta" / "src", outside)
    co_before, out_before = snapshot(co), snapshot(outside)
    assert run("--yes", "--no-restore") == 0
    assert not env.store_root.exists()
    assert snapshot(co) == co_before and snapshot(outside) == out_before


def test_delete_tree_refuses_links(tmp_path):
    root = tmp_path / "d"
    (root / "sub").mkdir(parents=True)
    target = tmp_path / "target"
    target.mkdir()
    (target / "f.txt").write_text("x")
    links.create_link(root / "sub" / "l", target)
    with pytest.raises(RuntimeError, match="link"):
        uninstall.delete_tree(root)
    assert (target / "f.txt").read_text() == "x"
    assert [p.name for p in uninstall.find_links(root)] == ["l"]


def test_store_root_that_is_a_link_is_not_deleted(env, tmp_path, monkeypatch, capsys):
    env.add_skill("alpha")
    alias = tmp_path / "store-alias"
    links.create_link(alias, env.store_root)
    monkeypatch.setenv("SKILL_STORE_HOME", str(alias))
    before = snapshot(env.store_root)
    assert run("--yes") == 1
    assert snapshot(env.store_root) == before and links.is_link(alias)
    assert "itself a link" in capsys.readouterr().out


def test_never_deletes_the_checkout(env, monkeypatch, tmp_path):
    env.add_skill("alpha")
    monkeypatch.setattr(uninstall, "REPO_ROOT", env.store_root / "skills" / "alpha")
    assert run("--yes") == 1
    assert (env.store_root / "skills" / "alpha" / "SKILL.md").is_file()


# ---------------------------------------------------------------------------------------
# package, environment, profiles
# ---------------------------------------------------------------------------------------
def test_package_installs_and_test_venvs_removed(env, fakes, capsys):
    env.add_skill("alpha")
    fakes.which = {"pipx": "pipx", "uv": "uv"}
    fakes.responses += [
        ([sys.executable, "-m", "pip", "show", "claude-skill-store"],
         (0, "Name: claude-skill-store\nEditable project location: /src/claude-skill-store\n")),
        (["pipx", "list", "--short"], (0, "black 24.1\nclaude-skill-store 0.1.0\n")),
        (["uv", "tool", "list"], (0, "claude-skill-store v0.1.0\n- skill-store\nruff v0.5\n")),
        ([sys.executable, "-m", "pip", "uninstall"], (0, "ok")),
        (["pipx", "uninstall"], (0, "ok")),
        (["uv", "tool", "uninstall"], (0, "ok")),
    ]
    venv = fakes.systemp / "skill-store-test-venv"
    (venv / "Scripts").mkdir(parents=True)
    (venv / "pyvenv.cfg").write_text("home = x\n")
    src = fakes.systemp / "skill-store-src.abc123"
    src.mkdir()
    (src / "pyproject.toml").write_text("")
    unrelated = fakes.systemp / "other-venv"
    unrelated.mkdir()
    assert run("--yes") == 0
    assert [sys.executable, "-m", "pip", "uninstall", "-y", "claude-skill-store"] in fakes.calls
    assert ["pipx", "uninstall", "claude-skill-store"] in fakes.calls
    assert ["uv", "tool", "uninstall", "claude-skill-store"] in fakes.calls
    assert not any(c[-1] in ("black", "ruff") for c in fakes.calls)
    assert not venv.exists() and not src.exists() and unrelated.is_dir()


def test_temp_dir_without_pyvenv_cfg_is_left(env, fakes):
    env.add_skill("alpha")
    d = fakes.systemp / "skill-store-test-venv"
    d.mkdir()
    (d / "important.txt").write_text("x")
    assert run("--yes") == 0
    assert (d / "important.txt").is_file()


def test_windows_user_environment(env, monkeypatch, tmp_path):
    env.add_skill("alpha")
    scripts = Path(os.environ["APPDATA"]) / "Python" / "Python312" / "Scripts"
    scripts.mkdir(parents=True)
    (scripts / "skill-store.exe").write_text("")
    busy = Path(os.environ["APPDATA"]) / "Python" / "Python313" / "Scripts"
    busy.mkdir(parents=True)
    (busy / "black.exe").write_text("")
    fake = FakeUserEnv({"SKILL_STORE_HOME": "D:\\store", "SKILL_STORE_CMD": "x",
                        "Path": f"C:\\tools;{scripts};{busy}", "OTHER": "1"})
    monkeypatch.setattr(uninstall, "_user_env", lambda: fake)
    assert run("--yes") == 0
    assert fake.values == {"Path": f"C:\\tools;{busy}", "OTHER": "1"}


def test_shell_profile_lines_need_confirmation(env, fakes, capsys):
    env.add_skill("alpha")
    rc = Path.home() / ".bashrc"
    text = ('alias ll="ls -l"\n'
            'export SKILL_STORE_HOME="$HOME/.skill-store"\n'
            'claude-ss() { skill-store sync; claude "$@"; }\n'
            'export PATH="$HOME/.local/bin:$PATH"\n')
    rc.write_text(text)
    (Path.home() / ".local" / "bin").mkdir(parents=True)
    (Path.home() / ".local" / "bin" / "other-tool").write_text("")
    fakes.answers = ["n", "y", "n"]  # restore: no; proceed: yes; profile lines: no
    assert run() == 0
    assert rc.read_text() == text
    assert not env.store_root.exists()
    out = capsys.readouterr().out
    assert "lines kept (you chose to keep them)" in out and "SKILL_STORE_HOME" in out


def test_shell_profile_lines_removed_with_yes(env):
    env.add_skill("alpha")
    rc = Path.home() / ".bashrc"
    text = ('alias ll="ls -l"\n'
            'export SKILL_STORE_HOME="$HOME/.skill-store"\n'
            'claude-ss() { skill-store sync; claude "$@"; }\n'
            'export PATH="$HOME/.local/bin:$PATH"\n')
    rc.write_text(text)
    (Path.home() / ".local" / "bin").mkdir(parents=True)
    (Path.home() / ".local" / "bin" / "other-tool").write_text("")
    assert run("--yes") == 0
    assert rc.read_text() == 'alias ll="ls -l"\nexport PATH="$HOME/.local/bin:$PATH"\n'
    assert len(list(Path.home().glob(".bashrc.skill-store-backup-*"))) == 1


def test_readme_path_line_removed_when_local_bin_is_empty(env):
    env.add_skill("alpha")
    rc = Path.home() / ".bashrc"
    rc.write_text('export PATH="$HOME/.local/bin:$PATH"\n')
    (Path.home() / ".local" / "bin").mkdir(parents=True)
    (Path.home() / ".local" / "bin" / "skill-store").write_text("")
    assert run("--yes") == 0
    assert rc.read_text() == ""


# ---------------------------------------------------------------------------------------
# runs without the installed package and without PyYAML
# ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("how", ["file", "module"])
def test_runs_from_checkout_without_site_packages(how, tmp_path):
    cmd = [sys.executable, "-S"] + ([str(ROOT / "skillstore" / "uninstall.py")] if how == "file"
                                    else ["-m", "skillstore.uninstall"]) + ["--help"]
    res = subprocess.run(cmd, cwd=str(ROOT if how == "module" else tmp_path), capture_output=True,
                         text=True, timeout=60, stdin=subprocess.DEVNULL)
    assert res.returncode == 0, res.stderr
    assert "--dry-run" in res.stdout and "--no-restore" in res.stdout
