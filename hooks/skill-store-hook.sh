#!/usr/bin/env sh
# Claude Code hook wrapper for skill-store (Linux, macOS, Git Bash on Windows).
#
#   skill-store-hook.sh log-usage                     # PostToolUse, matcher "Skill"
#   skill-store-hook.sh sync --quiet --hook-output    # SessionStart
#
# With --hook-output, sync prints one line of Claude Code hook JSON when it created links (it asks
# Claude Code to re-scan skills and tells the user to run /reload-skills if they are still
# missing), and nothing otherwise. That JSON passes straight through on stdout.
#
# Why a wrapper instead of calling `skill-store` directly:
#   * it finds skill-store even when it is not on the hook's PATH
#     (SKILL_STORE_CMD, then `skill-store`, then `python3 -m skillstore`);
#   * project `sync` is skipped in projects without .claude/skills.manifest.json, which saves
#     a Python start-up there (`sync` itself is a no-op without a manifest anyway);
#     `--global sync` always runs;
#   * it always exits 0, so a broken store never blocks a tool call or a session.
#
# Hook JSON arrives on stdin and is passed straight through to skill-store.

sub="${1:-}"

if [ "$sub" = "sync" ]; then
    is_global=0
    for a in "$@"; do [ "$a" = "--global" ] && is_global=1; done
    if [ "$is_global" = 0 ]; then
        proj="${CLAUDE_PROJECT_DIR:-$PWD}"
        [ -f "$proj/.claude/skills.manifest.json" ] || exit 0
    fi
fi

if [ -n "${SKILL_STORE_CMD:-}" ]; then
    set -- "$SKILL_STORE_CMD" "$@"
elif command -v skill-store >/dev/null 2>&1; then
    set -- skill-store "$@"
elif command -v python3 >/dev/null 2>&1; then
    set -- python3 -m skillstore "$@"
elif command -v python >/dev/null 2>&1; then
    set -- python -m skillstore "$@"
else
    echo "skill-store-hook: skill-store not found (set SKILL_STORE_CMD)" >&2
    exit 0
fi

"$@" || echo "skill-store-hook: '$sub' failed (ignored)" >&2
exit 0
