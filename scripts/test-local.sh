#!/usr/bin/env sh
# Run the skill-store test suite locally on Linux (or inside WSL). No hosted CI needed.
#
#   scripts/test-local.sh [--venv DIR] [--python PY] [--other-volume DIR] [-- PYTEST_ARGS...]
#
# - Creates (or reuses) a venv in the temp dir, never in the repo: ${TMPDIR:-/tmp}/skill-store-test-venv.
#   If the system Python has no ensurepip (e.g. Kali/Debian without python3-venv), the venv is
#   made with --without-pip and pip is bootstrapped from https://bootstrap.pypa.io/get-pip.py.
#   No sudo, no system packages.
# - Installs the checkout with `pip install -e ".[test]"` and runs pytest.
# - If the checkout is on a Windows drive (/mnt/<drive>/...), it is first copied to a temp dir
#   (without .git and build junk), so pip/pytest never write into the Windows checkout.
# - --other-volume DIR enables the opt-in cross-filesystem tests (SKILL_STORE_TEST_OTHER_VOLUME),
#   e.g. --other-volume /mnt/c/Users/<you>/AppData/Local/Temp from inside WSL.
#
# The tests themselves redirect HOME/USERPROFILE/SKILL_STORE_HOME to temp dirs and fail if the
# real ~/.claude/skills, ~/.agents or ~/.skill-store change. This script never touches them.
# Exit status: pytest's (non-zero on any failure).
set -eu

here=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
venv="${TMPDIR:-/tmp}/skill-store-test-venv"
py=""
other=""

while [ $# -gt 0 ]; do
  case "$1" in
    --venv) venv="$2"; shift 2 ;;
    --python) py="$2"; shift 2 ;;
    --other-volume) other="$2"; shift 2 ;;
    --) shift; break ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "test-local.sh: unknown argument: $1 (pytest args go after --)" >&2; exit 2 ;;
  esac
done

if [ -z "$py" ]; then
  if command -v python3 >/dev/null 2>&1; then py=python3; else py=python; fi
fi
command -v "$py" >/dev/null 2>&1 || { echo "test-local.sh: $py not found (need Python >= 3.10)" >&2; exit 2; }

# ---- venv in the temp dir --------------------------------------------------------------
vpy="$venv/bin/python"
if [ ! -x "$vpy" ] || ! "$vpy" -m pip --version >/dev/null 2>&1; then
  echo "==> creating venv $venv ($("$py" --version 2>&1))"
  rm -rf -- "$venv"
  if ! "$py" -m venv "$venv" >/dev/null 2>&1; then
    echo "==> no ensurepip; creating venv --without-pip and bootstrapping pip"
    rm -rf -- "$venv"
    "$py" -m venv --without-pip "$venv"
    getpip="${TMPDIR:-/tmp}/skill-store-get-pip.py"
    "$vpy" -c "import urllib.request,sys; urllib.request.urlretrieve('https://bootstrap.pypa.io/get-pip.py', sys.argv[1])" "$getpip"
    "$vpy" "$getpip" -q
    rm -f -- "$getpip"
  fi
else
  echo "==> reusing venv $venv"
fi

# ---- source: in place, or a temp copy when on a Windows drive ---------------------------
src="$here"
work=""
case "$here" in
  /mnt/[a-zA-Z]/*)
    work=$(mktemp -d "${TMPDIR:-/tmp}/skill-store-src.XXXXXX")
    echo "==> checkout is on a Windows drive; copying to $work"
    (cd "$here" && tar cf - --exclude=.git --exclude='*.egg-info' --exclude=__pycache__ \
        --exclude=.pytest_cache --exclude=.venv .) | (cd "$work" && tar xf -)
    src="$work"
    ;;
esac
cleanup() { if [ -n "$work" ]; then rm -rf -- "$work"; fi; }
trap cleanup EXIT INT TERM

echo "==> pip install -e .[test]"
"$vpy" -m pip install -q --disable-pip-version-check -e "$src[test]"

if [ -n "$other" ]; then
  export SKILL_STORE_TEST_OTHER_VOLUME="$other"
  echo "==> cross-volume tests enabled: SKILL_STORE_TEST_OTHER_VOLUME=$other"
fi

echo "==> pytest ($("$vpy" --version 2>&1), $(uname -sr))"
cd "$src"
set +e
"$vpy" -m pytest -p no:cacheprovider -q "$@"
rc=$?
set -e
if [ "$rc" -eq 0 ]; then echo "==> PASS (linux)"; else echo "==> FAIL (linux, pytest exit $rc)"; fi
exit "$rc"
