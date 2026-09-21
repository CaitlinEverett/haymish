# Haymish execution roadmap

## Wave 0 — trust floor (now)

- [x] Rename checkout to `haymish`
- [x] Normalize PhotoKit delete identifiers and prove partial unstaging
- [x] Add library-scoped incremental observation/processor/effect substrate
- [x] Add pure tag-first domain values and legacy Rule adapter
- [x] Remove backup bypass
- [x] Fail final deletion closed while only legacy single-file archive rows exist
- [ ] Replace single-file archive rows with complete asset manifests
- [ ] Include Live Photo and RAW companions
- [ ] Fix Apple failure/noise score semantics; disable false-zero culling until calibrated
- [ ] Verify configured read library equals mutation target
- [x] Make starter behavior file/tag-only; no default hide

Exit gate: no known contradiction between executable deletion safety and product claims; safety paths have isolated tests.

## Wave 1 — first trusted value

- [ ] Resolve installed models by role in doctor/setup
- [x] Run bounded `qwen3-vl:8b` caption pilot; reject unattended scale-up from measured completion/quality
- [x] Add caption empty-output, failure-rate, chunk, concurrency, and retry circuit breakers
- [ ] Evaluate `qwen3-vl:32b` plus a non-thinking vision model on one fixed labeled set
- [ ] Add classify/search circuit breakers and Ollama contention diagnosis
- [ ] Put multi-hour indexing behind a durable job envelope with checkpoints, heartbeat, recovery, and cooperative cancellation
- [ ] File/tag-only 5-photo canary
- [ ] Verify Mac/iCloud outcome
- [ ] Verify undo against actual Photos state
- [ ] Run 30-screenshot reviewed pilot and record explicit judgments

Exit gate: zero unexplained mutations; action ledger reconciles; canary roundtrip is verified.

## Wave 2 — browser substrate

- [x] Extract dashboard CSS/JS into modules with parity tests
- [x] Add server static allowlist and content types
- [x] Add validated read-only Collections API compiled from legacy rules
- [ ] Display layered index quality, not one coverage number
- [ ] Add component tests for collection/lens/disposition/cadence serialization

Exit gate: existing dashboard behavior survives; new components can render a collection without mutating Photos.

## Wave 3 — screenshot workbench

- [ ] Incremental screenshot inbox
- [ ] Rename, split, merge, refine, and parent suggested groups
- [ ] Assign different keyword/album/protect/later dispositions per group
- [ ] Explicit judgments: apply, skip effect, not match, protect, later, never suggest
- [ ] Preview exact effect delta and conflicts
- [ ] Persist collection revisions and judgments
- [ ] Apply additive canaries through the single executor

Exit gate: 4k screenshots can be reduced to reviewable group decisions; unchanged resolved groups do not return.

## Wave 4 — cadence and trust promotion

- [ ] Persistent work queue with leases/retries/dead-letter state
- [ ] Lightweight discovery cadence
- [ ] Bounded idle enrichment
- [ ] Review digest
- [ ] Rule probation and promotion
- [ ] Trusted additive routines only
- [ ] External drift reconciliation
- [ ] Cache/storage budgets and pruning

Exit gate: regular runs process only new/changed/unresolved work and never overlap mutations.

## Wave 5 — intent-aware similarity

- [ ] Original-byte exact duplicate manifests
- [ ] Pixel-equivalent and perceptual similarity tiers
- [ ] Asset-family relationships
- [ ] Utility screenshot redundancy policy
- [ ] Portrait/burst selection sets with quality plus diversity
- [ ] Bracket/stack/panorama/artistic protection
- [ ] Deletion-candidate evidence and keeper comparison

Exit gate: no code path equates visual similarity with deletion without intent and protection evaluation.

## Wave 6 — Studio

- [ ] Shoot/session boundaries and presets
- [ ] Contact sheet, keyboard culling, compare/zoom
- [ ] Pick/alternate/technical-review tags
- [ ] Ingest and multi-volume backup manifests
- [ ] Export decisions for external tools
- [ ] Source-derived blink/expression composite spike with full provenance
- [ ] Documentary mode disables composites

## Wave 7 — receipts and expansion

- [ ] Vision OCR fallback
- [ ] Reviewed merchant/date/amount extraction
- [ ] Field-level confidence and source spans
- [ ] CSV export after review
- [ ] Pilot with organizers/archivists

## Business track

- [ ] Choose license before public onboarding
- [ ] Run 5 Personal, 5 Studio, and 2 organizer pilots
- [ ] Publish honest private-beta page
- [ ] Offer founding paid beta before broad advanced build
- [ ] Choose Personal/Studio packaging from observed value and support cost
