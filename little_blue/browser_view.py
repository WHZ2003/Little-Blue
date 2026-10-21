"""Open a single-use local viewer link without claiming the Qt host session."""
from pathlib import Path
import uuid
import webbrowser

import httpx


def main():
    token = (Path(__file__).resolve().parents[1] / '.secrets/core_token').read_text().strip()
    with httpx.Client(trust_env=False, timeout=8) as client:
        response = client.post('http://127.0.0.1:8765/rpc',
            headers={'Authorization': 'Bearer ' + token},
            json={'op': 'browser', 'browser_action': 'view', 'host': uuid.uuid4().hex})
        response.raise_for_status()
        url = response.json()['url']
    if not url.startswith('http://127.0.0.1:8766/?ticket='):
        raise ValueError('Invalid browser viewer URL')
    webbrowser.open(url)


if __name__ == '__main__':
    main()
