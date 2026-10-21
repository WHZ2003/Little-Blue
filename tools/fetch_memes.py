"""Fetch the user's requested community pack at a pinned revision; no code is run."""
import hashlib
import json
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parents[1] / 'assets' / 'memes'


def main():
    catalog = json.loads((ROOT / 'catalog.json').read_text(encoding='utf-8'))
    destination = ROOT / 'community'
    destination.mkdir(parents=True, exist_ok=True)
    with httpx.Client(trust_env=False, follow_redirects=False, timeout=20) as client:
        for item in catalog['items']:
            if item.get('source', 'community') != 'community':
                continue  # Locally generated assets never resolve to upstream URLs.
            path = destination / (item['id'] + '.webp')
            url = f"https://raw.githubusercontent.com/{catalog['repository']}/{catalog['revision']}/previews/{item['id']}.webp"
            with client.stream('GET', url) as response:
                response.raise_for_status()
                data = bytearray()
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    if len(data) > 2 * 1024 * 1024:
                        raise ValueError('Image too large')
            blob = b'blob ' + str(len(data)).encode() + b'\0' + data
            if (hashlib.sha1(blob).hexdigest() != item['git_blob_sha'] or
                    len(data) != item['bytes'] or data[:4] != b'RIFF' or data[8:12] != b'WEBP'):
                raise ValueError('Pinned image integrity check failed')
            path.write_bytes(data)
            print('Verified meme ' + item['id'], flush=True)


if __name__ == '__main__':
    main()
