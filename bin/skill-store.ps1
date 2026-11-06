# Thin wrapper: run skill-store from a checkout without installing it.
# Prefer `pip install -e .` (or pipx), which puts `skill-store` on PATH.
$root = Split-Path -Parent $PSScriptRoot
$py = Get-Command py -ErrorAction SilentlyContinue
if ($py) { $exe = 'py'; $pre = @('-3') } else { $exe = 'python'; $pre = @() }
$env:PYTHONPATH = if ($env:PYTHONPATH) { "$root;$env:PYTHONPATH" } else { $root }
& $exe @pre -m skillstore @args
exit $LASTEXITCODE
