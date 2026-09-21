# Screenshot caption pilot — 2026-09-03

## Decision

`qwen3-vl:8b` is **not approved for unattended batch captioning** on the current Ollama setup. Do not run the full screenshot library with it. Preserve the successful local captions as experimental evidence, but choose the next model from a fixed labeled evaluation set rather than more ad hoc prompt tuning.

No Apple Photos mutation occurred during this experiment. Reads used osxphotos/local derivatives; writes were limited to versioned captions and embeddings in Haymish's local catalog.

## Baseline pilot

Scope: 30 screenshot candidates, `nomic-embed-text` preserved, `qwen3-vl:8b`, no stale-caption deletion.

- Concurrency 2 immediately showed contention: one success and three timeouts before the run was stopped.
- A one-image concurrency-1 preflight succeeded.
- The remaining concurrency-1 run completed with 16 captions and 12 timeouts in 39.7 minutes.
- Total current-model result: **18/30 captioned; 12/30 failed**.
- The command exited 0 despite the 43% failure rate. This was a job-semantics defect, not a successful pilot.
- Successful and failed source derivatives had nearly identical size distributions (means 0.338 MB vs 0.364 MB) and overlapping dimensions. File size/resolution did not explain the failures.

## Baseline quality review

All 18 successful captions began with an image-kind word, and most were useful for broad retrieval. The sample nevertheless contained four clear factual/category errors and one questionable ungrounded identification:

- one screenshot described as a photo;
- one email described as a text-message conversation;
- two social interfaces assigned the wrong platform;
- one person named without clearly grounded visible text.

The model reliably quoted prominent text, sometimes including private document amounts or message content. That can aid local search but requires deliberate UI/privacy treatment. The sample contained no video-call, map, or receipt examples, so those requirements remain unvalidated.

## Runtime diagnosis

- The installed model uses Ollama's `qwen3-vl-thinking` renderer/parser.
- Caption requests omitted `think=False` and used a 120-second ceiling.
- The shared generate helper retried every error without the `think` option. A timeout could therefore trigger a second full inference; one measured request took 162.75 seconds despite a 120-second timeout.
- On a warm identical image, a direct request took about 64 seconds with default thinking and 32 seconds with `think=False`.
- Thinking was not fully eliminated: Ollama still returned hidden reasoning content. A small output ceiling could be consumed entirely by reasoning, yielding an empty final response.

## Corrective implementation

The code now:

- sends `think=False` for captions;
- retries without `think` only when Ollama explicitly rejects that field with HTTP 400/422;
- never retries timeouts, connection failures, or server errors as a second inference;
- rejects empty model output in both the Ollama client and `Catalog.put_caption`;
- uses deterministic caption options (`temperature=0`, fixed seed) and a bounded output budget;
- caps automatic caption concurrency at 2 while preserving explicit overrides;
- reduces caption/checkpoint chunks from 32 to 8;
- stops on five consecutive failures or four failures in the latest eight requests;
- returns nonzero whenever the final caption failure rate is at least 25%, including a 1/1 failure;
- fails when a requested vision model is unavailable instead of silently building a captionless index;
- grounds screenshot kind from Photos metadata and asks for generic social/person descriptions rather than unsupported identities.

Invalid empty `p4` caption rows were removed. Successful rows from other revisions remain as experimental evidence.

## Controlled regression rounds

The same six difficult images were reused only to test the identified failure modes; this is not a general quality benchmark.

| Revision | Result | Runtime/quality note |
|---|---:|---|
| p3 | 5/6 | 8.1 min; fixed screenshot-kind, email/chat, and one platform error; one timeout and two grounding errors remained |
| p4 | false 6/6 | 2.5 min, but all six responses were empty after reasoning consumed a 256-token cap; rows removed |
| p5 | 3/6 | three valid captions, three explicit empty-response failures at a 512-token cap |
| p6 | 4/6, nonzero | two explicit empty-response failures; job correctly logged `ABORT` and exited nonzero |

Qwen's `/no_think` control improved one direct probe but did not make the six-image batch reliable. More prompt iterations on this tiny set would overfit and are not justified.

## Next model evaluation

Build one fixed, human-labeled set covering at least:

- video call;
- email;
- text/chat conversation;
- ordinary web page;
- social post without platform guessing;
- map/directions;
- receipt/document;
- product page;
- lock/home screen;
- photograph displayed inside a screenshot;
- people in scene versus tiny avatars;
- low-text and dense-text screenshots.

Compare `qwen3-vl:32b` and at least one genuinely non-thinking local vision model at concurrency 1. Record completion rate, p50/p95 latency, broad-type accuracy, unsupported proper nouns, and useful-text extraction.

Provisional batch gate:

- at least 95% non-empty completion;
- zero silent/empty successes;
- p95 below the configured timeout with margin;
- at least 90% broad-type/subtype accuracy on the labeled set;
- no unsupported person identification;
- no social-platform assertion unless grounded by explicit visible text;
- high-failure runs exit nonzero and preserve completed work for retry.
