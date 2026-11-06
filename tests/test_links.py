"""Link primitives: junctions on Windows, symlinks elsewhere; removal never deletes targets."""

import os
from pathlib import Path

import pytest

from skillstore import links
from skillstore.links import RefusedError


def _target(tmp_path: Path) -> Path:
    t = tmp_path / "store" / "skills" / "demo"
    (t / "references").mkdir(parents=True)
    (t / "SKILL.md").write_text("x")
    (t / "references" / "a.md").write_text("ref")
    return t


def _intact(t: Path):
    assert (t / "SKILL.md").read_text() == "x"
    assert (t / "references" / "a.md").read_text() == "ref"


def test_create_link_kind(tmp_path):
    t = _target(tmp_path)
    lp = tmp_path / "proj" / ".claude" / "skills" / "demo"
    kind = links.create_link(lp, t)
    assert kind == ("junction" if os.name == "nt" else "symlink")
    assert links.link_kind(lp) == kind
    assert (lp / "SKILL.md").read_text() == "x"
    assert links.same_target(lp, t)
    assert links.points_into(lp, t.parent)


def test_remove_link_keeps_target(tmp_path):
    t = _target(tmp_path)
    lp = tmp_path / "link"
    links.create_link(lp, t)
    links.remove_link(lp)
    assert not os.path.lexists(lp)
    _intact(t)


def test_remove_link_with_trailing_separator_keeps_target(tmp_path):
    t = _target(tmp_path)
    lp = tmp_path / "link"
    links.create_link(lp, t)
    links.remove_link(Path(str(lp) + os.sep))
    assert not os.path.lexists(lp)
    _intact(t)


def test_remove_link_refuses_real_directory(tmp_path):
    real = _target(tmp_path)
    with pytest.raises(RefusedError, match="real directory"):
        links.remove_link(real)
    _intact(real)


def test_remove_link_refuses_file_and_missing(tmp_path):
    f = tmp_path / "f.txt"
    f.write_text("keep")
    with pytest.raises(RefusedError):
        links.remove_link(f)
    assert f.read_text() == "keep"
    with pytest.raises(RefusedError):
        links.remove_link(tmp_path / "nope")


def test_remove_dangling_link(tmp_path):
    t = _target(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    lp = tmp_path / "link"
    links.create_link(lp, other)
    other.rmdir()
    assert links.is_link(lp)
    links.remove_link(lp)
    assert not os.path.lexists(lp)
    _intact(t)


def test_create_link_refuses_existing(tmp_path):
    t = _target(tmp_path)
    lp = tmp_path / "exists"
    lp.mkdir()
    with pytest.raises(RefusedError):
        links.create_link(lp, t)


def test_points_into_rejects_foreign(tmp_path):
    t = _target(tmp_path)
    foreign = tmp_path / "elsewhere" / "demo"
    foreign.mkdir(parents=True)
    lp = tmp_path / "link"
    links.create_link(lp, foreign)
    assert not links.points_into(lp, t.parent)
    assert not links.points_into(t, t.parent)  # a real dir is never "into the store"


def test_no_recursive_delete_in_link_code():
    """Static guard: the modules that touch project .claude/skills never delete recursively."""
    root = Path(__file__).resolve().parent.parent / "skillstore"
    for mod in ("links.py", "ops.py", "manifest.py", "cli.py"):
        src = (root / mod).read_text(encoding="utf-8")
        code = "\n".join(line for line in src.splitlines() if not line.lstrip().startswith("#"))
        for bad in ("rmtree(", "Remove-Item", "rm -r", "removedirs("):
            assert bad not in code.split('"""', 2)[-1], f"{bad} found in {mod}"
