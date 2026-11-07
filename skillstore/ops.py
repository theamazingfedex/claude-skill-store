"""Operations behind the CLI commands. Each returns a JSON-serializable result dict.

Every link command works on one *scope target* (``ctx.target``):

- project scope (default): ``<project>/.claude/skills``, manifest
  ``<project>/.claude/skills.manifest.json`` (committed), plus a managed ``.gitignore``;
- global scope (``--global``): the user-level skills dir (``$CLAUDE_CONFIG_DIR/skills`` or
  ``~/.claude/skills``), manifest ``<store>/global.manifest.json``, no ``.gitignore``.

Both scopes follow the same rules: only links that resolve into the store are ever removed
(non-recursively), and foreign entries are never touched.

Cap semantics (see README.md and docs/design.md):

- The cap counts *store links* in the target's skills dir (foreign links and real
  directories never count and are never touched).
- ``link`` adds the skill to the manifest; if that puts the target over its cap, the least
  recently used other skills are unlinked **and removed from the manifest**, and reported.
- ``prune [--cap N]`` does the same on demand, down to N (default: the manifest cap).
- ``sync`` never edits the manifest (it runs at session start in every checkout; it must not
  create git diffs). If the manifest lists more linkable skills than the cap, sync links the
  ``cap`` most recently used ones and reports the rest as ``over_cap``.
- "Recently used" = latest entry in usage.jsonl (store.json ``last_used`` as a fallback).
  Never-used skills are the least recent; ties go by manifest order (earlier = older).
"""

from __future__ import annotations

import json
import os
import shlex
from datetime import datetime, timezone
from pathlib import Path

from skillstore import admission
from skillstore.links import (IS_WINDOWS, RefusedError, create_link, is_link, link_kind,
                              points_into, remove_link, same_target)
from skillstore.manifest import (GITIGNORE_TEXT, Manifest, ensure_target_dirs, gitignore_ok,
                                 init_project as _init_project)
from skillstore.paths import (GlobalPaths, ProjectPaths, SkillStoreError, legacy_home_manifest,
                              resolve_claude_config, same_dir, validate_name)
from skillstore.store import Store, now_iso

_EPOCH = datetime.min.replace(tzinfo=timezone.utc)


class Ctx:
    """``project_root`` selects the project scope; ``global_scope=True`` selects the global
    scope (``ctx.target``). ``ctx.globals`` is always available, e.g. for ``status``."""

    def __init__(self, store_root: Path, project_root: Path | None = None, *,
                 global_scope: bool = False, config_dir: Path | None = None):
        self.store = Store(store_root)
        self.project = ProjectPaths(project_root) if project_root is not None else None
        self.globals = GlobalPaths(store_root, config_dir or resolve_claude_config())
        self.target = self.globals if global_scope else self.project
        if self.target is None:
            raise SkillStoreError("no project given for a project-scope command")


# ---------------------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------------------
def store_links(ctx: Ctx, target=None) -> dict[str, Path]:
    """Entries of the target's skills dir that are links resolving into the store."""
    out: dict[str, Path] = {}
    d = (target or ctx.target).skills
    if not d.is_dir() or is_link(d):
        return out
    for entry in sorted(d.iterdir(), key=lambda p: p.name):
        if is_link(entry) and points_into(entry, ctx.store.paths.skills):
            out[entry.name] = entry
    return out


def _where(target) -> dict:
    """Result keys naming the scope target (``project`` stays for project-scope callers)."""
    if target.is_global:
        return {"scope": "global", "project": None, "skills_dir": str(target.skills)}
    return {"scope": "project", "project": str(target.root), "skills_dir": str(target.skills)}


def _check_target(ctx: Ctx) -> None:
    """Refuse to manage a skills dir that would make the two scopes fight over one directory,
    or (global scope) a skills dir that is itself a link."""
    t = ctx.target
    if t.is_global:
        if is_link(t.skills):
            raise RefusedError(f"{t.skills} is itself a link; skill-store manages links only inside "
                               "a real skills directory and leaves this one alone")
        return
    if ctx.globals.manifest.is_file() and same_dir(t.skills, ctx.globals.skills):
        raise RefusedError(
            f"{t.skills} is the global skills directory, which the global scope manages "
            f"({ctx.globals.manifest}). Use 'skill-store --global <command>' instead of treating "
            f"{t.root} as a project")


def _legacy_only(ctx: Ctx, manifest: Manifest | None) -> set[str]:
    """Global scope: skills listed by the old ``--project ~`` manifest (same skills dir) that are
    not yet in the global manifest. Their links are left alone and not counted until migrated."""
    if not ctx.target.is_global:
        return set()
    legacy = _load_legacy(ctx)
    if legacy is None or not same_dir(legacy.path.parent / "skills", ctx.globals.skills):
        return set()
    listed = set(manifest.skills) if manifest else set()
    return {n for n in legacy.skills if n not in listed}


def managed_links(ctx: Ctx, manifest: Manifest | None) -> dict[str, Path]:
    keep_out = _legacy_only(ctx, manifest)
    return {n: p for n, p in store_links(ctx).items() if n not in keep_out}


def _recency_key(name: str, last_used: dict, order: list[str]):
    idx = order.index(name) if name in order else -1
    return (last_used.get(name, _EPOCH), idx)


def lru_sorted(names, last_used: dict, order: list[str]) -> list[str]:
    """Least recently used first."""
    return sorted(names, key=lambda n: _recency_key(n, last_used, order))


def _load_or_new_manifest(ctx: Ctx) -> Manifest:
    m = Manifest.load(ctx.target)
    return m if m is not None else Manifest(ctx.target.manifest)


def _iso(dt):
    return dt.isoformat().replace("+00:00", "Z") if dt and dt != _EPOCH else None


# ---------------------------------------------------------------------------------------
# the old "--project ~" workaround
# ---------------------------------------------------------------------------------------
def _load_legacy(ctx: Ctx) -> Manifest | None:
    path = legacy_home_manifest()
    if not path.is_file():
        return None
    try:
        return Manifest.load(ProjectPaths(path.parent.parent))
    except SkillStoreError:
        return Manifest(path, {"version": 1, "skills": []})


def _q(path: Path | str) -> str:
    s = str(path)
    if IS_WINDOWS:
        return "'" + s.replace("'", "''") + "'"
    return shlex.quote(s)


def legacy_info(ctx: Ctx) -> dict | None:
    """Detect ``~/.claude/skills.manifest.json`` (written by ``--project ~``) and list the
    exact commands that move it to the global scope. Never deletes or changes anything."""
    legacy = _load_legacy(ctx)
    if legacy is None:
        return None
    home_project = ProjectPaths(legacy.path.parent.parent)
    try:
        gm = Manifest.load(ctx.globals)
    except SkillStoreError:
        gm = None
    in_global = set(gm.skills) if gm else set()
    pending = [n for n in legacy.skills if n not in in_global]
    shared = same_dir(home_project.skills, ctx.globals.skills)
    gi = home_project.gitignore
    try:
        managed_gi = gi.is_file() and not is_link(gi) and gi.read_text(encoding="utf-8") == GITIGNORE_TEXT
    except OSError:
        managed_gi = False
    shell = "powershell" if IS_WINDOWS else "sh"
    commands = []
    if pending:
        commands.append("skill-store migrate-global")
    if not shared:
        # The old links are in a skills dir Claude Code no longer reads (CLAUDE_CONFIG_DIR is
        # elsewhere); remove them, store links only, non-recursively.
        commands.append(f"skill-store --project {_q(home_project.root)} unlink --all")
    files = [legacy.path] + ([gi] if managed_gi else [])
    if IS_WINDOWS:
        # File.Delete removes one file and can never delete a directory.
        commands += [f"[System.IO.File]::Delete({_q(f)})" for f in files]
    else:
        commands.append("rm -- " + " ".join(_q(f) for f in files))
    return {"manifest": str(legacy.path), "skills": list(legacy.skills), "pending": pending,
            "same_skills_dir": shared, "gitignore": str(gi) if managed_gi else None,
            "shell": shell, "commands": commands}


def _evict(ctx: Ctx, manifest: Manifest, cap: int, protect: set[str], last_used: dict) -> list[dict]:
    evicted = []
    links = managed_links(ctx, manifest)
    candidates = lru_sorted([n for n in links if n not in protect], last_used, manifest.skills)
    while len(links) > cap and candidates:
        victim = candidates.pop(0)
        remove_link(links.pop(victim))
        manifest.remove(victim)
        evicted.append({"skill": victim, "last_used": _iso(last_used.get(victim))})
    return evicted


# ---------------------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------------------
def init(ctx: Ctx) -> dict:
    return {"store": str(ctx.store.paths.root), "created": ctx.store.init()}


def init_project(ctx: Ctx) -> dict:
    _check_target(ctx)
    return {"project": str(ctx.project.root), "created": _init_project(ctx.project)}


def approve(ctx: Ctx, name: str) -> dict:
    validate_name(name)
    data = ctx.store.load()
    if not ctx.store.has_skill(name):
        raise SkillStoreError(f"{name!r} is not in the store ({ctx.store.paths.skill(name)})")
    entry = ctx.store.entry(data, name, create=True)
    was = bool(entry.get("approved"))
    entry["approved"] = True
    entry["approved_at"] = entry.get("approved_at") if was else now_iso()
    ctx.store.save(data)
    result = admission.check_skill(ctx.store.paths.skill(name), expected_name=name)
    return {"skill": name, "approved": True, "already_approved": was,
            "admission_ok": result.ok, "admission_errors": result.errors}


def check(ctx: Ctx, target: str) -> dict:
    p = Path(target)
    if p.is_dir() and (os.sep in target or "/" in target or not _is_store_name(ctx, target)):
        res = admission.check_skill(p, expected_name=p.resolve().name)
    else:
        validate_name(target)
        res = admission.check_skill(ctx.store.paths.skill(target), expected_name=target)
    return res.as_dict()


def _is_store_name(ctx: Ctx, target: str) -> bool:
    try:
        return ctx.store.has_skill(validate_name(target))
    except SkillStoreError:
        return False


def _admissible(ctx: Ctx, name: str) -> admission.CheckResult:
    return admission.check_skill(ctx.store.paths.skill(name), expected_name=name)


def link(ctx: Ctx, name: str) -> dict:
    validate_name(name)
    data = ctx.store.load()
    if not ctx.store.has_skill(name):
        raise SkillStoreError(f"{name!r} is not in the store; build it first (skill-store build {name} --recipe ...)")
    res = _admissible(ctx, name)
    if not res.ok:
        raise SkillStoreError(f"{name!r} fails admission (skill-store check {name}):\n"
                              + "\n".join(f"  - {e}" for e in res.errors))
    if not ctx.store.is_approved(data, name):
        raise SkillStoreError(
            f"{name!r} has never been approved for linking. Linking a new skill needs the user's "
            f"explicit approval: skill-store approve {name}"
        )
    _check_target(ctx)
    t = ctx.target
    manifest = _load_or_new_manifest(ctx)
    if manifest.cap < 1:
        where = "global scope's" if t.is_global else "project's"
        raise SkillStoreError(f"the {where} cap is {manifest.cap}; raise 'cap' in {manifest.path}")
    ensure_target_dirs(t)
    target = ctx.store.paths.skill(name)
    lp = t.link(name)
    state = "linked"
    if os.path.lexists(lp):
        if same_target(lp, target):
            state = "already linked"
        elif points_into(lp, ctx.store.paths.skills):
            remove_link(lp)
            create_link(lp, target)
            state = "relinked"
        else:
            kind = link_kind(lp) or ("directory" if lp.is_dir() else "file")
            raise RefusedError(f"{lp} already exists ({kind}, not a skill-store link); leaving it alone")
    else:
        create_link(lp, target)
    manifest.add(name)
    last_used = ctx.store.last_used_map(data)
    evicted = _evict(ctx, manifest, manifest.cap, {name}, last_used)
    manifest.save()
    return {"skill": name, "state": state, "link": str(lp), "evicted": evicted,
            "cap": manifest.cap, "linked_count": len(managed_links(ctx, manifest)),
            "manifest": str(manifest.path), **_where(t)}


def unlink(ctx: Ctx, name: str) -> dict:
    validate_name(name)
    _check_target(ctx)
    lp = ctx.target.link(name)
    state = "not linked"
    if os.path.lexists(lp):
        if not is_link(lp):
            raise RefusedError(f"refusing to unlink {lp}: it is a real directory/file, not a link")
        if not points_into(lp, ctx.store.paths.skills):
            raise RefusedError(f"refusing to unlink {lp}: it is not a link into the skill store")
        remove_link(lp)
        state = "unlinked"
    manifest = Manifest.load(ctx.target)
    removed = False
    if manifest is not None and manifest.remove(name):
        manifest.save()
        removed = True
    target = ctx.store.paths.skill(name)
    return {"skill": name, "state": state, "removed_from_manifest": removed,
            "store_copy_intact": (target / "SKILL.md").is_file() if target.exists() else None,
            **_where(ctx.target)}


def unlink_all(ctx: Ctx) -> dict:
    """Remove every link in the target's skills dir that resolves into the store.

    Non-recursive (same path as ``unlink <name>``), does NOT touch the manifest, leaves foreign
    links and real directories alone. Meant for an AO ``--pre-remove`` step: run it before a
    worktree is deleted so a recursive delete of the worktree cannot reach the store.
    Idempotent; does not require the store to exist.
    """
    _check_target(ctx)
    result = {**_where(ctx.target), "removed": [], "errors": []}
    for name, lp in store_links(ctx).items():
        try:
            remove_link(lp)
            result["removed"].append(name)
        except (SkillStoreError, OSError) as exc:
            result["errors"].append(f"{name}: {exc}")
    result["remaining_store_links"] = sorted(store_links(ctx))
    return result


def sync(ctx: Ctx) -> dict:
    t = ctx.target
    result = {**_where(t), "linked": [], "relinked": [], "already": [],
              "removed": [], "unlinked_over_cap": [], "unlinked_failed_check": [],
              "unlinked_unavailable": [], "needs_approval": [], "missing": [],
              "failed_check": {}, "over_cap": [], "conflicts": [], "errors": [], "warnings": []}
    if t.is_global:
        result["legacy"] = legacy_info(ctx)
        result["legacy_kept"] = []
    if not ctx.store.paths.exists():
        result["skipped"] = f"no skill store at {ctx.store.paths.root}"
        return result
    manifest = Manifest.load(t)
    if manifest is None:
        result["skipped"] = f"no manifest at {t.manifest}"
        return result
    _check_target(ctx)
    ensure_target_dirs(t)
    if not t.is_global and not gitignore_ok(t):
        result["warnings"].append(f"{t.gitignore} does not contain '*': links could be committed")
    data = ctx.store.load()
    last_used = ctx.store.last_used_map(data)

    ok = []
    for name in manifest.skills:
        if not ctx.store.has_skill(name):
            result["missing"].append(name)
        elif not ctx.store.is_approved(data, name):
            result["needs_approval"].append(name)
        else:
            res = _admissible(ctx, name)
            if res.ok:
                ok.append(name)
            else:
                result["failed_check"][name] = res.errors
    keep_sorted = list(reversed(lru_sorted(ok, last_used, manifest.skills)))  # most recent first
    keep = set(keep_sorted[: manifest.cap])
    result["over_cap"] = [n for n in manifest.skills if n in ok and n not in keep]

    # Every removed store link is reported under exactly one reason.
    listed = set(manifest.skills)
    legacy_only = _legacy_only(ctx, manifest)
    relink = set()
    for name, lp in store_links(ctx).items():
        if name in legacy_only:
            result["legacy_kept"].append(name)  # still owned by the old workaround's manifest
            continue
        if name in keep and same_target(lp, ctx.store.paths.skill(name)):
            continue
        if name in keep:
            reason = "relink"  # listed and linkable, but pointing at the wrong store skill
        elif name not in listed:
            reason = "removed"  # no longer listed in the manifest
        elif name in result["over_cap"]:
            reason = "unlinked_over_cap"
        elif name in result["failed_check"]:
            reason = "unlinked_failed_check"
        else:
            reason = "unlinked_unavailable"  # listed, but not in store or not approved
        try:
            remove_link(lp)
        except (SkillStoreError, OSError) as exc:  # e.g. a junction locked by AV/indexer
            result["errors"].append(f"{name}: {exc}")
            continue
        if reason == "relink":
            relink.add(name)
        else:
            result[reason].append(name)

    for name in [n for n in manifest.skills if n in keep]:
        lp = t.link(name)
        try:
            if same_target(lp, ctx.store.paths.skill(name)):
                result["already"].append(name)
            elif os.path.lexists(lp):
                result["conflicts"].append(name)
            else:
                create_link(lp, ctx.store.paths.skill(name))
                result["relinked" if name in relink else "linked"].append(name)
        except SkillStoreError as exc:
            result["errors"].append(f"{name}: {exc}")
        except OSError as exc:
            result["errors"].append(f"{name}: {exc}")
    result["cap"] = manifest.cap
    result["linked_count"] = len(managed_links(ctx, manifest))
    return result


def prune(ctx: Ctx, cap: int | None = None) -> dict:
    ctx.store.require()
    _check_target(ctx)
    manifest = _load_or_new_manifest(ctx)
    cap = manifest.cap if cap is None else cap
    if cap < 0:
        raise SkillStoreError("--cap must be >= 0")
    data = ctx.store.load()
    evicted = _evict(ctx, manifest, cap, set(), ctx.store.last_used_map(data))
    if evicted and manifest.path.exists():
        manifest.save()
    return {"cap": cap, "evicted": evicted, "linked_count": len(managed_links(ctx, manifest)),
            **_where(ctx.target)}


def _unmanaged(target, skip: dict) -> list[dict]:
    out = []
    if target.skills.is_dir() and not is_link(target.skills):
        for entry in sorted(target.skills.iterdir(), key=lambda p: p.name):
            if entry.name.startswith(".") or entry.name in skip:
                continue
            out.append({"name": entry.name, "kind": link_kind(entry) or
                        ("directory" if entry.is_dir() else "file")})
    return out


def _safe_manifest(target) -> Manifest | None:
    try:
        return Manifest.load(target)
    except SkillStoreError:
        return None


def status(ctx: Ctx) -> dict:
    data = ctx.store.load()
    last_used = ctx.store.last_used_map(data)
    t = ctx.target
    manifest = Manifest.load(t)
    listed = manifest.skills if manifest else []
    links = managed_links(ctx, manifest)
    gmanifest = manifest if t.is_global else _safe_manifest(ctx.globals)
    glinks = links if t.is_global else store_links(ctx, ctx.globals)
    names = sorted(set(ctx.store.skill_names_on_disk()) | set(data["skills"]))
    skills = []
    for n in names:
        e = data["skills"].get(n, {})
        on_disk = ctx.store.has_skill(n)
        skills.append({
            "skill": n,
            "in_store": on_disk,
            "approved": bool(e.get("approved")),
            "in_manifest": n in listed,
            "linked": n in links,
            "linked_globally": n in glinks,
            "last_used": _iso(last_used.get(n)),
            "built_at": e.get("built_at"),
            "source": e.get("source"),
            "admission_ok": _admissible(ctx, n).ok if on_disk else None,
        })
    others = _unmanaged(t, store_links(ctx))
    return {"store": str(ctx.store.paths.root), **_where(t),
            "manifest": str(t.manifest) if manifest else None,
            "cap": manifest.cap if manifest else None, "linked_count": len(links),
            "skills": skills, "unmanaged_entries": others,
            "missing_from_store": [n for n in listed if not ctx.store.has_skill(n)],
            "global": {"skills_dir": str(ctx.globals.skills),
                       "manifest": str(ctx.globals.manifest) if gmanifest else None,
                       "cap": gmanifest.cap if gmanifest else None,
                       "linked": sorted(glinks)},
            "legacy": legacy_info(ctx)}


def migrate_global(ctx: Ctx) -> dict:
    """Move the old ``--project ~`` workaround to the global scope: link every skill its
    manifest lists with ``--global link`` (an existing store link is adopted as is). Deletes
    nothing except store links (only via cap eviction); prints the cleanup commands."""
    if not ctx.target.is_global:
        raise SkillStoreError("migrate-global works on the global scope only")
    before = legacy_info(ctx)
    result = {"legacy": before, "migrated": [], "failed": {}, "evicted": []}
    if before is None:
        return result
    legacy = _load_legacy(ctx)
    gm = _load_or_new_manifest(ctx)
    if not gm.path.exists():
        if "cap" in legacy.data:
            gm.data["cap"] = legacy.cap
        ctx.store.require()
        gm.save()
    for name in before["pending"]:
        try:
            r = link(ctx, name)
        except SkillStoreError as exc:
            result["failed"][name] = str(exc)
            continue
        result["migrated"].append({"skill": name, "state": r["state"]})
        result["evicted"] += r["evicted"]
    result["legacy"] = legacy_info(ctx)
    return result


def log_usage(store_root: Path, stdin_text: str, environ=None) -> dict:
    """Hook entry point. Callers must swallow every exception (the hook must never fail)."""
    environ = os.environ if environ is None else environ
    stdin_text = stdin_text.lstrip("﻿")  # a UTF-8 BOM would otherwise fail the JSON parse
    try:
        payload = json.loads(stdin_text) if stdin_text.strip() else {}
    except ValueError:
        return {"logged": False, "reason": "stdin is not JSON"}
    if not isinstance(payload, dict) or payload.get("tool_name") != "Skill":
        return {"logged": False, "reason": "not a Skill tool call"}
    tool_input = payload.get("tool_input") or {}
    raw = tool_input.get("skill") or tool_input.get("command") or ""
    if not isinstance(raw, str) or not raw.strip():
        return {"logged": False, "reason": "no skill name in tool_input"}
    skill = raw.strip().lstrip("/").split()[0]
    store = Store(store_root)
    if not store.paths.exists():
        return {"logged": False, "reason": "no store"}
    record = {
        "skill": skill,
        "session": payload.get("session_id"),
        "project": environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd"),
        "time": now_iso(),
    }
    # Append only. The hook runs in many sessions at once; a read-modify-write of store.json
    # here would race with approve/build and lose updates. usage.jsonl is authoritative for
    # recency; store.json ``last_used`` is refreshed from it whenever a command saves store.json.
    store.append_usage(record)
    return {"logged": True, "record": record}
