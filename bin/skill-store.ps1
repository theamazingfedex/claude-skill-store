# Thin wrapper: run skill-store from a checkout without installing it.
# Prefer `pip install -e .` (or pipx), which puts `skill-store` on PATH.
# Uses the `python` on PATH (e.g. an active venv) and falls back to the `py -3` launcher.
$root = Split-Path -Parent $PSScriptRoot
if (Get-Command python -ErrorAction SilentlyContinue) { $exe = 'python'; $pre = @() }
else { $exe = 'py'; $pre = @('-3') }
$env:PYTHONPATH = if ($env:PYTHONPATH) { "$root;$env:PYTHONPATH" } else { $root }
& $exe @pre -m skillstore @args
exit $LASTEXITCODE
