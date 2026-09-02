"""Build leaderboard / report tables with pandas."""
from __future__ import annotations

import pandas as pd

from . import database as db


def _participant_stats(quiz_id: int):
    questions = db.get_questions(quiz_id)
    total_q = len(questions)
    stats = []
    for p in db.get_participants(quiz_id):
        resp = db.responses_by_question(p["id"])
        answered = sum(1 for r in resp.values() if r["selected_answer"])
        correct = sum(1 for r in resp.values() if r["is_correct"])
        incorrect = answered - correct
        unanswered = total_q - answered
        total_time = round(sum((r["time_taken"] or 0) for r in resp.values()), 2)
        pct = round(100 * correct / total_q, 1) if total_q else 0.0
        stats.append({
            "participant": p,
            "score": correct,
            "total_questions": total_q,
            "percentage": pct,
            "correct": correct,
            "incorrect": incorrect,
            "unanswered": unanswered,
            "total_time": total_time,
        })
    stats.sort(key=lambda s: (-s["score"], s["total_time"], s["participant"]["name"].lower()))
    return stats, questions


def leaderboard_df(quiz_id: int) -> pd.DataFrame:
    stats, _ = _participant_stats(quiz_id)
    rows = []
    for rank, s in enumerate(stats, start=1):
        rows.append({
            "Rank": rank,
            "Name": s["participant"]["name"],
            "Registration No.": s["participant"]["reg_no"],
            "Score": s["score"],
            "Time (s)": s["total_time"],
            "Percentage": f"{s['percentage']}%",
        })
    return pd.DataFrame(
        rows, columns=["Rank", "Name", "Registration No.", "Score", "Time (s)", "Percentage"]
    )


def summary_df(quiz_id: int) -> pd.DataFrame:
    stats, _ = _participant_stats(quiz_id)
    rows = []
    for rank, s in enumerate(stats, start=1):
        p = s["participant"]
        rows.append({
            "Rank": rank,
            "Name": p["name"],
            "Registration Number": p["reg_no"],
            "Email": p["email"],
            "Score": s["score"],
            "Total Questions": s["total_questions"],
            "Percentage": s["percentage"],
            "Correct Answers": s["correct"],
            "Incorrect Answers": s["incorrect"],
            "Unanswered": s["unanswered"],
            "Total Time (s)": s["total_time"],
        })
    return pd.DataFrame(rows, columns=[
        "Rank", "Name", "Registration Number", "Email", "Score", "Total Questions",
        "Percentage", "Correct Answers", "Incorrect Answers", "Unanswered", "Total Time (s)",
    ])


def detailed_df(quiz_id: int) -> pd.DataFrame:
    stats, questions = _participant_stats(quiz_id)
    rows = []
    for s in stats:
        p = s["participant"]
        resp = db.responses_by_question(p["id"])
        for q in questions:
            r = resp.get(q["id"])
            selected = r["selected_answer"] if r and r["selected_answer"] else ""
            if not selected:
                verdict = "Unanswered"
            elif r["is_correct"]:
                verdict = "Correct"
            else:
                verdict = "Incorrect"
            taken = r["time_taken"] if r and r["time_taken"] is not None else None
            rows.append({
                "Name": p["name"],
                "Registration Number": p["reg_no"],
                "Question Number": q["q_order"],
                "Question": q["question"],
                "Question Type": q["type"],
                "Selected Answer": selected,
                "Correct Answer": q["correct_answer"],
                "Correct/Incorrect": verdict,
                "Time Taken (s)": ("" if taken is None else f"{taken:.1f}"),
            })
    return pd.DataFrame(rows, columns=[
        "Name", "Registration Number", "Question Number", "Question", "Question Type",
        "Selected Answer", "Correct Answer", "Correct/Incorrect", "Time Taken (s)",
    ])


def quiz_history() -> list[dict]:
    out = []
    for quiz in db.list_quizzes():
        stats, _ = _participant_stats(quiz["id"])
        n = len(stats)
        avg = round(sum(s["score"] for s in stats) / n, 1) if n else 0.0
        total_q = stats[0]["total_questions"] if stats else db.question_count(quiz["id"])
        out.append({
            "title": quiz["title"],
            "code": quiz["code"],
            "created_at": quiz["created_at"],
            "status": quiz["status"],
            "participants": n,
            "average_score": avg,
            "total_questions": total_q,
        })
    return out
