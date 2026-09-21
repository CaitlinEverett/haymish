# Haymish architecture direction

## Current system

- `library.py`: defensive read adapter over osxphotos
- `config.py`: legacy TOML Rule parsing and safety validation
- `sweep.py`: selection and lifecycle execution
- `catalog.py`: SQLite cache, judgments, runs, action ledger, and incremental state
- `domain.py`: pure future Lens/Collection/Disposition/Routine values
- `incremental.py`: typed observation, processor, and effect states
- `actions/`: supported Photos mutations
- `server.py`: localhost daemon and JSON API
- `static/dashboard.html` + `static/dashboard/`: browser shell, extracted assets, and component modules

The existing rule path remains operational while the new domain compiles toward it. Migration must not create a second mutation engine.

## Target layers

### Adapters

Photos read/write, local AI, filesystem/archive, scheduling, and notifications. Adapter outputs are normalized into domain evidence and effects.

### Domain

Pure immutable values:

```text
Lens → Collection → Disposition → Routine
```

Revision hashes invalidate judgments and automation when meaning changes.

### Incremental substrate

Library-scoped observations prevent UUID state crossing libraries. Processor rows answer whether exact work for a processor version and input fingerprint is reusable. Effect rows answer whether a disposition revision and target were already applied/verified.

Future additions:

- inventory run/watermark and missing-asset reconciliation
- persistent work queue with lease/retry/dead-letter state
- collection membership and explicit judgment tables
- asset groups/relationships/shoots/derived provenance
- exported state manifest

### Durable job contract

Any operation that can outlive one HTTP request, terminal session, or editor turn must be restartable from durable state. The current in-memory `ServeState.jobs` map is a compatibility path for short interactive work, not the target for multi-hour indexing or mutation.

- Create the job before starting work, with immutable normalized input, a library ID, code/model revisions, and an idempotency key.
- Represent bounded steps explicitly. Persist a checkpoint only after that step's outputs or effects are durable; progress text is not a checkpoint.
- Claim work atomically with an owner and expiring lease. Heartbeats show liveness; an expired lease makes work recoverable after a crash without declaring it failed.
- Make cancellation cooperative and durable (`cancel_requested` → `cancelled`) at safe boundaries. Never kill a thread in the middle of a Photos mutation.
- Classify failures as retryable, blocked, or terminal. Retry only idempotent steps with bounded backoff; retain attempt history and a dead-letter reason.
- Freeze the planned denominator and expose completed, skipped, failed, current step, last heartbeat, and the next safe action. A client disconnect must not erase status.
- Serialize Photos mutations per library. Read/enrichment work may run concurrently only when resource budgets and exact processor claims allow it.
- Give every side effect an idempotency key and durable receipt. On replay, verify an existing effect or surface drift; never infer success from a prior process having started.

The first implementation should wrap indexing in this envelope without changing its per-chunk durable caption/embedding behavior. General scheduling and mutation queues come only after recovery, cancellation, and replay tests pass.

### Services

- inventory service
- feature/enrichment service
- lens evaluation service
- review service
- effect planner/executor
- integrity reconciler
- routine scheduler

Services call one effect executor. Browser, CLI, MCP, and launchd are clients.

### API

Versioned JSON contracts with explicit request validation. Mutating operations use one-shot preview/session IDs, exact candidate sets, and serialized jobs. No endpoint accepts arbitrary destructive UUIDs outside a verified session.

### Browser

Extract the current single file into static modules before adding the workbench:

```text
static/dashboard/
  index.html
  styles.css
  app.js
  api.js
  state.js
  components/
    status-bar.js
    collection-list.js
    lens-builder.js
    group-review.js
    disposition-editor.js
    cadence-editor.js
    effect-preview.js
    gallery-browser.js
```

Use plain modern browser modules initially; avoid a framework dependency until component/state complexity proves it necessary. Components consume JSON and emit events. Domain validation remains server-side and is mirrored for immediate UX only.

## Incremental flow

1. Identify the configured Photos library and derive a stable `library_id`.
2. Observe cheap asset fingerprints.
3. New/changed observations enqueue only processors whose exact version/input result is absent.
4. Evaluate affected collection revisions from cached evidence.
5. Exclude resolved judgments and satisfied effects before expensive work.
6. Present unresolved/outlier work.
7. Execute selected effects idempotently and verify Photos state.
8. Reconcile external drift; ask before reapplying a manually removed effect.

A cheap UUID/metadata inventory may still occur. The guarantee is no repeated expensive processing or mutation for unchanged resolved work.

## Safety boundaries

- Configured read library must match the Photos mutation target; otherwise run read-only.
- Protection and shoot retention are evaluated before hide/delete policy.
- Archive manifests represent every asset component, not one path.
- Similarity evidence never directly stages deletion.
- Remote model use is explicit and provenance-recorded.
- Tests use temp catalogs and mocked Photos modules.

## Migration slices

1. Trust-floor correctness and tests.
2. Pure domain and incremental catalog substrate.
3. Extract static browser modules with behavior parity.
4. Read-only Collections API compiled from legacy rules.
5. Screenshot workbench judgments and per-group additive dispositions.
6. Persisted routines and review digest.
7. Trusted additive incremental application.
8. Intent-aware grouping and professional posture. Archive manifests remain a trust-floor prerequisite, not a late feature.
