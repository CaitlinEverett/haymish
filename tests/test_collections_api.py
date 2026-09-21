"""The read-only Collections slice: the pure legacy-rule compile and the
/api/collections endpoint that serves it.

Nothing here opens a socket, reads the user's catalog, or loads a Photos
library. The compile is a pure function over a config; the endpoint test drives
the real handler with only its socket plumbing faked, a catalog stub that
records what was asked of it, and a library module that raises if the endpoint
ever reaches for photos.
"""

from __future__ import annotations

import email.message
import io
import json
from datetime import date
from pathlib import Path

import pytest

from haymish import server
from haymish.config import Config, Rule, StageConfig
from haymish.domain import collection_from_rule


def make_config(*rules: Rule) -> Config:
    """A config pointed at paths that don't exist, so accidental I/O fails loudly."""
    return Config(
        library=Path("/nonexistent/Test.photoslibrary"), backup=None,
        report_dir=Path("/nonexistent/reports"), ollama_host="", ollama_model="",
        claude_model="", ai_embed_model="", ai_vision_model="",
        ai_planner_backend="ollama", ai_planner_model="", rules=list(rules),
        source_path=Path("/nonexistent/rules.toml"),
    )


def receipts_rule(**overrides) -> Rule:
    fields = dict(
        name="receipt-candidates",
        query={"keywords": ["receipt"], "screenshot": True},
        detector="receipts",
        semantic={"query": "a receipt", "min_score": 0.4},
        classify={"backend": "ollama", "prompt": "Is this a receipt?"},
        file={"keyword": "expense:receipt", "album": "Expenses/Receipts"},
        hide=StageConfig(after_days=7),
        archive=StageConfig(after_days=30),
        delete=StageConfig(after_days=90),
    )
    fields.update(overrides)
    return Rule(**fields)


def entries_by_name(entries: list[dict]) -> dict[str, dict]:
    return {entry["name"]: entry for entry in entries}


# -- the pure compile ---------------------------------------------------------

def test_legacy_rule_compiles_to_the_same_collection_the_domain_would_build():
    rule = receipts_rule(report_only=True)
    expected = collection_from_rule(rule)

    entry, = server.collections_payload(make_config(rule))

    assert entry["id"] == "receipt-candidates"
    assert entry["name"] == "receipt-candidates"
    assert entry["source"] == "legacy-rule"
    assert entry["error"] is None
    assert entry["report_only"] is True
    assert entry["enabled"] is True

    # The revisions must be the domain's, not a re-derivation that could drift.
    assert entry["collection_revision"] == expected.revision
    assert entry["lens_revision"] == expected.lens.revision
    assert entry["disposition_revision"] == expected.disposition.revision

    # And the payload carries the canonical forms those revisions were taken
    # over, so a client is looking at the same values the hash covers.
    assert entry["lens"] == expected.lens.to_dict()
    assert entry["disposition"] == expected.disposition.to_dict()
    assert entry["disposition"]["keywords"] == ["expense:receipt"]
    assert entry["disposition"]["albums"] == ["Expenses/Receipts"]
    assert entry["disposition"]["hide_after_days"] == 7
    assert entry["disposition"]["archive_after_days"] == 30
    assert entry["disposition"]["stage_delete_after_days"] == 90
    assert entry["disposition"]["protect"] is False

    sources = {child["source"] for child in entry["lens"]["children"]}
    assert sources == {"query", "detector", "semantic", "classify"}


def test_a_pack_rule_describes_where_it_came_from():
    rule = receipts_rule(name="work:receipts")
    rule.pack = "work"

    entry, = server.collections_payload(make_config(rule))

    assert entry["description"] == "From the work rule pack."
    # The description is part of the collection, so its revision must include it.
    assert entry["collection_revision"] == collection_from_rule(
        rule, description="From the work rule pack.").revision
    assert entry["collection_revision"] != collection_from_rule(rule).revision


def test_entries_survive_a_json_round_trip_including_non_native_criteria():
    # tomllib hands back real date objects, which json.dumps cannot encode. The
    # canonical form has to have already dealt with that before the daemon
    # tries to serialize a response.
    rule = receipts_rule(query={"from": date(2024, 1, 1), "screenshot": True})

    entries = server.collections_payload(make_config(rule))

    assert json.loads(json.dumps(entries)) == entries
    criteria = [child["criteria"] for child in entries[0]["lens"]["children"]
                if child.get("source") == "query"]
    assert criteria == [{"from": {"$date": "2024-01-01"}, "screenshot": True}]


# -- overrides ----------------------------------------------------------------

@pytest.mark.parametrize("configured, override, effective", [
    (True, None, True),
    (False, None, False),
    (True, False, False),
    (False, True, True),
])
def test_catalog_overrides_decide_effective_enabled(configured, override, effective):
    rule = receipts_rule(enabled=configured)
    overrides = {} if override is None else {rule.name: override}

    entry, = server.collections_payload(make_config(rule), overrides)

    assert entry["enabled"] is effective
    # The override is a runtime toggle, not an edit: the rule itself is untouched.
    assert rule.enabled is configured


def test_an_override_for_another_rule_does_not_leak():
    rule = receipts_rule(enabled=True)

    entry, = server.collections_payload(make_config(rule), {"some-other-rule": False})

    assert entry["enabled"] is True


def test_omitting_overrides_falls_back_to_the_configured_state():
    rules = (receipts_rule(name="on", enabled=True), receipts_rule(name="off", enabled=False))

    entries = entries_by_name(server.collections_payload(make_config(*rules)))

    assert entries["on"]["enabled"] is True
    assert entries["off"]["enabled"] is False


# -- honest failures ----------------------------------------------------------

def test_a_rule_the_model_cannot_express_reports_its_error_without_hiding_the_rest():
    good_before = receipts_rule(name="before")
    unconvertible = Rule(name="no-evidence")   # nothing for a lens to stand on
    good_after = receipts_rule(name="after")

    entries = server.collections_payload(
        make_config(good_before, unconvertible, good_after))

    # Order is preserved and nothing is silently dropped -- a shorter list would
    # read as "these are all your rules", which would be a lie.
    assert [entry["name"] for entry in entries] == ["before", "no-evidence", "after"]

    failed = entries[1]
    assert "no lens evidence" in failed["error"]
    assert failed["error"].startswith("ValueError: ")
    assert failed["lens"] is None and failed["disposition"] is None
    assert failed["collection_revision"] is None
    assert failed["lens_revision"] is None
    assert failed["disposition_revision"] is None
    # Identity and effective state still come through, so it can be shown.
    assert failed["id"] == "no-evidence"
    assert failed["source"] == "legacy-rule"
    assert failed["enabled"] is True

    for entry in (entries[0], entries[2]):
        assert entry["error"] is None
        assert entry["collection_revision"]

    assert json.loads(json.dumps(entries)) == entries


def test_a_rule_the_domain_rejects_outright_is_isolated_too():
    # archive must precede stage-delete; the Disposition constructor refuses.
    contradictory = receipts_rule(
        name="archive-too-late",
        archive=StageConfig(after_days=90),
        delete=StageConfig(after_days=30),
    )

    entries = entries_by_name(server.collections_payload(
        make_config(contradictory, receipts_rule(name="fine"))))

    assert "archive must occur before stage-delete" in entries["archive-too-late"]["error"]
    assert entries["fine"]["error"] is None


# -- the endpoint -------------------------------------------------------------

class FakeCatalog:
    """Stands in for the real catalog and records how the endpoint used it."""

    instances: list["FakeCatalog"] = []

    def __init__(self, path=None):
        self.overrides_read = 0
        self.closed = False
        FakeCatalog.instances.append(self)

    def rule_overrides(self) -> dict[str, bool]:
        self.overrides_read += 1
        return {"receipt-candidates": False}

    def close(self):
        self.closed = True


class FakeHandler(server.HaymishHandler):
    """The real handler with only its socket plumbing faked out."""

    def __init__(self, path: str, config: Config, token: str = "deadbeef",
                 supplied_token: str | None = "deadbeef"):
        self.path = path
        self.headers = email.message.Message()
        self.headers["Host"] = "127.0.0.1"
        if supplied_token is not None:
            self.headers["X-Haymish-Token"] = supplied_token
        self.state = server.ServeState(config)
        self.state.token = token
        self.status: int | None = None
        self.sent_headers: dict[str, str] = {}
        self.sent_body = io.BytesIO()
        self.wfile = self.sent_body

    def send_response(self, status, message=None):
        self.status = status

    def send_header(self, key, value):
        self.sent_headers[key] = value

    def end_headers(self):
        pass

    @property
    def payload(self):
        return json.loads(self.sent_body.getvalue())


@pytest.fixture
def offline_daemon(monkeypatch):
    """Catalog stubbed, and any reach for the Photos library made fatal."""
    FakeCatalog.instances = []
    monkeypatch.setattr(server, "Catalog", FakeCatalog)

    def no_photos(*args, **kwargs):
        raise AssertionError("the collections endpoint must not load the Photos library")

    monkeypatch.setattr(server.library, "all_photos", no_photos)
    monkeypatch.setattr(server.library, "load_photosdb", no_photos)
    return FakeCatalog


def test_endpoint_returns_compiled_collections_with_the_catalog_overrides_applied(offline_daemon):
    config = make_config(receipts_rule(enabled=True), receipts_rule(name="other"))
    handler = FakeHandler("/api/collections", config)

    handler.do_GET()

    assert handler.status == 200
    assert handler.sent_headers["Content-Type"] == "application/json"
    entries = entries_by_name(handler.payload["collections"])
    assert set(entries) == {"receipt-candidates", "other"}
    # The stub disables exactly one rule; the response has to reflect that.
    assert entries["receipt-candidates"]["enabled"] is False
    assert entries["other"]["enabled"] is True
    assert entries["other"]["collection_revision"]


def test_endpoint_reads_overrides_once_and_always_closes_the_catalog(offline_daemon):
    handler = FakeHandler("/api/collections", make_config(receipts_rule()))

    handler.do_GET()

    catalog, = offline_daemon.instances
    assert catalog.overrides_read == 1
    assert catalog.closed is True


def test_endpoint_closes_the_catalog_even_when_reading_overrides_fails(offline_daemon,
                                                                       monkeypatch):
    def boom(self):
        raise RuntimeError("catalog is locked")

    monkeypatch.setattr(FakeCatalog, "rule_overrides", boom)
    handler = FakeHandler("/api/collections", make_config(receipts_rule()))

    with pytest.raises(RuntimeError):
        handler.do_GET()

    catalog, = offline_daemon.instances
    assert catalog.closed is True


def test_endpoint_never_touches_the_photos_library(offline_daemon):
    handler = FakeHandler("/api/collections", make_config(receipts_rule()))

    handler.do_GET()

    assert handler.status == 200
    # The fixture makes any library call fatal; this pins the other half, that
    # the endpoint did not quietly wait on or trigger a load.
    assert handler.state.photosdb is None
    assert handler.state.photosdb_ready.is_set() is False


@pytest.mark.parametrize("supplied", [None, "", "not-the-token", "deadbee"])
def test_endpoint_requires_the_per_run_token(offline_daemon, supplied):
    handler = FakeHandler("/api/collections", make_config(receipts_rule()),
                          token="deadbeef", supplied_token=supplied)

    handler.do_GET()

    assert handler.status == 401
    assert handler.payload == {"error": "missing or bad token"}
    # Rejected before any work: no catalog was opened at all.
    assert offline_daemon.instances == []


def test_endpoint_rejects_a_foreign_host_header(offline_daemon):
    handler = FakeHandler("/api/collections", make_config(receipts_rule()))
    handler.headers.replace_header("Host", "evil.example.com")

    handler.do_GET()

    assert handler.status == 403
    assert offline_daemon.instances == []


def test_endpoint_response_is_json_serializable_end_to_end(offline_daemon):
    config = make_config(receipts_rule(query={"from": date(2024, 1, 1)}),
                         Rule(name="no-evidence"))
    handler = FakeHandler("/api/collections", config)

    handler.do_GET()

    assert handler.status == 200
    payload = handler.payload
    assert [entry["name"] for entry in payload["collections"]] == [
        "receipt-candidates", "no-evidence"]
    assert payload["collections"][1]["error"]
