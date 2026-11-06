"""sync: idempotent, only touches store links, never fails a session start."""

import os

from skillstore import links


def test_sync_creates_links_and_is_idempotent(env):
    env.add_skill("alpha")
    env.add_skill("beta")
    env.write_manifest(["alpha", "beta"])
    rc, r, _ = env.json("sync")
    assert rc == 0
    assert sorted(r["linked"]) == ["alpha", "beta"]
    rc, r2, _ = env.json("sync")
    assert rc == 0
    assert r2["linked"] == [] and r2["removed"] == []
    assert sorted(r2["already"]) == ["alpha", "beta"]
    for n in ("alpha", "beta"):
        assert links.same_target(env.skills_dir / n, env.store.paths.skills / n)


def test_sync_removes_unlisted_store_links_only(env, tmp_path):
    env.add_skill("alpha")
    env.add_skill("beta")
    env.write_manifest(["alpha", "beta"])
    env.run("sync")
    # A foreign link, a real project skill and a stray file must survive.
    foreign_target = tmp_path / "agents" / "find-skills"
    foreign_target.mkdir(parents=True)
    (foreign_target / "SKILL.md").write_text("foreign")
    links.create_link(env.skills_dir / "find-skills", foreign_target)
    real = env.skills_dir / "project-own"
    real.mkdir()
    (real / "SKILL.md").write_text("real")
    env.write_manifest(["alpha"])

    rc, r, _ = env.json("sync")
    assert rc == 0
    assert r["removed"] == ["beta"]
    assert not os.path.lexists(env.skills_dir / "beta")
    assert (env.store.paths.skills / "beta" / "SKILL.md").is_file()  # store copy intact
    assert links.is_link(env.skills_dir / "find-skills")
    assert (foreign_target / "SKILL.md").read_text() == "foreign"
    assert (real / "SKILL.md").read_text() == "real"
    assert (env.skills_dir / ".gitignore").is_file()


def test_sync_reports_unapproved_and_missing(env):
    env.add_skill("alpha", approved=False)
    env.store.init()
    env.write_manifest(["alpha", "ghost"])
    rc, r, _ = env.json("sync")
    assert rc == 0
    assert r["needs_approval"] == ["alpha"]
    assert r["missing"] == ["ghost"]
    assert not os.path.lexists(env.skills_dir / "alpha")


def test_sync_leaves_conflicting_real_dir(env):
    env.add_skill("alpha")
    env.write_manifest(["alpha"])
    real = env.skills_dir / "alpha"
    real.mkdir(parents=True)
    (real / "SKILL.md").write_text("mine")
    rc, r, _ = env.json("sync")
    assert rc == 0 and r["conflicts"] == ["alpha"]
    assert (real / "SKILL.md").read_text() == "mine"


def test_sync_without_manifest_does_nothing(env):
    env.add_skill("alpha")
    rc, r, _ = env.json("sync")
    assert rc == 0 and "no manifest" in r["skipped"]
    assert not (env.project / ".claude").exists()


def test_sync_without_store_is_quiet_noop(env):
    env.write_manifest(["alpha"])
    rc, out, err = env.run("sync", "--quiet")
    assert rc == 0 and out == "" and err == ""


def test_sync_quiet_never_fails_on_broken_manifest(env):
    env.store.init()
    p = env.project / ".claude" / "skills.manifest.json"
    p.parent.mkdir(parents=True)
    p.write_text("{not json")
    rc, out, err = env.run("sync", "--quiet")
    assert rc == 0 and out == ""
    assert "cannot read manifest" in err
    rc, _, _ = env.run("sync")
    assert rc == 1


def test_sync_quiet_is_silent_on_success(env):
    env.add_skill("alpha")
    env.write_manifest(["alpha"])
    rc, out, err = env.run("sync", "--quiet")
    assert rc == 0 and out == "" and err == ""
    assert links.is_link(env.skills_dir / "alpha")


def test_sync_replaces_store_link_with_wrong_target(env):
    env.add_skill("alpha")
    env.add_skill("beta")
    env.write_manifest(["alpha"])
    env.skills_dir.mkdir(parents=True)
    links.create_link(env.skills_dir / "alpha", env.store.paths.skills / "beta")
    rc, r, _ = env.json("sync")
    assert rc == 0 and r["relinked"] == ["alpha"] and r["removed"] == [] and r["linked"] == []
    assert links.same_target(env.skills_dir / "alpha", env.store.paths.skills / "alpha")


def test_sync_unlinks_skill_that_now_fails_admission(env):
    d = env.add_skill("alpha")
    env.write_manifest(["alpha"])
    env.run("sync")
    (d / "SKILL.md").write_text("---\nname: alpha\ndescription: Use when working with alpha\n---\n")
    rc, r, _ = env.json("sync")
    assert "alpha" in r["failed_check"] and r["unlinked_failed_check"] == ["alpha"]
    assert r["removed"] == []
    assert (d / "SKILL.md").is_file()


def test_sync_over_cap_links_most_recent_and_keeps_manifest(env):
    for n in ("a1", "a2", "a3"):
        env.add_skill(n)
    env.write_manifest(["a1", "a2", "a3"], cap=2)
    env.log_use("a1", "2026-11-01T00:00:00Z")
    env.log_use("a3", "2026-11-03T00:00:00Z")
    rc, r, _ = env.json("sync")
    assert rc == 0
    assert sorted(r["linked"]) == ["a1", "a3"]
    assert r["over_cap"] == ["a2"]
    assert env.manifest()["skills"] == ["a1", "a2", "a3"]  # sync never edits the manifest
    rc, r, _ = env.json("sync")
    assert r["linked"] == [] and r["removed"] == []


def _categories(r):
    keys = ("linked", "relinked", "already", "removed", "unlinked_over_cap",
            "unlinked_failed_check", "unlinked_unavailable", "conflicts")
    return {k: sorted(r[k]) for k in keys if r[k]}


def test_sync_report_categories_when_manifest_exceeds_cap(env):
    """A listed skill pushed over the cap is reported only as over cap, never as 'no longer listed'."""
    for n in ("verify-alpha", "verify-beta", "verify-gamma", "gone"):
        env.add_skill(n)
    env.write_manifest(["verify-alpha", "verify-beta", "gone"], cap=3)
    env.run("sync")
    env.log_use("verify-alpha", "2026-11-03T00:00:00Z")
    env.log_use("verify-gamma", "2026-11-04T00:00:00Z")
    env.log_use("gone", "2026-11-05T00:00:00Z")
    # verify-beta (never used) is now the LRU listed skill; 'gone' is dropped from the manifest.
    env.write_manifest(["verify-alpha", "verify-beta", "verify-gamma"], cap=2)
    rc, r, _ = env.json("sync")
    assert rc == 0
    assert _categories(r) == {"already": ["verify-alpha"], "linked": ["verify-gamma"],
                              "removed": ["gone"], "unlinked_over_cap": ["verify-beta"]}
    assert r["over_cap"] == ["verify-beta"]
    rc, out, _ = env.run("sync")  # second run: nothing changes, beta is over cap and not linked
    assert rc == 0
    assert "no longer listed" not in out
    assert out.count("verify-beta") == 1 and "over cap, not linked: verify-beta" in out


def test_sync_text_reports_over_cap_unlink_once(env):
    for n in ("verify-alpha", "verify-beta"):
        env.add_skill(n)
    env.write_manifest(["verify-alpha", "verify-beta"])
    env.run("sync")
    env.log_use("verify-alpha", "2026-11-03T00:00:00Z")
    env.write_manifest(["verify-alpha", "verify-beta"], cap=1)
    rc, out, _ = env.run("sync")
    assert rc == 0
    assert "no longer listed" not in out
    assert out.count("verify-beta") == 1
    assert "over cap, unlinked (least recently used; store copy untouched): verify-beta" in out


def test_sync_report_unavailable_listed_skill(env):
    env.add_skill("alpha")
    env.write_manifest(["alpha"])
    env.run("sync")
    data = env.store.load()
    data["skills"]["alpha"]["approved"] = False
    env.store.save(data)
    rc, r, _ = env.json("sync")
    assert _categories(r) == {"unlinked_unavailable": ["alpha"]}
    assert r["needs_approval"] == ["alpha"]


def test_sync_continues_when_one_link_removal_raises_oserror(env, monkeypatch):
    """A locked junction (PermissionError) is reported; the rest of the sync still runs."""
    from skillstore import ops
    for n in ("aa", "bb", "cc"):
        env.add_skill(n)
    env.write_manifest(["aa", "bb"])
    env.run("sync")
    env.write_manifest(["cc"])
    real_remove = ops.remove_link

    def flaky(path):
        if os.path.basename(str(path)) == "aa":
            raise PermissionError(13, "The process cannot access the file", str(path))
        return real_remove(path)

    monkeypatch.setattr(ops, "remove_link", flaky)
    rc, r, _ = env.json("sync")
    assert rc == 1
    assert len(r["errors"]) == 1 and r["errors"][0].startswith("aa:")
    assert r["removed"] == ["bb"] and r["linked"] == ["cc"]
    assert links.is_link(env.skills_dir / "aa")  # left in place, reported
    rc, out, err = env.run("sync", "--quiet")
    assert rc == 0 and out == "" and "aa:" in err
