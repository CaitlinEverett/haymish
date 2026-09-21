"""The Haymish daemon: one persistent localhost server behind the dashboard, the
menu bar, and the MCP server.

Design contract (the safety story, restated for this surface):
  - Bound to 127.0.0.1 only. Every /api/* call requires the per-run token from
    ~/.haymish/serve.json (readable only by this user); the dashboard page gets it
    injected at load. Host header is checked to block DNS-rebinding tricks.
  - Mutations (apply) act only on explicit uuid selections against a preview
    session this daemon built — the browser review grid is where a human makes
    that selection. There is NO delete endpoint: staged candidates are visible
    read-only. Final deletion remains disabled until complete archive manifests
    exist; its eventual CLI path also requires typed and macOS confirmation.

Long work (library load, review builds, ask planning, indexing) runs as jobs on
threads; the API is poll-based (POST returns a job id, GET /api/jobs/<id> reports
progress). Each job opens its own Catalog connection — sqlite cross-connection
locking plus the busy_timeout in Catalog covers concurrent commits.
"""

from __future__ import annotations

import hmac
import html
import http.server
import importlib.resources
import json
import os
import secrets
import signal
import threading
import time
import uuid as uuidlib
from dataclasses import dataclass, field
from typing import Any

from . import library
from .catalog import Catalog
from .config import Config, Rule
from .domain import collection_from_rule
from .paths import APP_DIR
from .review import ensure_thumbnail, _thumbnail_path
from .sweep import HidePreviewMeta, RulePreview, apply_confirmed, preview_sweep

SERVE_STATE_PATH = APP_DIR / "serve.json"
DEFAULT_PORT = 8787
_SESSION_LIMIT = 20  # oldest preview sessions get dropped past this

TOKEN_PLACEHOLDER = "__HAYMISH_TOKEN__"

# Every static asset this daemon will serve, keyed by the exact request path.
# This is an allowlist, not a document root: there is no path joining, no
# normalization, and no directory walk, so `/static/dashboard/../../../etc/passwd`
# resolves to nothing and 404s like any other unknown path. Adding a dashboard
# asset means adding a line here.
_STATIC_ASSETS: dict[str, tuple[str, str]] = {
    "/static/dashboard/styles.css": ("static/dashboard/styles.css", "text/css; charset=utf-8"),
    "/static/dashboard/app.js": ("static/dashboard/app.js", "text/javascript; charset=utf-8"),
    "/static/dashboard/components/collections.js": (
        "static/dashboard/components/collections.js", "text/javascript; charset=utf-8"),
}


def static_asset(path: str) -> tuple[bytes, str] | None:
    """(body, content type) for an allowlisted asset, or None if not allowlisted.

    Read from package resources so it works the same from a source checkout and
    an installed wheel.
    """
    entry = _STATIC_ASSETS.get(path)
    if entry is None:
        return None
    resource, content_type = entry
    body = importlib.resources.files("haymish").joinpath(resource).read_bytes()
    return body, content_type


def dashboard_html(token: str) -> str:
    """The dashboard page with the per-run token injected into its meta element.

    app.js is a static packaged file now, so text substitution into the script
    is no longer possible -- the token rides in the markup and the module reads
    it from there. It is escaped as an HTML attribute value even though
    `secrets.token_hex` can only produce hex: a page that is safe only because
    of the token's alphabet is one token-format change away from injection.
    """
    page = importlib.resources.files("haymish").joinpath(
        "static/dashboard.html").read_text(encoding="utf-8")
    return page.replace(TOKEN_PLACEHOLDER, html.escape(token, quote=True))


def _explain_library_error(error: Exception, library_path) -> str:
    """Turn a library-load failure into something a person can act on.

    The common case is a permission error: the daemon can read its own config
    but not the Photos database. That surfaces from osxphotos as a bare
    'Operation not permitted' / 'Error copying ... Photos.sqlite', which tells
    the user nothing. Note the daemon can lack Full Disk Access even when the
    terminal that launched it has it -- it runs in its own session, so macOS
    may attribute it separately.
    """
    text = str(error)
    permission_denied = (
        isinstance(error, PermissionError)
        or "Operation not permitted" in text
        or "Error copying" in text
    )
    if permission_denied:
        return (
            "Full Disk Access is missing for the Haymish daemon, so it can't read "
            "the Photos database. Fix: System Settings → Privacy & Security → Full "
            "Disk Access → enable your terminal app, then quit that app completely "
            "(⌘Q, not just the window) and run `haymish serve` again from it. "
            "Running `haymish serve` in the foreground of an already-authorized "
            "terminal is the most reliable way to get the daemon authorized."
        )
    if isinstance(error, FileNotFoundError) or "no such file" in text.lower():
        return (f"No Photos library at {library_path} — set [global].library in "
                f"~/.haymish/rules.toml if it lives somewhere else.")
    return f"{type(error).__name__}: {error}"


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
                self.photosdb_error = _explain_library_error(e, self.config.library)
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


def _rule_description(rule: Rule) -> str:
    return f"From the {rule.pack} rule pack." if rule.pack else ""


def _stale_caption_count(config: Config, caption_models: dict[str, int]) -> int:
    """Count captions not keyed to the configured model and prompt version."""
    from .ai.indexer import caption_key

    current = caption_key(config)
    return sum(count for key, count in caption_models.items() if key != current)


def collections_payload(config: Config, overrides: dict[str, bool] | None = None) -> list[dict]:
    """Each legacy rule compiled through the domain model, as JSON-safe entries.

    Pure: it reads the config and the catalog's enable/disable overrides that
    the caller already fetched, and touches neither the catalog nor Photos. The
    compiled values are the canonical `to_dict()` forms, so a client sees the
    same shape the revisions were computed over.

    A rule the domain model cannot express yet (one with no lens evidence, say)
    carries its own `error` instead of taking the whole list down or quietly
    disappearing from it: a visible gap is honest, a shorter list is not.
    """
    overrides = overrides or {}
    entries: list[dict] = []
    for rule in config.rules:
        description = _rule_description(rule)
        entry: dict[str, Any] = {
            "id": rule.name,
            "name": rule.name,
            "description": description,
            "source": "legacy-rule",
            "enabled": bool(overrides.get(rule.name, rule.enabled)),
            "report_only": bool(rule.report_only),
            "collection_revision": None,
            "lens_revision": None,
            "disposition_revision": None,
            "lens": None,
            "disposition": None,
            "error": None,
        }
        try:
            collection = collection_from_rule(rule, description=description)
            compiled = {
                "id": collection.id,
                "name": collection.name,
                "description": collection.description,
                "collection_revision": collection.revision,
                "lens_revision": collection.lens.revision,
                "disposition_revision": collection.disposition.revision,
                "lens": collection.lens.to_dict(),
                "disposition": collection.disposition.to_dict(),
            }
        except Exception as error:
            entry["error"] = f"{type(error).__name__}: {error}"
        else:
            entry.update(compiled)
        entries.append(entry)
    return entries


def _subgroups_for(state: "ServeState", previews) -> dict[str, list[dict]]:
    """rule name -> labelled sub-groups, for rules matching enough photos that a
    flat grid stops being reviewable. Best-effort: any failure just means the
    UI falls back to the flat list, which still works."""
    from .subgroup import subgroup_photos

    out: dict[str, list[dict]] = {}
    catalog = Catalog()
    try:
        for rp in previews:
            if len(rp.candidates) < 24:      # small queues read fine flat
                continue
            try:
                groups = subgroup_photos(state.config, catalog, rp.candidates)
            except Exception:
                continue
            if len(groups) > 1:
                out[rp.rule.name] = [
                    {"key": g.key, "label": g.label, "size": g.size, "uuids": g.uuids}
                    for g in groups
                ]
    finally:
        catalog.close()
    return out


def _hide_preview_json(meta: HidePreviewMeta | None) -> dict | None:
    if meta is None:
        return None
    return {
        "due": meta.due,
        "hideable": meta.hideable,
        "skipped_icloud": meta.skipped_icloud,
        "already_hidden": meta.already_hidden,
    }


def _review_empty_state(previews) -> str | None:
    """Why the review grid has no checkboxes — distinct messages in the dashboard."""
    if any(rp.candidates for rp in previews):
        return None
    if any(rp.errors for rp in previews):
        return "errors"
    return "zero_matches"


def _session_payload(session_id: str, session: dict) -> dict:
    subgroups = session.get("subgroups") or {}
    payload = {
        "session": session_id,
        "plan": session.get("plan"),
        "empty_state": session.get("empty_state"),
        "rules": [
            {
                "name": rp.rule.name,
                "action": _rule_action_label(rp.rule),
                "errors": rp.errors,
                "hide": _hide_preview_json(rp.hide_preview),
                # Present only for big, heterogeneous queues. A screenshots rule
                # can match thousands of unrelated things -- FaceTime stills, web
                # pages, receipts, photos of people -- and no single answer to
                # "apply this?" is right for all of them. Groups let one decision
                # cover hundreds.
                "subgroups": subgroups.get(rp.rule.name, []),
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
    return payload


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

    job.progress = {"phase": "grouping"}
    subgroups = _subgroups_for(state, previews)

    empty_state = _review_empty_state(previews)
    if empty_state == "zero_matches":
        probe_catalog = Catalog()
        try:
            probe = [rp for rp in preview_sweep(state.config, probe_catalog, photosdb,
                                                 rule_names=rule_names,
                                                 rules_override=rules_override,
                                                 honor_rejects=False)
                     if rp.candidates or rp.errors]
        finally:
            probe_catalog.close()
        if any(rp.candidates for rp in probe):
            empty_state = "all_rejected"

    session_id = state.put_session(previews, plan=plan)
    state.sessions[session_id]["subgroups"] = subgroups
    state.sessions[session_id]["empty_state"] = empty_state
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
            self._send(200, "text/html; charset=utf-8",
                       dashboard_html(self.state.token).encode())
        elif path.startswith("/static/"):
            asset = static_asset(path)
            if asset is None:
                self._send(404, "text/plain", b"not found")
                return
            body, content_type = asset
            self._send(200, content_type, body)
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
        elif path == "/api/collections":
            # Read-only and library-free: the compile is pure, so the only
            # runtime state needed is the enable/disable overrides.
            catalog = Catalog()
            try:
                overrides = catalog.rule_overrides()
            finally:
                catalog.close()
            self._json({"collections": collections_payload(state.config, overrides)})
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
                by_uuid = None
                if state.photosdb is not None:
                    by_uuid = {p.uuid: p for p in library.all_photos(state.photosdb)}
                for row in rows:
                    row["backed_up"] = catalog.is_archived_and_verified(row["uuid"])
                    if by_uuid is not None:
                        photo = by_uuid.get(row["uuid"])
                        if photo is not None:
                            row["filename"] = getattr(photo, "original_filename", None) or row["uuid"]
            finally:
                catalog.close()
            self._json({"staged": rows,
                        "note": "Final deletion is disabled until complete asset-component "
                                "backup manifests are implemented and re-verifiable."})
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
            caption_models = catalog.caption_models()
            from .ai.indexer import caption_key

            captioned = len(catalog.captioned_uuids(caption_key(state.config)))
        finally:
            catalog.close()

        library_stats = {"loaded": state.photosdb is not None,
                         "error": state.photosdb_error}
        # Caption identity includes the prompt version as well as the vision
        # model. Comparing against the raw model name marks every current caption
        # stale because catalog keys look like "model+p2".
        index = {
            "embedded": len(embedded),
            "captioned": captioned,
            "caption_models": caption_models,
            "stale_captions": _stale_caption_count(state.config, caption_models),
        }
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
                    def progress(done, total, rule_name):
                        job.progress = {
                            "phase": "applying",
                            "done": done,
                            "total": total,
                            "rule": rule_name,
                        }

                    report = apply_confirmed(
                        state.config, catalog, session["previews"], selections,
                        progress=progress,
                    )
                finally:
                    catalog.close()
                return {"run_id": report.run_id,
                        "outcomes": [{"rule": o.rule, "matched": o.matched, "rejected": o.rejected,
                                       "filed": o.filed, "hidden": o.hidden, "archived": o.archived,
                                       "staged_deletes": o.staged_deletes,
                                       "errors": o.action_errors}
                                      for o in report.outcomes]}

            job = state.start_job("apply", run)
            self._json({"job": job.id})

        elif path == "/api/galleries":
            min_photos = int(body.get("min_photos") or 40)
            gap_hours = float(body.get("gap_hours") or 14.0)
            max_km = float(body.get("max_km") or 60.0)
            limit = int(body.get("limit") or 40)
            query = (body.get("query") or "").strip() or None

            def run(job: Job):
                from .events import cluster_events, pick_representative

                photosdb = state.require_photosdb()
                photos = library.all_photos(photosdb)
                by_uuid = {p.uuid: p for p in photos}
                job.progress = {"phase": "clustering"}
                events = cluster_events(photos, max_gap_hours=gap_hours,
                                         max_km=max_km, min_photos=min_photos)

                # Apply remembered human judgments: galleries you rejected stay
                # gone, names you chose stick, photos you pulled out stay out.
                catalog = Catalog()
                try:
                    declined = catalog.declined_galleries()
                    chosen_names = catalog.gallery_names()
                    exclusions = catalog.gallery_exclusions()
                finally:
                    catalog.close()

                events = [e for e in events if e.key not in declined]
                for e in events:
                    dropped = exclusions.get(e.key)
                    if dropped:
                        e.uuids = [u for u in e.uuids if u not in dropped]
                        e.photo_count = len(e.uuids)
                events = [e for e in events if e.photo_count]

                # -- semantic ranking (optional) --------------------------------
                query_meta = None
                if query:
                    from .ai.ollama_client import AIError
                    from .ai.search import gallery_scores, semantic_scores

                    catalog_q = Catalog()
                    try:
                        scores = semantic_scores(state.config, catalog_q, query)
                    except AIError as e:
                        # Degrade gracefully: fall back to significance ranking
                        # and surface the error.
                        query_meta = {"query": query, "error": str(e)}
                        scores = {}
                    finally:
                        catalog_q.close()

                    if scores:
                        ranked = gallery_scores(scores, events)
                        events = [ev for ev, _s, _i, _t in ranked]
                        query_meta = {
                            "query": query,
                            "scores": {
                                ev.key: round(score, 3)
                                for ev, score, _i, _t in ranked
                            },
                            "coverage": {
                                ev.key: {"indexed": idx, "total": tot}
                                for ev, _s, idx, tot in ranked
                            },
                        }
                    elif not query_meta:
                        query_meta = {
                            "query": query,
                            "error": "No photos indexed yet — run `haymish index` first.",
                        }
                else:
                    events.sort(key=lambda e: e.significance, reverse=True)
                events = events[:limit]

                # Only the cover thumbnail is generated up front. Member
                # thumbnails are made on demand when a gallery is expanded --
                # rendering 11,000 of them to draw 40 covers would be absurd.
                out = []
                score_map = (query_meta or {}).get("scores", {})
                for i, e in enumerate(events):
                    members = [by_uuid[u] for u in e.uuids if u in by_uuid]
                    cover = pick_representative(members)
                    if cover is not None:
                        ensure_thumbnail(cover)
                    entry = {
                        "key": e.key, "label": e.label, "place": e.place,
                        "photo_count": e.photo_count, "days": e.days,
                        "start": e.start.isoformat(), "end": e.end.isoformat(),
                        "cover": cover.uuid if cover is not None else None,
                        "uuids": e.uuids,
                        # The name last chosen for this gallery, so the field is
                        # pre-filled with the user's wording rather than the
                        # regenerated label.
                        "album": chosen_names.get(e.key, ""),
                        "excluded": sorted(exclusions.get(e.key, ())),
                    }
                    if e.key in score_map:
                        entry["score"] = score_map[e.key]
                    out.append(entry)
                    job.progress = {"phase": "covers", "done": i + 1, "total": len(events)}
                result = {"events": out, "total_events": len(events)}
                if query_meta:
                    result["query"] = query_meta
                return result

            job = state.start_job("galleries", run)
            self._json({"job": job.id})

        elif path == "/api/galleries/decline":
            # "Don't suggest this again." Galleries are recomputed every run, so
            # without persisting this the same rejected grouping returns forever.
            key = body.get("key")
            if not key:
                self._json({"error": "key required"}, 400)
                return
            catalog = Catalog()
            try:
                if body.get("undo"):
                    catalog.undecline_gallery(key)
                else:
                    catalog.decline_gallery(key, body.get("label", ""))
            finally:
                catalog.close()
            self._json({"key": key, "declined": not body.get("undo")})

        elif path == "/api/galleries/create":
            # Explicit and literal: the client sends exactly which galleries,
            # under exactly which names, containing exactly which photos. No
            # inference here -- the human already made every one of those calls
            # in the UI, and this endpoint's job is to carry them out faithfully.
            wanted = body.get("galleries") or []
            if not wanted:
                self._json({"error": "no galleries selected"}, 400)
                return

            def run(job: Job):
                from .actions import albums as album_action

                catalog = Catalog()
                run_id = catalog.start_run("galleries-create")
                results = []
                try:
                    for i, g in enumerate(wanted):
                        key = g.get("key") or ""
                        album = (g.get("album") or "").strip()
                        uuids = list(g.get("uuids") or [])
                        if not album or not uuids:
                            results.append({"key": key, "album": album, "filed": 0,
                                             "error": "needs an album name and photos"})
                            continue

                        # Anything the user removed from the gallery is recorded,
                        # so it stays out when this gallery is computed again.
                        dropped = [u for u in (g.get("excluded") or []) if u]
                        if dropped:
                            catalog.exclude_from_gallery(key, dropped)
                        catalog.set_gallery_name(key, album)

                        n, failed = album_action.add_to_album(uuids, album)
                        for u in (u for u in uuids if u not in failed):
                            catalog.log_action(run_id, f"gallery:{key}", u,
                                                "album", {"album": album})
                        results.append({"key": key, "album": album, "filed": n,
                                         "failed": len(failed)})
                        job.progress = {"phase": "filing", "done": i + 1,
                                        "total": len(wanted)}
                    catalog.finish_run(run_id, {"galleries": len(wanted)})
                finally:
                    catalog.close()
                return {"run_id": run_id, "results": results}

            job = state.start_job("galleries-create", run)
            self._json({"job": job.id})

        elif path == "/api/gallery/thumbs":
            # Thumbnails for one expanded gallery, generated on demand.
            uuids = list(body.get("uuids") or [])[:400]

            def run(job: Job):
                photosdb = state.require_photosdb()
                by_uuid = {p.uuid: p for p in library.all_photos(photosdb)}
                ready = []
                for i, uuid in enumerate(uuids):
                    photo = by_uuid.get(uuid)
                    if photo is not None:
                        ensure_thumbnail(photo)
                        ready.append({
                            "uuid": uuid,
                            "filename": getattr(photo, "original_filename", "") or uuid,
                            "thumb": _thumbnail_path(uuid).is_file(),
                        })
                    job.progress = {"phase": "thumbnails", "done": i + 1, "total": len(uuids)}
                return {"photos": ready}

            job = state.start_job("gallery-thumbs", run)
            self._json({"job": job.id})

        elif path == "/api/index/build":
            catch_up = bool(body.get("catch_up_captions"))
            captions = False if body.get("no_captions") else bool(body.get("captions", True))
            if catch_up:
                captions = True
            limit = body.get("limit")
            concurrency = body.get("concurrency")

            def run(job: Job):
                from .ai.indexer import index_photos

                photosdb = state.require_photosdb()
                catalog = Catalog()
                try:
                    def progress(done, total, phase):
                        job.progress = {"phase": phase, "done": done, "total": total}

                    stats = index_photos(state.config, catalog, library.all_photos(photosdb),
                                          captions=captions,
                                          catch_up_captions=catch_up,
                                          limit=int(limit) if limit else None,
                                          progress=progress,
                                          concurrency=int(concurrency) if concurrency else None)
                finally:
                    catalog.close()
                return {"embedded": stats.embedded, "captioned": stats.captioned,
                        "already_indexed": stats.already_indexed,
                        "caption_workers": stats.caption_workers, "errors": stats.errors}

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


class DaemonAlreadyRunning(RuntimeError):
    """Another Haymish daemon holds the port. Carries its url and pid."""

    def __init__(self, url: str, pid: int | None):
        self.url, self.pid = url, pid
        super().__init__(f"a Haymish daemon is already running at {url}")


def serve(config: Config, port: int = DEFAULT_PORT, photosdb=None,
          replace: bool = False) -> None:
    """Blocking. Binds 127.0.0.1 only. photosdb is injectable for tests (skips
    the real library load, which needs Full Disk Access).

    Refuses to start a second daemon rather than silently taking an ephemeral
    port. That fallback used to strand people: an old daemon kept :8787, the new
    one landed on a random port, and the browser at the address they knew showed
    a stale build missing today's endpoints -- which reads as "the feature is
    broken", not "you're looking at the wrong process". With replace=True the
    running one is stopped first.
    """
    existing = daemon_url()
    if existing:
        saved = read_state_file() or {}
        pid = saved.get("pid")
        if not replace:
            raise DaemonAlreadyRunning(existing, pid)
        if pid:
            try:
                os.kill(pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
            for _ in range(40):          # give it a moment to release the port
                if daemon_url() is None:
                    break
                time.sleep(0.1)

    state = ServeState(config)
    if photosdb is not None:
        state.photosdb = photosdb
        state.photosdb_ready.set()
    else:
        state.load_photosdb_async()

    handler = type("BoundHandler", (HaymishHandler,), {"state": state})
    try:
        server = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    except OSError as e:
        # Something that isn't us holds the port -- say which, don't drift to a
        # random one the user will never think to open.
        raise RuntimeError(
            f"port {port} is in use by another program (not Haymish): {e}. "
            f"Free it, or run `haymish serve --port <other>`."
        ) from None
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
