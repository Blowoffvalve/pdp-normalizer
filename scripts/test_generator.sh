#!/usr/bin/env bash
# Runs the generator tests (including the on-campus housing enrichment) from the build folder.
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m pytest tests/generator -q "$@"
