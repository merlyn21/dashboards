#!/usr/bin/env bash
# Terminal view of this machine's connections: fetch missing databases on the first run, then start.
# sudo ./start.sh                     collect here (root: conntrack, iptables, process names)
# ./start.sh --url http://host:8787   show what a running linux-api service collects
# ./start.sh --update                 refresh the databases first
set -euo pipefail
cd "$(dirname "$0")"
args=()
for a in "$@"; do
  if [ "$a" = "--update" ]; then python3 fetch_data.py --update || true; else args+=("$a"); fi
done
python3 fetch_data.py || echo "Не все базы получены: места или организации будут неизвестны." >&2
exec python3 tui.py ${args[@]+"${args[@]}"}
