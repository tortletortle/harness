#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

echo "[1/4] Checking formatting"
cargo fmt --all -- --check
echo "[2/4] Checking workspace"
cargo check --workspace --all-targets
echo "[3/4] Running tests"
cargo test --workspace
echo "[4/4] Building release executables"
cargo build --release -p eas-gui -p eas-server

printf 'Artifacts:\n  target/release/eas-gui\n  target/release/eas-server\n'
