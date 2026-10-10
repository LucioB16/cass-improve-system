param(
    [ValidateSet("Agents", "Both", "Claude", "Auto")]
    [string]$Target = "Both",
    [string]$Source = "",
    [string]$HomeDir = "",
    [switch]$Force
)
$ErrorActionPreference = "Stop"

# Thin launcher: all install policy lives in scripts/skill_install.py so
# Windows/POSIX behavior cannot diverge.
$engine = Join-Path $PSScriptRoot "skill_install.py"
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) { $python = Get-Command py -ErrorAction SilentlyContinue }
if (-not $python) { Write-Error "Python 3 is required (python or py on PATH)"; exit 1 }

$map = @{ Agents = "agents"; Both = "both"; Claude = "claude" }
if ($Target -eq "Auto") {
    if (Get-Command claude -ErrorAction SilentlyContinue) { $Target = "Both" }
    else { $Target = "Agents" }
    Write-Host "Auto-detected target: $Target"
}

$args = @($engine, "install", "--target", $map[$Target])
if ($Source) { $args += @("--source", $Source) }
if ($HomeDir) { $args += @("--home", $HomeDir) }
if ($Force) { $args += "--force" }
& $python.Source $args
exit $LASTEXITCODE
