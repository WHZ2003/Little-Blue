"""Local-only model worker. stdin/stdout JSON protocol; no tools or model downloads."""
import hashlib
import json
import os
from pathlib import Path
import sys
from .vector_index import VectorIndex, CHUNK_VERSION


def main():
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
    os.environ['TOKENIZERS_PARALLELISM'] = 'false'
    folder = Path(sys.argv[2])
    manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
    for name, expected in manifest['files'].items():
        file = folder / name
        if file.resolve().parent != folder.resolve():
            raise ValueError('Invalid model file')
        if hashlib.sha256(file.read_bytes()).hexdigest() != expected:
            raise ValueError('Model checksum mismatch')
    from fastembed import TextEmbedding
    model = TextEmbedding('BAAI/bge-small-zh-v1.5', specific_model_path=str(folder),
                          local_files_only=True, threads=1, providers=['CPUExecutionProvider'])
    key = 'bge-small-zh-v1.5:' + hashlib.sha256((folder / 'manifest.json').read_bytes()).hexdigest() + ':' + CHUNK_VERSION
    index = VectorIndex(sys.argv[1], model, key)
    print(json.dumps({'ready': True, 'model': key, **index.status()}), flush=True)
    try:
        for line in sys.stdin:
            try:
                request = json.loads(line)
                if request['op'] == 'index':
                    result = index.backfill()
                elif request['op'] == 'search':
                    result = {'hits': index.search(request['query'], request['limit'])}
                else:
                    raise ValueError('Unknown worker request')
                print(json.dumps(result, ensure_ascii=False), flush=True)
            except Exception:
                print(json.dumps({'error': 'vector_operation_failed'}), flush=True)
    finally:
        index.close()


if __name__ == '__main__':
    main()
