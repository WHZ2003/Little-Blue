"""Prepare the cloud overlay and application source; no keys or state enter the release."""
import copy
import hashlib
import json
from pathlib import Path
import tarfile

import yaml

ROOT = Path(__file__).resolve().parents[1]


def configuration():
    base = yaml.safe_load((ROOT / 'deploy/cloud/compose.yaml').read_text())
    overlay = yaml.safe_load((ROOT / 'deploy/cloud/compose.hermes.yaml').read_text())
    for name, service in overlay['services'].items():
        existing = base['services'].setdefault(name, {})
        for key, value in service.items():
            if isinstance(value, dict):
                existing.setdefault(key, {}).update(value)
            else:
                existing[key] = value
    base['services']['core']['image'] = 'little-blue-core:hermes-55ee4ac-final'
    base['secrets'].update(overlay['secrets'])
    base['networks'] = {'default': {}, **overlay['networks']}
    return base


def main():
    folder = ROOT / '.research/hermes-release'
    folder.mkdir(exist_ok=True)
    config = configuration()
    (folder / 'compose.yaml').write_text(yaml.safe_dump(config, sort_keys=False), encoding='utf-8')
    canary = copy.deepcopy(config)
    canary['name'] = 'dafeiyu-hermes-canary'
    canary['services'].pop('browser')
    core = canary['services']['core']
    core.pop('build')
    core['ports'] = ['127.0.0.1:18767:8765']
    core['env_file'] = [{'path': '/opt/dafeiyu/.env', 'required': True}]
    core['volumes'] = ['core-data:/data', '/opt/dafeiyu/assets/memes:/app/assets/memes:ro', '/opt/dafeiyu/models:/models:ro']
    for secret in canary['secrets'].values():
        secret['file'] = '/opt/dafeiyu/' + secret['file'].removeprefix('./')
    for service in canary['services'].values():
        service['restart'] = 'no'
    (folder / 'canary.yaml').write_text(yaml.safe_dump(canary, sort_keys=False), encoding='utf-8')
    output = ROOT / 'backups/hermes-release.tar.gz'
    with tarfile.open(output, 'w:gz') as tar:
        for name in ('compose.yaml', 'canary.yaml'):
            tar.add(folder / name, arcname=name)
        for path in sorted((ROOT / 'little_blue').glob('*.py')):
            tar.add(path, arcname=path.relative_to(ROOT).as_posix())
        for name in ('tools/init_core.py', 'tools/start_cloud.sh', 'deploy/hermes/upstream.json',
                     'deploy/hermes/Dockerfile', 'deploy/hermes/service.py', 'deploy/hermes/worker.py'):
            tar.add(ROOT / name, arcname=name)
    print(json.dumps({'package': str(output), 'sha256': hashlib.file_digest(output.open('rb'), 'sha256').hexdigest()}))


if __name__ == '__main__':
    main()
