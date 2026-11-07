# Claude Code hook wrapper for skill-store (Windows PowerShell 5.1 / PowerShell 7).
#
#   skill-store-hook.ps1 log-usage                    # PostToolUse, matcher "Skill"
#   skill-store-hook.ps1 sync --quiet --hook-output   # SessionStart
#
# Same contract as skill-store-hook.sh:
#   * finds skill-store via $env:SKILL_STORE_CMD, then `skill-store` on PATH,
#     then `py -3 -m skillstore` / `python -m skillstore`;
#   * skips project `sync` in projects without .claude\skills.manifest.json (where it is a no-op
#     anyway); `--global sync` always runs;
#   * with --hook-output, passes sync's one line of hook JSON through on stdout (printed only when
#     links were created), so Claude Code re-scans skills and shows the user a notice;
#   * always exits 0, so it never blocks a tool call or a session start.
#
# Hook JSON arrives on stdin; for log-usage it is forwarded to skill-store as UTF-8.

$ErrorActionPreference = 'Continue'
$sub = if ($args.Count -gt 0) { [string]$args[0] } else { '' }

try {
    if ($sub -eq 'sync' -and -not ($args -contains '--global')) {
        $proj = if ($env:CLAUDE_PROJECT_DIR) { $env:CLAUDE_PROJECT_DIR } else { (Get-Location).Path }
        if (-not (Test-Path -LiteralPath (Join-Path $proj '.claude\skills.manifest.json'))) { exit 0 }
    }

    $exe = $null; $pre = @()
    if ($env:SKILL_STORE_CMD) {
        $exe = $env:SKILL_STORE_CMD
    } elseif (Get-Command skill-store -ErrorAction SilentlyContinue) {
        $exe = 'skill-store'
    } elseif (Get-Command py -ErrorAction SilentlyContinue) {
        $exe = 'py'; $pre = @('-3', '-m', 'skillstore')
    } elseif (Get-Command python -ErrorAction SilentlyContinue) {
        $exe = 'python'; $pre = @('-m', 'skillstore')
    } else {
        [Console]::Error.WriteLine('skill-store-hook: skill-store not found (set SKILL_STORE_CMD)')
        exit 0
    }

    $allArgs = @($pre) + @($args)
    if ($sub -eq 'log-usage') {
        # Windows PowerShell pipes to native programs using $OutputEncoding (ASCII by default);
        # force UTF-8 without BOM so non-ASCII paths in the hook JSON survive.
        [Console]::InputEncoding = New-Object System.Text.UTF8Encoding $false
        $OutputEncoding = New-Object System.Text.UTF8Encoding $false
        $payload = [Console]::In.ReadToEnd()
        $payload | & $exe @allArgs
    } else {
        & $exe @allArgs
    }
    if ($LASTEXITCODE -ne 0) {
        [Console]::Error.WriteLine("skill-store-hook: '$sub' exited $LASTEXITCODE (ignored)")
    }
} catch {
    [Console]::Error.WriteLine("skill-store-hook: $($_.Exception.Message) (ignored)")
}
exit 0
