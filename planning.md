# Provenance Guard — Planning Document

> Written before implementation, updated before any stretch features.

---

## Architecture

### Submission Flow Narrative

A creator submits a piece of text via `POST /submit`. The request carries the raw `text`
and a `creator_id`. The endpoint first checks rate limits (Flask-Limiter rejects the
request with a 429 if exceeded). It then runs two independent detection signals in
sequence: (1) an LLM-based classifier via Groq that returns a probability the content
is AI-generated, and (2) a stylometric heuristics function that computes statistical
properties of the text (sentence-length variance, type-token ratio, punctuation density)
and returns its own 0–1 AI probability. The confidence scoring module combines these
two signal scores into a single weighted confidence score. That score is passed to the
label generator, which returns the exact transparency text a user would see. Finally,
every step is written to the structured audit log (SQLite), a unique `content_id` is
assigned (UUID), and the endpoint returns the full result to the caller.

### Appeal Flow Narrative

A creator who disputes the label calls `POST /appeal` with the `content_id` returned at
submission time and their `creator_reasoning` (a free-text explanation). The endpoint
looks up the submission in the audit log, updates its status from `"classified"` to
`"under_review"`, appends an appeal entry to the log (with the reasoning and a new
timestamp), and returns a confirmation. A human reviewer can later call `GET /log` to
see all entries, including appeals.

### Architecture Diagram

```
SUBMISSION FLOW
===============

POST /submit
  │
  ▼
[Rate Limiter]
  │ (429 if exceeded)
  │ (pass-through if ok)
  ▼
[Input Validator]
  │  text, creator_id
  ▼
[Signal 1: LLM Classifier]──────────────────────────────────────┐
  │  Groq llama-3.3-70b-versatile                               │
  │  Prompt: "Rate 0.0–1.0 how likely this is AI-generated"    │
  │  Output: llm_score (float 0.0–1.0)                         │
  │                                                             │
[Signal 2: Stylometric Heuristics]───────────────────────────── │
  │  - Sentence length variance (std dev)                       │
  │  - Type-token ratio (vocabulary diversity)                  │
  │  - Punctuation density (punct chars / total chars)          │
  │  Output: style_score (float 0.0–1.0)                        │
  │                                                              │
  └─────────────────┬────────────────────────────────────────────┘
                    │ llm_score + style_score
                    ▼
         [Confidence Scorer]
           │  weighted average:
           │  confidence = 0.60 × llm_score + 0.40 × style_score
           │  Output: confidence (float 0.0–1.0)
           ▼
         [Label Generator]
           │  < 0.30  → "Likely Human" label
           │  0.30–0.69 → "Uncertain" label
           │  ≥ 0.70  → "Likely AI" label
           │  Output: label_text (string), label_code (enum)
           ▼
         [Audit Logger]
           │  Writes structured JSON row to SQLite
           │  Fields: content_id, creator_id, timestamp,
           │          llm_score, style_score, confidence,
           │          attribution, label_code, status
           ▼
         [Response]
           └─ JSON: content_id, attribution, confidence,
                    label, llm_score, style_score, status


APPEAL FLOW
===========

POST /appeal
  │  content_id, creator_reasoning
  ▼
[Lookup content_id in audit log]
  │  (404 if not found)
  ▼
[Update status → "under_review"]
  │
  ▼
[Append appeal entry to audit log]
  │  Fields: content_id, appeal_reasoning, appeal_timestamp
  ▼
[Response]
  └─ JSON: content_id, status, message
```

---

## Detection Signals

### Signal 1 — LLM Classifier (Groq)

**What it measures:** The semantic and holistic stylistic "AI-ness" of text. The LLM has
been trained on vast corpora and can recognize patterns (hedged phrasing, over-structured
paragraphs, lack of personal voice, suspiciously even register) that are difficult to
enumerate as rules.

**Output format:** A float from 0.0 to 1.0, where 1.0 = definitely AI-generated.
The Groq API call asks the model to return JSON like `{"ai_probability": 0.83}`.

**Blind spot:** The LLM may be biased toward formal or academic human writing, which
can pattern-match to AI output. A non-native English speaker writing in a careful,
structured way could get a high score even though it's genuinely their work.

### Signal 2 — Stylometric Heuristics (pure Python)

**What it measures:** Statistical uniformity. AI models generate text with suspiciously
low variance — sentences cluster around a narrow length range, vocabulary repeats at
predictable rates, punctuation is sparse and regular. Human writing is messier.

Specific metrics:
- **Sentence length variance**: std deviation of word-count per sentence. Low variance → more AI-like.
- **Type-token ratio (TTR)**: unique words / total words. AI tends to repeat vocabulary; humans use more diverse words.
- **Punctuation density**: punctuation characters / total characters. AI text is often under-punctuated; humans use dashes, ellipses, exclamation points more freely.

**Output format:** A float from 0.0 to 1.0. Each sub-metric is normalized and combined
into a single style_score where 1.0 = very uniform / AI-like patterns.

**Blind spot:** Polished human writing (an essay revised many times, a formal cover
letter) can exhibit low variance and high regularity — scoring like AI even when human.

---

## Uncertainty Representation

**Philosophy:** A false positive (labeling human work as AI) is worse than a false
negative on a creative platform. The threshold for the "Likely AI" label is deliberately
higher (≥ 0.70) than a naive 0.50 cutoff. The system is designed to err on the side of
"uncertain" when unsure.

**Score → label mapping:**

| Confidence Score | Attribution Code | What it means |
|-----------------|-----------------|---------------|
| 0.00 – 0.29     | `likely_human`  | Strong evidence of human authorship |
| 0.30 – 0.69     | `uncertain`     | Mixed signals; can't determine clearly |
| 0.70 – 1.00     | `likely_ai`     | Strong evidence of AI generation |

**What does 0.6 mean?** The system has moderate evidence pointing toward AI patterns,
but not strong enough to confidently label it. The label will say "uncertain" and will
NOT flag the creator publicly. A score of 0.51 and 0.68 are both "uncertain" — but the
label text does include the actual score so a reviewer can distinguish them.

**How we achieve calibration:** The LLM signal is given 60% weight because it captures
semantic nuance better; the stylometric signal gets 40% because it is fast and
interpretable but misses context. Both signals are tested independently on known samples
before combining.

---

## Transparency Label Variants

These are the exact strings the system returns. Three variants:

### Variant 1 — High-Confidence AI (confidence ≥ 0.70)

```
⚠️  AI-Assisted Content Detected
Our analysis found strong indicators that this content was generated or heavily
assisted by AI tools (confidence: {score}%). This label helps readers understand
the content's origins.

If this is your original work and you believe this is incorrect, you have the
right to appeal this classification. Appeals are reviewed by our team.
```

### Variant 2 — Uncertain (confidence 0.30–0.69)

```
ℹ️  Content Origin: Uncertain
Our system detected mixed signals and cannot confidently determine whether this
content is human-written or AI-assisted (confidence: {score}%). No action has
been taken on your content.

If you'd like to clarify the origin of this work, you may submit an explanation
via our appeals process.
```

### Variant 3 — High-Confidence Human (confidence < 0.30)

```
✓  Original Human Authorship
Our analysis found strong indicators of original human authorship
(confidence: {score}% human). No action required.
```

*(In all labels, `{score}` is replaced with the human-readable percentage,
e.g. a confidence of 0.82 → "82% AI confidence" for Variant 1,
or a confidence of 0.18 → "82% human confidence" for Variant 3.)*

---

## Appeals Workflow

**Who can appeal:** Any creator who has a `content_id` from a previous `/submit` response.
In a real system this would be gated to the authenticated creator; in this implementation
the `content_id` acts as a simple token.

**What they provide:** `content_id` + `creator_reasoning` (free text, max 2000 chars).

**What the system does on appeal:**
1. Looks up the `content_id` in the SQLite `submissions` table.
2. Updates `status` from `"classified"` to `"under_review"`.
3. Inserts a new row into the `appeals` table with: `content_id`, `creator_reasoning`,
   `appeal_timestamp`.
4. The audit log (`GET /log`) will show both the original classification entry and the
   appeal entry, linked by `content_id`.
5. Returns `{ "content_id": "...", "status": "under_review", "message": "..." }`.

**What a human reviewer sees:** `GET /log` returns all entries. An appeal entry looks like:

```json
{
  "type": "appeal",
  "content_id": "3f7a2b1e-...",
  "creator_reasoning": "I wrote this myself ...",
  "appeal_timestamp": "2025-04-01T15:00:00Z",
  "original_confidence": 0.73,
  "original_attribution": "likely_ai"
}
```

**No automated re-classification** — appeals go to a human queue.

---

## Anticipated Edge Cases

### Edge Case 1: Non-native English speakers with formal writing style

A creator who is not a native English speaker may write in a structured, carefully
controlled style — low sentence variance, limited vocabulary, careful punctuation.
This pattern looks exactly like AI output to our stylometric signal, and the formal
register may also raise the LLM score. **Mitigation:** The appeal workflow exists
precisely for this case. The label text in the Uncertain and AI variants explicitly
invites appeals.

### Edge Case 2: AI-edited human writing

A creator writes a draft, then asks an AI to "clean it up" or "fix grammar." The
result is human in origin but statistically uniform. Our system will likely score
it as AI. This is a genuine attribution gray area — not really a failure, but a
case where the label "AI-assisted" is arguably accurate. Our label wording says
"generated or heavily assisted by AI" to cover this case.

### Edge Case 3: Very short text (< 50 words)

Stylometric heuristics become unreliable with tiny samples — you can't compute
meaningful sentence-length variance from 2 sentences. We'll note in the response
when text is too short for reliable analysis and widen the "uncertain" band.

### Edge Case 4: Poetry and highly stylized writing

Poetry often uses unconventional punctuation, very short lines (low word count per
"sentence"), and intentional repetition. This can make the stylometric signal fire
incorrectly. Our LLM signal should handle this better, but we should give the
stylometric signal less weight for content detected as poetry-like.

---

## AI Tool Plan

### Milestone 3 — Submission endpoint + first signal (LLM)

**Spec sections provided to AI:** Detection Signals (Signal 1 section) + Architecture
diagram (submission flow) + the required JSON response format.

**What I'll ask the AI to generate:**
1. Flask app skeleton with `POST /submit` route stub, `GET /log` stub, SQLite setup.
2. The `classify_with_llm(text)` function that calls Groq and returns a float.

**Verification:** I'll call `classify_with_llm()` directly on 3 test inputs (clearly AI,
clearly human, borderline) and check that the float output varies meaningfully before
wiring it into the endpoint.

### Milestone 4 — Second signal + confidence scoring

**Spec sections provided to AI:** Detection Signals (Signal 2 section) + Uncertainty
Representation section + Architecture diagram.

**What I'll ask the AI to generate:**
1. `compute_stylometric_score(text)` function returning a float.
2. `combine_scores(llm_score, style_score)` function implementing the 60/40 weighted average.

**Verification:** Run all 4 test inputs from the milestone spec. Both signals should
produce scores that differ between the "clearly AI" and "clearly human" cases.

### Milestone 5 — Production layer

**Spec sections provided to AI:** Transparency Label Variants section + Appeals Workflow
section + Architecture diagram (appeal flow).

**What I'll ask the AI to generate:**
1. `generate_label(confidence, attribution)` function returning the exact label text.
2. `POST /appeal` endpoint with SQLite update and appeal log entry.

**Verification:**
- Hit `/submit` with inputs that produce each of the 3 confidence bands, confirm label text
  matches the three variants written in this document.
- Hit `/appeal` with a valid `content_id`, then call `GET /log` to confirm status changed
  to `"under_review"` and appeal reasoning is logged.
