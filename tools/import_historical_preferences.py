"""Import a reviewed preference manifest without replaying historical conversations.

Run inside the core environment against a backed-up database. Dry-run by default.
Only preference records/history/receipts are writable; operational Settings are excluded.
"""
import argparse
import hashlib
import json
import sqlite3
import time

from little_blue.preferences import DEFAULTS, SETTING_KEYS, Change, executable_evidence, validate
from little_blue.models import Settings


def import_review(db, manifest, *, apply=False):
    source = manifest['source']
    if not source.startswith('historical-review:') or len(source) > 160:
        raise ValueError('Invalid migration source')
    fingerprint = hashlib.sha256(json.dumps(manifest, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    if db.in_transaction:
        raise ValueError('An independent transaction is required')
    db.row_factory = sqlite3.Row
    db.execute('BEGIN IMMEDIATE' if apply else 'BEGIN')
    try:
        old = db.execute('SELECT * FROM preference_receipts WHERE source=?', (source,)).fetchone()
        if old:
            if old['fingerprint'] != fingerprint:
                raise ValueError('Conflicting migration replay')
            db.rollback()
            return {**json.loads(old['result']), 'replayed': True}
        revision = db.execute('SELECT coalesce(max(revision),0) FROM preference_history').fetchone()[0]
        if revision != manifest['expected_revision']:
            raise ValueError('Preferences changed; review a fresh snapshot')
        records = [dict(r) for r in db.execute('SELECT * FROM shared_preferences ORDER BY key')]
        applicable, skipped, seen = [], [], set()

        def evidence(items):
            if not items:
                raise ValueError('Missing original user evidence')
            for item in items:
                row = db.execute('SELECT role,text,created FROM conversation WHERE id=?', (item['id'],)).fetchone()
                if not row or row['role'] != 'user' or row['text'] != item['text'] or row['created'] != item['created']:
                    raise ValueError('Original user evidence does not match')
                if row['created'] > time.time() + 60:
                    raise ValueError('Future evidence')
            return max(x['created'] for x in items)

        for entry in manifest['changes']:
            key, value = entry['key'], entry['value']
            validate(key, value)
            if key in SETTING_KEYS or key in seen:
                raise ValueError('Operational settings or duplicate keys are not allowed')
            seen.add(key)
            created = evidence(entry['evidence'])
            quote = '\n'.join(x['text'] for x in entry['evidence'])
            change = Change(key=key, value=value, quote=quote)
            if not executable_evidence(change):
                raise ValueError('Missing executable evidence')
            prior = next((r for r in records if r['key'] == key), None)
            if prior and prior['source_at'] >= created:
                skipped.append(key)
            else:
                applicable.append((change, created))
        for entry in manifest.get('deferred', []):
            evidence(entry['evidence'])
        values = {**DEFAULTS, **{r['key']: json.loads(r['value']) for r in records if json.loads(r['value']) is not None}}
        row = db.execute("SELECT value FROM kv WHERE key='settings'").fetchone()
        settings = json.loads(row[0]) if row else {}
        values.update({k: settings.get(k, getattr(Settings(), k)) for k in SETTING_KEYS})
        before = {'revision': revision, 'values': values, 'records': records}
        receipt = '已从历史原话导入偏好：' + '、'.join(c.key for c, _ in applicable) + '。格式由发送器执行；语气和兴趣用于两端回复参考。'
        if manifest.get('deferred'):
            receipt += ' 尚未设为自动执行：' + '；'.join(x['summary'] for x in manifest['deferred']) + '。'
        result = {'status': 'applied' if applicable else 'stale', 'receipt': receipt,
                  'imported': [c.key for c, _ in applicable], 'skipped': skipped,
                  'historical_review': manifest}
        if apply:
            if applicable:
                revision = db.execute('INSERT INTO preference_history(before_json,after_json,source,created) VALUES(?,?,?,?)',
                    (json.dumps(before, ensure_ascii=False), json.dumps([c.model_dump() for c, _ in applicable], ensure_ascii=False), source, time.time())).lastrowid
                for c, created in applicable:
                    db.execute('INSERT OR REPLACE INTO shared_preferences VALUES(?,?,?,?,?,?)',
                        (c.key, json.dumps(c.value, ensure_ascii=False), source, c.quote, created, revision))
            result['revision'] = revision
            db.execute('INSERT INTO preference_receipts VALUES(?,?,?,?)',
                (source, fingerprint, json.dumps(result, ensure_ascii=False), time.time()))
            db.commit()
        else:
            result['revision'] = revision
            db.rollback()
        return result
    except BaseException:
        db.rollback()
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database')
    parser.add_argument('manifest')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    with open(args.manifest, encoding='utf-8') as f:
        manifest = json.load(f)
    with sqlite3.connect(args.database, timeout=15) as db:
        result = import_review(db, manifest, apply=args.apply)
    print(json.dumps({k: v for k, v in result.items() if k != 'historical_review'}, ensure_ascii=False))
