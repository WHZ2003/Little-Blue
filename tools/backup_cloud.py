"""Download a consistent cloud memory/chat backup through pinned-host SSH."""
from datetime import datetime
import json
import re
from pathlib import Path
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from little_blue.memory_transfer import inspect_memory


def main():
    ssh_target = (ROOT / '.secrets/cloud_ssh_target').read_text().strip()
    if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*@[A-Za-z0-9][A-Za-z0-9.-]*', ssh_target):
        raise ValueError('Set .secrets/cloud_ssh_target to your SSH user@hostname.')
    ident = uuid.uuid4().hex
    remote = '/tmp/cloud-memory-' + ident + '.zip'
    folder = ROOT / 'backups'
    folder.mkdir(exist_ok=True)
    target = folder / ('cloud-memory-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + ident[:8] + '.zip')
    partial = target.with_suffix('.partial')
    ssh = ['ssh', '-i', str(ROOT / '.secrets/aliyun_dafeiyu_ed25519'),
           '-o', 'UserKnownHostsFile=' + str(ROOT / '.secrets/aliyun_known_hosts'),
           '-o', 'StrictHostKeyChecking=yes', '-o', 'BatchMode=yes',
           '-o', 'ConnectTimeout=20', '-o', 'ServerAliveInterval=30',
           '-o', 'ServerAliveCountMax=3', '-o', 'KexAlgorithms=curve25519-sha256',
           ssh_target]
    prefix = 'cd /opt/dafeiyu && docker compose exec -T core '
    try:
        exported = subprocess.run(ssh + [prefix + 'python -m little_blue.memory_transfer export --data-dir /data --output ' + remote], capture_output=True, timeout=180)
        if exported.returncode:
            raise RuntimeError('Cloud export failed. Check the SSH connection and core health.')
        # The path contains only a fixed prefix and a generated hexadecimal UUID.
        command = prefix + 'python -c \'import shutil,sys; shutil.copyfileobj(open("' + remote + '","rb"),sys.stdout.buffer)\''
        with partial.open('xb') as out:
            copied = subprocess.run(ssh + [command], stdout=out, stderr=subprocess.PIPE, timeout=300)
        if copied.returncode:
            raise RuntimeError('Backup download failed; the partial file was not published.')
        manifest = inspect_memory(partial)
        partial.rename(target)
        print(json.dumps({'backup': str(target), 'verified': True, 'databases': len(manifest['databases'])}, ensure_ascii=False))
    finally:
        # Remove only this invocation's temporary file inside the container.
        cleanup = prefix + 'python -c \'from pathlib import Path; Path("' + remote + '").unlink(missing_ok=True)\''
        subprocess.run(ssh + [cleanup], capture_output=True, timeout=45)


if __name__ == '__main__':
    main()
