#!/usr/bin/env sh
# Thin wrapper: run skill-store from a checkout without installing it.
# Prefer `pip install -e .` (or pipx), which puts `skill-store` on PATH.
here=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
if command -v python3 >/dev/null 2>&1; then py=python3; else py=python; fi
PYTHONPATH="$here${PYTHONPATH:+:$PYTHONPATH}" exec "$py" -m skillstore "$@"
