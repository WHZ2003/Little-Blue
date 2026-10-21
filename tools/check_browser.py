"""Read-only web smoke test against a paused local browser; leaves it paused."""
import json
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parents[1]
token = (ROOT / '.secrets/browser_token').read_text().strip()
base = 'http://127.0.0.1:8766'
with httpx.Client(base_url=base, timeout=25, trust_env=False) as client:
    headers = {'Authorization': 'Bearer ' + token}
    def status():
        r = client.get('/internal/status', headers=headers)
        r.raise_for_status()
        return r.json()
    def command(action, **extra):
        r = client.post('/internal/control', headers=headers,
            json={'action': action, 'version': status()['version'], **extra})
        r.raise_for_status()
        return r.json()
    assert status()['mode'] == 'pause', 'Pause the browser before running this check'
    assert client.get('/api/status').status_code == 401
    assert client.get('/internal/status').status_code == 401
    try:
        command('auto')
        page = command('visit', url='https://science.nasa.gov/earth/')
        assert page['text'].strip() and len(page['text']) > 100 and page['url'].startswith('https://science.nasa.gov/')
        print(json.dumps({'page_title':page['title'], 'text_chars':len(page['text']), 'links':len(page['links'])}), flush=True)
        old = status()['version']
        command('pause')
        assert client.post('/internal/control', headers=headers,
            json={'action':'visit','version':old,'url':'https://science.nasa.gov/earth/'}).status_code == 400
        assert client.post('/internal/control', headers=headers,
            json={'action':'visit','version':status()['version'],'url':'http://127.0.0.1:8765'}).status_code == 400
        ticket = client.post('/internal/ticket', headers=headers, json={}).json()['url']
        assert client.get(ticket, follow_redirects=True).status_code == 200
        assert client.get(ticket, follow_redirects=False).status_code == 401
        human_headers = {'Origin':base,'X-Browser-Control':'1'}
        def human(action, **extra):
            r = client.post('/api/control', headers=human_headers,
                json={'action':action,'version':status()['version'],**extra})
            r.raise_for_status()
            return r.json()
        human('manual')
        human('scroll', delta=550)
        assert client.post('/internal/control', headers=headers,
            json={'action':'visit','version':status()['version'],'url':'https://science.nasa.gov/earth/'}).status_code == 400
        image = client.get('/api/screenshot')
        image.raise_for_status()
        assert image.content.startswith(b'\xff\xd8') and len(image.content)>2000
        (ROOT/'qa-output').mkdir(exist_ok=True)
        (ROOT/'qa-output/browser-page.jpg').write_bytes(image.content)
        print('PASS: real Chromium page, authenticated screenshot, single-use ticket, manual scroll, pause and stale-agent rejection.', flush=True)
    finally:
        command('pause')
