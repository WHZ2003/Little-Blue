"""Install the pinned nekowhale GIF pack without executing upstream code."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
from pathlib import Path
from urllib.parse import quote

import httpx
from PIL import Image

ROOT = Path(__file__).resolve().parents[1] / 'assets/memes'
REVISION = 'b7b58c0ff347d639fabe87d90d6716c4b3cad185'
LIMIT = 20 * 1024 * 1024


def install():
    metadata = json.loads((ROOT/'nekowhale-meta/metadata.json').read_text(encoding='utf-8'))
    folder = ROOT/'nekowhale'
    folder.mkdir(exist_ok=True)
    def fetch(pair):
        number, entry = pair
        ident = f'n{number:03d}'
        path = folder/(ident+'.gif')
        pack, stem = entry['id'].split('/')
        url = f'https://kiriyuki.com/media/gif/{quote(pack)}/{quote(stem)}.gif?h={entry["hash"]}'
        data = path.read_bytes() if path.exists() else b''
        if hashlib.sha1(data).hexdigest()[:12] != entry['hash']:
            with httpx.Client(trust_env=False, follow_redirects=False, timeout=40) as client:
                with client.stream('GET', url) as response:
                    response.raise_for_status()
                    chunks = bytearray()
                    for chunk in response.iter_bytes():
                        chunks.extend(chunk)
                        if len(chunks) > LIMIT:
                            raise ValueError('GIF exceeds transport size: '+ident)
                    data = bytes(chunks)
        if len(data) > LIMIT or data[:6] not in (b'GIF87a', b'GIF89a') or hashlib.sha1(data).hexdigest()[:12] != entry['hash']:
            raise ValueError('Pinned GIF integrity check failed: '+ident)
        with Image.open(io.BytesIO(data)) as im:
            if im.width > 2048 or im.height > 2048 or im.n_frames > 600:
                raise ValueError('GIF dimensions or frame count exceed limit')
            frames = im.n_frames
            im.verify()
        path.write_bytes(data)
        return dict(id=ident, title=stem.split('_',1)[-1], source='nekowhale', format='gif',
                    bytes=len(data), git_blob_sha=hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest(),
                    upstream_id=entry['id'], url=url, frames=frames, emotion=entry['emotion'],
                    tags=entry['tags'], direction=entry.get('dir',''),
                    auto_context=entry['use']+('；方向：'+entry['dir'] if entry.get('dir') else ''))
    with ThreadPoolExecutor(max_workers=6) as pool:
        items = list(pool.map(fetch, enumerate(metadata['stickers'],1)))
    manifest = {'repository':'Loping151/dsh-nekowhale-stickers', 'revision':REVISION,
                'attribution':metadata['attribution'], 'items':items}
    target = ROOT/'nekowhale.json'
    temp = target.with_suffix('.tmp')
    temp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    temp.replace(target)
    print(f'Installed and verified {len(items)} GIFs ({sum(i["bytes"] for i in items)} bytes)')


if __name__ == '__main__':
    install()
