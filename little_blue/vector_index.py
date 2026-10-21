"""Rebuildable SQLite vector index. Never initializes task or dialogue recovery."""
import hashlib
import math
import sqlite3
import struct

SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_vector_docs(
 memory_id INTEGER NOT NULL, model TEXT NOT NULL, digest TEXT NOT NULL,
 PRIMARY KEY(memory_id, model));
CREATE TABLE IF NOT EXISTS memory_vectors(
 memory_id INTEGER NOT NULL, model TEXT NOT NULL, chunk INTEGER NOT NULL,
 vector BLOB NOT NULL, PRIMARY KEY(memory_id, model, chunk));
CREATE TRIGGER IF NOT EXISTS memory_vectors_delete AFTER DELETE ON memories BEGIN
 DELETE FROM memory_vectors WHERE memory_id=old.id;
 DELETE FROM memory_vector_docs WHERE memory_id=old.id;
END;
CREATE TRIGGER IF NOT EXISTS memory_vectors_update AFTER UPDATE OF text ON memories BEGIN
 DELETE FROM memory_vectors WHERE memory_id=old.id;
 DELETE FROM memory_vector_docs WHERE memory_id=old.id;
END;
"""
CHUNK_VERSION = 'chars320-overlap40-v1'


def chunks(text):
    # Bounded overlapping passages keep the tail of long Chinese memories searchable.
    return [text[i:i + 320] for i in range(0, len(text), 280) if text[i:i + 320].strip()]


def normalized(values, dim):
    values = [float(x) for x in values]
    if len(values) != dim or not all(math.isfinite(x) for x in values):
        raise ValueError('Invalid embedding')
    norm = math.sqrt(sum(x * x for x in values))
    if not math.isfinite(norm) or norm < 1e-12:
        raise ValueError('Empty embedding')
    return [x / norm for x in values]


class VectorIndex:
    def __init__(self, path, encoder, model, dim=512):
        self.db = sqlite3.connect(path, timeout=2)
        self.db.row_factory = sqlite3.Row
        self.encoder, self.model, self.dim = encoder, model, dim
        self.db.executescript(SCHEMA)

    def status(self):
        total = self.db.execute('SELECT count(*) FROM memories').fetchone()[0]
        indexed = self.db.execute('SELECT count(*) FROM memory_vector_docs d JOIN memories m '
                                  'ON m.id=d.memory_id WHERE d.model=?', (self.model,)).fetchone()[0]
        return {'total': total, 'indexed': indexed, 'pending': max(0, total - indexed)}

    def backfill(self, batch=4):
        rows = self.db.execute('SELECT m.id,m.text FROM memories m LEFT JOIN memory_vector_docs d '
            'ON d.memory_id=m.id AND d.model=? WHERE d.memory_id IS NULL ORDER BY m.id LIMIT ?',
            (self.model, min(8, max(1, batch)))).fetchall()
        for row in rows:
            passages = chunks(row['text']) or [' ']
            vectors = list(self.encoder.embed(passages, batch_size=4))
            if len(vectors) != len(passages):
                raise ValueError('Incomplete embeddings')
            packed = [struct.pack('<' + 'f' * self.dim, *normalized(v, self.dim)) for v in vectors]
            with self.db:
                # The source can be edited/deleted while inference is in progress.
                self.db.execute('BEGIN IMMEDIATE')
                current = self.db.execute('SELECT text FROM memories WHERE id=?', (row['id'],)).fetchone()
                if not current or current['text'] != row['text']:
                    continue
                self.db.execute('DELETE FROM memory_vectors WHERE memory_id=? AND model=?', (row['id'], self.model))
                self.db.executemany('INSERT INTO memory_vectors VALUES(?,?,?,?)',
                    [(row['id'], self.model, n, v) for n, v in enumerate(packed)])
                self.db.execute('INSERT OR REPLACE INTO memory_vector_docs VALUES(?,?,?)',
                    (row['id'], self.model, hashlib.sha256(row['text'].encode()).hexdigest()))
        return self.status()

    def search(self, query, limit=20):
        if not query.strip():
            return []
        q = normalized(next(iter(self.encoder.query_embed(query[:2000]))), self.dim)
        # Exact cosine scan is bounded in memory and adequate for a personal archive.
        # It runs only in the killable worker process, not the life/Qt event loops.
        scores = {}
        cursor = self.db.execute('SELECT v.memory_id,v.vector FROM memory_vectors v '
            'JOIN memory_vector_docs d ON d.memory_id=v.memory_id AND d.model=v.model '
            'JOIN memories m ON m.id=v.memory_id WHERE v.model=?', (self.model,))
        for row in cursor:
            try:
                v = struct.unpack('<' + 'f' * self.dim, row['vector'])
                if not all(math.isfinite(x) for x in v):
                    continue
                score = sum(a * b for a, b in zip(q, v))
            except (struct.error, TypeError):
                continue
            if score >= .5:
                scores[row['memory_id']] = max(scores.get(row['memory_id'], -1), score)
        return [{'id': ident, 'similarity': round(score, 6)} for ident, score in
                sorted(scores.items(), key=lambda x: (-x[1], -x[0]))[:min(60, max(1, limit))]]

    def close(self):
        self.db.close()


def fuse(lexical, semantic, limit=8):
    """Reciprocal rank fusion; preserve provenance, never treat similarity as fact."""
    scores, rows, channels = {}, {}, {}
    for name, candidates in [('keyword', lexical), ('vector', semantic)]:
        seen = set()
        for rank, row in enumerate(candidates, 1):
            ident = row['id']
            if ident in seen:
                continue
            seen.add(ident)
            rows[ident] = row
            scores[ident] = scores.get(ident, 0) + 1 / (60 + rank)
            channels.setdefault(ident, []).append(name)
    return [{**rows[i], 'retrieval': {'via': channels[i], 'score': round(scores[i], 6)}}
            for i in sorted(scores, key=lambda i: (-scores[i], -i))[:limit]]
