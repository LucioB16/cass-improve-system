param(
    [ValidateSet("Agents", "Both", "Claude", "Cline", "All")]
    [string]$Target = "Claude",
    [string]$HomeDir = "",
    [switch]$Force
)
$ErrorActionPreference = "Stop"

# Thin launcher: uninstall policy lives in scripts/skill_install.py.
# Removing the Claude link never touches the canonical directory.
# Removing the canonical directory requires -Force.
$engine = Join-Path $PSScriptRoot "skill_install.py"
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) { $python = Get-Command py -ErrorAction SilentlyContinue }
if (-not $python) { Write-Error "Python 3 is required (python or py on PATH)"; exit 1 }

$map = @{ Agents = "agents"; Both = "both"; Claude = "claude"; Cline = "cline"; All = "all" }
$args = @($engine, "uninstall", "--target", $map[$Target])
if ($HomeDir) { $args += @("--home", $HomeDir) }
if ($Force) { $args += "--force" }
& $python.Source $args
exit $LASTEXITCODE
