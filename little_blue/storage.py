"""SQLite storage with a shared lock; parameterized queries and bounded retrieval."""
from collections import deque
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
import threading
import uuid
from .errors import BudgetExceeded


class Store:
    def __init__(self, path):
        self.path = str(Path(path).resolve())
        self.retriever = None
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False, timeout=5)
        self.db.row_factory = sqlite3.Row
        self.working = deque(maxlen=24)
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS memories(id INTEGER PRIMARY KEY, kind TEXT NOT NULL,
                text TEXT NOT NULL, created TEXT NOT NULL);
            CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(text, content='memories', content_rowid='id');
            CREATE TRIGGER IF NOT EXISTS memory_insert AFTER INSERT ON memories BEGIN
                INSERT INTO memory_fts(rowid,text) VALUES(new.id,new.text); END;
            CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, status TEXT NOT NULL,
                detail TEXT NOT NULL, updated TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, tool TEXT,
                decision TEXT, created TEXT);
            CREATE TABLE IF NOT EXISTS reminders(id TEXT PRIMARY KEY, text TEXT,
                due REAL NOT NULL, delivered INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS budget(day TEXT PRIMARY KEY, requests INTEGER NOT NULL,
                reserved INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS budget_reservations(id TEXT PRIMARY KEY, day TEXT NOT NULL,
                amount INTEGER NOT NULL, actual INTEGER);
            CREATE TABLE IF NOT EXISTS channel_messages(channel TEXT, message_id TEXT,
                created TEXT NOT NULL, PRIMARY KEY(channel, message_id));
        """)
        from .vector_index import SCHEMA
        self.db.executescript(SCHEMA + """
            CREATE TRIGGER IF NOT EXISTS memory_fts_delete AFTER DELETE ON memories BEGIN
                INSERT INTO memory_fts(memory_fts,rowid,text) VALUES('delete',old.id,old.text); END;
            CREATE TRIGGER IF NOT EXISTS memory_fts_update AFTER UPDATE OF text ON memories BEGIN
                INSERT INTO memory_fts(memory_fts,rowid,text) VALUES('delete',old.id,old.text);
                INSERT INTO memory_fts(rowid,text) VALUES(new.id,new.text); END;
        """)
        # Incomplete tasks are never automatically replayed after a crash/restart.
        with self.db:
            self.db.execute("UPDATE tasks SET status='interrupted', updated=? WHERE status IN ('running','pending','approval')", (self.now(),))
        from .growth import GrowthMemory
        self.growth = GrowthMemory(self)
        from .continuity import Continuity
        self.continuity = Continuity(self)
        from .memory_ledger import MemoryLedger
        self.ledger = MemoryLedger(self)

    @staticmethod
    def now():
        return datetime.now(timezone.utc).isoformat()

    def get(self, key, default=None):
        with self.lock:
            row = self.db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row[0])
        except (ValueError, TypeError):
            return default

    def put(self, key, value):
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO kv VALUES(?,?)", (key, json.dumps(value, ensure_ascii=False)))

    def claim_message(self, channel, message_id):
        """Durable at-most-once admission; unfinished messages aren't replayed."""
        with self.lock, self.db:
            return self.db.execute('INSERT OR IGNORE INTO channel_messages VALUES(?,?,?)',
                (channel, message_id, self.now())).rowcount == 1

    def remember(self, text, kind="episodic"):
        if kind not in ("episodic", "semantic", "reflection"):
            raise ValueError("Invalid memory kind")
        text = str(text).strip()[:4000]
        if not text:
            return
        with self.lock, self.db:
            memory_id = self.db.execute("INSERT INTO memories(kind,text,created) VALUES(?,?,?)", (kind, text, self.now())).lastrowid
            self.working.append(text)
            if kind == 'semantic':
                self.db.execute('INSERT INTO growth_sources VALUES(?,?,?)', (memory_id, text[:1000], 'explicit_preference'))
        return memory_id

    def remember_dialogue(self, text, answer, learn=True):
        from .persona import NAME
        memory_id = self.remember(f'用户：{text[:1000]}\n{NAME}：{answer[:2000]}')
        if learn:
            self.growth.source(memory_id, text, 'user_dialogue')

    def search_candidates(self, query, limit=8):
        limit = max(1, min(limit, 60))
        terms = re.findall(r"\w+", str(query))[:12]
        with self.lock:
            if terms:
                expression = " OR ".join('"' + term + '"' for term in terms)
                rows = self.db.execute("SELECT m.* FROM memory_fts f JOIN memories m ON m.id=f.rowid WHERE memory_fts MATCH ? ORDER BY rank LIMIT ?", (expression, limit)).fetchall()
                # unicode61 doesn't segment Chinese words; use literal substring fallback.
                if not rows:
                    rows = self.db.execute("SELECT * FROM memories WHERE instr(text,?)>0 ORDER BY id DESC LIMIT ?", (str(query)[:500], limit)).fetchall()
            else:
                rows = self.db.execute("SELECT * FROM memories ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def search(self, query, limit=8):
        limit = max(1, min(limit, 30))
        return self.ledger.rank(self.search_candidates(query, min(60, limit * 3)), str(query), limit)

    async def search_hybrid(self, query, limit=8):
        if self.retriever is not None:
            return await self.retriever.search(query, limit)
        return self.search(query, limit)

    def task(self, task_id, status, detail):
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO tasks VALUES(?,?,?,?)", (task_id, status, detail[:1000], self.now()))

    def tasks(self):
        with self.lock:
            return [dict(r) for r in self.db.execute("SELECT * FROM tasks ORDER BY updated DESC LIMIT 30")]

    def audit(self, tool, decision):
        with self.lock, self.db:
            # Do not log arguments, secrets, file contents or private URLs.
            self.db.execute("INSERT INTO audit(tool,decision,created) VALUES(?,?,?)", (tool, decision, self.now()))

    def semantic(self, limit=8):
        with self.lock:
            rows = [dict(r) for r in self.db.execute("SELECT m.* FROM memories m LEFT JOIN memory_meta x ON x.memory_id=m.id "
                "WHERE m.kind='semantic' AND coalesce(x.status,'active')='active' ORDER BY m.id DESC LIMIT ?", (min(limit, 20),))]
            return [{**r, 'memory': self.ledger.meta(r)} for r in rows]

    def budget_usage(self):
        day = datetime.now().astimezone().date().isoformat()
        with self.lock:
            row = self.db.execute("SELECT requests,reserved FROM budget WHERE day=?", (day,)).fetchone()
        return dict(row) if row else {"requests": 0, "reserved": 0}

    def reserve(self, requests_limit, token_limit, amount):
        if type(amount) is not int or amount <= 0:
            raise ValueError('Invalid token reservation')
        day = datetime.now().astimezone().date().isoformat()
        ticket = uuid.uuid4().hex
        with self.lock, self.db:
            self.db.execute("INSERT OR IGNORE INTO budget VALUES(?,0,0)", (day,))
            row = self.db.execute("SELECT requests,reserved FROM budget WHERE day=?", (day,)).fetchone()
            if row[0] >= requests_limit:
                raise BudgetExceeded('requests', row[0], requests_limit)
            if row[1] + amount > token_limit:
                raise BudgetExceeded('tokens', row[1], token_limit, amount)
            # Reserve pessimistically BEFORE transmission, including failed/cancelled calls.
            self.db.execute("UPDATE budget SET requests=requests+1,reserved=reserved+? WHERE day=?", (amount, day))
            self.db.execute('INSERT INTO budget_reservations VALUES(?,?,?,NULL)', (ticket, day, amount))
        return ticket

    def settle_budget(self, ticket, usage):
        """Idempotent actual usage settlement on the reservation's original day.

        Missing/malformed usage, cancellation and ambiguous failures retain the
        reservation. Legacy totals without receipts are intentionally untouched.
        """
        if not isinstance(usage, dict):
            return False
        values = [usage.get(k) for k in ('prompt_tokens', 'completion_tokens', 'total_tokens')]
        if any(type(v) is not int or not 0 <= v <= 100000000 for v in values):
            return False
        prompt, completion, total = values
        if total <= 0 or prompt + completion != total:
            return False
        with self.lock, self.db:
            row = self.db.execute('SELECT day,amount,actual FROM budget_reservations WHERE id=?', (ticket,)).fetchone()
            if row is None or row['actual'] is not None:
                return False
            # Record overruns too: actual usage must never be capped to the estimate.
            self.db.execute('UPDATE budget SET reserved=reserved+? WHERE day=?', (total - row['amount'], row['day']))
            self.db.execute('UPDATE budget_reservations SET actual=? WHERE id=?', (total, ticket))
        return True

    def add_reminder(self, text, due):
        rid = uuid.uuid4().hex
        with self.lock, self.db:
            self.db.execute("INSERT INTO reminders(id,text,due) VALUES(?,?,?)", (rid, text, due))
        return rid

    def due_reminders(self, now):
        with self.lock, self.db:
            rows = self.db.execute("SELECT * FROM reminders WHERE due<=? AND delivered=0", (now,)).fetchall()
            self.db.executemany("UPDATE reminders SET delivered=1 WHERE id=?", [(r["id"],) for r in rows])
        return [dict(r) for r in rows]

    def close(self):
        with self.lock:
            self.db.close()
