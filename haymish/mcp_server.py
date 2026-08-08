"""Haymish MCP server: read-and-propose access to the Photos cleanup daemon.

Safety contract for this surface: the AI proposes; the human disposes — every
mutation goes through the browser review page, and deletion isn't reachable
from this surface at all.

Concretely: every tool here is a thin HTTP client of the local Haymish daemon
(see server.py). The tools can inspect status, search the index, build preview
sessions, and kick off indexing — but there is deliberately no apply, delete,
hide, or rules-toggle tool. A preview session only becomes action when a human
opens the returned review_url in a browser and clicks Apply there; permanent
deletion additionally requires `haymish confirm-deletes` in a terminal, with a
typed confirmation and the macOS system dialog as the final gate.

The `mcp` dependency is optional (`uv sync --extra mcp`); this module imports
it lazily inside create_server() so the rest of haymish never depends on it.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from . import server

# Per-request HTTP timeout (each poll / POST is quick; the long part is the job).
_HTTP_TIMEOUT = 30.0
# Job-completion ceilings, per the daemon's typical workloads.
_JOB_TIMEOUT_INDEX = 600.0   # index builds can caption thousands of photos
_JOB_TIMEOUT_DEFAULT = 300.0  # review builds / ask planning / find
_POLL_INTERVAL = 1.0


def _connect() -> tuple[str, dict[str, str]]:
    """(base_url, auth headers) for a live daemon, spawning it if needed."""
    url, token = server.ensure_daemon()
    return url, {"X-Haymish-Token": token}


def _get(url: str, headers: dict[str, str], path: str) -> dict:
    r = httpx.get(f"{url}{path}", headers=headers, timeout=_HTTP_TIMEOUT)
    r.raise_for_status()
    return r.json()


def _post(url: str, headers: dict[str, str], path: str, body: dict) -> dict:
    r = httpx.post(f"{url}{path}", headers=headers, json=body, timeout=_HTTP_TIMEOUT)
    r.raise_for_status()
    return r.json()


def _wait_for_job(url: str, headers: dict[str, str], job_id: str,
                  timeout: float = _JOB_TIMEOUT_DEFAULT) -> dict:
    """Poll GET /api/jobs/<id> until done/error, or until the ceiling.

    Returns the job's result dict on success. On job error returns
    {"error": ...}; on timeout returns whatever progress info the daemon last
    reported, plus a clear message and the job id so the caller can keep
    polling with haymish_job_status.
    """
    deadline = time.monotonic() + timeout
    job: dict = {}
    while time.monotonic() < deadline:
        job = _get(url, headers, f"/api/jobs/{job_id}")
        state = job.get("state")
        if state == "done":
            return job.get("result") or {}
        if state == "error":
            return {"error": job.get("error") or "job failed", "job": job_id}
        time.sleep(_POLL_INTERVAL)
    return {
        "timed_out": True,
        "job": job_id,
        "progress": job.get("progress") or {},
        "message": (f"Job {job_id} is still running after {int(timeout)}s — "
                    f"it continues in the daemon. Check on it with "
                    f"haymish_job_status('{job_id}')."),
    }


def _review_url(base_url: str, session_id: str | None) -> str | None:
    return f"{base_url}/?session={session_id}" if session_id else None


_HUMAN_GATE_NOTE = (
    "Nothing has been changed. A human must open review_url in their browser, "
    "uncheck any false positives, and click Apply there — this tool surface "
    "cannot apply, hide, archive, or delete anything."
)


def _with_review_url(result: dict, base_url: str) -> dict:
    """Attach review_url + the human-gate note to a session payload (or pass
    through error/timeout results untouched)."""
    if "error" in result or result.get("timed_out"):
        return result
    result["review_url"] = _review_url(base_url, result.get("session"))
    result["note"] = _HUMAN_GATE_NOTE
    return result


def create_server():
    """Build the FastMCP server with all tools registered (lazy mcp import)."""
    try:
        # SDK 1.x FastMCP; renamed to MCPServer (same decorator API) in SDK 2.x.
        try:
            from mcp.server.fastmcp import FastMCP
        except ImportError:
            from mcp.server import MCPServer as FastMCP
    except ImportError as e:
        raise ImportError(
            "The MCP extra is not installed — run `uv sync --extra mcp` "
            "(or `pip install 'haymish[mcp]'`) to use the Haymish MCP server."
        ) from e

    mcp = FastMCP("haymish")

    @mcp.tool()
    def haymish_status() -> dict:
        """Current Haymish daemon status: Photos library stats, AI index
        coverage, staged-delete count, configured rules and models.

        Read-only. Starts the local daemon if it isn't running (which loads
        the Photos library — first call can take a minute or two on large
        libraries). Never modifies the library.
        """
        url, headers = _connect()
        return _get(url, headers, "/api/status")

    @mcp.tool()
    def haymish_find(query: str, top: int = 20) -> dict:
        """Semantic search over the local AI photo index — e.g.
        "whiteboard from the conference" or "receipts from March".

        Read-only: returns matches (uuid, score, filename, date, caption) and
        never modifies the library. Requires the index to have been built
        (haymish_index_refresh or `haymish index`); if nothing is indexed the
        result will be empty.
        """
        url, headers = _connect()
        resp = _post(url, headers, "/api/find", {"query": query, "top": top})
        return _wait_for_job(url, headers, resp["job"])

    @mcp.tool()
    def haymish_review_preview(rules: list[str] | None = None) -> dict:
        """Build a review session previewing what the configured sweep rules
        would do (optionally limited to the named rules), without doing any
        of it.

        Returns per-rule candidate lists plus a review_url. This tool CANNOT
        apply anything: the preview only becomes action when the human opens
        review_url in their browser, unchecks false positives, and clicks
        Apply on that page. Deletion is not reachable even from that page —
        it only ever stages candidates, and finalizing requires the separate
        `haymish confirm-deletes` terminal flow.

        Building thumbnails for many candidates can take a few minutes; on
        timeout the job keeps running and can be polled with
        haymish_job_status.
        """
        url, headers = _connect()
        body: dict = {"rules": rules} if rules else {}
        resp = _post(url, headers, "/api/review/build", body)
        result = _wait_for_job(url, headers, resp["job"])
        return _with_review_url(result, url)

    @mcp.tool()
    def haymish_ask_plan(request: str) -> dict:
        """Turn a plain-language cleanup request (e.g. "file my recipe
        screenshots into Recipes") into a rule via the local planner LLM,
        and preview its matches.

        Returns the plan (description + generated rule), candidate counts per
        rule, and a review_url. This tool CANNOT apply the plan: the human
        must open review_url in their browser and click Apply there. Ask-style
        rules can only file/tag/hide — never archive or delete — and even
        those actions happen only after browser confirmation.
        """
        url, headers = _connect()
        resp = _post(url, headers, "/api/ask", {"request": request})
        result = _wait_for_job(url, headers, resp["job"])
        result = _with_review_url(result, url)
        if "rules" in result:
            result["candidate_counts"] = {
                r["name"]: len(r.get("candidates") or []) for r in result["rules"]
            }
        return result

    @mcp.tool()
    def haymish_index_refresh(captions: bool = True) -> dict:
        """Start (re)building the local AI index — a caption + embedding per
        photo, cached on this Mac. Incremental: only new photos are processed.

        Returns immediately with the job id; indexing continues in the daemon
        (it can take a long time on large libraries — poll with
        haymish_job_status). Set captions=False to skip vision-LLM captions
        and index only Photos' own OCR text and labels (much faster).

        This writes only to Haymish's local cache database; it never modifies
        the Photos library itself.
        """
        url, headers = _connect()
        resp = _post(url, headers, "/api/index/build", {"captions": captions})
        return {
            "job": resp["job"],
            "note": ("Indexing started in the background — check progress with "
                     f"haymish_job_status('{resp['job']}'). Large libraries can "
                     "take a while (ceiling for a single wait is ~10 minutes; "
                     "the job itself has no ceiling)."),
        }

    @mcp.tool()
    def haymish_job_status(job_id: str) -> dict:
        """Status of a daemon job (from haymish_index_refresh, or any tool
        that timed out while waiting): state (running/done/error), progress,
        and the result once done. Read-only.
        """
        url, headers = _connect()
        return _get(url, headers, f"/api/jobs/{job_id}")

    @mcp.tool()
    def haymish_staged_deletes() -> dict:
        """Read-only manifest of photos currently staged for deletion,
        including whether each has a verified backup copy.

        Purely informational: nothing on this surface can stage, unstage, or
        finalize a deletion. Finalizing happens only via `haymish
        confirm-deletes` in a terminal — typed confirmation plus the macOS
        system dialog — and requires verified backups first.
        """
        url, headers = _connect()
        return _get(url, headers, "/api/staged-deletes")

    return mcp


def main() -> None:
    """Run the Haymish MCP server over stdio (wired to `haymish mcp`)."""
    create_server().run(transport="stdio")


if __name__ == "__main__":
    main()
