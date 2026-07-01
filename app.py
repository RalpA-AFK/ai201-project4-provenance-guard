"""Provenance Guard API.

Endpoints:
  POST /submit  — two-signal attribution + transparency label (rate limited)
  POST /appeal  — contest a verdict; moves status to under_review, logs the appeal
  GET  /log     — structured audit log (submissions + appeals)
  GET  /health  — liveness check

Every decision and appeal is written to a structured SQLite audit log.
"""

import uuid

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

import scoring
import signals
import storage

load_dotenv()

app = Flask(__name__)
storage.init_db()

# Rate limiting. See README for the reasoning behind these specific numbers.
# 10/min: no genuine writer submits >10 pieces a minute, but it trips a flood
# script immediately and protects the Groq quota (1 LLM call per submit).
# 100/day: comfortable headroom for a prolific writer or small editorial team
# while capping slow-drip abuse that stays under the per-minute limit.
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],
    storage_uri="memory://",
)


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


@app.post("/submit")
@limiter.limit("10 per minute;100 per day")
def submit():
    body = request.get_json(silent=True) or {}
    text = (body.get("text") or "").strip()
    creator_id = body.get("creator_id")

    if not text:
        return jsonify({"error": "field 'text' is required and must be non-empty"}), 400
    if not creator_id:
        return jsonify({"error": "field 'creator_id' is required"}), 400

    content_id = str(uuid.uuid4())

    # Two independent signals: semantic (LLM) and structural (stylometry).
    try:
        llm = signals.llm_signal(text)
    except Exception as exc:  # e.g. invalid/missing GROQ_API_KEY, network error
        return jsonify({
            "error": "The AI language-model signal is unavailable. Check that "
                     "GROQ_API_KEY in your .env is a valid key.",
            "detail": str(exc),
        }), 502
    stylometry = signals.stylometry_signal(text)

    # Combine into a single calibrated score (disagreement -> uncertainty).
    ai_score = scoring.combine(llm["p_ai"], stylometry["p_ai"])

    attribution = scoring.score_to_attribution(ai_score)
    confidence = scoring.reader_confidence(ai_score)
    label = scoring.make_label(attribution)  # placeholder until M5

    signal_detail = {"llm": llm, "stylometry": stylometry}
    entry = {
        "content_id": content_id,
        "creator_id": creator_id,
        "timestamp": storage.now_iso(),
        "text": text,
        "attribution": attribution,
        "confidence": confidence,
        "llm_score": llm["p_ai"],
        "stylometry_score": stylometry["p_ai"],
        "ai_score": ai_score,
        "signals": signal_detail,
        "status": "classified",
    }
    storage.log_decision(entry)

    return jsonify({
        "content_id": content_id,
        "attribution": attribution,
        "confidence": confidence,
        "ai_score": ai_score,
        "label": label,
        "signals": signal_detail,
    })


@app.post("/appeal")
def appeal():
    body = request.get_json(silent=True) or {}
    content_id = body.get("content_id")
    creator_reasoning = (body.get("creator_reasoning") or "").strip()

    if not content_id:
        return jsonify({"error": "field 'content_id' is required"}), 400
    if not creator_reasoning:
        return jsonify({"error": "field 'creator_reasoning' is required"}), 400

    updated = storage.add_appeal(content_id, creator_reasoning)
    if not updated:
        return jsonify({"error": f"no submission found for content_id {content_id}"}), 404

    return jsonify({
        "content_id": content_id,
        "status": "under_review",
        "message": "Appeal received. This content is now under review by a human moderator.",
    })


@app.get("/log")
def log():
    return jsonify({"entries": storage.get_log()})


if __name__ == "__main__":
    app.run(port=5000, debug=True)
