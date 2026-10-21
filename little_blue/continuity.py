"""One durable conversation and explicit current activity for both channels.

Activity is user-reported context, never a runnable agent task or screen inference.
Only anchored statements/commands mutate it; model output cannot do so.
"""
import re
import time


class Continuity:
    def __init__(self, store):
        self.store = store
        with store.lock, store.db:
            store.db.execute('''CREATE TABLE IF NOT EXISTS conversation(
                id INTEGER PRIMARY KEY, role TEXT NOT NULL, origin TEXT NOT NULL,
                text TEXT NOT NULL, created REAL NOT NULL)''')
            columns = {r[1] for r in store.db.execute('PRAGMA table_info(conversation)')}
            if 'delivery' not in columns:
                store.db.execute("ALTER TABLE conversation ADD COLUMN delivery TEXT NOT NULL DEFAULT 'legacy'")
            # In-flight sends are never resumed or assumed delivered after restart.
            store.db.execute("UPDATE conversation SET delivery='failed' WHERE delivery='pending'")

    def append(self, role, text, origin, now=None, delivery='legacy'):
        if role not in ('user', 'assistant') or origin not in ('desktop', 'wechat'):
            raise ValueError('Invalid conversation source')
        stamp = time.time() if now is None else now
        with self.store.lock, self.store.db:
            mid = self.store.db.execute('INSERT INTO conversation(role,origin,text,created,delivery) VALUES(?,?,?,?,?)',
                (role, origin, text[:6000], stamp, delivery)).lastrowid
            if role == 'user':
                self.store.put('last_user_at', stamp)
            return mid

    def delivered(self, message_id, ok):
        if message_id is None:
            return
        with self.store.lock, self.store.db:
            self.store.db.execute("UPDATE conversation SET delivery=? WHERE id=? AND role='assistant' AND origin='wechat'",
                                  ('delivered' if ok else 'failed', message_id))

    def received(self, ids):
        if not ids or len(ids) > 6 or any(type(i) is not int for i in ids):
            raise ValueError('Invalid received message IDs')
        with self.store.lock:
            rows = self.store.db.execute('SELECT * FROM conversation WHERE id IN (' +
                ','.join('?' for _ in ids) + ') ORDER BY id', ids).fetchall()
        if len(rows) != len(ids) or any(r['role'] != 'user' or r['origin'] != 'wechat' for r in rows):
            raise ValueError('Invalid received message source')
        return [dict(r) for r in rows]

    def recent(self, limit=24, before=None):
        with self.store.lock:
            rows = self.store.db.execute('''SELECT * FROM conversation WHERE id < ?
                ORDER BY id DESC LIMIT ?''', (before or 9223372036854775807, min(40, max(1, limit)))).fetchall()
        return [dict(r) for r in reversed(rows)]

    def snapshot(self):
        return {'messages': self.recent(), 'activity': self.store.get('current_activity'),
                'followups': self.store.get('activity_followups', True)}

    def describe(self):
        a = self.store.get('current_activity')
        status = {'active': '进行中', 'done': '已完成', 'paused': '已暂停'}
        text = (f"你之前告诉我：{a['title']}（{status[a['status']]}）。" if a else '还没有记下你正在忙的事。')
        return text + '\n适时跟进：' + ('开' if self.store.get('activity_followups', True) else '关')

    def observe(self, text, origin, source_id, now=None):
        """Return an acknowledgement for local controls; None for ordinary dialogue."""
        now = time.time() if now is None else now
        text = text.strip()
        if text == '/context':
            return self.describe()
        if text in ('/followup on', '/followup off', '关闭跟进', '开启跟进', '不要再问进度了'):
            enabled = text in ('/followup on', '开启跟进')
            self.store.put('activity_followups', enabled)
            return '好，适合的时候我会关心一下进展。' if enabled else '好，不再主动问进度；记下的事还在。'
        title = None
        if text.startswith('/doing '):
            title = text[7:].strip()[:120]
        else:
            match = re.fullmatch(r'(?:记住[，,：: ]*)?我(?:现在正在|正在|现在在|在忙|在)([^，,。！？!?\n]{2,120})[。！!]?', text)
            if match:
                candidate = match[1].strip()
                # Don't infer an activity from questions, hypotheticals, or negations.
                if not re.search(r'[？?]|(?:吗|么|呢)$|^(?:想|考虑|假设|如果|不|没|找你|跟你|和你)', candidate):
                    title = candidate.split('，')[0].split(',')[0].strip()
        if title:
            self.store.put('current_activity', {'title': title, 'status': 'active',
                'source_id': source_id, 'source': text[:300], 'origin': origin,
                'updated': now, 'due': now + 1800, 'expires': now + 86400, 'followed': False})
            return f'记住了，你在{title}。忙完可以告诉我“做完了”，不想被问进度就说“关闭跟进”。'
        a = self.store.get('current_activity')
        clean = text.rstrip('。！! ')
        action = ('done' if clean in ('/done', '做完了', '完成了', '写完了', '忙完了', '我做完了', '我完成了') else
                  'paused' if clean in ('/pause', '先不做了', '先放一放', '暂停这件事') else
                  'later' if clean in ('/later', '晚点再问我', '稍后再问我', '还没做完', '还在做') else None)
        if action:
            if not a or a['status'] != 'active':
                return '目前没有进行中的事。可以告诉我“我正在……”来记下。'
            a.update(source_id=source_id, source=text[:300], origin=origin, updated=now)
            if action == 'later':
                a.update(due=now + 3600, expires=now + 86400, followed=False)
                reply = f"好，{a['title']}这件事晚点再聊。"
            else:
                a['status'] = action
                reply = f"记下了，{a['title']}" + ('已经完成。辛苦啦！' if action == 'done' else '先放一放。')
            self.store.put('current_activity', a)
            return reply
        return None

    def claim_followup(self, now, settings, env, gate):
        """At most once per update, at most twice/24h, with the shared proactive gate."""
        with self.store.lock:
            a = self.store.get('current_activity')
            if (not self.store.get('activity_followups', True) or not a or a['status'] != 'active'
                    or a['followed'] or not a['due'] <= now <= a['expires']
                    or not gate.available(now, settings, env)):
                return None
            recent = self.recent(1)
            if recent and now - recent[-1]['created'] < 600:
                return None
            times = [t for t in self.store.get('activity_followup_times', []) if now - t < 86400]
            if len(times) >= 2:
                return None
            a['followed'] = True
            self.store.put('current_activity', a)
            self.store.put('activity_followup_times', times + [now])
            gate.claim(now)
            return f"之前你在{a['title']}，现在进展怎么样？不急，方便时再告诉我就好。"
