# Overnight Full-Library Reindex

**Status:** Planning  
**Last Updated:** 2026-09-21

## Objective

Run a complete library reindex as a **host job** (launchd/cron), not a Cursor agent session. The job must:
- Degrade gracefully if captioning fails (continue with `--no-captions`)
- Support both catch-up (incremental) and full rebuild modes
- Log comprehensively for post-hoc review
- Resume from interruption without re-processing completed work
- Be wire-able and launch-able by agents, but not babysit-able overnight

---

## Non-Negotiable Safety Invariants

From Haymish `AGENTS.md`:

1. **Automated tests must never touch the real Photos library**, `~/.haymish/catalog.db`, or a real backup volume.
2. **Expensive work is keyed by library, asset, processor version, and input fingerprint. Unchanged completed work must not rerun.**
3. **Effects are keyed by collection/disposition revision and target fingerprint. An already satisfied effect should be verified or skipped, not blindly repeated.**
4. **Run at most one blocking long-duration subprocess per control loop.**
5. **Persist the exact prompt and output location before launch. Poll status separately.**
6. **Treat timeout, disconnect, or UI hang as indeterminate, not failed.**

---

## Job Architecture

### Why Host Job, Not Agent

| Aspect | Cursor Agent | Host Job (launchd) |
|--------|--------------|-------------------|
| Session persistence | ❌ UI/transport can disconnect | ✅ Survives logout |
| Resource contention | ❌ Competes with editor | ✅ Dedicated resources |
| Overnight operation | ❌ Fragile, needs babysitting | ✅ Fire and forget |
| Progress monitoring | ✅ Interactive | ✅ Logs + status file |
| Error recovery | ❌ May retry incorrectly | ✅ Idempotent resume |
| Cost | ✅ Cursor credits | ✅ Free (local compute) |

### Job Components

```
~/.haymish/
├── jobs/
│   ├── reindex.sh              # Main script
│   ├── reindex.plist           # launchd job definition
│   └── reindex-status.json     # Current run status
├── logs/
│   └── reindex-YYYY-MM-DD.log  # Daily log files
└── catalog.db                  # The database being updated
```

---

## Graceful Degradation

### Captioning Failure Handling

The reindex may include AI-powered captioning (local Ollama or remote API). If captioning fails:

1. **Detect failure:** Captioning timeout, model unavailable, or error rate > 10%
2. **Log clearly:** `WARN: Captioning unavailable, degrading to --no-captions`
3. **Continue:** Process all other metadata (EXIF, faces, duplicates, etc.)
4. **Mark incomplete:** Set `captions_complete: false` in status
5. **Queue retry:** Next run attempts captioning for uncaptioned assets

```bash
# Degradation logic in reindex.sh
if ! haymish index --test-captioning --timeout 30; then
  echo "WARN: Captioning unavailable, continuing without"
  CAPTION_FLAG="--no-captions"
else
  CAPTION_FLAG=""
fi

haymish index --full $CAPTION_FLAG
```

### Other Degradation Scenarios

| Failure | Degradation | Continues? |
|---------|-------------|------------|
| Captioning unavailable | `--no-captions` | ✅ Yes |
| Ollama not running | Skip embeddings | ✅ Yes |
| Disk space < 1GB | Pause, alert | ❌ No |
| Photos library locked | Retry 3x, then fail | ❌ No |
| Network unavailable | Skip remote models | ✅ Yes |

---

## Catch-Up vs Full Rebuild

### Catch-Up (Incremental) Mode

```bash
haymish index --catch-up
```

- Processes only assets modified since last successful run
- Uses `last_indexed_at` timestamp per asset
- Typical runtime: 5-30 minutes
- Default for scheduled runs

### Full Rebuild Mode

```bash
haymish index --full --rebuild-cache
```

- Reprocesses all assets regardless of cache
- Rebuilds similarity index, embeddings, and derived data
- Typical runtime: 4-12 hours for large library
- Use when: processor version changes, corruption suspected, major config change

### Mode Selection Logic

```bash
# In reindex.sh
if [ "$1" = "--full" ]; then
  MODE="--full --rebuild-cache"
elif [ -f ~/.haymish/force-full-rebuild ]; then
  MODE="--full --rebuild-cache"
  rm ~/.haymish/force-full-rebuild
else
  MODE="--catch-up"
fi
```

---

## Logging and Monitoring

### Log Format

```
2026-09-21T02:30:00 INFO  Starting reindex (catch-up mode)
2026-09-21T02:30:01 INFO  Library: /Users/caitlin/Pictures/Photos Library.photoslibrary
2026-09-21T02:30:01 INFO  Assets to process: 1,247 (modified since 2026-09-20T02:30:00)
2026-09-21T02:30:15 INFO  Phase 1/4: EXIF extraction (1247/1247)
2026-09-21T02:35:22 INFO  Phase 2/4: Similarity hashing (1247/1247)
2026-09-21T02:40:18 WARN  Captioning unavailable, degrading to --no-captions
2026-09-21T02:40:18 INFO  Phase 3/4: Skipped (no captions)
2026-09-21T02:45:00 INFO  Phase 4/4: Database commit
2026-09-21T02:45:02 INFO  Completed: 1247 assets, 0 errors, 15m02s
```

### Status File

```json
{
  "run_id": "reindex-2026-09-21-0230",
  "started_at": "2026-09-21T02:30:00Z",
  "status": "running",
  "mode": "catch-up",
  "progress": {
    "phase": "similarity",
    "processed": 847,
    "total": 1247
  },
  "degradations": ["captions"],
  "pid": 12345,
  "log_path": "~/.haymish/logs/reindex-2026-09-21.log"
}
```

### Monitoring Commands

```bash
# Check if running
haymish index --status

# Tail live log
tail -f ~/.haymish/logs/reindex-$(date +%Y-%m-%d).log

# Check last run result
cat ~/.haymish/jobs/reindex-status.json | jq '.status, .completed_at'
```

---

## Resume from Interruption

### Checkpoint Strategy

- Checkpoint after each phase (EXIF, similarity, captions, commit)
- Checkpoint every 100 assets within a phase
- Checkpoint file: `~/.haymish/jobs/reindex-checkpoint.json`

### Resume Logic

```bash
# On start, check for incomplete run
if haymish index --has-checkpoint; then
  echo "Resuming from checkpoint..."
  haymish index --resume
else
  haymish index $MODE
fi
```

### Checkpoint File

```json
{
  "run_id": "reindex-2026-09-21-0230",
  "phase": "similarity",
  "last_processed_uuid": "ABC123...",
  "processed_count": 500,
  "remaining_uuids": ["DEF456...", "..."]
}
```

---

## Phased Rollout

### Phase 1: Manual Host Execution
- Create `reindex.sh` script with degradation logic
- Test catch-up mode manually
- Verify checkpoint/resume works

### Phase 2: Scheduled Execution
- Create launchd plist for nightly runs
- Configure to run at 2:30 AM
- Test with `launchctl load`

### Phase 3: Agent Integration
- Agent can run `haymish index --status` to check
- Agent can create `~/.haymish/force-full-rebuild` to request full
- Agent can read logs for diagnostics
- Agent cannot start/stop/babysit the job

### Phase 4: Alerting
- On failure, write to `~/.haymish/alerts/`
- Optional: send notification via terminal-notifier
- Agent can read alerts and surface to Caitlin

---

## Validation Gates

| Gate | Check | Blocks |
|------|-------|--------|
| No concurrent run | PID file not locked | Job start |
| Library accessible | Photos library path exists | Job start |
| Disk space | > 1GB free | Job start |
| Checkpoint valid | If resuming, checkpoint file valid | Resume |
| Database writable | Can acquire catalog.db lock | Commit phase |

---

## Agent CAN vs MUST NOT

### Agents CAN:
- Check job status via `haymish index --status`
- Read log files for diagnostics
- Request full rebuild via flag file
- Wire up the script and plist (create/edit)
- Test individual index commands interactively
- Report indexing statistics

### Agents MUST NOT:
- Run full overnight index synchronously (will timeout)
- Kill running index jobs
- Modify checkpoint files
- Edit database directly during index
- Babysit long-running jobs (no polling loops)
- Start jobs that will outlive the agent session without handoff

---

## Host / Human Gates

| Operation | Host (Terminal) | Cursor Agent | Human Required |
|-----------|-----------------|--------------|----------------|
| Run catch-up index | ✅ | ✅ (if quick) | ❌ |
| Run full rebuild | ✅ Best | ❌ Too long | ❌ |
| Check status | ✅ | ✅ | ❌ |
| Read logs | ✅ | ✅ | ❌ |
| Start scheduled job | ✅ launchctl | ❌ | ❌ |
| Stop scheduled job | ✅ launchctl | ❌ | ✅ Review |
| Create plist | ✅ | ✅ (wire only) | ✅ Review |
| Force full rebuild | ✅ | ✅ (flag file) | ❌ |

---

## Execution Mode Comparison

| Aspect | Cursor Agent | Host Terminal | Human-Only |
|--------|--------------|---------------|------------|
| Quick catch-up (< 30m) | ✅ Acceptable | ✅ Better | ❌ Overkill |
| Full rebuild (4-12h) | ❌ Will timeout | ✅ Best | ❌ Impractical |
| Wire up scripts | ✅ Best | ✅ Good | ❌ Tedious |
| Debug failures | ✅ Good | ✅ Good | ❌ Slow |
| Monitor overnight | ❌ Cannot | ✅ Logs only | ❌ Sleep |
| launchd management | ❌ Should not | ✅ Best | ✅ Review |

**Recommendation:** Use Cursor agents to wire up scripts and debug issues. Use Host Terminal (launchd) for actual overnight execution. Human reviews plist before loading.

---

## Rollback Procedures

| Scenario | Rollback Method | Notes |
|----------|-----------------|-------|
| Bad index run | `haymish index --rollback-to <timestamp>` | If catalog has backups |
| Corrupted checkpoint | Delete checkpoint file, restart | Loses partial progress |
| Wrong processor version | Force full rebuild | May take hours |
| Scheduled job broken | `launchctl unload`, fix plist | Human intervention |

---

## Concrete Next Command for Caitlin

```bash
# 1. Create the job script and test manually:
cd ~/dev/haymish && cat > ~/.haymish/jobs/reindex.sh << 'EOF'
#!/bin/bash
set -euo pipefail
cd ~/dev/haymish
LOG=~/.haymish/logs/reindex-$(date +%Y-%m-%d).log
exec >> "$LOG" 2>&1

echo "$(date -Iseconds) INFO Starting reindex"

# Test captioning availability
if ! uv run haymish index --test-captioning --timeout 30 2>/dev/null; then
  echo "$(date -Iseconds) WARN Captioning unavailable, degrading"
  CAP_FLAG="--no-captions"
else
  CAP_FLAG=""
fi

# Check for full rebuild request
if [ -f ~/.haymish/force-full-rebuild ]; then
  MODE="--full"
  rm ~/.haymish/force-full-rebuild
else
  MODE="--catch-up"
fi

uv run haymish index $MODE $CAP_FLAG
echo "$(date -Iseconds) INFO Completed"
EOF
chmod +x ~/.haymish/jobs/reindex.sh

# 2. Test it:
~/.haymish/jobs/reindex.sh
```

After testing, create the launchd plist for scheduled execution.

---

## Open Questions

1. What time should scheduled runs occur? (2:30 AM proposed)
2. Should failed runs retry automatically or wait for next scheduled time?
3. How many days of logs to retain?
4. Should captioning be a separate job or integrated with reindex?
