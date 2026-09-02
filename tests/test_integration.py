"""Full live-quiz loop with the new host flow.

  * one button at a time for the host (START QUIZ -> NEXT QUESTION -> FINISH)
  * clicking it starts the next question immediately
  * it stays disabled until the current question is closed
    (timer reached OR every participant has answered)
  * participants see the leaderboard after each question closes

Driven with three independent AppTest "browsers" sharing one database.

    python tests/test_integration.py
"""
from __future__ import annotations

import os
import sys
import tempfile

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


def join(code, name, reg):
    at = browser().run()
    at.text_input[0].set_value(code)
    at.text_input[1].set_value(name)
    at.text_input[2].set_value(reg)
    at.text_input[3].set_value("")
    at.button[0].click().run()
    check(not at.exception, f"{name} joined without error")
    return at


def button(at, needle):
    for b in at.button:
        if needle.lower() in b.label.lower():
            return b
    return None


def click(at, needle, why):
    b = button(at, needle)
    check(b is not None, f"button '{needle}' present — {why}")
    b.click().run()
    check(not at.exception, why)


def main():
    db.init_db()
    rows, errs = quizlib.parse_questions_csv(CSV)
    check(not errs, f"CSV parsed clean ({errs})")
    quiz = db.create_quiz("Integration Quiz", rows, 30)
    code, qid = quiz["code"], quiz["id"]
    questions = db.get_questions(qid)

    # --- participants join ------------------------------------------
    p1 = join(code, "Alice", "R1")
    p2 = join(code, "Bob", "R2")
    p1_id = p1.session_state["participant_id"]
    p2_id = p2.session_state["participant_id"]
    check(db.participant_count(qid) == 2, "two participants registered")

    # --- host starts quiz -> Q1 active immediately ------------------
    host = browser({"host_authed": True, "host_quiz_id": qid}, host=True).run()
    click(host, "START QUIZ", "host started the quiz")
    fresh = db.get_quiz(qid)
    check(fresh["status"] == "running" and fresh["current_q_status"] == "active",
          "Q1 is active immediately after START QUIZ")
    q1, q2 = questions

    # --- Q1: only Alice answers -> question still OPEN --------------
    p1.run()
    check(p1.radio, "Alice sees Q1 options")
    p1.radio[0].set_value("B").run()               # correct
    click(p1, "Submit answer", "Alice submitted Q1")
    check(db.get_response(p1_id, q1["id"])["is_correct"] == 1, "Alice Q1 correct stored")

    host.run()
    check(button(host, "NEXT QUESTION") is not None, "host sees NEXT QUESTION button")
    check(button(host, "NEXT QUESTION").proto.disabled,
          "NEXT QUESTION disabled while Q1 still open (Bob hasn't answered)")
    check(any("unlocks when the timer" in str(i.value).lower() for i in host.info),
          "host told why the button is locked")

    # --- Bob answers -> 2/2 -> Q1 closes ---------------------------
    p2.run()
    p2.radio[0].set_value("A").run()               # wrong
    click(p2, "Submit answer", "Bob submitted Q1")

    check(quizlib.question_closed("active", db.get_quiz(qid)["current_q_started_at"],
                                  30, 2, db.answer_count(qid, q1["id"])),
          "Q1 is now closed (everyone answered)")

    # participants see the leaderboard now that Q1 is closed
    p1.run()
    check(any("leaderboard" in str(s.value).lower() for s in p1.subheader),
          "Alice sees the leaderboard after Q1 closes")
    check(any("your position" in str(i.value).lower() for i in p1.info),
          "Alice sees her position")

    host.run()
    check(not button(host, "NEXT QUESTION").proto.disabled,
          "NEXT QUESTION unlocked once Q1 closed")
    click(host, "NEXT QUESTION", "host advanced to Q2")
    fresh = db.get_quiz(qid)
    check(fresh["current_q_index"] == 1 and fresh["current_q_status"] == "active",
          "Q2 active immediately after NEXT QUESTION")

    # --- Q2: both answer -> closes -> FINISH -----------------------
    p1.run()
    p1.radio[0].set_value("False").run()           # correct
    click(p1, "Submit answer", "Alice submitted Q2")
    p2.run()
    p2.radio[0].set_value("True").run()            # wrong
    click(p2, "Submit answer", "Bob submitted Q2")

    host.run()
    check(button(host, "FINISH QUIZ") is not None and not button(host, "FINISH QUIZ").proto.disabled,
          "FINISH QUIZ enabled after last question closed")
    click(host, "FINISH QUIZ", "host finished the quiz")
    check(db.get_quiz(qid)["status"] == "finished", "quiz finished")

    # --- results & leaderboard -----------------------------------
    p1.run()
    p2.run()
    check(any("completed" in str(h.value).lower() for h in p1.header), "Alice sees result screen")
    check(any("2 / 2" in str(m.value) for m in p1.metric), "Alice scored 2/2")
    check(any("0 / 2" in str(m.value) for m in p2.metric), "Bob scored 0/2")

    lb = reports.leaderboard_df(qid)
    check(list(lb["Name"]) == ["Alice", "Bob"], "leaderboard ordered by score")
    check("Time (s)" in lb.columns, "leaderboard shows answering time")
    detailed = reports.detailed_df(qid)
    check(len(detailed) == 4, "detailed report: 2 participants x 2 questions")

    print("\nIntegration test passed — new host flow + participant leaderboard work.")


if __name__ == "__main__":
    main()
