"""
Quick test script — run directly (not via pytest) to verify signals work.

Usage:
  .venv/bin/python test_signals.py
"""

from dotenv import load_dotenv
load_dotenv()

import signals as sig

SAMPLES = [
    (
        "clearly_ai",
        """Artificial intelligence represents a transformative paradigm shift in modern society.
It is important to note that while the benefits of AI are numerous, it is equally
essential to consider the ethical implications. Furthermore, stakeholders across
various sectors must collaborate to ensure responsible deployment.""",
    ),
    (
        "clearly_human",
        """ok so i finally tried that new ramen place downtown and honestly?
underwhelming. the broth was fine but they put WAY too much sodium in it and
i was thirsty for like three hours after. my friend got the spicy version and
said it was better. probably won't go back unless someone drags me there""",
    ),
    (
        "borderline_formal_human",
        """The relationship between monetary policy and asset price inflation has been
extensively studied in the literature. Central banks face a fundamental tension
between their mandate for price stability and the unintended consequences of
prolonged low interest rates on equity and real estate valuations.""",
    ),
    (
        "borderline_lightly_edited_ai",
        """I've been thinking a lot about remote work lately. There are genuine tradeoffs —
flexibility and no commute on one side, isolation and blurred work-life boundaries
on the other. Studies show productivity varies widely by individual and role type.""",
    ),
]


def run_tests():
    print("=" * 65)
    print("STYLOMETRIC SIGNAL TEST (no API call needed)")
    print("=" * 65)
    for label, text in SAMPLES:
        result = sig.compute_stylometric_score(text)
        print(f"\n[{label}]")
        print(f"  style_score : {result['style_score']:.4f}")
        for k, v in result["sub_scores"].items():
            print(f"    {k:<30} {v:.4f}")

    print("\n" + "=" * 65)
    print("LLM SIGNAL TEST (makes a Groq API call for each sample)")
    print("=" * 65)
    for label, text in SAMPLES:
        llm_score, reasoning = sig.classify_with_llm(text)
        style_result = sig.compute_stylometric_score(text)
        confidence = sig.combine_scores(llm_score, style_result["style_score"])
        attribution = sig.get_attribution(confidence)
        print(f"\n[{label}]")
        print(f"  llm_score   : {llm_score:.4f}")
        print(f"  style_score : {style_result['style_score']:.4f}")
        print(f"  confidence  : {confidence:.4f}  →  {attribution}")
        print(f"  reasoning   : {reasoning[:80]}...")


if __name__ == "__main__":
    run_tests()
