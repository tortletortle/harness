param(
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

Write-Host "[1/4] Checking formatting"
cargo fmt --all -- --check
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "[2/4] Checking workspace"
cargo check --workspace --all-targets
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if (-not $SkipTests) {
    Write-Host "[3/4] Running tests"
    cargo test --workspace
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} else {
    Write-Host "[3/4] Tests skipped by request"
}

Write-Host "[4/4] Building release executables"
cargo build --release -p eas-gui -p eas-server
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Artifacts:"
Write-Host "  target/release/eas-gui.exe"
Write-Host "  target/release/eas-server.exe"
