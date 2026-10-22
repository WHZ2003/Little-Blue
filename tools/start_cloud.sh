#!/usr/bin/env bash
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$root"
if [[ "$EUID" == 0 ]]; then
    exec sudo -H -u dafeiyu bash "$root/tools/start_cloud.sh"
fi
[[ "$(id -u)" == 10001 ]] || { echo 'Run as the dafeiyu service user.'; exit 1; }
.venv/bin/python -m little_blue.handoff check
docker compose config --quiet
if docker compose config --services | grep -qx hermes; then
    docker compose up -d --no-deps --no-build --wait hermes
fi
docker compose up -d --no-deps --no-build --wait core
echo 'Core ready on server loopback port 8765; browser is disabled by default.'
