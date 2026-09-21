# Backup / Archive / Delete Dogfood Enablement

**Status:** Planning  
**Last Updated:** 2026-09-21

## Objective

Enable safe dogfooding of the backup → archive → delete pipeline with complete verification at every stage. No photo is deleted without:
1. Independently verified backup manifest confirming the asset exists in backup
2. Typed confirmation from Caitlin
3. macOS system dialog acceptance

This plan treats backup verification as the **hard gate**—if backup cannot be verified, delete is blocked.

---

## Non-Negotiable Safety Invariants

From Haymish `AGENTS.md`:

1. **Final deletion requires a complete independently verified backup manifest, typed confirmation, and the macOS system dialog. Do not add or restore a backup bypass.**
2. **Protection and whole-shoot retention judgments veto hide and deletion staging. Making an additional backup copy remains allowed.**
3. **Scheduled work may stage a reviewed deletion candidate but must never finalize deletion.**
4. **Culling is not deletion.** Distinct states: `pick`, `alternate`, `not selected`, `technical review`, `deletion candidate`.
5. **Similarity alone never implies redundancy.**
6. **Do not claim backup completeness beyond what code and live verification prove.**

---

## Backup Manifest Requirements

### What "Independently Verified" Means

The backup manifest is **not** just a file list. It must:

1. **Hash verification:** SHA-256 of backed-up file matches original asset fingerprint
2. **Path verification:** Asset exists at expected backup path
3. **Freshness check:** Backup timestamp ≥ asset modification timestamp
4. **Independent read:** Verification reads from backup volume, not catalog cache
5. **Volume identity:** Backup volume UUID matches expected (no wrong-disk)

### Manifest Schema

```json
{
  "manifest_version": "1.0",
  "library_uuid": "...",
  "backup_volume_uuid": "...",
  "backup_root": "/Volumes/Backup/Photos",
  "verified_at": "2026-09-21T14:30:00Z",
  "assets": [
    {
      "uuid": "ABC123",
      "original_path": "/path/in/library",
      "backup_path": "/Volumes/Backup/Photos/2024/...",
      "sha256": "deadbeef...",
      "verified": true
    }
  ]
}
```

### Verification Command

```bash
haymish backup verify --volume /Volumes/Backup --assets <uuid-list-or-query>
```

Returns exit code 0 only if ALL assets verified. Any failure = exit 1.

---

## Protection and Whole-Shoot Vetoes

### Protection Flags

Assets with these attributes **cannot be staged for deletion**:
- `protected: true` (explicit protection)
- `favorite: true` (Photos favorite)
- `in_album: <protected-album>` (e.g., "Keep Forever")
- `has_keyword: protected` or similar protection keywords

### Whole-Shoot Retention

If a deletion candidate is part of a shoot where:
- Another asset from same shoot is `pick` or `protected`
- Shoot policy says "keep all alternates"
- Shoot has < N assets remaining after proposed deletion

Then the deletion is **vetoed**. The veto is logged; Caitlin can override with explicit `--override-shoot-veto` but this resets the review requirement.

---

## Phased Rollout

### Phase 1: Backup Manifest Generation
- Implement `haymish backup scan --volume <path>` to generate manifest
- Hash verification for each asset
- Store manifest in `~/.haymish/manifests/<volume-uuid>-<timestamp>.json`
- No delete capability yet

### Phase 2: Backup Verification Gate
- Implement `haymish backup verify --manifest <path> --assets <query>`
- Returns clear pass/fail per asset
- Integrate with `haymish stage-delete` to require verification

### Phase 3: Staged Deletion with Backup Gate
- `haymish stage-delete` requires `--verified-manifest <path>`
- Manifest must be < 24 hours old (configurable)
- All proposed assets must be in verified manifest
- Creates "Deletion Candidates" collection, not actual delete

### Phase 4: Finalize Delete (Human-Only)
- `haymish finalize-delete` flow:
  1. Re-verify manifest (must still be valid)
  2. Display summary: N assets, total size, backup paths
  3. Prompt: `Type "DELETE <N> ASSETS" to confirm:`
  4. On match, trigger Photos deletion (macOS dialog appears)
  5. Log result to audit trail

---

## Validation Gates

| Gate | Check | Blocks |
|------|-------|--------|
| Backup volume mounted | Volume UUID matches expected | All backup ops |
| Manifest exists | Valid JSON at expected path | Stage-delete |
| Manifest fresh | < 24h since verification | Finalize-delete |
| All assets verified | 100% of proposed set in manifest | Stage-delete |
| No protected assets | Protection flags clear | Stage-delete |
| No shoot vetoes | Whole-shoot policy passes | Stage-delete |
| Typed confirmation | Exact string match | Finalize-delete |
| macOS dialog | User clicks Delete in Photos | Finalize-delete |

---

## Agent CAN vs MUST NOT

### Agents CAN:
- Run `haymish backup scan` to generate manifests
- Run `haymish backup verify` (read-only check)
- Query which assets are protected
- Report backup coverage statistics
- List assets missing from backup

### Agents MUST NOT:
- Execute `haymish stage-delete`
- Execute `haymish finalize-delete`
- Modify protection flags without human review
- Override shoot vetoes
- Create/modify backup bypass logic
- Mark assets as "verified" without actual verification
- Delete or modify backup manifest files

---

## Host / Human Gates

| Operation | Host (Terminal) | Cursor Agent | Human Required |
|-----------|-----------------|--------------|----------------|
| Backup scan | ✅ | ✅ | ❌ |
| Backup verify | ✅ | ✅ (read-only) | ❌ |
| View protection status | ✅ | ✅ | ❌ |
| Stage delete | ✅ | ❌ | ✅ Review + confirm |
| Finalize delete | ✅ | ❌ | ✅ Typed + dialog |
| Override shoot veto | ✅ | ❌ | ✅ Explicit flag |
| Set protection flags | ✅ | ❌ | ✅ Review |
| Mount backup volume | ❌ | ❌ | ✅ Physical/Finder |

---

## Rollback Procedures

| Stage | Rollback Method | Notes |
|-------|-----------------|-------|
| Manifest generation | Delete manifest file | No side effects |
| Staged deletion | Remove from "Deletion Candidates" | Assets unchanged |
| Finalized deletion | **Restore from backup** | Human-only, use `haymish restore` |

### Restore Command

```bash
haymish restore --from-backup /Volumes/Backup --assets <uuid-list>
```

Imports assets back into Photos library. Metadata may be partially lost (keywords, albums need re-association).

---

## Execution Mode Comparison

| Aspect | Cursor Agent | Host Terminal | Human-Only |
|--------|--------------|---------------|------------|
| Backup scan | ✅ Good | ✅ Best (long-running) | ❌ Tedious |
| Backup verify | ✅ Good | ✅ Good | ❌ Tedious |
| Protection queries | ✅ Best | ✅ Good | ❌ Slow |
| Stage delete | ❌ Prohibited | ✅ w/human review | ✅ Required |
| Finalize delete | ❌ Prohibited | ❌ Prohibited | ✅ Only option |
| Mount backup volume | ❌ Cannot | ❌ Cannot | ✅ Required |
| Restore from backup | ❌ Prohibited | ✅ w/human review | ✅ Required |

**Recommendation:** Use Cursor agents for backup scanning and verification queries. Use Host Terminal for stage-delete with human review session. Human-only for finalize delete, volume mounting, and restore operations.

---

## Concrete Next Command for Caitlin

```bash
# 1. Mount backup volume in Finder first, then:
cd ~/dev/haymish && uv run haymish backup scan \
  --volume /Volumes/PhotosBackup \
  --output ~/.haymish/manifests/initial-scan.json \
  --progress

# 2. After scan completes, verify a small test set:
uv run haymish backup verify \
  --manifest ~/.haymish/manifests/initial-scan.json \
  --query 'date:2024-01-01..2024-01-07' \
  --verbose
```

If backup volume path differs, adjust `--volume`. Expected runtime for full library: 30-60 minutes depending on volume speed.

---

## Why Backup Bypass Must Never Exist

The temptation to add `--skip-backup-check` or similar is high when:
- Backup volume is temporarily unavailable
- "I know these are backed up elsewhere"
- Testing/development convenience

**This bypass must never be added because:**
1. Human memory of "backed up elsewhere" is unreliable
2. Testing convenience becomes production habit
3. Once bypass exists, pressure to use it in edge cases grows
4. A single use of bypass + accidental delete = permanent data loss
5. The 30-second inconvenience of mounting a volume is trivial vs. losing irreplaceable photos

The backup gate is the **last line of defense**. It must remain non-negotiable.

---

## Open Questions

1. Manifest freshness window: 24 hours appropriate, or should it be shorter for finalize?
2. Should partial verification (e.g., 95% verified) ever allow staging with warnings?
3. How to handle assets that exist in multiple backup locations?
4. Should restore operation preserve original UUIDs or generate new ones?
