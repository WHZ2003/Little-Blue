"""Create a local API token without reading/modifying the user's DeepSeek key."""
from pathlib import Path
import secrets

root = Path(__file__).resolve().parents[1]
folder = root / '.secrets'
folder.mkdir(exist_ok=True)
token = folder / 'core_token'
try:
    with token.open('x', encoding='utf-8') as stream:
        stream.write(secrets.token_urlsafe(48))
    token.chmod(0o600)
    print('Created local core authentication token (value hidden).')
except FileExistsError:
    print('Keeping existing core token.')

browser_token = folder / 'browser_token'
try:
    with browser_token.open('x', encoding='utf-8') as stream:
        stream.write(secrets.token_urlsafe(48))
    browser_token.chmod(0o600)
    print('Created separate browser authentication token (value hidden).')
except FileExistsError:
    print('Keeping existing browser token.')

bridge_token = folder / 'bridge_token'
try:
    with bridge_token.open('x', encoding='utf-8') as stream:
        stream.write(secrets.token_urlsafe(48))
    bridge_token.chmod(0o600)
    print('Created limited Windows bridge token (value hidden).')
except FileExistsError:
    print('Keeping existing bridge token.')
