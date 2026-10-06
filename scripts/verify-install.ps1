param(
    [string]$InstalledPath = ""
)
$ErrorActionPreference = "Stop"
if (-not $InstalledPath) { $InstalledPath = Join-Path $HOME ".agents\skills\cass-improve-system" }
$failed = $false
foreach ($rel in @("SKILL.md", "scripts/preflight.py", "scripts/cass_adapter.py",
                   "scripts/collect_sessions.py", "scripts/normalize_cass.py",
                   "scripts/analyze.py", "scripts/report.py", "scripts/period.py",
                   "references/analysis-rubric.md", "references/promotion-policy.md",
                   "references/report-format.md")) {
    $p = Join-Path $InstalledPath $rel
    if (Test-Path -LiteralPath $p) { Write-Host "OK  $rel" }
    else { Write-Host "MISS $rel"; $failed = $true }
}
if ($failed) { Write-Error "Verification failed for $InstalledPath"; exit 1 }
Write-Host "Verified $InstalledPath"
