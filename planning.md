# Provenance Guard — Spec (planning.md)

Design document written before implementation. It is also the primary prompting
artifact for Milestones 3–5: each implementation milestone quotes specific
sections of this file (plus the Architecture diagram) to an AI tool.

**Core principle:** on a writing platform, a false positive (labeling a human's
work as AI) is the worst error. Every design decision below — asymmetric
thresholds, disagreement-widens-uncertainty scoring, non-accusatory label
wording, and an easy appeal path — exists to protect the human creator.

---

## 1. Detection signals

Two genuinely distinct signals: one **semantic** (reads meaning), one
**structural** (measures form). Neither is a variant of the other, so combining
them is more informative than either alone.

### Signal 1 — LLM classification (Groq, `llama-3.3-70b-versatile`)

- **Measures:** semantic and stylistic coherence — does the text read as human-
  or AI-written when judged holistically?
- **Why it differs:** AI prose is typically fluent but generic, evenly weighted,
  and "safe"; human writing takes sharper, less predictable turns and uneven
  emphasis.
- **Output shape:** the model is prompted to return strict JSON:
  ```json
  { "p_ai": 0.0-1.0, "rationale": "one short sentence" }
  ```
  `p_ai` is the model's estimated probability the text is AI-generated. The
  rationale is stored for the appeal reviewer, not shown to end users.
- **Blind spot:** lightly edited AI text can pass as human; non-deterministic,
  so repeated calls can disagree at the margins; no ground truth — it can be
  confidently wrong.

### Signal 2 — Stylometric heuristics (pure Python)

Three sub-metrics, each mapped to a partial AI-likelihood in `[0,1]`, then
averaged into the signal's `p_ai`.

| Sub-metric | What it captures | Human-leaning | AI-leaning |
|---|---|---|---|
| Sentence-length CV (stdev / mean of words-per-sentence) | rhythm variability | high (≥ 0.60) | low (≤ 0.25) |
| Average word length (chars) | lexical sophistication | low (≤ 4.5) | high (≥ 6.0) |
| Long-word fraction (share of words ≥ 7 chars) | vocabulary complexity | low (≤ 0.15) | high (≥ 0.45) |

- **Measures:** structural uniformity and lexical sophistication of the writing.
- **Why it differs:** AI/formal prose is statistically more uniform and uses
  longer, more sophisticated words; casual human writing varies more in rhythm
  and leans on shorter words.
- **Output shape:** `{ "p_ai": 0.0-1.0, "metrics": { "cv": ..., "avg_word_len":
  ..., "long_word_ratio": ..., "word_count": ... } }`. Each sub-metric maps to
  `[0,1]` by linear interpolation between the human-leaning and AI-leaning
  thresholds above (clamped), then averaged.
- **Calibration note:** the metric set was revised during Milestone 4. The
  original spec named type-token ratio and punctuation density, but testing
  against sample texts showed both cluster tightly across AI and human at typical
  submission length (~40-60 words) and fail to discriminate. They were replaced
  with average word length and long-word fraction, which separate cleanly.
- **Blind spot:** short texts produce unstable statistics (mitigated by pulling
  p_ai toward 0.5 below ~40 words); formal or highly polished human writing looks
  "AI-like" on every lexical metric (mitigated because the LLM usually disagrees,
  dragging the combined score into "uncertain" rather than a false "AI").

### Combining the signals

1. **Weighted mean.** The LLM is the more reliable single judge, so:
   `base = 0.6 * llm.p_ai + 0.4 * stylometry.p_ai`.
2. **Disagreement widens uncertainty.** Let `d = |llm.p_ai - stylometry.p_ai|`.
   When the two signals disagree, pull the result toward 0.5 (uncertain)
   proportionally:
   `ai_score = base * (1 - d) + 0.5 * d`.
   - Signals agree (`d ≈ 0`) → `ai_score ≈ base` (a confident verdict is allowed).
   - Signals disagree (`d` large) → `ai_score` is dragged toward 0.5 (uncertain).
   This is the mechanism that stops a lone signal from producing a confident
   false "AI" verdict — directly addresses the false-positive scenario.

`ai_score` ∈ [0,1] is the single returned score.

---

## 2. Uncertainty representation

**What the score means.** `ai_score` is the system's estimated probability the
text is AI-generated. `0.0` = confidently human, `1.0` = confidently AI, and
**`0.5` = maximally uncertain (the system is refusing to guess)**. We picked the
meaning of the middle first: 0.5 is not "50% AI," it is "we don't know." A 0.6
means "leaning AI but not confident — do not accuse." A 0.95 means "strong,
agreeing evidence of AI."

**Mapping raw outputs to a calibrated score.** Each signal already emits a
`p_ai` in [0,1] (LLM directly; stylometry via the interpolation table in §1).
The combination formula in §1 produces the final calibrated `ai_score`. The
disagreement term is the calibration step that keeps single-signal noise from
masquerading as confidence.

**Thresholds (asymmetric — biased against false-positive AI calls):**

| Band | Condition | Verdict |
|---|---|---|
| High-confidence AI | `ai_score ≥ 0.80` | likely AI |
| Uncertain | `0.30 < ai_score < 0.80` | inconclusive |
| High-confidence human | `ai_score ≤ 0.30` | likely human |

The AI band demands more evidence (≥ 0.80) than the human band (≤ 0.30). We
require stronger proof before labeling a human's work as AI. This guarantees the
three-way behavior the checkpoint requires: **no binary flip at 0.5** — a 0.51
lands in "uncertain," a 0.95 in "high-confidence AI," and they produce different
labels.

**Reader-facing confidence** (optional field): `round(2 * |ai_score - 0.5|, 2)`,
so the middle reads as low confidence and both extremes as high confidence.

---

## 3. Transparency label design

Three variants. Exact end-user text (rationale strings and raw scores are *not*
shown to readers — only to appeal reviewers):

**High-confidence AI** (`ai_score ≥ 0.80`):
> ⚠️ **Likely AI-generated.** Our automated analysis found strong signs that this
> text was created with significant AI assistance. Automated detection is
> imperfect and can be wrong — if you're the author and disagree, you can appeal
> this label.

**High-confidence human** (`ai_score ≤ 0.30`):
> ✓ **Likely human-written.** Our automated analysis found no strong signs of AI
> generation in this text. Automated checks aren't perfect, but this content
> reads as human-written.

**Uncertain** (`0.30 < ai_score < 0.80`):
> ❓ **Not enough evidence to say.** Our automated checks were inconclusive or
> disagreed for this text, so we won't guess whether it was written by a human or
> AI. If you're the author, you can add context through an appeal.

Design notes: the AI label is deliberately non-accusatory ("found signs,"
"can be wrong"), always mentions the appeal path, and never states AI generation
as fact. The uncertain label frames refusal to guess as an intentional choice,
not a failure.

---

## 4. Appeals workflow

- **Who can appeal:** the content's creator, identified by the `content_id`
  returned from `/submit`. (In a production system this would be tied to an
  authenticated account; here the `content_id` is the handle.)
- **What they provide:** `{ "content_id": <id>, "creator_reasoning": "<free
  text>" }` — their reasoning for contesting the verdict.
- **What the system does on receipt:**
  1. Look up the original decision by `content_id`; return `404` if unknown.
  2. Write an **appeal record** to the audit log, linked to the original
     `content_id`, containing the reasoning and a timestamp.
  3. Update the content's status from `classified` → `under_review`.
  4. Return `{ content_id, status: "under_review", message }`. **No automated
     re-classification.**
- **What a reviewer sees in the appeal queue** (per item, newest first):
  original text, verdict + `ai_score`, both signals' `p_ai`, the stylometry
  sub-metrics, the LLM rationale, the original timestamp, the creator's appeal
  reason, and current status. This is exactly the record needed to make a manual
  call.

---

## 5. Anticipated edge cases

1. **Repetitive, simple-vocabulary poetry** (villanelle, nursery-rhyme style).
   Low sentence-length CV + low type-token ratio → the stylometry signal scores
   it AI-leaning, but it's human. Mitigation: the LLM signal usually recognizes
   the poetic form, so signals disagree → the disagreement term drags `ai_score`
   toward "uncertain" rather than a false "AI" verdict, and the ≥ 0.80 AI
   threshold makes a confident false positive unlikely.

2. **Lightly human-edited AI text** (AI draft polished by a person). The LLM may
   call it human while stylometry still reads uniform → genuine ambiguity. This
   *should* land in the uncertain band; that's the honest answer, not a failure.

3. **Very short submissions** (a haiku, a two-line excerpt). Too little text for
   stable TTR / sentence-variance statistics. Mitigation: enforce a minimum
   length (e.g. reject or flag under ~40 words), and down-weight stylometry /
   widen uncertainty for short inputs so the unreliable signal can't drive a
   confident verdict.

---

## Architecture

**Narrative.** *Submission:* text hits `POST /submit`, passes the rate limiter,
runs through both signals in the pipeline, is combined into one `ai_score`,
mapped to a transparency label, logged, and returned. *Appeal:* a creator sends
their `id` and reasoning to `POST /appeal`; the system logs the appeal beside the
original decision, sets status to "under review," and returns the new status. No
automated re-classification.

```
SUBMISSION FLOW
   Client
     |  { text }
     v
  POST /submit
     |
     v
  [ Rate limiter ] --over limit--> 429
     |  raw text (allowed)
     +-----------------+-----------------+
     |                                   |
     v                                   v
  Signal 1: Groq LLM              Signal 2: Stylometry
  (p_ai + rationale)              (p_ai + metrics)
     |                                   |
     +------------------+----------------+
                        |  two p_ai values
                        v
              Confidence scoring
              (weighted mean + disagreement term)
                        |  ai_score (0-1)
                        v
              Transparency label
              (AI / human / uncertain)
                        |  verdict + label + signals
                        v
                 [ Audit log ] (SQLite/JSON)
                        |  decision record
                        v
             Response: { id, verdict, confidence, label, signals }
                        |
                        v
                     Client

APPEAL FLOW
   Creator
     |  { id, reason }
     v
  POST /appeal
     |  lookup id ---not found---> 404
     v
  [ Audit log ] <-- append appeal record (reason + timestamp)
     |
     v
  Status: classified -> under review
     |
     v
  Response: { id, status: "under review" }
```

---

## AI Tool Plan

For each implementation milestone: which spec sections feed the AI tool, what to
ask it to generate, and how to verify.

### M3 — Submission endpoint + first signal
- **Provide:** §1 (Detection signals, esp. Signal 1 output shape) + the
  Architecture diagram + §4's `/submit` request/response contract.
- **Ask for:** a Flask app skeleton with `POST /submit`, plus a standalone
  `llm_signal(text) -> { p_ai, rationale }` function that calls Groq and parses
  the strict-JSON response.
- **Verify:** call `llm_signal` directly on 3–4 hand-picked texts (obvious AI,
  obvious human, borderline) and confirm `p_ai` moves in the expected direction
  *before* wiring it into the endpoint. Then confirm `/submit` returns the
  contract shape.

### M4 — Second signal + confidence scoring
- **Provide:** §1 (Signal 2 sub-metrics + combination formula) + §2 (Uncertainty
  representation, thresholds) + the diagram.
- **Ask for:** `stylometry_signal(text) -> { p_ai, metrics }` and a
  `combine(llm_p, styl_p) -> ai_score` implementing the weighted-mean +
  disagreement term, plus `score_to_band(ai_score)`.
- **Check:** run clearly-AI vs clearly-human samples and confirm scores separate
  meaningfully (not clustered at 0.5); confirm a constructed disagreement case
  lands in the uncertain band; confirm no binary flip at 0.5.

### M5 — Production layer (labels, appeals, safety)
- **Provide:** §3 (three label variants, exact text) + §4 (Appeals workflow) +
  the diagram.
- **Ask for:** `make_label(band) -> text` returning the exact §3 strings, the
  `POST /appeal` endpoint (lookup → log → status change), rate-limiting config,
  and audit-log writes on every decision and appeal.
- **Verify:** craft inputs that reach all three label variants; submit an appeal
  and confirm status becomes "under review" and both the decision and appeal
  appear in `GET /log`; confirm the rate limiter returns 429 past the limit.
