"""Operations behind the CLI commands. Each returns a JSON-serialisable result dict.

Cap semantics (see README.md and docs/design.md):

- The cap counts *store links* in the project's ``.claude/skills`` (foreign links and real
  directories never count and are never touched).
- ``link`` adds the skill to the manifest; if that puts the project over its cap, the least
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
from datetime import datetime, timezone
from pathlib import Path

from skillstore import admission
from skillstore.links import (RefusedError, create_link, is_link, link_kind, points_into,
                              remove_link, same_target)
from skillstore.manifest import (Manifest, ensure_project_dirs, gitignore_ok,
                                 init_project as _init_project)
from skillstore.paths import ProjectPaths, SkillStoreError, validate_name
from skillstore.store import Store, now_iso

_EPOCH = datetime.min.replace(tzinfo=timezone.utc)


class Ctx:
    def __init__(self, store_root: Path, project_root: Path):
        self.store = Store(store_root)
        self.project = ProjectPaths(project_root)


# ---------------------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------------------
def store_links(ctx: Ctx) -> dict[str, Path]:
    """Entries of <project>/.claude/skills that are links resolving into the store."""
    out: dict[str, Path] = {}
    d = ctx.project.skills
    if not d.is_dir() or is_link(d):
        return out
    for entry in sorted(d.iterdir(), key=lambda p: p.name):
        if is_link(entry) and points_into(entry, ctx.store.paths.skills):
            out[entry.name] = entry
    return out


def _recency_key(name: str, last_used: dict, order: list[str]):
    idx = order.index(name) if name in order else -1
    return (last_used.get(name, _EPOCH), idx)


def lru_sorted(names, last_used: dict, order: list[str]) -> list[str]:
    """Least recently used first."""
    return sorted(names, key=lambda n: _recency_key(n, last_used, order))


def _load_or_new_manifest(ctx: Ctx) -> Manifest:
    m = Manifest.load(ctx.project)
    return m if m is not None else Manifest(ctx.project.manifest)


def _iso(dt):
    return dt.isoformat().replace("+00:00", "Z") if dt and dt != _EPOCH else None


def _evict(ctx: Ctx, manifest: Manifest, cap: int, protect: set[str], last_used: dict) -> list[dict]:
    evicted = []
    links = store_links(ctx)
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
    manifest = _load_or_new_manifest(ctx)
    if manifest.cap < 1:
        raise SkillStoreError(f"the project's cap is {manifest.cap}; raise 'cap' in {manifest.path}")
    ensure_project_dirs(ctx.project)
    target = ctx.store.paths.skill(name)
    lp = ctx.project.link(name)
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
            "cap": manifest.cap, "linked_count": len(store_links(ctx))}


def unlink(ctx: Ctx, name: str) -> dict:
    validate_name(name)
    lp = ctx.project.link(name)
    state = "not linked"
    if os.path.lexists(lp):
        if not is_link(lp):
            raise RefusedError(f"refusing to unlink {lp}: it is a real directory/file, not a link")
        if not points_into(lp, ctx.store.paths.skills):
            raise RefusedError(f"refusing to unlink {lp}: it is not a link into the skill store")
        remove_link(lp)
        state = "unlinked"
    manifest = Manifest.load(ctx.project)
    removed = False
    if manifest is not None and manifest.remove(name):
        manifest.save()
        removed = True
    target = ctx.store.paths.skill(name)
    return {"skill": name, "state": state, "removed_from_manifest": removed,
            "store_copy_intact": (target / "SKILL.md").is_file() if target.exists() else None}


def unlink_all(ctx: Ctx) -> dict:
    """Remove every link in <project>/.claude/skills that resolves into the store.

    Non-recursive (same path as ``unlink <name>``), does NOT touch the manifest, leaves foreign
    links and real directories alone. Meant for an AO ``--pre-remove`` step: run it before a
    worktree is deleted so a recursive delete of the worktree cannot reach the store.
    Idempotent; does not require the store to exist.
    """
    result = {"project": str(ctx.project.root), "removed": [], "errors": []}
    for name, lp in store_links(ctx).items():
        try:
            remove_link(lp)
            result["removed"].append(name)
        except (SkillStoreError, OSError) as exc:
            result["errors"].append(f"{name}: {exc}")
    result["remaining_store_links"] = sorted(store_links(ctx))
    return result


def sync(ctx: Ctx) -> dict:
    result = {"project": str(ctx.project.root), "linked": [], "relinked": [], "already": [],
              "removed": [], "unlinked_over_cap": [], "unlinked_failed_check": [],
              "unlinked_unavailable": [], "needs_approval": [], "missing": [],
              "failed_check": {}, "over_cap": [], "conflicts": [], "errors": [], "warnings": []}
    if not ctx.store.paths.exists():
        result["skipped"] = f"no skill store at {ctx.store.paths.root}"
        return result
    manifest = Manifest.load(ctx.project)
    if manifest is None:
        result["skipped"] = f"no manifest at {ctx.project.manifest}"
        return result
    ensure_project_dirs(ctx.project)
    if not gitignore_ok(ctx.project):
        result["warnings"].append(f"{ctx.project.gitignore} does not contain '*': links could be committed")
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
    relink = set()
    for name, lp in store_links(ctx).items():
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
        lp = ctx.project.link(name)
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
    result["linked_count"] = len(store_links(ctx))
    return result


def prune(ctx: Ctx, cap: int | None = None) -> dict:
    ctx.store.require()
    manifest = _load_or_new_manifest(ctx)
    cap = manifest.cap if cap is None else cap
    if cap < 0:
        raise SkillStoreError("--cap must be >= 0")
    data = ctx.store.load()
    evicted = _evict(ctx, manifest, cap, set(), ctx.store.last_used_map(data))
    if evicted and manifest.path.exists():
        manifest.save()
    return {"cap": cap, "evicted": evicted, "linked_count": len(store_links(ctx))}


def status(ctx: Ctx) -> dict:
    data = ctx.store.load()
    last_used = ctx.store.last_used_map(data)
    manifest = Manifest.load(ctx.project)
    listed = manifest.skills if manifest else []
    links = store_links(ctx)
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
            "last_used": _iso(last_used.get(n)),
            "built_at": e.get("built_at"),
            "source": e.get("source"),
            "admission_ok": _admissible(ctx, n).ok if on_disk else None,
        })
    others = []
    if ctx.project.skills.is_dir():
        for entry in sorted(ctx.project.skills.iterdir(), key=lambda p: p.name):
            if entry.name.startswith(".") or entry.name in links:
                continue
            others.append({"name": entry.name, "kind": link_kind(entry) or
                           ("directory" if entry.is_dir() else "file")})
    return {"store": str(ctx.store.paths.root), "project": str(ctx.project.root),
            "manifest": str(ctx.project.manifest) if manifest else None,
            "cap": manifest.cap if manifest else None, "linked_count": len(links),
            "skills": skills, "unmanaged_entries": others,
            "missing_from_store": [n for n in listed if not ctx.store.has_skill(n)]}


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
