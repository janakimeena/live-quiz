"""Simple Live Quiz Platform — a minimal Kahoot-style app.

Run locally:      streamlit run app.py
Participant URL:  <app-url>/
Faculty URL:      <app-url>/?role=host

All shared state lives in SQLite (utils/database.py); Streamlit session_state
only holds this browser's identity so a refresh recovers gracefully.
"""
from __future__ import annotations

import os
import time
from datetime import datetime

import pandas as pd
import streamlit as st

from utils import database as db
from utils import quiz as quizlib
from utils import reports

st.set_page_config(page_title="Live Quiz", page_icon="🧠", layout="centered")
db.init_db()

SAMPLE_CSV_PATH = os.path.join(os.path.dirname(__file__), "sample_questions.csv")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _setting(*names: str, default: str = "") -> str:
    """Look a value up in st.secrets first, then environment variables."""
    for name in names:
        try:
            if name in st.secrets:
                return str(st.secrets[name])
        except Exception:
            pass
        val = os.environ.get(name)
        if val:
            return val
    return default


def get_admin_password() -> str:
    return _setting("admin_password", "QUIZ_ADMIN_PASSWORD", default="admin123")


def get_public_url() -> str:
    return _setting("public_url", "QUIZ_PUBLIC_URL").rstrip("/")


def question_closed(quiz: dict, question: dict, n_participants: int, n_answered: int) -> bool:
    return quizlib.question_closed(
        quiz["current_q_status"], quiz["current_q_started_at"],
        question["time_limit"], n_participants, n_answered,
    )


def _advance_quiz(quiz: dict, idx: int, is_last: bool) -> None:
    if is_last:
        db.set_quiz_field(quiz["id"], status="finished")
    else:
        db.set_quiz_field(
            quiz["id"], current_q_index=idx + 1,
            current_q_status="active", current_q_started_at=time.time(),
        )
    st.rerun()


def fmt_date(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%d-%m-%Y %H:%M")
    except Exception:
        return iso


def qr_image(data: str):
    """Return PNG bytes for a QR code, or None if qrcode isn't available."""
    try:
        import io

        import qrcode

        buf = io.BytesIO()
        qrcode.make(data).save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        return None


# ===========================================================================
# PARTICIPANT
# ===========================================================================

def participant_app() -> None:
    st.title("🧠 Live Quiz")

    pid = st.session_state.get("participant_id")
    if pid:
        participant = db.get_participant(pid)
        if participant:
            _participant_routed(participant)
            return
        st.session_state.pop("participant_id", None)

    _registration_form()
    st.divider()
    st.caption("Faculty / host? Open the [host console](?role=host).")


def _registration_form() -> None:
    st.subheader("Join a quiz")
    with st.form("join"):
        code = st.text_input("Quiz Code *", max_chars=12, placeholder="e.g. AB3KP").strip().upper()
        name = st.text_input("Name *").strip()
        reg_no = st.text_input("Registration Number *").strip()
        email = st.text_input("Email (optional)").strip()
        submitted = st.form_submit_button("JOIN QUIZ", type="primary")

    if not submitted:
        return

    if not code or not name or not reg_no:
        st.error("Quiz code, name and registration number are all required.")
        return

    quiz = db.get_quiz_by_code(code)
    if not quiz:
        st.error("No quiz found for that code. Please check with your host.")
        return
    if quiz["status"] == "finished":
        st.error("This quiz has already finished.")
        return

    try:
        participant, note = db.register_participant(quiz["id"], name, reg_no, email)
    except ValueError as exc:
        st.error(str(exc))
        return

    st.session_state["participant_id"] = participant["id"]
    if note:
        st.info(note)
    st.rerun()


def _participant_routed(participant: dict) -> None:
    quiz = db.get_quiz(participant["quiz_id"])
    if not quiz:
        st.error("This quiz no longer exists.")
        if st.button("Start over"):
            st.session_state.pop("participant_id", None)
            st.rerun()
        return

    if st.session_state.get("_leave"):
        st.session_state.pop("participant_id", None)
        st.session_state.pop("_leave", None)
        st.rerun()

    if quiz["status"] == "lobby":
        _waiting_room(quiz, participant)
    elif quiz["status"] == "running":
        _question_screen(quiz["id"], participant)
    else:
        _result_screen(quiz, participant)


@st.fragment(run_every=2)
def _waiting_room(quiz: dict, participant: dict) -> None:
    fresh = db.get_quiz(quiz["id"])
    if fresh["status"] != "lobby":
        st.rerun(scope="app")

    st.success("You have successfully joined the quiz.")
    st.write(f"**Quiz:** {fresh['title']}")
    st.write(f"**Participant:** {participant['name']}")
    st.write(f"**Registration Number:** {participant['reg_no']}")
    st.info("Waiting for the quiz to start…")
    st.caption("Keep this page open. It will update automatically.")


def _participant_leaderboard(quiz_id: int, participant: dict, limit: int | None = 10) -> None:
    df = reports.leaderboard_df(quiz_id)
    if df.empty:
        return
    st.subheader("🏆 Leaderboard" + ("" if limit is None else " so far"))
    mine = df.index[df["Registration No."] == participant["reg_no"]].tolist()
    if mine:
        st.info(f"Your position: **#{int(df.loc[mine[0], 'Rank'])}** of {len(df)}")
    st.dataframe(df if limit is None else df.head(limit), hide_index=True)
    st.caption("Ranked by score, then by total answering time.")


@st.fragment(run_every=1)
def _question_screen(quiz_id: int, participant: dict) -> None:
    quiz = db.get_quiz(quiz_id)
    if quiz["status"] != "running":
        st.rerun(scope="app")

    questions = db.get_questions(quiz_id)
    idx = quiz["current_q_index"]
    if idx >= len(questions):
        st.info("Waiting for the host…")
        return

    q = questions[idx]
    st.subheader(f"Question {idx + 1} of {len(questions)}")

    if quiz["current_q_status"] != "active":
        st.info("Get ready — the quiz is starting.")
        return

    started_at = quiz["current_q_started_at"]
    n_participants = db.participant_count(quiz_id)
    n_answered = db.answer_count(quiz_id, q["id"])
    existing = db.get_response(participant["id"], q["id"])
    closed = question_closed(quiz, q, n_participants, n_answered)

    st.markdown(f"### {q['question']}")

    if closed:
        if existing is None:
            selected = st.session_state.get(f"ans_{q['id']}")
            db.submit_response(quiz_id, participant["id"], q, selected, q["time_limit"])
            st.rerun()
            return
        if existing["selected_answer"]:
            st.success(f"Your answer: **{existing['selected_answer']}**")
        else:
            st.warning("You did not answer this question.")
        st.caption("Question closed. Waiting for the host to continue…")
        if quiz["show_leaderboard"]:
            st.divider()
            _participant_leaderboard(quiz_id, participant)
        return

    remaining = q["time_limit"] - (time.time() - started_at)

    if existing is not None:
        st.success(f"Answer recorded: **{existing['selected_answer'] or '—'}**")
        st.caption(
            f"{n_answered} of {n_participants} answered  ·  "
            f"question closes in {int(max(0, remaining))}s (or when everyone has answered)"
        )
        return

    st.progress(max(0.0, min(1.0, remaining / q["time_limit"])))
    st.caption(
        f"⏱️ Time remaining: {int(max(0, remaining))} seconds  ·  "
        f"{n_answered}/{n_participants} answered"
    )

    if q["type"] == "TF":
        choice = st.radio("Your answer", ["True", "False"], index=None, key=f"ans_{q['id']}")
    else:
        letters = quizlib.option_letters(q)
        text_map = {L: v for L, v in q["options"]}
        choice = st.radio(
            "Your answer", letters, index=None,
            format_func=lambda L: f"{L}. {text_map[L]}", key=f"ans_{q['id']}",
        )

    if st.button("Submit answer", type="primary", disabled=choice is None):
        taken = max(0.0, time.time() - started_at) if started_at else 0.0
        db.submit_response(quiz_id, participant["id"], q, choice, taken)
        st.rerun()


def _result_screen(quiz: dict, participant: dict) -> None:
    resp = db.responses_by_question(participant["id"])
    total_q = db.question_count(quiz["id"])
    score = sum(1 for r in resp.values() if r["is_correct"])
    pct = round(100 * score / total_q) if total_q else 0

    st.header("✅ Quiz completed")
    st.write(f"**Participant:** {participant['name']}")
    st.write(f"**Registration Number:** {participant['reg_no']}")
    c1, c2 = st.columns(2)
    c1.metric("Score", f"{score} / {total_q}")
    c2.metric("Percentage", f"{pct}%")
    st.success("Thank you for participating!")

    if quiz["show_leaderboard"]:
        st.divider()
        _participant_leaderboard(quiz["id"], participant, limit=None)

    st.divider()
    if st.button("Leave / join another quiz"):
        st.session_state["_leave"] = True
        st.rerun()


# ===========================================================================
# HOST
# ===========================================================================

def host_app() -> None:
    st.title("🎓 Faculty / Host Console")

    if not st.session_state.get("host_authed"):
        with st.form("host_login"):
            pw = st.text_input("Host password", type="password")
            if st.form_submit_button("Log in", type="primary"):
                if pw == get_admin_password():
                    st.session_state["host_authed"] = True
                    st.rerun()
                else:
                    st.error("Incorrect password.")
        st.caption("Participants: open the [quiz join page](?role=).")
        return

    tab_quiz, tab_players, tab_control, tab_board, tab_reports, tab_history = st.tabs(
        ["Create / Load", "Participants", "Quiz Control", "Leaderboard", "Reports", "History"]
    )
    with tab_quiz:
        _host_create_load()
    with tab_players:
        _host_participants()
    with tab_control:
        _host_control()
    with tab_board:
        _host_leaderboard()
    with tab_reports:
        _host_reports()
    with tab_history:
        _host_history()


def _current_quiz() -> dict | None:
    qid = st.session_state.get("host_quiz_id")
    return db.get_quiz(qid) if qid else None


def _host_create_load() -> None:
    st.subheader("Create a new quiz")

    if os.path.exists(SAMPLE_CSV_PATH):
        with open(SAMPLE_CSV_PATH, "rb") as fh:
            st.download_button("Download sample_questions.csv", fh.read(),
                               file_name="sample_questions.csv", mime="text/csv")

    with st.form("create_quiz"):
        title = st.text_input("Quiz Title", placeholder="Generative AI Quiz — VAP 2026")
        default_tl = st.number_input("Default time per question (seconds)", 5, 300, 30, step=5)
        upload = st.file_uploader("Question CSV", type=["csv"])
        create = st.form_submit_button("Create Quiz", type="primary")

    if create:
        if not title.strip():
            st.error("Please enter a quiz title.")
        elif upload is None:
            st.error("Please upload a question CSV.")
        else:
            rows, errors = quizlib.parse_questions_csv(upload.getvalue(), int(default_tl))
            for e in errors:
                st.warning(e)
            if rows:
                quiz = db.create_quiz(title, rows, int(default_tl))
                st.session_state["host_quiz_id"] = quiz["id"]
                st.success(f"Created '{quiz['title']}' with {len(rows)} questions.")
                st.rerun()

    st.divider()
    st.subheader("Load an existing quiz")
    quizzes = db.list_quizzes()
    if quizzes:
        labels = {f"{q['title']}  ·  {q['code']}  ·  {fmt_date(q['created_at'])}": q["id"] for q in quizzes}
        pick = st.selectbox("Select quiz", list(labels), index=None, placeholder="Choose…")
        if pick and st.button("Load quiz"):
            st.session_state["host_quiz_id"] = labels[pick]
            st.rerun()
    else:
        st.caption("No quizzes yet.")

    quiz = _current_quiz()
    if quiz:
        st.divider()
        st.subheader(f"Active quiz: {quiz['title']}")
        c1, c2, c3 = st.columns(3)
        c1.metric("Quiz code", quiz["code"])
        c2.metric("Questions", db.question_count(quiz["id"]))
        c3.metric("Status", quiz["status"])
        public_url = get_public_url()
        if public_url:
            st.write(f"**Share this link:** {public_url}/")
            img = qr_image(public_url + "/")
            if img is not None:
                st.image(img, width=200, caption=f"Scan to join · code {quiz['code']}")
        else:
            st.info(
                "Share this app's URL with participants, along with the quiz code above. "
                "Add `public_url = \"https://your-app.streamlit.app\"` to the app secrets "
                "to show a shareable link and QR code here."
            )
        _host_manage_questions(quiz)


def _host_manage_questions(quiz: dict) -> None:
    with st.expander("View / add / remove questions"):
        questions = db.get_questions(quiz["id"])
        if questions:
            st.dataframe(
                pd.DataFrame([
                    {"#": q["q_order"], "Type": q["type"], "Question": q["question"],
                     "Correct": q["correct_answer"], "Time": q["time_limit"]}
                    for q in questions
                ]),
                hide_index=True,
            )
            rm = st.selectbox("Remove a question", [q["id"] for q in questions],
                              format_func=lambda i: next(f"#{q['q_order']} {q['question'][:40]}" for q in questions if q["id"] == i),
                              index=None)
            if rm and st.button("Delete question"):
                db.delete_question(rm)
                st.rerun()

        st.markdown("**Add a question manually**")
        with st.form("add_q"):
            qtype = st.selectbox("Type", ["MCQ", "TF"])
            text = st.text_input("Question text")
            a = st.text_input("Option A")
            b = st.text_input("Option B")
            c = st.text_input("Option C")
            d = st.text_input("Option D")
            correct = st.text_input("Correct answer (A/B/C/D or True/False)")
            tl = st.number_input("Time limit (s)", 5, 300, quiz["default_time_limit"], step=5)
            if st.form_submit_button("Add question"):
                if qtype == "TF":
                    row = {"question": text, "type": "TF", "correct_answer": correct,
                           "option_a": "True", "option_b": "False", "option_c": "", "option_d": "",
                           "time_limit": int(tl)}
                    ok = quizlib.normalize_tf(correct) is not None and text.strip()
                    if ok:
                        row["correct_answer"] = quizlib.normalize_tf(correct)
                else:
                    row = {"question": text, "type": "MCQ", "correct_answer": correct.strip().upper(),
                           "option_a": a, "option_b": b, "option_c": c, "option_d": d,
                           "time_limit": int(tl)}
                    filled = [L for L, v in zip("ABCD", [a, b, c, d]) if v.strip()]
                    ok = bool(text.strip()) and correct.strip().upper() in filled
                if not ok:
                    st.error("Please fill the question and a valid correct answer.")
                else:
                    db.add_questions(quiz["id"], [row], quiz["default_time_limit"])
                    st.rerun()


@st.fragment(run_every=3)
def _host_participants() -> None:
    quiz = _current_quiz()
    if not quiz:
        st.info("Create or load a quiz first.")
        return
    players = db.get_participants(quiz["id"])
    st.subheader(f"Participants Joined: {len(players)}")
    if players:
        st.dataframe(
            pd.DataFrame([
                {"#": i + 1, "Name": p["name"], "Registration No.": p["reg_no"], "Email": p["email"]}
                for i, p in enumerate(players)
            ]),
            hide_index=True,
        )
    st.caption("This list refreshes automatically.")


@st.fragment(run_every=2)
def _host_control() -> None:
    quiz = _current_quiz()
    if not quiz:
        st.info("Create or load a quiz first.")
        return

    questions = db.get_questions(quiz["id"])
    n = len(questions)
    st.write(f"**{quiz['title']}**  ·  code **{quiz['code']}**  ·  {n} questions  ·  "
             f"status: **{quiz['status']}**")

    if quiz["status"] == "lobby":
        st.caption("Starting the quiz immediately begins Question 1 and its timer.")
        if st.button("▶️ START QUIZ", type="primary", disabled=n == 0):
            db.set_quiz_field(quiz["id"], status="running", current_q_index=0,
                              current_q_status="active", current_q_started_at=time.time())
            st.rerun()
        _host_leaderboard_toggle(quiz)
        return

    if quiz["status"] == "finished":
        st.success("Quiz finished. See the Leaderboard and Reports tabs.")
        if st.button("Re-open quiz (back to lobby)"):
            db.set_quiz_field(quiz["id"], status="lobby", current_q_index=0,
                              current_q_status="pending", current_q_started_at=None)
            st.rerun()
        _host_leaderboard_toggle(quiz)
        return

    idx = quiz["current_q_index"]
    q = questions[idx]
    is_last = idx + 1 >= n
    n_participants = db.participant_count(quiz["id"])
    n_answered = db.answer_count(quiz["id"], q["id"])
    closed = question_closed(quiz, q, n_participants, n_answered)
    elapsed = time.time() - quiz["current_q_started_at"] if quiz["current_q_started_at"] else 0

    st.divider()
    st.markdown(f"### Question {idx + 1} / {n}")
    st.write(q["question"])
    st.caption(f"Type: {q['type']}  ·  Correct: **{q['correct_answer']}**  ·  Limit: {q['time_limit']}s")

    m1, m2, m3 = st.columns(3)
    m1.metric("Answered", f"{n_answered} / {n_participants}")
    m2.metric("Elapsed", f"{int(min(elapsed, q['time_limit']))}s / {q['time_limit']}s")
    m3.metric("Question", "Closed ✓" if closed else "Open…")

    if not closed:
        st.info("**Next question** unlocks when the timer runs out or every "
                "participant has answered.")

    label = "🏁 FINISH QUIZ" if is_last else "⏭️ NEXT QUESTION"
    if st.button(label, type="primary", disabled=not closed):
        _advance_quiz(quiz, idx, is_last)

    with st.expander("Emergency override"):
        st.caption("Only use this if a participant's device is stuck and you cannot "
                   "wait for the timer.")
        if st.button(f"Force {'finish' if is_last else 'next question'} now"):
            _advance_quiz(quiz, idx, is_last)

    _host_leaderboard_toggle(quiz)


def _host_leaderboard_toggle(quiz: dict) -> None:
    st.divider()
    show = st.toggle(
        "Show leaderboard to participants (after each question and on their result screen)",
        value=bool(quiz["show_leaderboard"]),
    )
    if show != bool(quiz["show_leaderboard"]):
        db.set_quiz_field(quiz["id"], show_leaderboard=1 if show else 0)
        st.rerun()


def _host_leaderboard() -> None:
    quiz = _current_quiz()
    if not quiz:
        st.info("Create or load a quiz first.")
        return
    st.subheader(f"🏆 Leaderboard — {quiz['title']}")
    if st.button("🔄 Refresh leaderboard"):
        st.rerun()
    df = reports.leaderboard_df(quiz["id"])
    if df.empty:
        st.caption("No participants yet.")
    else:
        st.dataframe(df, hide_index=True)


def _host_reports() -> None:
    quiz = _current_quiz()
    if not quiz:
        st.info("Create or load a quiz first.")
        return
    st.subheader(f"Download results — {quiz['title']}")
    summary = reports.summary_df(quiz["id"])
    detailed = reports.detailed_df(quiz["id"])

    st.download_button(
        "⬇️ DOWNLOAD REPORT (Quiz Results.csv)",
        summary.to_csv(index=False).encode("utf-8"),
        file_name="Quiz Results.csv", mime="text/csv", type="primary",
    )
    st.download_button(
        "⬇️ Detailed Quiz Responses.csv",
        detailed.to_csv(index=False).encode("utf-8"),
        file_name="Detailed Quiz Responses.csv", mime="text/csv",
    )
    st.divider()
    st.write("**Preview — summary**")
    st.dataframe(summary, hide_index=True)
    st.write("**Preview — detailed responses**")
    st.dataframe(detailed, hide_index=True)


def _host_history() -> None:
    st.subheader("Previous quizzes")
    for h in reports.quiz_history():
        st.markdown(
            f"**{h['title']}** — {fmt_date(h['created_at'])}  ·  code `{h['code']}`  ·  _{h['status']}_\n\n"
            f"Participants: {h['participants']}  ·  Average score: {h['average_score']} / {h['total_questions']}"
        )
        st.divider()


# ===========================================================================
# Router
# ===========================================================================

def main() -> None:
    role = st.query_params.get("role", "")
    if role == "host":
        host_app()
    else:
        participant_app()


main()
