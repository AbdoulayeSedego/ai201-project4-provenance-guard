"""
Provenance Guard — Flask API + UI

Endpoints:
  GET  /           — serves the web UI
  POST /submit     — submit content for attribution analysis
  POST /appeal     — contest a classification
  GET  /log        — view structured audit log
  GET  /analytics  — aggregated stats for the dashboard
  GET  /health     — sanity check
"""

import uuid
from datetime import datetime, timezone

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

import database as db
import signals as sig
from labels import generate_label

load_dotenv()

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Rate Limiter
# ---------------------------------------------------------------------------
# 10/min  — a human submitting real work never hits this
# 100/day — generous for power users; blocks bulk scripts
# ---------------------------------------------------------------------------
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],
    storage_uri="memory://",
)

with app.app_context():
    db.init_db()


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")


# ---------------------------------------------------------------------------
# API Endpoints
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
      { "text": "...", "creator_id": "user-123" }
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

    # --- Signal 3: AI phrase detector ---
    phrase_result = sig.detect_ai_phrases(text)
    phrase_score = phrase_result["phrase_score"]

    # --- Ensemble confidence score (stretch feature: 3 signals) ---
    confidence = sig.combine_scores(llm_score, style_score, phrase_score)
    attribution = sig.get_attribution(confidence)

    # --- Generate transparency label ---
    label = generate_label(confidence, attribution)

    content_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).isoformat()

    db.log_submission({
        "content_id":   content_id,
        "creator_id":   creator_id,
        "timestamp":    timestamp,
        "text_snippet": text[:200],
        "llm_score":    llm_score,
        "style_score":  style_score,
        "phrase_score": phrase_score,
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
        "phrase_score": phrase_score,
        "label_code":  label["label_code"],
        "label_text":  label["label_text"],
        "status":      "classified",
        "stylometric_breakdown": style_result["sub_scores"],
        "phrase_breakdown": {
            "match_count":        phrase_result["match_count"],
            "tells_per_100_words": phrase_result["tells_per_100_words"],
            "matched_phrases":    phrase_result["matches"],
        },
        "llm_reasoning": llm_reasoning,
    })


@app.route("/appeal", methods=["POST"])
def appeal():
    """
    Contest a classification.

    Request body (JSON):
      { "content_id": "uuid", "creator_reasoning": "..." }
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
        "appeal_id":         str(uuid.uuid4()),
        "content_id":        content_id,
        "appeal_timestamp":  datetime.now(timezone.utc).isoformat(),
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
    """Return the most recent audit log entries as structured JSON."""
    try:
        limit = min(int(request.args.get("limit", 50)), 200)
    except ValueError:
        limit = 50
    entries = db.get_log(limit=limit)
    return jsonify({"count": len(entries), "entries": entries})


@app.route("/analytics", methods=["GET"])
def analytics():
    """Return aggregated detection stats for the analytics dashboard."""
    return jsonify(db.get_analytics())


# ---------------------------------------------------------------------------
# Error handlers
# ---------------------------------------------------------------------------

@app.errorhandler(429)
def rate_limit_exceeded(e):
    return jsonify({
        "error":       "Rate limit exceeded",
        "message":     str(e.description),
        "retry_after": "Please wait before submitting again.",
    }), 429


if __name__ == "__main__":
    app.run(debug=True, port=5001)
