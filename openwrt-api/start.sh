#!/usr/bin/env bash
# Start the map: fetch missing databases on the first run, then run the service.
# ./start.sh --update refreshes the databases first. On Windows use start.bat.
set -euo pipefail
cd "$(dirname "$0")"
exec python3 start.py "$@"
