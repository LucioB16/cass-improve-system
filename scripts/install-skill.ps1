param(
    [ValidateSet("Agents", "Claude", "Auto")]
    [string]$Target = "Agents",
    [string]$Source = "",
    [switch]$Force
)
$ErrorActionPreference = "Stop"

if (-not $Source) {
    $Source = Join-Path $PSScriptRoot "..\skill\cass-improve-system"
}

function Get-Destination([string]$t) {
    if ($t -eq "Claude") { return Join-Path $HOME ".claude\skills\cass-improve-system" }
    return Join-Path $HOME ".agents\skills\cass-improve-system"
}

if ($Target -eq "Auto") {
    if (Get-Command claude -ErrorAction SilentlyContinue) { $Target = "Claude" }
    else { $Target = "Agents" }
    Write-Host "Auto-detected target: $Target"
}

$src = Resolve-Path -LiteralPath $Source
$dest = Get-Destination $Target
if ((Test-Path $dest) -and -not $Force) {
    Write-Error "Destination already exists: $dest (re-run with -Force to overwrite)"
}
New-Item -ItemType Directory -Force -Path $dest | Out-Null
Copy-Item (Join-Path $src "*") $dest -Recurse -Force
& (Join-Path $PSScriptRoot "verify-install.ps1") -InstalledPath $dest
Write-Host "Installed cass-improve-system ($Target) to $dest"
