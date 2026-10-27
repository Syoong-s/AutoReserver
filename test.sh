#!/usr/bin/env bash
# Verify: Run standard-library tests plus the local-only browser scenarios.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
export RUN_BROWSER_TESTS=1
export RUN_NATIVE_TESTS=1
export RUN_GTK_TESTS=1
export AUTORESERVE_NO_BOOTSTRAP=1
exec python3 scripts/launch.py -m unittest discover -s tests -v
