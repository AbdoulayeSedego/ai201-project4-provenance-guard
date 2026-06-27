"""
Provenance Guard — Flask API

Endpoints:
  POST /submit  — submit content for attribution analysis
  POST /appeal  — contest a classification
  GET  /log     — view structured audit log (most recent entries first)
  GET  /health  — sanity check
"""

import uuid
from datetime import datetime, timezone

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

import database as db
import signals as sig
from labels import generate_label

load_dotenv()  # reads GROQ_API_KEY from .env

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Rate Limiter
# ---------------------------------------------------------------------------
# Reasoning for chosen limits:
#   10 per minute  — A human creator submitting their own work won't hit this;
#                    a script flooding the system will be blocked quickly.
#   100 per day    — Generous enough for power users; prevents bulk abuse.
# ---------------------------------------------------------------------------
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],
    storage_uri="memory://",
)

# ---------------------------------------------------------------------------
# Database init
# ---------------------------------------------------------------------------
with app.app_context():
    db.init_db()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "provenance-guard"})


@app.route("/submit", methods=["POST"])
@limiter.limit("10 per minute;100 per day")
def submit():
    """
    Accept a piece of text for attribution analysis.

    Request body (JSON):
      {
        "text":       "...",        required — the content to analyze
        "creator_id": "user-123"    required — platform user identifier
      }

    Response:
      {
        "content_id":  "uuid",
        "attribution": "likely_ai" | "uncertain" | "likely_human",
        "confidence":  0.0–1.0,
        "llm_score":   0.0–1.0,
        "style_score": 0.0–1.0,
        "label_code":  "likely_ai" | "uncertain" | "likely_human",
        "label_text":  "verbatim display text",
        "status":      "classified"
      }
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be JSON"}), 400

    text = (data.get("text") or "").strip()
    creator_id = (data.get("creator_id") or "").strip()

    if not text:
        return jsonify({"error": "text field is required"}), 400
    if not creator_id:
        return jsonify({"error": "creator_id field is required"}), 400
    if len(text) < 20:
        return jsonify({"error": "text is too short for meaningful analysis (min 20 chars)"}), 400

    # --- Signal 1: LLM classifier ---
    llm_score, llm_reasoning = sig.classify_with_llm(text)

    # --- Signal 2: Stylometric heuristics ---
    style_result = sig.compute_stylometric_score(text)
    style_score = style_result["style_score"]

    # --- Combine into confidence score ---
    confidence = sig.combine_scores(llm_score, style_score)
    attribution = sig.get_attribution(confidence)

    # --- Generate transparency label ---
    label = generate_label(confidence, attribution)

    # --- Assign unique content ID ---
    content_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).isoformat()

    # --- Write to audit log ---
    db.log_submission({
        "content_id":   content_id,
        "creator_id":   creator_id,
        "timestamp":    timestamp,
        "text_snippet": text[:200],  # store only first 200 chars
        "llm_score":    llm_score,
        "style_score":  style_score,
        "confidence":   confidence,
        "attribution":  attribution,
        "label_code":   label["label_code"],
        "status":       "classified",
    })

    return jsonify({
        "content_id":  content_id,
        "attribution": attribution,
        "confidence":  confidence,
        "llm_score":   llm_score,
        "style_score": style_score,
        "label_code":  label["label_code"],
        "label_text":  label["label_text"],
        "status":      "classified",
        # include sub-scores for transparency
        "stylometric_breakdown": style_result["sub_scores"],
        "llm_reasoning": llm_reasoning,
    })


@app.route("/appeal", methods=["POST"])
def appeal():
    """
    Contest a classification.

    Request body (JSON):
      {
        "content_id":        "uuid",   required
        "creator_reasoning": "..."     required — the creator's explanation
      }

    Response:
      {
        "content_id": "uuid",
        "status":     "under_review",
        "message":    "..."
      }
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be JSON"}), 400

    content_id = (data.get("content_id") or "").strip()
    reasoning = (data.get("creator_reasoning") or "").strip()

    if not content_id:
        return jsonify({"error": "content_id field is required"}), 400
    if not reasoning:
        return jsonify({"error": "creator_reasoning field is required"}), 400
    if len(reasoning) > 2000:
        return jsonify({"error": "creator_reasoning must be 2000 characters or fewer"}), 400

    submission = db.get_submission(content_id)
    if not submission:
        return jsonify({"error": f"No submission found with content_id: {content_id}"}), 404

    if submission["status"] == "under_review":
        return jsonify({
            "content_id": content_id,
            "status":     "under_review",
            "message":    "This content is already under review.",
        }), 200

    db.log_appeal({
        "appeal_id":        str(uuid.uuid4()),
        "content_id":       content_id,
        "appeal_timestamp": datetime.now(timezone.utc).isoformat(),
        "creator_reasoning": reasoning,
    })

    return jsonify({
        "content_id": content_id,
        "status":     "under_review",
        "message": (
            "Your appeal has been received and your content is now marked as under review. "
            "Our team will assess it. Thank you for helping us improve accuracy."
        ),
    })


@app.route("/log", methods=["GET"])
def get_log():
    """
    Return the most recent audit log entries as structured JSON.

    Query param:
      limit — number of entries to return (default 50, max 200)
    """
    try:
        limit = min(int(request.args.get("limit", 50)), 200)
    except ValueError:
        limit = 50

    entries = db.get_log(limit=limit)
    return jsonify({"count": len(entries), "entries": entries})


# ---------------------------------------------------------------------------
# Rate limit error handler
# ---------------------------------------------------------------------------

@app.errorhandler(429)
def rate_limit_exceeded(e):
    return jsonify({
        "error": "Rate limit exceeded",
        "message": str(e.description),
        "retry_after": "Please wait before submitting again.",
    }), 429


if __name__ == "__main__":
    app.run(debug=True, port=5001)
