"""Opt-in: links whose target is on another volume (e.g. store on C:, project on E:).

Set SKILL_STORE_TEST_OTHER_VOLUME to a writable directory on a different drive/filesystem
than the pytest temp dir, e.g. ``SKILL_STORE_TEST_OTHER_VOLUME=E:\tmp python -m pytest``.
"""

import os
import shutil
import uuid
from pathlib import Path

import pytest

from skillstore import links

OTHER = os.environ.get("SKILL_STORE_TEST_OTHER_VOLUME")
pytestmark = pytest.mark.skipif(not OTHER, reason="SKILL_STORE_TEST_OTHER_VOLUME not set")


@pytest.fixture
def other_dir():
    d = Path(OTHER) / f"skill-store-xvol-{uuid.uuid4().hex[:8]}"
    d.mkdir(parents=True)
    yield d
    # Only clean up once no links remain inside (never recurse through a link).
    assert not any(links.is_link(p) for p in d.rglob("*")), "link left behind"
    shutil.rmtree(d)


@pytest.mark.parametrize("direction", ["store-here-project-there", "store-there-project-here"])
def test_cross_volume_link_sync_unlink(env, other_dir, direction):
    if direction == "store-there-project-here":
        env.store_root = other_dir / "store"
        from skillstore.store import Store
        env.store = Store(env.store_root)
        extra = ["--store", str(env.store_root)]
    else:
        env.project = other_dir / "project"
        env.project.mkdir()
        extra = []
    assert os.path.splitdrive(str(env.store_root))[0] != os.path.splitdrive(str(env.project))[0] or os.name != "nt"
    env.add_skill("alpha")
    env.write_manifest(["alpha"])
    rc, out, err = env.run("sync", *extra)
    assert rc == 0, err
    lp = env.skills_dir / "alpha"
    assert links.is_link(lp) and (lp / "SKILL.md").is_file()
    assert env.run("sync", *extra)[0] == 0
    assert env.run("unlink", "alpha", *extra)[0] == 0
    assert not os.path.lexists(lp)
    assert (env.store_root / "skills" / "alpha" / "SKILL.md").is_file()
