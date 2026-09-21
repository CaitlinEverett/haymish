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
