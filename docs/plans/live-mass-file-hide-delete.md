# Live Mass File / Hide / Delete

**Status:** Planning  
**Last Updated:** 2026-09-21

## Objective

Enable safe live mass operations against Apple Photos through Haymish:
- **File (tag/album):** Additive, reversible, lowest risk
- **Hide:** Additive-ish (assets remain, visibility changes), medium risk
- **Delete:** Destructive, highest risk—never mass-autonomous

Operations must follow the **dry-run → review → apply** ladder with blast-radius caps.

---

## Non-Negotiable Safety Invariants

From Haymish `AGENTS.md`:

1. **Never write directly to the Photos database.** Read through `osxphotos`; mutate through supported Photos automation APIs (PhotoKit/AppleScript).
2. **Automated tests must never touch the real Photos library**, `~/.haymish/catalog.db`, or a real backup volume.
3. **Scheduled work may stage a reviewed deletion candidate but must never finalize deletion.**
4. **Final deletion requires:** (a) complete independently verified backup manifest, (b) typed confirmation, (c) macOS system dialog.
5. **Protection and whole-shoot retention judgments veto hide and deletion staging.**
6. **Culling is not deletion.** `pick`, `alternate`, `not selected`, `technical review`, and `deletion candidate` are distinct states.
7. **Similarity alone never implies redundancy.**
8. **Do not claim safety beyond what code and live verification prove.**

---

## Risk Tiers & Blast-Radius Caps

| Operation | Risk | Mass-Auto OK? | Per-Batch Cap | Notes |
|-----------|------|---------------|---------------|-------|
| Add keyword/tag | Low | Yes | 500 | Fully reversible via remove-keyword |
| Add to album | Low | Yes | 500 | Fully reversible via remove-from-album |
| Remove from album | Medium | Yes (reviewed) | 100 | Does not delete asset |
| Hide | Medium | Yes (reviewed) | 50 | Reversible via unhide; needs Terminal TCC |
| Unhide (recover) | Low | Yes | 200 | Restores visibility |
| Stage deletion | Medium | Reviewed only | 20 | Moves to staging, no finalize |
| Finalize delete | **NEVER** | **NO** | 1 | Human-only, backup-gated |

---

## Technical Prerequisites

### Terminal TCC for PhotoKit

- PhotoKit mutation requires Terminal to have Full Disk Access or Photos entitlement.
- Check: `tccutil reset Photos com.apple.Terminal` or verify in System Preferences → Privacy → Photos.
- If TCC denied, operations fail silently or throw; detect and surface clearly.

### iCloud `ismissing` Handling

- Assets with `ismissing=True` (iCloud-only, not downloaded) must be **skipped** for mutation.
- Log skipped UUIDs; do not error the batch.
- Optionally queue for download-then-retry.

### recover-hidden Flow

- Hidden assets live in Photos' "Hidden" album.
- `recover-hidden` command must enumerate Hidden album → present for review → unhide selected.
- Never auto-unhide; always review.

---

## Phased Rollout

### Phase 1: Additive Tag/Album Only
- Enable `haymish apply --dry-run` for keywords and albums.
- Implement blast-radius caps (500/batch).
- Log all proposed changes to `~/.haymish/logs/apply-YYYY-MM-DD.jsonl`.
- No hide/delete.

### Phase 2: Hide with Review
- Enable `haymish hide --dry-run --reviewed-set <review-id>`.
- Require prior human review (dashboard or CLI review session).
- Cap: 50/batch.
- Implement `haymish recover-hidden` for reversal.
- TCC check before attempt.

### Phase 3: Staged Deletion
- Enable `haymish stage-delete --dry-run --reviewed-set <review-id>`.
- Reviewed candidates only; cap: 20/batch.
- Staged assets move to "Deletion Candidates" smart album, not trash.
- No finalize command in CLI yet.

### Phase 4: Finalize Delete (Human-Only)
- `haymish finalize-delete` requires:
  1. Complete backup manifest verification
  2. Typed confirmation (`DELETE <n> ASSETS`)
  3. macOS system dialog (Photos app prompt)
- Never batch > 1 at a time without re-confirmation.
- Never automated.

---

## Validation Gates

| Gate | Check | Blocks |
|------|-------|--------|
| TCC-Photos | PhotoKit entitlement valid | All mutations |
| Library-lock | No concurrent Photos.app edits | All mutations |
| Dry-run log | `--dry-run` produced valid JSONL | Apply step |
| Review-set match | Review ID matches proposed set hash | Hide, Stage-delete |
| Backup manifest | All staged UUIDs in verified backup | Finalize-delete |
| Typed confirm | User types exact confirmation string | Finalize-delete |
| macOS dialog | System prompt accepted | Finalize-delete |

---

## Agent CAN vs MUST NOT

### Agents CAN:
- Run `haymish apply --dry-run` and report results
- Generate review sets from queries
- Log proposed changes
- Check TCC status
- Verify backup manifest exists (read-only)
- Execute reviewed additive operations (tag/album) up to cap

### Agents MUST NOT:
- Execute hide without prior human-reviewed set
- Execute any delete (stage or finalize)
- Bypass dry-run step
- Exceed blast-radius caps
- Modify backup verification logic
- Auto-retry failed mutations without human review
- Touch assets with `ismissing=True`

---

## Host / Human Gates

| Operation | Host (Terminal) | Cursor Agent | Human Required |
|-----------|-----------------|--------------|----------------|
| Dry-run any | ✅ | ✅ | ❌ |
| Apply tag/album | ✅ | ✅ (reviewed) | Review only |
| Hide | ✅ | ❌ | ✅ Review + confirm |
| Stage delete | ✅ | ❌ | ✅ Review + confirm |
| Finalize delete | ✅ | ❌ | ✅ Typed + dialog |
| Backup verify | ✅ | Read-only | ✅ Owns backup |
| TCC grant | ❌ | ❌ | ✅ System Prefs |

---

## Rollback Procedures

| Operation | Rollback Method | Automation OK? |
|-----------|-----------------|----------------|
| Add keyword | `haymish apply --remove-keyword <kw>` | Yes |
| Add to album | `haymish apply --remove-from-album <album>` | Yes |
| Hide | `haymish recover-hidden --review-id <id>` | Review required |
| Stage delete | Remove from "Deletion Candidates" album | Yes |
| Finalize delete | **Restore from backup** | Human-only |

---

## Execution Mode Comparison

| Aspect | Cursor Agent | Host Terminal | Human-Only |
|--------|--------------|---------------|------------|
| Dry-run generation | ✅ Best | ✅ Good | ❌ Tedious |
| Apply additive (tag/album) | ✅ Good w/caps | ✅ Best | ❌ Slow |
| Hide operations | ❌ Prohibited | ✅ w/review | ✅ Required |
| Stage delete | ❌ Prohibited | ✅ w/review | ✅ Required |
| Finalize delete | ❌ Prohibited | ❌ Prohibited | ✅ Only option |
| TCC setup | ❌ Cannot | ❌ Cannot | ✅ Required |
| Overnight batch | ❌ Too fragile | ✅ Best | ❌ Impractical |

**Recommendation:** Use Cursor agents for dry-run generation and additive-only apply. Host Terminal for hide/stage-delete with human review. Human-only for finalize delete and TCC setup.

---

## Concrete Next Command for Caitlin

```bash
# Verify TCC status and test dry-run on small set
cd ~/dev/haymish && uv run python -c "
from haymish.effects import check_photokit_entitlement
print('TCC OK:', check_photokit_entitlement())
" && uv run haymish apply --dry-run --query 'keyword:test-batch' --add-keyword 'haymish-test' --limit 10
```

If TCC fails: System Preferences → Privacy & Security → Photos → Enable Terminal.

---

## Open Questions

1. Should `recover-hidden` auto-generate a review set or require explicit query?
2. Batch size caps: are 500/100/50/20 appropriate for this library size?
3. Should failed mutations retry automatically or require human re-review?
