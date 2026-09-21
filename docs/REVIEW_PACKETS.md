# Review packets (2026-09-21)

Pushed to `main`. Full suite green after these commits. Live Photos apply was not part of this pass.

## Packet A — `7e1964f` galleries + paged review

**Review:** [7e1964f](https://github.com/CaitlinEverett/haymish/commit/7e1964f)

| Area | What to check |
|------|----------------|
| CLI | `haymish galleries --query "beach vacation"` ranks by mean member score; partial index warning |
| API | `POST /api/galleries` optional `query`; per-event `score` + query metadata |
| Dashboard | Galleries form semantic field; score badges; review grids fold by default, **48/page**, Previous/Next |
| Apply safety | `sessionPicks` keeps off-page/folded selections so reject blast radius is correct |
| Tests | `tests/test_gallery_query.py` |

**Smoke:** open app → expand one subgroup → page if >48 → uncheck one → confirm selected count.

## Packet B — `293043e` smoke + deferred plans

**Review:** [293043e](https://github.com/CaitlinEverett/haymish/commit/293043e)

| Doc | Intent |
|-----|--------|
| `docs/SMOKE.md` | Terminal doctor → catch-up → one subgroup → undo/recover |
| `docs/plans/live-mass-file-hide-delete.md` | Caps + human gates for mass file/hide; no agent finalize delete |
| `docs/plans/backup-archive-delete-dogfood.md` | Manifest verify before any delete dogfood |
| `docs/plans/overnight-full-library-reindex.md` | Host/launchd job; agents wire only |
| `docs/plans/mcp-apply-delete-tools.md` | Keep propose-only; no delete MCP |

**Smoke:** skim plans; follow SMOKE when ready for live UI.

## Packet C — `ec34cb0` doctor fix config + hardening tests

**Review:** [ec34cb0](https://github.com/CaitlinEverett/haymish/commit/ec34cb0)

| Area | What to check |
|------|----------------|
| CLI | `haymish doctor --fix config` prints proposals; **never** rewrites `rules.toml` |
| Doctor | Backup fail-closed when archive/delete rules exist without backup (already live) |
| Tests | ask `--save` gate, MCP propose-only registry, recover ledger, session empty_state, hide/sweep counters |

**Smoke:** `uv run haymish doctor --fix config` (read-only suggestions).

## Packet E — `aec2ab4` / `7c3e933` / `924601b` / `d398a4b` host catch-up resilience

**Review:** [aec2ab4](https://github.com/CaitlinEverett/haymish/commit/aec2ab4) … [d398a4b](https://github.com/CaitlinEverett/haymish/commit/d398a4b)

| Area | What to check |
|------|----------------|
| Non-TTY index | Plain progress + chunk heartbeats; Rich Progress only on TTY |
| Doctor | `--fix config` proposes commenting archive/delete when backup unset |
| Host launcher | `./scripts/haymish-reindex-catchup.sh` double-forks out of agent process groups |
| Captions | `num_predict=1024`, retry on empty `length`, catch-up warns instead of aborting high rates |
| Live tip | If catch-up stalls, `ollama ps` — stop large non-vision models hogging VRAM |

**Watch (on your Mac):** `tail -f ~/.haymish/index.log` and `cat ~/.haymish/jobs/reindex-status.json`  
Heuristics lens: `docs/HCI_HEURISTICS.md` (Atlas 2.5).

## Prior efficacy (already on main)

**Review:** [52f3f15](https://github.com/CaitlinEverett/haymish/commit/52f3f15) — review UX, `model_resolve`, hide eligibility.

## Host job (not a commit)

Prefer the launcher (survives agent exit; plain progress when non-TTY):

```bash
cd ~/dev/haymish && ./scripts/haymish-reindex-catchup.sh --catch-up-captions
tail -f ~/.haymish/logs/reindex-catchup-*.log
# status: cat ~/.haymish/jobs/reindex-status.json
```

`doctor --fix config` now also proposes commenting out `archive`/`delete` when backup is unset (does not rewrite rules.toml).

## Known live doctor gaps (human)

1. Index freshness — catch-up in progress / needed.
2. Backup volume unset while archive/delete rules enabled — paste doctor proposals or set `[global].backup` before any delete dogfood (`docs/plans/backup-archive-delete-dogfood.md`).

## Return handoff — 2026-09-21 evening (UT + cleanup)

Git: everything from this push is already on `main` (`aeab4e9` and ancestors). No open PR; no dirty tree after this handoff commit. Session `/tmp/haymish-*` temps (including a dash URL that held a serve token) were deleted.

### Shipped this session (already on main)

| Commit range | What |
|--------------|------|
| `52f3f15`…`7e1964f` | Efficacy + galleries `--query` + paged review / `sessionPicks` |
| `293043e`…`ec34cb0` | SMOKE + deferred plans; doctor `--fix config`; safety tests |
| `a6f5f1f`…`d398a4b` | Review packets; non-TTY/heartbeats; caption length resilience; host launcher |
| `f8b7545`…`aeab4e9` | Atlas HCI heuristics + standing brief doctrine |

### Live host state (not in git)

- **Catch-up:** `./scripts/haymish-reindex-catchup.sh` family — watch `~/.haymish/index.log` (was ~104/29,116 captions, failed=0). Status: `~/.haymish/jobs/reindex-status.json`.
- **Serve:** was healthy on `:8787` at handoff (durable pid family ~72965). If it dies: restart outside the agent shell so it survives. Token lives in `~/.haymish/serve.json` — do not paste it into chat.
- **No Photos apply** was done in UT.

### Pretend-user findings (next product work, not committed as code)

1. Fail-closed UI for archive/delete/hide when backup unset (doctor proposes; rules still look “ready”).
2. Cap first review page; show why-matched / hide eligibility on cards (4.3k screenshot queue is a cliff).
3. Index panel should show host catch-up % / rate (status file does not update mid-run).
4. Find/Ask honesty when captions ≪ embeds; Ask subgroup labels can be nonsense (“document, car, card”).
5. Rename `junk`; `haymish serve` auto-detach.

### What you should check when you return

```bash
cd ~/dev/haymish
git status -sb                    # expect clean main == origin/main
uv run haymish doctor --fix config   # paste archive/delete comments or set backup
tail -20 ~/.haymish/index.log     # catch-up still advancing?
cat ~/.haymish/jobs/reindex-status.json
# if catch-up dead:
./scripts/haymish-reindex-catchup.sh --catch-up-captions
# dashboard (optional):
uv run haymish serve              # then open printed URL; prefer Terminal for PhotoKit
```

Smoke when ready for a real apply: `docs/SMOKE.md` (one small subgroup → undo). Do not enable delete dogfood until backup plan is done.
