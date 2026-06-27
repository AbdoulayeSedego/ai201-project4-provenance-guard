"""
Transparency label generator.

Three variants depending on attribution + confidence:
  likely_ai    → high-confidence AI label
  uncertain    → uncertain label
  likely_human → high-confidence human label

The label text is what would be shown verbatim to a reader on the platform.
"""


def generate_label(confidence: float, attribution: str) -> dict:
    """
    Return the transparency label dict for a given confidence score.

    Args:
        confidence: float 0.0–1.0 (1.0 = high AI confidence)
        attribution: one of "likely_ai", "uncertain", "likely_human"

    Returns:
        {
          "label_code": str,
          "label_text": str,   ← verbatim display text
          "display_score": str ← human-readable percentage
        }
    """
    if attribution == "likely_ai":
        display_score = f"{round(confidence * 100)}%"
        label_text = (
            f"⚠️  AI-Assisted Content Detected\n"
            f"Our analysis found strong indicators that this content was generated or heavily "
            f"assisted by AI tools (confidence: {display_score}). This label helps readers "
            f"understand the content's origins.\n\n"
            f"If this is your original work and you believe this is incorrect, you have the "
            f"right to appeal this classification. Appeals are reviewed by our team."
        )
        return {
            "label_code": "likely_ai",
            "label_text": label_text,
            "display_score": display_score,
        }

    if attribution == "uncertain":
        display_score = f"{round(confidence * 100)}%"
        label_text = (
            f"ℹ️  Content Origin: Uncertain\n"
            f"Our system detected mixed signals and cannot confidently determine whether this "
            f"content is human-written or AI-assisted (confidence: {display_score}). No action "
            f"has been taken on your content.\n\n"
            f"If you'd like to clarify the origin of this work, you may submit an explanation "
            f"via our appeals process."
        )
        return {
            "label_code": "uncertain",
            "label_text": label_text,
            "display_score": display_score,
        }

    # likely_human
    human_confidence = f"{round((1.0 - confidence) * 100)}%"
    label_text = (
        f"✓  Original Human Authorship\n"
        f"Our analysis found strong indicators of original human authorship "
        f"({human_confidence} human confidence). No action required."
    )
    return {
        "label_code": "likely_human",
        "label_text": label_text,
        "display_score": human_confidence,
    }
