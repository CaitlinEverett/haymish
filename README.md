# Haymish

*Haymish (Yiddish, from "heymish") — cozy, unpretentious, at ease. The goal: a camera roll
that feels that way instead of cluttered with screenshots and duplicates.*

Review and organize an Apple Photos library with rule-based collections: screenshots,
selfies, receipts, message screenshots, and related captures can be annotated with
keywords and added to album views. Visibility, backup, and cleanup are separate policies.
Final deletion is currently disabled while complete Live Photo/RAW backup manifests are
implemented.

Haymish reads the modern Photos library (`~/Pictures/Photos Library.photoslibrary`) and
applies supported Photos changes through Apple automation APIs. Cross-device behavior
must be verified during the first canary; it is not assumed from local success.

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

The installed starter rules are additive only: album/keyword actions, with no default
hide, archive, or deletion stages. Do not enable state-changing stages before a reviewed
canary and verified undo.

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

**Videos are included in selection and organization.** Rules can target them with
`movie = true` or `screen_recording = true`; the index captions a poster-frame
derivative when available. Album/keyword behavior still needs the same real canary as
photos, and backup/deletion parity is not claimed before complete manifests exist.

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
| `confirm-deletes` | Fails closed in this build until complete asset-component backup manifests exist; later also requires typed and macOS confirmation |
| `undo` | Reverse supported album/keyword/hide/staged-delete actions from ordinary sweep/review runs |
| `archive` | Export an integrity-tracked file to the backup volume; complete Live Photo/RAW manifests are not implemented yet |
| `import <files>` | Import files into Photos and immediately run rules on them |
| `schedule` | Install a launchd job: refreshes the AI index, then sweeps — periodically, unattended |
| `menubar` | Menu-bar app: Review Now, Sweep Now, Confirm Deletes |
| `doctor` | Permission / environment checks |

## The dashboard

`haymish app` opens a local dashboard (menu-bar → "Open Haymish" does the same):
status at a glance, the ask box, semantic find, the review queue with thumbnails,
rule toggles, and index refresh — all served from a daemon bound to 127.0.0.1
with a per-run token. Deletion is never available from the dashboard; staged
candidates are read-only, and `haymish confirm-deletes` currently refuses finalization
until complete asset-component manifests are implemented.

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

**Deletion currently fails closed.** Scheduled sweeps can only stage candidates. The
legacy archive ledger records one exported file and cannot prove complete coverage for
Live Photo motion or associated RAW components, so `confirm-deletes` refuses to remove
anything in this build. Complete independently re-verified manifests, an exact typed
confirmation, and the macOS system dialog are required before finalization is re-enabled.

## Safety model

- Dry-run by default everywhere; every applied action is logged and `undo`-able
  (album, keyword, hide).
- Cleanup candidates can be staged, but final deletion remains disabled until the
  complete-manifest gate is implemented and tested.
- The Photos library file is never touched directly; all writes go through Apple's
  supported automation APIs.

## Spikes

`spikes/hide_spike.py` — verifies programmatic hide/unhide (safe roundtrip on one photo).
`spikes/vision_bench.py` — benchmarks Apple Vision + Ollama on sample images.
