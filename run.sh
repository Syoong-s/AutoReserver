#!/usr/bin/env bash
# Launch: Validate complete browser imports before choosing the Python interpreter.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
exec python3 scripts/launch.py -m autoreserve "$@"
