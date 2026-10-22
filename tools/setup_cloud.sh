#!/usr/bin/env bash
# Fresh Ubuntu 24.04 deployment, run as root from /opt/dafeiyu.
set -euo pipefail
umask 077
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
[[ "$root" == /opt/dafeiyu ]] || { echo 'Extract into /opt/dafeiyu first.'; exit 1; }
[[ "$EUID" == 0 ]] || { echo 'Run with sudo.'; exit 1; }
source /etc/os-release
[[ "$ID" == ubuntu && "$VERSION_ID" == 24.04 ]] || { echo 'This bootstrap requires Ubuntu 24.04.'; exit 1; }
[[ "$(uname -m)" == x86_64 ]] || { echo 'This bundle is validated for x86_64 only.'; exit 1; }
cd "$root"
# Never initialize over a previous deployment or handoff journal.
[[ ! -e .handoff/handoff.json && ! -e compose.override.yaml ]] || { echo 'Existing handoff state: use the existing installation.'; exit 1; }
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y docker.io docker-compose-v2 python3-venv ca-certificates
systemctl enable --now docker
existing="$(getent passwd 10001 | cut -d: -f1 || true)"
[[ -z "$existing" || "$existing" == dafeiyu ]] || { echo 'UID 10001 already belongs to another account.'; exit 1; }
if ! id dafeiyu >/dev/null 2>&1; then
    useradd --uid 10001 --user-group --no-create-home --home-dir /opt/dafeiyu --shell /usr/sbin/nologin dafeiyu
fi
[[ "$(id -u dafeiyu)" == 10001 ]] || { echo 'Unexpected dafeiyu user ID.'; exit 1; }
usermod -aG docker dafeiyu
# Match the container UID so private handoff keys remain mode 0600 and readable
# only by the service identity, including inside the offline import helper.
chmod 755 "$root"
mkdir -p .handoff backups .secrets assets/memes models
chmod 700 .handoff backups .secrets
[[ -e .env ]] || install -m 600 /dev/null .env
python3 tools/init_core.py
python3 -m venv .venv
.venv/bin/pip install 'httpx==0.28.1' 'cryptography==48.0.1' 'pydantic==2.14.1'
if [[ "$(swapon --noheadings --show | wc -l)" == 0 && ! -e /swapfile-dafeiyu ]]; then
    fallocate -l 1G /swapfile-dafeiyu
    chmod 600 /swapfile-dafeiyu
    mkswap /swapfile-dafeiyu
    swapon /swapfile-dafeiyu
    printf '/swapfile-dafeiyu none swap sw 0 0\n' >> /etc/fstab
fi
# Absolute deployment root is checked above; no other directories are changed.
chown -R dafeiyu:dafeiyu "$root"
if ! docker image inspect little-blue-core:local >/dev/null 2>&1; then
    echo 'Load the supplied core image first: sudo docker load -i /path/core-image.tar'
    echo 'Then run: sudo bash tools/start_cloud.sh'
    exit 0
fi
bash tools/start_cloud.sh
