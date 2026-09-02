# Haymish — handoff

A macOS Apple Photos cleanup tool. Local-first: everything runs on this Mac,
nothing leaves it. Public repo, **deliberately no LICENSE** (public + unlicensed
= all rights reserved, while licensing is undecided). Marketing site is built
but **not deployed** on purpose.

## What it is

Haymish is a **lens and control surface over Photos, not a replacement for it.**
Every decision it makes is expressed in Photos' own vocabulary — albums,
keywords, hidden — so it syncs to iPhone via iCloud and never becomes a silo.
If Haymish were deleted tomorrow, the organization it created would remain.

## Architecture

- `library.py` — read layer over **osxphotos** (needs Full Disk Access) plus the
  query vocabulary (`matches_query`): dates, place/near/has_location, persons,
  scores, camera/lens, raw/burst/live_photo, screenshot/selfie flags.
- `config.py` — `rules.toml` loading, validation, rule packs, posture presets
  (`tidy` vs `archival`; archival refuses delete stages at load time).
- `sweep.py` — the rule pipeline: query (metadata) → semantic (embeddings) →
  classify (vision LLM) → lifecycle (file → hide → archive → delete).
- `catalog.py` — SQLite at `~/.haymish/catalog.db`: actions ledger (for undo),
  captions, embeddings, review rejections, gallery judgments.
- `ai/indexer.py` — captions + embeddings. **Captions are keyed by
  `model + prompt version`** so changing either makes old captions visibly
  stale instead of silently mixing two descriptions of the library.
- `events.py` — trip/event clustering (time gap + haversine), significance
  ranking, representative-photo picking, place-name borrowing.
- `subgroup.py` — k-means over cached embeddings to split a huge review queue
  into labelled groups. Deterministic seeding: a queue must not reshuffle
  between visits.
- `server.py` + `static/dashboard.html` — localhost daemon and review UI.
- `actions/` — album/keyword writes via **photoscript** (AppleScript); hide and
  delete via **PhotoKit/pyobjc**.

## Non-negotiable safety model

1. **Deletion is always staged**, never immediate. It requires a verified
   backup (re-hashed at confirm time, not trusted from a DB flag), a typed
   confirmation, and macOS's own un-bypassable delete dialog.
2. **Never delete unattended.** Scheduled sweeps may file/tag/hide; they may
   never delete.
3. **The MCP server exposes no mutation tools.** Read-only by construction.
4. **Every action is undoable** via the actions ledger (`haymish undo`).
5. Keywords are only writable through photoscript — no PhotoKit API exists for
   them. Don't "fix" this; it was researched.

## State as of this handoff

**Working and tested:** rules engine, AI index (37,612 embeddings), galleries
with editing (rename, exclude photos, decline, all persisted), sub-grouped
review queues, packs, posture, tuning, daemon, MCP, scheduling with load
gating. 29 tests in `tests/`, all green (`uv run --extra dev pytest tests/`).

**The honest gap: Haymish has never been applied to the real library.**
`SELECT count(*) FROM actions WHERE undone=0` is **0**. Not one photo has been
filed, tagged, or hidden. Review rejections are also 0, which is why
`haymish tune` cannot suggest thresholds — it refuses to invent them. The build
is far ahead of the validation.

**Immediate blocker:** `rules.toml` points `vision_model` at `gemma3:4b`, which
is **no longer pulled** in Ollama. The last index run logged `vision=off`,
captioned nothing, and aborted on a timeout after 2 minutes. Pick a vision
model that actually exists (`ollama list`) — `qwen3-vl:8b` / `qwen3-vl:32b` are
present — and set `[global.ai] vision_model`.

**Caption coverage is ~50 of 28,268.** This matters: FaceTime stills and photos
of people contain no text, so with almost nothing captioned the index has
nothing to discriminate on and every semantic query scores a flat ~0.65. There
are **4,042 screenshots**; `haymish index --rule screenshots-general` targets
just those.

## Things already tried and deliberately rejected

Don't redo these without new evidence:

- **Structured caption fields** (`KIND:`/`SUBTYPE:`/`PEOPLE:`/`SENSITIVE:`).
  Measured on a 4B model: filed FaceTime under "app", called a photo of three
  people a screenshot, ignored the supplied vocabulary, and flagged SENSITIVE
  on 3 of 4 harmless images. Prose is fuzzy-matched so a wrong word costs
  relevance; a structured field is read as fact, so a wrong value is a lie.
- **Downscaling images before captioning.** Measured: 4032px vs 768px was 38s
  vs 34s. Ollama normalizes internally. Not worth it.
- **Hiding photos to collapse them in Photos.app.** Verified against Apple's
  docs: hidden photos do not appear in albums, so hiding evicts them from the
  very albums Haymish files them into. That is why the collapsed gallery view
  lives in Haymish's own UI. Keep it opt-in with its cost labelled.
- **Setting an album's key photo.** No public API exists in AppleScript or
  PhotoKit. Covers are marked with a keyword instead.

## Next steps, in order

1. Fix `vision_model`, run `haymish doctor` until clean.
2. Pilot: `haymish index --rule screenshots-general --limit 30`, then read the
   captions. They should name apps and say "video call" / "web page" / how many
   people. If not, tune `CAPTION_PROMPT` (and bump `CAPTION_PROMPT_VERSION`)
   while it is still cheap.
3. Full run: `caffeinate -i haymish index --rule screenshots-general`.
   `caffeinate` matters — the Mac sleeping stalls the run.
4. **Then actually apply something.** `haymish serve --replace`, review the
   sub-grouped screenshots queue, approve a group, and let it file. This is the
   step that has never happened and it is the whole point.
5. Extraction: receipts → merchant/amount/date → CSV. The accuracy bar jumps
   here — a wrong "is this a receipt" costs a click, a wrong amount costs an
   audit. Show parsed numbers for review before exporting.

## Working conventions

- Verify against the real library, not just synthetic fixtures. Several real
  bugs (place-name borrowing across cities, significance ranking, the 400-photo
  cap) were only visible on 28,000 real photos.
- When a check is cheap and the claim is load-bearing, measure it. Most of the
  rejected ideas above died to a five-minute benchmark.
- Long unattended runs need circuit breakers. A run that "completes" after
  hours having done nothing is worse than one that fails loudly.
