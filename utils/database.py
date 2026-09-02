"""SQLite persistence layer for the live quiz platform.

Everything that must survive a browser refresh or be shared between
participants lives here — Streamlit ``session_state`` is only used for
per-browser convenience.

The database is deliberately tiny (4 tables) and every write goes through a
process-level lock so concurrent Streamlit script threads cannot corrupt it.
For ~20 participants this is more than fast enough.
"""
from __future__ import annotations

import os
import random
import sqlite3
import string
import threading
from datetime import datetime

from . import quiz as quizlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.environ.get("QUIZ_DB_PATH") or os.path.join(ROOT, "data", "quiz.db")

_write_lock = threading.Lock()

# Unambiguous alphabet for quiz codes (no 0/O, 1/I).
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def get_conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS quizzes (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    code                 TEXT UNIQUE NOT NULL,
    title                TEXT NOT NULL,
    status               TEXT NOT NULL DEFAULT 'lobby',      -- lobby | running | finished
    current_q_index      INTEGER NOT NULL DEFAULT 0,
    current_q_status     TEXT NOT NULL DEFAULT 'pending',    -- pending | active | ended
    current_q_started_at REAL,
    show_leaderboard     INTEGER NOT NULL DEFAULT 1,
    default_time_limit   INTEGER NOT NULL DEFAULT 30,
    created_at           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS questions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    quiz_id        INTEGER NOT NULL REFERENCES quizzes(id) ON DELETE CASCADE,
    q_order        INTEGER NOT NULL,
    question       TEXT NOT NULL,
    type           TEXT NOT NULL,
    option_a       TEXT DEFAULT '',
    option_b       TEXT DEFAULT '',
    option_c       TEXT DEFAULT '',
    option_d       TEXT DEFAULT '',
    correct_answer TEXT NOT NULL,
    time_limit     INTEGER NOT NULL DEFAULT 30
);

CREATE TABLE IF NOT EXISTS participants (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    quiz_id   INTEGER NOT NULL REFERENCES quizzes(id) ON DELETE CASCADE,
    name      TEXT NOT NULL,
    reg_no    TEXT NOT NULL,
    email     TEXT DEFAULT '',
    joined_at TEXT NOT NULL,
    UNIQUE (quiz_id, reg_no)
);

CREATE TABLE IF NOT EXISTS responses (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    quiz_id         INTEGER NOT NULL,
    participant_id  INTEGER NOT NULL REFERENCES participants(id) ON DELETE CASCADE,
    question_id     INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    selected_answer TEXT,
    is_correct      INTEGER NOT NULL DEFAULT 0,
    time_taken      REAL,
    answered_at     TEXT NOT NULL,
    UNIQUE (participant_id, question_id)
);
"""


def init_db() -> None:
    with _write_lock, get_conn() as conn:
        conn.executescript(SCHEMA)


# ---------------------------------------------------------------------------
# Quizzes
# ---------------------------------------------------------------------------

def _generate_code(conn, length: int = 5) -> str:
    for _ in range(50):
        code = "".join(random.choice(_CODE_ALPHABET) for _ in range(length))
        exists = conn.execute("SELECT 1 FROM quizzes WHERE code = ?", (code,)).fetchone()
        if not exists:
            return code
    raise RuntimeError("Could not allocate a unique quiz code")


def create_quiz(title: str, question_rows: list[dict], default_time_limit: int = 30) -> dict:
    with _write_lock, get_conn() as conn:
        code = _generate_code(conn)
        cur = conn.execute(
            "INSERT INTO quizzes (code, title, default_time_limit, created_at) VALUES (?, ?, ?, ?)",
            (code, title.strip(), int(default_time_limit), _now_iso()),
        )
        quiz_id = cur.lastrowid
        _insert_questions(conn, quiz_id, question_rows, default_time_limit)
        return dict(conn.execute("SELECT * FROM quizzes WHERE id = ?", (quiz_id,)).fetchone())


def _insert_questions(conn, quiz_id: int, rows: list[dict], default_time_limit: int) -> None:
    start = conn.execute(
        "SELECT COALESCE(MAX(q_order), 0) FROM questions WHERE quiz_id = ?", (quiz_id,)
    ).fetchone()[0]
    for offset, r in enumerate(rows, start=1):
        conn.execute(
            """INSERT INTO questions
                 (quiz_id, q_order, question, type, option_a, option_b, option_c, option_d,
                  correct_answer, time_limit)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                quiz_id,
                start + offset,
                r["question"],
                r["type"],
                r.get("option_a", ""),
                r.get("option_b", ""),
                r.get("option_c", ""),
                r.get("option_d", ""),
                r["correct_answer"],
                int(r.get("time_limit") or default_time_limit),
            ),
        )


def add_questions(quiz_id: int, rows: list[dict], default_time_limit: int = 30) -> None:
    with _write_lock, get_conn() as conn:
        _insert_questions(conn, quiz_id, rows, default_time_limit)


def delete_question(question_id: int) -> None:
    with _write_lock, get_conn() as conn:
        row = conn.execute("SELECT quiz_id FROM questions WHERE id = ?", (question_id,)).fetchone()
        conn.execute("DELETE FROM questions WHERE id = ?", (question_id,))
        if row:
            _renumber_questions(conn, row["quiz_id"])


def _renumber_questions(conn, quiz_id: int) -> None:
    rows = conn.execute(
        "SELECT id FROM questions WHERE quiz_id = ? ORDER BY q_order, id", (quiz_id,)
    ).fetchall()
    for i, r in enumerate(rows, start=1):
        conn.execute("UPDATE questions SET q_order = ? WHERE id = ?", (i, r["id"]))


def get_quiz(quiz_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM quizzes WHERE id = ?", (quiz_id,)).fetchone()
        return dict(row) if row else None


def get_quiz_by_code(code: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM quizzes WHERE code = ?", (str(code).strip().upper(),)
        ).fetchone()
        return dict(row) if row else None


def list_quizzes() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM quizzes ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]


def set_quiz_field(quiz_id: int, **fields) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    with _write_lock, get_conn() as conn:
        conn.execute(f"UPDATE quizzes SET {cols} WHERE id = ?", (*fields.values(), quiz_id))


# ---------------------------------------------------------------------------
# Questions
# ---------------------------------------------------------------------------

def _row_to_question(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["options"] = [
        (L, d[f"option_{L.lower()}"]) for L in "ABCD" if str(d.get(f"option_{L.lower()}", "")).strip()
    ]
    return d


def get_questions(quiz_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM questions WHERE quiz_id = ? ORDER BY q_order, id", (quiz_id,)
        ).fetchall()
        return [_row_to_question(r) for r in rows]


def question_count(quiz_id: int) -> int:
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM questions WHERE quiz_id = ?", (quiz_id,)
        ).fetchone()[0]


# ---------------------------------------------------------------------------
# Participants
# ---------------------------------------------------------------------------

def register_participant(quiz_id: int, name: str, reg_no: str, email: str = ""):
    """Register a participant or resume an existing one.

    Returns ``(participant_dict, note)``. Raises ``ValueError`` when the
    registration number is already used by a *different* name (so a refresh by
    the same person resumes, but a clash is rejected).
    """
    name = (name or "").strip()
    reg_key = (reg_no or "").strip().upper()
    email = (email or "").strip()
    if not name:
        raise ValueError("Name is required.")
    if not reg_key:
        raise ValueError("Registration number is required.")

    with _write_lock, get_conn() as conn:
        existing = conn.execute(
            "SELECT * FROM participants WHERE quiz_id = ? AND reg_no = ?", (quiz_id, reg_key)
        ).fetchone()
        if existing:
            if existing["name"].strip().lower() != name.lower():
                raise ValueError(
                    "This registration number has already joined this quiz."
                )
            return dict(existing), "Welcome back — resuming your session."
        cur = conn.execute(
            "INSERT INTO participants (quiz_id, name, reg_no, email, joined_at) VALUES (?, ?, ?, ?, ?)",
            (quiz_id, name, reg_key, email, _now_iso()),
        )
        row = conn.execute("SELECT * FROM participants WHERE id = ?", (cur.lastrowid,)).fetchone()
        return dict(row), ""


def get_participant(participant_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM participants WHERE id = ?", (participant_id,)).fetchone()
        return dict(row) if row else None


def participant_count(quiz_id: int) -> int:
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM participants WHERE quiz_id = ?", (quiz_id,)
        ).fetchone()[0]


def get_participants(quiz_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM participants WHERE quiz_id = ? ORDER BY joined_at, id", (quiz_id,)
        ).fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------

def submit_response(quiz_id: int, participant_id: int, question: dict, selected, time_taken):
    """Persist a participant's answer. Idempotent — the first write wins, so a
    page refresh or a late auto-submit can never overwrite a real answer."""
    selected = None if selected is None or str(selected).strip() == "" else str(selected).strip()
    correct = 1 if quizlib.is_correct(question, selected) else 0
    tt = None if time_taken is None else round(float(time_taken), 2)
    with _write_lock, get_conn() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO responses
                 (quiz_id, participant_id, question_id, selected_answer, is_correct, time_taken, answered_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (quiz_id, participant_id, question["id"], selected, correct, tt, _now_iso()),
        )


def get_response(participant_id: int, question_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM responses WHERE participant_id = ? AND question_id = ?",
            (participant_id, question_id),
        ).fetchone()
        return dict(row) if row else None


def responses_by_question(participant_id: int) -> dict[int, dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM responses WHERE participant_id = ?", (participant_id,)
        ).fetchall()
        return {r["question_id"]: dict(r) for r in rows}


def answer_count(quiz_id: int, question_id: int) -> int:
    with get_conn() as conn:
        return conn.execute(
            """SELECT COUNT(*) FROM responses
               WHERE quiz_id = ? AND question_id = ? AND selected_answer IS NOT NULL""",
            (quiz_id, question_id),
        ).fetchone()[0]
