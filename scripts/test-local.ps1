<#
.SYNOPSIS
  Run the skill-store test suite locally on Windows, and optionally inside WSL. No hosted CI needed.

.DESCRIPTION
  - Creates (or reuses) a venv in the system temp dir, never in the repo:
    %TEMP%\skill-store-test-venv. Installs the checkout with pip install -e ".[test]" and runs pytest.
  - -Wsl also runs scripts/test-local.sh inside the WSL distro -WslDistro (default kali-linux).
    That script copies the checkout from /mnt/<drive>/... to a Linux temp dir, makes its own venv
    in /tmp (bootstrapping pip with --without-pip + get-pip.py when ensurepip is missing; no sudo),
    and runs the same suite.
  - -OtherVolume DIR enables the opt-in cross-volume tests (SKILL_STORE_TEST_OTHER_VOLUME): give a
    writable directory on a different drive than %TEMP% (e.g. E:\tmp). With -Wsl the same directory
    is passed to the Linux run as its /mnt path, which tests ext4 <-> drvfs links.
  - The tests redirect HOME/USERPROFILE/SKILL_STORE_HOME to temp dirs and fail if the real
    ~/.claude/skills, ~/.agents or ~/.skill-store change. This script never touches them.
  - Exits non-zero if any run fails. Extra arguments are passed to pytest.

.EXAMPLE
  .\scripts\test-local.ps1
.EXAMPLE
  .\scripts\test-local.ps1 -Wsl -OtherVolume E:\tmp
#>
[CmdletBinding()]
param(
    [switch]$Wsl,
    [string]$WslDistro = 'kali-linux',
    [string]$OtherVolume,
    [string]$Python,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$PytestArgs = @()
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$venv = Join-Path ([IO.Path]::GetTempPath()) 'skill-store-test-venv'
$vpy = Join-Path $venv 'Scripts\python.exe'
$results = [ordered]@{}

function Invoke-Native {
    # Run a native command, stream its output, return the exit code (no stderr->error records).
    param([string]$Exe, [string[]]$Arguments)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & $Exe @Arguments 2>&1 | ForEach-Object { "$_" } | Out-Host } finally { $ErrorActionPreference = $prev }
    return $LASTEXITCODE
}

if ($OtherVolume) {
    if (-not (Test-Path -LiteralPath $OtherVolume -PathType Container)) {
        throw "-OtherVolume '$OtherVolume' is not an existing directory"
    }
    $OtherVolume = (Resolve-Path -LiteralPath $OtherVolume).Path
}

# ---- Windows ------------------------------------------------------------------------------
Write-Host "==> Windows: venv $venv"
if (-not (Test-Path $vpy)) {
    if ($Python) { $base = @($Python) }
    elseif (Get-Command python -ErrorAction SilentlyContinue) { $base = @('python') }
    elseif (Get-Command py -ErrorAction SilentlyContinue) { $base = @('py', '-3') }
    else { throw 'Python >= 3.10 not found (python or py on PATH)' }
    $baseArgs = @()
    if ($base.Count -gt 1) { $baseArgs = $base[1..($base.Count - 1)] }
    $rc = Invoke-Native $base[0] ($baseArgs + @('-m', 'venv', $venv))
    if ($rc -ne 0) { throw "could not create venv $venv" }
} else {
    Write-Host '==> reusing venv'
}

$rc = Invoke-Native $vpy @('-m', 'pip', 'install', '-q', '--disable-pip-version-check', '-e', "$repo[test]")
if ($rc -ne 0) {
    $results['Windows'] = "FAIL (pip install exit $rc)"
} else {
    $hadOther = Test-Path Env:SKILL_STORE_TEST_OTHER_VOLUME
    $oldOther = $env:SKILL_STORE_TEST_OTHER_VOLUME
    if ($OtherVolume) { $env:SKILL_STORE_TEST_OTHER_VOLUME = $OtherVolume; Write-Host "==> cross-volume tests enabled: $OtherVolume" }
    Push-Location $repo
    try {
        $ver = & $vpy --version 2>&1
        Write-Host "==> pytest on Windows ($ver)"
        $rc = Invoke-Native $vpy (@('-m', 'pytest', '-p', 'no:cacheprovider', '-q') + $PytestArgs)
    } finally {
        Pop-Location
        if ($hadOther) { $env:SKILL_STORE_TEST_OTHER_VOLUME = $oldOther } else { Remove-Item Env:SKILL_STORE_TEST_OTHER_VOLUME -ErrorAction SilentlyContinue }
    }
    $results['Windows'] = $(if ($rc -eq 0) { 'PASS' } else { "FAIL (pytest exit $rc)" })
}

# ---- WSL ----------------------------------------------------------------------------------
if ($Wsl) {
    $key = "WSL $WslDistro"
    if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
        $results[$key] = 'FAIL (wsl.exe not found)'
    } else {
        $toWsl = {
            param($winPath)
            $p = (& wsl.exe -d $WslDistro -- wslpath -a ($winPath -replace '\\', '/')) 2>$null
            if ($LASTEXITCODE -ne 0 -or -not $p) { throw "wslpath failed for $winPath in $WslDistro" }
            ("$p").Trim()
        }
        try {
            $script = & $toWsl (Join-Path $repo 'scripts\test-local.sh')
            $wslArgs = @('-d', $WslDistro, '--', 'sh', $script)
            if ($OtherVolume) { $wslArgs += @('--other-volume', (& $toWsl $OtherVolume)) }
            if ($PytestArgs.Count) { $wslArgs += @('--') + $PytestArgs }
            Write-Host "==> WSL ($WslDistro): sh $script"
            $rc = Invoke-Native 'wsl.exe' $wslArgs
            $results[$key] = $(if ($rc -eq 0) { 'PASS' } else { "FAIL (exit $rc)" })
        } catch {
            $results[$key] = "FAIL ($($_.Exception.Message))"
        }
    }
}

# ---- summary ------------------------------------------------------------------------------
Write-Host ''
Write-Host '==> Summary'
$failed = $false
foreach ($k in $results.Keys) {
    Write-Host ('    {0,-22} {1}' -f $k, $results[$k])
    if ($results[$k] -ne 'PASS') { $failed = $true }
}
if ($failed) { exit 1 } else { exit 0 }
