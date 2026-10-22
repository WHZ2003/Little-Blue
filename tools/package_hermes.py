"""Stage the pinned upstream source without private application files."""
import json
from pathlib import Path
import shutil
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    spec = json.loads((ROOT / 'deploy/hermes/upstream.json').read_text())
    repo = ROOT / '.research/hermes-agent'
    revision = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()
    if revision != spec['commit']:
        raise ValueError('Hermes source revision does not match the pinned manifest')
    target = ROOT / '.research/hermes-build'
    target.mkdir(exist_ok=True)
    names = subprocess.check_output(['git', '-C', str(repo), 'ls-files', '-z']).decode().split('\0')
    excluded = {'tests', 'tests-js', 'website', 'web', 'apps', 'assets', 'ui-tui', 'evals', '.github', 'nix', 'contributors'}
    with tarfile.open(target / 'hermes-source.tar.gz', 'w:gz') as tar:
        for name in names:
            if name and Path(name).parts[0] not in excluded and (repo / name).is_file():
                tar.add(repo / name, arcname=name, recursive=False)
    for name in ('Dockerfile', 'service.py', 'worker.py'):
        path = ROOT / 'deploy/hermes' / name
        if path.exists():
            shutil.copyfile(path, target / name)
    print(json.dumps({'build_context': str(target), 'commit': revision, 'source_bytes': (target / 'hermes-source.tar.gz').stat().st_size}))


if __name__ == '__main__':
    main()
