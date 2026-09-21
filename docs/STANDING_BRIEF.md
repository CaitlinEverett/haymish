# Standing brief (doctrine)

Holds on every turn, conversation, compaction, feature, and epic.
Source frame: user standing brief + CS 6750 Atlas ([lesson 2.5](https://omscs6750.gatech.edu/atlas/lesson-2-5.html)).
Leave lecture prose on Atlas; apply the checks here.

Agents: re-read this file after any compaction summary before editing.

---

## Product card (filled once)

| Field | Haymish |
|-------|---------|
| **Name and one-sentence job** | **Haymish** — a local-first, review-first control surface that helps a person turn repetitive Apple Photos decisions into durable Photos-native organization (tags/albums/state) without becoming a second photo silo. |
| **The loop the person is here to finish** | Discover / match → **review** (judge) → apply trusted effects → verify (undo/recover if needed) → optionally save a routine. Sub-loop for index: caption/embed catch-up → find/ask/semantic quality. |
| **What this product is not** | Not a second library; not cloud sync; not unsupervised mass delete; not “AI organizes your life for you”; not a Photos DB editor; not a backup product that invents a volume path. |
| **Where the source of truth lives** | **Apple Photos** (assets + albums/keywords/hidden). Haymish catalog/ledger is evidence, judgments, and index — disposable relative to Photos. Rules live in `~/.haymish/rules.toml` (human-owned). |
| **What the person must do on purpose** | Grant TCC/Full Disk / Photos automation (prefer Terminal for PhotoKit); set or refuse backup before archive/delete; review before apply; finalize delete only via typed confirm + system dialog; paste doctor config proposals (Haymish will not silently rewrite rules); start/stop overnight host index jobs. |
| **Offer names; who may take money** | **Community** (free trust floor — inventory, review, undo, safety) — **no money**. **Personal** (paid app / major upgrades — workbench, routines, polish) — **may take money** when offered. **Studio** (pro seat/support) — **may take money** when offered. Safety, own decisions, and recovery are never paywalled (`docs/BUSINESS.md`). |
| **Closed until a named decision** | Enabling archive/delete dogfood; backup volume identity; finalize-delete path; MCP apply/delete tools; overnight full rebuild vs catch-up as default; charging for Personal/Studio; any backup bypass (forever closed). |
| **Secret and deploy rules** | No secret values in chat, code, or commits — `op://` refs only; use `~/dev/secrets-hygiene`. No push/deploy/store publish unless Caitlin asks. No silent paid-API fallback. Local Ollama preferred for AI. |

---

## Profiles (four cells)

### Surface: Review workbench (dashboard + CLI review)

| | Newbie | Expert |
|--|--------|--------|
| **High trust** | Guided build queue → fold/page → small apply → undo | Keyboard, rules, daemon jobs, catch-up, packs |
| **Low trust** | Dry-run / report-only, empty states, doctor CTAs | Preview eligibility, reject durability, ledger inspect |

- **Job:** Finish one reviewed apply (or an honest zero-match) without surprise mutations.
- **Cell that must succeed:** High-trust newbie (first successful small apply + undo).
- **Cell that must not be harmed:** Low-trust expert (constraints, ledger, no silent mass reject).

### Surface: Host index / doctor (Terminal + status files)

| | Newbie | Expert |
|--|--------|--------|
| **High trust** | `doctor` ✓/✗ with next command | catch-up script, logs, `ollama ps` |
| **Low trust** | “nothing to do” / propose-only `--fix config` | fail-closed backup, no silent rewrite |

- **Job:** Know whether captions/embeddings are healthy enough for find/ask/review subgroups.
- **Must succeed:** Low-trust newbie (clear next action without reading source).
- **Must not harm:** High-trust expert (resume, fingerprints, no catalog wipe).

### Surface: Marketing / account / payment *(future)*

| | Unsure | Certain |
|--|--------|---------|
| **High trust** | Clear Community vs Personal vs Studio | Checkout that states what unlocked |
| **Low trust** | Privacy/terms match product; no fake “AI deletes safely” | Receipt + success URL says what happened |

- **Job:** Choose an offer without paying for safety.
- **Must succeed:** Unsure + low trust (honest boundaries).
- **Must not harm:** Certain + high trust (no bait-and-switch on deletion/backup).
- **Money decision:** epic-level only — not inside a feature turn.

If a written journey disagrees with the running product, **believe the product** and say so.

---

## Atlas check (every turn / feature / epic)

1. **Close both gulfs** (lesson 2.2): person can tell **what to do next** (execution) and **what just happened** (evaluation).
2. **Feature:** name lesson **2.5** principles touched: discoverability, simplicity, affordances, mapping, perceptibility, consistency, flexibility, equity, ease, structure, constraints, tolerance, feedback, documentation. Detail: `docs/HCI_HEURISTICS.md`.
3. Say whether a likely failure would be a **slip** or a **mistake**.
4. **Expert blind spot:** expert path is not the only path; newbie path does not block the expert.
5. **Epic:** existing journeys = task analysis. Wrong surface → **two alternatives before a build**. Chat evaluation = walkthrough of the **four cells**, not a user study.

---

## Grains

| Grain | Rule |
|-------|------|
| **Turn** | One slice of the active feature. Stop with: **changed / verified / not opened**. |
| **Conversation** | One feature unless this chat is explicitly an epic. Open by filling this brief if empty. Close with **Learn** and **Evolve** lines. |
| **Compaction** | Carry: product card, surface, job, four cells, kill condition, built, measured, unverified, do-not-rebuild, out of scope, business/infra gates. After a summary, **re-read this brief before editing**. A summary does not prove tests ran. |
| **Feature** | Fill feature brief below. Smallest change that serves the cell. Measure all four cells on that surface. Learn: Good / More UI / Different UI. Evolve default, copy, or journey note before the next feature. |
| **Epic** | Pair of jobs that pull against each other. Sequence features; later must not break earlier cells. Money, accounts, deploy decided here. Name where knowledge lives: person’s head / this product / another tool. |

---

## Feature brief (template — fill per feature)

- **Surface and job:**
- **Cell that must succeed / kill if we miss it:**
- **Cell that must not be harmed:**
- **Principles this change touches:**
- **Build:**
- **Measure (incl. what cannot be launched):**
- **Learn:**
- **Evolve:**
- **Out of scope:**

---

## Testing bar

A render is not a test. Walk **empty**, **error**, and the **cell that must not be harmed**. Public copy matches terms/privacy when those exist. Download/payment links resolve; success URL says what happened. **Secret values stay out of chat, code, and commits.**

Run Terminal for host/job/doctor signals when claiming “on your Mac” state. Prefer `docs/HCI_HEURISTICS.md` “live” table.

---

## Active conversation grain (this chat)

- **Grain:** epic-adjacent credit burn → standing brief lock-in (this turn = doctrine).
- **Surface:** agent/process (not a user-facing feature slice).
- **Built:** product card + profiles + Atlas binding in-repo.
- **Not opened:** Personal/Studio checkout; backup dogfood; MCP mutations.
