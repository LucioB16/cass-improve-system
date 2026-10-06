param(
    [string]$InstalledPath = ""
)
$ErrorActionPreference = "Stop"
if (-not $InstalledPath) { $InstalledPath = Join-Path $HOME ".agents\skills\cass-improve-system" }
if (-not (Test-Path -LiteralPath $InstalledPath)) {
    Write-Host "Nothing to remove: $InstalledPath does not exist."
    exit 0
}
Remove-Item -LiteralPath $InstalledPath -Recurse -Force
Write-Host "Removed $InstalledPath"
