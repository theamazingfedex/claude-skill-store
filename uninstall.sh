#!/usr/bin/env sh
# Uninstall skill-store on Linux/macOS: optionally restore the cached skills into your global
# Claude skills, then remove every trace of skill-store except this checkout.
#
#   sh uninstall.sh --dry-run        # show everything, change nothing (start here)
#   sh uninstall.sh                  # interactive
#   sh uninstall.sh --help           # all options
#
# Thin wrapper around skillstore/uninstall.py, run from this checkout (not from the installed
# package, which it uninstalls). Needs Python 3.10+; PyYAML is not needed.
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if command -v python3 >/dev/null 2>&1; then py=python3
elif command -v python >/dev/null 2>&1; then py=python
else echo "uninstall.sh: Python 3.10+ not found" >&2; exit 2
fi
exec "$py" "$here/skillstore/uninstall.py" "$@"
