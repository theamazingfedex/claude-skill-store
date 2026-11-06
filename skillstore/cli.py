"""Command-line interface: ``skill-store <command>`` (or ``python -m skillstore``)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from skillstore import __version__, ops
from skillstore.paths import SkillStoreError, resolve_project, resolve_store


def _common() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--store", default=argparse.SUPPRESS,
                   help="store root (default: $SKILL_STORE_HOME or ~/.skill-store)")
    p.add_argument("--project", default=argparse.SUPPRESS,
                   help="project root (default: $CLAUDE_PROJECT_DIR, git toplevel, or cwd)")
    p.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                   help="print the result as JSON")
    return p


def build_parser() -> argparse.ArgumentParser:
    common = _common()
    parser = argparse.ArgumentParser(
        prog="skill-store", parents=[common],
        description="Keep Claude Code skills in a user-level store and link an approved, "
                    "capped set into each project. Unlinking never deletes.")
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
    p = add("link", "link an approved skill into the project (adds it to the manifest, enforces the cap)")
    p.add_argument("name")
    p = add("unlink", "remove a skill's link (never its store copy) and drop it from the manifest")
    p.add_argument("name")
    p = add("sync", "make the project's links match its manifest (idempotent; for SessionStart)")
    p.add_argument("--quiet", action="store_true", help="no output on success; always exit 0")
    add("status", "show store skills, approval, links in this project, last use and cap")
    p = add("prune", "unlink least recently used skills down to the cap (on demand only)")
    p.add_argument("--cap", type=int, help="target cap (default: the manifest cap)")
    add("log-usage", "PostToolUse hook entry point: log a Skill tool call (always exits 0)")
    return parser


def _ctx(args) -> ops.Ctx:
    return ops.Ctx(resolve_store(getattr(args, "store", None)),
                   resolve_project(getattr(args, "project", None)))


def _print(obj, as_json: bool, text: str) -> None:
    if as_json:
        print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))
    elif text:
        print(text)


def _fmt_sync(r: dict) -> str:
    if r.get("skipped"):
        return f"sync: nothing to do ({r['skipped']})"
    lines = []
    for key, label in (("linked", "linked"), ("removed", "unlinked (no longer listed)"),
                       ("already", "already linked"), ("over_cap", "over cap, not linked"),
                       ("needs_approval", "needs approval (skill-store approve <name>)"),
                       ("missing", "not in store (build it)"), ("conflicts",
                       "name taken by a non-store entry, left alone")):
        if r.get(key):
            lines.append(f"{label}: {', '.join(r[key])}")
    for n, errs in r.get("failed_check", {}).items():
        lines.append(f"fails admission, not linked: {n} ({'; '.join(errs)})")
    for w in r.get("warnings", []):
        lines.append(f"warning: {w}")
    for e in r.get("errors", []):
        lines.append(f"error: {e}")
    lines.append(f"{r.get('linked_count', 0)}/{r.get('cap')} store skills linked in {r['project']}")
    return "\n".join(lines)


def _fmt_status(r: dict) -> str:
    lines = [f"store:   {r['store']}", f"project: {r['project']}",
             f"manifest: {r['manifest'] or '(none: skill-store init-project)'}"]
    if r["cap"] is not None:
        lines.append(f"linked:  {r['linked_count']}/{r['cap']}")
    if not r["skills"]:
        lines.append("(store is empty)")
    else:
        lines.append(f"{'skill':32} {'approved':8} {'manifest':8} {'linked':6} {'check':5} last used")
        for s in r["skills"]:
            chk = "-" if s["admission_ok"] is None else ("ok" if s["admission_ok"] else "FAIL")
            lines.append(f"{s['skill']:32} {('yes' if s['approved'] else 'no'):8} "
                         f"{('yes' if s['in_manifest'] else 'no'):8} {('yes' if s['linked'] else 'no'):6} "
                         f"{chk:5} {s['last_used'] or 'never'}")
    for o in r["unmanaged_entries"]:
        lines.append(f"unmanaged in .claude/skills (left alone): {o['name']} ({o['kind']})")
    for n in r["missing_from_store"]:
        lines.append(f"listed but not in store: {n}")
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


def _log_usage_main(args) -> int:
    try:
        data = sys.stdin.read() if not sys.stdin.isatty() else ""
        ops.log_usage(resolve_store(getattr(args, "store", None)), data)
    except BaseException:  # noqa: BLE001 - the hook must never fail the tool call
        pass
    return 0


def run(argv: list[str]) -> int:
    parser = build_parser()
    if "log-usage" in argv:
        try:
            args = parser.parse_args(argv)
        except SystemExit:
            return 0
        return _log_usage_main(args)
    args = parser.parse_args(argv)
    as_json = bool(getattr(args, "json", False))
    cmd = args.command
    if cmd == "sync":
        quiet = args.quiet
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
            for n in filter(None, notes):
                print(f"skill-store sync: {n}", file=sys.stderr)
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
    elif cmd == "unlink":
        r = ops.unlink(ctx, args.name)
        txt = f"{r['state']}: {args.name}" + (" (removed from manifest)" if r["removed_from_manifest"] else "")
        _print(r, as_json, txt)
    elif cmd == "status":
        r = ops.status(ctx)
        _print(r, as_json, _fmt_status(r))
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
        if "log-usage" in argv:
            return 0
        print(f"skill-store: error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
