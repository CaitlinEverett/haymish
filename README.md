# Haymish

*Haymish (Yiddish, from "heymish") — cozy, unpretentious, at ease. The goal: a camera roll
that feels that way instead of cluttered with screenshots and duplicates.*

Clean up and categorize your Apple Photos library with rule-based sweeps: screenshots,
selfies, receipts, message screenshots, duplicates, and junk — filed into albums, tagged
with keywords, hidden from the roll, archived to a backup volume, and (only ever with
your explicit confirmation) deleted.

Works against the modern Photos library (`~/Pictures/Photos Library.photoslibrary`);
actions sync everywhere via iCloud Photos.

## Setup

```sh
uv sync
uv run haymish init      # installs ~/.haymish/rules.toml
uv run haymish doctor    # checks permissions & backends — follow any ✗ fix
```

`doctor` will tell you to grant **Full Disk Access** to the app you run haymish
from (System Settings → Privacy & Security → Full Disk Access). That's required to
read the Photos database. Hide/delete actions additionally prompt for Photos access
on first use, and album/keyword writes prompt for Photos automation — approve once.

## Commands

| Command | What it does |
|---|---|
| `scan` | Read-only inventory + report: screenshots by age, selfies, receipt/message candidates, duplicates, junk-score calibration, people-tag hygiene |
| `sweep [rule]` | Run rules from `rules.toml`. **Dry-run by default**; `--apply` to act |
| `confirm-deletes` | Review staged deletions; requires verified backup copies; macOS shows its own final confirmation dialog |
| `undo` | Reverse the last sweep's album/keyword/hide actions |
| `archive` | Export originals to the backup volume, checksum-verified |
| `import <files>` | Import into Photos and immediately run rules on them |
| `schedule` | Install a launchd job for periodic sweeps |
| `menubar` | Menu-bar app: status, Sweep Now, Confirm Deletes |
| `doctor` | Permission / environment checks |

## How rules work

Each rule in `~/.haymish/rules.toml` has a query, an optional classifier
(Apple's on-device signals free; local Ollama vision model by default; Claude API
opt-in per rule), and an age-gated lifecycle ladder:

```
file (album/keyword, immediate) → hide (off the roll) → archive (backup copy) → delete (staged)
```

Ages are relative to the photo's own date, so behavior is predictable.

**Deletion is deliberately hard.** Scheduled sweeps only *stage* deletions. Nothing
is removed until you run `confirm-deletes`, which refuses without checksum-verified
backup copies and ends at a macOS system dialog that cannot be scripted away.
Treat deletion as permanent; keep a backup volume (a USB stick works) configured in
`[global].backup`.

## Safety model

- Dry-run by default everywhere; every applied action is logged and `undo`-able
  (album, keyword, hide).
- Deletes are staged → human-confirmed → OS-confirmed → Recently Deleted (30-day
  window) — four layers.
- The Photos library file is never touched directly; all writes go through Apple's
  supported automation APIs.

## Spikes

`spikes/hide_spike.py` — verifies programmatic hide/unhide (safe roundtrip on one photo).
`spikes/vision_bench.py` — benchmarks Apple Vision + Ollama on sample images.
