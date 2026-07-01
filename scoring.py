"""Score -> attribution/label mapping.

Thresholds are asymmetric on purpose: a false positive (labeling a human's work
as AI) is the worst error on a writing platform, so the AI band demands more
evidence (>= 0.80) than the human band (<= 0.30). See planning.md sections 2-3.

The combined score is produced by combine(): a weighted mean of the two signals,
then a disagreement term that pulls the result toward 0.5 (uncertain) when the
signals disagree — this is what stops a lone signal from producing a confident
(possibly false-positive) verdict.
"""

# ai_score is P(text is AI-generated). 0.5 = "refusing to guess".
AI_THRESHOLD = 0.80      # ai_score >= this -> likely AI
HUMAN_THRESHOLD = 0.30   # ai_score <= this -> likely human
# between the two -> uncertain

# The LLM is the more reliable single judge, so it carries more weight.
LLM_WEIGHT = 0.6
STYLOMETRY_WEIGHT = 0.4


def combine(llm_p: float, styl_p: float) -> float:
    """Combine two signal p_ai values into a single calibrated ai_score.

    base       = weighted mean of the signals
    d          = |llm_p - styl_p|  (how much they disagree)
    ai_score   = base * (1 - d) + 0.5 * d

    When the signals agree (d~0) a confident verdict is allowed; when they
    disagree (d large) the score is dragged toward 0.5. See planning.md section 1.
    """
    base = LLM_WEIGHT * llm_p + STYLOMETRY_WEIGHT * styl_p
    d = abs(llm_p - styl_p)
    ai_score = base * (1 - d) + 0.5 * d
    return round(max(0.0, min(1.0, ai_score)), 4)

ATTR_AI = "likely_ai"
ATTR_HUMAN = "likely_human"
ATTR_UNCERTAIN = "uncertain"


def score_to_attribution(ai_score: float) -> str:
    """Map an ai_score in [0,1] to one of three attribution bands."""
    if ai_score >= AI_THRESHOLD:
        return ATTR_AI
    if ai_score <= HUMAN_THRESHOLD:
        return ATTR_HUMAN
    return ATTR_UNCERTAIN


def reader_confidence(ai_score: float) -> float:
    """Reader-facing confidence: 0 at the uncertain middle, 1 at either extreme."""
    return round(2 * abs(ai_score - 0.5), 2)


# End-user transparency labels (planning.md section 3). Deliberately worded:
# the AI label is non-accusatory and always points to the appeal path; the
# uncertain label frames refusing to guess as an intentional choice.
_LABELS = {
    ATTR_AI: (
        "⚠️ Likely AI-generated. Our automated analysis found strong signs that "
        "this text was created with significant AI assistance. Automated "
        "detection is imperfect and can be wrong — if you're the author and "
        "disagree, you can appeal this label."
    ),
    ATTR_HUMAN: (
        "✓ Likely human-written. Our automated analysis found no strong signs of "
        "AI generation in this text. Automated checks aren't perfect, but this "
        "content reads as human-written."
    ),
    ATTR_UNCERTAIN: (
        "❓ Not enough evidence to say. Our automated checks were inconclusive or "
        "disagreed for this text, so we won't guess whether it was written by a "
        "human or AI. If you're the author, you can add context through an appeal."
    ),
}


def make_label(attribution: str) -> str:
    """Return the end-user transparency label text for an attribution band."""
    return _LABELS.get(attribution, _LABELS[ATTR_UNCERTAIN])
