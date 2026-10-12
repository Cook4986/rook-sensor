"""Bounded SQLite event+image spool. Enqueue is durable only after commit returns.

Use from a bounded background producer, never synchronously in the camera loop.
Delivery is a separate process; this module has no networking or alert imports.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid

from rook_events import sha256, validate_attachment, validate_event, validate_receipt


class OutboxFull(RuntimeError):
    pass


@dataclass(frozen=True)
class Delivery:
    device_id: str
    event_id: str
    event: dict
    payload: bytes
    image: bytes | None
    token: str
    attempts: int


class Outbox:
    def __init__(self, path, max_items=None, max_bytes=None):
        self.path = Path(path).expanduser().resolve()
        if any(p in ('Dropbox', 'CloudStorage') for p in self.path.parts):
            raise ValueError('Outbox must be on local disk, outside Dropbox')
        requested_items, requested_bytes = max_items, max_bytes
        max_items = 1000 if max_items is None else max_items
        max_bytes = 256 * 1024 * 1024 if max_bytes is None else max_bytes
        if type(max_items) is not int or max_items < 1 or type(max_bytes) is not int or max_bytes < 1:
            raise ValueError('Positive queue limits required')
        self.max_items, self.max_bytes = max_items, max_bytes
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Create private before SQLite sees the file; never weaken existing mode.
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        os.chmod(self.path, 0o600)
        with self._connection() as db:
            db.execute('PRAGMA journal_mode=DELETE')
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if version not in (0, 1):
                raise ValueError('Unsupported outbox schema')
        with self._transaction() as db:
            db.execute('CREATE TABLE IF NOT EXISTS limits ('
                       'singleton INTEGER PRIMARY KEY CHECK(singleton=1), max_items INTEGER, max_bytes INTEGER)')
            db.execute('INSERT OR IGNORE INTO limits VALUES(1,?,?)', (max_items, max_bytes))
            limits = db.execute('SELECT max_items,max_bytes FROM limits WHERE singleton=1').fetchone()
            if ((requested_items is not None and requested_items != limits['max_items'])
                    or (requested_bytes is not None and requested_bytes != limits['max_bytes'])):
                raise ValueError('Queue limits are fixed at creation; do not silently change them')
            self.max_items, self.max_bytes = limits['max_items'], limits['max_bytes']
            db.execute('CREATE TABLE IF NOT EXISTS events ('
                       'device_id TEXT NOT NULL, event_id TEXT NOT NULL, payload BLOB NOT NULL, '
                       'image BLOB, size INTEGER NOT NULL, created_at REAL NOT NULL, '
                       'next_attempt REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, '
                       "state TEXT NOT NULL DEFAULT 'pending', lease_token TEXT, "
                       'lease_until REAL NOT NULL DEFAULT 0, last_error TEXT, '
                       'PRIMARY KEY(device_id,event_id))')
            db.execute('PRAGMA user_version=1')

    @contextmanager
    def _connection(self):
        db = sqlite3.connect(self.path, timeout=0.1, isolation_level=None)
        try:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA synchronous=FULL')
            db.execute('PRAGMA secure_delete=ON')
            # Bound allocation too, not just live payload. DELETE journal may
            # temporarily add up to another database's worth of disk use.
            pages = (self.max_bytes + self.max_items * 8192 + 1048576) // 4096 + 1
            db.execute(f'PRAGMA max_page_count={pages}')
            yield db
        finally:
            db.close()

    @contextmanager
    def _transaction(self):
        with self._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                yield db
                db.execute('COMMIT')
            except BaseException:
                if db.in_transaction:
                    db.execute('ROLLBACK')
                raise

    def enqueue(self, event, image=None, now=None):
        payload = validate_event(event)
        event = json.loads(payload)
        validate_attachment(event, image)
        size = len(payload) + len(image or b'')
        now = time.time() if now is None else now
        with self._transaction() as db:
            previous = db.execute('SELECT payload,image FROM events WHERE device_id=? AND event_id=?',
                                  (event['device_id'], event['event_id'])).fetchone()
            if previous is not None:
                if previous['payload'] != payload or previous['image'] != image:
                    raise ValueError('Event ID reused with different content')
                return False
            count, used = db.execute('SELECT COUNT(*),COALESCE(SUM(size),0) FROM events').fetchone()
            if count >= self.max_items or used + size > self.max_bytes:
                raise OutboxFull('Queue full; existing undelivered events preserved')
            db.execute('INSERT INTO events(device_id,event_id,payload,image,size,created_at,next_attempt) '
                       'VALUES(?,?,?,?,?,?,?)',
                       (event['device_id'], event['event_id'], payload, image, size, now, now))
        return True

    def claim(self, now=None, lease_seconds=60):
        now = time.time() if now is None else now
        if not 1 <= lease_seconds <= 3600:
            raise ValueError('Invalid lease duration')
        with self._transaction() as db:
            row = db.execute("SELECT * FROM events WHERE state='pending' AND next_attempt<=? "
                             'AND lease_until<=? ORDER BY created_at,event_id LIMIT 1', (now, now)).fetchone()
            if row is None:
                return None
            token = str(uuid.uuid4())
            db.execute('UPDATE events SET lease_token=?,lease_until=?,attempts=attempts+1 '
                       'WHERE device_id=? AND event_id=?',
                       (token, now + lease_seconds, row['device_id'], row['event_id']))
            try:
                event = json.loads(row['payload'])
            except (ValueError, TypeError):
                event = {}  # Worker quarantines corrupt payload using row identity.
            return Delivery(row['device_id'], row['event_id'], event, row['payload'],
                            row['image'], token, row['attempts'] + 1)

    def acknowledge(self, delivery, receipt):
        validate_receipt(receipt, delivery.event, sha256(delivery.payload))
        if receipt['device_id'] != delivery.device_id or receipt['event_id'] != delivery.event_id:
            raise ValueError('Receipt does not match queue identity')
        with self._transaction() as db:
            return db.execute('DELETE FROM events WHERE device_id=? AND event_id=? AND lease_token=?',
                              (delivery.device_id, delivery.event_id, delivery.token)).rowcount == 1

    def failed(self, delivery, code, retry_seconds=30, blocked=False, now=None):
        # Store only a small machine code, never URLs/tokens/remote response bodies.
        if not code or len(code) > 64 or not all(c.isalnum() or c == '_' for c in code):
            raise ValueError('Invalid error code')
        if not 1 <= retry_seconds <= 86400:
            raise ValueError('Invalid retry interval')
        now = time.time() if now is None else now
        with self._transaction() as db:
            return db.execute('UPDATE events SET state=?,last_error=?,next_attempt=?,lease_token=NULL,lease_until=0 '
                              'WHERE device_id=? AND event_id=? AND lease_token=?',
                              ('blocked' if blocked else 'pending', code, now + retry_seconds,
                               delivery.device_id, delivery.event_id, delivery.token)).rowcount == 1

    def retry_blocked(self, now=None):
        with self._transaction() as db:
            return db.execute("UPDATE events SET state='pending',next_attempt=?,last_error=NULL "
                              "WHERE state='blocked'", (time.time() if now is None else now,)).rowcount

    def status(self):
        with self._connection() as db:
            row = db.execute('SELECT COUNT(*) AS items,COALESCE(SUM(size),0) AS bytes, '
                             "COALESCE(SUM(state='blocked'),0) AS blocked, MIN(created_at) AS oldest_at "
                             'FROM events').fetchone()
            errors = db.execute('SELECT last_error,COUNT(*) FROM events WHERE last_error IS NOT NULL '
                                'GROUP BY last_error').fetchall()
            return {**dict(row), 'max_items': self.max_items, 'max_bytes': self.max_bytes,
                    'errors': {r[0]: r[1] for r in errors}}
