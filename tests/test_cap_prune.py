"""Cap enforcement on link and on-demand prune, both LRU from the usage log."""

import json
import os


def test_link_beyond_cap_unlinks_lru(env):
    for n in ("s1", "s2", "s3", "s4"):
        env.add_skill(n)
    env.write_manifest([], cap=3)
    for n in ("s1", "s2", "s3"):
        assert env.run("link", n)[0] == 0
    env.log_use("s1", "2026-11-04T00:00:00Z")
    env.log_use("s2", "2026-11-01T00:00:00Z")  # least recently used
    env.log_use("s3", "2026-11-03T00:00:00Z")
    rc, r, _ = env.json("link", "s4")
    assert rc == 0
    assert [e["skill"] for e in r["evicted"]] == ["s2"]
    assert not os.path.lexists(env.skills_dir / "s2")
    assert (env.store.paths.skills / "s2" / "SKILL.md").is_file()
    assert env.manifest()["skills"] == ["s1", "s3", "s4"]
    rc, out, _ = env.run("link", "s2")  # text output names the evicted skill
    assert "unlinked least recently used s4" in out  # never used = least recent


def test_never_used_skills_go_first_then_manifest_order(env):
    for n in ("old", "mid", "used", "new"):
        env.add_skill(n)
    env.write_manifest([], cap=3)
    for n in ("old", "mid", "used"):
        env.run("link", n)
    env.log_use("used", "2026-11-01T00:00:00Z")
    rc, r, _ = env.json("link", "new")
    assert [e["skill"] for e in r["evicted"]] == ["old"]


def test_default_cap_is_8(env):
    names = [f"k{i}" for i in range(9)]
    for n in names:
        env.add_skill(n)
    for n in names:
        env.run("link", n)
    m = env.manifest()
    assert "cap" not in m and len(m["skills"]) == 8 and "k0" not in m["skills"]


def test_prune_to_cap(env):
    for n in ("p1", "p2", "p3", "p4"):
        env.add_skill(n)
    env.write_manifest(["p1", "p2", "p3", "p4"])
    env.run("sync")
    env.log_use("p1", "2026-11-04T00:00:00Z")
    env.log_use("p2", "2026-11-02T00:00:00Z")
    env.log_use("p3", "2026-11-03T00:00:00Z")
    env.log_use("p4", "2026-11-01T00:00:00Z")
    rc, r, _ = env.json("prune", "--cap", "2")
    assert rc == 0
    assert [e["skill"] for e in r["evicted"]] == ["p4", "p2"]
    assert env.manifest()["skills"] == ["p1", "p3"]
    for n in ("p2", "p4"):
        assert not os.path.lexists(env.skills_dir / n)
        assert (env.store.paths.skills / n / "SKILL.md").is_file()
    rc, r, _ = env.json("prune", "--cap", "2")
    assert r["evicted"] == []
    rc, r, _ = env.json("sync")  # pruned skills stay pruned
    assert r["linked"] == []


def test_prune_ignores_foreign_entries(env, tmp_path):
    from skillstore import links
    env.add_skill("p1")
    env.write_manifest(["p1"], cap=1)
    env.run("sync")
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    links.create_link(env.skills_dir / "foreign", foreign)
    (env.skills_dir / "realdir").mkdir()
    rc, r, _ = env.json("prune", "--cap", "0")
    assert [e["skill"] for e in r["evicted"]] == ["p1"]
    assert links.is_link(env.skills_dir / "foreign")
    assert (env.skills_dir / "realdir").is_dir()


def test_store_json_last_used_is_fallback(env):
    for n in ("a", "b", "c"):
        env.add_skill(n)
    data = env.store.load()
    data["skills"]["a"]["last_used"] = "2026-10-01T00:00:00Z"
    data["skills"]["b"]["last_used"] = "2026-10-05T00:00:00Z"
    env.store.save(data)
    env.write_manifest([], cap=2)
    env.run("link", "a")
    env.run("link", "b")
    rc, r, _ = env.json("link", "c")
    assert [e["skill"] for e in r["evicted"]] == ["a"]
