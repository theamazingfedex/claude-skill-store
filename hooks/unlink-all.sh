#!/usr/bin/env sh
# Remove every skill-store link under <project>/.claude/skills WITHOUT touching
# the store or the manifest. Use it before deleting a checkout or worktree
# (e.g. as an AO --pre-remove step), so no recursive delete can ever reach the
# store through a link.
#
#   unlink-all.sh [PROJECT_DIR]
#
# PROJECT_DIR defaults to $CLAUDE_PROJECT_DIR, then the current directory.
# Store root: $SKILL_STORE_HOME, else ~/.skill-store.
#
# Only symlinks/junctions that resolve into <store>/skills are removed, each
# with a plain non-recursive `rm -- <link>` (never a trailing slash, never -r).
# Real directories and foreign links are reported and left alone. Exits 0.

proj="${1:-${CLAUDE_PROJECT_DIR:-$PWD}}"
store="${SKILL_STORE_HOME:-$HOME/.skill-store}"
dir="$proj/.claude/skills"
[ -d "$dir" ] || exit 0

# Canonical form for comparison. On Git Bash/MSYS the same directory can show up
# as /tmp/... or /c/Users/..., so convert to a Windows path and lowercase it.
norm() {
    if command -v cygpath >/dev/null 2>&1; then
        cygpath -m "$1" | tr '[:upper:]' '[:lower:]'
    else
        printf '%s\n' "$1"
    fi
}

store_skills=$(cd "$store/skills" 2>/dev/null && norm "$(pwd -P)") || {
    echo "unlink-all: store not found at $store; nothing removed" >&2
    exit 0
}

for entry in "$dir"/*; do
    [ -e "$entry" ] || [ -L "$entry" ] || continue
    name=${entry##*/}
    if [ -L "$entry" ]; then
        target=$(cd "$entry" 2>/dev/null && norm "$(pwd -P)") || target=""
        case "$target" in
            "$store_skills"/*)
                rm -- "$entry" && echo "unlinked $name" ;;
            *)
                echo "kept $name (link does not point into the store)" ;;
        esac
    elif [ -d "$entry" ]; then
        echo "kept $name (real directory, not a link)"
    fi
done
exit 0
