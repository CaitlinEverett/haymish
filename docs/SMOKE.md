# Haymish smoke checklist

Short manual pass after changes that touch the daemon, review UI, or Photos
mutations. Run from the repo with the project venv (`uv sync --extra dev`).

## Safety (read first)

- Haymish never writes the Photos database directly; mutations go through supported
  Photos automation APIs only.
- **Do not enable deletion or finalize staged deletes** in this smoke path. Final
  deletion requires a complete backup manifest, typed confirmation, and the macOS
  system dialog — there is no bypass.
- Starter rules are additive (albums/keywords). Hide/archive/delete stages stay off
  until you have run a reviewed canary and verified `undo`.
- Automated tests must not use your real library or `~/.haymish/catalog.db`; this
  checklist is for **your** machine only.

## Permissions (TCC)

Run commands from **Terminal.app** (or another app with Full Disk Access) when
PhotoKit hide/recover actions are involved. Cursor often lacks Photos TCC even when
Terminal works — `haymish doctor` calls out the host app to grant.

1. System Settings → Privacy & Security → **Full Disk Access** → enable for Terminal
   (required to read the Photos library).
2. On first hide/album/keyword apply, approve **Photos** automation when macOS prompts.

## 1. Doctor

```sh
cd /path/to/haymish
uv run haymish doctor
```

- Fix every ✗ before continuing (library path, Ollama, index freshness, permissions).
- If index/caption freshness fails:

```sh
uv run haymish doctor --fix index
# or:
uv run haymish index --catch-up-captions
```

Re-run `uv run haymish doctor` until checks pass or only expected warnings remain.

## 2. Review UI (one subgroup)

```sh
uv run haymish review
# or a narrow rule, e.g.:
# uv run haymish review screenshots-general
```

In the dashboard:

1. Confirm server-clustered rules show **subgroups folded** by default (headers only,
   no thumbnail grid until you expand).
2. Expand one subgroup (**show group**). At most **48** cards render; use **Previous /
   Next** if the group is larger. Thumbnails use `loading="lazy"`.
3. Uncheck one obvious false positive; note the selected count includes picks on other
   pages/folded groups (not only visible cards).
4. **Apply** a small, trusted subset (or dry-run via report-only rules if configured).

If grouping is missing for a large flat queue, run caption catch-up (step 1) and
rebuild the queue.

## 3. Undo (Terminal)

If Apply changed the library and something looks wrong:

```sh
uv run haymish undo
```

Confirm supported album/keyword/hide actions reverse. Archive copies are intentionally
not removed by undo.

## 4. Recover hidden (optional)

Only if this smoke run used a **hide** stage and you need to restore visibility:

```sh
uv run haymish recover-hidden --help
uv run haymish recover-hidden --run-id <run-id>
# and/or: uv run haymish recover-hidden <uuid> ...
```

Prefer Terminal for PhotoKit access. See `haymish doctor` if recover fails with a
permissions error.

## Done when

- Doctor passes (or documented known gaps).
- One subgroup review: fold → expand → page → lazy thumbs → apply or report-only.
- `undo` (and `recover-hidden` if used) behave as expected with no surprise rejects
  from off-page or folded candidates.

## Deferred (do not improvise in smoke)

Longer ladders live under `docs/plans/`:

- `live-mass-file-hide-delete.md`
- `backup-archive-delete-dogfood.md`
- `overnight-full-library-reindex.md`
- `mcp-apply-delete-tools.md`
