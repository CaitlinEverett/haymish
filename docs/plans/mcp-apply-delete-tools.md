# MCP Apply / Delete Tools

**Status:** Planning  
**Last Updated:** 2026-09-21

## Objective

Define the policy for MCP tool exposure in Haymish. Currently `haymish_find` is **propose-only**—it queries and returns results but cannot mutate. This document establishes:
- Why propose-only should remain the default for longer
- Under what conditions apply tools might be added
- Why delete tools should be the absolute last capability added (if ever)
- Required safeguards for any mutation tools

---

## Non-Negotiable Safety Invariants

From Haymish `AGENTS.md`:

1. **Never write directly to the Photos database.** Mutate through supported Photos automation APIs.
2. **Scheduled work may discover, enrich, notify, apply explicitly trusted additive effects, or stage a reviewed deletion candidate. It must never finalize deletion.**
3. **Final deletion requires a complete independently verified backup manifest, typed confirmation, and the macOS system dialog.**
4. **Protection and whole-shoot retention judgments veto hide and deletion staging.**
5. **Do not claim safety beyond what code and live verification prove.**

**Additional MCP-specific invariants:**

6. **No delete tools exposed via MCP by default.** Delete requires out-of-band confirmation.
7. **Apply tools limited to additive trusted effects.** Tag/album add only; no remove/hide/delete.
8. **All mutations logged to audit ledger.** Every MCP-initiated change is recorded.
9. **Rate limits enforced.** Prevent runaway automation.

---

## Current State: Propose-Only

### What `haymish_find` Does

```python
# Current MCP tool
@mcp_tool
def haymish_find(query: str, limit: int = 100) -> list[AssetSummary]:
    """Find assets matching query. Returns metadata only, no mutations."""
    return catalog.query(query, limit=limit)
```

### Why Propose-Only is Correct for Now

1. **Trust establishment:** Haymish is in early dogfood. Query accuracy must be proven before mutation is trusted.
2. **Blast radius:** A buggy query in propose-only shows wrong results. A buggy query with apply attached to it hides/deletes wrong photos.
3. **Human review loop:** Propose → review → apply-via-CLI is the safe pattern. MCP shortcuts this.
4. **Agent behavior:** LLM agents may call tools repeatedly, optimistically, or incorrectly. Read-only limits damage.
5. **Auditability:** Propose-only means the audit trail lives in chat transcripts. Mutations need a separate ledger.

---

## If Apply Tools Were Added

### Permitted Apply Operations (Additive Only)

| Operation | MCP Eligible? | Notes |
|-----------|---------------|-------|
| Add keyword | ✅ Potentially | Fully reversible |
| Add to album | ✅ Potentially | Fully reversible |
| Remove keyword | ❌ No | Destructive-ish |
| Remove from album | ❌ No | Destructive-ish |
| Hide | ❌ No | Requires human review |
| Unhide | ❌ No | Should review what's hidden first |
| Stage delete | ❌ No | Requires backup verification |
| Finalize delete | ❌ Never | Human-only |

### Required Safeguards

1. **Explicit human confirmation channel:**
   - MCP tool proposes change
   - Returns confirmation token
   - Human must approve token via separate channel (CLI, dashboard)
   - Only then does mutation execute

2. **Rate limits:**
   ```python
   RATE_LIMITS = {
       "add_keyword": {"per_minute": 100, "per_hour": 500},
       "add_to_album": {"per_minute": 50, "per_hour": 200},
   }
   ```

3. **Audit ledger:**
   ```json
   {
     "timestamp": "2026-09-21T14:30:00Z",
     "tool": "haymish_apply",
     "operation": "add_keyword",
     "keyword": "vacation",
     "assets": ["uuid1", "uuid2"],
     "agent_session": "cursor-abc123",
     "confirmation_token": "tok_xyz",
     "confirmed_by": "cli",
     "result": "success"
   }
   ```

4. **Batch limits:**
   - Max 100 assets per MCP call
   - Requires explicit `--large-batch` flag for > 50

5. **Dry-run default:**
   - All apply tools default to `dry_run=True`
   - Must explicitly pass `dry_run=False` to execute

### Hypothetical Apply Tool

```python
@mcp_tool
def haymish_apply(
    operation: Literal["add_keyword", "add_to_album"],
    target: str,  # keyword name or album name
    assets: list[str],  # UUIDs
    dry_run: bool = True,
    confirmation_token: str | None = None,
) -> ApplyResult:
    """
    Apply additive operation to assets.
    
    If dry_run=True (default), returns what would change.
    If dry_run=False, requires valid confirmation_token from prior proposal.
    """
    if len(assets) > 100:
        raise ValueError("Batch too large for MCP")
    
    if not dry_run and not verify_confirmation_token(confirmation_token):
        raise ValueError("Invalid or expired confirmation token")
    
    # ... execute with full audit logging
```

---

## Why Delete MCP Tools Are Last (If Ever)

### The Delete Escalation Ladder

1. **Query/Propose** ← We are here
2. **Additive apply (tag/album)** ← Next possible step
3. **Reversible removal (remove from album)** ← Requires more trust
4. **Hide** ← Significant, but recoverable
5. **Stage delete** ← Requires backup verification
6. **Finalize delete** ← Never via MCP

### Why Delete Tools Should Not Exist in MCP

1. **LLM hallucination risk:** An agent might "helpfully" delete duplicates that aren't duplicates.
2. **Prompt injection:** Malicious content in photo metadata could influence agent behavior.
3. **No confirmation UX:** MCP has no native "Are you sure?" dialog.
4. **Blast radius:** A single bad MCP call could queue hundreds of photos for deletion.
5. **Backup gate bypass temptation:** Pressure to skip verification "just for MCP convenience."
6. **Irreversibility:** Even staged deletion is hard to fully undo if the agent proceeds quickly.

### The Only Acceptable Delete Pattern

If delete-adjacent MCP tools ever exist:

```python
@mcp_tool
def haymish_propose_deletion_review(
    query: str,
    reason: str,
) -> DeletionProposal:
    """
    Propose assets for human deletion review.
    
    Returns a proposal ID. Human must:
    1. Review proposal in dashboard
    2. Verify backup for each asset
    3. Confirm via CLI with typed confirmation
    4. Accept macOS dialog
    
    MCP cannot proceed past proposal.
    """
    # Creates proposal record, returns ID
    # Human takes over from here
```

This is **not** a delete tool. It's a "create a task for human" tool.

---

## Phased Rollout (Conservative)

### Phase 1: Propose-Only (Current)
- `haymish_find` returns results
- No mutations via MCP
- Audit: chat transcripts only

### Phase 2: Dry-Run Apply
- Add `haymish_apply` with `dry_run=True` only
- Returns what would change
- No actual mutations yet
- Audit: proposals logged

### Phase 3: Confirmed Additive Apply
- Enable `dry_run=False` for add keyword/album
- Requires confirmation token from CLI
- Rate limits active
- Full audit ledger

### Phase 4: Extended Additive
- Enable remove-from-album (with confirmation)
- Still no hide/delete

### Phase 5: Never
- No hide via MCP
- No stage-delete via MCP
- No finalize-delete via MCP

---

## Validation Gates

| Gate | Check | Blocks |
|------|-------|--------|
| Rate limit | Under per-minute and per-hour caps | All apply tools |
| Batch size | ≤ 100 assets | All apply tools |
| Confirmation token | Valid, unexpired, matches proposal | Actual mutations |
| Operation allowed | In permitted set (additive only) | All apply tools |
| Audit write | Ledger entry created before execution | All apply tools |
| Dry-run check | If `dry_run=False`, token required | All apply tools |

---

## Agent CAN vs MUST NOT

### Agents CAN:
- Call `haymish_find` freely (read-only)
- Propose changes via dry-run apply tools
- Read audit ledger
- Check rate limit status

### Agents MUST NOT:
- Execute mutations without confirmation token
- Bypass rate limits
- Call any hide/delete tools (they don't exist)
- Create confirmation tokens (human-only)
- Modify audit ledger
- Disable dry-run default

---

## Host / Human Gates

| Operation | MCP (Agent) | Host CLI | Human Required |
|-----------|-------------|----------|----------------|
| Find/query | ✅ | ✅ | ❌ |
| Dry-run apply | ✅ | ✅ | ❌ |
| Confirmed add tag/album | ✅ w/token | ✅ | ✅ Token generation |
| Remove from album | ❌ | ✅ | ✅ Review |
| Hide | ❌ | ✅ | ✅ Review |
| Stage delete | ❌ | ✅ | ✅ Backup verify |
| Finalize delete | ❌ | ❌ | ✅ Typed + dialog |
| Read audit ledger | ✅ | ✅ | ❌ |
| Clear rate limits | ❌ | ❌ | ✅ Manual |

---

## Execution Mode Comparison

| Aspect | MCP (Agent) | Host CLI | Human-Only |
|--------|-------------|----------|------------|
| Bulk queries | ✅ Best | ✅ Good | ❌ Tedious |
| Explore results | ✅ Best | ✅ Good | ❌ Slow |
| Additive apply | ⚠️ w/confirmation | ✅ Best | ❌ Slow |
| Destructive apply | ❌ Prohibited | ✅ w/review | ✅ Required |
| Delete operations | ❌ Prohibited | ❌ Prohibited | ✅ Only |
| Audit review | ✅ Good | ✅ Good | ✅ Required for delete |
| Rate limit bypass | ❌ Cannot | ❌ Should not | ⚠️ Exceptional |

**Recommendation:** Keep MCP propose-only for the foreseeable future. Use Host CLI for any mutations. Human-only for anything destructive.

---

## Rollback Procedures

| Scenario | Rollback Method | Notes |
|----------|-----------------|-------|
| Bad query results | None needed | Read-only |
| Bad dry-run proposal | Discard proposal | No mutations occurred |
| Bad confirmed apply | Reverse operation via CLI | Keywords/albums reversible |
| Rate limit hit | Wait for window reset | Or human override |
| Audit corruption | Restore from backup | Audit is append-only |

---

## Why Propose-Only Should Stay Longer

### Trust Accumulation

Before adding apply tools, we need evidence that:

1. **Query accuracy is high:** `haymish_find` returns what users expect
2. **Agent behavior is predictable:** Agents don't call tools unexpectedly
3. **Audit infrastructure is solid:** Every action is logged and reviewable
4. **Rate limits work:** Tested under load
5. **Confirmation flow is smooth:** Token generation → approval → execution works

### Milestones Before Apply Tools

- [ ] 100+ successful query sessions without false positives
- [ ] Audit ledger schema finalized and tested
- [ ] Rate limit implementation verified
- [ ] Confirmation token flow implemented in CLI
- [ ] Dashboard can display pending confirmations
- [ ] Rollback procedures tested for each operation type

---

## Concrete Next Command for Caitlin

```bash
# Review current MCP tool exposure and audit logging:
cd ~/dev/haymish && uv run python -c "
from haymish.mcp import get_exposed_tools, get_audit_config

print('Exposed MCP tools:')
for tool in get_exposed_tools():
    print(f'  - {tool.name}: {tool.description[:60]}...')

print()
print('Audit config:')
print(f'  Ledger path: {get_audit_config().ledger_path}')
print(f'  Enabled: {get_audit_config().enabled}')
"

# If audit isn't configured yet, that's the first step before any apply tools.
```

---

## Open Questions

1. Should confirmation tokens expire? (Proposed: 1 hour)
2. Should rate limits be per-agent-session or global?
3. Is there ever a case for read-modify-write operations via MCP?
4. Should the audit ledger be a separate database or table in catalog.db?
5. How to handle MCP calls when Photos.app is open and may conflict?
