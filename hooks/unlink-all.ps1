# Remove every skill-store junction/symlink under <project>\.claude\skills WITHOUT
# touching the store or the manifest. Use it before deleting a checkout or
# worktree (e.g. as an AO --pre-remove step), so no recursive delete can ever
# reach the store through a link.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File unlink-all.ps1 [-Project DIR] [-Store DIR]
#
# Project defaults to $env:CLAUDE_PROJECT_DIR, then the current directory.
# Store defaults to $env:SKILL_STORE_HOME, then $env:USERPROFILE\.skill-store.
#
# Only reparse points that resolve into <store>\skills are removed, each with
# [System.IO.Directory]::Delete(path, $false) (non-recursive: removes the link only).
# Never Remove-Item -Recurse. Real directories and foreign links are left alone.

param(
    [string]$Project = $(if ($env:CLAUDE_PROJECT_DIR) { $env:CLAUDE_PROJECT_DIR } else { (Get-Location).Path }),
    [string]$Store   = $(if ($env:SKILL_STORE_HOME) { $env:SKILL_STORE_HOME } else { Join-Path $env:USERPROFILE '.skill-store' })
)

function Get-NormPath([string]$p) {
    if ($p.StartsWith('\??\')) { $p = $p.Substring(4) }
    return [System.IO.Path]::GetFullPath($p).TrimEnd('\', '/')
}

$dir = Join-Path $Project '.claude\skills'
if (-not (Test-Path -LiteralPath $dir -PathType Container)) { exit 0 }
$storeSkills = Join-Path $Store 'skills'
if (-not (Test-Path -LiteralPath $storeSkills -PathType Container)) {
    [Console]::Error.WriteLine("unlink-all: store not found at $Store; nothing removed")
    exit 0
}
$storeSkills = Get-NormPath (Get-Item -LiteralPath $storeSkills -Force).FullName

foreach ($item in Get-ChildItem -LiteralPath $dir -Force -Directory) {
    if (-not ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint)) {
        Write-Output "kept $($item.Name) (real directory, not a link)"
        continue
    }
    $target = @($item.Target) | Select-Object -First 1
    if (-not $target) { Write-Output "kept $($item.Name) (unreadable link target)"; continue }
    if (-not [System.IO.Path]::IsPathRooted($target)) { $target = Join-Path $item.Parent.FullName $target }
    $target = Get-NormPath $target
    if ($target.StartsWith($storeSkills + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
        [System.IO.Directory]::Delete($item.FullName, $false)
        Write-Output "unlinked $($item.Name)"
    } else {
        Write-Output "kept $($item.Name) (link does not point into the store)"
    }
}
exit 0
