"""Command-line interface: ``skill-store <command>`` (or ``python -m skillstore``)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from skillstore import __version__, ops
from skillstore.paths import SkillStoreError, legacy_home_manifest, resolve_project, resolve_store


def _common() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--store", default=argparse.SUPPRESS,
                   help="store root (default: $SKILL_STORE_HOME or ~/.skill-store)")
    p.add_argument("--project", default=argparse.SUPPRESS,
                   help="project root (default: $CLAUDE_PROJECT_DIR, git toplevel, or cwd)")
    p.add_argument("--global", dest="global_scope", action="store_true", default=argparse.SUPPRESS,
                   help="use the global scope: link into $CLAUDE_CONFIG_DIR/skills or "
                        "~/.claude/skills (manifest: <store>/global.manifest.json)")
    p.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                   help="print the result as JSON")
    return p


def build_parser() -> argparse.ArgumentParser:
    common = _common()
    parser = argparse.ArgumentParser(
        prog="skill-store", parents=[common],
        description="Keep Claude Code skills in a user-level store and link an approved, "
                    "capped set into each project (or, with --global, into ~/.claude/skills). "
                    "Unlinking never deletes.")
    parser.add_argument("--version", action="version", version=f"skill-store {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    sub.required = True

    def add(name, help_):
        return sub.add_parser(name, parents=[common], help=help_, description=help_)

    add("init", "create the store layout (idempotent)")
    add("init-project", "create .claude/skills/.gitignore and an empty manifest in the project")
    p = add("build", "build a skill with skill-seekers 3.1.3 and post-process it into the store")
    p.add_argument("name")
    p.add_argument("--recipe", required=True, help="build recipe JSON (see examples/recipes/)")
    p.add_argument("--skip-scrape", action="store_true", help="rebuild from cached raw pages")
    p.add_argument("--any-skill-seekers-version", action="store_true",
                   help="allow a skill-seekers version other than 3.1.3")
    p.add_argument("--no-claude-validate", action="store_true",
                   help="skip 'claude plugin validate' even if claude is on PATH")
    p = add("postprocess", "post-process build/<name>/output/<name> (or --from DIR) into the store")
    p.add_argument("name")
    p.add_argument("--recipe", help="recipe JSON (default: build/<name>/recipe.json if present)")
    p.add_argument("--from", dest="src", help="source skill dir (default: build/<name>/output/<name>)")
    p.add_argument("--no-claude-validate", action="store_true")
    p = add("check", "check admission rules for a store skill name or a skill directory")
    p.add_argument("target")
    p = add("approve", "record the user's explicit approval to link a skill")
    p.add_argument("name")
    p = add("link", "link an approved skill into the project, or with --global into the user-level "
                    "skills dir (adds it to the manifest, enforces the cap)")
    p.add_argument("name")
    p = add("unlink", "remove a skill's link (never its store copy) and drop it from the manifest; "
                      "--all removes every store link and leaves the manifest alone")
    p.add_argument("name", nargs="?")
    p.add_argument("--all", action="store_true", dest="all_links",
                   help="remove every link into the store (for AO --pre-remove); manifest unchanged")
    p = add("sync", "make the project's links match its manifest (idempotent; for SessionStart)")
    p.add_argument("--quiet", action="store_true", help="no output on success; always exit 0")
    p.add_argument("--hook-output", action="store_true",
                   help="for the SessionStart hook: implies --quiet; when links were created, print "
                        "Claude Code hook JSON that asks it to re-scan skills and tells the user "
                        "to run /reload-skills if they are missing; otherwise print nothing")
    add("status", "show store skills, approval, links in this project (or --global), global links, "
                  "last use and cap")
    add("migrate-global", "move the old '--project ~' setup to the global scope (links only; "
                          "prints the cleanup commands, deletes no files)")
    p = add("prune", "unlink least recently used skills down to the cap (on demand only)")
    p.add_argument("--cap", type=int, help="target cap (default: the manifest cap)")
    add("log-usage", "PostToolUse hook entry point: log a Skill tool call (always exits 0)")
    return parser


_GLOBAL_COMMANDS = {"link", "unlink", "sync", "status", "prune", "migrate-global",
                    "init", "approve", "check"}  # the last three are store-wide: --global is a no-op


def _is_global(args) -> bool:
    return bool(getattr(args, "global_scope", False)) or args.command == "migrate-global"


def _ctx(args) -> ops.Ctx:
    store = resolve_store(getattr(args, "store", None))
    if _is_global(args):
        return ops.Ctx(store, None, global_scope=True)
    return ops.Ctx(store, resolve_project(getattr(args, "project", None)))


def _check_scope_args(parser, args) -> None:
    if not _is_global(args):
        return
    if getattr(args, "project", None) is not None:
        parser.error("--global and --project are mutually exclusive"
                     + (" (migrate-global always uses the global scope)"
                        if args.command == "migrate-global" else ""))
    if args.command not in _GLOBAL_COMMANDS:
        parser.error(f"--global does not apply to '{args.command}'")


def _fmt_legacy(lg: dict | None) -> list[str]:
    if not lg:
        return []
    lines = [f"old setup found: {lg['manifest']} (written by '--project ~')"]
    if lg.get("error"):
        return lines + [f"  it can't be read: {lg['error']}",
                        "  fix it, then run: skill-store migrate-global. Until then, global sync and "
                        "prune leave every unlisted store link alone"]
    if lg["pending"]:
        lines.append(f"  not yet in the global scope: {', '.join(lg['pending'])}")
        lines.append(f"  move to the global scope ({lg['shell']}):")
    else:
        lines.append(f"  its skills are all in the global scope; remove the old files ({lg['shell']}):")
    lines += [f"    {c}" for c in lg["commands"]]
    return lines


def _where_text(r: dict) -> str:
    return f"global scope ({r['skills_dir']})" if r.get("scope") == "global" else r["project"]


def _print(obj, as_json: bool, text: str) -> None:
    if as_json:
        print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))
    elif text:
        print(text)


def _fmt_sync(r: dict) -> str:
    if r.get("skipped"):
        return "\n".join([f"sync: nothing to do ({r['skipped']})", *_fmt_legacy(r.get("legacy"))])
    lines = []
    r = dict(r)
    # over_cap lists every listed skill beyond the cap; show each once, by what happened to it.
    r["over_cap_not_linked"] = [n for n in r.get("over_cap", []) if n not in r.get("unlinked_over_cap", [])]
    for key, label in (("linked", "linked"), ("relinked", "relinked (pointed at the wrong store skill)"),
                       ("removed", "unlinked (no longer listed)"),
                       ("unlinked_over_cap", "over cap, unlinked (least recently used; store copy untouched)"),
                       ("unlinked_failed_check", "unlinked (now fails admission)"),
                       ("unlinked_unavailable", "unlinked (not in store or not approved)"),
                       ("already", "already linked"), ("over_cap_not_linked", "over cap, not linked"),
                       ("needs_approval", "needs approval (skill-store approve <name>)"),
                       ("missing", "not in store (build it)"), ("conflicts",
                       "name taken by a non-store entry, left alone"),
                       ("legacy_kept", "left alone (listed in the old '--project ~' manifest)")):
        if r.get(key):
            lines.append(f"{label}: {', '.join(r[key])}")
    for n, errs in r.get("failed_check", {}).items():
        lines.append(f"fails admission, not linked: {n} ({'; '.join(errs)})")
    for w in r.get("warnings", []):
        lines.append(f"warning: {w}")
    for e in r.get("errors", []):
        lines.append(f"error: {e}")
    lines.append(f"{r.get('linked_count', 0)}/{r.get('cap')} store skills linked in {_where_text(r)}")
    lines += _fmt_legacy(r.get("legacy"))
    return "\n".join(lines)


def _fmt_status(r: dict) -> str:
    is_global = r.get("scope") == "global"
    g = r["global"]
    if is_global:
        lines = [f"store:   {r['store']}", f"scope:   global ({r['skills_dir']})",
                 f"manifest: {r['manifest'] or '(none yet: created by skill-store --global link <name>)'}"]
    else:
        lines = [f"store:   {r['store']}", f"project: {r['project']}",
                 f"manifest: {r['manifest'] or '(none: skill-store init-project)'}"]
    if r["cap"] is not None:
        lines.append(f"linked:  {r['linked_count']}/{r['cap']}")
    if not is_global:
        cap = f"/{g['cap']}" if g["cap"] is not None else ""
        lines.append(f"global:  {len(g['linked'])}{cap} linked in {g['skills_dir']}")
    if not r["skills"]:
        lines.append("(store is empty)")
    else:
        hdr = f"{'skill':32} {'approved':8} {'manifest':8} {'linked':6} "
        hdr += "" if is_global else f"{'global':6} "
        lines.append(hdr + f"{'check':5} last used")
        for s in r["skills"]:
            chk = "-" if s["admission_ok"] is None else ("ok" if s["admission_ok"] else "FAIL")
            row = (f"{s['skill']:32} {('yes' if s['approved'] else 'no'):8} "
                   f"{('yes' if s['in_manifest'] else 'no'):8} {('yes' if s['linked'] else 'no'):6} ")
            if not is_global:
                row += f"{('yes' if s['linked_globally'] else 'no'):6} "
            lines.append(row + f"{chk:5} {s['last_used'] or 'never'}")
    where = r["skills_dir"] if is_global else ".claude/skills"
    for o in r["unmanaged_entries"]:
        lines.append(f"unmanaged in {where} (left alone): {o['name']} ({o['kind']})")
    for n in r["missing_from_store"]:
        lines.append(f"listed but not in store: {n}")
    lines += _fmt_legacy(r.get("legacy"))
    return "\n".join(lines)


def _fmt_check(r: dict) -> str:
    lines = [f"{'PASS' if r['ok'] else 'FAIL'}: {r['path']}"]
    lines += [f"  error: {e}" for e in r["errors"]]
    lines += [f"  warning: {w}" for w in r["warnings"]]
    st = r["stats"]
    if "skill_md_tokens" in st:
        lines.append(f"  SKILL.md: {st['skill_md_tokens']} tokens; description: "
                     f"{st.get('description_chars', '?')} chars")
    for ref, tok in st.get("references", {}).items():
        lines.append(f"  {ref}: {tok} tokens")
    return "\n".join(lines)


def _fmt_pp(name: str, r: dict) -> str:
    lines = [f"{name}: {r.get('registered', 'post-processed')}"]
    for w in r.get("warnings", []):
        lines.append(f"  warning: {w}")
    for d in r.get("dropped", []):
        lines.append(f"  dropped {d}")
    for ref, parts in r.get("split", {}).items():
        lines.append(f"  split {ref} -> {', '.join(parts)}")
    for ref in r.get("toc_added", []):
        lines.append(f"  added a table of contents to {ref}")
    cv = r.get("claude_validate")
    if cv:
        lines.append("  claude plugin validate: " + ("passed" if cv["ran"] and cv["ok"] else
                                                     "skipped (claude not on PATH)" if not cv["ran"] else "FAILED"))
    lines.append(f"  next: skill-store approve {name}   (the user's call), then skill-store link {name}")
    return "\n".join(lines)


_HOOK_NAMES_SHOWN = 10


def _session_start_output(r: dict) -> dict | None:
    """Claude Code SessionStart hook JSON for a sync result, or None when no link was created.

    Links that a SessionStart hook creates arrive after Claude Code's first skill scan, so the
    session can miss them. ``reloadSkills`` asks Claude Code to re-scan once the SessionStart hooks
    finish; ``systemMessage`` is shown to the user (in case the re-scan isn't supported or misses
    them); ``additionalContext`` tells the model the same. See
    https://code.claude.com/docs/en/hooks (SessionStart decision control).
    """
    created = sorted(set(r.get("linked", [])) | set(r.get("relinked", [])))
    if r.get("skipped") or not created:
        return None
    shown = ", ".join(created[:_HOOK_NAMES_SHOWN])
    if len(created) > _HOOK_NAMES_SHOWN:
        shown += f" and {len(created) - _HOOK_NAMES_SHOWN} more"
    where = "global skills" if r.get("scope") == "global" else "this checkout"
    noun = "skill" if len(created) == 1 else "skills"
    return {
        "systemMessage": f"skill-store linked {len(created)} {noun} into {where} at startup: {shown}. "
                         "If /skills doesn't list them, run /reload-skills once.",
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": f"skill-store linked these {noun} into {where} after the session's "
                                 f"first skill scan: {shown}. If one of them is needed but isn't "
                                 "available, ask the user to run /reload-skills once.",
            "reloadSkills": True,
        },
    }


def _emit_hook_json(obj: dict | None) -> None:
    """Print one JSON object on one line (ASCII only, so no code page can garble it), or nothing."""
    if obj is None:
        return
    try:
        sys.stdout.write(json.dumps(obj, ensure_ascii=True) + "\n")
        sys.stdout.flush()
    except (OSError, ValueError):
        pass  # stdout closed: the session must still start


def _read_hook_stdin() -> str:
    """Hook JSON from stdin as UTF-8, tolerating a BOM. Bytes are decoded explicitly so a
    Windows locale code page never mangles the payload."""
    if sys.stdin is None or sys.stdin.isatty():
        return ""
    buf = getattr(sys.stdin, "buffer", None)
    if buf is not None:
        return buf.read().decode("utf-8-sig", errors="replace")
    return sys.stdin.read()


def _log_usage_main(args) -> int:
    try:
        data = _read_hook_stdin()
        ops.log_usage(resolve_store(getattr(args, "store", None)), data)
    except BaseException:  # noqa: BLE001 - the hook must never fail the tool call
        pass
    return 0


_VALUE_OPTIONS = ("--store", "--project")


def _subcommand(argv: list[str]) -> str | None:
    """The first non-option token, skipping the values of global options (``--store X``).
    Used only to decide, before parsing, whether bad arguments must still exit 0."""
    skip = False
    for tok in argv:
        if skip:
            skip = False
            continue
        if tok in _VALUE_OPTIONS:
            skip = True
            continue
        if tok == "--":
            return None
        if tok.startswith("-"):
            continue
        return tok
    return None


def _never_fails(argv: list[str]) -> bool:
    """Hook entry points that must exit 0 even on bad arguments."""
    sub = _subcommand(argv)
    return sub == "log-usage" or (sub == "sync" and "--hook-output" in argv)


def run(argv: list[str]) -> int:
    parser = build_parser()
    if _never_fails(argv):
        try:
            args = parser.parse_args(argv)
            if args.command != "log-usage":
                _check_scope_args(parser, args)
        except SystemExit:
            return 0  # a hook must never fail the tool call or session start, even on bad arguments
    else:
        args = parser.parse_args(argv)
    if args.command == "log-usage":
        return _log_usage_main(args)
    _check_scope_args(parser, args)
    as_json = bool(getattr(args, "json", False))
    cmd = args.command
    if cmd == "sync":
        hook_output = args.hook_output
        quiet = args.quiet or hook_output
        try:
            r = ops.sync(_ctx(args))
        except BaseException as exc:  # noqa: BLE001 - never fail a session start hard
            if isinstance(exc, KeyboardInterrupt) and not quiet:
                raise
            print(f"skill-store sync: {exc}", file=sys.stderr)
            return 0 if quiet else 1
        if quiet:
            notes = [f"needs approval: {', '.join(r['needs_approval'])}" if r["needs_approval"] else "",
                     f"not in store: {', '.join(r['missing'])}" if r["missing"] else "",
                     *[f"error: {e}" for e in r["errors"]], *[f"warning: {w}" for w in r["warnings"]]]
            lg = r.get("legacy")
            if lg and lg.get("error"):
                notes.append(f"old '--project ~' manifest can't be read ({lg['error']}); fix it")
            elif lg and lg["pending"]:
                notes.append(f"old '--project ~' setup found ({lg['manifest']}); "
                             "run: skill-store migrate-global")
            for n in filter(None, notes):
                print(f"skill-store sync: {n}", file=sys.stderr)
            if hook_output:  # stdout carries only the hook JSON; --json is ignored
                _emit_hook_json(_session_start_output(r))
                return 0
            if as_json:
                _print(r, True, "")
            return 0
        _print(r, as_json, _fmt_sync(r))
        return 1 if r["errors"] else 0

    ctx = _ctx(args)
    if cmd == "init":
        r = ops.init(ctx)
        _print(r, as_json, f"store ready at {r['store']}" +
               "".join(f"\n  created {c}" for c in r["created"]))
    elif cmd == "init-project":
        r = ops.init_project(ctx)
        _print(r, as_json, f"project ready at {r['project']}" +
               "".join(f"\n  created {c}" for c in r["created"]))
    elif cmd == "build":
        from skillstore.build import build
        log = (lambda m: print(m, file=sys.stderr)) if as_json else print
        r = build(ctx.store, args.name, Path(args.recipe), skip_scrape=args.skip_scrape,
                  allow_any_version=args.any_skill_seekers_version,
                  run_claude_validate=not args.no_claude_validate, log=log)
        _print(r, as_json, _fmt_pp(args.name, r))
    elif cmd == "postprocess":
        from skillstore.build import load_recipe, process_and_register
        from skillstore.paths import validate_name
        validate_name(args.name)
        ctx.store.require()
        bdir = ctx.store.paths.build_dir(args.name)
        recipe_path = Path(args.recipe) if args.recipe else bdir / "recipe.json"
        recipe = load_recipe(recipe_path) if recipe_path.is_file() else None
        if args.recipe and recipe is None:
            raise SkillStoreError(f"recipe not found: {recipe_path}")
        src = Path(args.src) if args.src else bdir / "output" / args.name
        r = process_and_register(ctx.store, args.name, src, recipe,
                                 run_claude_validate=not args.no_claude_validate)
        _print(r, as_json, _fmt_pp(args.name, r))
    elif cmd == "check":
        r = ops.check(ctx, args.target)
        _print(r, as_json, _fmt_check(r))
        return 0 if r["ok"] else 1
    elif cmd == "approve":
        r = ops.approve(ctx, args.name)
        txt = f"approved {args.name} for linking" + (" (was already approved)" if r["already_approved"] else "")
        if not r["admission_ok"]:
            txt += "\n  warning: it fails admission, so link will refuse it until fixed:" + \
                   "".join(f"\n    - {e}" for e in r["admission_errors"])
        _print(r, as_json, txt)
    elif cmd == "link":
        r = ops.link(ctx, args.name)
        txt = f"{r['state']}: {args.name} -> {r['link']}"
        for e in r["evicted"]:
            txt += f"\nover cap ({r['cap']}): unlinked least recently used {e['skill']} " \
                   f"(last used {e['last_used'] or 'never'}); its store copy is untouched"
        _print(r, as_json, txt)
    elif cmd == "unlink" and args.all_links:
        if args.name:
            raise SkillStoreError("use either 'unlink <name>' or 'unlink --all', not both")
        r = ops.unlink_all(ctx)
        txt = ("unlinked: " + ", ".join(r["removed"])) if r["removed"] else \
            f"nothing to unlink in {_where_text(r)}"
        txt += "".join(f"\nerror: {e}" for e in r["errors"])
        _print(r, as_json, txt)
        return 1 if r["errors"] else 0
    elif cmd == "unlink":
        if not args.name:
            raise SkillStoreError("unlink needs a skill name, or --all")
        r = ops.unlink(ctx, args.name)
        txt = f"{r['state']}: {args.name}" + (" (removed from manifest)" if r["removed_from_manifest"] else "")
        _print(r, as_json, txt)
    elif cmd == "status":
        r = ops.status(ctx)
        _print(r, as_json, _fmt_status(r))
    elif cmd == "migrate-global":
        r = ops.migrate_global(ctx)
        if r["legacy"] is None and not r["migrated"]:
            txt = f"nothing to migrate (no {legacy_home_manifest()})"
        else:
            txt = "\n".join([f"{m['state']}: {m['skill']} (global scope)" for m in r["migrated"]]
                            + [f"over cap: unlinked least recently used {e['skill']}" for e in r["evicted"]]
                            + [f"not migrated: {n}: {e}" for n, e in r["failed"].items()]
                            + _fmt_legacy(r["legacy"]))
        _print(r, as_json, txt)
        return 1 if r["failed"] else 0
    elif cmd == "prune":
        r = ops.prune(ctx, args.cap)
        txt = "\n".join(f"unlinked {e['skill']} (last used {e['last_used'] or 'never'})" for e in r["evicted"]) \
            or f"nothing to prune ({r['linked_count']} linked, cap {r['cap']})"
        _print(r, as_json, txt)
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    try:
        return run(argv)
    except SkillStoreError as exc:
        if _never_fails(argv):
            return 0
        print(f"skill-store: error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
