"""End-to-end simulation of a full quiz without Streamlit.

Runs the exact code paths the app uses (register -> answer -> score -> report)
for a batch of simulated participants and asserts the results.

    python tests/simulate.py
"""
from __future__ import annotations

import os
import sys
import tempfile

# Point the database module at a throwaway file BEFORE importing it.
_TMP = tempfile.mkdtemp(prefix="quizsim_")
os.environ["QUIZ_DB_PATH"] = os.path.join(_TMP, "quiz.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import database as db  # noqa: E402
from utils import quiz as quizlib  # noqa: E402
from utils import reports  # noqa: E402

CSV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sample_questions.csv")


def main() -> None:
    db.init_db()

    rows, errors = quizlib.parse_questions_csv(CSV)
    assert not errors, errors
    print(f"Parsed {len(rows)} questions, no errors.")

    quiz = db.create_quiz("Simulation Quiz", rows, 30)
    quiz_id = quiz["id"]
    questions = db.get_questions(quiz_id)
    n = len(questions)
    print(f"Quiz code: {quiz['code']}  ({n} questions)")

    # --- register 10 participants -----------------------------------------
    people = [(f"Student {i}", f"23BCE{1000 + i}") for i in range(1, 11)]
    parts = []
    for name, reg in people:
        p, note = db.register_participant(quiz_id, name, reg)
        parts.append(p)

    assert len(db.get_participants(quiz_id)) == 10

    # duplicate registration number, same name -> resume
    again, note = db.register_participant(quiz_id, "Student 1", "23bce1001")
    assert again["id"] == parts[0]["id"] and "resum" in note.lower()
    # duplicate registration number, different name -> rejected
    try:
        db.register_participant(quiz_id, "Impostor", "23BCE1001")
        raise AssertionError("expected ValueError for reg-no clash")
    except ValueError:
        pass
    print("Duplicate-registration rules OK.")

    # --- answer every question ------------------------------------------
    # Student i answers the first i questions correctly, leaves the rest:
    #   - some unanswered (no row)
    #   - one deliberately wrong
    db.set_quiz_field(quiz_id, status="running")
    for i, p in enumerate(parts, start=1):
        for j, q in enumerate(questions):
            if j < i - 1:
                sel = q["correct_answer"]            # correct
                db.submit_response(quiz_id, p["id"], q, sel, 3.0)
            elif j == i - 1 and i <= n:
                wrong = "A" if q["type"] == "MCQ" and q["correct_answer"] != "A" else (
                    "False" if q["correct_answer"] == "True" else "True")
                db.submit_response(quiz_id, p["id"], q, wrong, 5.0)  # incorrect
            elif j == n - 1:
                db.submit_response(quiz_id, p["id"], q, None, 30.0)  # timed-out, unanswered
            # else: no row at all -> unanswered

    # idempotency: a late auto-submit must not overwrite a real answer
    db.submit_response(quiz_id, parts[9]["id"], questions[0], "ZZ", 30.0)
    r = db.get_response(parts[9]["id"], questions[0]["id"])
    assert r["selected_answer"] == questions[0]["correct_answer"], "answer was overwritten!"
    print("Answer idempotency OK.")

    db.set_quiz_field(quiz_id, status="finished")

    # --- verify scoring & reports -------------------------------------
    summary = reports.summary_df(quiz_id)
    detailed = reports.detailed_df(quiz_id)

    for idx, row in summary.iterrows():
        expected_correct = 0
        # student number from name
        num = int(row["Name"].split()[-1])
        expected_correct = min(num - 1, n)
        assert row["Score"] == expected_correct, (row["Name"], row["Score"], expected_correct)
        assert row["Correct Answers"] + row["Incorrect Answers"] + row["Unanswered"] == n
    print("Scoring matches expectations for all participants.")

    # leaderboard is sorted by score desc
    scores = list(summary["Score"])
    assert scores == sorted(scores, reverse=True)
    assert list(summary["Rank"]) == list(range(1, 11))

    required_summary_cols = {
        "Rank", "Name", "Registration Number", "Email", "Score", "Total Questions",
        "Percentage", "Correct Answers", "Incorrect Answers", "Unanswered",
    }
    assert required_summary_cols <= set(summary.columns)
    assert len(detailed) == 10 * n
    assert set(detailed["Correct/Incorrect"]) <= {"Correct", "Incorrect", "Unanswered"}

    out_dir = os.environ.get("QUIZ_SIM_OUT", _TMP)
    summary.to_csv(os.path.join(out_dir, "Quiz Results.csv"), index=False)
    detailed.to_csv(os.path.join(out_dir, "Detailed Quiz Responses.csv"), index=False)

    hist = reports.quiz_history()
    assert hist and hist[0]["participants"] == 10
    print(f"Quiz history OK: {hist[0]['title']} avg {hist[0]['average_score']}/{hist[0]['total_questions']}")

    print("\nSummary report:\n")
    print(summary.to_string(index=False))
    print(f"\nAll checks passed. CSVs written to {out_dir}")


if __name__ == "__main__":
    main()
