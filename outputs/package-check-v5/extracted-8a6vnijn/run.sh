#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
exec python3 -u bot.py --risk-policy learned --search-ms 600 --particles 256 --worlds 6 --depth 4 --nodes 120000 "$@"
