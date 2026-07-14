"""Local sqlite cache and action ledger at ~/.haymish/catalog.db.

Caching is load-bearing: LLM verdicts, OCR, and perceptual hashes are expensive,
so they're keyed by photo UUID (plus prompt hash for verdicts — editing a rule's
prompt invalidates its cache). Scheduled sweeps only pay for new photos.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import sqlite3
import uuid as uuidlib

from .paths import CATALOG_PATH, ensure_app_dirs

SCHEMA = """
CREATE TABLE IF NOT EXISTS verdicts(
  uuid TEXT NOT NULL, rule TEXT NOT NULL, backend TEXT, prompt_hash TEXT,
  verdict INTEGER, confidence REAL, detail TEXT, computed_at TEXT,
  PRIMARY KEY(uuid, rule)
);
CREATE TABLE IF NOT EXISTS ocr(
  uuid TEXT PRIMARY KEY, source TEXT, text TEXT, computed_at TEXT
);
CREATE TABLE IF NOT EXISTS phash(
  uuid TEXT PRIMARY KEY, hash TEXT, computed_at TEXT
);
CREATE TABLE IF NOT EXISTS archived(
  uuid TEXT PRIMARY KEY, path TEXT, sha256 TEXT, bytes INTEGER,
  archived_at TEXT, verified_at TEXT
);
CREATE TABLE IF NOT EXISTS staged_deletes(
  uuid TEXT PRIMARY KEY, rule TEXT, staged_at TEXT, run_id TEXT
);
CREATE TABLE IF NOT EXISTS actions(
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, ts TEXT, rule TEXT,
  uuid TEXT, action TEXT, detail TEXT, undone INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS runs(
  run_id TEXT PRIMARY KEY, started TEXT, finished TEXT, mode TEXT, stats TEXT
);
"""


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def prompt_hash(backend: str, model: str, prompt: str) -> str:
    return hashlib.sha256(f"{backend}|{model}|{prompt}".encode()).hexdigest()[:16]


class Catalog:
    def __init__(self, path=None):
        ensure_app_dirs()
        self.db = sqlite3.connect(path or CATALOG_PATH)
        self.db.executescript(SCHEMA)

    def close(self):
        self.db.close()

    # -- runs ---------------------------------------------------------------
    def start_run(self, mode: str) -> str:
        run_id = uuidlib.uuid4().hex[:12]
        self.db.execute(
            "INSERT INTO runs(run_id, started, mode) VALUES(?,?,?)", (run_id, _now(), mode)
        )
        self.db.commit()
        return run_id

    def finish_run(self, run_id: str, stats: dict):
        self.db.execute(
            "UPDATE runs SET finished=?, stats=? WHERE run_id=?",
            (_now(), json.dumps(stats), run_id),
        )
        self.db.commit()

    # -- verdict cache ------------------------------------------------------
    def get_verdict(self, uuid: str, rule: str, phash: str):
        row = self.db.execute(
            "SELECT verdict, confidence FROM verdicts WHERE uuid=? AND rule=? AND prompt_hash=?",
            (uuid, rule, phash),
        ).fetchone()
        return None if row is None else (bool(row[0]), row[1])

    def put_verdict(self, uuid: str, rule: str, backend: str, phash: str,
                    verdict: bool, confidence: float, detail: str = ""):
        self.db.execute(
            "INSERT OR REPLACE INTO verdicts VALUES(?,?,?,?,?,?,?,?)",
            (uuid, rule, backend, phash, int(verdict), confidence, detail, _now()),
        )
        self.db.commit()

    # -- action ledger ------------------------------------------------------
    def log_action(self, run_id: str, rule: str, uuid: str, action: str, detail: dict) -> int:
        cur = self.db.execute(
            "INSERT INTO actions(run_id, ts, rule, uuid, action, detail) VALUES(?,?,?,?,?,?)",
            (run_id, _now(), rule, uuid, action, json.dumps(detail)),
        )
        self.db.commit()
        return cur.lastrowid

    def recent_actions(self, run_id: str | None = None, actions: list[str] | None = None,
                        limit: int = 500) -> list[dict]:
        """Most-recent-first, for `undo`. Filter by run_id and/or action type(s)."""
        clauses, params = ["undone = 0"], []
        if run_id:
            clauses.append("run_id = ?")
            params.append(run_id)
        if actions:
            clauses.append(f"action IN ({','.join('?' * len(actions))})")
            params.extend(actions)
        rows = self.db.execute(
            f"SELECT id, run_id, ts, rule, uuid, action, detail FROM actions "
            f"WHERE {' AND '.join(clauses)} ORDER BY id DESC LIMIT ?",
            (*params, limit),
        ).fetchall()
        return [
            {"id": r[0], "run_id": r[1], "ts": r[2], "rule": r[3], "uuid": r[4],
             "action": r[5], "detail": json.loads(r[6])}
            for r in rows
        ]

    def last_run_id(self, mode: str | None = None) -> str | None:
        if mode:
            row = self.db.execute(
                "SELECT run_id FROM runs WHERE mode=? ORDER BY started DESC LIMIT 1", (mode,)
            ).fetchone()
        else:
            row = self.db.execute("SELECT run_id FROM runs ORDER BY started DESC LIMIT 1").fetchone()
        return row[0] if row else None

    def mark_undone(self, action_id: int):
        self.db.execute("UPDATE actions SET undone=1 WHERE id=?", (action_id,))
        self.db.commit()

    # -- archive ledger -------------------------------------------------------
    def record_archive(self, uuid: str, path: str, sha256: str, nbytes: int, verified: bool):
        self.db.execute(
            "INSERT OR REPLACE INTO archived(uuid, path, sha256, bytes, archived_at, verified_at) "
            "VALUES(?,?,?,?,?,?)",
            (uuid, path, sha256, nbytes, _now(), _now() if verified else None),
        )
        self.db.commit()

    def get_archive(self, uuid: str) -> dict | None:
        row = self.db.execute(
            "SELECT path, sha256, bytes, archived_at, verified_at FROM archived WHERE uuid=?", (uuid,)
        ).fetchone()
        if row is None:
            return None
        return {"path": row[0], "sha256": row[1], "bytes": row[2],
                "archived_at": row[3], "verified_at": row[4]}

    def is_archived_and_verified(self, uuid: str) -> bool:
        a = self.get_archive(uuid)
        return a is not None and a["verified_at"] is not None

    # -- staged deletes ---------------------------------------------------------
    def stage_delete(self, uuid: str, rule: str, run_id: str):
        self.db.execute(
            "INSERT OR REPLACE INTO staged_deletes(uuid, rule, staged_at, run_id) VALUES(?,?,?,?)",
            (uuid, rule, _now(), run_id),
        )
        self.db.commit()

    def unstage_delete(self, uuid: str):
        self.db.execute("DELETE FROM staged_deletes WHERE uuid=?", (uuid,))
        self.db.commit()

    def list_staged_deletes(self) -> list[dict]:
        rows = self.db.execute(
            "SELECT uuid, rule, staged_at, run_id FROM staged_deletes ORDER BY staged_at"
        ).fetchall()
        return [{"uuid": r[0], "rule": r[1], "staged_at": r[2], "run_id": r[3]} for r in rows]
