"""Full live-quiz loop: 1 host + 2 participants through 2 questions.

Drives the real Streamlit script (participant fragments, host control, state
transitions, timer auto-submit) with three independent AppTest "browsers"
sharing one database.

    python tests/test_integration.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

_TMP = tempfile.mkdtemp(prefix="quizint_")
os.environ["QUIZ_DB_PATH"] = os.path.join(_TMP, "quiz.db")
os.environ["QUIZ_ADMIN_PASSWORD"] = "pw"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from streamlit.testing.v1 import AppTest  # noqa: E402

from utils import database as db  # noqa: E402
from utils import quiz as quizlib  # noqa: E402
from utils import reports  # noqa: E402

APP = os.path.join(ROOT, "app.py")
CSV = (
    "question,type,option_a,option_b,option_c,option_d,correct_answer,time_limit\n"
    '"2+2 = ?","MCQ","3","4","5","6","B",30\n'
    '"The sky is green.","TF","","","","","False",30\n'
)


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok: {msg}")


def browser(session=None, host=False):
    at = AppTest.from_file(APP, default_timeout=30)
    if host:
        at.query_params["role"] = "host"
    for k, v in (session or {}).items():
        at.session_state[k] = v
    return at


def join(quiz_code, name, reg):
    at = browser().run()
    at.text_input[0].set_value(quiz_code)
    at.text_input[1].set_value(name)
    at.text_input[2].set_value(reg)
    at.text_input[3].set_value("")
    at.button[0].click().run()
    check(not at.exception, f"{name} joined without error")
    return at


def click_label(at, needle):
    for b in at.button:
        if needle.lower() in b.label.lower():
            b.click().run()
            return True
    return False


def main():
    db.init_db()
    rows, errs = quizlib.parse_questions_csv(CSV)
    check(not errs, f"CSV parsed clean ({errs})")
    quiz = db.create_quiz("Integration Quiz", rows, 30)
    code = quiz["code"]
    qid = quiz["id"]
    questions = db.get_questions(qid)

    # --- participants join (quiz still in lobby) -----------------------
    p1 = join(code, "Alice", "R1")
    p2 = join(code, "Bob", "R2")
    check(len(db.get_participants(qid)) == 2, "two participants registered")
    check(any("waiting for the quiz" in str(m.value).lower() for m in p1.info),
          "Alice sees the waiting room")

    p1_id = p1.session_state["participant_id"]
    p2_id = p2.session_state["participant_id"]

    # --- host starts the quiz ----------------------------------------
    host = browser({"host_authed": True, "host_quiz_id": qid}, host=True).run()
    check(not host.exception, "host console renders")
    check(click_label(host, "START QUIZ"), "host clicked START QUIZ")
    check(db.get_quiz(qid)["status"] == "running", "quiz is running")

    # --- Question 1 -------------------------------------------------
    check(click_label(host, "START QUESTION"), "host started question 1")
    q1 = questions[0]

    p1.run()
    check(not p1.exception and p1.radio, "Alice sees Q1 with answer options")
    p1.radio[0].set_value("B").run()          # correct
    check(click_label(p1, "Submit answer"), "Alice submitted Q1")
    r = db.get_response(p1_id, q1["id"])
    check(r and r["is_correct"] == 1, "Alice's Q1 answer stored correct")

    # Bob picks a wrong answer but never presses submit; host ends the question.
    p2.run()
    p2.radio[0].set_value("A").run()          # selected, not submitted
    check(click_label(host, "END QUESTION"), "host ended question 1")

    p2.run()  # fragment should auto-submit Bob's pending selection
    r2 = db.get_response(p2_id, q1["id"])
    check(r2 is not None, "Bob's Q1 auto-submitted on question end")
    check(r2["selected_answer"] == "A" and r2["is_correct"] == 0, "Bob's Q1 recorded wrong")

    # --- Question 2 -------------------------------------------------
    check(click_label(host, "NEXT QUESTION"), "host advanced to question 2")
    check(db.get_quiz(qid)["current_q_index"] == 1, "now on question 2")
    check(click_label(host, "START QUESTION"), "host started question 2")
    q2 = questions[1]

    p1.run()
    p1.radio[0].set_value("False").run()      # correct
    check(click_label(p1, "Submit answer"), "Alice submitted Q2")

    p2.run()
    p2.radio[0].set_value("True").run()       # wrong
    check(click_label(p2, "Submit answer"), "Bob submitted Q2")

    # --- finish ----------------------------------------------------
    check(click_label(host, "END QUESTION"), "host ended question 2")
    check(click_label(host, "FINISH QUIZ"), "host finished the quiz")
    check(db.get_quiz(qid)["status"] == "finished", "quiz finished")

    p1.run()
    p2.run()
    check(any("completed" in str(h.value).lower() for h in p1.header), "Alice sees result screen")
    check(any("2 / 2" in str(m.value) for m in p1.metric), "Alice scored 2/2")
    check(any("0 / 2" in str(m.value) for m in p2.metric), "Bob scored 0/2")

    lb = reports.leaderboard_df(qid)
    check(list(lb["Name"]) == ["Alice", "Bob"], "leaderboard ordered by score")
    summary = reports.summary_df(qid)
    check(int(summary.iloc[0]["Correct Answers"]) == 2, "summary: Alice 2 correct")
    check(int(summary.iloc[1]["Incorrect Answers"]) == 2, "summary: Bob 2 incorrect")
    detailed = reports.detailed_df(qid)
    check(len(detailed) == 4, "detailed report has 4 rows (2 participants x 2 questions)")

    print("\nIntegration test passed — full host+participant loop works.")


if __name__ == "__main__":
    main()
