"""Uninstall skill-store: restore cached skills (optional), then remove every trace except the clone.

Run it from the checkout, never from the installed package (it uninstalls that package):

    python skillstore/uninstall.py [--dry-run] [--yes] [--restore|--no-restore] [--store DIR] [--project DIR]...
    python -m skillstore.uninstall ...        (from the checkout root)

``uninstall.ps1`` (Windows) and ``uninstall.sh`` (Linux/macOS) in the repo root are thin wrappers.

Flow: discover the store and the Claude config dir, print a summary of every cached skill, ask
whether to restore them into the global Claude skills dir, show the removal plan, confirm once
more, then remove: store links (global and per project), skill-store hook entries in the user
``settings.json`` (after a timestamped backup), the package (pip / pipx / uv tool) and the
temp-dir test venvs, ``SKILL_STORE_HOME`` and README-suggested PATH/profile lines, and finally
the store directory itself. Files committed in users' repos are listed, never deleted.

Safety: links are removed one at a time and non-recursively (``links.remove_link``). The store is
deleted only after every link inside it was removed and a second scan finds no symlink or
reparse point; the delete itself refuses to enter a link. Foreign entries in the global skills
dir are never touched, and the checkout this script runs from is never deleted.

This module uses the standard library and the stdlib-only parts of ``skillstore`` (paths, links,
store, manifest), so it works without PyYAML and without the installed package.
"""

from __future__ import annotations

import os
import sys

if __name__ == "__main__" and not __package__:
    # Run as a file (uninstall.ps1 / uninstall.sh): put the checkout, not skillstore/, first on
    # sys.path, so this checkout's package is imported and nothing in skillstore/ shadows stdlib.
    _here = os.path.normcase(os.path.dirname(os.path.abspath(__file__)))
    sys.path[:] = [p for p in sys.path if os.path.normcase(os.path.abspath(p or ".")) != _here]
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse  # noqa: E402
import json  # noqa: E402
import math  # noqa: E402
import re  # noqa: E402
import shutil  # noqa: E402
import stat  # noqa: E402
import subprocess  # noqa: E402
import tempfile  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402
from datetime import datetime  # noqa: E402
from pathlib import Path  # noqa: E402

from skillstore.links import (IS_WINDOWS, create_link, is_link, link_kind, link_target,  # noqa: E402
                              points_into, remove_link)
from skillstore.manifest import GITIGNORE_TEXT  # noqa: E402
from skillstore.paths import (GLOBAL_MANIFEST_NAME, legacy_home_manifest,  # noqa: E402
                              resolve_claude_config, resolve_store, same_dir)
from skillstore.store import atomic_write_text  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_NAMES = ("claude-skill-store", "skill-store")
ENV_VARS = ("SKILL_STORE_HOME", "SKILL_STORE_CMD")
HOOK_RE = re.compile(r"skill[-_]?store", re.I)  # skill-store, skillstore, skill-store-hook.*, SKILL_STORE_*
SNIPPET_MARKER = "Skills store: unlink safety"
README_PATH_LINE = 'export PATH="$HOME/.local/bin:$PATH"'
_FM_RE = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.S)
_REPARSE = 0x400


# ---------------------------------------------------------------------------------------
# indirections (tests replace these; nothing here may touch the real system in tests)
# ---------------------------------------------------------------------------------------
def _run(cmd: list[str], timeout: int = 120) -> tuple[int, str]:
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                             stdin=subprocess.DEVNULL, encoding="utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, str(exc)
    return res.returncode, (res.stdout or "") + (res.stderr or "")


def _which(name: str) -> str | None:
    return shutil.which(name)


def _input(prompt: str) -> str:
    return input(prompt)


class WinUserEnv:
    """The per-user environment (HKCU\\Environment) on Windows."""

    KEY = "Environment"

    def get(self, name: str):
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.KEY) as k:
                value, kind = winreg.QueryValueEx(k, name)
        except FileNotFoundError:
            return None
        return value, kind

    def set(self, name: str, value: str, kind: int) -> None:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.KEY, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, name, 0, kind, value)
        self._broadcast()

    def delete(self, name: str) -> None:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.KEY, 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, name)
        self._broadcast()

    @staticmethod
    def _broadcast() -> None:
        try:  # tell Explorer (and new terminals) that the environment changed
            import ctypes
            from ctypes import wintypes
            res = wintypes.DWORD()
            ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, "Environment", 0x0002, 5000,
                                                     ctypes.byref(res))
        except Exception:  # noqa: BLE001 - best effort only
            pass


def _user_env():
    return WinUserEnv() if IS_WINDOWS else None


# ---------------------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------------------
def tokens(text: str) -> int:
    """Same measure as admission: chars / 4, rounded up."""
    return math.ceil(len(text) / 4)


def _key(p: Path | str) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(str(p))))


def _is_within(child: Path, parent: Path) -> bool:
    c, p = _key(child), _key(parent)
    return c == p or c.startswith(p.rstrip(os.sep) + os.sep)


def _is_reparse_or_link(p: Path) -> bool:
    """Any symlink, junction or other reparse point: never descend into or delete through it."""
    try:
        st = os.lstat(p)
    except OSError:
        return False
    if stat.S_ISLNK(st.st_mode):
        return True
    return bool(getattr(st, "st_file_attributes", 0) & _REPARSE)


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _backup(path: Path) -> Path:
    dest = path.with_name(f"{path.name}.skill-store-backup-{_stamp()}")
    n = 1
    while os.path.lexists(dest):
        n += 1
        dest = path.with_name(f"{path.name}.skill-store-backup-{_stamp()}-{n}")
    shutil.copy2(path, dest)
    return dest


def _ps_q(s) -> str:
    return "'" + str(s).replace("'", "''") + "'"


def _sh_q(s) -> str:
    import shlex
    return shlex.quote(str(s))


# ---------------------------------------------------------------------------------------
# SKILL.md parsing (no PyYAML needed)
# ---------------------------------------------------------------------------------------
def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        if value[0] == '"':
            try:
                return json.loads(value)
            except ValueError:
                return value[1:-1]
        return value[1:-1].replace("''", "'")
    return value


def _description(raw: str) -> str:
    try:
        import yaml  # optional: exact when available
        data = yaml.safe_load(raw)
        if isinstance(data, dict) and isinstance(data.get("description"), str):
            return data["description"]
    except Exception:  # noqa: BLE001 - fall back to the lenient parse
        pass
    lines = raw.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"^description:(?:\s+(.*))?$", line)
        if not m:
            continue
        value = (m.group(1) or "").strip()
        cont = []
        for nxt in lines[i + 1:]:
            if nxt.strip() and not nxt[0].isspace():
                break
            cont.append(nxt.strip())
        if value[:1] in (">", "|"):
            return ("\n" if value[0] == "|" else " ").join(c for c in cont if c).strip()
        return _unquote(" ".join([value] + [c for c in cont if c]).strip())
    return ""


def skill_costs(skill_dir: Path) -> tuple[int, int, bool]:
    """(listing tokens = description chars/4, body tokens = SKILL.md body chars/4, has SKILL.md)."""
    try:
        text = (skill_dir / "SKILL.md").read_text(encoding="utf-8", errors="replace").lstrip("﻿")
    except OSError:
        return 0, 0, False
    m = _FM_RE.match(text)
    if not m:
        return 0, tokens(text), True
    return tokens(_description(m.group(1))), tokens(text[m.end():]), True


# ---------------------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------------------
@dataclass
class Skill:
    name: str
    entry: Path
    approved: bool
    external: Path | None = None    # the checkout the store entry links to
    problem: str | None = None      # why it can't be restored (dangling link, no SKILL.md)
    listing_tokens: int = 0
    body_tokens: int = 0
    global_link: Path | None = None
    projects: list[str] = field(default_factory=list)
    conflict: str | None = None     # a foreign entry in the global dir uses this name

    @property
    def restorable(self) -> bool:
        return self.problem is None and self.conflict is None


@dataclass
class Project:
    root: Path
    sources: set[str]
    exists: bool = False
    links: dict[str, Path] = field(default_factory=dict)    # link path str -> store target name
    committed: list[str] = field(default_factory=list)
    settings_hooks: list[str] = field(default_factory=list)


@dataclass
class State:
    store: Path
    config: Path
    gdir: Path
    gdir_is_link: bool
    skills: list[Skill]
    global_links: dict[str, Path]       # link name -> path
    projects: list[Project]
    store_internal_links: list[tuple[Path, Path | None]]
    settings: Path
    settings_hooks: list[str]
    settings_error: str | None
    legacy_files: list[Path]


def _store_links_in(skills_dir: Path, store_skills: Path) -> dict[str, Path]:
    out: dict[str, Path] = {}
    if not skills_dir.is_dir() or is_link(skills_dir):
        return out
    try:
        entries = sorted(skills_dir.iterdir(), key=lambda p: p.name)
    except OSError:
        return out
    for e in entries:
        if is_link(e) and points_into(e, store_skills):
            out[e.name] = e
    return out


def _target_name(link: Path) -> str:
    t = link_target(link)
    return t.name if t is not None else link.name


def find_links(root: Path) -> list[Path]:
    """Every symlink/junction/reparse point under ``root``, without ever descending into one."""
    out: list[Path] = []
    stack = [Path(root)]
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                entries = list(it)
        except OSError:
            continue
        for e in entries:
            p = Path(e.path)
            if _is_reparse_or_link(p):
                out.append(p)
            elif e.is_dir(follow_symlinks=False):
                stack.append(p)
    return sorted(out)


def _usage_projects(usage: Path) -> list[str]:
    out = []
    try:
        fh = open(usage, encoding="utf-8", errors="replace")
    except OSError:
        return out
    with fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict) and isinstance(rec.get("project"), str) and rec["project"].strip():
                out.append(rec["project"].strip())
    return out


def _git_worktrees(root: Path) -> list[str]:
    rc, out = _run(["git", "-C", str(root), "worktree", "list", "--porcelain"], timeout=20)
    if rc != 0:
        return []
    return [ln[len("worktree "):].strip() for ln in out.splitlines() if ln.startswith("worktree ")]


def _hook_entries(data) -> list[str]:
    """Descriptions of skill-store hook entries in a settings object."""
    found = []
    hooks = data.get("hooks") if isinstance(data, dict) else None
    if not isinstance(hooks, dict):
        return found
    for event, groups in hooks.items():
        if not isinstance(groups, list):
            continue
        for g in groups:
            if isinstance(g, dict) and isinstance(g.get("hooks"), list):
                for h in g["hooks"]:
                    if _is_store_hook(h):
                        found.append(f"{event}: {_hook_text(h)}")
    return found


def _hook_text(h) -> str:
    if not isinstance(h, dict):
        return ""
    parts = [h.get("command")] if isinstance(h.get("command"), str) else []
    if isinstance(h.get("args"), list):
        parts += [a for a in h["args"] if isinstance(a, str)]
    return " ".join(parts)


def _is_store_hook(h) -> bool:
    return bool(HOOK_RE.search(_hook_text(h)))


def strip_store_hooks(data: dict) -> tuple[dict, list[str]]:
    """A copy of ``data`` without skill-store hook entries. Groups and events emptied by the
    removal are dropped; everything else (other hooks, other settings, key order) is kept."""
    new = json.loads(json.dumps(data))
    removed = _hook_entries(new)
    hooks = new.get("hooks")
    if not removed or not isinstance(hooks, dict):
        return new, []
    for event in list(hooks):
        groups = hooks[event]
        if not isinstance(groups, list):
            continue
        kept_groups = []
        for g in groups:
            if isinstance(g, dict) and isinstance(g.get("hooks"), list):
                kept = [h for h in g["hooks"] if not _is_store_hook(h)]
                if len(kept) != len(g["hooks"]):
                    if not kept:
                        continue
                    g["hooks"] = kept
            kept_groups.append(g)
        if len(kept_groups) != len(groups) and not kept_groups:
            del hooks[event]
        else:
            hooks[event] = kept_groups
    return new, removed


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8").lstrip("﻿"))


def _scan_project(p: Project, store_skills: Path, home: Path) -> None:
    p.exists = p.root.is_dir()
    if not p.exists:
        return
    claude = p.root / ".claude"
    p.links = {str(lp): _target_name(lp) for lp in _store_links_in(claude / "skills", store_skills).values()}
    if not same_dir(p.root, home):  # the old --project ~ files are removed, not listed
        man = claude / "skills.manifest.json"
        if man.is_file():
            p.committed.append(str(man))
        gi = claude / "skills" / ".gitignore"
        try:
            if gi.is_file() and not is_link(gi):
                text = gi.read_text(encoding="utf-8", errors="replace")
                if text == GITIGNORE_TEXT or "skill-store" in text:
                    p.committed.append(str(gi))
        except OSError:
            pass
    for doc in (p.root / "CLAUDE.md", claude / "CLAUDE.md", p.root / "AGENTS.md"):
        try:
            if doc.is_file() and SNIPPET_MARKER in doc.read_text(encoding="utf-8", errors="replace"):
                p.committed.append(f"{doc} (the '{SNIPPET_MARKER}' section)")
        except OSError:
            pass
    for name in ("settings.json", "settings.local.json"):
        s = claude / name
        try:
            if s.is_file() and _hook_entries(_read_json(s)):
                p.settings_hooks.append(str(s))
        except (OSError, ValueError):
            pass


def discover(store_flag: str | None = None, extra_projects: list[str] | None = None) -> State:
    store = resolve_store(store_flag)
    config = resolve_claude_config()
    gdir = config / "skills"
    store_skills = store / "skills"
    home = Path.home()

    # store.json approval (read raw: a damaged store.json must not stop the uninstall)
    approved: dict[str, bool] = {}
    try:
        data = _read_json(store / "store.json")
        for n, e in (data.get("skills") or {}).items():
            approved[n] = bool(isinstance(e, dict) and e.get("approved") is True)
    except (OSError, ValueError, AttributeError):
        pass

    gdir_is_link = is_link(gdir)
    global_links = {} if gdir_is_link else _store_links_in(gdir, store_skills)

    # projects: usage.jsonl, the old --project ~ manifest, --project flags, and their worktrees
    cands: dict[str, Project] = {}

    def add(path: str, source: str):
        try:
            root = Path(path).expanduser().absolute()
        except (OSError, ValueError):
            return
        if same_dir(root / ".claude" / "skills", gdir):
            return  # that's the global dir, handled as such
        k = _key(root)
        if k not in cands:
            cands[k] = Project(root, set())
        cands[k].sources.add(source)

    for pth in _usage_projects(store / "usage.jsonl"):
        add(pth, "usage.jsonl")
    if legacy_home_manifest().is_file():
        add(str(home), "old --project ~ manifest")
    for pth in extra_projects or []:
        add(pth, "--project")
    for proj in list(cands.values()):
        if proj.root.is_dir() and (proj.root / ".git").exists():
            for wt in _git_worktrees(proj.root):
                add(wt, f"git worktree of {proj.root}")
    projects = sorted(cands.values(), key=lambda p: _key(p.root))
    for proj in projects:
        _scan_project(proj, store_skills, home)

    skills: list[Skill] = []
    if store_skills.is_dir() and not _is_reparse_or_link(store_skills):
        for entry in sorted(store_skills.iterdir(), key=lambda p: p.name):
            if entry.name.startswith(".") or not (entry.is_dir() or is_link(entry)):
                continue
            s = Skill(entry.name, entry, approved.get(entry.name, False))
            if is_link(entry):
                t = link_target(entry)
                s.external = Path(os.path.realpath(t)) if t is not None else None
                if s.external is None or not s.external.is_dir():
                    s.problem = f"link target missing ({t})"
            s.listing_tokens, s.body_tokens, has_md = skill_costs(entry)
            if s.problem is None and not has_md:
                s.problem = "no SKILL.md"
            for lp in global_links.values():
                if _target_name(lp) == s.name:
                    s.global_link = lp
            s.projects = [str(p.root) for p in projects if s.name in p.links.values()]
            dest = gdir / s.name
            if not gdir_is_link and os.path.lexists(dest) and s.global_link != dest:
                if not (is_link(dest) and points_into(dest, store_skills)):
                    s.conflict = (f"{dest} already exists "
                                  f"({link_kind(dest) or ('directory' if dest.is_dir() else 'file')}, "
                                  "not a skill-store link)")
            if gdir_is_link:
                s.conflict = f"{gdir} is itself a link; skill-store leaves it alone"
            skills.append(s)

    internal = []
    if store.is_dir() and not _is_reparse_or_link(store):
        internal = [(lp, link_target(lp)) for lp in find_links(store)]

    settings = config / "settings.json"
    settings_hooks, settings_error = [], None
    if settings.is_file():
        try:
            settings_hooks = _hook_entries(_read_json(settings))
        except (OSError, ValueError) as exc:
            settings_error = f"cannot read {settings}: {exc}"

    legacy = []
    lm = legacy_home_manifest()
    if lm.is_file():
        legacy.append(lm)
    gi = lm.parent / "skills" / ".gitignore"
    try:
        if gi.is_file() and not is_link(gi) and gi.read_text(encoding="utf-8") == GITIGNORE_TEXT:
            legacy.append(gi)
    except OSError:
        pass

    return State(store, config, gdir, gdir_is_link, skills, global_links, projects, internal,
                 settings, settings_hooks, settings_error, legacy)


# ---------------------------------------------------------------------------------------
# summary
# ---------------------------------------------------------------------------------------
def _linked_col(s: Skill) -> str:
    parts = []
    if s.global_link:
        parts.append("global")
    if s.projects:
        parts.append(f"{len(s.projects)} project{'s' if len(s.projects) != 1 else ''}")
    return " + ".join(parts) or "-"


def format_summary(st: State) -> str:
    out = ["skill-store uninstall", "",
           f"  store:          {st.store}{'' if st.store.exists() else '  (not found)'}",
           f"  Claude config:  {st.config}",
           f"  global skills:  {st.gdir}{'  (itself a link: left alone)' if st.gdir_is_link else ''}"]
    found = [p for p in st.projects if p.exists]
    missing = [p for p in st.projects if not p.exists]
    out.append(f"  projects:       {len(found)} found, {len(missing)} recorded but no longer on disk")
    out.append("")
    if not st.skills:
        out.append(f"No skills cached in {st.store / 'skills'}.")
        return "\n".join(out)
    out.append(f"Cached skills in {st.store / 'skills'} ({len(st.skills)}):")
    w = max(4, max(len(s.name) for s in st.skills))
    out.append(f"  {'NAME':<{w}}  APPROVED  {'LINKED':<20}  {'LISTING':>7}  {'BODY':>6}  SOURCE")
    for s in st.skills:
        src = f"link to checkout {s.external}" if s.external else "store copy"
        out.append(f"  {s.name:<{w}}  {'yes' if s.approved else 'no':<8}  {_linked_col(s):<20}  "
                   f"{s.listing_tokens:>7}  {s.body_tokens:>6}  {src}")
        for p in s.projects:
            out.append(f"  {'':<{w}}    linked in project {p}")
        if s.problem:
            out.append(f"  {'':<{w}}    not restorable: {s.problem}")
        if s.conflict:
            out.append(f"  {'':<{w}}    will be skipped: {s.conflict}")
    rs = [s for s in st.skills if s.restorable]
    out.append("")
    out.append(f"  Skills to be restored: {len(rs)} of {len(st.skills)}. Their totals: listing "
               f"{sum(s.listing_tokens for s in rs)} tokens (loaded on every turn), "
               f"SKILL.md bodies {sum(s.body_tokens for s in rs)} tokens (loaded when invoked).")
    out.append("  (LISTING = description chars/4; BODY = SKILL.md body chars/4, without the frontmatter.)")
    return "\n".join(out)


# ---------------------------------------------------------------------------------------
# plan and execution
# ---------------------------------------------------------------------------------------
class Skip(Exception):
    """An action that turned out not to apply: reported as skipped / left in place."""


@dataclass
class Action:
    group: str
    text: str
    fn: object  # callable() -> str (what was done); raises Skip or an error
    kind: str = "removed"  # report bucket on success: removed | restored


@dataclass
class Report:
    restored: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    left: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


GROUPS = ["Restore into the global skills dir", "Store links", "Hooks in settings.json", "Package",
          "Environment and shell profiles", "Old --project ~ files", "Store directory"]


def _restore_action(st: State, s: Skill) -> Action:
    dest = st.gdir / s.name
    store_skills = st.store / "skills"

    def fn():
        if os.path.lexists(dest) and not (is_link(dest) and points_into(dest, store_skills)):
            raise Skip(f"{s.name}: {dest} exists and is not a skill-store link; left alone")
        st.gdir.mkdir(parents=True, exist_ok=True)
        if s.external is not None:
            if os.path.lexists(dest):
                remove_link(dest)
            kind = create_link(dest, s.external)
            return f"{s.name}: {dest} is now a {kind} to the checkout {s.external}"
        tmp = st.gdir / f".{s.name}.skill-store-restore"
        if os.path.lexists(tmp):
            raise RuntimeError(f"{tmp} already exists; remove it and run again")
        shutil.copytree(s.entry, tmp, symlinks=True)
        had_link = os.path.lexists(dest)
        if had_link:
            remove_link(dest)
        try:
            os.rename(tmp, dest)
        except OSError:
            if had_link and not os.path.lexists(dest):
                create_link(dest, s.entry)  # put the global store link back
            raise
        return f"{s.name}: copied into {dest} (a standalone directory)"

    how = f"link {dest} -> {s.external}" if s.external else f"copy the store copy to {dest}"
    if s.global_link:
        how += " (replaces the global store link)"
    return Action(GROUPS[0], f"{s.name}: {how}", fn, kind="restored")


def _unlink_action(group: str, lp: Path, store_skills: Path, label: str) -> Action:
    def fn():
        if not os.path.lexists(lp):
            raise Skip(f"{lp}: already gone")
        if not (is_link(lp) and points_into(lp, store_skills)):
            raise Skip(f"{lp}: no longer a skill-store link; left alone")
        remove_link(lp)
        return f"link {lp} ({label})"
    return Action(group, f"remove link {lp} ({label})", fn)


def _settings_action(st: State) -> Action:
    def fn():
        data = _read_json(st.settings)
        new, removed = strip_store_hooks(data)
        if not removed:
            raise Skip(f"{st.settings}: no skill-store hooks left")
        text = json.dumps(new, indent=2, ensure_ascii=False) + "\n"
        json.loads(text)  # always valid JSON
        bak = _backup(st.settings)
        atomic_write_text(st.settings, text)
        return (f"{len(removed)} skill-store hook entr{'y' if len(removed) == 1 else 'ies'} from "
                f"{st.settings} (backup: {bak})")
    lines = "; ".join(st.settings_hooks)
    return Action(GROUPS[2], f"remove from {st.settings} (backup first): {lines}", fn)


# ----- package -----------------------------------------------------------------------
def _python_candidates() -> list[str]:
    cands = [sys.executable]
    if IS_WINDOWS and _which("py"):
        rc, out = _run(["py", "-0p"], timeout=30)
        if rc == 0:
            for ln in out.splitlines():
                m = re.search(r"([A-Za-z]:\\.*?python[w]?\.exe)\s*$", ln.strip(), re.I)
                if m:
                    cands.append(m.group(1))
    for n in ("python3", "python"):
        p = _which(n)
        if p:
            cands.append(p)
    seen, out = set(), []
    for c in cands:
        if not c or "WindowsApps" in c:
            continue  # the Microsoft Store alias stub
        k = _key(c)
        if k not in seen:
            seen.add(k)
            out.append(c)
    return out


def _package_actions() -> list[Action]:
    acts: list[Action] = []
    for py in _python_candidates():
        for name in PACKAGE_NAMES:
            rc, out = _run([py, "-m", "pip", "show", name], timeout=60)
            if rc != 0 or not re.search(r"^Name:", out, re.M):
                continue
            loc = re.search(r"^(?:Editable project location|Location):\s*(.+)$", out, re.M)
            where = loc.group(1).strip() if loc else "?"

            def fn(py=py, name=name):
                rc, out = _run([py, "-m", "pip", "uninstall", "-y", name], timeout=300)
                if rc != 0:
                    raise RuntimeError(f"pip uninstall {name} failed for {py}: {out.strip()[-400:]}\n"
                                       f"    run it yourself: {py} -m pip uninstall {name}")
                return f"pip package {name} from {py}"
            acts.append(Action(GROUPS[3], f"pip uninstall {name} ({py}; {where})", fn))
    for tool, list_cmd, rm in (("pipx", ["pipx", "list", "--short"], ["pipx", "uninstall"]),
                               ("uv", ["uv", "tool", "list"], ["uv", "tool", "uninstall"])):
        if not _which(tool):
            continue
        rc, out = _run(list_cmd, timeout=60)
        if rc != 0:
            continue
        for name in PACKAGE_NAMES:
            if any(ln.strip().split(" ")[0] == name for ln in out.splitlines()):
                def fn(rm=rm, name=name, tool=tool):
                    rc, out = _run(rm + [name], timeout=300)
                    if rc != 0:
                        raise RuntimeError(f"{' '.join(rm)} {name} failed: {out.strip()[-400:]}")
                    return f"{tool} install of {name}"
                acts.append(Action(GROUPS[3], f"{' '.join(rm)} {name}", fn))
    tmp = Path(tempfile.gettempdir())
    venv = tmp / "skill-store-test-venv"
    if venv.is_dir() and not _is_reparse_or_link(venv) and (venv / "pyvenv.cfg").is_file():
        acts.append(Action(GROUPS[3], f"delete the test venv {venv} (made by scripts/test-local.*)",
                           lambda: (delete_tree(venv), f"test venv {venv}")[1]))
    for src in sorted(tmp.glob("skill-store-src.*")):
        if src.is_dir() and not _is_reparse_or_link(src) and (src / "pyproject.toml").is_file():
            acts.append(Action(GROUPS[3], f"delete the leftover test copy {src}",
                               lambda src=src: (delete_tree(src), f"test copy {src}")[1]))
    getpip = tmp / "skill-store-get-pip.py"
    if getpip.is_file() and not is_link(getpip):
        acts.append(Action(GROUPS[3], f"delete {getpip}", lambda: (os.unlink(getpip), str(getpip))[1]))
    return acts


# ----- environment and profiles ------------------------------------------------------
def _only_store_files(d: Path) -> bool:
    """True if ``d`` is missing or holds nothing but skill-store's own console scripts."""
    try:
        names = os.listdir(d)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return all(n.lower().startswith("skill-store") for n in names)


def _profile_files() -> list[Path]:
    h = Path.home()
    files = [h / n for n in (".bashrc", ".bash_profile", ".profile", ".zshrc", ".zprofile")]
    for docs in (h / "Documents", h / "OneDrive" / "Documents"):
        for sub in ("WindowsPowerShell", "PowerShell"):
            files += sorted((docs / sub).glob("*profile.ps1")) if (docs / sub).is_dir() else []
    return [f for f in files if f.is_file() and not is_link(f)]


def _profile_hits(f: Path) -> tuple[list[str], list[str]]:
    """(lines to remove, PATH lines removable only if ~/.local/bin holds nothing else)."""
    try:
        text = f.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return [], []
    sure, cond = [], []
    for ln in text.splitlines():
        s = ln.strip()
        if s.startswith("#"):
            continue
        if HOOK_RE.search(s):
            sure.append(ln)
        elif s == README_PATH_LINE:
            cond.append(ln)
    return sure, cond


def _remove_lines(f: Path, lines: set[str]) -> int:
    with open(f, encoding="utf-8", errors="replace", newline="") as fh:
        text = fh.read()
    keep, n = [], 0
    for ln in text.splitlines(keepends=True):
        if ln.rstrip("\r\n") in lines:
            n += 1
        else:
            keep.append(ln)
    if n:
        _backup(f)
        with open(f, "w", encoding="utf-8", newline="") as fh:
            fh.write("".join(keep))
    return n


def _env_actions(st: State) -> tuple[list[Action], list[Action]]:
    """(environment-variable actions, shell-profile actions needing their own confirmation)."""
    acts, profile = [], []
    env = _user_env()
    if env is not None:
        for var in ENV_VARS:
            if env.get(var) is not None:
                def fn(var=var):
                    env.delete(var)
                    return f"user environment variable {var}"
                acts.append(Action(GROUPS[4], f"remove the user environment variable {var}", fn))
        cur = env.get("Path")
        appdata = os.environ.get("APPDATA")
        if cur is not None and appdata:
            value, kind = cur
            pyroot = Path(appdata) / "Python"
            entries = [e for e in str(value).split(";") if e]
            cands = [e for e in entries
                     if _is_within(Path(os.path.expandvars(e)), pyroot)
                     and Path(os.path.expandvars(e)).name.lower() == "scripts"]
            for e in cands:
                def fn(e=e):
                    d = Path(os.path.expandvars(e))
                    if not _only_store_files(d):
                        raise Skip(f"PATH entry {e}: other programs are installed there; left in PATH")
                    value, kind = env.get("Path")
                    parts = [x for x in str(value).split(";") if x and _key(os.path.expandvars(x)) != _key(d)]
                    env.set("Path", ";".join(parts), kind)
                    return f"user PATH entry {e}"
                acts.append(Action(GROUPS[4], f"remove the user PATH entry {e} (the pip --user Scripts "
                                   "dir), only if no other programs are installed there", fn))
    for f in _profile_files():
        sure, cond = _profile_hits(f)
        if not sure and not cond:
            continue

        def fn(f=f, sure=sure, cond=cond):
            drop = set(sure)
            local_bin = Path.home() / ".local" / "bin"
            kept = []
            if cond:
                if _only_store_files(local_bin):
                    drop |= set(cond)
                else:
                    kept = [f"{f}: kept '{c.strip()}' (other programs live in {local_bin})" for c in cond]
            n = _remove_lines(f, drop) if drop else 0  # one backup per file
            if not n:
                raise Skip("; ".join(kept) or f"{f}: the lines were already gone")
            msg = f"{n} line{'s' if n != 1 else ''} from {f} (backup kept): " + " | ".join(
                ln.strip() for ln in sure + cond if ln in drop)
            return msg + ("; " + "; ".join(kept) if kept else "")

        text = f"{f}: remove these lines (a backup is kept):" + "".join(
            f"\n        {ln.strip()}" for ln in sure) + "".join(
            f"\n        {ln.strip()}   (only if ~/.local/bin holds nothing else)" for ln in cond)
        profile.append(Action(GROUPS[4], text, fn))
    return acts, profile


# ----- store directory -------------------------------------------------------------
def delete_tree(root: Path) -> None:
    """Recursive delete that never follows or deletes through a link: it raises on any symlink,
    junction or reparse point instead (callers remove those first, one by one)."""
    root = Path(root)
    if _is_reparse_or_link(root):
        raise RuntimeError(f"refusing to delete {root}: it is a link")

    def rm(p: Path):
        with os.scandir(p) as it:
            entries = list(it)
        for e in entries:
            c = Path(e.path)
            if _is_reparse_or_link(c):
                raise RuntimeError(f"refusing to delete through the link {c}")
            if e.is_dir(follow_symlinks=False):
                rm(c)
            else:
                try:
                    os.unlink(c)
                except PermissionError:
                    os.chmod(c, stat.S_IWRITE)
                    os.unlink(c)
        os.rmdir(p)

    rm(root)


def _store_refusal(st: State) -> str | None:
    s = st.store
    if not os.path.lexists(s):
        return None
    if _is_reparse_or_link(s):
        return f"{s} is itself a link; remove the link and its target yourself"
    if not s.is_dir():
        return f"{s} is not a directory"
    if same_dir(s, Path.home()) or s.parent == s or same_dir(s, st.config):
        return f"{s} is your home, a drive root or the Claude config dir"
    if _is_within(REPO_ROOT, s):
        return f"the checkout {REPO_ROOT} is inside the store {s}; delete the store by hand"
    looks = any(os.path.lexists(s / n) for n in ("store.json", "skills", "build", "usage.jsonl",
                                                 GLOBAL_MANIFEST_NAME))
    if not looks and any(s.iterdir()):
        return f"{s} doesn't look like a skill store (no store.json, skills/ or build/)"
    return None


def _store_action(st: State) -> Action:
    def fn():
        why = _store_refusal(st)
        if why:
            raise RuntimeError(f"store not deleted: {why}")
        if not os.path.lexists(st.store):
            raise Skip(f"{st.store}: already gone")
        notes = []
        for lp in find_links(st.store):
            tgt = link_target(lp)
            remove_link(lp)  # non-recursive; refuses anything that is not a symlink/junction
            notes.append(f"link {lp}" + (f" (its target {tgt} is kept)" if tgt else ""))
        left = find_links(st.store)
        if left:
            raise RuntimeError("store not deleted: links remain inside it: " + ", ".join(map(str, left)))
        delete_tree(st.store)
        return "; ".join(notes + [f"store directory {st.store}"])
    n = len(st.store_internal_links)
    text = f"delete {st.store}"
    if n:
        text += (f": first remove the {n} link{'s' if n != 1 else ''} inside it, one by one and "
                 "non-recursively (their targets are kept), check that none remain, then delete")
    return Action(GROUPS[6], text, fn)


def build_plan(st: State, restore: bool) -> tuple[list[Action], list[Action], Report]:
    """(actions, shell-profile actions, report with the skipped / left-in-place items)."""
    rep = Report()
    acts: list[Action] = []
    store_skills = st.store / "skills"
    restored = set()
    if restore:
        for s in st.skills:
            if s.restorable:
                acts.append(_restore_action(st, s))
                restored.add(s.name)
    for s in st.skills:
        if s.conflict:
            rep.skipped.append(f"{s.name}: not restored, {s.conflict}")
        elif s.problem and restore:
            rep.skipped.append(f"{s.name}: not restored, {s.problem}")
        elif not restore:
            rep.skipped.append(f"{s.name}: not restored (you chose not to)")

    for name, lp in st.global_links.items():
        if _target_name(lp) in restored and lp == st.gdir / _target_name(lp):
            continue  # replaced by the restore
        acts.append(_unlink_action(GROUPS[1], lp, store_skills, "global"))
    for p in st.projects:
        for lps in p.links:
            acts.append(_unlink_action(GROUPS[1], Path(lps), store_skills, f"project {p.root}"))
    if st.settings_hooks:
        acts.append(_settings_action(st))
    elif st.settings_error:
        rep.left.append(f"{st.settings}: {st.settings_error}; check it for skill-store hooks yourself")
    acts += _package_actions()
    env_acts, profile_acts = _env_actions(st)
    acts += env_acts
    for f in st.legacy_files:
        acts.append(Action(GROUPS[5], f"delete the file {f}",
                           lambda f=f: (os.unlink(f), f"file {f}")[1]))
    if os.path.lexists(st.store):
        acts.append(_store_action(st))

    # left in place
    rep.left.append(f"the checkout {REPO_ROOT} (delete it yourself once you're done)")
    for s in st.skills:
        if s.external is not None:
            rep.left.append(f"the checkout {s.external} that {s.name} linked to")
    for p in st.projects:
        for c in p.committed:
            rep.left.append(f"committed in {p.root}: {c}")
        for sh in p.settings_hooks:
            rep.left.append(f"project settings with skill-store hooks (not edited): {sh}")
    missing = [p for p in st.projects if not p.exists]
    for p in missing:
        rep.left.append(f"project not found: {p.root} (from {', '.join(sorted(p.sources))})")
    if st.gdir_is_link:
        rep.left.append(f"{st.gdir} is itself a link and was not managed")
    return acts, profile_acts, rep


def format_plan(acts: list[Action], profile: list[Action], dry: bool) -> str:
    out = ["", "Plan (dry run: nothing will be changed):" if dry else "Plan:"]
    if not acts and not profile:
        out.append("  nothing to do")
    for g in GROUPS:
        items = [a for a in acts if a.group == g] + ([a for a in profile] if g == GROUPS[4] else [])
        if items:
            out.append(f"  {g}:")
            out += [f"    - {a.text}" for a in items]
    return "\n".join(out)


def _hint(st: State) -> list[str]:
    sk = st.store / "skills"
    if IS_WINDOWS:
        find = (f"Get-ChildItem -Path $HOME -Recurse -Directory -Attributes ReparsePoint -ErrorAction "
                f"SilentlyContinue | Where-Object {{ \"$($_.Target)\" -like {_ps_q(str(sk) + '*')} }} "
                "| Select-Object FullName, Target")
        rm = "[System.IO.Directory]::Delete('<path>', $false)   # removes only the link"
    else:
        find = f"find ~ -type l -lname {_sh_q(str(sk) + '/*')} 2>/dev/null"
        rm = "rm -- '<path>'   # no -r, no trailing slash: removes only the link"
    return ["Store links in projects this script didn't find point into", f"  {sk}",
            "and stop working once the store is gone. Find them with:", f"  {find}",
            "and remove each one with:", f"  {rm}"]


def format_report(st: State, rep: Report) -> str:
    out = ["", "Report"]
    for title, items in (("Restored", rep.restored), ("Removed", rep.removed),
                         ("Skipped", rep.skipped), ("Left in place", rep.left), ("Errors", rep.errors)):
        out.append(f"  {title} ({len(items)}):")
        out += [f"    - {i}" for i in items] or ["    (none)"]
    out.append("")
    out += _hint(st)
    out.append("")
    if IS_WINDOWS:
        out.append("WSL has its own installs: run ./uninstall.sh inside each distro you used.")
    out.append("If an AO project runs skill-store in --post-create / --pre-remove, remove those steps. "
               "Open a new terminal (and restart Claude Code) so the environment changes apply.")
    return "\n".join(out)


def execute(acts: list[Action], rep: Report) -> None:
    failed_restores: list[str] = []
    for a in acts:
        if a.group == GROUPS[6] and failed_restores:
            # The store may hold the only copy of a skill whose restore failed: keep it.
            rep.errors.append(f"{a.text}: not done, because restoring {', '.join(failed_restores)} "
                              "failed and the store still holds the only copy. Fix the error above "
                              "and run the uninstall again")
            continue
        try:
            msg = a.fn()
        except Skip as exc:
            rep.skipped.append(str(exc))
        except Exception as exc:  # noqa: BLE001 - report and continue
            rep.errors.append(f"{a.text}: {exc}")
            if a.kind == "restored":
                failed_restores.append(a.text.split(":", 1)[0])
        else:
            (rep.restored if a.kind == "restored" else rep.removed).append(msg)


# ---------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------
def _ask(question: str) -> bool:
    try:
        ans = _input(f"{question} [y/N] ")
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return ans.strip().lower() in ("y", "yes")


def parse_args(argv):
    ap = argparse.ArgumentParser(
        prog="uninstall",
        description="Restore cached skills (optional) and remove every trace of skill-store except "
                    "this checkout.")
    ap.add_argument("--dry-run", action="store_true", help="show everything, change nothing")
    ap.add_argument("--yes", "-y", action="store_true",
                    help="non-interactive: answer yes to every confirmation (restore stays off unless --restore)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--restore", dest="restore", action="store_true", default=None,
                   help="restore the cached skills into the global Claude skills dir")
    g.add_argument("--no-restore", dest="restore", action="store_false", help="don't restore")
    ap.add_argument("--store", help="the store dir (default: SKILL_STORE_HOME, then ~/.skill-store)")
    ap.add_argument("--project", action="append", default=[], metavar="DIR",
                    help="also clean this project's links (repeatable)")
    return ap.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    st = discover(args.store, args.project)
    print(format_summary(st))

    n = sum(1 for s in st.skills if s.restorable)
    if args.restore is not None:
        restore = args.restore
    elif args.yes or n == 0:
        restore = False
    else:
        print()
        restore = _ask(f"Restore all {n} cached skills into your global Claude skills ({st.gdir})?")
    restore = restore and n > 0

    acts, profile, rep = build_plan(st, restore)
    print(format_plan(acts, profile, args.dry_run))
    if args.dry_run:
        print(format_report(st, rep).replace("Report", "Report (dry run: nothing was changed)", 1))
        return 0
    if not acts and not profile:
        print(format_report(st, rep))
        return 0
    if not args.yes:
        print()
        if not _ask("Proceed and remove everything listed above?"):
            print("Aborted: nothing was changed.")
            return 1
    if profile and not args.yes:
        if not _ask(f"Also remove the skill-store lines from the {len(profile)} shell profile "
                    "file(s) listed above (a backup of each file is kept)?"):
            for a in profile:
                rep.left.append(a.text.replace(": remove these lines (a backup is kept):",
                                               ": lines kept (you chose to keep them):", 1))
            profile = []
    execute(acts + profile, rep)
    print(format_report(st, rep))
    if os.environ.get("SKILL_STORE_HOME"):
        print("SKILL_STORE_HOME is still set in this shell: "
              + ("Remove-Item Env:SKILL_STORE_HOME" if IS_WINDOWS else "unset SKILL_STORE_HOME"))
    return 1 if rep.errors else 0


if __name__ == "__main__":
    sys.exit(main())
