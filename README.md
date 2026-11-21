# Smart Exam Tutor

An AI exam-preparation coach. Students create an account, add their subjects
(syllabus PDF + exam date + optional question-paper pattern or photo), and the
app builds a study plan, teaches topics step by step, quizzes them and tracks
progress. Everything is saved to the student's account.

## Where the data is saved

- **On the live site (Streamlit Cloud):** files are erased on every restart, so
  the app needs an online PostgreSQL database. Create a free one (for example
  at neon.com), copy its connection string and put it in the app's Secrets as
  `DATABASE_URL` (see `secrets_example.toml`). Without it, the app shows a
  warning because accounts would be lost.
- **On your own computer:** with no `DATABASE_URL`, the app saves to
  `smart_exam_tutor.db` next to `app.py`. Back that file up and never share it.

## Run on your computer (Windows)

    D:\python\python.exe -m pip install -r requirements.txt
    setx GEMINI_API_KEY "PASTE_YOUR_KEY_HERE"
    D:\python\python.exe -m streamlit run .\app.py

## Notes

- Passwords are stored as salted PBKDF2 hashes. There is no password reset.
- Never put keys or the database address in the code or on GitHub.
- All visitors share the Gemini key set in the app's Secrets, so its free quota
  is shared too.
