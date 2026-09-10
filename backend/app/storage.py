import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from threading import Lock

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS sessions (
 id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, intent TEXT NOT NULL,
 browser_hash TEXT UNIQUE NOT NULL, tool_hash TEXT UNIQUE NOT NULL,
 expires_at REAL NOT NULL, conversation_id TEXT UNIQUE, verified INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS inspections (
 id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
 request_id TEXT NOT NULL, intent TEXT NOT NULL, revision INTEGER NOT NULL,
 snapshot TEXT NOT NULL, UNIQUE(session_id, request_id)
);
CREATE TABLE IF NOT EXISTS evidence (
 id TEXT PRIMARY KEY, inspection_id TEXT NOT NULL REFERENCES inspections(id),
 sha256 TEXT NOT NULL, receipt TEXT NOT NULL, media_path TEXT NOT NULL,
 UNIQUE(inspection_id, sha256)
);
CREATE TABLE IF NOT EXISTS assessments (
 id TEXT PRIMARY KEY, evidence_id TEXT UNIQUE NOT NULL REFERENCES evidence(id),
 inspection_id TEXT NOT NULL REFERENCES inspections(id), result TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
 inspection_id TEXT, kind TEXT NOT NULL, created_at TEXT NOT NULL, detail TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS proposals (
 id TEXT PRIMARY KEY, inspection_id TEXT NOT NULL REFERENCES inspections(id),
 request_id TEXT NOT NULL, intent TEXT NOT NULL, payload TEXT NOT NULL,
 status TEXT NOT NULL, expires_at REAL NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(inspection_id, request_id)
);
CREATE TABLE IF NOT EXISTS operations (
 id TEXT PRIMARY KEY, proposal_id TEXT UNIQUE NOT NULL REFERENCES proposals(id),
 inspection_id TEXT NOT NULL REFERENCES inspections(id), action_type TEXT NOT NULL,
 payload_hash TEXT NOT NULL, status TEXT NOT NULL, external_reference TEXT,
 attempts INTEGER NOT NULL DEFAULT 0, last_error_code TEXT,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS conversation_analyses (
 conversation_id TEXT PRIMARY KEY, agent_id TEXT NOT NULL, agent_name TEXT,
 version_id TEXT, branch_id TEXT, status TEXT NOT NULL,
 event_timestamp REAL, duration_seconds REAL,
 transcript TEXT NOT NULL, analysis TEXT NOT NULL, metadata TEXT NOT NULL,
 received_at TEXT NOT NULL
);
"""


class Store:
    def __init__(self, data_dir: Path):
        data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.media_dir = data_dir / "media"
        self.media_dir.mkdir(exist_ok=True, mode=0o700)
        self.path = data_dir / "lumafield.sqlite3"
        # Single process only. SQLite still serializes input commits; vendor I/O never
        # holds a database transaction. G0 serializes assessment requests to avoid
        # duplicate paid calls in one process. Scale-out requires a durable job queue.
        self.assessment_lock = Lock()
        with self.connect() as db:
            db.executescript(SCHEMA)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def transaction(self):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    @staticmethod
    def event(db, session_id, inspection_id, kind, created_at, detail):
        db.execute("INSERT INTO events(session_id,inspection_id,kind,created_at,detail) VALUES(?,?,?,?,?)",
                   (session_id, inspection_id, kind, created_at, json.dumps(detail)))
