#!/usr/bin/env bash
# Launch: Open the native desktop reservation client with one action.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
exec ./run.sh "$@" desktop
