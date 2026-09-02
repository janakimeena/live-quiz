"""Quiz helpers: CSV parsing, normalisation and scoring.

This module has no dependency on the database so it can be unit-tested in
isolation and reused by the report generator.
"""
from __future__ import annotations

import io
import time
from typing import Any

import pandas as pd

# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------

_MCQ_ALIASES = {
    "mcq", "multiple", "multiple choice", "multiplechoice", "multiple_choice",
    "single", "single choice", "choice", "m",
}
_TF_ALIASES = {
    "tf", "t/f", "truefalse", "true/false", "true false", "true_false",
    "boolean", "bool", "yesno", "yes/no", "t", "tf question",
}

_TRUE_TOKENS = {"true", "t", "1", "yes", "y", "correct"}
_FALSE_TOKENS = {"false", "f", "0", "no", "n", "incorrect"}

DEFAULT_TIME_LIMIT = 30


def normalize_type(value: str) -> str | None:
    key = str(value or "").strip().lower()
    if key in _MCQ_ALIASES:
        return "MCQ"
    if key in _TF_ALIASES:
        return "TF"
    return None


def normalize_tf(value: str) -> str | None:
    key = str(value or "").strip().lower()
    if key in _TRUE_TOKENS:
        return "True"
    if key in _FALSE_TOKENS:
        return "False"
    return None


def _parse_time_limit(value: Any) -> int | None:
    try:
        v = int(float(str(value).strip()))
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# CSV parsing
# ---------------------------------------------------------------------------

REQUIRED_COLUMNS = {"question", "type", "correct_answer"}


def parse_questions_csv(source: Any, default_time_limit: int = DEFAULT_TIME_LIMIT):
    """Parse an uploaded question CSV.

    ``source`` may be a file-like object, a path, or raw bytes/str.
    Returns ``(rows, errors)`` where ``rows`` is a list of question dicts ready
    for :func:`utils.database.add_questions` and ``errors`` is a list of
    human-readable strings.
    """
    errors: list[str] = []

    if isinstance(source, str) and "\n" in source:
        source = source.encode("utf-8")
    if hasattr(source, "read"):
        source = source.read()
    if isinstance(source, str):  # a file path
        with open(source, "rb") as fh:
            source = fh.read()
    raw_bytes = source if isinstance(source, bytes) else bytes(source)

    df = None
    last_exc = None
    # Try comma first, then semicolon/tab (Excel in some locales), utf-8-sig strips a BOM.
    for sep in (",", ";", "\t"):
        try:
            candidate = pd.read_csv(
                io.BytesIO(raw_bytes), dtype=str, sep=sep, encoding="utf-8-sig"
            )
            if candidate.shape[1] >= 3:
                df = candidate
                break
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
    if df is None:
        return [], [f"Could not read CSV: {last_exc}"]

    df = df.fillna("")
    df.columns = [str(c).strip().lstrip("﻿").lower() for c in df.columns]

    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        return [], [f"CSV is missing required column(s): {', '.join(sorted(missing))}"]

    rows: list[dict] = []
    order = 0
    for i, raw in df.iterrows():
        line = i + 2  # header is line 1
        question = str(raw.get("question", "")).strip()
        if not question:
            continue  # silently skip blank lines

        qtype = normalize_type(raw.get("type", ""))
        if qtype is None:
            errors.append(f"Line {line}: unknown question type '{raw.get('type', '')}'.")
            continue

        time_limit = _parse_time_limit(raw.get("time_limit")) or default_time_limit
        correct_raw = str(raw.get("correct_answer", "")).strip()

        if qtype == "MCQ":
            options = {
                L: str(raw.get(f"option_{L.lower()}", "")).strip() for L in "ABCD"
            }
            present = [L for L in "ABCD" if options[L]]
            if len(present) < 2:
                errors.append(f"Line {line}: MCQ needs at least two options.")
                continue
            correct = correct_raw.upper()
            if correct not in present:
                by_text = [L for L in present if options[L].lower() == correct_raw.lower()]
                if by_text:
                    correct = by_text[0]
                else:
                    errors.append(
                        f"Line {line}: correct_answer '{correct_raw}' does not match an option."
                    )
                    continue
            order += 1
            rows.append({
                "q_order": order,
                "question": question,
                "type": "MCQ",
                "option_a": options["A"],
                "option_b": options["B"],
                "option_c": options["C"],
                "option_d": options["D"],
                "correct_answer": correct,
                "time_limit": time_limit,
            })
        else:  # TF
            correct = normalize_tf(correct_raw)
            if correct is None:
                errors.append(
                    f"Line {line}: True/False correct_answer must be True or False (got '{correct_raw}')."
                )
                continue
            order += 1
            rows.append({
                "q_order": order,
                "question": question,
                "type": "TF",
                "option_a": "True",
                "option_b": "False",
                "option_c": "",
                "option_d": "",
                "correct_answer": correct,
                "time_limit": time_limit,
            })

    if not rows and not errors:
        errors.append("No valid questions found in the CSV.")
    return rows, errors


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def is_correct(question: dict, selected: Any) -> bool:
    """Return True when ``selected`` is the right answer for ``question``.

    ``question`` is a dict/row with ``type`` and ``correct_answer`` keys.
    ``selected`` of ``None``/empty is always wrong (unanswered).
    """
    if selected is None or str(selected).strip() == "":
        return False
    correct = str(question["correct_answer"]).strip().lower()
    return str(selected).strip().lower() == correct


def option_letters(question: dict) -> list[str]:
    return [L for L in "ABCD" if str(question.get(f"option_{L.lower()}", "")).strip()]


# ---------------------------------------------------------------------------
# Live-question state
# ---------------------------------------------------------------------------

def question_closed(
    current_status: str,
    started_at: float | None,
    time_limit: float,
    n_participants: int,
    n_answered: int,
    now: float | None = None,
) -> bool:
    """A running question is *closed* once either its time limit has elapsed or
    every registered participant has submitted an answer. The host cannot move
    to the next question until this is true."""
    if current_status != "active" or not started_at:
        return False
    now = time.time() if now is None else now
    if now - started_at >= time_limit:
        return True
    return n_participants > 0 and n_answered >= n_participants
