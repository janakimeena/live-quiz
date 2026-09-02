"""Render-path smoke tests using Streamlit's AppTest.

Catches exceptions in the participant and host UI code without a browser.

    python tests/test_apptest.py
"""
from __future__ import annotations

import os
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="quizapptest_")
os.environ["QUIZ_DB_PATH"] = os.path.join(_TMP, "quiz.db")
os.environ["QUIZ_ADMIN_PASSWORD"] = "test-pw"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from streamlit.testing.v1 import AppTest  # noqa: E402

from utils import database as db  # noqa: E402
from utils import quiz as quizlib  # noqa: E402

APP = os.path.join(ROOT, "app.py")


def _no_exception(at, label):
    assert not at.exception, f"{label}: {at.exception}"
    print(f"  ok: {label}")


def seed_quiz():
    db.init_db()
    rows, errs = quizlib.parse_questions_csv(os.path.join(ROOT, "sample_questions.csv"))
    assert not errs, errs
    return db.create_quiz("AppTest Quiz", rows, 30)


def test_participant_registration():
    quiz = seed_quiz()
    at = AppTest.from_file(APP, default_timeout=15).run()
    _no_exception(at, "participant welcome")

    at.text_input(key="join-code" if False else None)  # noqa - placeholder
    # fill the join form (text inputs are positional inside the form)
    at.text_input[0].set_value(quiz["code"])
    at.text_input[1].set_value("Asha R")
    at.text_input[2].set_value("23BCE9001")
    at.text_input[3].set_value("asha@example.com")
    at.button[0].click().run()
    _no_exception(at, "participant joined -> waiting room")
    assert db.get_participants(quiz["id"]), "participant not persisted"

    # duplicate different name -> error, no crash
    at2 = AppTest.from_file(APP, default_timeout=15).run()
    at2.text_input[0].set_value(quiz["code"])
    at2.text_input[1].set_value("Someone Else")
    at2.text_input[2].set_value("23BCE9001")
    at2.button[0].click().run()
    _no_exception(at2, "duplicate registration handled")
    assert any("already joined" in str(e.value).lower() for e in at2.error)


def test_participant_question_and_result():
    quiz = seed_quiz()
    p, _ = db.register_participant(quiz["id"], "Bob", "23BCE7001")
    questions = db.get_questions(quiz["id"])

    db.set_quiz_field(quiz["id"], status="running", current_q_index=0,
                      current_q_status="active", current_q_started_at=__import__("time").time())
    at = AppTest.from_file(APP, default_timeout=15)
    at.session_state["participant_id"] = p["id"]
    at.run()
    _no_exception(at, "active question renders")
    assert at.radio, "answer widget missing"

    at.radio[0].set_value(questions[0]["correct_answer"]).run()
    at.button[0].click().run()
    _no_exception(at, "answer submitted")
    r = db.get_response(p["id"], questions[0]["id"])
    assert r and r["is_correct"] == 1

    db.set_quiz_field(quiz["id"], status="finished")
    at.run()
    _no_exception(at, "result screen renders")
    assert any("completed" in str(h.value).lower() for h in at.header)


def test_host_console():
    quiz = seed_quiz()
    db.register_participant(quiz["id"], "Cara", "23BCE6001")

    at = AppTest.from_file(APP, default_timeout=20)
    at.query_params["role"] = "host"
    at.run()
    _no_exception(at, "host login screen")

    at.text_input[0].set_value("wrong").run()
    at.button[0].click().run()
    assert at.error, "wrong password should error"

    at.text_input[0].set_value("test-pw").run()
    at.button[0].click().run()
    _no_exception(at, "host authed -> tabs")

    # load the quiz via session_state and re-run the control tab
    at.session_state["host_quiz_id"] = quiz["id"]
    at.run()
    _no_exception(at, "host with active quiz")

    # start the quiz through the control buttons
    started = False
    for b in at.button:
        if "START QUIZ" in b.label:
            b.click().run()
            started = True
            break
    if started:
        _no_exception(at, "host START QUIZ")
        assert db.get_quiz(quiz["id"])["status"] == "running"


if __name__ == "__main__":
    for fn in [test_participant_registration, test_participant_question_and_result, test_host_console]:
        print(f"\n{fn.__name__}")
        fn()
    print("\nAll AppTest smoke tests passed.")
