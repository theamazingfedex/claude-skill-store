"""Directory links: NTFS junctions on Windows, symlinks elsewhere.

HARD SAFETY RULE: this module only ever removes a *link*. ``remove_link`` verifies that the
path is a symlink or junction and removes it non-recursively (``os.rmdir`` on a reparse point
on Windows, ``os.unlink`` elsewhere). Nothing in this package calls ``shutil.rmtree`` or any
other recursive delete on a project's ``.claude/skills`` directory.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

from skillstore.paths import SkillStoreError

IS_WINDOWS = os.name == "nt"
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_IO_REPARSE_TAG_MOUNT_POINT = 0xA0000003  # junction


class RefusedError(SkillStoreError):
    """Raised when an operation would touch something that is not a link we may remove."""


def link_kind(path: Path) -> str | None:
    """'symlink', 'junction', or None (missing, real directory or file)."""
    try:
        st = os.lstat(path)
    except (FileNotFoundError, NotADirectoryError):
        return None
    if stat.S_ISLNK(st.st_mode):
        return "symlink"
    if IS_WINDOWS:
        attrs = getattr(st, "st_file_attributes", 0)
        tag = getattr(st, "st_reparse_tag", 0)
        if attrs & _FILE_ATTRIBUTE_REPARSE_POINT and tag == _IO_REPARSE_TAG_MOUNT_POINT:
            return "junction"
    return None


def is_link(path: Path) -> bool:
    return link_kind(path) is not None


def _strip_nt_prefix(target: str) -> str:
    if target.startswith("\\\\?\\UNC\\"):
        return "\\\\" + target[8:]
    if target.startswith("\\\\?\\") or target.startswith("\\??\\"):
        return target[4:]
    return target


def link_target(path: Path) -> Path | None:
    """The absolute path a link points at (it may not exist), or None if not a link."""
    if not is_link(path):
        return None
    try:
        raw = os.readlink(path)
    except OSError:
        return None
    target = Path(_strip_nt_prefix(str(raw)))
    if not target.is_absolute():
        target = Path(path).parent / target
    return Path(os.path.normpath(str(target)))


def _norm(p: Path) -> str:
    return os.path.normcase(os.path.normpath(str(p)))


def _variants(p: Path) -> set[str]:
    out = {_norm(Path(os.path.abspath(p)))}
    try:
        out.add(_norm(Path(os.path.realpath(p))))
    except OSError:
        pass
    return out


def points_into(path: Path, directory: Path) -> bool:
    """True if ``path`` is a link whose target is a direct child of ``directory``."""
    target = link_target(path)
    if target is None:
        return False
    parents = _variants(Path(directory))
    candidates = {_norm(target.parent)}
    try:
        candidates.add(_norm(Path(os.path.realpath(target)).parent))
    except OSError:
        pass
    return bool(parents & candidates)


def same_target(path: Path, target: Path) -> bool:
    current = link_target(path)
    if current is None:
        return False
    a = {_norm(current)}
    try:
        a.add(_norm(Path(os.path.realpath(current))))
    except OSError:
        pass
    return bool(a & _variants(Path(target)))


def create_link(link: Path, target: Path) -> str:
    """Create ``link`` -> ``target`` (a directory). Returns the link kind created."""
    link = Path(os.path.abspath(link))
    target = Path(os.path.abspath(target))
    if not target.is_dir():
        raise SkillStoreError(f"link target is not a directory: {target}")
    if os.path.lexists(link):
        raise RefusedError(f"refusing to create link: {link} already exists")
    link.parent.mkdir(parents=True, exist_ok=True)
    if IS_WINDOWS:
        try:
            import _winapi

            _winapi.CreateJunction(str(target), str(link))
        except (ImportError, AttributeError, OSError):
            res = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link), str(target)],
                capture_output=True,
                text=True,
                stdin=subprocess.DEVNULL,
            )
            if res.returncode != 0:
                raise SkillStoreError(
                    f"could not create junction {link} -> {target}: "
                    f"{(res.stderr or res.stdout).strip()}"
                )
        if link_kind(link) != "junction":
            raise SkillStoreError(f"junction creation did not produce a junction at {link}")
        return "junction"
    os.symlink(str(target), str(link), target_is_directory=True)
    return "symlink"


def remove_link(link: Path) -> None:
    """Remove a symlink/junction, never its target. Refuses anything that is not a link."""
    link = Path(os.path.abspath(link))  # also strips any trailing separator
    kind = link_kind(link)
    if kind is None:
        if os.path.isdir(link):
            raise RefusedError(
                f"refusing to remove {link}: it is a real directory, not a link "
                "(skill-store never deletes directories)"
            )
        if os.path.lexists(link):
            raise RefusedError(f"refusing to remove {link}: it is not a link")
        raise RefusedError(f"nothing to remove: {link} does not exist")
    if IS_WINDOWS:
        # RemoveDirectoryW on a reparse point removes the link itself, non-recursively.
        try:
            os.rmdir(link)
        except NotADirectoryError:
            os.unlink(link)  # file symlink
    else:
        os.unlink(link)
    if os.path.lexists(link):
        raise SkillStoreError(f"failed to remove link {link}")
