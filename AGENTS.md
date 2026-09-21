# Haymish agent guide

## Mission

Haymish is a local-first, review-first control surface over Apple Photos. It helps people annotate, group, curate, and organize assets without becoming a second photo silo. Applied albums, keywords, and Photos state should remain useful if Haymish is removed.

The product is tag-first. Albums are optional views; `hide`, archive, and deletion are separate policies, not synonyms for organization.

## Non-negotiable safety

1. Never write directly to the Photos database. Read through `osxphotos`; mutate through supported Photos automation APIs.
2. Automated tests must never touch the real Photos library, `~/.haymish/catalog.db`, or a real backup volume. Use mocks and temporary databases/directories.
3. Scheduled work may discover, enrich, notify, apply explicitly trusted additive effects, or stage a reviewed deletion candidate. It must never finalize deletion.
4. Final deletion requires a complete independently verified backup manifest, typed confirmation, and the macOS system dialog. Do not add or restore a backup bypass.
5. Protection and whole-shoot retention judgments veto hide and deletion staging. Making an additional backup copy remains allowed.
6. Culling is not deletion. `pick`, `alternate`, `not selected`, `technical review`, and `deletion candidate` are distinct states.
7. Similarity alone never implies redundancy. Interpret similarity together with asset family, capture intent, shoot policy, and human judgment.
8. Do not claim safety, accuracy, backup completeness, or cross-device behavior beyond what code and live verification prove.

## Architecture direction

- `haymish/domain.py`: pure, immutable Lens → Collection → Disposition → Routine model. It does not execute.
- `haymish/incremental.py` + incremental `Catalog` APIs: library-scoped asset observations, processor completion, and effect satisfaction.
- Existing `config.Rule` and sweep behavior remain compatibility paths until migration is explicitly tested.
- Browser work should use explicit API contracts and small components. Do not add more behavior to the monolithic dashboard without first extracting a component/module boundary.
- Expensive work is keyed by library, asset, processor version, and input fingerprint. Unchanged completed work must not rerun.
- Effects are keyed by collection/disposition revision and target fingerprint. An already satisfied effect should be verified or skipped, not blindly repeated.

## Validation

Run the narrowest relevant tests, then the full suite:

```sh
uv sync --extra dev --extra mcp
uv run pytest tests/test_<area>.py -q
uv run pytest tests/ -q
```

For static diagnostics, the project virtual environment is `.venv`. Do not use ambient Conda Python.

For safety-path tests:

- Make the fake execute callbacks/change blocks; a mock that returns success without executing the mutation closure is not coverage.
- Mutation-test the regression where practical.
- Include partial success, replay/idempotency, cancellation, missing assets, stale state, and library isolation.

## Agent execution

- Prefer the logged-in Cursor Agent CLI for subscription-backed autonomous coding in this repo. The Commonscience Otto MCP is scoped to a different workspace and must not be presented as operating here.
- Use Dirac for an independent adversarial review of bounded diffs; invoke it non-interactively with the `openai-codex` subscription provider.
- Use Aider only with a local model or another explicitly non-metered provider. Never silently fall back to paid API credentials.
- Give agents disjoint write scopes. Review generated diffs and rerun tests yourself.
- Do not commit or push unless Caitlin explicitly asks.
- Preserve unrelated working-tree changes.

## Long-running agent work

Treat any multi-step task or external-agent run as a restartable workflow, not one large editor turn.

1. Write down the objective, invariants, bounded phases, and validation gate before editing. Keep each phase small enough to leave a coherent diff if the process stops.
2. Preflight authentication, model/provider, binary, and a tiny read-only call before launching expensive work. A configured command is not proof that a headless invocation works.
3. Run at most one blocking long-duration subprocess per control loop. Prefer detached/async execution with a durable run ID and log path; never bundle multiple agent calls plus a full test suite into one synchronous tool request.
4. Persist the exact prompt and output location before launch. Poll status separately so editor or transport failure does not erase the only handle to a still-running job.
5. Treat timeout, disconnect, or UI hang as **indeterminate**, not failed. Check process ownership, logs, git state, and generated files before retrying; retries must not duplicate edits or side effects.
6. Checkpoint at semantic boundaries with narrow tests and `git diff --check`. Do not use commits as checkpoints unless Caitlin asked for commits.
7. Keep implementation and adversarial review separate. Reviewers are read-only; incorporate one concrete finding at a time and rerun the relevant test before broad validation.
8. Tests should pin behavior, safety invariants, and API contracts—not whitespace, line wrapping, generated prose, or another incidental representation.
9. A handoff must include: objective, completed phases, exact validation run/results, dirty paths, active run IDs/PIDs/logs, unresolved findings, and the single safest restart command.
10. Before yielding, stop or identify every child process and remove temporary extracts while preserving review logs that another agent needs.

## Product priorities

1. Trust floor: identifiers, complete archive manifests, mandatory backup gate, library identity, reversible additive actions.
2. First real canary: reviewed tag/album application and verified undo.
3. Incremental screenshot workbench: split/merge/refine groups and assign different dispositions.
4. Regular cadence: lightweight discovery, bounded enrichment, review digest, trusted additive routines.
5. Intent-aware duplicate/portrait/shoot workflows.
6. Receipt extraction and professional tooling after core trust is measured.

## Terminology

Prefer:

- annotate / tag / add to collection
- exact duplicate / pixel-equivalent / similar capture / asset family
- pick / alternate / not selected / technical review
- cleanup candidate / staged deletion

Avoid using `move`, `junk`, `reject`, or `best` when a more precise term exists.
