"""Timer correctness.

Checks the countdown itself, and then checks it against the live app: the
number a participant sees, the number the host sees, and the instant the
question actually closes must all be the same event.

    python tests/test_timer.py
"""
from __future__ import annotations

import os
import re
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="quiztimer_")
os.environ["QUIZ_DB_PATH"] = os.path.join(_TMP, "quiz.db")
os.environ["QUIZ_ADMIN_PASSWORD"] = "pw"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from streamlit.testing.v1 import AppTest  # noqa: E402

from utils import database as db  # noqa: E402
from utils import quiz as quizlib  # noqa: E402

APP = os.path.join(ROOT, "app.py")
CSV = 'question,type,correct_answer,time_limit\n"The sky is blue.",TF,True,30\n'


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok: {msg}")


# ---------------------------------------------------------------------------
# 1. The countdown function itself
# ---------------------------------------------------------------------------

def test_countdown_maths():
    rs = quizlib.remaining_seconds
    T0 = 1000.0

    check(rs(T0, 30, now=T0) == 30, "shows the full 30 s the instant it starts")
    check(rs(T0, 30, now=T0 + 0.01) == 30, "still 30 a hair after the start")
    check(rs(T0, 30, now=T0 + 0.99) == 30, "still 30 during the first second")
    check(rs(T0, 30, now=T0 + 1.0) == 29, "ticks to 29 at exactly 1 s")
    check(rs(T0, 30, now=T0 + 29.5) == 1, "shows 1 during the final second")
    check(rs(T0, 30, now=T0 + 30.0) == 0, "hits 0 exactly at the limit")
    check(rs(T0, 30, now=T0 + 45.0) == 0, "never goes negative")
    check(rs(None, 30) == 30, "un-started question shows the full limit")

    # every value from 30 down to 0 appears exactly once, no skips, no repeats
    seen = [rs(T0, 30, now=T0 + t / 100) for t in range(0, 3001)]
    check(sorted(set(seen), reverse=True) == list(range(30, -1, -1)),
          "counts down through every second 30..0 with no gaps")
    check(all(a >= b for a, b in zip(seen, seen[1:])), "never counts back up")


def test_countdown_agrees_with_closing():
    """The moment the display reads 0 must be the moment the question closes."""
    T0 = 1000.0
    for hundredths in range(0, 3100, 7):
        now = T0 + hundredths / 100
        shows_zero = quizlib.remaining_seconds(T0, 30, now=now) == 0
        is_closed = quizlib.question_closed("active", T0, 30, 5, 0, now=now)
        check_pair = shows_zero == is_closed
        if not check_pair:
            raise AssertionError(
                f"at t+{now - T0:.2f}s display-zero={shows_zero} but closed={is_closed}"
            )
    print("  ok: 'timer reads 0' and 'question is closed' are the same instant")


# ---------------------------------------------------------------------------
# 2. The live app: host clock == participant clock
# ---------------------------------------------------------------------------

def _int_in(text_list, pattern):
    for t in text_list:
        m = re.search(pattern, str(t))
        if m:
            return int(m.group(1))
    return None


def test_host_and_participant_clocks_match():
    db.init_db()
    rows, errs = quizlib.parse_questions_csv(CSV)
    assert not errs, errs
    quiz = db.create_quiz("Timer Quiz", rows, 30)
    qid = quiz["id"]
    p, _ = db.register_participant(qid, "Alice", "R1")
    q = db.get_questions(qid)[0]

    host = AppTest.from_file(APP, default_timeout=30)
    host.query_params["role"] = "host"
    host.session_state["host_authed"] = True
    host.session_state["host_quiz_id"] = qid
    host.run()
    for b in host.button:
        if "START QUIZ" in b.label:
            b.click().run()
            break

    part = AppTest.from_file(APP, default_timeout=30)
    part.session_state["participant_id"] = p["id"]

    # Sample the two screens at several points across the question's life by
    # rewinding the stored start time instead of sleeping.
    started = db.get_quiz(qid)["current_q_started_at"]
    import time as _t

    for age in (0.0, 1.5, 10.0, 29.5):
        db.set_quiz_field(qid, current_q_started_at=_t.time() - age)
        part.run()
        host.run()

        p_left = _int_in([c.value for c in part.caption], r"Time remaining: (\d+) seconds")
        h_left = _int_in([m.value for m in host.metric], r"^(\d+)s / 30s$")
        expected = quizlib.remaining_seconds(_t.time() - age, 30)

        check(p_left is not None, f"participant shows a countdown at t+{age}s")
        check(h_left is not None, f"host shows a countdown at t+{age}s")
        check(abs(p_left - expected) <= 1,
              f"participant clock {p_left}s matches expected {expected}s at t+{age}s")
        check(abs(p_left - h_left) <= 1,
              f"host {h_left}s and participant {p_left}s agree at t+{age}s")

    # Past the limit: closed, auto-submitted as unanswered, no negative clock.
    db.set_quiz_field(qid, current_q_started_at=_t.time() - 31)
    part.run()
    check(not part.exception, "participant screen renders after the timer expires")
    resp = db.get_response(p["id"], q["id"])
    check(resp is not None, "unanswered auto-submit fired when the timer expired")
    check(resp["selected_answer"] is None, "recorded as unanswered")
    check(resp["time_taken"] <= 30, f"stored time {resp['time_taken']} never exceeds the limit")

    host.run()
    check(any("Closed" in str(m.value) for m in host.metric), "host sees the question closed")
    check(started is not None, "question had a start timestamp")


def test_stored_time_is_clamped():
    """A late-landing click must not record more than the time limit."""
    db.init_db()
    rows, _ = quizlib.parse_questions_csv(CSV)
    quiz = db.create_quiz("Clamp Quiz", rows, 30)
    q = db.get_questions(quiz["id"])[0]
    p, _ = db.register_participant(quiz["id"], "Bob", "R9")

    import time as _t
    started = _t.time() - 40  # question started 40 s ago, limit is 30
    taken = min(float(q["time_limit"]), quizlib.elapsed_since(started))
    db.submit_response(quiz["id"], p["id"], q, "True", taken)
    stored = db.get_response(p["id"], q["id"])["time_taken"]
    check(stored == 30.0, f"answering time clamped to the 30 s limit (got {stored})")


if __name__ == "__main__":
    for fn in (test_countdown_maths, test_countdown_agrees_with_closing,
               test_host_and_participant_clocks_match, test_stored_time_is_clamped):
        print(f"\n{fn.__name__}")
        fn()
    print("\nTimer tests passed.")
