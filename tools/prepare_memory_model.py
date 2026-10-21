"""Explicit one-time download of a pinned public ONNX model, never user memories."""
import hashlib
import json
from pathlib import Path
import urllib.request

REPO = 'Qdrant/bge-small-zh-v1.5'
REVISION = '46fbe35fd4374a00fee7de77dfddaeb6dd6a2c59'
FILES = ['README.md', 'config.json', 'model_optimized.onnx', 'ort_config.json',
         'special_tokens_map.json', 'tokenizer.json', 'tokenizer_config.json', 'vocab.txt']


def main():
    folder = Path(__file__).resolve().parents[1] / 'models/bge-small-zh-v1.5'
    folder.mkdir(parents=True, exist_ok=True)
    manifest_path = folder / 'manifest.json'
    old = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    hashes = {}
    for name in FILES:
        target = folder / name
        if (old.get('revision') == REVISION and target.is_file() and
                hashlib.sha256(target.read_bytes()).hexdigest() == old.get('files', {}).get(name)):
            hashes[name] = old['files'][name]
            continue
        temp = target.with_suffix(target.suffix + '.partial')
        url = f'https://huggingface.co/{REPO}/resolve/{REVISION}/{name}'
        with urllib.request.urlopen(url, timeout=60) as response, temp.open('wb') as output:
            size = 0
            while block := response.read(1024 * 1024):
                size += len(block)
                if size > 300 * 1024 * 1024:
                    raise ValueError('Model file exceeds size bound')
                output.write(block)
        temp.replace(target)
        hashes[name] = hashlib.sha256(target.read_bytes()).hexdigest()
        print('Downloaded', name, size, flush=True)
    manifest_path.write_text(json.dumps({'repo': REPO, 'revision': REVISION, 'files': hashes},
                                         indent=2) + '\n', encoding='utf-8')
    print('Local model ready:', folder, flush=True)


if __name__ == '__main__':
    main()
