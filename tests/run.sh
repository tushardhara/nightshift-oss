#!/usr/bin/env bash
# Run the control-plane test suite. stdlib only — python3 is the one dep.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
exec python3 run_suite.py "$@"
