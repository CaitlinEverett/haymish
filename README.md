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

## Dogfood path (recommended)

```sh
uv run haymish doctor                          # fix any ✗ first (esp. Full Disk Access)
uv run haymish review screenshots-general      # start narrow — thumbnails, then Apply
# or: uv run haymish review                    # all actionable rules
uv run haymish undo                            # if something looks wrong after Apply
```

`review` opens a localhost page with thumbnails. Uncheck false positives (they're
remembered and won't resurface), then Apply. Same stage code as `sweep --apply` —
no separate apply path that can drift.

Archive/delete stages need `[global].backup` set in `~/.haymish/rules.toml` (USB
stick path is fine). File + hide work without it.

## Prompts: ask, find, and semantic rules

Build the AI index once (a caption + embedding per photo, all local via Ollama —
nothing leaves your Mac), then drive cleanup in plain language:

```sh
uv run haymish index                                       # one-time-ish; incremental after
uv run haymish find "the whiteboard from the conference"   # semantic search, read-only
uv run haymish find "receipts from this spring" --album "Expenses"   # file via review UI
uv run haymish ask "put my recipe screenshots in a Recipes album"
uv run haymish ask "hide selfies older than a week" --save hide-old-selfies
```

`ask` compiles the request into a rule with a local LLM, prints its interpretation,
and opens the same thumbnail review — nothing happens until you approve. `--save`
writes the generated rule into `rules.toml` so it runs in every future sweep:
one-off prompts graduate into standing automation.

Rules can also match by content directly:

```toml
[rule.recipes]
query = { screenshot = true }
semantic = { query = "cooking recipe with ingredients or instructions", min_score = 0.35 }
file = { album = "Recipes" }
```

**Prompts can never delete.** `ask` plans are stripped to file/tag/hide no matter
what's requested; archive and delete stay in `rules.toml` plus the staged
`confirm-deletes` flow.

**Videos are covered too.** Rules can target them with `movie = true` or
`screen_recording = true` query flags; the index captions each video's poster
frame, so `find` and `ask` see them; every action (file, hide, archive, delete)
works on videos the same as photos.

## Commands

| Command | What it does |
|---|---|
| `app` | Open the dashboard in your browser (starts the daemon if needed) |
| `serve` | Run the daemon: dashboard + local API at http://127.0.0.1:8787 |
| `mcp` | MCP server (stdio) so your AI can drive Haymish — see below |
| `review [rule]` | Localhost thumbnail UI; apply only what you leave checked |
| `ask "<request>"` | Plain-language cleanup → generated rule → review UI. `--save NAME` makes it permanent |
| `find "<query>"` | Semantic search over the AI index; `--album X` files confirmed matches |
| `index` | Build/refresh the local caption+embedding index behind ask/find/semantic rules |
| `scan` | Read-only inventory + report: screenshots by age, selfies, receipt/message candidates, duplicates, junk-score calibration, people-tag hygiene |
| `sweep [rule]` | Run rules from `rules.toml`. **Dry-run by default**; `--apply` to act blindly |
| `confirm-deletes` | Review staged deletions; requires verified backup copies; macOS shows its own final confirmation dialog |
| `undo` | Reverse the last sweep's album/keyword/hide actions |
| `archive` | Export originals to the backup volume, checksum-verified |
| `import <files>` | Import files into Photos and immediately run rules on them |
| `schedule` | Install a launchd job: refreshes the AI index, then sweeps — periodically, unattended |
| `menubar` | Menu-bar app: Review Now, Sweep Now, Confirm Deletes |
| `doctor` | Permission / environment checks |

## The dashboard

`haymish app` opens a local dashboard (menu-bar → "Open Haymish" does the same):
status at a glance, the ask box, semantic find, the review queue with thumbnails,
rule toggles, and index refresh — all served from a daemon bound to 127.0.0.1
with a per-run token. Deletion is never available from the dashboard; staged
deletes are shown read-only and finalized only via `haymish confirm-deletes`.

## Your AI as a photo librarian (MCP)

`uv sync --extra mcp`, then register with your MCP client (e.g. Claude Code:
`claude mcp add haymish -- uv run --project /path/to/haymish haymish mcp`).
Your AI gets tools to check status, search the library semantically, and draft
cleanup plans — but the contract is strict: **the AI proposes, the human
disposes.** Plan tools return a review URL; a person opens it, sees thumbnails,
and clicks Apply. There is no apply tool and no delete tool over MCP.

## How rules work

Each rule in `~/.haymish/rules.toml` selects photos through up to three tiers —
cheap metadata query flags, a `semantic` embedding match against the AI index,
and a per-photo vision `classify` check (Apple's on-device signals free; local
Ollama model by default; Claude API opt-in per rule) — then walks an age-gated
lifecycle ladder:

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
