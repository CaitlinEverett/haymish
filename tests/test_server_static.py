"""The dashboard's static boundary: packaged CSS/JS, an asset allowlist, and
token injection that survives the token no longer being substituted into JS.

Nothing here starts a daemon, opens a socket, or touches a Photos library or
catalog: the asset lookup and page rendering are pure functions over package
resources, and the routing tests drive the real handler methods with the socket
plumbing replaced. The component tests run the exact bytes the allowlist serves
under node, with a stub api and a bare object for a root element -- no network
and no browser.
"""

from __future__ import annotations

import email.message
import html
import io
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from haymish import server
from haymish.ai.indexer import caption_key
from haymish.config import Config

CSS_PATH = "/static/dashboard/styles.css"
JS_PATH = "/static/dashboard/app.js"
COLLECTIONS_PATH = "/static/dashboard/components/collections.js"
SCRIPT_PATHS = (JS_PATH, COLLECTIONS_PATH)

requires_node = pytest.mark.skipif(shutil.which("node") is None,
                                   reason="node is needed to run the dashboard modules")


def offline_state(token: str) -> server.ServeState:
    """A real ServeState with a known token, pointed at paths that don't exist.

    ServeState's constructor does no I/O, and the static/page routes never reach
    the library or the catalog -- if one ever did, this state would fail loudly
    rather than open the user's real library.
    """
    config = Config(
        library=Path("/nonexistent/Test.photoslibrary"), backup=None,
        report_dir=Path("/nonexistent/reports"), ollama_host="", ollama_model="",
        claude_model="", ai_embed_model="", ai_vision_model="",
        ai_planner_backend="ollama", ai_planner_model="", rules=[],
        source_path=Path("/nonexistent/rules.toml"),
    )
    state = server.ServeState(config)
    state.token = token
    return state


class FakeHandler(server.HaymishHandler):
    """The real handler with only its socket plumbing faked out.

    do_GET, the host check and _send all run for real -- a stub that returned a
    canned response would tell us nothing about routing or headers.
    """

    def __init__(self, path: str, host: str = "127.0.0.1", token: str = "deadbeef"):
        self.path = path
        self.headers = email.message.Message()
        self.headers["Host"] = host
        self.state = offline_state(token)
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
    def body(self) -> bytes:
        return self.sent_body.getvalue()


def get(path: str, **kwargs) -> FakeHandler:
    handler = FakeHandler(path, **kwargs)
    handler.do_GET()
    return handler


def asset(path: str) -> tuple[bytes, str]:
    found = server.static_asset(path)
    assert found is not None, f"{path} should be allowlisted"
    return found


# -- asset allowlist ----------------------------------------------------------

def test_every_dashboard_asset_loads_from_package_resources():
    css_body, css_type = asset(CSS_PATH)
    js_body, js_type = asset(JS_PATH)
    component_body, component_type = asset(COLLECTIONS_PATH)

    assert css_type == "text/css; charset=utf-8"
    assert js_type == component_type == "text/javascript; charset=utf-8"
    # Real extracted payload, not a placeholder file.
    assert b":root{" in css_body and b"--ink" in css_body
    assert b"X-Haymish-Token" in js_body and b"function api(" in js_body
    assert b"export async function initCollections" in component_body
    assert len(css_body) > 5_000 and len(js_body) > 30_000
    assert len(component_body) > 1_000


def test_asset_bodies_decode_as_utf8():
    for path in (CSS_PATH, JS_PATH, COLLECTIONS_PATH):
        body, content_type = asset(path)
        assert "charset=utf-8" in content_type
        body.decode("utf-8")


def test_app_js_reads_the_injected_token_instead_of_a_substituted_one():
    js = asset(JS_PATH)[0].decode("utf-8")

    # A packaged file can't be text-substituted, so the placeholder must be gone
    # and the token must come from the page.
    assert server.TOKEN_PLACEHOLDER not in js
    assert 'meta[name="haymish-token"]' in js


def test_rule_toggle_refreshes_the_compiled_collections_view():
    js = asset(JS_PATH)[0].decode("utf-8")
    toggle = js[js.index("'/api/rules/toggle'"):js.index("// ---- Collections")]

    assert "await refreshCollections();" in toggle
    assert "const refreshCollections = () => initCollections" in js


def test_caption_staleness_uses_model_and_prompt_version_identity():
    config = offline_state("token").config
    config.ai_vision_model = "vision-model"
    current = caption_key(config)

    assert server._stale_caption_count(
        config,
        {current: 8, "vision-model": 5, "vision-model+p1": 3, "other+p2": 2},
    ) == 10


@pytest.mark.parametrize("path", [
    "/static/dashboard/../../server.py",
    "/static/dashboard/../../../etc/passwd",
    "/static/../server.py",
    "/static/dashboard/app.js/../app.js",
    "/static/dashboard/",
    "/static/dashboard/styles.css.map",
    "/static/dashboard/APP.JS",
    "/static/dashboard.html",
    "/static/broom.png",
    "/static/",
    "/static/dashboard/components/",
    "/static/dashboard/components/collections.js.map",
    "/static/dashboard/components/../app.js",
    "/static/dashboard/components/../../server.py",
    "/static/dashboard/components/collections.mjs",
    "/static/components/collections.js",
])
def test_unknown_and_traversal_static_paths_are_not_allowlisted(path):
    assert server.static_asset(path) is None


def test_allowlist_covers_exactly_the_packaged_dashboard_assets():
    assert set(server._STATIC_ASSETS) == {CSS_PATH, JS_PATH, COLLECTIONS_PATH}


# -- routing ------------------------------------------------------------------

def test_get_serves_allowlisted_asset_with_type_and_no_store():
    for path, expected_type in ((CSS_PATH, "text/css; charset=utf-8"),
                                (JS_PATH, "text/javascript; charset=utf-8"),
                                (COLLECTIONS_PATH, "text/javascript; charset=utf-8")):
        handler = get(path)
        assert handler.status == 200
        assert handler.sent_headers["Content-Type"] == expected_type
        assert handler.sent_headers["Cache-Control"] == "no-store"
        assert handler.sent_headers["Content-Length"] == str(len(handler.body))
        assert handler.body == asset(path)[0]


def test_get_rejects_traversal_and_unknown_static_paths():
    for path in ("/static/dashboard/../../server.py", "/static/", "/static/anything.js"):
        handler = get(path)
        assert handler.status == 404
        assert handler.body == b"not found"


def test_static_assets_keep_the_host_header_protection():
    handler = get(JS_PATH, host="evil.example.com")
    assert handler.status == 403
    assert b"bad host" in handler.body


def test_query_string_does_not_defeat_the_allowlist():
    assert get(JS_PATH + "?v=2").status == 200
    assert get("/static/dashboard/../app.js?x=" + JS_PATH).status == 404


# -- page ---------------------------------------------------------------------

def test_dashboard_html_links_external_assets_and_keeps_no_inline_payload():
    page = server.dashboard_html("abc123")

    assert '<link rel="stylesheet" href="/static/dashboard/styles.css">' in page
    assert '<script type="module" src="/static/dashboard/app.js"></script>' in page

    # The old inline blocks and their payloads are gone, not just moved around.
    assert "<style>" not in page
    assert ":root{" not in page
    assert "<script>" not in page
    assert "const TOKEN" not in page
    assert "addEventListener" not in page
    # Markup itself is untouched: the anchors app.js binds to still exist.
    for anchor in ('id="review-root"', 'id="apply-bar"', 'id="rules-list"',
                   'id="gallery-root"', 'id="banners"', 'id="collections-root"'):
        assert anchor in page


def test_dashboard_html_injects_the_token_into_a_meta_element():
    page = server.dashboard_html("0f1e2d3c")

    assert server.TOKEN_PLACEHOLDER not in page
    assert '<meta name="haymish-token" content="0f1e2d3c">' in page


def test_token_injection_is_escaped_as_an_html_attribute():
    # Hex today, but the page must not depend on that: a token that could close
    # the attribute would otherwise inject markup into every dashboard load.
    hostile = '"><script>alert(1)</script><x y="'
    page = server.dashboard_html(hostile)

    assert server.TOKEN_PLACEHOLDER not in page
    assert "<script>alert(1)" not in page
    assert "&quot;&gt;&lt;script&gt;" in page

    meta = re.search(r'<meta name="haymish-token" content="([^"]*)">', page)
    assert meta is not None, "token meta element was mangled by the hostile value"
    assert html.unescape(meta.group(1)) == hostile


def test_get_root_renders_the_page_with_this_runs_token():
    for path in ("/", "/index.html"):
        handler = get(path, token="feedface")
        assert handler.status == 200
        assert handler.sent_headers["Content-Type"] == "text/html; charset=utf-8"
        assert handler.sent_headers["Cache-Control"] == "no-store"
        page = handler.body.decode("utf-8")
        assert 'content="feedface"' in page
        assert "/static/dashboard/app.js" in page


# -- dashboard modules --------------------------------------------------------

def written(tmp_path: Path, path: str) -> Path:
    """The served bytes on disk, so node runs exactly what a browser would get."""
    target = tmp_path / Path(path).name
    target.write_bytes(asset(path)[0])
    return target


def run_collections(tmp_path: Path, api_body: str) -> dict:
    """Drive initCollections under node with a stub api and a plain-object root.

    api_body is a JS arrow function so each test can decide what the endpoint
    returns -- or that it fails. There is no DOM here on purpose: a module that
    needed one to be handed its own root would be reaching past the page.
    """
    module = written(tmp_path, COLLECTIONS_PATH)
    script = f"""
import {{ initCollections }} from {json.dumps(module.as_uri())};

const root = {{ innerHTML: null }};
const calls = [];
const banners = [];
const respond = {api_body};
const api = async path => {{ calls.push(path); return await respond(path); }};
const banner = (msg, key) => {{ banners.push([msg, key ?? null]); }};

await initCollections({{ api, banner, root }});
console.log(JSON.stringify({{ calls, banners, html: root.innerHTML }}));
"""
    result = subprocess.run(["node", "--input-type=module", "--eval", script],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def one_collection(**overrides) -> str:
    entry = {
        "id": "receipt-candidates", "name": "receipt-candidates",
        "description": "", "source": "legacy-rule",
        "enabled": True, "report_only": False,
        "collection_revision": "a" * 64, "lens_revision": "b" * 64,
        "disposition_revision": "c" * 64,
        "lens": {"type": "all", "children": [
            {"type": "atom", "source": "query", "criteria": {"screenshot": True}},
            {"type": "atom", "source": "detector", "criteria": {"name": "receipts"}},
        ]},
        "disposition": {"type": "disposition", "keywords": ["expense:receipt"],
                        "albums": ["Expenses/Receipts"], "protect": False,
                        "hide_after_days": 7, "archive_after_days": 30,
                        "stage_delete_after_days": 90},
        "error": None,
    }
    entry.update(overrides)
    return "async () => (" + json.dumps({"collections": [entry]}) + ")"


def test_app_js_delegates_the_collections_section_to_the_component():
    js = asset(JS_PATH)[0].decode("utf-8")

    assert "import { initCollections } from './components/collections.js';" in js
    # Wired with the page's own plumbing rather than a second copy of it. Match
    # the contract, not incidental line wrapping from a formatter or editor.
    assert re.search(
        r"initCollections\(\{\s*api,\s*banner,\s*root:\s*\$\('collections-root'\)\s*\}\)",
        js,
    )
    # Rules keep their existing behaviour, including the only rule mutation.
    assert "function renderRules(" in js
    assert "'/api/rules/toggle'" in js


@requires_node
@pytest.mark.parametrize("path", SCRIPT_PATHS)
def test_served_scripts_parse_as_modules(tmp_path, path):
    module = written(tmp_path, path)
    result = subprocess.run(["node", "--check", str(module)],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr


@requires_node
def test_component_calls_the_endpoint_and_renders_a_read_only_section(tmp_path):
    out = run_collections(tmp_path, one_collection())

    assert out["calls"] == ["/api/collections"]
    assert out["banners"] == []
    markup = out["html"]

    assert "receipt-candidates" in markup
    assert "legacy-rule" in markup
    assert "Evidence: query and detector" in markup
    for chip in ("enabled", "tag expense:receipt", "album Expenses/Receipts",
                 "hide after 7d", "archive after 30d", "stage delete after 90d"):
        assert ">" + chip + "<" in markup
    # Truncated revisions, so the section stays scannable but still identifies.
    assert "collection " + "a" * 12 in markup
    assert "a" * 13 not in markup

    # Read-only means read-only: nothing here can submit, toggle or navigate.
    for forbidden in ("<input", "<button", "<form", "<a ", "onclick"):
        assert forbidden not in markup


@requires_node
def test_component_shows_report_only_and_disabled_state(tmp_path):
    out = run_collections(tmp_path, one_collection(enabled=False, report_only=True))

    assert ">disabled<" in out["html"]
    assert ">report only<" in out["html"]
    assert ">enabled<" not in out["html"]


@requires_node
def test_component_shows_protection_instead_of_destructive_stages(tmp_path):
    disposition = {"type": "disposition", "keywords": [], "albums": [], "protect": True,
                   "hide_after_days": None, "archive_after_days": None,
                   "stage_delete_after_days": None}
    out = run_collections(tmp_path, one_collection(disposition=disposition))

    assert ">protected<" in out["html"]
    assert "hide after" not in out["html"]
    assert "stage delete" not in out["html"]


@requires_node
def test_component_shows_a_failed_conversion_without_dropping_it(tmp_path):
    out = run_collections(tmp_path, one_collection(
        error="ValueError: legacy rule 'no-evidence' has no lens evidence",
        lens=None, disposition=None, collection_revision=None,
        lens_revision=None, disposition_revision=None))

    markup = out["html"]
    assert "Could not compile this rule" in markup
    assert "has no lens evidence" in markup
    assert "receipt-candidates" in markup
    # No revisions to show, and no invented evidence line either.
    assert "Evidence:" not in markup


@requires_node
def test_component_escapes_every_string_the_server_hands_it(tmp_path):
    hostile = '<img src=x onerror="alert(1)">'
    disposition = {"type": "disposition", "keywords": [hostile], "albums": [hostile],
                   "protect": False, "hide_after_days": None,
                   "archive_after_days": None, "stage_delete_after_days": None}
    out = run_collections(tmp_path, one_collection(
        name=hostile, description=hostile, source=hostile, disposition=disposition,
        lens={"type": "atom", "source": hostile, "criteria": {}},
        error=hostile))

    markup = out["html"]
    # Rule names, keywords, album paths and error text all come from a file the
    # user typed; none of it may reach the page as markup.
    assert "<img" not in markup
    assert 'onerror="' not in markup
    # Name, source, description, error, keyword chip, album chip -- every place
    # the payload reaches the page, escaped and intact.
    assert markup.count("&lt;img src=x onerror=&quot;alert(1)&quot;&gt;") == 6


@requires_node
def test_component_reports_an_unreachable_endpoint_through_the_banner(tmp_path):
    out = run_collections(tmp_path, "async () => { throw new Error('daemon is gone'); }")

    assert out["calls"] == ["/api/collections"]
    assert out["banners"] == [["Could not load collections: daemon is gone", "collections"]]
    # The section says so rather than sitting on a stale "Loading…".
    assert "Collections unavailable." in out["html"]


@requires_node
def test_component_says_so_when_there_are_no_collections(tmp_path):
    out = run_collections(tmp_path, 'async () => ({"collections": []})')

    assert "No collections yet" in out["html"]


@requires_node
def test_component_does_nothing_without_a_root_or_an_api(tmp_path):
    module = written(tmp_path, COLLECTIONS_PATH)
    script = f"""
import {{ initCollections }} from {json.dumps(module.as_uri())};

let called = false;
const api = async () => {{ called = true; return {{collections: []}}; }};
// No root and no document: the component must not assume it owns the page.
await initCollections({{ api }});
await initCollections({{ root: {{ innerHTML: null }} }});
await initCollections();
console.log(JSON.stringify({{ called, hasDocument: typeof document !== 'undefined' }}));
"""
    result = subprocess.run(["node", "--input-type=module", "--eval", script],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"called": False, "hasDocument": False}
