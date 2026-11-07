"""Global scope: ``--global`` links store skills into $CLAUDE_CONFIG_DIR/skills (~/.claude/skills).

Every test runs with HOME/USERPROFILE and CLAUDE_CONFIG_DIR inside a temp dir (conftest).
"""

import json
import os
from pathlib import Path

import pytest

from skillstore import links


def _tree(d: Path) -> list[str]:
    return sorted(p.relative_to(d).as_posix() for p in d.rglob("*"))


def _foreign_layout(env, tmp_path):
    """What a real ~/.claude/skills can hold that skill-store must never touch."""
    g = env.global_dir
    agents = tmp_path / "home" / ".agents" / "skills" / "vercel-thing"
    agents.mkdir(parents=True)
    (agents / "SKILL.md").write_text("vercel-owned")
    g.mkdir(parents=True, exist_ok=True)
    links.create_link(g / "vercel-thing", agents)          # another tool's junction/symlink
    (g / "synced" / "docx").mkdir(parents=True)              # claude.ai sync
    (g / "synced" / "docx" / "SKILL.md").write_text("synced")
    (g / ".trash" / "old").mkdir(parents=True)
    (g / ".trash" / "old" / "SKILL.md").write_text("trash")
    (g / "handmade").mkdir()                                  # a real skill dir
    (g / "handmade" / "SKILL.md").write_text("mine")
    return agents


def _assert_foreign_intact(env, agents):
    g = env.global_dir
    assert links.same_target(g / "vercel-thing", agents)
    assert (agents / "SKILL.md").read_text() == "vercel-owned"
    assert (g / "synced" / "docx" / "SKILL.md").read_text() == "synced"
    assert (g / ".trash" / "old" / "SKILL.md").read_text() == "trash"
    assert (g / "handmade" / "SKILL.md").read_text() == "mine"


# ---------------------------------------------------------------------------------------
# link / unlink / sync
# ---------------------------------------------------------------------------------------
def test_global_link_unlink_sync_idempotent(env):
    store_copy = env.add_skill("alpha")
    before = _tree(store_copy)
    rc, r, err = env.gjson("link", "alpha")
    assert rc == 0, err
    assert r["scope"] == "global" and r["state"] == "linked"
    lp = env.global_dir / "alpha"
    assert links.same_target(lp, env.store.paths.skills / "alpha")
    assert env.global_manifest() == {"version": 1, "skills": ["alpha"]}
    assert not (env.global_dir / ".gitignore").exists()             # never a .gitignore there
    assert not (env.global_dir.parent / "skills.manifest.json").exists()  # manifest is in the store
    assert not (env.project / ".claude").exists()                   # no project touched

    rc, r, _ = env.gjson("link", "alpha")
    assert rc == 0 and r["state"] == "already linked"
    assert env.global_manifest()["skills"] == ["alpha"]

    for _ in range(2):
        rc, r, _ = env.gjson("sync")
        assert rc == 0 and r["already"] == ["alpha"] and r["linked"] == [] and r["removed"] == []
    assert env.global_manifest() == {"version": 1, "skills": ["alpha"]}  # sync never writes it

    rc, r, _ = env.gjson("unlink", "alpha")
    assert rc == 0 and r["state"] == "unlinked" and r["removed_from_manifest"]
    assert not os.path.lexists(lp)
    assert _tree(store_copy) == before
    rc, r, _ = env.gjson("unlink", "alpha")
    assert rc == 0 and r["state"] == "not linked"

    # sync links what the manifest lists, and removes store links it no longer lists
    env.global_manifest_path.write_text(json.dumps({"version": 1, "skills": ["alpha"]}))
    rc, r, _ = env.gjson("sync")
    assert r["linked"] == ["alpha"]
    env.global_manifest_path.write_text(json.dumps({"version": 1, "skills": []}))
    rc, r, _ = env.gjson("sync")
    assert r["removed"] == ["alpha"] and not os.path.lexists(lp)
    assert _tree(store_copy) == before


def test_global_text_output(env):
    env.add_skill("alpha")
    rc, out, _ = env.run_raw("--global", "link", "alpha")
    assert rc == 0 and "linked: alpha" in out
    rc, out, _ = env.run_raw("sync", "--global")  # the flag also works after the command
    assert rc == 0 and "already linked: alpha" in out and "global scope" in out
    rc, out, _ = env.run_raw("--global", "status")
    assert rc == 0 and "scope:   global" in out and "linked:  1/8" in out


def test_global_dir_default_and_config_dir(env, tmp_path, monkeypatch):
    env.add_skill("alpha")
    monkeypatch.delenv("CLAUDE_CONFIG_DIR")
    assert env.gjson("link", "alpha")[0] == 0
    assert links.is_link(Path.home() / ".claude" / "skills" / "alpha")
    assert env.gjson("unlink", "alpha")[0] == 0

    cfg = tmp_path / "custom-config"  # missing: created on first link
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    rc, r, _ = env.gjson("link", "alpha")
    assert rc == 0 and Path(r["link"]) == cfg / "skills" / "alpha"
    assert links.is_link(cfg / "skills" / "alpha")
    assert not os.path.lexists(Path.home() / ".claude" / "skills" / "alpha")


def test_global_sync_without_manifest_creates_nothing(env):
    env.add_skill("alpha")
    rc, r, _ = env.gjson("sync")
    assert rc == 0 and "no manifest" in r["skipped"]
    assert not env.global_dir.exists() and not env.global_manifest_path.exists()
    rc, _, _ = env.run_raw("--global", "sync", "--quiet")
    assert rc == 0


# ---------------------------------------------------------------------------------------
# foreign entries
# ---------------------------------------------------------------------------------------
def test_global_foreign_entries_left_alone(env, tmp_path):
    agents = _foreign_layout(env, tmp_path)
    for n in ("alpha", "beta"):
        env.add_skill(n)
    assert env.gjson("link", "alpha")[0] == 0
    assert env.gjson("link", "beta")[0] == 0
    rc, r, _ = env.gjson("status")
    assert r["linked_count"] == 2
    assert {e["name"] for e in r["unmanaged_entries"]} == {"vercel-thing", "synced", "handmade"}

    env.global_manifest_path.write_text(json.dumps({"version": 1, "skills": ["beta"]}))
    rc, r, _ = env.gjson("sync")
    assert r["removed"] == ["alpha"] and r["already"] == ["beta"]
    _assert_foreign_intact(env, agents)

    rc, r, _ = env.gjson("prune", "--cap", "0")
    assert [e["skill"] for e in r["evicted"]] == ["beta"]
    _assert_foreign_intact(env, agents)

    assert env.gjson("link", "alpha")[0] == 0
    rc, r, _ = env.gjson("unlink", "--all")
    assert rc == 0 and r["removed"] == ["alpha"] and r["remaining_store_links"] == []
    _assert_foreign_intact(env, agents)


def test_global_name_clash_with_foreign_entry_refused(env, tmp_path):
    agents = _foreign_layout(env, tmp_path)
    env.add_skill("vercel-thing")
    env.add_skill("handmade")
    for name in ("vercel-thing", "handmade"):
        rc, _, err = env.run_raw("--global", "link", name)
        assert rc == 1 and "leaving it alone" in err
        rc, _, err = env.run_raw("--global", "unlink", name)
        assert rc == 1 and "refusing" in err
    assert not env.global_manifest_path.exists()
    env.global_manifest_path.write_text(json.dumps({"version": 1, "skills": ["vercel-thing", "handmade"]}))
    rc, r, _ = env.gjson("sync")
    assert sorted(r["conflicts"]) == ["handmade", "vercel-thing"]
    _assert_foreign_intact(env, agents)


def test_global_skills_dir_that_is_a_link_is_refused(env, tmp_path):
    env.add_skill("alpha")
    real = tmp_path / "dotfiles-skills"
    real.mkdir()
    env.global_dir.parent.mkdir(parents=True, exist_ok=True)
    links.create_link(env.global_dir, real)
    rc, _, err = env.run_raw("--global", "link", "alpha")
    assert rc == 1 and "is itself a link" in err
    assert list(real.iterdir()) == []


# ---------------------------------------------------------------------------------------
# approval, cap/LRU
# ---------------------------------------------------------------------------------------
def test_global_link_requires_approval(env):
    env.add_skill("alpha", approved=False)
    rc, _, err = env.run_raw("--global", "link", "alpha")
    assert rc == 1 and "skill-store approve alpha" in err
    assert not os.path.lexists(env.global_dir / "alpha")
    assert not env.global_manifest_path.exists()

    env.global_manifest_path.write_text(json.dumps({"version": 1, "skills": ["alpha"]}))
    rc, r, _ = env.gjson("sync")
    assert r["needs_approval"] == ["alpha"] and not os.path.lexists(env.global_dir / "alpha")

    assert env.run_raw("approve", "alpha")[0] == 0
    rc, r, _ = env.gjson("link", "alpha")
    assert rc == 0 and r["state"] == "linked"


def test_global_cap_and_lru(env):
    for n in ("a1", "a2", "a3"):
        env.add_skill(n)
    env.global_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    env.global_manifest_path.write_text(json.dumps({"version": 1, "cap": 2, "skills": []}))
    env.log_use("a1", "2026-11-05T10:00:00Z")
    env.log_use("a2", "2026-11-05T09:00:00Z")
    assert env.gjson("link", "a1")[0] == 0
    assert env.gjson("link", "a2")[0] == 0
    rc, r, _ = env.gjson("link", "a3")
    assert rc == 0 and [e["skill"] for e in r["evicted"]] == ["a2"]  # least recently used
    assert r["linked_count"] == 2 and r["cap"] == 2
    assert env.global_manifest()["skills"] == ["a1", "a3"]
    assert (env.store_root / "skills" / "a2" / "SKILL.md").is_file()

    # sync over cap links the most recently used and never writes the manifest
    env.global_manifest_path.write_text(json.dumps({"version": 1, "cap": 1, "skills": ["a1", "a2", "a3"]}))
    rc, r, _ = env.gjson("sync")
    assert r["already"] == ["a1"] and sorted(r["over_cap"]) == ["a2", "a3"]
    assert r["unlinked_over_cap"] == ["a3"]
    assert env.global_manifest()["skills"] == ["a1", "a2", "a3"]

    rc, r, _ = env.gjson("prune", "--cap", "0")
    assert [e["skill"] for e in r["evicted"]] == ["a1"] and r["linked_count"] == 0


def test_global_cap_independent_of_project(env):
    for n in ("p1", "p2", "g1"):
        env.add_skill(n)
    env.write_manifest([], cap=1)
    assert env.run("link", "p1")[0] == 0
    assert env.gjson("link", "g1")[0] == 0
    rc, r, _ = env.json("link", "p2")
    assert [e["skill"] for e in r["evicted"]] == ["p1"]   # the global link does not count here
    assert links.is_link(env.global_dir / "g1")
    assert env.global_manifest()["skills"] == ["g1"]


# ---------------------------------------------------------------------------------------
# a store skill that is itself a link to an external checkout
# ---------------------------------------------------------------------------------------
def test_global_link_to_store_link_to_checkout(env, tmp_path):
    env.store.init()
    checkout = tmp_path / "checkouts" / "repo" / "skills" / "ext-skill"
    (checkout / "references").mkdir(parents=True)
    (checkout / "SKILL.md").write_text(
        '---\nname: ext-skill\ndescription: "Use when testing a skill kept in a git checkout."\n---\n\n# Ext\n',
        encoding="utf-8")
    (checkout / "references" / "notes.md").write_text("# Notes\n\nShort.\n", encoding="utf-8")
    store_entry = env.store.paths.skills / "ext-skill"
    links.create_link(store_entry, checkout)
    before = _tree(checkout)
    assert env.run_raw("approve", "ext-skill")[0] == 0

    rc, r, err = env.gjson("link", "ext-skill")
    assert rc == 0 and r["state"] == "linked", err
    lp = env.global_dir / "ext-skill"
    assert links.same_target(lp, store_entry)          # global -> store entry (not the checkout)
    assert (lp / "SKILL.md").read_text(encoding="utf-8").startswith("---")
    rc, r, _ = env.gjson("link", "ext-skill")
    assert r["state"] == "already linked"
    rc, r, _ = env.gjson("sync")
    assert r["already"] == ["ext-skill"]
    rc, r, _ = env.gjson("status")
    by = {s["skill"]: s for s in r["skills"]}
    assert by["ext-skill"]["linked"] and r["linked_count"] == 1

    rc, r, _ = env.gjson("unlink", "--all")
    assert r["removed"] == ["ext-skill"]
    assert not os.path.lexists(lp)
    assert links.same_target(store_entry, checkout)    # the store's own link is untouched
    assert _tree(checkout) == before


# ---------------------------------------------------------------------------------------
# status from a project
# ---------------------------------------------------------------------------------------
def test_project_status_shows_global_links(env):
    env.add_skill("alpha")
    env.add_skill("beta")
    assert env.run("link", "beta")[0] == 0
    assert env.gjson("link", "alpha")[0] == 0
    rc, r, _ = env.json("status")
    assert rc == 0 and r["scope"] == "project"
    by = {s["skill"]: s for s in r["skills"]}
    assert by["alpha"]["linked_globally"] and not by["alpha"]["linked"]
    assert by["beta"]["linked"] and not by["beta"]["linked_globally"]
    assert r["global"]["linked"] == ["alpha"] and r["global"]["cap"] == 8
    assert r["linked_count"] == 1
    rc, out, _ = env.run("status")
    assert "global:  1/8 linked in" in out


# ---------------------------------------------------------------------------------------
# flags
# ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("argv", [
    ["--global", "--project", ".", "link", "alpha"],
    ["link", "alpha", "--global", "--project", "."],
    ["--project", ".", "status", "--global"],
    ["--project", ".", "migrate-global"],
    ["--global", "init-project"],
])
def test_global_and_project_rejected(env, argv):
    env.add_skill("alpha")
    with pytest.raises(SystemExit) as exc:  # argparse usage error
        env.run_raw(*argv)
    assert exc.value.code == 2
    err = env.capsys.readouterr().err
    assert "mutually exclusive" in err or "does not apply" in err
    assert not os.path.lexists(env.global_dir / "alpha")
    assert not os.path.lexists(env.project / ".claude")


def test_global_flag_is_harmless_on_store_wide_commands(env):
    env.add_skill("alpha", approved=False)
    assert env.run_raw("--global", "approve", "alpha")[0] == 0
    assert env.run_raw("--global", "check", "alpha")[0] == 0


# ---------------------------------------------------------------------------------------
# migration from the old "--project ~" workaround
# ---------------------------------------------------------------------------------------
def _old_workaround(env, *names):
    home = str(Path.home())
    for n in names:
        rc, out, err = env.run_raw("--project", home, "link", n)
        assert rc == 0, err
    return Path.home() / ".claude" / "skills.manifest.json"


def test_migration_detected_and_migrated(env):
    for n in ("alpha", "beta"):
        env.add_skill(n)
    legacy = _old_workaround(env, "alpha", "beta")
    gitignore = Path.home() / ".claude" / "skills" / ".gitignore"
    assert legacy.is_file() and gitignore.is_file()

    rc, r, _ = env.gjson("status")
    lg = r["legacy"]
    assert lg["pending"] == ["alpha", "beta"] and lg["same_skills_dir"]
    assert lg["commands"][0] == "skill-store migrate-global"
    assert any(str(legacy) in c for c in lg["commands"])
    assert any(str(gitignore) in c for c in lg["commands"])
    assert not any("unlink --all" in c for c in lg["commands"])  # would remove the global links
    rc, out, _ = env.run_raw("--global", "status")
    assert "old setup found" in out and "skill-store migrate-global" in out
    rc, out, _ = env.run("status")  # plain status in a project mentions it too
    assert "old setup found" in out

    rc, r, _ = env.gjson("sync")   # no global manifest yet: creates nothing, prints the commands
    assert "no manifest" in r["skipped"] and r["legacy"]["pending"] == ["alpha", "beta"]
    rc, out, _ = env.run_raw("--global", "sync")
    assert "skill-store migrate-global" in out
    rc, _, err = env.run_raw("--global", "sync", "--quiet")
    assert rc == 0 and "migrate-global" in err

    rc, r, _ = env.gjson("migrate-global")
    assert rc == 0
    assert [m["skill"] for m in r["migrated"]] == ["alpha", "beta"]
    assert all(m["state"] == "already linked" for m in r["migrated"])  # existing links adopted
    assert env.global_manifest()["skills"] == ["alpha", "beta"]
    assert r["legacy"]["pending"] == []
    assert "skill-store migrate-global" not in r["legacy"]["commands"]
    # nothing deleted except store links: the old files are still there for the user to remove
    assert legacy.is_file() and gitignore.is_file()
    for n in ("alpha", "beta"):
        assert links.same_target(env.global_dir / n, env.store.paths.skills / n)

    rc, r, _ = env.gjson("migrate-global")  # idempotent
    assert rc == 0 and r["migrated"] == []

    # once the global scope is in use, the home dir can no longer act as a project
    home = str(Path.home())
    for argv in (["sync"], ["unlink", "--all"], ["unlink", "alpha"], ["link", "alpha"], ["prune"],
                 ["init-project"]):
        rc, _, err = env.run_raw("--project", home, *argv)
        assert rc == 1 and "global skills directory" in err, argv
    assert links.is_link(env.global_dir / "alpha")

    # the printed cleanup works and ends the migration notice
    legacy.unlink()
    gitignore.unlink()
    rc, r, _ = env.gjson("status")
    assert r["legacy"] is None and r["linked_count"] == 2


def test_global_sync_keeps_unmigrated_legacy_links(env):
    for n in ("alpha", "beta"):
        env.add_skill(n)
    _old_workaround(env, "alpha")
    assert env.gjson("link", "beta")[0] == 0   # global scope started before migrating
    rc, r, _ = env.gjson("sync")
    assert r["legacy_kept"] == ["alpha"] and r["removed"] == []
    assert r["linked_count"] == 1
    assert links.is_link(env.global_dir / "alpha")
    rc, r, _ = env.gjson("prune", "--cap", "0")
    assert [e["skill"] for e in r["evicted"]] == ["beta"]
    assert links.is_link(env.global_dir / "alpha")


def test_migration_with_other_config_dir(env, tmp_path, monkeypatch):
    env.add_skill("alpha")
    _old_workaround(env, "alpha")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
    rc, r, _ = env.gjson("migrate-global")
    assert rc == 0 and r["migrated"] == [{"skill": "alpha", "state": "linked"}]
    assert links.is_link(tmp_path / "cfg" / "skills" / "alpha")
    lg = r["legacy"]
    assert not lg["same_skills_dir"]
    assert any(c.startswith("skill-store --project ") and c.endswith(" unlink --all") for c in lg["commands"])
    # that printed command removes only the old store links
    home = str(Path.home())
    rc, out, _ = env.run_raw("--project", home, "unlink", "--all")
    assert rc == 0 and not os.path.lexists(Path.home() / ".claude" / "skills" / "alpha")
    assert links.is_link(tmp_path / "cfg" / "skills" / "alpha")


def test_migrate_global_without_old_setup(env):
    env.store.init()
    rc, out, _ = env.run_raw("migrate-global")
    assert rc == 0 and "nothing to migrate" in out
    assert not env.global_manifest_path.exists()


def test_migrate_global_reports_unapproved(env):
    env.add_skill("alpha")
    _old_workaround(env, "alpha")
    data = env.store.load()
    data["skills"]["alpha"]["approved"] = False
    env.store.save(data)
    rc, r, _ = env.gjson("migrate-global")
    assert rc == 1 and "approve alpha" in r["failed"]["alpha"]
    assert links.is_link(env.global_dir / "alpha")   # left in place


def test_unreadable_legacy_manifest_is_kept_and_protected(env):
    for n in ("alpha", "beta", "gamma"):
        env.add_skill(n)
    legacy = _old_workaround(env, "alpha")
    assert env.gjson("link", "beta")[0] == 0
    legacy.write_text("{not json")                      # damaged old list
    damaged = legacy.read_text()

    rc, r, _ = env.gjson("status")
    lg = r["legacy"]
    assert lg["error"] and lg["commands"] == []         # no delete command for a damaged file
    rc, out, _ = env.run_raw("--global", "status")
    assert "can't be read" in out and "Delete" not in out and "rm --" not in out

    rc, _, err = env.run_raw("migrate-global")
    assert rc == 1 and "cannot migrate" in err
    assert legacy.read_text() == damaged

    # sync and prune keep every unlisted store link, since the old list can't be read
    env.global_manifest_path.write_text(json.dumps({"version": 1, "skills": ["beta", "gamma"]}))
    rc, r, _ = env.gjson("sync")
    assert r["legacy_kept"] == ["alpha"] and r["removed"] == [] and r["linked"] == ["gamma"]
    rc, r, _ = env.gjson("prune", "--cap", "0")
    assert sorted(e["skill"] for e in r["evicted"]) == ["beta", "gamma"]
    assert links.is_link(env.global_dir / "alpha")
    rc, _, err = env.run_raw("--global", "sync", "--quiet")
    assert rc == 0 and "can't be read" in err


def test_global_unlink_all_also_removes_unmigrated_links(env):
    """Documented: unlink --all removes every store link, whatever listed it."""
    for n in ("alpha", "beta"):
        env.add_skill(n)
    _old_workaround(env, "alpha")
    assert env.gjson("link", "beta")[0] == 0
    rc, r, _ = env.gjson("unlink", "--all")
    assert rc == 0 and sorted(r["removed"]) == ["alpha", "beta"]
    assert (env.store_root / "skills" / "alpha" / "SKILL.md").is_file()
