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
  captions, embeddings, review judgments, gallery judgments, and the new
  library-scoped observation/processor/effect substrate.
- `ai/indexer.py` — captions + embeddings. **Captions are keyed by
  `model + prompt version`** so changing either makes old captions visibly
  stale instead of silently mixing two descriptions of the library.
- `events.py` — trip/event clustering (time gap + haversine), significance
  ranking, representative-photo picking, place-name borrowing.
- `subgroup.py` — k-means over cached embeddings to split a huge review queue
  into labelled groups. Deterministic seeding: a queue must not reshuffle
  between visits.
- `server.py` + `static/dashboard/` — localhost daemon, strict static allowlist,
  extracted browser assets, and a first read-only Collections component/API.
- `domain.py` — pure immutable Lens → Collection → Disposition → Routine values;
  current rules compile into this model but still execute through the legacy path.
- `actions/` — album/keyword writes via **photoscript** (AppleScript); hide and
  delete via **PhotoKit/pyobjc**.

## Non-negotiable safety model

1. **Deletion is always staged**, never immediate. Final deletion is currently
   disabled until complete asset-component backup manifests replace legacy
   single-file rows. Re-enabling it still requires live re-verification, typed
   confirmation, and macOS's own un-bypassable delete dialog; there is no bypass.
2. **Never delete unattended.** Scheduled sweeps may file/tag/hide; they may
   never delete.
3. **The MCP server exposes no mutation tools.** Read-only by construction.
4. Supported album, keyword, hide, and staged-delete actions from ordinary
   sweep/review runs use the ledger and can be undone. Backup exports are retained;
   a finalized deletion would not be undoable through Haymish.
5. Keywords are only writable through photoscript — no PhotoKit API exists for
   them. Don't "fix" this; it was researched.

## State as of the 2026-09-03 continuation

**Working and tested:** the existing rules engine, AI index, editable galleries,
sub-grouped review queues, packs, posture, tuning, daemon, MCP, and scheduling
remain intact. The uncommitted continuation adds PhotoKit identifier
normalization, an exact-version incremental catalog substrate with atomic
migration, pure tag-first domain values, extracted/package-tested browser
assets, a read-only Collections API/component, and evidence-driven caption
runtime circuit breakers. The isolated suite is now **147 tests**.

**The honest product gap is unchanged: no real canary has been completed.** No
Photos mutation was performed during this continuation. The prior read-only
audit found 0 active actions and 0 review judgments. Do not infer Mac/iCloud
behavior, undo reliability, precision, or trust from synthetic tests.

**Deletion is intentionally unavailable.** The bypass is gone, but the legacy
archive ledger still records only one exported path and omits Live Photo/RAW
companions. `haymish confirm-deletes` therefore fails closed before loading
Photos. Complete manifests are the next trust-floor implementation, not an
optional enhancement.

**Model/runtime blockers are measured, not hypothetical.** Configuration now
uses installed `qwen3-vl:8b` and `qwen3.6:35b`, while preserving the existing
`nomic-embed-text` index. The bounded 30-screenshot run produced only 18
captions and 12 final timeouts; visual review found useful broad descriptions
but four clear category/platform errors and one questionable identity claim.
Follow-up revisions exposed hidden-reasoning exhaustion and now fail honestly,
but `qwen3-vl:8b` remains rejected for unattended batches. See
`docs/CAPTION-PILOT-2026-09-03.md`. A single green "index coverage" number is
still misleading until the UI shows quality layers.

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
- **Scaling `qwen3-vl:8b` after one good example.** A 30-item run completed only
  18 captions; deterministic `/no_think` revisions still failed 2 of 6 on the
  fixed difficult subset. Do not run the 4k screenshot queue with this model
  without new evidence from a balanced labeled benchmark.

## Next steps, in order

1. Implement complete, library-scoped archive manifests for every exported
   component (primary original/video, Live Photo motion, associated RAW), with
   source comparison where possible and live re-verification of every component.
   Only then replace the temporary final-deletion stop gate.
2. Verify configured read-library identity equals the Photos mutation target;
   mismatch must force read-only behavior.
3. Correct Apple failure/noise score semantics and keep uncalibrated culling
   disabled. The starter template is now file/tag-only with no default hide.
4. Evaluate `qwen3-vl:32b` and a genuinely non-thinking local vision model on
   one fixed balanced set; keep concurrency 1 and the new failure gates. Add
   classify/search/Ollama circuit breakers and then wrap multi-hour indexing in
   a durable resumable job envelope.
5. Run a 5-photo album/tag-only canary, verify the Mac and iCloud result, then
   verify undo against actual Photos state. This requires Caitlin present.
6. Run a reviewed screenshot-group pilot and record explicit judgments. Do not
   launch a full caption run or unattended routine until a model passes the
   completion and factuality gates in the caption-pilot note.
7. Build group-level screenshot dispositions and versioned judgments; receipt
   extraction and Studio workflows follow only after the trust floor is measured.

## Working conventions

- Verify against the real library, not just synthetic fixtures, but keep all
  automated tests on temporary databases/directories with mocked Photos APIs.
- When a check is cheap and the claim is load-bearing, measure it. Most rejected
  ideas above died to a five-minute benchmark.
- Long work is restartable: bounded phases, durable identity/checkpoints,
  idempotent effects, heartbeat/lease ownership, cooperative cancellation, and
  actionable failure state. Progress text alone is not a checkpoint.
- For agent work, preflight auth/model with a tiny call, run at most one blocking
  long subprocess per control loop, persist prompt/run ID/log first, and treat
  timeout or editor disconnect as indeterminate until process/log/git state is
  checked. Never launch multiple long reviews plus a full suite in one editor
  turn; that pattern caused the difficult 2026-09-02 handoff.
- Tests should pin behavior and safety contracts, not whitespace or line wrapping.
- Handoffs include exact validation, dirty paths, active PIDs/run IDs/logs,
  unresolved findings, and one safe restart command.
