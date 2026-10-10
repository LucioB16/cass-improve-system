param(
    [string]$InstalledPath = "",
    [string]$Source = "",
    [string]$HomeDir = ""
)
$ErrorActionPreference = "Stop"

# Thin launcher: verification policy lives in scripts/skill_install.py.
$engine = Join-Path $PSScriptRoot "skill_install.py"
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) { $python = Get-Command py -ErrorAction SilentlyContinue }
if (-not $python) { Write-Error "Python 3 is required (python or py on PATH)"; exit 1 }

$args = @($engine, "verify")
if ($Source) { $args += @("--source", $Source) }
if ($HomeDir) { $args += @("--home", $HomeDir) }
if ($InstalledPath) {
    Write-Host "note: explicit -InstalledPath is informational only; verification is home-rooted"
    Write-Host "      checked path: $InstalledPath"
}
& $python.Source $args
exit $LASTEXITCODE
