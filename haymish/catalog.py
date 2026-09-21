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

from .incremental import EffectState, ObservationResult, ProcessorState
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
CREATE TABLE IF NOT EXISTS review_rejected(
  uuid TEXT NOT NULL, rule TEXT NOT NULL, rejected_at TEXT,
  PRIMARY KEY(uuid, rule)
);
CREATE TABLE IF NOT EXISTS captions(
  uuid TEXT NOT NULL, caption TEXT, model TEXT NOT NULL, computed_at TEXT,
  PRIMARY KEY(uuid, model)
);
CREATE TABLE IF NOT EXISTS embeddings(
  uuid TEXT NOT NULL, model TEXT NOT NULL, dim INTEGER, vector BLOB, computed_at TEXT,
  PRIMARY KEY(uuid, model)
);
CREATE TABLE IF NOT EXISTS rule_overrides(
  rule TEXT PRIMARY KEY, enabled INTEGER, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS declined_galleries(
  key TEXT PRIMARY KEY, label TEXT, declined_at TEXT
);
CREATE TABLE IF NOT EXISTS gallery_names(
  key TEXT PRIMARY KEY, album TEXT, saved_at TEXT
);
CREATE TABLE IF NOT EXISTS gallery_excluded(
  key TEXT NOT NULL, uuid TEXT NOT NULL, excluded_at TEXT,
  PRIMARY KEY(key, uuid)
);
CREATE TABLE IF NOT EXISTS observed_assets(
  library_id TEXT NOT NULL, uuid TEXT NOT NULL, fingerprint TEXT NOT NULL,
  first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, changed_at TEXT NOT NULL,
  PRIMARY KEY(library_id, uuid)
);
CREATE TABLE IF NOT EXISTS processor_states(
  library_id TEXT NOT NULL, uuid TEXT NOT NULL, processor_id TEXT NOT NULL,
  processor_version TEXT NOT NULL, input_fingerprint TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('running', 'completed', 'failed')),
  error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  PRIMARY KEY(
    library_id, uuid, processor_id, processor_version, input_fingerprint
  )
);
CREATE TABLE IF NOT EXISTS applied_effects(
  library_id TEXT NOT NULL, uuid TEXT NOT NULL,
  disposition_id TEXT NOT NULL, disposition_revision TEXT NOT NULL,
  target_fingerprint TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('pending', 'applied', 'verified', 'failed')),
  error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  PRIMARY KEY(
    library_id, uuid, disposition_id, disposition_revision, target_fingerprint
  )
);
"""


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def prompt_hash(backend: str, model: str, prompt: str) -> str:
    return hashlib.sha256(f"{backend}|{model}|{prompt}".encode()).hexdigest()[:16]


class Catalog:
    def __init__(self, path=None):
        ensure_app_dirs()
        # check_same_thread=False: `haymish review`'s local HTTP server handles its
        # one /apply POST on a server-spawned thread, not whichever thread created
        # this Catalog. The daemon (`haymish serve`) additionally creates a separate
        # Catalog PER JOB THREAD, so cross-thread sharing of one connection stays
        # limited to the review server's single-request pattern. busy_timeout covers
        # the daemon's multi-connection case: writers wait instead of failing with
        # "database is locked" when two jobs commit at once.
        self.db = sqlite3.connect(path or CATALOG_PATH, check_same_thread=False)
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.executescript(SCHEMA)
        self._migrate()

    def _primary_key_columns(self, table: str) -> list[str]:
        columns = self.db.execute(f"PRAGMA table_info({table})").fetchall()
        keyed = sorted((column[5], column[1]) for column in columns if column[5])
        return [name for _, name in keyed]

    def _migrate(self):
        """Run every schema migration independently.

        Do not return from this dispatcher just because one table is current: a
        catalog can be current for captions and still need a later migration.
        """
        self._migrate_captions()
        self._migrate_processor_states()

    def _run_schema_migration(self, statements: str) -> None:
        """Run a table rebuild atomically, including rollback on interruption.

        ``executescript`` commits any pending transaction before it runs and does
        not make the script atomic by itself. The explicit transaction keeps a
        crash or SQL error between DROP and RENAME from losing the source table.
        """
        try:
            self.db.executescript(f"BEGIN IMMEDIATE;\n{statements}\nCOMMIT;")
        except BaseException:
            if self.db.in_transaction:
                self.db.rollback()
            raise

    def _migrate_captions(self) -> None:
        """Preserve model identity for captions created by older versions."""
        pk_cols = self._primary_key_columns("captions")
        if pk_cols == ["uuid", "model"]:
            return
        self._run_schema_migration("""
            DROP TABLE IF EXISTS captions_new;
            CREATE TABLE captions_new(
              uuid TEXT NOT NULL, caption TEXT, model TEXT NOT NULL, computed_at TEXT,
              PRIMARY KEY(uuid, model)
            );
            INSERT OR REPLACE INTO captions_new(uuid, caption, model, computed_at)
              SELECT uuid, caption, COALESCE(NULLIF(model, ''), 'unknown'), computed_at
              FROM captions;
            DROP TABLE captions;
            ALTER TABLE captions_new RENAME TO captions;
        """)

    def _migrate_processor_states(self) -> None:
        """Make processor version and input fingerprint part of durable identity.

        The first incremental schema kept only the latest row for a processor.
        That contradicted the reuse contract: completing v2 destroyed proof that
        the exact v1/input result had already completed. Preserve the legacy row
        while widening the key so switching versions never erases reusable work.
        """
        expected = [
            "library_id", "uuid", "processor_id", "processor_version",
            "input_fingerprint",
        ]
        pk_cols = self._primary_key_columns("processor_states")
        if pk_cols == expected:
            return
        legacy = ["library_id", "uuid", "processor_id"]
        if pk_cols != legacy:
            raise RuntimeError(
                f"unsupported processor_states primary key: {pk_cols!r}"
            )
        self._run_schema_migration("""
            DROP TABLE IF EXISTS processor_states_new;
            CREATE TABLE processor_states_new(
              library_id TEXT NOT NULL, uuid TEXT NOT NULL, processor_id TEXT NOT NULL,
              processor_version TEXT NOT NULL, input_fingerprint TEXT NOT NULL,
              state TEXT NOT NULL CHECK(state IN ('running', 'completed', 'failed')),
              error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
              PRIMARY KEY(
                library_id, uuid, processor_id, processor_version, input_fingerprint
              )
            );
            INSERT INTO processor_states_new(
              library_id, uuid, processor_id, processor_version, input_fingerprint,
              state, error, created_at, updated_at
            )
            SELECT
              library_id, uuid, processor_id, processor_version, input_fingerprint,
              state, error, created_at, updated_at
            FROM processor_states;
            DROP TABLE processor_states;
            ALTER TABLE processor_states_new RENAME TO processor_states;
        """)

    def close(self):
        self.db.close()

    # -- incremental processing --------------------------------------------
    def observe_asset(
        self, library_id: str, uuid: str, fingerprint: str
    ) -> ObservationResult:
        """Record an asset sighting and report how it compares with the last one."""
        row = self.db.execute(
            "SELECT fingerprint FROM observed_assets WHERE library_id=? AND uuid=?",
            (library_id, uuid),
        ).fetchone()
        now = _now()
        if row is None:
            self.db.execute(
                "INSERT INTO observed_assets("
                "library_id, uuid, fingerprint, first_seen_at, last_seen_at, changed_at"
                ") VALUES(?,?,?,?,?,?)",
                (library_id, uuid, fingerprint, now, now, now),
            )
            result = ObservationResult.NEW
        elif row[0] == fingerprint:
            self.db.execute(
                "UPDATE observed_assets SET last_seen_at=? "
                "WHERE library_id=? AND uuid=?",
                (now, library_id, uuid),
            )
            result = ObservationResult.UNCHANGED
        else:
            self.db.execute(
                "UPDATE observed_assets "
                "SET fingerprint=?, last_seen_at=?, changed_at=? "
                "WHERE library_id=? AND uuid=?",
                (fingerprint, now, now, library_id, uuid),
            )
            result = ObservationResult.CHANGED
        self.db.commit()
        return result

    def processor_needs_work(
        self,
        library_id: str,
        uuid: str,
        processor_id: str,
        processor_version: str,
        input_fingerprint: str,
    ) -> bool:
        """Only an exact completed processor result is reusable."""
        row = self.db.execute(
            "SELECT 1 FROM processor_states "
            "WHERE library_id=? AND uuid=? AND processor_id=? "
            "AND processor_version=? AND input_fingerprint=? AND state=?",
            (
                library_id,
                uuid,
                processor_id,
                processor_version,
                input_fingerprint,
                ProcessorState.COMPLETED.value,
            ),
        ).fetchone()
        return row is None

    def record_processor_state(
        self,
        library_id: str,
        uuid: str,
        processor_id: str,
        processor_version: str,
        input_fingerprint: str,
        state: str | ProcessorState,
        error: str | None = None,
    ) -> None:
        """Persist state for one exact processor version and input.

        Completion is terminal for that identity. A caller that truly needs to
        recompute must change the processor version or input fingerprint rather
        than regressing durable completed work back to running after a retry race.
        """
        state = ProcessorState(state)
        if state is not ProcessorState.FAILED:
            error = None
        now = _now()
        self.db.execute(
            "INSERT INTO processor_states("
            "library_id, uuid, processor_id, processor_version, input_fingerprint, "
            "state, error, created_at, updated_at"
            ") VALUES(?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT("
            "library_id, uuid, processor_id, processor_version, input_fingerprint"
            ") DO UPDATE SET "
            "state=excluded.state, error=excluded.error, updated_at=excluded.updated_at "
            "WHERE processor_states.state != 'completed' AND ("
            "processor_states.state IS NOT excluded.state "
            "OR processor_states.error IS NOT excluded.error) ",
            (
                library_id,
                uuid,
                processor_id,
                processor_version,
                input_fingerprint,
                state.value,
                error,
                now,
                now,
            ),
        )
        self.db.commit()

    def effect_is_satisfied(
        self,
        library_id: str,
        uuid: str,
        disposition_id: str,
        disposition_revision: str,
        target_fingerprint: str,
    ) -> bool:
        """Return whether the exact effect target was applied or verified."""
        row = self.db.execute(
            "SELECT 1 FROM applied_effects "
            "WHERE library_id=? AND uuid=? AND disposition_id=? "
            "AND disposition_revision=? AND target_fingerprint=? "
            "AND state IN (?, ?)",
            (
                library_id,
                uuid,
                disposition_id,
                disposition_revision,
                target_fingerprint,
                EffectState.APPLIED.value,
                EffectState.VERIFIED.value,
            ),
        ).fetchone()
        return row is not None

    def record_effect(
        self,
        library_id: str,
        uuid: str,
        disposition_id: str,
        disposition_revision: str,
        target_fingerprint: str,
        state: str | EffectState,
        error: str | None = None,
    ) -> None:
        """Persist an idempotent state transition for an exact effect target."""
        state = EffectState(state)
        now = _now()
        self.db.execute(
            "INSERT INTO applied_effects("
            "library_id, uuid, disposition_id, disposition_revision, "
            "target_fingerprint, state, error, created_at, updated_at"
            ") VALUES(?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT("
            "library_id, uuid, disposition_id, disposition_revision, target_fingerprint"
            ") DO UPDATE SET "
            "state=excluded.state, error=excluded.error, updated_at=excluded.updated_at "
            "WHERE applied_effects.state IS NOT excluded.state "
            "OR applied_effects.error IS NOT excluded.error",
            (
                library_id,
                uuid,
                disposition_id,
                disposition_revision,
                target_fingerprint,
                state.value,
                error,
                now,
                now,
            ),
        )
        self.db.commit()

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
        """Returns (verdict, confidence, detail) or None on cache miss.

        Detail is the classifier's free-text rationale — review UI surfaces it as
        "why this matched". Empty string when the backend didn't provide one.
        """
        row = self.db.execute(
            "SELECT verdict, confidence, detail FROM verdicts "
            "WHERE uuid=? AND rule=? AND prompt_hash=?",
            (uuid, rule, phash),
        ).fetchone()
        return None if row is None else (bool(row[0]), row[1], row[2] or "")

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

    def last_undoable_run_id(self) -> str | None:
        """Most recent run that could have logged album/keyword/hide/stage_delete.

        Both `sweep --apply` and `review` Apply write those actions; scan /
        dry-run / confirm-deletes do not. Picking the newest of the apply modes
        (not "any run") keeps undo from landing on a scan that has nothing to
        reverse.
        """
        row = self.db.execute(
            "SELECT run_id FROM runs WHERE mode IN ('sweep-apply', 'review-apply') "
            "ORDER BY started DESC LIMIT 1"
        ).fetchone()
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

    # -- review queue ---------------------------------------------------------
    def reject_candidate(self, uuid: str, rule: str):
        """Records an explicit 'no, not this one' from a review session so this
        exact (photo, rule) pairing doesn't keep resurfacing in future reviews or
        sweeps -- unlike an unmatched classify verdict, this persists even if the
        rule's query/classify would otherwise keep matching the photo forever."""
        self.db.execute(
            "INSERT OR REPLACE INTO review_rejected(uuid, rule, rejected_at) VALUES(?,?,?)",
            (uuid, rule, _now()),
        )
        self.db.commit()

    def rejected_uuids_for_rule(self, rule: str) -> set[str]:
        rows = self.db.execute(
            "SELECT uuid FROM review_rejected WHERE rule=?", (rule,)
        ).fetchall()
        return {r[0] for r in rows}

    # -- AI index (captions + embeddings) --------------------------------------
    def get_caption(self, uuid: str, model: str | None = None) -> str | None:
        """Caption for this photo. With a model, returns only that model's caption
        (so an upgraded vision model doesn't silently reuse the old one). Without,
        returns the most recent caption from any model -- for display, where a
        stale caption still beats none."""
        if model is not None:
            row = self.db.execute(
                "SELECT caption FROM captions WHERE uuid=? AND model=?", (uuid, model)
            ).fetchone()
        else:
            row = self.db.execute(
                "SELECT caption FROM captions WHERE uuid=? ORDER BY computed_at DESC LIMIT 1",
                (uuid,),
            ).fetchone()
        return row[0] if row else None

    def put_caption(self, uuid: str, caption: str, model: str):
        caption = caption.strip()
        if not caption:
            raise ValueError("refusing to store an empty caption")
        self.db.execute(
            "INSERT OR REPLACE INTO captions VALUES(?,?,?,?)", (uuid, caption, model, _now())
        )
        self.db.commit()

    def captioned_uuids(self, model: str | None = None) -> set[str]:
        if model is not None:
            rows = self.db.execute("SELECT uuid FROM captions WHERE model=?", (model,)).fetchall()
        else:
            rows = self.db.execute("SELECT uuid FROM captions").fetchall()
        return {r[0] for r in rows}

    def caption_models(self) -> dict[str, int]:
        """model -> caption count, so `doctor` can report "1,204 captions from a
        model you no longer use" instead of leaving staleness invisible."""
        rows = self.db.execute(
            "SELECT model, COUNT(*) FROM captions GROUP BY model ORDER BY COUNT(*) DESC"
        ).fetchall()
        return {r[0]: r[1] for r in rows}

    def clear_captions(self, model: str | None = None) -> int:
        """Drop captions (all, or just one model's) so they get regenerated.
        Returns rows removed. Embeddings are left alone -- they're rebuilt from
        captions on the next index pass anyway."""
        if model is not None:
            cur = self.db.execute("DELETE FROM captions WHERE model=?", (model,))
        else:
            cur = self.db.execute("DELETE FROM captions")
        self.db.commit()
        return cur.rowcount

    def put_embedding(self, uuid: str, model: str, vector: bytes, dim: int):
        self.db.execute(
            "INSERT OR REPLACE INTO embeddings VALUES(?,?,?,?,?)",
            (uuid, model, dim, vector, _now()),
        )
        self.db.commit()

    def embedded_uuids(self, model: str) -> set[str]:
        rows = self.db.execute("SELECT uuid FROM embeddings WHERE model=?", (model,)).fetchall()
        return {r[0] for r in rows}

    def all_embeddings(self, model: str) -> list[tuple[str, bytes, int]]:
        return self.db.execute(
            "SELECT uuid, vector, dim FROM embeddings WHERE model=?", (model,)
        ).fetchall()

    # -- rule overrides (dashboard enable/disable toggles) ---------------------
    # Stored here rather than rewritten into rules.toml so user comments and
    # formatting in that file are never touched by the UI.
    def set_rule_override(self, rule: str, enabled: bool):
        self.db.execute(
            "INSERT OR REPLACE INTO rule_overrides VALUES(?,?,?)", (rule, int(enabled), _now())
        )
        self.db.commit()

    def clear_rule_override(self, rule: str):
        self.db.execute("DELETE FROM rule_overrides WHERE rule=?", (rule,))
        self.db.commit()

    def rule_overrides(self) -> dict[str, bool]:
        rows = self.db.execute("SELECT rule, enabled FROM rule_overrides").fetchall()
        return {r[0]: bool(r[1]) for r in rows}

    # -- galleries -------------------------------------------------------------
    # Galleries are recomputed from scratch every run (they're derived from photo
    # timestamps and locations, not stored), so every human judgment about them
    # -- "not a real event", "call it this", "not that photo" -- has to persist
    # here or it's lost the moment you click again.
    def decline_gallery(self, key: str, label: str = ""):
        self.db.execute(
            "INSERT OR REPLACE INTO declined_galleries VALUES(?,?,?)", (key, label, _now())
        )
        self.db.commit()

    def undecline_gallery(self, key: str):
        self.db.execute("DELETE FROM declined_galleries WHERE key=?", (key,))
        self.db.commit()

    def declined_galleries(self) -> dict[str, str]:
        rows = self.db.execute("SELECT key, label FROM declined_galleries").fetchall()
        return {r[0]: r[1] for r in rows}

    def set_gallery_name(self, key: str, album: str):
        """Remember the album name chosen for a gallery, so re-running keeps
        filing into the album the user named rather than minting a new one from
        a regenerated label."""
        self.db.execute(
            "INSERT OR REPLACE INTO gallery_names VALUES(?,?,?)", (key, album, _now())
        )
        self.db.commit()

    def gallery_names(self) -> dict[str, str]:
        rows = self.db.execute("SELECT key, album FROM gallery_names").fetchall()
        return {r[0]: r[1] for r in rows}

    def exclude_from_gallery(self, key: str, uuids: list[str]):
        for uuid in uuids:
            self.db.execute(
                "INSERT OR REPLACE INTO gallery_excluded VALUES(?,?,?)", (key, uuid, _now())
            )
        self.db.commit()

    def gallery_exclusions(self, key: str | None = None) -> dict[str, set[str]]:
        if key is not None:
            rows = self.db.execute(
                "SELECT key, uuid FROM gallery_excluded WHERE key=?", (key,)
            ).fetchall()
        else:
            rows = self.db.execute("SELECT key, uuid FROM gallery_excluded").fetchall()
        out: dict[str, set[str]] = {}
        for k, uuid in rows:
            out.setdefault(k, set()).add(uuid)
        return out
