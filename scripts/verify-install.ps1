param(
    [string]$InstalledPath = "",
    [string]$Source = ""
)
$ErrorActionPreference = "Stop"
if (-not $InstalledPath) { $InstalledPath = Join-Path $HOME ".agents\skills\cass-improve-system" }
if (-not $Source) {
    $candidate = Join-Path $PSScriptRoot "..\skill\cass-improve-system"
    if (Test-Path -LiteralPath (Join-Path $candidate "SKILL.md")) { $Source = $candidate }
}
if (-not (Test-Path -LiteralPath $InstalledPath)) {
    Write-Error "Not installed: $InstalledPath"; exit 1
}
if ($Source) {
    # Full byte-identity check: presence alone cannot detect stale scripts.
    $manifestPy = Join-Path $Source "scripts\manifest.py"
    if (-not (Test-Path -LiteralPath $manifestPy)) {
        Write-Error "manifest.py missing in source $Source"; exit 1
    }
    $srcJson = & python $manifestPy $Source 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $srcJson) { Write-Error "manifest failed for source"; exit 1 }
    $instJson = & python $manifestPy $InstalledPath 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $instJson) { Write-Error "manifest failed for installed copy"; exit 1 }
    $src = $srcJson | ConvertFrom-Json
    $inst = $instJson | ConvertFrom-Json
    $failed = $false
    foreach ($f in $src.PSObject.Properties) {
        $have = $inst.PSObject.Properties[$f.Name]
        if (-not $have) { Write-Host "MISS $($f.Name)"; $failed = $true }
        elseif ($have.Value -ne $f.Value) { Write-Host "STALE $($f.Name)"; $failed = $true }
        else { Write-Host "OK   $($f.Name)" }
    }
    foreach ($f in $inst.PSObject.Properties) {
        if (-not $src.PSObject.Properties[$f.Name]) { Write-Host "EXTRA $($f.Name)"; $failed = $true }
    }
    if ($failed) {
        Write-Error "Verification failed: installed copy differs from $Source (reinstall with -Force)"
        exit 1
    }
    Write-Host "Verified byte-identical: $InstalledPath == $Source"
} else {
    Write-Host "Verified present (no source given, hash check skipped): $InstalledPath"
}
