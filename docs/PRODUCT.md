# Haymish product doctrine

## Product thesis

Haymish is a local-first, review-first control surface over Apple Photos. It helps a person turn repetitive photo decisions into durable Photos-native organization while preserving adjustable agency: Haymish can suggest, group, and eventually automate more only as each collection earns trust.

The first wedge is screenshot triage. Professional shoot curation is a second posture built on the same substrate, not a separate rules engine.

## Core concepts

### Lens

A composable description of how assets are selected. Lenses can combine metadata, detectors, OCR, semantic evidence, vision evidence, examples, explicit UUIDs, and other lenses with ALL, ANY, and NOT.

### Collection

A human-owned meaning such as `Recipes`, `Calls with Mom`, `Travel documents`, or `Smith Wedding picks`. Collections may overlap, nest, split, merge, and be renamed. AI cluster labels are suggestions, not truth.

### Disposition

What should happen to a collection:

- add Photos keywords
- add album views
- protect
- decide later
- hide after a labeled consequence and grace period
- create a backup copy
- mark as a deletion candidate or stage after review

Organization is tag-first. Albums are optional synced views. Hide/archive/delete are separate policies.

### Routine

A collection plus cadence and automation level. Automation is explicit:

1. Explore
2. Hand-pick
3. Review group
4. Review new matches
5. Sample then apply
6. Trusted additive
7. Delayed state change
8. Stage only

Material lens, model, prompt, or collection-boundary changes demote trust back to review.

## User paths

All paths converge on the same decision workbench and execution ledger:

- browse and hand-pick
- guided cleanup
- Ask in plain language
- saved recurring routine
- professional shoot session

There must not be separate mutation implementations for CLI, dashboard, MCP, or scheduling.

## Granularity

A user can narrow a collection by adding evidence, excluding examples, or splitting/reclustering. A user can broaden it by removing conditions, forming a parent, or combining lenses. Every change previews its added and removed assets.

Combining is non-destructive by default: preserve source subcollections and create a parent or union lens. Photos are multi-label; no single category needs to win.

## Review judgments

Unchecked is too ambiguous. The workbench needs explicit judgments:

- apply
- correct match, skip this effect
- not a match
- keep/protect
- decide later
- never suggest for this collection

Judgments are versioned against collection evidence. They can be inspected, cleared, exported, and migrated.

## Incremental cadence

Regular operation has separate layers:

- lightweight discovery of new/changed assets
- bounded enrichment while idle/on power
- periodic review digest
- trusted additive application
- occasional integrity reconciliation

Haymish may perform a cheap metadata inventory, but unchanged completed inference, review, and effects do not rerun.

## Similarity and deletion

Similarity class and capture intent are independent.

- source-byte identical: exact duplicate review
- decoded-pixel identical: pixel-equivalent review
- RAW/JPEG/Live/edit/composite: related asset family, protected by default
- repeated utility screenshot: stronger redundancy suggestion
- portrait/burst: selection set, not duplicate set
- bracket/stack/panorama/timelapse: protected technical sequence
- artistic/documentary series: preserve diversity
- unknown intent: review without deletion recommendation

Culling is not deletion. A `not selected` professional frame remains retained unless a separate retention policy explicitly advances it.

Deletion begins as an annotation with reason, confidence, group, keeper suggestion, estimated recovery, unique Photos state, and backup coverage. It advances through review → complete backup manifest → staged deletion → typed confirmation → macOS confirmation. Nothing finalizes unattended.

## Professional posture

Professional libraries are inventory. Defaults:

- no automatic hide or delete
- keep whole shoot available
- file/tag only
- asset-family awareness
- contact sheet and compare workflows
- multiple picks: technical, expression, composition, and diverse alternates
- protect documentary and technical-source sequences
- exportable decisions and provenance

Portrait composite work is optional and derived-only. Sources remain protected. Source-pixel blink/expression replacement is distinct from generative repair, and documentary presets structurally disable composites.

## Product truth

Applied Photos organization survives without Haymish. Haymish-only judgments, semantic features, groups, and routine history require export/backup. The UI must say this plainly.

Safety is not a paid feature. Privacy means no upload by default and clear per-feature disclosure for any opt-in remote model.
