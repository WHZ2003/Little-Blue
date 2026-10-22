"""Create a public-source/model bundle; transfer private state separately via handoff."""
import hashlib
import json
from pathlib import Path
import tarfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    names = {'Dockerfile', 'pyproject.toml', 'requirements-core.txt', '.dockerignore', '.gitignore'}
    for folder in ('little_blue', 'browser_service', 'assets/memes', 'models/bge-small-zh-v1.5'):
        for path in (ROOT / folder).rglob('*'):
            if path.is_file() and '__pycache__' not in path.parts and not any(p in path.parts for p in ('user', 'originals')):
                if path.suffix not in ('.pyc', '.partial', '.sqlite3', '.log'):
                    names.add(path.relative_to(ROOT).as_posix())
    names.update(('tools/init_core.py', 'tools/setup_cloud.sh', 'tools/start_cloud.sh', 'docs/cloud-deployment.md'))
    output = ROOT / 'backups/DaFeiYu-cloud-deploy.tar.gz'
    output.parent.mkdir(exist_ok=True)
    with tarfile.open(output, 'w:gz') as archive:
        for name in sorted(names):
            archive.add(ROOT / name, arcname=name, recursive=False)
        archive.add(ROOT / 'deploy/cloud/compose.yaml', arcname='compose.yaml')
    with tarfile.open(output) as archive:
        assert not any(part in ('.env', '.secrets', '.handoff', 'data') for entry in archive for part in Path(entry.name).parts)
    print(json.dumps({'package': str(output), 'bytes': output.stat().st_size,
                      'sha256': hashlib.sha256(output.read_bytes()).hexdigest()}, indent=2))


if __name__ == '__main__':
    main()
