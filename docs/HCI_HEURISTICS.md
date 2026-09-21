# Haymish HCI heuristics (CS 6750 Atlas)

Working guidelines for agents and humans designing Haymish surfaces.
Source: [OMS CS6750 Atlas · 2.5 Design Principles and Heuristics](https://omscs6750.gatech.edu/atlas/lesson-2-5.html)
(Joyner; Norman + Nielsen + Constantine/Lockwood + Universal Design, merged into 15).

**These are guidelines that often conflict — not laws.** Prefer the safer / more
review-first choice when principles collide (Haymish `AGENTS.md` safety wins).

## The 15 (Atlas names) → Haymish practice

| # | Principle | Haymish translation |
|---|-----------|---------------------|
| 1 | **Discoverability** | Actions visible where the task is (Review / Apply / Undo / catch-up). Prefer labeled controls over hidden CLI-only paths for primary flows. |
| 2 | **Simplicity** | One job per section. Don’t dump OCR dump / model IDs / full stacks in the primary path; put detail behind doctor/logs. |
| 3 | **Affordances / signifiers** | Checkboxes look checkable; Apply looks consequential; fold/expand/pager look like controls. Don’t style destructive actions like primary file. |
| 4 | **Mapping** | Labels match Photos vocabulary (`hide`, `keyword`, `album`, `stage delete` ≠ “junk/move/best”). Effects preview before apply. |
| 5 | **Perceptibility** | Always show system state: selected count, job state/error, hideable vs iCloud-skipped, caption/embed coverage, catch-up `done/total`. |
| 6 | **Consistency** | Same words CLI ↔ dashboard ↔ MCP. Same keyboard (Space / a / n) wherever review grids appear. |
| 7 | **Flexibility** | Novice: buttons + empty-state CTAs. Expert: CLI, keyboard, MCP propose-only. Don’t force one path. |
| 8 | **Equity** | Don’t segregate “power” recovery; TCC/Terminal hints must be readable and actionable for everyone stuck on PhotoKit. |
| 9 | **Ease and comfort** | Large queues: fold + page (≤48). Don’t require scanning 10k DOM nodes. |
| 10 | **Structure** | Review → Apply → Outcome → Undo/Recover. Doctor → Fix proposals → Human paste. Index → status → log. |
| 11 | **Constraints** | Hard gates: backup before delete, confirm >threshold apply, no MCP apply/delete. Make illegal paths unclickable, not merely warned. |
| 12 | **Tolerance** | Undo / recover-hidden / durable rejects / catch-up resume. Mistakes shouldn’t require Photos surgery. |
| 13 | **Feedback** | Immediate + informative: job progress, apply reject counts, doctor ✓/✗ with next command. Silent success is a bug. |
| 14 | **Documentation** | Task-shaped (`docs/SMOKE.md`, `docs/plans/*`), searchable, concrete steps — not essay dumps in the UI. |
| 15 | **(Conflict awareness)** | Discoverability vs simplicity, flexibility vs equity, consistency with Photos vs consistency with our older CLI — decide explicitly in the PR/review packet. |

## Heuristic evaluation checklist (use on every UX PR)

For the changed surface, score each principle **Pass / Gap / N/A** in one line:

1. Can a new user see what they can do next without reading docs?
2. Is anything on screen competing with the one decision that matters?
3. Does the control look like what it does?
4. Do words map to Photos outcomes?
5. Is progress / error / selection state perceptible within a few seconds?
6. Same terms as last week’s CLI?
7. Keyboard *and* mouse for review?
8. Recovery instructions don’t assume “you already know Terminal”?
9. Large sets stay comfortable?
10. Clear sequence of steps?
11. Dangerous actions constrained?
12. Undo/recover path obvious after a mistake?
13. Feedback arrives before the user re-clicks?
14. Docs answer the task they are in?

## “Live” = perceptibility + feedback (not git)

When we say **live**, we mean the user’s Mac **right now**. Check in Terminal:

```sh
cat ~/.haymish/jobs/reindex-status.json          # process claim
tail -20 ~/.haymish/index.log                    # durable progress (authoritative)
tail -20 "$(python3 -c 'import json;print(json.load(open("/Users/caitlineverett/.haymish/jobs/reindex-status.json"))["log"])')"
uv run haymish doctor                            # ✓/✗ with next action
curl -s http://localhost:11434/api/ps            # what’s actually loaded
```

| Signal | Healthy | Unhealthy |
|--------|---------|-----------|
| `reindex-status.json` `state` | `running` | `exited` / missing pid |
| `index.log` `N/total · captioned=` | N increases | stuck / `ABORT` / rising `FAIL` |
| `ollama ps` | `qwen3-vl:*` (+ embed) | huge unrelated model hogging VRAM |
| `doctor` Index freshness | trends toward ✓ | forever ✗ with dead job |

Git commits / review packets are **not** live signals.

## Priority gaps (heuristic audit, 2026-09-21)

| Principle | Gap | Next slice |
|-----------|-----|------------|
| Perceptibility | Catch-up progress lives in log files; dashboard doesn’t show host index job | Surface `reindex-status.json` + last `index.log` line on dashboard Index panel |
| Feedback | Doctor ✗ Backup doesn’t link the propose-only comment-out recipe inline | Doctor output already proposes; keep Rich-escaped keys |
| Mapping | “Live” was agent jargon | Prefer “on your Mac” / “host job” in user-facing text |
| Discoverability | Keyboard shortcuts under-documented in UI | One-line hint near review grid (`Space` toggle, `a`/`n`) |
| Tolerance | Caption empty-`length` still slows overnight | Keep retry + catch-up warn path; monitor fail rate in `index.log` |
| Constraints | Live `rules.toml` still has archive/delete without backup | Human pastes `doctor --fix config` proposals |

Agents: when changing dashboard/CLI UX, cite the principle you optimized and any principle you knowingly traded off.
