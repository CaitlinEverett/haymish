"""The Haymish daemon: one persistent localhost server behind the dashboard, the
menu bar, and the MCP server.

Design contract (the safety story, restated for this surface):
  - Bound to 127.0.0.1 only. Every /api/* call requires the per-run token from
    ~/.haymish/serve.json (readable only by this user); the dashboard page gets it
    injected at load. Host header is checked to block DNS-rebinding tricks.
  - Mutations (apply) act only on explicit uuid selections against a preview
    session this daemon built — the browser review grid is where a human makes
    that selection. There is NO delete endpoint: staged deletions are visible
    read-only, and finalizing them stays in `haymish confirm-deletes` where the
    typed confirmation and the macOS system dialog live.

Long work (library load, review builds, ask planning, indexing) runs as jobs on
threads; the API is poll-based (POST returns a job id, GET /api/jobs/<id> reports
progress). Each job opens its own Catalog connection — sqlite cross-connection
locking plus the busy_timeout in Catalog covers concurrent commits.
"""

from __future__ import annotations

import hmac
import http.server
import importlib.resources
import json
import os
import secrets
import threading
import uuid as uuidlib
from dataclasses import dataclass, field
from typing import Any

from . import library
from .catalog import Catalog
from .config import Config, Rule
from .paths import APP_DIR
from .review import ensure_thumbnail, _thumbnail_path
from .sweep import RulePreview, apply_confirmed, preview_sweep

SERVE_STATE_PATH = APP_DIR / "serve.json"
DEFAULT_PORT = 8787
_SESSION_LIMIT = 20  # oldest preview sessions get dropped past this


@dataclass
class Job:
    id: str
    kind: str
    state: str = "running"          # running | done | error
    progress: dict = field(default_factory=dict)
    result: Any = None
    error: str = ""


class ServeState:
    """Everything the request handlers share."""

    def __init__(self, config: Config):
        self.config = config
        self.token = secrets.token_hex(16)
        self.photosdb = None
        self.photosdb_error: str | None = None
        self.photosdb_ready = threading.Event()
        self.jobs: dict[str, Job] = {}
        self.sessions: dict[str, dict] = {}   # id -> {previews, plan, created}
        self.lock = threading.Lock()

    # -- library ---------------------------------------------------------------
    def load_photosdb_async(self):
        def load():
            try:
                self.photosdb = library.load_photosdb(self.config.library)
            except Exception as e:
                self.photosdb_error = f"{type(e).__name__}: {e}"
            finally:
                self.photosdb_ready.set()

        threading.Thread(target=load, daemon=True).start()

    def require_photosdb(self):
        self.photosdb_ready.wait(timeout=600)
        if self.photosdb is None:
            raise RuntimeError(self.photosdb_error or "Photos library not loaded yet")
        return self.photosdb

    # -- jobs ------------------------------------------------------------------
    def start_job(self, kind: str, fn) -> Job:
        job = Job(id=uuidlib.uuid4().hex[:12], kind=kind)
        with self.lock:
            self.jobs[job.id] = job

        def run():
            try:
                job.result = fn(job)
                job.state = "done"
            except Exception as e:
                job.error = f"{type(e).__name__}: {e}"
                job.state = "error"

        threading.Thread(target=run, daemon=True).start()
        return job

    # -- sessions --------------------------------------------------------------
    def put_session(self, previews: list[RulePreview], plan: dict | None = None) -> str:
        session_id = uuidlib.uuid4().hex[:12]
        with self.lock:
            self.sessions[session_id] = {"previews": previews, "plan": plan}
            while len(self.sessions) > _SESSION_LIMIT:
                self.sessions.pop(next(iter(self.sessions)))
        return session_id


def _rule_action_label(rule: Rule) -> str:
    parts = []
    if rule.file:
        if rule.file.get("album"):
            parts.append(f"file → {rule.file['album']}")
        if rule.file.get("keyword"):
            parts.append(f"tag {rule.file['keyword']}")
    if rule.hide:
        parts.append(f"hide after {rule.hide.after_days}d")
    if rule.archive:
        parts.append(f"archive after {rule.archive.after_days}d")
    if rule.delete:
        parts.append(f"stage delete after {rule.delete.after_days}d")
    return " · ".join(parts) or "report only"


def _session_payload(session_id: str, session: dict) -> dict:
    return {
        "session": session_id,
        "plan": session.get("plan"),
        "rules": [
            {
                "name": rp.rule.name,
                "action": _rule_action_label(rp.rule),
                "errors": rp.errors,
                "candidates": [
                    {"uuid": pc.uuid, "filename": pc.filename, "date": pc.date,
                     "detail": pc.classify_detail,
                     "thumb": _thumbnail_path(pc.uuid).is_file()}
                    for pc in rp.preview_candidates
                ],
            }
            for rp in session["previews"]
        ],
    }


def _build_previews_session(state: ServeState, job: Job, rules_override=None,
                             rule_names=None, plan: dict | None = None) -> dict:
    photosdb = state.require_photosdb()
    catalog = Catalog()
    try:
        job.progress = {"phase": "matching"}
        # Keep rules with zero candidates when they have an error to explain why
        # (e.g. semantic rule with no AI index built yet) -- dropping them here
        # would silently turn "couldn't check" back into "nothing to do".
        previews = [rp for rp in preview_sweep(state.config, catalog, photosdb,
                                                rule_names=rule_names,
                                                rules_override=rules_override)
                    if rp.candidates or rp.errors]
        total = sum(len(rp.candidates) for rp in previews)
        done = 0
        for rp in previews:
            for p in rp.candidates:
                ensure_thumbnail(p)
                done += 1
                job.progress = {"phase": "thumbnails", "done": done, "total": total}
    finally:
        catalog.close()
    session_id = state.put_session(previews, plan=plan)
    return _session_payload(session_id, state.sessions[session_id])


class HaymishHandler(http.server.BaseHTTPRequestHandler):
    state: ServeState = None  # set by make_handler

    def log_message(self, fmt, *args):
        pass

    # -- plumbing --------------------------------------------------------------
    def _send(self, status: int, content_type: str, body: bytes):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload, status: int = 200):
        self._send(status, "application/json", json.dumps(payload).encode())

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in {"127.0.0.1", "localhost"}

    def _auth_ok(self) -> bool:
        supplied = self.headers.get("X-Haymish-Token", "")
        return hmac.compare_digest(supplied, self.state.token)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if length > 1_000_000:
            raise ValueError("request too large")
        return json.loads(self.rfile.read(length) or b"{}")

    # -- routing ---------------------------------------------------------------
    def do_GET(self):
        if not self._host_ok():
            self._json({"error": "bad host"}, 403)
            return
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            page = importlib.resources.files("haymish").joinpath(
                "static/dashboard.html").read_text()
            page = page.replace("__HAYMISH_TOKEN__", self.state.token)
            self._send(200, "text/html; charset=utf-8", page.encode())
        elif path == "/api/health":
            self._json({"ok": True, "app": "haymish"})
        elif path.startswith("/thumb/"):
            thumb = _thumbnail_path(path[len("/thumb/"):])
            if thumb.is_file():
                self._send(200, "image/jpeg", thumb.read_bytes())
            else:
                self._send(404, "text/plain", b"not found")
        elif path.startswith("/api/"):
            if not self._auth_ok():
                self._json({"error": "missing or bad token"}, 401)
                return
            self._api_get(path)
        else:
            self._send(404, "text/plain", b"not found")

    def do_POST(self):
        if not self._host_ok():
            self._json({"error": "bad host"}, 403)
            return
        if not self._auth_ok():
            self._json({"error": "missing or bad token"}, 401)
            return
        try:
            body = self._read_body()
        except (ValueError, json.JSONDecodeError):
            self._json({"error": "bad request body"}, 400)
            return
        self._api_post(self.path.split("?")[0], body)

    # -- GET endpoints ---------------------------------------------------------
    def _api_get(self, path: str):
        state = self.state
        if path == "/api/status":
            self._json(self._status_payload())
        elif path.startswith("/api/jobs/"):
            job = state.jobs.get(path.rsplit("/", 1)[1])
            if job is None:
                self._json({"error": "unknown job"}, 404)
                return
            self._json({"id": job.id, "kind": job.kind, "state": job.state,
                        "progress": job.progress, "result": job.result, "error": job.error})
        elif path.startswith("/api/session/"):
            session_id = path.rsplit("/", 1)[1]
            session = state.sessions.get(session_id)
            if session is None:
                self._json({"error": "unknown or expired session"}, 404)
                return
            self._json(_session_payload(session_id, session))
        elif path == "/api/staged-deletes":
            catalog = Catalog()
            try:
                rows = catalog.list_staged_deletes()
                for row in rows:
                    row["backed_up"] = catalog.is_archived_and_verified(row["uuid"])
            finally:
                catalog.close()
            self._json({"staged": rows,
                        "note": "Finalize in a terminal with `haymish confirm-deletes` — "
                                "deletion is never available from this dashboard."})
        else:
            self._json({"error": "not found"}, 404)

    def _status_payload(self) -> dict:
        state = self.state
        catalog = Catalog()
        try:
            overrides = catalog.rule_overrides()
            staged = len(catalog.list_staged_deletes())
            last = catalog.recent_actions(limit=1)
            embedded = catalog.embedded_uuids(state.config.ai_embed_model)
            run_id = catalog.last_run_id()
        finally:
            catalog.close()

        library_stats = {"loaded": state.photosdb is not None,
                         "error": state.photosdb_error}
        index = {"embedded": len(embedded)}
        if state.photosdb is not None:
            photos = library.all_photos(state.photosdb)
            library_stats.update(
                photos=sum(1 for p in photos if not getattr(p, "ismovie", False)),
                videos=sum(1 for p in photos if getattr(p, "ismovie", False)),
            )
            index["total"] = len(photos)
            index["covered"] = sum(1 for p in photos if p.uuid in embedded)

        return {
            "library": library_stats,
            "index": index,
            "staged_deletes": staged,
            "last_run_id": run_id,
            "last_action_at": last[0]["ts"] if last else None,
            "rules": [
                {"name": r.name,
                 "enabled": overrides.get(r.name, r.enabled),
                 "overridden": r.name in overrides,
                 "report_only": r.report_only,
                 "action": _rule_action_label(r)}
                for r in state.config.rules
            ],
            "models": {"embed": state.config.ai_embed_model,
                        "vision": state.config.ai_vision_model,
                        "planner": state.config.ai_planner_model},
        }

    # -- POST endpoints --------------------------------------------------------
    def _api_post(self, path: str, body: dict):
        state = self.state
        if path == "/api/review/build":
            rule_names = body.get("rules") or None
            job = state.start_job("review", lambda j: _build_previews_session(
                state, j, rule_names=rule_names))
            self._json({"job": job.id})

        elif path == "/api/ask":
            request = (body.get("request") or "").strip()
            if not request:
                self._json({"error": "request text required"}, 400)
                return

            def run(job: Job):
                from .ai.planner import plan_from_prompt

                job.progress = {"phase": "planning"}
                photosdb = state.require_photosdb()
                album_names = sorted({a for p in library.all_photos(photosdb)
                                       for a in (p.albums or [])})
                plan = plan_from_prompt(state.config, request,
                                         existing_albums=album_names,
                                         existing_rule_names={r.name for r in state.config.rules})
                plan_info = {"description": plan.description, "rule": plan.raw}
                return _build_previews_session(state, job, rules_override=[plan.rule],
                                                plan=plan_info)

            job = state.start_job("ask", run)
            self._json({"job": job.id})

        elif path == "/api/find":
            query = (body.get("query") or "").strip()
            if not query:
                self._json({"error": "query text required"}, 400)
                return
            top = int(body.get("top") or 24)
            album = (body.get("album") or "").strip() or None

            def run(job: Job):
                from .ai.search import semantic_scores, top_matches

                photosdb = state.require_photosdb()
                catalog = Catalog()
                try:
                    scores = semantic_scores(state.config, catalog, query)
                finally:
                    catalog.close()
                by_uuid = {p.uuid: p for p in library.all_photos(photosdb)}
                matches = [(u, s) for u, s in top_matches(scores, top) if u in by_uuid]
                if album:
                    rule = Rule(name=f"find:{query[:40]}",
                                semantic={"query": query, "min_score": 0.0, "top": top},
                                file={"album": album})
                    return _build_previews_session(state, job, rules_override=[rule])
                catalog = Catalog()
                try:
                    results = []
                    for u, s in matches:
                        p = by_uuid[u]
                        ensure_thumbnail(p)
                        results.append({"uuid": u, "score": round(s, 3),
                                        "filename": p.original_filename,
                                        "date": str(getattr(p, "date", "") or ""),
                                        "caption": (catalog.get_caption(u) or "")[:160]})
                finally:
                    catalog.close()
                return {"matches": results, "indexed": len(scores)}

            job = state.start_job("find", run)
            self._json({"job": job.id})

        elif path == "/api/apply":
            session = state.sessions.get(body.get("session") or "")
            if session is None:
                self._json({"error": "unknown or expired session"}, 404)
                return
            selections = {rule: set(uuids) for rule, uuids in (body.get("selections") or {}).items()}

            def run(job: Job):
                catalog = Catalog()
                try:
                    report = apply_confirmed(state.config, catalog, session["previews"], selections)
                finally:
                    catalog.close()
                return {"run_id": report.run_id,
                        "outcomes": [{"rule": o.rule, "matched": o.matched, "filed": o.filed,
                                       "hidden": o.hidden, "archived": o.archived,
                                       "staged_deletes": o.staged_deletes,
                                       "errors": o.action_errors}
                                      for o in report.outcomes]}

            job = state.start_job("apply", run)
            self._json({"job": job.id})

        elif path == "/api/index/build":
            captions = bool(body.get("captions", True))
            limit = body.get("limit")

            def run(job: Job):
                from .ai.indexer import index_photos

                photosdb = state.require_photosdb()
                catalog = Catalog()
                try:
                    def progress(done, total, phase):
                        job.progress = {"phase": phase, "done": done, "total": total}

                    stats = index_photos(state.config, catalog, library.all_photos(photosdb),
                                          captions=captions,
                                          limit=int(limit) if limit else None,
                                          progress=progress)
                finally:
                    catalog.close()
                return {"embedded": stats.embedded, "captioned": stats.captioned,
                        "already_indexed": stats.already_indexed, "errors": stats.errors}

            job = state.start_job("index", run)
            self._json({"job": job.id})

        elif path == "/api/rules/toggle":
            name = body.get("rule")
            if name not in {r.name for r in state.config.rules}:
                self._json({"error": f"unknown rule {name!r}"}, 404)
                return
            catalog = Catalog()
            try:
                rule = state.config.rule(name)
                enabled = bool(body.get("enabled"))
                if enabled == rule.enabled:
                    catalog.clear_rule_override(name)
                else:
                    catalog.set_rule_override(name, enabled)
            finally:
                catalog.close()
            self._json({"rule": name, "enabled": enabled})

        else:
            self._json({"error": "not found"}, 404)


def write_state_file(port: int, token: str):
    APP_DIR.mkdir(exist_ok=True)
    SERVE_STATE_PATH.write_text(json.dumps({"port": port, "pid": os.getpid(), "token": token}))
    os.chmod(SERVE_STATE_PATH, 0o600)


def read_state_file() -> dict | None:
    try:
        return json.loads(SERVE_STATE_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def serve(config: Config, port: int = DEFAULT_PORT, photosdb=None) -> None:
    """Blocking. Binds 127.0.0.1 only; falls back to an ephemeral port if the
    requested one is taken by something that isn't us. photosdb is injectable
    for tests (skips the real library load, which needs Full Disk Access)."""
    state = ServeState(config)
    if photosdb is not None:
        state.photosdb = photosdb
        state.photosdb_ready.set()
    else:
        state.load_photosdb_async()

    handler = type("BoundHandler", (HaymishHandler,), {"state": state})
    try:
        server = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    except OSError:
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    actual_port = server.server_address[1]
    write_state_file(actual_port, state.token)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        SERVE_STATE_PATH.unlink(missing_ok=True)
        server.shutdown()


def daemon_url() -> str | None:
    """URL of a live daemon, or None. Health-checks the state file's port so a
    stale file from a crashed daemon doesn't get treated as alive."""
    import httpx

    saved = read_state_file()
    if not saved:
        return None
    url = f"http://127.0.0.1:{saved['port']}"
    try:
        r = httpx.get(f"{url}/api/health", timeout=2)
        if r.status_code == 200 and r.json().get("app") == "haymish":
            return url
    except httpx.HTTPError:
        pass
    return None


def ensure_daemon(timeout: float = 120) -> tuple[str, str]:
    """(url, token) of a running daemon, spawning `haymish serve` detached if
    needed. Timeout is generous because first start loads the Photos library."""
    import shutil
    import subprocess
    import sys
    import time

    url = daemon_url()
    if url is None:
        bin_path = shutil.which("haymish")
        cmd = [bin_path, "serve"] if bin_path else [sys.executable, "-m", "haymish.cli", "serve"]
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                          start_new_session=True)
        deadline = time.time() + timeout
        while time.time() < deadline:
            url = daemon_url()
            if url:
                break
            time.sleep(0.5)
        if url is None:
            raise RuntimeError("haymish daemon did not start — try `haymish serve` in a terminal")
    saved = read_state_file()
    return url, saved["token"]
