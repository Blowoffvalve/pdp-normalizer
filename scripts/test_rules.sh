#!/usr/bin/env bash
# Runs the rules tests from the build folder.
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m pytest tests/rules -q "$@"
