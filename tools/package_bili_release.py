"""Package Bilibili adaptation sources and candidate Compose config, never user data."""
import copy
import hashlib
import json
from pathlib import Path
import tarfile

import yaml
from package_hermes_release import configuration

ROOT = Path(__file__).resolve().parents[1]


def main():
    folder = ROOT / '.research/bili-release'
    folder.mkdir(exist_ok=True)
    config = configuration()
    config['services']['core']['image'] = 'little-blue-core:romance-v3'
    config['services']['hermes']['image'] = 'dafeiyu-hermes:style-v1'
    (folder / 'compose.yaml').write_text(yaml.safe_dump(config,sort_keys=False))
    canary = copy.deepcopy(config)
    canary['name'] = 'dafeiyu-bili-canary'
    canary['services'].pop('browser')
    core = canary['services']['core']
    core.pop('build')
    core['ports'] = ['127.0.0.1:18767:8765']
    core['env_file'] = [{'path':'/opt/dafeiyu/.env','required':True}]
    core['volumes'] = ['core-data:/data','/opt/dafeiyu/models:/models:ro','/opt/dafeiyu/assets/memes:/app/assets/memes:ro']
    for secret in canary['secrets'].values():secret['file'] = '/opt/dafeiyu/' + secret['file'].removeprefix('./')
    for service in canary['services'].values():service['restart'] = 'no'
    (folder / 'canary.yaml').write_text(yaml.safe_dump(canary,sort_keys=False))
    (folder / 'Core.Dockerfile').write_text('FROM little-blue-core:bili-dependencies\nCOPY little_blue/ /app/little_blue/\n')
    (folder / 'Hermes.Dockerfile').write_text('FROM dafeiyu-hermes:55ee4ac-final\nCOPY deploy/hermes/worker.py /adapter/worker.py\n')
    output = ROOT / 'backups/bili-release.tar.gz'
    with tarfile.open(output,'w:gz') as tar:
        for p in folder.iterdir():tar.add(p,arcname=p.name)
        for pattern in ('*.py','*.json','*-LICENSE.txt'):
            for p in (ROOT/'little_blue').glob(pattern):tar.add(p,arcname=p.relative_to(ROOT).as_posix())
        for name in ('deploy/hermes/worker.py','deploy/bili/upstream.json','requirements-core.txt','pyproject.toml',
                     'tools/start_cloud.sh','tools/bili_cloud.py','tools/package_bili_release.py'):
            tar.add(ROOT/name,arcname=name)
    print(json.dumps({'package':str(output),'sha256':hashlib.file_digest(output.open('rb'),'sha256').hexdigest()}))


if __name__ == '__main__':main()
