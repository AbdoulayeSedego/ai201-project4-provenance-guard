"""
SQLite setup and audit log helpers.

Two tables:
  submissions — one row per POST /submit call
  appeals     — one row per POST /appeal call (linked by content_id)
"""

import sqlite3
import os

DB_PATH = os.environ.get("DB_PATH", "provenance.db")


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # rows behave like dicts
    return conn


def init_db():
    """Create tables if they don't exist. Called once at app startup."""
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS submissions (
                content_id    TEXT PRIMARY KEY,
                creator_id    TEXT NOT NULL,
                timestamp     TEXT NOT NULL,
                text_snippet  TEXT NOT NULL,
                llm_score     REAL,
                style_score   REAL,
                phrase_score  REAL,
                confidence    REAL NOT NULL,
                attribution   TEXT NOT NULL,
                label_code    TEXT NOT NULL,
                status        TEXT NOT NULL DEFAULT 'classified'
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS appeals (
                appeal_id         TEXT PRIMARY KEY,
                content_id        TEXT NOT NULL,
                appeal_timestamp  TEXT NOT NULL,
                creator_reasoning TEXT NOT NULL,
                FOREIGN KEY (content_id) REFERENCES submissions(content_id)
            )
        """)
        # Migrate existing DBs that don't have phrase_score column yet
        try:
            conn.execute("ALTER TABLE submissions ADD COLUMN phrase_score REAL")
        except Exception:
            pass
        conn.commit()


def log_submission(row: dict):
    """Insert a new submission row into the audit log."""
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO submissions
              (content_id, creator_id, timestamp, text_snippet,
               llm_score, style_score, phrase_score, confidence,
               attribution, label_code, status)
            VALUES
              (:content_id, :creator_id, :timestamp, :text_snippet,
               :llm_score, :style_score, :phrase_score, :confidence,
               :attribution, :label_code, :status)
            """,
            row,
        )
        conn.commit()


def log_appeal(row: dict):
    """Insert an appeal row and update the submission status."""
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO appeals (appeal_id, content_id, appeal_timestamp, creator_reasoning)
            VALUES (:appeal_id, :content_id, :appeal_timestamp, :creator_reasoning)
            """,
            row,
        )
        conn.execute(
            "UPDATE submissions SET status = 'under_review' WHERE content_id = ?",
            (row["content_id"],),
        )
        conn.commit()


def get_submission(content_id: str):
    """Return a single submission row or None."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM submissions WHERE content_id = ?", (content_id,)
        ).fetchone()
        return dict(row) if row else None


def get_log(limit: int = 50) -> list[dict]:
    """
    Return the most recent audit entries (submissions + appeals) merged and
    sorted by timestamp descending.
    """
    with get_connection() as conn:
        submissions = conn.execute(
            """
            SELECT 'submission' AS type, content_id, creator_id, timestamp,
                   text_snippet, llm_score, style_score, phrase_score, confidence,
                   attribution, label_code, status,
                   NULL AS appeal_id, NULL AS creator_reasoning, NULL AS appeal_timestamp
            FROM submissions
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

        appeals = conn.execute(
            """
            SELECT 'appeal' AS type,
                   a.content_id, s.creator_id, a.appeal_timestamp AS timestamp,
                   s.text_snippet, s.llm_score, s.style_score, s.phrase_score,
                   s.confidence, s.attribution, s.label_code, s.status,
                   a.appeal_id, a.creator_reasoning, a.appeal_timestamp
            FROM appeals a
            JOIN submissions s ON a.content_id = s.content_id
            ORDER BY a.appeal_timestamp DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    entries = [dict(r) for r in submissions] + [dict(r) for r in appeals]
    entries.sort(key=lambda e: e["timestamp"], reverse=True)
    return entries[:limit]


def get_analytics() -> dict:
    """Return aggregated stats for the analytics dashboard."""
    with get_connection() as conn:
        total = conn.execute("SELECT COUNT(*) FROM submissions").fetchone()[0]

        rows = conn.execute(
            "SELECT attribution, COUNT(*) as cnt FROM submissions GROUP BY attribution"
        ).fetchall()
        breakdown = {r["attribution"]: r["cnt"] for r in rows}

        appeals_count = conn.execute("SELECT COUNT(*) FROM appeals").fetchone()[0]

        avg_row = conn.execute(
            "SELECT AVG(confidence) as avg_conf, AVG(llm_score) as avg_llm, "
            "AVG(style_score) as avg_style, AVG(phrase_score) as avg_phrase "
            "FROM submissions"
        ).fetchone()

        recent = conn.execute(
            """
            SELECT content_id, creator_id, timestamp, attribution, confidence, status
            FROM submissions ORDER BY timestamp DESC LIMIT 5
            """
        ).fetchall()

    return {
        "total_submissions": total,
        "attribution_breakdown": {
            "likely_ai":    breakdown.get("likely_ai", 0),
            "uncertain":    breakdown.get("uncertain", 0),
            "likely_human": breakdown.get("likely_human", 0),
        },
        "appeals_count": appeals_count,
        "appeal_rate": round(appeals_count / total, 3) if total > 0 else 0,
        "avg_confidence":  round((avg_row["avg_conf"] or 0), 3),
        "avg_llm_score":   round((avg_row["avg_llm"] or 0), 3),
        "avg_style_score": round((avg_row["avg_style"] or 0), 3),
        "avg_phrase_score": round((avg_row["avg_phrase"] or 0), 3),
        "recent_submissions": [dict(r) for r in recent],
    }
