"""Manage cloud Bilibili browsing through the existing authenticated SSH tunnel."""
import argparse
import json
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def main():
    p = argparse.ArgumentParser()
    p.add_argument('action', choices=['status', 'login', 'check', 'run', 'pause', 'auto', 'cancel', 'report', 'share-on', 'share-off', 'mention-on', 'mention-off', 'verify', 'dm-on', 'dm-off', 'dm-check'])
    p.add_argument('--uid',type=int)
    p.add_argument('--name')
    args = p.parse_args()
    token = (ROOT / '.secrets/cloud_core_token').read_text().strip()
    with httpx.Client(base_url='http://127.0.0.1:18765', trust_env=False, timeout=45,
                      headers={'Authorization':'Bearer ' + token}) as client:
        body = {'action': {'login':'login_start','dm-check':'dm_check'}.get(args.action, args.action)}
        if args.action in ('pause', 'auto', 'share-on', 'share-off', 'mention-on', 'mention-off', 'dm-on', 'dm-off'):
            r = client.post('/bili', json={'action':'status'}); r.raise_for_status()
            config = r.json()['settings']
            if args.action in ('dm-on', 'dm-off'):
                config['share_dm'] = args.action == 'dm-on'
                if args.uid is not None:config['dm_uid'] = args.uid
                if args.name is not None:config['dm_name'] = args.name
                if args.action == 'dm-on':
                    config['share_bili'] = False
                    config['share_wechat'] = False
            elif args.action in ('mention-on', 'mention-off'):
                config['share_bili'] = args.action == 'mention-on'
                if args.uid is not None:config['mention_uid'] = args.uid
                if args.name is not None:config['mention_name'] = args.name
            elif args.action in ('share-on', 'share-off'):
                config['share_wechat'] = args.action == 'share-on'
            else:
                config['enabled'] = args.action == 'auto'
            body = {'action':'configure', 'settings':config}
        r = client.post('/bili', json=body)
        if r.status_code != 200:
            raise SystemExit(f'Bilibili command did not complete (HTTP {r.status_code}); check cloud status.')
        result = r.json()
        if args.action == 'login':
            import qrcode
            output = ROOT / 'qa-output/bilibili-login.png'
            output.parent.mkdir(exist_ok=True)
            qrcode.make(result['url']).save(output)
            print(json.dumps({'qr_image':str(output), 'expires_in':result['expires_in']}))
        elif args.action == 'report':
            output = ROOT / 'backups/bilibili-report.md'
            output.parent.mkdir(exist_ok=True)
            output.write_text(result['markdown'], encoding='utf-8')
            print(json.dumps({'report':str(output)}))
        else:
            print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == '__main__':
    main()
