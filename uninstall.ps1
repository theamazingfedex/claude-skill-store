<#
.SYNOPSIS
  Uninstall skill-store on Windows: optionally restore the cached skills into your global Claude
  skills, then remove every trace of skill-store except this checkout.

.DESCRIPTION
  Thin wrapper around skillstore\uninstall.py, run from this checkout (not from the installed
  package, which it uninstalls). Needs Python 3.10+ (py launcher or python on PATH); PyYAML is not
  needed. Run with -h for the options. Start with --dry-run.

.EXAMPLE
  .\uninstall.ps1 --dry-run
.EXAMPLE
  .\uninstall.ps1
.EXAMPLE
  .\uninstall.ps1 --yes --restore --project D:\src\my-game
#>
$script = Join-Path $PSScriptRoot 'skillstore\uninstall.py'
if (Get-Command py -ErrorAction SilentlyContinue) { $exe = 'py'; $pre = @('-3') }
elseif (Get-Command python -ErrorAction SilentlyContinue) { $exe = 'python'; $pre = @() }
else { [Console]::Error.WriteLine('uninstall.ps1: Python 3.10+ not found (py or python on PATH)'); exit 2 }
& $exe @pre $script @args
exit $LASTEXITCODE
