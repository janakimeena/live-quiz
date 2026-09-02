# Simple Live Quiz Platform

A minimal, Kahoot-style live quiz for a classroom event (~20 participants).
Built with **Streamlit + SQLite + pandas** — one process, one file database,
no external services.

* Multiple-choice and True/False questions
* Participants join with a **quiz code** + name + registration number
* Host-controlled flow: one button at a time — **START QUIZ → NEXT QUESTION → … → FINISH QUIZ**
* Clicking the button starts the next question (and its timer) immediately; it stays
  locked until the current question closes — i.e. the time limit is reached **or**
  every participant has answered
* Per-question timer with automatic submission when it expires
* Participants see a live **leaderboard after every question** (score, then total
  answering time as the tie-breaker); host can turn this off
* Faculty leaderboard and **CSV report download** (Excel-compatible)
* Everything is saved to SQLite immediately, so a browser refresh recovers

---

## 1. Run locally

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# optional but recommended — set a host password (default is "admin123")
export QUIZ_ADMIN_PASSWORD="your-password"

streamlit run app.py
```

Open the app:

| Who         | URL                                   |
| ----------- | ------------------------------------- |
| Participant | `http://localhost:8501/`             |
| Faculty     | `http://localhost:8501/?role=host`   |

---

## 2. Running the event

### Faculty

1. Go to `/?role=host`, log in with the host password.
2. **Create / Load** tab → enter a quiz title, set the default time per question,
   upload the question CSV → **Create Quiz**.
3. Note the **quiz code** shown (e.g. `AB3KP`). Share the app URL + code with
   students (WhatsApp / email / projector / QR code).
4. **Participants** tab → watch students join (auto-refreshes).
5. **Quiz Control** tab → **START QUIZ** (this immediately shows Question 1 and
   starts its timer). Read the question aloud. The **NEXT QUESTION** button is
   locked until the question closes — when the timer runs out *or* everyone has
   answered (the panel shows a live "Answered X / Y" counter). Then click
   **NEXT QUESTION** for each following question, and **FINISH QUIZ** on the last.
   An **Emergency override** expander lets you force-advance if a device is stuck.
6. **Leaderboard** tab → live ranking (score, then answering time). The toggle
   in Quiz Control controls whether participants also see the leaderboard after
   each question and on their result screen (on by default).
7. **Reports** tab → **DOWNLOAD REPORT** (`Quiz Results.csv`) and
   `Detailed Quiz Responses.csv`.

### Participant

1. Open the app URL, enter the **quiz code**, name, registration number
   (email optional) → **JOIN QUIZ**.
2. Wait on the "Waiting for the quiz to start…" screen (auto-updates).
3. Answer each question before the timer runs out and press **Submit answer**.
   If the timer expires, the current selection is submitted automatically
   (or recorded as unanswered).
4. After each question closes you see the leaderboard so far and your position;
   the next question appears automatically when the host advances.
5. After the quiz finishes, see your score, percentage and final ranking.

If a participant's page refreshes or the tab closes, they simply re-enter the
same code + name + registration number to resume — their answers are safe in
the database.

---

## 3. Question CSV format

Header row (exact column names, order doesn't matter):

```csv
question,type,option_a,option_b,option_c,option_d,correct_answer,time_limit
"Which technique combines retrieval with text generation?","MCQ","CNN","RAG","GAN","PCA","B",30
"RAG stands for Retrieval-Augmented Generation.","TF","","","","","True",20
```

* `type` — `MCQ` or `TF` (also accepts `multiple choice`, `true/false`, …)
* `correct_answer`
  * MCQ: the option letter `A`–`D` (or the exact option text)
  * TF: `True` / `False` (also `T`/`F`, `yes`/`no`, `1`/`0`)
* `option_*` columns are ignored for `TF` rows
* `time_limit` (seconds) is **optional** — omit the column to use the quiz default

A ready-made `sample_questions.csv` is included. Questions can also be added or
removed by hand from the host **Create / Load** tab.

---

## 4. Deploy to Streamlit Community Cloud (free)

1. Push this folder to a **GitHub** repository.
2. Go to <https://share.streamlit.io>, sign in with GitHub, **New app**.
3. Pick the repo/branch, set **Main file path** to `app.py`, deploy.
4. Open **⋮ → Settings → Secrets** and add:

   ```toml
   admin_password = "your-strong-password"
   ```

5. After the app is live, copy its URL and add it to the same Secrets box so the
   host console shows a share link + QR code:

   ```toml
   admin_password = "your-strong-password"
   public_url     = "https://your-app.streamlit.app"
   ```

6. Share `https://your-app.streamlit.app/` (participants) and
   `https://your-app.streamlit.app/?role=host` (faculty).

> **Storage note:** Community Cloud uses ephemeral disk. The SQLite database
> (`data/quiz.db`) persists while the app is running but is wiped if the app
> reboots or is redeployed. For a single event this is fine — just
> **download the CSV report right after the quiz**. For guaranteed persistence,
> run it on a small always-on VM instead (`pip install -r requirements.txt &&
> streamlit run app.py`).

Any host that runs a single Python process works: Render, Railway, a college
server, or a laptop on the classroom Wi-Fi.

---

## 5. Tests

```bash
python tests/simulate.py         # quiz lifecycle + scoring + reports, no browser
python tests/test_apptest.py     # renders every screen via Streamlit AppTest
python tests/test_integration.py # full host + 2-participant live loop
```

`simulate.py` covers CSV parsing, 10 participants, duplicate registration
numbers, unanswered questions, the question-closed rules, answer idempotency on
refresh, scoring, leaderboard ordering, and both CSV reports.
`test_integration.py` drives the real host flow: START QUIZ, the NEXT-QUESTION
button staying locked until the question closes, the participant leaderboard
between questions, and FINISH QUIZ.

---

## 6. Project structure

```
Interactive quiz/
├── app.py                 # all Streamlit UI (participant + host), the router
├── requirements.txt
├── sample_questions.csv
├── README.md
├── data/
│   └── quiz.db            # created on first run (git-ignored)
├── utils/
│   ├── database.py        # SQLite: quizzes, questions, participants, responses
│   ├── quiz.py            # CSV parsing, normalisation, scoring rules
│   └── reports.py         # leaderboard + summary + detailed report (pandas)
├── tests/
│   ├── simulate.py
│   ├── test_apptest.py
│   └── test_integration.py
└── .streamlit/
    ├── config.toml
    └── secrets.toml.example
```

## 7. Scoring rules

* Correct answer: **+1**
* Incorrect answer: **0**
* Unanswered: **0**
* No negative marking.
* Leaderboard sorts by score (desc), then total answering time (asc) as a
  tie-breaker.
