"""`haymish review`: a local, localhost-only browser page showing exactly which
photos preview_sweep matched, with thumbnails, before anything is applied.

Deliberately reuses sweep.py's real per-stage functions (via apply_confirmed) for
whatever the user confirms -- there's no separate "preview apply" code path that
could drift from what `sweep --apply` would actually do to the same photos.

Thumbnails prefer osxphotos' own cached preview derivatives over decoding full
originals -- much faster, and works for HEIC/RAW without extra codecs.
"""

from __future__ import annotations

import functools
import html as htmllib
import http.server
import json
import subprocess
import threading
from pathlib import Path

from .catalog import Catalog
from .config import Config
from .paths import APP_DIR
from .sweep import RulePreview, SweepReport, apply_confirmed, preview_sweep

THUMB_DIR = APP_DIR / "thumbnails"
THUMB_SIZE = (320, 320)


def _thumbnail_path(uuid: str) -> Path:
    return THUMB_DIR / f"{uuid}.jpg"


def ensure_thumbnail(photo) -> Path | None:
    """Caches a small JPEG preview for `photo`. Returns None (no image, UI falls
    back to a placeholder) rather than raising -- a bad thumbnail shouldn't block
    reviewing every other photo in the batch."""
    dest = _thumbnail_path(photo.uuid)
    if dest.is_file():
        return dest

    from PIL import Image

    from . import library

    # image_source picks an image derivative — for videos that's the poster
    # frame, so video candidates get a real thumbnail instead of a placeholder.
    source = library.image_source(photo)
    if not source:
        return None

    THUMB_DIR.mkdir(parents=True, exist_ok=True)
    try:
        img = Image.open(source)
        img.thumbnail(THUMB_SIZE)
        img.convert("RGB").save(dest, "JPEG", quality=82)
    except Exception:
        return None
    return dest


def _rule_label(rule) -> str:
    parts = []
    if rule.hide:
        parts.append(f"hide after {rule.hide.after_days}d")
    if rule.archive:
        parts.append(f"archive after {rule.archive.after_days}d")
    if rule.delete:
        parts.append(f"stage delete after {rule.delete.after_days}d")
    return " · ".join(parts) if parts else "file only"


def _render_page(previews: list[RulePreview]) -> str:
    sections = []
    for rp in previews:
        cards = []
        for pc in rp.preview_candidates:
            has_thumb = _thumbnail_path(pc.uuid).is_file()
            thumb_html = (
                f'<img src="/thumb/{pc.uuid}" alt="" loading="lazy">'
                if has_thumb else '<div class="no-thumb">no preview</div>'
            )
            detail_html = (
                f'<p class="detail">{htmllib.escape(pc.classify_detail)}</p>'
                if pc.classify_detail else ""
            )
            cards.append(f"""
            <label class="card">
              <input type="checkbox" class="pick" data-rule="{htmllib.escape(rp.rule.name)}"
                     data-uuid="{htmllib.escape(pc.uuid)}" checked>
              <div class="thumb">{thumb_html}</div>
              <p class="filename">{htmllib.escape(pc.filename)}</p>
              {detail_html}
            </label>""")

        error_html = "".join(
            f'<p class="rule-error">{htmllib.escape(e)}</p>' for e in rp.errors
        )
        sections.append(f"""
        <section class="rule-section" data-rule="{htmllib.escape(rp.rule.name)}">
          <div class="rule-header">
            <div>
              <h2>{htmllib.escape(rp.rule.name)}</h2>
              <p class="rule-meta">{len(rp.preview_candidates)} matched · {htmllib.escape(_rule_label(rp.rule))}</p>
              {error_html}
            </div>
            <div class="rule-actions">
              <button type="button" class="link-btn" data-action="select-all">select all</button>
              <button type="button" class="link-btn" data-action="select-none">select none</button>
            </div>
          </div>
          <div class="grid">{"".join(cards)}</div>
        </section>""")

    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>haymish review</title>
<style>
  :root{{ --ink:#17191c; --ink-2:#585c5f; --ink-3:#8a8e90; --paper:#f6f6f4; --card:#fff;
    --line:#dfe1de; --accent:#1f5c56; --accent-tint:#e2eeec; }}
  @media (prefers-color-scheme: dark){{
    :root{{ --ink:#e9eae7; --ink-2:#b7bab6; --ink-3:#84898a; --paper:#15171a; --card:#1c1f22;
      --line:#2c3033; --accent:#5fb3a8; --accent-tint:#1d3532; }}
  }}
  *{{box-sizing:border-box;}}
  body{{margin:0; background:var(--paper); color:var(--ink);
    font-family:-apple-system,BlinkMacSystemFont,"Inter",sans-serif; line-height:1.5;}}
  .wrap{{max-width:1040px; margin:0 auto; padding:1.5rem 1.5rem 6rem;}}
  header{{margin-bottom:2rem;}}
  h1{{font-size:22px; font-weight:500; margin:0 0 .2rem;}}
  .sub{{color:var(--ink-2); font-size:14px; margin:0;}}
  .rule-section{{margin-bottom:2.5rem;}}
  .rule-header{{display:flex; justify-content:space-between; align-items:baseline;
    border-bottom:1px solid var(--line); padding-bottom:.5rem; margin-bottom:1rem;}}
  h2{{font-size:16px; font-weight:500; margin:0; text-transform:none;}}
  .rule-meta{{font-size:12.5px; color:var(--ink-3); margin:2px 0 0;}}
  .rule-error{{font-size:12.5px; color:#b0492e; margin:4px 0 0;}}
  @media (prefers-color-scheme: dark){{ .rule-error{{color:#e08468;}} }}
  .rule-actions{{display:flex; gap:10px;}}
  .link-btn{{background:none; border:none; color:var(--accent); font-size:12.5px; cursor:pointer;
    padding:0; text-decoration:underline;}}
  .grid{{display:grid; grid-template-columns:repeat(auto-fill,minmax(150px,1fr)); gap:10px;}}
  .card{{position:relative; display:block; cursor:pointer; border-radius:8px; overflow:hidden;
    border:1px solid var(--line); background:var(--card);}}
  .card input.pick{{position:absolute; top:8px; left:8px; width:18px; height:18px; z-index:2;}}
  .thumb{{aspect-ratio:1; background:var(--paper); display:flex; align-items:center; justify-content:center;}}
  .thumb img{{width:100%; height:100%; object-fit:cover; display:block;}}
  .no-thumb{{color:var(--ink-3); font-size:11px;}}
  .card:has(input:not(:checked)){{opacity:.35;}}
  .filename{{font-size:11px; color:var(--ink-2); margin:6px 8px 2px; overflow:hidden;
    text-overflow:ellipsis; white-space:nowrap;}}
  .detail{{font-size:10.5px; color:var(--ink-3); margin:0 8px 8px; font-style:italic;
    overflow:hidden; text-overflow:ellipsis; white-space:nowrap;}}
  .bar{{position:fixed; bottom:0; left:0; right:0; background:var(--card); border-top:1px solid var(--line);
    padding:1rem 1.5rem; display:flex; align-items:center; gap:1rem;}}
  .bar .count{{font-size:14px; color:var(--ink-2); flex:1;}}
  .bar button.apply{{background:var(--accent); color:var(--accent-tint); border:none; border-radius:6px;
    padding:.6rem 1.2rem; font-size:14px; font-weight:500; cursor:pointer;}}
  .bar button.apply:disabled{{opacity:.5; cursor:default;}}
  .bar .status{{font-size:13px; color:var(--ink-2);}}
</style></head>
<body>
<div class="wrap">
  <header>
    <h1>Review queue</h1>
    <p class="sub">Uncheck anything that shouldn't happen. Unchecked photos won't be
      suggested again for that rule.</p>
  </header>
  {"".join(sections)}
</div>
<div class="bar">
  <span class="count"><span id="n">0</span> selected</span>
  <span class="status" id="status"></span>
  <button class="apply" id="apply-btn" type="button">Apply selected</button>
</div>
<script>
  function updateCount() {{
    document.getElementById('n').textContent = document.querySelectorAll('.pick:checked').length;
  }}
  document.querySelectorAll('.pick').forEach(cb => cb.addEventListener('change', updateCount));
  document.querySelectorAll('[data-action]').forEach(btn => {{
    btn.addEventListener('click', () => {{
      const section = btn.closest('.rule-section');
      const checked = btn.dataset.action === 'select-all';
      section.querySelectorAll('.pick').forEach(cb => cb.checked = checked);
      updateCount();
    }});
  }});
  updateCount();

  document.getElementById('apply-btn').addEventListener('click', async () => {{
    const btn = document.getElementById('apply-btn');
    const status = document.getElementById('status');
    btn.disabled = true;
    status.textContent = 'Applying…';
    const byRule = {{}};
    document.querySelectorAll('.pick:checked').forEach(cb => {{
      (byRule[cb.dataset.rule] ||= []).push(cb.dataset.uuid);
    }});
    try {{
      const resp = await fetch('/apply', {{
        method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify(byRule)
      }});
      const data = await resp.json();
      if (data.ok) {{
        status.textContent = 'Done — you can close this tab.';
      }} else {{
        status.textContent = 'Something went wrong.'; btn.disabled = false;
      }}
    }} catch (e) {{
      status.textContent = 'Could not reach haymish.'; btn.disabled = false;
    }}
  }});
</script>
</body></html>"""


class _ReviewHandler(http.server.BaseHTTPRequestHandler):
    def __init__(self, *args, previews=None, config=None, catalog=None,
                 done_event=None, result_holder=None, **kwargs):
        self.previews = previews
        self.config = config
        self.catalog = catalog
        self.done_event = done_event
        self.result_holder = result_holder
        super().__init__(*args, **kwargs)

    def log_message(self, fmt, *args):  # keep the terminal quiet
        pass

    def _send(self, status: int, content_type: str, body: bytes):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send(200, "text/html; charset=utf-8", _render_page(self.previews).encode("utf-8"))
        elif self.path.startswith("/thumb/"):
            path = _thumbnail_path(self.path[len("/thumb/"):])
            if path.is_file():
                self._send(200, "image/jpeg", path.read_bytes())
            else:
                self._send(404, "text/plain", b"not found")
        else:
            self._send(404, "text/plain", b"not found")

    def do_POST(self):
        if self.path != "/apply":
            self._send(404, "text/plain", b"not found")
            return
        length = int(self.headers.get("Content-Length", 0))
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
            selections = {rule: set(uuids) for rule, uuids in payload.items()}
        except (json.JSONDecodeError, ValueError, AttributeError):
            self._send(400, "text/plain", b"bad request")
            return

        report = apply_confirmed(self.config, self.catalog, self.previews, selections)
        self.result_holder["report"] = report
        self._send(200, "application/json",
                    json.dumps({"ok": True, "counts": {o.rule: o.matched for o in report.outcomes}}).encode())
        self.done_event.set()


def run_review(config: Config, catalog: Catalog, photosdb, rule_names: list[str] | None = None,
               auto_open: bool = True, on_ready=None, rules_override=None) -> SweepReport | None:
    """Blocks until the user applies (or Ctrl-C cancels, applying nothing).
    on_ready(url), if given, is called once the server is listening -- lets the
    caller print/open the URL without this function needing to know how.
    rules_override reviews ephemeral rules (from `ask`/`find`) instead of rules.toml.
    A rule with zero candidates but an error (e.g. semantic rule, no AI index built
    yet) still opens the browser so the reason is visible, rather than silently
    reporting "nothing matched" for what was actually "couldn't check"."""
    previews = [rp for rp in preview_sweep(config, catalog, photosdb, rule_names,
                                            rules_override=rules_override)
                if rp.candidates or rp.errors]
    if not previews:
        return None

    for rp in previews:
        for p in rp.candidates:
            ensure_thumbnail(p)

    done_event = threading.Event()
    result_holder: dict = {}
    handler = functools.partial(_ReviewHandler, previews=previews, config=config, catalog=catalog,
                                 done_event=done_event, result_holder=result_holder)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = server.server_address[1]
    url = f"http://127.0.0.1:{port}/"

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    if on_ready:
        on_ready(url)
    if auto_open:
        subprocess.run(["open", url], check=False)

    try:
        done_event.wait()
    except KeyboardInterrupt:
        server.shutdown()
        return None

    server.shutdown()
    return result_holder.get("report")
