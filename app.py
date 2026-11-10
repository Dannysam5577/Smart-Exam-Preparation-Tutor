import html
import json
import os
import re
import time
from datetime import date, datetime, timedelta

import pandas as pd
import streamlit as st
from google import genai
from google.genai import types

# PDF reading is optional at import time so the app still opens if pypdf is missing
try:
    from pypdf import PdfReader
    PDF_AVAILABLE = True
except ImportError:
    PDF_AVAILABLE = False

MODEL_NAME = "gemini-3.6-flash"

st.set_page_config(
    page_title="Smart Exam Tutor",
    page_icon="📚",
    layout="wide"
)

# =====================================================================
# SESSION STATE
# =====================================================================

_defaults = {
    "student_name": "",
    "subjects": [],            # list of subject dictionaries
    "form_id": 0,              # changes after each save so the form resets
    "flash": None,             # one-time success message
    "parse_cache": {},         # extracted PDF text/rows, keyed by file signature
    "completed": {},           # subject name -> list of completed topic names
    "study_plans": {},         # subject name -> timetable rows
    "tutor_chats": {},         # subject name -> chat messages
    "ai_questions": {},        # "subject||topic" -> AI generated questions
    "practice_results": [],
    "weak_topics": [],         # "Subject › Topic"
    "practice_attempt_id": 0,
    "practice_feedback": None,
    "coach": {},               # subject name -> Study Coach session state
}

for _key, _value in _defaults.items():
    if _key not in st.session_state:
        st.session_state[_key] = _value

# =====================================================================
# CONSTANTS
# =====================================================================

DIFFICULTY = ["Easy", "Medium", "Hard"]
PREPARATION = ["Not Started", "Partially Prepared", "Well Prepared"]

DIFFICULTY_MULTIPLIER = {"Easy": 1.0, "Medium": 1.2, "Hard": 1.4}
PREPARATION_MULTIPLIER = {
    "Not Started": 1.5,
    "Partially Prepared": 1.2,
    "Well Prepared": 1.0
}

TOPIC_COLUMNS = ["Unit", "Topic", "Weightage (%)", "Difficulty", "Preparation"]
PATTERN_COLUMNS = ["Section", "Questions", "Marks each"]

# =====================================================================
# PREDEFINED PYTHON LESSONS AND QUESTIONS (used only for Python subjects)
# =====================================================================

practice_questions = {
    "variables": [
        {
            "question": "Which symbol is used to assign a value to a variable?",
            "options": ["==", "=", "!=", "+"],
            "answer": "=",
            "explanation": "The = operator assigns a value to a variable."
        },
        {
            "question": "Which is a valid Python variable name?",
            "options": ["2name", "my-name", "my_name", "class"],
            "answer": "my_name",
            "explanation": "Variable names can contain letters, numbers and underscores, but cannot start with a number or use reserved keywords."
        },
        {
            "question": "What is the output of x = 10; print(x)?",
            "options": ["x", "10", "None", "Error"],
            "answer": "10",
            "explanation": "The variable x stores 10, so print(x) displays 10."
        }
    ],
    "loops": [
        {
            "question": "Which loop is commonly used when the number of repetitions is known?",
            "options": ["if loop", "for loop", "while loop", "switch loop"],
            "answer": "for loop",
            "explanation": "A for loop is useful for iterating over a sequence or a known range."
        },
        {
            "question": "How many times does range(5) repeat a loop?",
            "options": ["4", "5", "6", "10"],
            "answer": "5",
            "explanation": "range(5) generates 0, 1, 2, 3 and 4, which are five values."
        },
        {
            "question": "Which keyword immediately terminates a loop?",
            "options": ["skip", "stop", "break", "continue"],
            "answer": "break",
            "explanation": "The break statement immediately exits the nearest enclosing loop."
        }
    ],
    "functions": [
        {
            "question": "Which keyword is used to define a function in Python?",
            "options": ["function", "define", "def", "fun"],
            "answer": "def",
            "explanation": "Python uses the def keyword to define a function."
        },
        {
            "question": "Which keyword sends a value back from a function?",
            "options": ["print", "return", "break", "input"],
            "answer": "return",
            "explanation": "The return statement sends a result back to the code that called the function."
        },
        {
            "question": "What is a function?",
            "options": [
                "A reusable block of code",
                "A type of variable",
                "A Python error",
                "A loop condition"
            ],
            "answer": "A reusable block of code",
            "explanation": "Functions organize code into reusable blocks that perform specific tasks."
        }
    ],
    "conditional statements": [
        {
            "question": "Which keyword checks a condition in Python?",
            "options": ["for", "if", "def", "print"],
            "answer": "if",
            "explanation": "The if keyword executes a block of code when its condition is True."
        },
        {
            "question": "Which keyword provides an alternative condition?",
            "options": ["repeat", "loop", "elif", "return"],
            "answer": "elif",
            "explanation": "elif means else if and checks another condition when previous conditions are False."
        },
        {
            "question": "What is the output of print(5 > 3)?",
            "options": ["5", "3", "True", "False"],
            "answer": "True",
            "explanation": "Five is greater than three, so the comparison evaluates to True."
        }
    ],
    "lists": [
        {
            "question": "Which brackets are used to create a Python list?",
            "options": ["()", "{}", "[]", "<>"],
            "answer": "[]",
            "explanation": "Square brackets are used to create a list in Python."
        },
        {
            "question": "What is the index of the first item in a Python list?",
            "options": ["1", "-1", "0", "2"],
            "answer": "0",
            "explanation": "Python list indexing starts at zero."
        },
        {
            "question": "Which method adds an item to the end of a list?",
            "options": ["add()", "append()", "insert_end()", "push()"],
            "answer": "append()",
            "explanation": "The append() method adds an item to the end of a list."
        }
    ]
}

lessons = {
    "variables": {
        "title": "Variables in Python",
        "definition": "A variable is a named location used to store data in a program.",
        "explanation": (
            "Think of a variable as a labelled box. "
            "You put information inside the box and use its name "
            "whenever you need that information."
        ),
        "example": "age = 18\nname = 'Danny'\nprint(age)\nprint(name)",
        "output": "18\nDanny",
        "real_life": (
            "A student's marks can be stored in a variable called marks. "
            "For example, marks = 85."
        ),
        "exam_points": [
            "Variables store data values.",
            "Python creates a variable when you assign a value to it.",
            "Python does not require you to declare a variable's data type separately."
        ],
        "revision": "Variable = Name + Stored Value"
    },
    "loops": {
        "title": "Loops in Python",
        "definition": (
            "A loop is a programming structure used to execute "
            "a block of code repeatedly."
        ),
        "explanation": (
            "Imagine writing 'Hello' ten times. Instead of typing "
            "the same instruction ten times, you can use a loop "
            "to repeat it automatically."
        ),
        "example": "for i in range(1, 6):\n    print(i)",
        "output": "1\n2\n3\n4\n5",
        "real_life": (
            "A loop can be used to display the names of all students "
            "in a class one by one."
        ),
        "exam_points": [
            "Python supports for loops and while loops.",
            "A for loop is commonly used to iterate over a sequence.",
            "A while loop repeats while its condition remains true."
        ],
        "revision": "Loop = Repeating a block of code"
    },
    "functions": {
        "title": "Functions in Python",
        "definition": (
            "A function is a reusable block of code designed "
            "to perform a particular task."
        ),
        "explanation": (
            "A function is like a machine. You give it input, "
            "it performs a task, and it can return a result."
        ),
        "example": (
            "def add(a, b):\n"
            "    return a + b\n\n"
            "result = add(5, 3)\n"
            "print(result)"
        ),
        "output": "8",
        "real_life": (
            "A calculator's addition function takes two numbers "
            "and produces their sum."
        ),
        "exam_points": [
            "The def keyword is used to define a function.",
            "Parameters receive input values.",
            "The return statement sends a result back to the caller."
        ],
        "revision": "Function = Reusable block of code"
    },
    "conditional statements": {
        "title": "Conditional Statements in Python",
        "definition": (
            "Conditional statements allow a program to make decisions "
            "based on whether a condition is true or false."
        ),
        "explanation": (
            "Think of checking whether you passed an exam. "
            "If your marks are at least 40, you pass; otherwise, "
            "you do not pass."
        ),
        "example": (
            "marks = 75\n\n"
            "if marks >= 40:\n"
            "    print('Pass')\n"
            "else:\n"
            "    print('Fail')"
        ),
        "output": "Pass",
        "real_life": (
            "A shopping website may give a discount if the order "
            "amount is above a certain value."
        ),
        "exam_points": [
            "if checks a condition.",
            "elif checks another condition if earlier conditions are false.",
            "else runs when the previous conditions are false.",
            "Indentation is important in Python."
        ],
        "revision": "if = Check a condition; else = Alternative"
    },
    "lists": {
        "title": "Lists in Python",
        "definition": (
            "A list is a collection used to store multiple items "
            "in a single variable."
        ),
        "explanation": (
            "Imagine a shopping list containing several items. "
            "Python lists work similarly by keeping multiple values "
            "together in an ordered collection."
        ),
        "example": (
            "subjects = ['Python', 'Maths', 'Physics']\n"
            "print(subjects[0])\n"
            "print(subjects[1])"
        ),
        "output": "Python\nMaths",
        "real_life": (
            "A student can store the names of all subjects "
            "in one list."
        ),
        "exam_points": [
            "Lists are written using square brackets.",
            "List indexing starts at 0.",
            "Lists are mutable, meaning their contents can be changed."
        ],
        "revision": "List = Ordered, changeable collection of items"
    }
}

# =====================================================================
# HELPER FUNCTIONS - PDF PARSING (kept free of Streamlit calls)
# =====================================================================

UNIT_RE = re.compile(
    r"^\s*(?:unit|module|chapter)\s*[-–—:.]?\s*(\d+|[ivx]+)\b\s*[-–—:.)]*\s*(.*)$",
    re.I
)
# Table style syllabi: "1 Introduction to Python:" without the word "Unit"
NUM_HEAD_RE = re.compile(
    r"^\s*(\d{1,2})[.)]?\s+([A-Z][A-Za-z0-9 ,&/()\-–]{3,90}?)\s*(:?)\s*$"
)
BULLET_RE = re.compile(r"^\s*(?:[\-\*•·●▪○◦■►]|\(?\d+[.)])\s+")
NOISE_RE = re.compile(
    r"^(page\s*\d+|\d+|total\b.*|marks?\b.*|credits?\b.*|hours?\b.*|"
    r"reference books?.*|text books?.*)$",
    re.I
)
SPLIT_RE = re.compile(r";|,(?![^(]*\))|\s[–—]\s")
REGION_START_RE = re.compile(
    r"^(?:(?:course|detailed|syllabus)\s+)?contents?\s*:?\s*$|^unit\s+topics\b",
    re.I
)
REGION_END_RE = re.compile(
    r"^(?:total\s+hours?\b|references?\b|text\s*books?\b|reference\s+books?\b|"
    r"suggested\b|supplementary\b|recommended\b)",
    re.I
)


def extract_pdf_text(uploaded_file):
    """Return all text from an uploaded PDF (empty string if unreadable)."""
    if not PDF_AVAILABLE:
        return ""
    try:
        reader = PdfReader(uploaded_file)
        pages = [(page.extract_text() or "") for page in reader.pages]
        return "\n".join(pages)
    except Exception:
        return ""


def _syllabus_region(lines):
    """Keep only the contents part of the syllabus (drop objective, books, etc.)."""
    start = next(
        (i for i, line in enumerate(lines) if REGION_START_RE.match(line)), None
    )

    if start is None:
        return lines

    end = next(
        (i for i in range(start + 1, len(lines)) if REGION_END_RE.match(lines[i])),
        len(lines)
    )

    return lines[start + 1:end]


def parse_syllabus(text):
    """Basic offline reader: list of {'unit', 'topic', 'hours'} rows.

    Gemini does this better; this is the fallback when no API key is available.
    """
    raw_lines = [
        re.sub(r"[\x00-\x1f\x7f]", " ", raw).rstrip() for raw in text.splitlines()
    ]
    stripped = [line.strip() for line in raw_lines]

    # Page headers and footers repeat on every page: drop them
    counts = {}
    for line in stripped:
        counts[line] = counts.get(line, 0) + 1

    repeated = {line for line, n in counts.items() if n >= 2 and len(line) >= 15}

    lines = [
        raw for raw, line in zip(raw_lines, stripped)
        if line and line not in repeated
    ]

    lines = _syllabus_region(lines)
    has_unit_words = any(UNIT_RE.match(line) for line in lines)

    units = []  # [label, [raw items], hours]
    last_number = 0

    for raw in lines:
        # Some PDFs use unusual bullet glyphs; they show up as control characters
        symbol_start = bool(re.match(r"^\s*[^\w\s(\"']", raw))
        line = raw.strip()

        # A lone small number after a unit's content is its contact hours
        if re.fullmatch(r"\d{1,2}", line):
            if units and units[-1][1] and units[-1][2] is None and int(line) <= 30:
                units[-1][2] = int(line)
            continue

        if NOISE_RE.match(line):
            continue

        match = UNIT_RE.match(line)

        if match:
            number = match.group(1).upper()
            title = match.group(2).strip(" -–—:.")

            if title and (len(title) > 60 or "," in title or ";" in title):
                # The topics are written on the same line as the unit heading
                units.append([f"Unit {number}", [title], None])
            elif title:
                units.append([f"Unit {number} – {title}", [], None])
            else:
                units.append([f"Unit {number}", [], None])
            continue

        head = NUM_HEAD_RE.match(line) if not has_unit_words else None

        if head:
            number = int(head.group(1))
            has_colon = bool(head.group(3))

            if has_colon or number == last_number + 1:
                last_number = number
                units.append(
                    [f"Unit {number} – {head.group(2).strip(' -–—:.')}", [], None]
                )
                continue

        if not units:
            units.append(["Syllabus", [], None])

        items = units[-1][1]
        is_bullet = bool(BULLET_RE.match(line)) or symbol_start
        line = BULLET_RE.sub("", line).strip()

        if not line:
            continue

        continues_previous = (
            items
            and not is_bullet
            and (
                items[-1].rstrip().endswith((",", "-", "–", "("))
                or line[:1].islower()
            )
        )

        if continues_previous:
            items[-1] += " " + line
        else:
            items.append(line)

    # Text before the first unit heading (title, headers) is not syllabus content
    if any(label != "Syllabus" for label, _, _ in units):
        units = [u for u in units if u[0] != "Syllabus"]

    rows = []

    for label, items, hours in units:
        seen = set()

        for item in items:
            for part in SPLIT_RE.split(item):
                part = re.sub(r"^[^\w(]+", "", part).strip(" .:-–—•\t")
                part = re.sub(r"\s+", " ", part)

                if not (3 <= len(part) <= 90):
                    continue
                if NOISE_RE.match(part) or part.lower().startswith("http"):
                    continue
                if part.lower() in seen:
                    continue

                seen.add(part.lower())
                rows.append({"unit": label, "topic": part, "hours": hours})

    return rows[:300]


def parse_pattern(text):
    """Find lines such as '10 x 2' or 'Section A: 5 × 5 = 25' in pattern text."""
    rows = []
    last_section = None
    used_sections = set()

    for line in text.splitlines():
        section_match = re.search(r"section\s*[-:]?\s*([A-Za-z0-9]+)", line, re.I)

        if section_match:
            last_section = f"Section {section_match.group(1).upper()}"

        match = re.search(r"(\d+)\s*[x×X*]\s*(\d+)", line)

        if not match:
            continue

        questions, marks = int(match.group(1)), int(match.group(2))

        if not (0 < questions <= 50 and 0 < marks <= 100):
            continue

        if last_section and last_section not in used_sections:
            name = last_section
        else:
            name = f"Section {chr(65 + min(len(rows), 25))}"

        used_sections.add(name)

        rows.append({
            "Section": name,
            "Questions": questions,
            "Marks each": marks
        })

    return rows


# =====================================================================
# HELPER FUNCTIONS - DATA
# =====================================================================

def days_left(exam_date):
    return (exam_date - date.today()).days


def make_topics_df(parsed_rows):
    """Build the editable topic table.

    When every unit has contact hours, a unit's share of the marks follows its
    hours and is split between its topics. Otherwise topics share equally.
    """
    count = max(1, len(parsed_rows))
    weights = [100.0 / count] * len(parsed_rows)

    unit_hours = {}
    unit_count = {}

    for row in parsed_rows:
        unit_count[row["unit"]] = unit_count.get(row["unit"], 0) + 1

        hours = row.get("hours")
        if hours and row["unit"] not in unit_hours:
            unit_hours[row["unit"]] = float(hours)

    total_hours = sum(unit_hours.values())

    if unit_count and len(unit_hours) == len(unit_count) and total_hours > 0:
        weights = [
            unit_hours[row["unit"]] / total_hours * 100.0 / unit_count[row["unit"]]
            for row in parsed_rows
        ]

    rounded = [round(w, 1) for w in weights]

    if rounded:
        # Rounding can leave the total at 99.9 or 100.1; fix it on the last row
        rounded[-1] = round(rounded[-1] + (100.0 - sum(rounded)), 1)

    return pd.DataFrame(
        [
            {
                "Unit": row["unit"],
                "Topic": row["topic"],
                "Weightage (%)": weight,
                "Difficulty": "Medium",
                "Preparation": "Not Started",
            }
            for row, weight in zip(parsed_rows, rounded)
        ],
        columns=TOPIC_COLUMNS
    )


def subject_topics_df(subject):
    return pd.DataFrame(
        [
            {
                "Unit": t["unit"],
                "Topic": t["name"],
                "Weightage (%)": t["weightage"],
                "Difficulty": t["difficulty"],
                "Preparation": t["preparation"],
            }
            for t in subject["topics"]
        ],
        columns=TOPIC_COLUMNS
    )


def _clean_value(value, default=""):
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return default
    return value


def clean_topics(df):
    """Convert the editor table to a clean list of topic dictionaries."""
    topics = []

    for _, row in df.iterrows():
        name = str(_clean_value(row.get("Topic"), "")).strip()

        if not name:
            continue

        difficulty = _clean_value(row.get("Difficulty"), "Medium")
        preparation = _clean_value(row.get("Preparation"), "Not Started")

        raw_weight = _clean_value(row.get("Weightage (%)"), None)

        try:
            weightage = None if raw_weight is None else float(raw_weight)
        except (TypeError, ValueError):
            weightage = None

        topics.append({
            "unit": str(_clean_value(row.get("Unit"), "")).strip() or "General",
            "name": name,
            "weightage": weightage,
            "difficulty": difficulty if difficulty in DIFFICULTY else "Medium",
            "preparation": (
                preparation if preparation in PREPARATION else "Not Started"
            ),
        })

    # A blank weightage becomes the average of the topics that have one
    given = [t["weightage"] for t in topics if t["weightage"] is not None]
    average = round(sum(given) / len(given), 1) if given else 0.0

    for t in topics:
        if t["weightage"] is None:
            t["weightage"] = average

    if topics and sum(t["weightage"] for t in topics) <= 0:
        share = round(100.0 / len(topics), 1)
        for t in topics:
            t["weightage"] = share

    return topics


def clean_pattern(df):
    rows = []

    for _, row in df.iterrows():
        section = str(_clean_value(row.get("Section"), "")).strip()

        try:
            questions = int(_clean_value(row.get("Questions"), 0) or 0)
            marks = float(_clean_value(row.get("Marks each"), 0) or 0)
        except (TypeError, ValueError):
            continue

        if section and questions > 0 and marks > 0:
            rows.append({
                "Section": section,
                "Questions": questions,
                "Marks each": marks
            })

    return rows


def unit_names(subject):
    seen = []
    for t in subject["topics"]:
        if t["unit"] not in seen:
            seen.append(t["unit"])
    return seen


def get_subject(name):
    for s in st.session_state.subjects:
        if s["name"] == name:
            return s
    return None


def completed_for(subject):
    done = st.session_state.completed.get(subject["name"], [])
    valid = {t["name"] for t in subject["topics"]}
    return [t for t in done if t in valid]


def completion_percent(subject):
    total = len(subject["topics"])
    if total == 0:
        return 0.0
    return len(completed_for(subject)) / total * 100


def priority_rows(subject):
    rows = []

    for t in subject["topics"]:
        score = (
            t["weightage"]
            * DIFFICULTY_MULTIPLIER[t["difficulty"]]
            * PREPARATION_MULTIPLIER[t["preparation"]]
        )
        rows.append({
            "Unit": t["unit"],
            "Topic": t["name"],
            "Weightage (%)": t["weightage"],
            "Difficulty": t["difficulty"],
            "Preparation": t["preparation"],
            "Priority Score": round(score, 2),
        })

    rows.sort(key=lambda r: r["Priority Score"], reverse=True)
    return rows


def pattern_text(subject):
    if subject["pattern_mode"] == "Not provided" or not subject["pattern"]:
        return "No official question-paper pattern was provided."

    parts = [
        f"{p['Section']}: {p['Questions']} questions x {p['Marks each']:g} marks"
        for p in subject["pattern"]
    ]
    return "; ".join(parts)


def is_python_subject(subject):
    return "python" in subject["name"].lower()


def find_lesson_key(subject, topic):
    if not is_python_subject(subject):
        return None
    for key in lessons:
        if key in topic.lower():
            return key
    return None


def delete_subject(name):
    st.session_state.subjects = [
        s for s in st.session_state.subjects if s["name"] != name
    ]
    st.session_state.completed.pop(name, None)
    st.session_state.study_plans.pop(name, None)
    st.session_state.tutor_chats.pop(name, None)
    st.session_state.coach.pop(name, None)
    prefix = f"{name} › "
    st.session_state.weak_topics = [
        w for w in st.session_state.weak_topics if not w.startswith(prefix)
    ]
    st.session_state.practice_results = [
        r for r in st.session_state.practice_results if r["subject"] != name
    ]
    st.session_state.practice_feedback = None
    st.session_state.pop("active_subject", None)


# =====================================================================
# HELPER FUNCTIONS - GEMINI
# =====================================================================

def call_gemini(contents, system_instruction=None, json_mode=False):
    api_key = os.getenv("GEMINI_API_KEY")

    if not api_key:
        raise RuntimeError("GEMINI_API_KEY was not found.")

    client = genai.Client(api_key=api_key)

    config_args = {}
    if system_instruction:
        config_args["system_instruction"] = system_instruction
    if json_mode:
        config_args["response_mime_type"] = "application/json"

    config = types.GenerateContentConfig(**config_args)

    last_error = None

    for attempt in range(3):
        try:
            response = client.models.generate_content(
                model=MODEL_NAME,
                contents=contents,
                config=config
            )
            return response.text or ""
        except Exception as error:
            last_error = error
            busy = any(
                code in str(error)
                for code in ("503", "UNAVAILABLE", "429")
            )
            if busy and attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
            raise

    raise last_error


def generate_ai_questions(subject, topic, count=5):
    prompt = f"""
Create {count} multiple-choice practice questions for exam preparation.

Subject: {subject['name']}
Topic: {topic}
Total exam marks: {subject['total_marks']}
Question paper pattern: {pattern_text(subject)}

Rules:
- Match the difficulty of a university exam.
- Each question has exactly 4 options.
- The "answer" value must be exactly equal to one of the options.
- Give a short explanation for the correct answer.

Return ONLY a JSON array. Each item must have the keys:
"question", "options", "answer", "explanation".
"""

    text = call_gemini(prompt, json_mode=True)
    data = json.loads(text)

    if isinstance(data, dict):
        lists = [v for v in data.values() if isinstance(v, list)]
        data = lists[0] if lists else []

    questions = []

    for item in data:
        if not isinstance(item, dict):
            continue

        options = item.get("options")
        answer = item.get("answer")

        if (
            item.get("question")
            and isinstance(options, list)
            and len(options) >= 2
            and answer in options
        ):
            questions.append({
                "question": str(item["question"]),
                "options": [str(o) for o in options],
                "answer": str(answer),
                "explanation": str(item.get("explanation", "")),
            })

    return questions


# =====================================================================
# QUESTION PAPER PHOTO -> PATTERN (Gemini vision)
# =====================================================================

IMAGE_TYPES = ["png", "jpg", "jpeg", "webp"]
MAX_PHOTO_SIDE = 1800   # phone photos are shrunk to this many pixels


def prepare_photo(raw_bytes, mime_type):
    """Shrink a big phone photo and fix its rotation. Returns (bytes, mime)."""
    try:
        from io import BytesIO
        from PIL import Image, ImageOps

        image = Image.open(BytesIO(raw_bytes))
        image = ImageOps.exif_transpose(image)
        image.thumbnail((MAX_PHOTO_SIDE, MAX_PHOTO_SIDE))

        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")

        buffer = BytesIO()
        image.save(buffer, format="JPEG", quality=88)
        return buffer.getvalue(), "image/jpeg"
    except Exception:
        # Pillow missing or unreadable image: send the original
        return raw_bytes, mime_type or "image/jpeg"


def parse_pattern_json(text):
    """Turn Gemini's JSON answer into (rows, info). Never raises."""
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return [], {}

    if isinstance(data, list):
        data = {"sections": data}

    if not isinstance(data, dict):
        return [], {}

    rows = []

    for item in data.get("sections") or []:
        if not isinstance(item, dict):
            continue

        try:
            questions = int(float(item.get("questions_to_answer")
                                  or item.get("questions") or 0))
            marks = float(item.get("marks_each") or 0)
        except (TypeError, ValueError):
            continue

        if not (0 < questions <= 100 and 0 < marks <= 100):
            continue

        label = str(item.get("section") or f"Section {chr(65 + min(len(rows), 25))}").strip()
        kind = str(item.get("type") or "").strip()

        if kind and kind.lower() not in label.lower():
            label = f"{label} ({kind})"

        rows.append({
            "Section": label[:60],
            "Questions": questions,
            "Marks each": marks,
        })

    info = {}

    for key in ("total_marks", "duration_hours"):
        try:
            value = float(data.get(key))
            if value > 0:
                info[key] = value
        except (TypeError, ValueError):
            pass

    return rows, info


def read_pattern_from_photos(photos):
    """photos: list of (bytes, mime). Returns (rows, info)."""
    prompt = """
These images are photos of ONE exam question paper (possibly several pages).
Work out the paper PATTERN only: how the marks are distributed.

For every section or part of the paper, give:
- "section": its name as printed (for example "Section A" or "Part B")
- "type": the question type in 1-3 words (for example "MCQ", "Short answer", "Long answer", "Fill in the blanks")
- "questions": how many questions the section contains
- "questions_to_answer": how many the student must answer, if the paper offers a choice (otherwise the same as "questions")
- "marks_each": marks for each question

Also give "total_marks" and "duration_hours" if they are printed on the paper.

Rules:
- Use only what is visible on the paper. Never guess a number that you cannot read.
- If a section's marks are written as a section total (for example "5 x 2 = 10"), use the per-question marks.
- If nothing readable is a question paper, return {"sections": []}.

Return ONLY JSON in this form:
{"total_marks": 100, "duration_hours": 3,
 "sections": [{"section": "Section A", "type": "MCQ", "questions": 10, "questions_to_answer": 10, "marks_each": 2}]}
"""

    parts = [
        types.Part.from_bytes(data=data, mime_type=mime)
        for data, mime in photos
    ]

    text = call_gemini(parts + [prompt], json_mode=True)
    return parse_pattern_json(text)


# =====================================================================
# SYLLABUS PDF -> UNITS AND TOPICS (Gemini)
# =====================================================================

SYLLABUS_PROMPT = """
You are reading a university syllabus document. Extract ONLY the study content
that a student must learn for the exam, organised by unit.

IGNORE all of this: university or department titles, subject code, objective,
course outcomes, prerequisites, credits, teaching and examination scheme tables,
reference or text books, web links, supplementary resources, instructional
methods, Bloom's taxonomy distribution, and the list of practicals or experiments.

For every unit give:
- "number": the unit number
- "title": the unit title without the number
- "hours": the contact hours printed for that unit (a number), or null if not printed
- "topics": between 4 and 10 topics

Rules for topics:
- Each topic is a study-sized chunk of 2 to 8 words that groups closely related
  small items (for example "Strings and string operations"), not a single word.
- Keep the syllabus's own wording and order. Fix obvious typos.
- Never invent topics that are not in the document.
- If a unit continues on the next page, merge it into the same unit.

Also return, when printed in the document:
- "subject_name", "subject_code", "total_hours"
- "marks_note": one short line summarising the marks scheme (otherwise "")

Return ONLY JSON in this form:
{"subject_name": "", "subject_code": "", "total_hours": 45, "marks_note": "",
 "units": [{"number": 1, "title": "", "hours": 5, "topics": ["", ""]}]}
"""


def syllabus_rows_from_json(text):
    """Turn Gemini's JSON answer into (rows, info). Never raises."""
    try:
        cleaned = re.sub(r"^```(?:json)?|```$", "", str(text).strip(), flags=re.M).strip()
        data = json.loads(cleaned)
    except (TypeError, ValueError):
        return [], {}

    if isinstance(data, list):
        data = {"units": data}

    if not isinstance(data, dict):
        return [], {}

    rows = []

    for index, unit in enumerate(data.get("units") or [], start=1):
        if not isinstance(unit, dict):
            continue

        number = unit.get("number")
        number = str(number).strip() if number not in (None, "") else str(index)
        title = str(unit.get("title") or "").strip(" -–—:.")
        label = f"Unit {number} – {title}" if title else f"Unit {number}"

        try:
            hours = float(unit.get("hours"))
            hours = hours if 0 < hours <= 200 else None
        except (TypeError, ValueError):
            hours = None

        seen = set()

        for topic in unit.get("topics") or []:
            topic = re.sub(r"\s+", " ", str(topic)).strip(" .:-–—•")

            if not (2 <= len(topic) <= 100) or topic.lower() in seen:
                continue

            seen.add(topic.lower())
            rows.append({"unit": label, "topic": topic, "hours": hours})

    info = {}

    for key in ("subject_name", "subject_code", "marks_note"):
        value = str(data.get(key) or "").strip()
        if value:
            info[key] = value[:160]

    try:
        total = float(data.get("total_hours"))
        if total > 0:
            info["total_hours"] = total
    except (TypeError, ValueError):
        pass

    return rows[:300], info


def extract_syllabus_ai(raw_bytes, text):
    """Ask Gemini to structure the syllabus. Uses the PDF itself if it has no text."""
    if text.strip():
        contents = [SYLLABUS_PROMPT + "\n\nSYLLABUS TEXT:\n" + text[:40000]]
    else:
        contents = [
            types.Part.from_bytes(data=raw_bytes, mime_type="application/pdf"),
            SYLLABUS_PROMPT,
        ]

    return syllabus_rows_from_json(call_gemini(contents, json_mode=True))


def read_syllabus(raw_bytes, text):
    """Returns {'rows', 'info', 'method', 'notice'}. Gemini first, basic reader second."""
    notice = ""

    if os.getenv("GEMINI_API_KEY"):
        try:
            rows, info = extract_syllabus_ai(raw_bytes, text)

            if rows:
                return {"rows": rows, "info": info, "method": "AI", "notice": ""}

            notice = "Gemini did not find a syllabus in this PDF."
        except Exception as error:
            notice = f"Gemini could not read it ({error})."
    else:
        notice = "No Gemini API key was found."

    rows = parse_syllabus(text) if text.strip() else []

    return {"rows": rows, "info": {}, "method": "rules", "notice": notice}


# =====================================================================
# PAGE: SUBJECT MANAGEMENT
# =====================================================================

def page_subjects():
    section_title("MODULE 02", "Subject Management", "Add subjects, upload syllabus PDFs and set exam details")

    if st.session_state.flash:
        st.success(st.session_state.flash)
        st.session_state.flash = None

    st.text_input("Student name", key="student_name")

    count = len(st.session_state.subjects)
    tab_add, tab_manage = st.tabs(
        ["➕ Add a subject", f"📚 My subjects ({count})"]
    )

    with tab_add:
        render_add_subject()

    with tab_manage:
        render_manage_subjects()


def render_add_subject():
    fid = st.session_state.form_id

    st.subheader("1. Subject and exam details")

    name = st.text_input(
        "Subject name",
        placeholder="Example: Python Programming",
        key=f"sub_name_{fid}"
    )

    c1, c2, c3, c4 = st.columns(4)

    exam_date = c1.date_input(
        "Exam date",
        value=date.today() + timedelta(days=14),
        min_value=date.today(),
        key=f"sub_date_{fid}"
    )
    total_marks = c2.number_input(
        "Total marks", min_value=1, max_value=1000, value=100,
        key=f"sub_marks_{fid}"
    )
    duration = c3.number_input(
        "Exam duration (hours)", min_value=0.5, max_value=12.0,
        value=3.0, step=0.5, key=f"sub_duration_{fid}"
    )
    daily_hours = c4.number_input(
        "Study hours per day", min_value=0.5, max_value=16.0,
        value=3.0, step=0.5, key=f"sub_hours_{fid}"
    )

    # ---------------- SYLLABUS PDF ----------------

    st.subheader("2. Syllabus PDF")

    if not PDF_AVAILABLE:
        st.error(
            "PDF reading needs the pypdf package. Run this in the terminal, "
            "then restart the app:  D:\\python\\python.exe -m pip install pypdf. "
            "Until then you can type the topics into the table below."
        )

    pdf = st.file_uploader(
        "Upload the syllabus PDF (one PDF per subject)",
        type=["pdf"],
        key=f"sub_pdf_{fid}"
    )

    signature = "none"
    base_df = make_topics_df([])
    stamp = 0

    if pdf is not None:
        signature = f"{pdf.name}-{pdf.size}"

        if signature not in st.session_state.parse_cache:
            with st.spinner("Your AI is reading the syllabus..."):
                text = extract_pdf_text(pdf)
                result = read_syllabus(pdf.getvalue(), text)

                st.session_state.parse_cache[signature] = {
                    "text": text,
                    "df": make_topics_df(result["rows"]),
                    "info": result["info"],
                    "method": result["method"],
                    "notice": result["notice"],
                    "stamp": time.time(),
                }

        cached = st.session_state.parse_cache[signature]
        base_df = cached["df"]
        stamp = cached.get("stamp", 0)

        if base_df.empty:
            if not cached["text"].strip() and cached["method"] != "AI":
                st.warning(
                    "No text could be read from this PDF. It may be a scanned "
                    "image and needs the Gemini key to read it. Or type the "
                    "topics into the table below."
                )
            else:
                st.warning(
                    "I couldn't find a syllabus in this PDF. "
                    f"{cached['notice']} Please type the topics below."
                )
        else:
            units_found = base_df["Unit"].nunique()

            if cached["method"] == "AI":
                st.success(
                    f"Your AI read the syllabus: {units_found} unit(s) and "
                    f"{len(base_df)} topics. Please check them below."
                )
            else:
                st.info(
                    f"A basic reader found {len(base_df)} possible topics in "
                    f"{units_found} unit(s). It is less accurate than the AI "
                    f"reader, so check the table carefully. {cached['notice']}"
                )

            info = cached["info"]
            facts = []
            if info.get("subject_name"):
                facts.append(f"subject **{info['subject_name']}**")
            if info.get("subject_code"):
                facts.append(f"code {info['subject_code']}")
            if info.get("total_hours"):
                facts.append(f"{info['total_hours']:g} contact hours")
            if info.get("marks_note"):
                facts.append(f"marks: {info['marks_note']}")
            if facts:
                st.caption("Found in the PDF: " + " · ".join(facts))

            st.caption(
                "Weightage is shared out by each unit's contact hours when the "
                "PDF lists them. Adjust it if your teacher stressed other units."
            )

        if st.button("🔄 Read this PDF again", key=f"reread_{fid}"):
            st.session_state.parse_cache.pop(signature, None)
            st.rerun()

    st.markdown("**Review your syllabus.** Fix names, delete wrong rows, add "
                "missing topics, and set the marks weightage, difficulty and "
                "your preparation level.")

    topics_df = st.data_editor(
        base_df,
        num_rows="dynamic",
        use_container_width=True,
        hide_index=True,
        key=f"topics_editor_{fid}_{signature}_{stamp}",
        column_config={
            "Unit": st.column_config.TextColumn("Unit"),
            "Topic": st.column_config.TextColumn("Topic"),
            "Weightage (%)": st.column_config.NumberColumn(
                "Weightage (%)", min_value=0.0, max_value=100.0,
                step=1.0, format="%.1f"
            ),
            "Difficulty": st.column_config.SelectboxColumn(
                "Difficulty", options=DIFFICULTY
            ),
            "Preparation": st.column_config.SelectboxColumn(
                "Preparation", options=PREPARATION
            ),
        }
    )

    # ---------------- QUESTION PAPER PATTERN ----------------

    st.subheader("3. Question-paper pattern (optional)")

    mode = st.radio(
        "How would you like to provide the pattern?",
        [
            "Upload pattern PDF",
            "📷 Photo of a question paper",
            "Enter manually",
            "I don't have a pattern",
        ],
        index=3,
        horizontal=True,
        key=f"pattern_mode_{fid}"
    )

    pattern_rows = []
    pattern_mode_label = "Not provided"

    if mode == "Upload pattern PDF":
        pattern_pdf = st.file_uploader(
            "Upload the question-paper pattern PDF",
            type=["pdf"],
            key=f"pattern_pdf_{fid}"
        )

        pattern_df = pd.DataFrame(columns=PATTERN_COLUMNS)
        psig = "none"

        if pattern_pdf is not None:
            psig = f"{pattern_pdf.name}-{pattern_pdf.size}"
            cache_key = f"pattern-{psig}"

            if cache_key not in st.session_state.parse_cache:
                text = extract_pdf_text(pattern_pdf)
                st.session_state.parse_cache[cache_key] = {
                    "text": text,
                    "df": pd.DataFrame(parse_pattern(text), columns=PATTERN_COLUMNS),
                }

            pattern_df = st.session_state.parse_cache[cache_key]["df"]

            if pattern_df.empty:
                st.warning(
                    "No sections such as '10 x 2' were found. "
                    "Please add them in the table below."
                )
            else:
                st.success("Sections found. Please check them below.")

        edited = st.data_editor(
            pattern_df,
            num_rows="dynamic",
            use_container_width=True,
            hide_index=True,
            key=f"pattern_editor_pdf_{fid}_{psig}",
            column_config={
                "Questions": st.column_config.NumberColumn(
                    "Questions", min_value=1, step=1
                ),
                "Marks each": st.column_config.NumberColumn(
                    "Marks each", min_value=0.5, step=0.5
                ),
            }
        )
        pattern_rows = clean_pattern(edited)
        pattern_mode_label = "Uploaded PDF"

    elif mode == "📷 Photo of a question paper":
        st.caption(
            "Take a clear photo of a previous exam paper (or upload one). "
            "For a paper with several pages, add every page. The photo is "
            "sent to Google Gemini to read the pattern."
        )

        uploads = st.file_uploader(
            "Upload photo(s) of the question paper",
            type=IMAGE_TYPES,
            accept_multiple_files=True,
            key=f"pattern_photos_{fid}"
        )

        camera_shot = None

        with st.expander("📸 Use my camera instead"):
            camera_shot = st.camera_input(
                "Take a photo of the paper", key=f"pattern_camera_{fid}"
            )

        files = list(uploads or [])
        if camera_shot is not None:
            files.append(camera_shot)

        photo_df = pd.DataFrame(columns=PATTERN_COLUMNS)
        psig = "none"
        stamp = 0

        if files:
            psig = "photo-" + "|".join(f"{f.name}-{f.size}" for f in files)

            preview_columns = st.columns(min(len(files), 4))
            for column, f in zip(preview_columns, files[:4]):
                column.image(f, use_container_width=True)

            cached = st.session_state.parse_cache.get(psig)

            if st.button(
                "🔍 Read the pattern from the photo(s)" if not cached
                else "🔄 Read again",
                key=f"read_photo_{fid}",
                type="primary" if not cached else "secondary"
            ):
                if not os.getenv("GEMINI_API_KEY"):
                    st.error(
                        "Reading photos needs your Gemini API key "
                        "(GEMINI_API_KEY)."
                    )
                else:
                    with st.spinner("Reading your question paper..."):
                        try:
                            photos = [
                                prepare_photo(f.getvalue(), f.type)
                                for f in files[:6]
                            ]
                            rows, info = read_pattern_from_photos(photos)
                            st.session_state.parse_cache[psig] = {
                                "df": pd.DataFrame(rows, columns=PATTERN_COLUMNS),
                                "info": info,
                                "error": "",
                                "stamp": time.time(),
                            }
                        except Exception as error:
                            st.session_state.parse_cache[psig] = {
                                "df": pd.DataFrame(columns=PATTERN_COLUMNS),
                                "info": {},
                                "error": str(error),
                                "stamp": time.time(),
                            }
                    cached = st.session_state.parse_cache.get(psig)

            if cached:
                photo_df = cached["df"]
                stamp = cached.get("stamp", 0)

                if cached["error"]:
                    st.error(
                        "I couldn't read the photo right now. Try again, or "
                        f"type the pattern below. ({cached['error']})"
                    )
                elif photo_df.empty:
                    st.warning(
                        "I couldn't find a clear pattern in this photo. Try "
                        "a sharper, well-lit photo, or type the sections "
                        "in the table below."
                    )
                else:
                    st.success(
                        "Pattern read from your photo. Please check every "
                        "row against the paper before saving."
                    )

                    bits = []
                    if cached["info"].get("total_marks"):
                        bits.append(f"total marks {cached['info']['total_marks']:g}")
                    if cached["info"].get("duration_hours"):
                        bits.append(f"duration {cached['info']['duration_hours']:g} h")
                    if bits:
                        st.caption(
                            "The paper also shows: " + ", ".join(bits)
                            + ". Update the exam details above if needed."
                        )

        edited = st.data_editor(
            photo_df,
            num_rows="dynamic",
            use_container_width=True,
            hide_index=True,
            key=f"pattern_editor_photo_{fid}_{abs(hash(psig))}_{stamp}",
            column_config={
                "Questions": st.column_config.NumberColumn(
                    "Questions", min_value=1, step=1
                ),
                "Marks each": st.column_config.NumberColumn(
                    "Marks each", min_value=0.5, step=0.5
                ),
            }
        )
        pattern_rows = clean_pattern(edited)
        pattern_mode_label = "Read from photo"

    elif mode == "Enter manually":
        starter = pd.DataFrame(
            [{"Section": "Section A", "Questions": 10, "Marks each": 2.0}],
            columns=PATTERN_COLUMNS
        )
        edited = st.data_editor(
            starter,
            num_rows="dynamic",
            use_container_width=True,
            hide_index=True,
            key=f"pattern_editor_manual_{fid}",
            column_config={
                "Questions": st.column_config.NumberColumn(
                    "Questions", min_value=1, step=1
                ),
                "Marks each": st.column_config.NumberColumn(
                    "Marks each", min_value=0.5, step=0.5
                ),
            }
        )
        pattern_rows = clean_pattern(edited)
        pattern_mode_label = "Entered manually"

    if pattern_rows:
        pattern_total = sum(p["Questions"] * p["Marks each"] for p in pattern_rows)

        if abs(pattern_total - total_marks) < 0.01:
            st.caption(f"Pattern total: {pattern_total:g} marks ✅")
        else:
            st.warning(
                f"The pattern adds up to {pattern_total:g} marks, but the "
                f"total marks are {total_marks}. Choice questions can cause "
                f"this; otherwise please check the table."
            )

    # ---------------- SAVE ----------------

    st.divider()

    if st.button("💾 Save subject", type="primary", use_container_width=True,
                 key=f"save_subject_{fid}"):

        topics = clean_topics(topics_df)
        errors = []
        clean_name = name.strip()

        if not clean_name:
            errors.append("Please enter the subject name.")
        elif get_subject(clean_name) or any(
            s["name"].lower() == clean_name.lower()
            for s in st.session_state.subjects
        ):
            errors.append("A subject with this name already exists.")

        if PDF_AVAILABLE and pdf is None:
            errors.append("Please upload the syllabus PDF.")

        if not topics:
            errors.append("Please add at least one topic to the syllabus.")

        if mode != "I don't have a pattern" and not pattern_rows:
            errors.append(
                "Add at least one pattern section, or choose "
                "'I don't have a pattern'."
            )

        if errors:
            for message in errors:
                st.error(message)
        else:
            st.session_state.subjects.append({
                "name": clean_name,
                "exam_date": exam_date,
                "total_marks": int(total_marks),
                "duration": float(duration),
                "daily_hours": float(daily_hours),
                "syllabus_file": pdf.name if pdf is not None else "Entered manually",
                "topics": topics,
                "pattern_mode": pattern_mode_label if pattern_rows else "Not provided",
                "pattern": pattern_rows,
                "version": 0,
            })
            st.session_state.flash = (
                f"'{clean_name}' was saved with {len(topics)} topics."
            )
            st.session_state.form_id += 1
            st.rerun()


def render_manage_subjects():
    subjects = st.session_state.subjects

    if not subjects:
        st.info("No subjects yet. Add your first subject in the other tab.")
        return

    for s in sorted(subjects, key=lambda x: x["exam_date"]):
        remaining = days_left(s["exam_date"])
        label = (
            f"{s['name']} · exam {s['exam_date']:%d %b %Y} "
            f"({max(0, remaining)} days left)"
        )

        with st.expander(label):
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Units", len(unit_names(s)))
            c2.metric("Topics", len(s["topics"]))
            c3.metric("Total marks", s["total_marks"])
            c4.metric("Duration", f"{s['duration']:g} h")

            st.write(f"**Syllabus file:** {s['syllabus_file']}")
            st.write(f"**Study hours per day:** {s['daily_hours']:g}")
            st.write(f"**Question-paper pattern:** {s['pattern_mode']}")

            if s["pattern"]:
                st.dataframe(
                    pd.DataFrame(s["pattern"]),
                    use_container_width=True,
                    hide_index=True
                )

            st.markdown("**Edit syllabus topics**")

            edited = st.data_editor(
                subject_topics_df(s),
                num_rows="dynamic",
                use_container_width=True,
                hide_index=True,
                key=f"edit_{s['name']}_{s['version']}",
                column_config={
                    "Weightage (%)": st.column_config.NumberColumn(
                        "Weightage (%)", min_value=0.0, max_value=100.0,
                        step=1.0, format="%.1f"
                    ),
                    "Difficulty": st.column_config.SelectboxColumn(
                        "Difficulty", options=DIFFICULTY
                    ),
                    "Preparation": st.column_config.SelectboxColumn(
                        "Preparation", options=PREPARATION
                    ),
                }
            )

            b1, b2 = st.columns(2)

            if b1.button("Save changes", key=f"save_edit_{s['name']}",
                         use_container_width=True):
                topics = clean_topics(edited)

                if not topics:
                    st.error("A subject needs at least one topic.")
                else:
                    s["topics"] = topics
                    s["version"] += 1
                    st.session_state.study_plans.pop(s["name"], None)
                    st.session_state.flash = f"Changes to '{s['name']}' were saved."
                    st.rerun()

            if b2.button("🗑️ Delete subject", key=f"delete_{s['name']}",
                         use_container_width=True):
                delete_subject(s["name"])
                st.session_state.flash = f"'{s['name']}' was deleted."
                st.rerun()


# =====================================================================
# PAGE: DASHBOARD
# =====================================================================

def page_dashboard():
    name = st.session_state.student_name.strip()
    section_title(
        "MODULE 01",
        "Command Deck",
        f"Welcome back, {name}. Here is your exam overview." if name
        else "Your exam overview"
    )

    subjects = st.session_state.subjects

    if not subjects:
        st.info(
            "Start in **Subjects**: add each subject, upload its syllabus PDF, "
            "set the exam date and (optionally) the question-paper pattern."
        )
        st.subheader("What can you do here?")
        st.write("📄 Upload a syllabus PDF for every subject")
        st.write("📅 Create a personalized study timetable")
        st.write("📖 Learn difficult topics in simple language")
        st.write("🤖 Ask questions to the AI tutor")
        st.write("📝 Practice with quizzes")
        st.write("📊 Track your learning progress")
        return

    total_units = sum(len(unit_names(s)) for s in subjects)
    total_topics = sum(len(s["topics"]) for s in subjects)

    upcoming = [s for s in subjects if days_left(s["exam_date"]) >= 0]
    next_exam = min(upcoming, key=lambda s: s["exam_date"]) if upcoming else None

    ring_col, tile_col = st.columns([1, 2.2])

    with ring_col:
        st.markdown(
            countdown_ring(
                days_left(next_exam["exam_date"]) if next_exam else None
            ),
            unsafe_allow_html=True
        )

    with tile_col:
        stat_tiles([
            ("📚", "Subjects", len(subjects), False),
            ("🧩", "Units", total_units, False),
            ("📝", "Topics", total_topics, False),
            ("🏁", "Exams ahead", len(upcoming), True),
        ])

    if next_exam:
        st.info(
            f"Next exam: **{next_exam['name']}** on "
            f"{next_exam['exam_date']:%d %B %Y}. "
            f"Open **🧠 Study Coach** for today's guided mission."
        )

    st.subheader("🛰️ Your subjects")

    ordered = sorted(subjects, key=lambda s: s["exam_date"])
    columns = st.columns(2)

    for index, s in enumerate(ordered):
        with columns[index % 2]:
            with st.container(border=True):
                remaining = days_left(s["exam_date"])

                st.markdown(f"### {s['name']}")
                st.caption(
                    f"{len(unit_names(s))} units · {len(s['topics'])} topics · "
                    f"{s['total_marks']} marks"
                )

                if remaining >= 0:
                    st.write(
                        f"📅 {s['exam_date']:%d %B %Y} — "
                        f"**{remaining} days left**"
                    )
                else:
                    st.write(f"📅 {s['exam_date']:%d %B %Y} — exam date passed")

                st.write(f"📋 Pattern: {s['pattern_mode']}")

                percent = completion_percent(s)
                st.progress(percent / 100)
                st.caption(f"Study completion: {percent:.0f}%")

                done = set(completed_for(s))
                pending = [r for r in priority_rows(s) if r["Topic"] not in done]

                if pending:
                    st.write(f"🎯 Focus next: **{pending[0]['Topic']}**")
                else:
                    st.write("🎉 All topics completed")

    st.subheader("Summary table")

    summary = pd.DataFrame([
        {
            "Subject": s["name"],
            "Exam date": s["exam_date"],
            "Days left": max(0, days_left(s["exam_date"])),
            "Units": len(unit_names(s)),
            "Topics": len(s["topics"]),
            "Total marks": s["total_marks"],
            "Pattern": s["pattern_mode"],
            "Completed (%)": round(completion_percent(s), 1),
        }
        for s in ordered
    ])
    st.dataframe(summary, use_container_width=True, hide_index=True)

    st.subheader("Syllabus overview")

    for s in ordered:
        with st.expander(f"{s['name']} — units and topics"):
            for unit in unit_names(s):
                st.markdown(f"**{unit}**")
                for t in s["topics"]:
                    if t["unit"] == unit:
                        st.write(f"- {t['name']}")


# =====================================================================
# PAGE: STUDY PLANNER
# =====================================================================

def build_timetable(subject, rows, done, days):
    daily = subject["daily_hours"]
    learning_hours = daily * 0.80
    revision_hours = daily * 0.20

    pending = [r for r in rows if r["Topic"] not in done]
    total_priority = sum(r["Priority Score"] for r in pending) or 1.0
    capacity = days * learning_hours

    queue = [
        [r["Topic"], r["Unit"], capacity * r["Priority Score"] / total_priority]
        for r in pending
    ]

    plan = []

    for day_number in range(days):
        study_date = date.today() + timedelta(days=day_number)
        remaining = learning_hours

        while remaining > 0.009 and queue:
            topic, unit, hours = queue[0]
            allocated = min(remaining, hours)

            plan.append({
                "Date": study_date,
                "Activity": "Learn / Practice",
                "Unit": unit,
                "Topic": topic,
                "Duration (Hours)": round(allocated, 2),
            })

            remaining -= allocated
            queue[0][2] -= allocated

            if queue[0][2] <= 0.009:
                queue.pop(0)

        plan.append({
            "Date": study_date,
            "Activity": "Revision",
            "Unit": "",
            "Topic": "Revise previously studied topics",
            "Duration (Hours)": round(revision_hours, 2),
        })

    return plan


def page_planner(s):
    section_title("MODULE 03", "Mission Planner", "A priority-based timetable from today to exam day")

    remaining = days_left(s["exam_date"])

    if remaining < 0:
        st.error(
            "This exam date has already passed. Update the subject's exam "
            "date by deleting and re-adding it."
        )
        return

    days_to_prepare = max(1, remaining)

    c1, c2, c3 = st.columns(3)
    c1.metric("Subject", s["name"])
    c2.metric("Days available", days_to_prepare)
    c3.metric("Study hours available", f"{days_to_prepare * s['daily_hours']:.1f}")

    st.divider()
    st.subheader("🧠 Topic Priority Analysis")
    st.write(
        "Topics are ranked by marks weightage, difficulty and your "
        "preparation level. This is a planning guide, not a guarantee of "
        "what will appear in the exam."
    )

    rows = priority_rows(s)
    done = set(completed_for(s))

    table = pd.DataFrame(rows)
    table["Status"] = table["Topic"].apply(
        lambda t: "Completed" if t in done else "Pending"
    )
    st.dataframe(table, use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("📆 Generate your study timetable")
    st.write(
        "80% of each day goes to learning topics (highest priority first) "
        "and 20% to revision. Topics you have marked as completed in the "
        "Progress Tracker are skipped."
    )

    if st.button("Generate Smart Study Plan", use_container_width=True):
        st.session_state.study_plans[s["name"]] = build_timetable(
            s, rows, done, days_to_prepare
        )
        st.success("Your personalized study plan is ready!")

    plan = st.session_state.study_plans.get(s["name"])

    if plan:
        st.subheader("📚 Your timetable")
        plan_df = pd.DataFrame(plan)
        st.dataframe(plan_df, use_container_width=True, hide_index=True)

        st.download_button(
            "⬇️ Download timetable (CSV)",
            plan_df.to_csv(index=False).encode("utf-8"),
            file_name=f"{s['name']}_study_plan.csv",
            mime="text/csv"
        )

        st.info(
            "Tip: after studying topics, mark them complete in the Progress "
            "Tracker and generate the plan again to adjust the schedule."
        )


# =====================================================================
# PAGE: LEARNING TUTOR
# =====================================================================

def page_tutor(s):
    section_title("MODULE 04", "AI Tutor", "Ask anything about your syllabus")
    st.write(
        "Ask questions and receive simple explanations, "
        "examples and exam-focused guidance."
    )

    topic_names = [t["name"] for t in s["topics"]]
    selected_topic = st.selectbox("Choose a topic to learn", topic_names)

    selected_unit = next(
        (t["unit"] for t in s["topics"] if t["name"] == selected_topic), ""
    )

    coach_say(
        f"Ask me anything about <b>{esc(selected_topic)}</b>. I'll explain it "
        f"in simple words, with examples and exam points."
    )

    st.divider()
    st.subheader("💬 Ask the AI Tutor")

    messages = st.session_state.tutor_chats.setdefault(s["name"], [])

    for message in messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    question = st.chat_input("Ask a question about your selected topic...")

    if question:
        messages.append({"role": "user", "content": question})

        with st.chat_message("user"):
            st.markdown(question)

        if not os.getenv("GEMINI_API_KEY"):
            st.warning("Gemini API key not detected. Please check your API setup.")
        else:
            with st.chat_message("assistant"):
                with st.spinner("Your tutor is preparing an explanation..."):
                    try:
                        syllabus_topics = ", ".join(t["name"] for t in s["topics"][:60])

                        instructions = (
                            f"You are a friendly and patient tutor for the subject "
                            f"'{s['name']}'.\n"
                            f"The student has an exam on {s['exam_date']:%d %B %Y} "
                            f"worth {s['total_marks']} marks.\n"
                            f"Selected topic: {selected_topic} (from {selected_unit}).\n"
                            f"Syllabus topics: {syllabus_topics}.\n"
                            f"Question paper pattern: {pattern_text(s)}\n\n"
                            "Explain concepts in simple beginner-friendly language. "
                            "Use headings and examples. Include code only when the "
                            "subject is about programming. Include important "
                            "definitions and exam points. Do not invent official "
                            "exam facts that were not given above."
                        )

                        history = [
                            {
                                "role": "user" if m["role"] == "user" else "model",
                                "parts": [{"text": m["content"]}],
                            }
                            for m in messages
                        ]

                        answer = call_gemini(history, system_instruction=instructions)

                        if not answer:
                            answer = "I couldn't generate an answer. Please try again."

                        st.markdown(answer)
                        messages.append({"role": "assistant", "content": answer})

                    except Exception as error:
                        st.error(
                            "The AI tutor could not respond. "
                            "Please check your API setup and try again."
                        )
                        st.caption(f"Technical details: {error}")

    if st.button("Clear Chat"):
        st.session_state.tutor_chats[s["name"]] = []
        st.rerun()


# =====================================================================
# PAGE: PRACTICE CENTRE
# =====================================================================

def page_practice(s):
    section_title("MODULE 05", "Practice Arena", "Test yourself and find weak topics")
    st.write(
        "Test your knowledge with multiple-choice questions "
        "and discover which topics need more revision."
    )

    topic_names = [t["name"] for t in s["topics"]]
    selected_topic = st.selectbox("Choose a topic for practice", topic_names)
    topic_index = topic_names.index(selected_topic)

    store_key = f"{s['name']}||{selected_topic}"
    ai_questions = st.session_state.ai_questions.get(store_key)

    lesson_key = find_lesson_key(s, selected_topic)
    built_in = practice_questions.get(lesson_key) if lesson_key else None

    questions = ai_questions or built_in

    button_label = (
        "🔄 Generate fresh AI questions" if questions
        else "🤖 Generate practice questions with AI"
    )

    if st.button(button_label):
        if not os.getenv("GEMINI_API_KEY"):
            st.warning("Gemini API key not detected. Please check your API setup.")
        else:
            with st.spinner("Creating questions..."):
                try:
                    new_questions = generate_ai_questions(s, selected_topic)

                    if new_questions:
                        st.session_state.ai_questions[store_key] = new_questions
                        st.session_state.practice_feedback = None
                        st.session_state.practice_attempt_id += 1
                        st.rerun()
                    else:
                        st.error("The AI did not return usable questions. Try again.")
                except Exception as error:
                    st.error("Could not create questions right now.")
                    st.caption(f"Technical details: {error}")

    if not questions:
        st.info("No practice questions yet. Use the button above to create some.")
    else:
        st.divider()
        st.subheader(f"📚 {selected_topic} Quiz")
        st.write(f"Total Questions: {len(questions)}")

        attempt = st.session_state.practice_attempt_id
        answers = {}

        with st.form(key=f"practice_form_{attempt}_{topic_index}"):
            for i, question in enumerate(questions):
                st.markdown(f"**Question {i + 1}: {question['question']}**")

                answers[i] = st.radio(
                    "Choose your answer:",
                    question["options"],
                    index=None,
                    key=f"answer_{attempt}_{s['name']}_{topic_index}_{i}"
                )
                st.write("")

            submitted = st.form_submit_button("Submit Quiz", use_container_width=True)

        if submitted:
            if any(a is None for a in answers.values()):
                st.warning("Please answer all questions before submitting.")
            else:
                score = 0
                feedback = []

                for i, question in enumerate(questions):
                    is_correct = answers[i] == question["answer"]
                    score += 1 if is_correct else 0

                    feedback.append({
                        "question": question["question"],
                        "selected": answers[i],
                        "correct": question["answer"],
                        "is_correct": is_correct,
                        "explanation": question["explanation"],
                    })

                percentage = score / len(questions) * 100

                st.session_state.practice_results.append({
                    "subject": s["name"],
                    "topic": selected_topic,
                    "score": score,
                    "total": len(questions),
                    "percentage": percentage,
                })

                st.session_state.practice_feedback = {
                    "subject": s["name"],
                    "topic": selected_topic,
                    "score": score,
                    "total": len(questions),
                    "percentage": percentage,
                    "feedback": feedback,
                }

                weak_key = f"{s['name']} › {selected_topic}"

                if score < len(questions):
                    if weak_key not in st.session_state.weak_topics:
                        st.session_state.weak_topics.append(weak_key)
                elif weak_key in st.session_state.weak_topics:
                    st.session_state.weak_topics.remove(weak_key)

                st.session_state.practice_attempt_id += 1
                st.rerun()

        result = st.session_state.practice_feedback

        if (
            result
            and result["subject"] == s["name"]
            and result["topic"] == selected_topic
        ):
            st.divider()
            st.subheader("📊 Your Quiz Result")

            st.metric("Your Score", f"{result['score']} / {result['total']}")
            st.progress(result["percentage"] / 100)
            st.write(f"Percentage: {result['percentage']:.1f}%")

            if result["percentage"] == 100:
                st.success("Excellent! All answers are correct!")
            elif result["percentage"] >= 60:
                st.success("Good work! Keep practising.")
            else:
                st.warning("Keep learning and revise this topic again.")

            st.subheader("📖 Answer Review")

            for i, item in enumerate(result["feedback"]):
                status = "Correct" if item["is_correct"] else "Incorrect"

                with st.expander(f"Question {i + 1} - {status}"):
                    st.write(item["question"])
                    st.write(f"Your answer: {item['selected']}")
                    st.write(f"Correct answer: {item['correct']}")
                    if item["explanation"]:
                        st.info(item["explanation"])

            if st.button("Try Again"):
                st.session_state.practice_feedback = None
                st.rerun()

    own_results = [
        r for r in st.session_state.practice_results if r["subject"] == s["name"]
    ]

    if own_results:
        st.divider()
        st.subheader("📈 Previous Quiz Attempts")

        for r in reversed(own_results[-5:]):
            st.write(
                f"**{r['topic']}** — {r['score']}/{r['total']} "
                f"({r['percentage']:.1f}%)"
            )

    prefix = f"{s['name']} › "
    weak = [w[len(prefix):] for w in st.session_state.weak_topics if w.startswith(prefix)]

    if weak:
        st.divider()
        st.subheader("🎯 Topics to Revise")
        for topic in weak:
            st.write(f"📌 {topic}")
    elif own_results:
        st.success("You have no weak topics from your latest quiz attempts!")


# =====================================================================
# PAGE: PROGRESS TRACKER
# =====================================================================

def page_progress(s):
    section_title("MODULE 06", "Progress Radar", "Track study completion and quiz performance")
    st.write(
        "Monitor your study progress, quiz performance "
        "and topics that need more attention."
    )

    subjects = st.session_state.subjects

    if len(subjects) > 1:
        st.subheader("All subjects at a glance")
        st.dataframe(
            pd.DataFrame([
                {
                    "Subject": x["name"],
                    "Exam date": x["exam_date"],
                    "Topics completed": f"{len(completed_for(x))}/{len(x['topics'])}",
                    "Completion (%)": round(completion_percent(x), 1),
                }
                for x in subjects
            ]),
            use_container_width=True,
            hide_index=True
        )
        st.divider()

    st.subheader(f"📚 Study Completion — {s['name']}")
    st.write("Select the topics you have completed:")

    all_topics = [t["name"] for t in s["topics"]]

    completed = st.multiselect(
        "Completed Topics",
        all_topics,
        default=completed_for(s),
        key=f"completed_selector_{s['name']}_{s['version']}"
    )

    st.session_state.completed[s["name"]] = completed

    total_topics = len(all_topics)
    completed_count = len(completed)
    study_percentage = completed_count / total_topics * 100 if total_topics else 0

    st.progress(study_percentage / 100)
    st.write(f"Completed: {completed_count} / {total_topics} topics")
    st.metric("Study Completion", f"{study_percentage:.1f}%")

    st.divider()
    st.subheader("📝 Quiz Performance")

    results = [r for r in st.session_state.practice_results if r["subject"] == s["name"]]
    average_score = 0

    if results:
        total_attempts = len(results)
        total_score = sum(r["score"] for r in results)
        total_questions = sum(r["total"] for r in results)
        average_score = total_score / total_questions * 100 if total_questions else 0

        c1, c2, c3 = st.columns(3)
        c1.metric("Quiz Attempts", total_attempts)
        c2.metric("Average Performance", f"{average_score:.1f}%")
        c3.metric("Questions Attempted", total_questions)

        st.subheader("📈 Quiz Performance Chart")

        chart_data = pd.DataFrame({
            "Attempt": [f"Quiz {i + 1}" for i in range(total_attempts)],
            "Percentage": [r["percentage"] for r in results],
        })
        st.line_chart(chart_data.set_index("Attempt"))

        st.subheader("📋 Quiz History")

        for i, r in enumerate(results):
            st.write(
                f"**{i + 1}. {r['topic']}** — {r['score']}/{r['total']} "
                f"({r['percentage']:.1f}%)"
            )
    else:
        st.info(
            "No quiz attempts yet. Complete a quiz in the Practice Centre "
            "to see your performance."
        )

    st.divider()
    st.subheader("🎯 Topics Needing More Practice")

    prefix = f"{s['name']} › "
    weak = [w[len(prefix):] for w in st.session_state.weak_topics if w.startswith(prefix)]

    if weak:
        for topic in weak:
            st.warning(f"📌 {topic}")
        st.write(
            "Suggestion: Revisit these topics in the Learning Tutor and "
            "attempt their quizzes again."
        )
    else:
        st.success(
            "No weak topics identified yet. "
            "Complete some quizzes to evaluate your preparation."
        )

    st.divider()
    st.subheader("📑 Preparation Summary")

    student = st.session_state.student_name.strip() or "Not entered"
    st.write(f"**Student:** {student}")
    st.write(f"**Subject:** {s['name']}")
    st.write(f"**Exam Date:** {s['exam_date']}")
    st.write(f"**Topics Completed:** {completed_count}/{total_topics}")
    st.write(f"**Study Completion:** {study_percentage:.1f}%")

    if results:
        st.write(f"**Average Quiz Performance:** {average_score:.1f}%")

    if study_percentage == 100 and results:
        st.success(
            "Excellent! You have completed all topics "
            "and attempted practice quizzes."
        )
    elif study_percentage >= 50:
        st.info(
            "Good progress! Continue completing the remaining topics "
            "and practising regularly."
        )
    else:
        st.info(
            "Keep going! Start completing topics and take practice "
            "quizzes to track your progress."
        )


# =====================================================================
# VISUAL DESIGN - sci-fi "mission control" theme (CSS, animations, HTML)
# =====================================================================

APP_VERSION = "3.0"

RANKS = ["Cadet", "Pilot", "Navigator", "Commander", "Captain", "Admiral"]
XP_PER_LEVEL = 300

CSS_TEMPLATE = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Orbitron:wght@500;700;900&family=Exo+2:wght@400;500;600;700&display=swap');

@property --p { syntax: '<number>'; inherits: false; initial-value: 0; }

:root {
  --cyan: #00e5ff; --violet: #7c4dff; --magenta: #ff2bd6; --green: #39ff88;
  --amber: #ffb020; --red: #ff4d6d; --ink: #e6f1ff; --muted: #8aa0c8;
  --panel: rgba(14, 21, 48, .74); --line: rgba(0, 229, 255, .28);
}

html, body, .stApp, [class*="css"] { font-family: 'Exo 2', 'Segoe UI', sans-serif; }
.stApp {
  color: var(--ink);
  background:
    radial-gradient(1100px 560px at 82% -8%, rgba(124, 77, 255, .38), transparent 60%),
    radial-gradient(900px 520px at -8% 18%, rgba(0, 229, 255, .20), transparent 60%),
    radial-gradient(800px 500px at 60% 110%, rgba(255, 43, 214, .14), transparent 60%),
    #060a18;
}

/* ---------- Moving grid and twinkling stars ---------- */
.stApp::before {
  content: ""; position: fixed; inset: 0; pointer-events: none; z-index: 0;
  background-image:
    linear-gradient(rgba(0, 229, 255, .06) 1px, transparent 1px),
    linear-gradient(90deg, rgba(0, 229, 255, .06) 1px, transparent 1px);
  background-size: 52px 52px; animation: gridMove 24s linear infinite;
  -webkit-mask-image: radial-gradient(ellipse at 50% 40%, #000 25%, transparent 78%);
  mask-image: radial-gradient(ellipse at 50% 40%, #000 25%, transparent 78%);
}
.stApp::after {
  content: ""; position: fixed; left: 0; top: 0; width: 2px; height: 2px; border-radius: 50%;
  pointer-events: none; z-index: 0; box-shadow: __STARS__; animation: twinkle 5s ease-in-out infinite;
}
.block-container { position: relative; z-index: 1; padding-top: 1.2rem; max-width: 1200px; animation: fadeUp .6s ease both; }
header[data-testid="stHeader"] { background: transparent; }

@keyframes gridMove { from { background-position: 0 0; } to { background-position: 0 52px; } }
@keyframes twinkle { 0%, 100% { opacity: .35; } 50% { opacity: 1; } }
@keyframes fadeUp { from { opacity: 0; transform: translateY(16px); } to { opacity: 1; transform: none; } }
@keyframes slideIn { from { opacity: 0; transform: translateX(-26px); } to { opacity: 1; transform: none; } }
@keyframes popIn { 0% { opacity: 0; transform: scale(.88); } 60% { transform: scale(1.03); } 100% { opacity: 1; transform: scale(1); } }
@keyframes scan { 0% { top: -4px; opacity: 0; } 10% { opacity: 1; } 90% { opacity: 1; } 100% { top: 100%; opacity: 0; } }
@keyframes floaty { 0%, 100% { transform: translateY(0) rotate(-5deg); } 50% { transform: translateY(-12px) rotate(5deg); } }
@keyframes glowPulse { 0%, 100% { box-shadow: 0 0 10px rgba(0,229,255,.35); } 50% { box-shadow: 0 0 26px rgba(124,77,255,.75); } }
@keyframes ringPulse { 0% { box-shadow: 0 0 0 0 rgba(0,229,255,.55); } 70% { box-shadow: 0 0 0 14px rgba(0,229,255,0); } 100% { box-shadow: 0 0 0 0 rgba(0,229,255,0); } }
@keyframes blink { 0%, 80%, 100% { opacity: .2; } 40% { opacity: 1; } }
@keyframes shimmer { 0% { background-position: -200% 0; } 100% { background-position: 200% 0; } }
@keyframes typing { from { width: 0; } to { width: 36ch; } }
@keyframes caret { 50% { border-color: transparent; } }
@keyframes glitch {
  0%, 91%, 100% { transform: none; text-shadow: 0 0 18px rgba(0,229,255,.7); }
  92% { transform: translateX(-4px) skewX(-8deg); text-shadow: 4px 0 var(--magenta), -4px 0 var(--cyan); }
  95% { transform: translateX(4px) skewX(6deg); text-shadow: -4px 0 var(--magenta), 4px 0 var(--cyan); }
  97% { transform: translateX(-2px); text-shadow: 2px 0 var(--magenta), -2px 0 var(--cyan); }
}
@keyframes ringFill { from { --p: 0; } }
@keyframes spin { to { transform: rotate(360deg); } }
@keyframes sweep { from { transform: translateX(-120%); } to { transform: translateX(220%); } }

/* ---------- Headings ---------- */
h1, h2, h3, h4 { font-family: 'Orbitron', 'Exo 2', sans-serif !important; letter-spacing: .04em; color: var(--ink) !important; font-weight: 700 !important; }
h2 { font-size: 1.45rem !important; text-shadow: 0 0 14px rgba(0,229,255,.45); }
h3 { font-size: 1.1rem !important; text-shadow: 0 0 10px rgba(0,229,255,.35); }

/* ---------- Hero ---------- */
.hero {
  position: relative; overflow: hidden; padding: 26px 32px; margin-bottom: 18px; border-radius: 18px;
  background: linear-gradient(135deg, rgba(0,229,255,.14), rgba(124,77,255,.22) 55%, rgba(255,43,214,.14));
  border: 1px solid rgba(0, 229, 255, .55); backdrop-filter: blur(8px);
  box-shadow: 0 0 34px rgba(0,229,255,.22), inset 0 0 44px rgba(124,77,255,.18);
  animation: fadeUp .7s ease both;
}
.hero .scan { position: absolute; left: 0; right: 0; top: 0; height: 2px; background: linear-gradient(90deg, transparent, var(--cyan), transparent); box-shadow: 0 0 12px var(--cyan); animation: scan 4.5s linear infinite; }
.hero .hero-title { font-family: 'Orbitron', sans-serif; font-weight: 900; font-size: 2.1rem; letter-spacing: .06em; text-transform: uppercase; color: #fff; animation: glitch 6s infinite; }
.hero p { margin: 6px 0 0 0; color: #b8c9ee; font-size: 1.02rem; }
.hero .badge { display: inline-block; padding: 3px 12px; margin-bottom: 8px; border-radius: 4px; font-family: 'Orbitron', sans-serif; font-size: .66rem; font-weight: 700; letter-spacing: .14em; color: var(--cyan); border: 1px solid var(--cyan); background: rgba(0,229,255,.1); }
.hero .float { position: absolute; right: 34px; top: 16px; font-size: 3.6rem; animation: floaty 4s ease-in-out infinite; filter: drop-shadow(0 0 14px rgba(0,229,255,.8)); }
.hero .boot { margin-top: 12px; display: block; width: 0; overflow: hidden; white-space: nowrap; font-family: 'Courier New', monospace; font-size: .82rem; color: var(--green);
              border-right: 2px solid var(--green); animation: typing 3s steps(36) .6s forwards, caret .8s step-end infinite; }

/* ---------- Section header ---------- */
.sect { margin: 4px 0 16px 0; padding-left: 16px; border-left: 3px solid var(--cyan); box-shadow: -8px 0 18px -10px var(--cyan); animation: slideIn .5s ease both; }
.sect .tag { font-family: 'Courier New', monospace; font-size: .75rem; color: var(--green); letter-spacing: .12em; }
.sect .name { font-family: 'Orbitron', sans-serif; font-weight: 900; font-size: 1.6rem; letter-spacing: .08em; text-transform: uppercase; color: #fff; text-shadow: 0 0 16px rgba(0,229,255,.6); }
.sect .sub { color: var(--muted); font-size: .92rem; }

/* ---------- Stat tiles ---------- */
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 14px; margin: 8px 0 16px 0; }
.tile { position: relative; padding: 16px 18px; background: var(--panel); border: 1px solid var(--line); backdrop-filter: blur(6px);
        clip-path: polygon(0 0, calc(100% - 18px) 0, 100% 18px, 100% 100%, 0 100%); animation: popIn .6s ease both; transition: border-color .25s, box-shadow .25s, transform .25s; }
.tile::before { content: ""; position: absolute; left: 0; top: 0; width: 38px; height: 2px; background: var(--cyan); box-shadow: 0 0 8px var(--cyan); }
.tile:hover { border-color: var(--cyan); box-shadow: inset 0 0 28px rgba(0,229,255,.2); transform: translateY(-4px); }
.tile.pulse { animation: popIn .6s ease both, glowPulse 2.6s infinite 1.2s; }
.tile .icon { font-size: 1.35rem; }
.tile .value { font-family: 'Orbitron', sans-serif; font-size: 1.7rem; font-weight: 700; color: var(--cyan); text-shadow: 0 0 14px rgba(0,229,255,.7); line-height: 1.25; }
.tile .label { font-size: .7rem; color: var(--muted); text-transform: uppercase; letter-spacing: .12em; font-weight: 600; }

/* ---------- Countdown ring ---------- */
.ringbox { position: relative; width: 190px; height: 190px; margin: 4px auto 10px auto; }
.ringbox::before { content: ""; position: absolute; inset: 0; border-radius: 50%; border: 2px dashed rgba(0,229,255,.45); animation: spin 22s linear infinite; }
.ring { position: absolute; inset: 14px; border-radius: 50%; animation: ringFill 1.8s ease-out both;
        background: conic-gradient(var(--cyan) calc(var(--p) * 1%), rgba(0,229,255,.13) 0);
        -webkit-mask: radial-gradient(farthest-side, transparent calc(100% - 13px), #000 calc(100% - 12px));
        mask: radial-gradient(farthest-side, transparent calc(100% - 13px), #000 calc(100% - 12px));
        filter: drop-shadow(0 0 8px rgba(0,229,255,.8)); }
.ringtext { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; text-align: center; }
.ringtext .small { font-family: 'Courier New', monospace; font-size: .7rem; letter-spacing: .2em; color: var(--green); }
.ringtext .big { font-family: 'Orbitron', sans-serif; font-size: 2.6rem; font-weight: 900; color: #fff; text-shadow: 0 0 18px var(--cyan); line-height: 1.1; }
.ringtext .unit { font-size: .72rem; letter-spacing: .25em; color: var(--muted); }

/* ---------- Coach (AI co-pilot) ---------- */
.coach { display: flex; gap: 14px; align-items: flex-start; margin: 8px 0 14px 0; }
.coach .avatar { width: 50px; height: 50px; flex-shrink: 0; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 1.5rem;
                 background: radial-gradient(circle at 30% 30%, rgba(0,229,255,.5), rgba(124,77,255,.35)); border: 1px solid var(--cyan); animation: ringPulse 2.6s infinite; }
.coach .bubble { position: relative; padding: 12px 18px; line-height: 1.55; background: var(--panel); border: 1px solid var(--line); border-radius: 4px 18px 18px 18px;
                 box-shadow: 0 0 22px rgba(0,229,255,.12); backdrop-filter: blur(6px); animation: popIn .5s ease both; }
.coach .bubble .who { display: block; font-family: 'Orbitron', sans-serif; font-size: .62rem; letter-spacing: .2em; color: var(--cyan); margin-bottom: 3px; }
.typing span { display: inline-block; width: 6px; height: 6px; margin-right: 3px; border-radius: 50%; background: var(--cyan); animation: blink 1.3s infinite; }
.typing span:nth-child(2) { animation-delay: .2s; }
.typing span:nth-child(3) { animation-delay: .4s; }

/* ---------- Mission cards ---------- */
.mission { position: relative; overflow: hidden; display: flex; gap: 16px; align-items: center; margin: 10px 0; padding: 14px 18px;
           background: var(--panel); border: 1px solid var(--line); border-left: 4px solid var(--cyan); animation: slideIn .55s ease both; transition: transform .25s, box-shadow .25s, border-color .25s;
           clip-path: polygon(0 0, 100% 0, 100% calc(100% - 14px), calc(100% - 14px) 100%, 0 100%); }
.mission::after { content: ""; position: absolute; top: 0; bottom: 0; width: 40%; left: 0; background: linear-gradient(100deg, transparent, rgba(0,229,255,.16), transparent); transform: translateX(-120%); }
.mission:hover { transform: translateX(8px); border-color: var(--cyan); box-shadow: inset 0 0 30px rgba(0,229,255,.16); }
.mission:hover::after { animation: sweep 1s ease; }
.mission.rev { border-left-color: var(--magenta); }
.mission .num { width: 44px; height: 44px; flex-shrink: 0; display: flex; align-items: center; justify-content: center; font-family: 'Orbitron', sans-serif; font-weight: 900; color: var(--cyan);
                border: 1px solid var(--cyan); border-radius: 50%; text-shadow: 0 0 10px var(--cyan); box-shadow: inset 0 0 12px rgba(0,229,255,.3); }
.mission.rev .num { color: var(--magenta); border-color: var(--magenta); text-shadow: 0 0 10px var(--magenta); box-shadow: inset 0 0 12px rgba(255,43,214,.3); }
.mission .tag { font-family: 'Courier New', monospace; font-size: .68rem; letter-spacing: .18em; color: var(--green); }
.mission .t { font-weight: 700; font-size: 1.05rem; color: #fff; }
.mission .s { color: var(--muted); font-size: .85rem; }
.mission .hrs { margin-left: auto; white-space: nowrap; padding: 5px 14px; font-family: 'Orbitron', sans-serif; font-size: .8rem; font-weight: 700; color: #fff;
                border-radius: 4px; background: linear-gradient(135deg, rgba(124,77,255,.55), rgba(255,43,214,.4)); border: 1px solid rgba(255,255,255,.2); }

/* ---------- Lesson step bars ---------- */
.steps { display: flex; gap: 8px; margin: 6px 0 14px 0; }
.steps .dot { height: 6px; flex: 1; border-radius: 2px; background: rgba(0,229,255,.14); transition: background .4s; }
.steps .dot.done { background: linear-gradient(90deg, var(--cyan), var(--violet)); box-shadow: 0 0 8px rgba(0,229,255,.6); }
.steps .dot.now { background: var(--cyan); animation: glowPulse 1.6s infinite; }

/* ---------- Sidebar ---------- */
[data-testid="stSidebar"] { background: linear-gradient(180deg, #0a1028 0%, #060a18 100%); border-right: 1px solid var(--line); box-shadow: 0 0 30px rgba(0,229,255,.08); }
[data-testid="stSidebar"] label { border-radius: 8px; transition: background .2s, transform .2s, box-shadow .2s; }
[data-testid="stSidebar"] label:hover { background: rgba(0,229,255,.09); transform: translateX(4px); }
[data-testid="stSidebar"] label:has(input:checked) { background: rgba(0,229,255,.13); box-shadow: inset 3px 0 0 var(--cyan); }
.brand { font-family: 'Orbitron', sans-serif; font-weight: 900; font-size: 1.05rem; letter-spacing: .08em; color: var(--cyan); text-shadow: 0 0 14px rgba(0,229,255,.8); }
.brand-sub { font-family: 'Courier New', monospace; font-size: .7rem; letter-spacing: .1em; color: var(--green); margin-bottom: 12px; }
.xp { margin: 4px 0 14px 0; padding: 10px 12px; border: 1px solid var(--line); background: rgba(0,229,255,.05); border-radius: 8px; }
.xp .top { display: flex; justify-content: space-between; font-family: 'Orbitron', sans-serif; font-size: .64rem; letter-spacing: .12em; color: #fff; }
.xp .bar { height: 7px; margin: 7px 0 4px 0; border-radius: 4px; background: rgba(0,229,255,.14); overflow: hidden; }
.xp .bar div { height: 100%; border-radius: 4px; background: linear-gradient(90deg, var(--cyan), var(--violet), var(--magenta)); box-shadow: 0 0 10px var(--cyan); transition: width 1s ease; }
.xp .sub { font-size: .7rem; color: var(--muted); }

/* ---------- Streamlit widgets ---------- */
.stButton > button, .stDownloadButton > button, [data-testid^="stBaseButton"] {
  position: relative; border-radius: 8px; font-family: 'Orbitron', sans-serif; font-size: .74rem; font-weight: 700; letter-spacing: .08em; text-transform: uppercase;
  color: #c8f7ff; background: rgba(0,229,255,.07); border: 1px solid rgba(0,229,255,.6); transition: transform .2s, box-shadow .2s, background .2s; }
.stButton > button:hover, .stDownloadButton > button:hover, [data-testid^="stBaseButton"]:hover {
  transform: translateY(-2px); color: #fff; background: rgba(0,229,255,.18); border-color: var(--cyan); box-shadow: 0 0 20px rgba(0,229,255,.6), inset 0 0 14px rgba(0,229,255,.25); }
button[kind="primary"], [data-testid="stBaseButton-primary"], [data-testid="stBaseButton-primaryFormSubmit"] {
  background: linear-gradient(135deg, #00b8ff, #6f4dff 60%, #ff2bd6) !important; border: none !important; color: #fff !important; animation: glowPulse 2.8s infinite; }
button[kind="primary"] p, [data-testid="stBaseButton-primary"] p { color: #fff !important; }
[data-testid="stVerticalBlockBorderWrapper"] { border-radius: 14px; border-color: var(--line) !important; background: rgba(14,21,48,.55); animation: fadeUp .5s ease both; transition: box-shadow .25s, border-color .25s; }
[data-testid="stVerticalBlockBorderWrapper"]:hover { border-color: rgba(0,229,255,.55) !important; box-shadow: 0 0 22px rgba(0,229,255,.16); }
[data-testid="stMetric"] { background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 12px 16px; }
[data-testid="stMetricValue"] { font-family: 'Orbitron', sans-serif; color: var(--cyan); text-shadow: 0 0 10px rgba(0,229,255,.6); }
[data-testid="stExpander"] { border: 1px solid var(--line) !important; border-radius: 12px; background: rgba(14,21,48,.55); animation: fadeUp .5s ease both; }
[data-testid="stAlert"] { border-radius: 12px; border: 1px solid var(--line); background: rgba(14,21,48,.78) !important; backdrop-filter: blur(6px); }
[data-baseweb="input"], [data-baseweb="select"] > div, [data-baseweb="textarea"] { border-radius: 8px !important; border-color: rgba(0,229,255,.3) !important; background: rgba(6,10,24,.7) !important; transition: box-shadow .2s, border-color .2s; }
[data-baseweb="input"]:focus-within, [data-baseweb="select"] > div:focus-within { border-color: var(--cyan) !important; box-shadow: 0 0 14px rgba(0,229,255,.45); }
[data-baseweb="tab"] { font-family: 'Orbitron', sans-serif; font-size: .78rem; letter-spacing: .06em; }
[data-testid="stProgress"] [role="progressbar"] > div, .stProgress > div > div > div > div {
  background: linear-gradient(90deg, var(--cyan), var(--violet), var(--magenta), var(--cyan)); background-size: 200% 100%; animation: shimmer 2.4s linear infinite; box-shadow: 0 0 12px rgba(0,229,255,.7); }
[data-testid="stChatMessage"] { border: 1px solid var(--line); border-radius: 14px; background: rgba(14,21,48,.6); animation: fadeUp .4s ease both; }
hr { border-color: var(--line) !important; }
::-webkit-scrollbar { width: 9px; height: 9px; }
::-webkit-scrollbar-thumb { background: rgba(0,229,255,.35); border-radius: 6px; }
::-webkit-scrollbar-track { background: rgba(6,10,24,.8); }
</style>
"""


def _star_shadows(count=30):
    import random

    rnd = random.Random(7)
    return ", ".join(
        f"{rnd.randint(0, 100)}vw {rnd.randint(0, 100)}vh "
        f"rgba(255,255,255,{rnd.choice([0.4, 0.6, 0.9])})"
        for _ in range(count)
    )


# Blank lines are removed so the markdown parser never splits the style block
CUSTOM_CSS = re.sub(r"\n\s*\n", "\n", CSS_TEMPLATE.replace("__STARS__", _star_shadows()))


def esc(value):
    """Escape text coming from PDFs or users before putting it into HTML."""
    return html.escape(str(value))


def inject_css():
    st.markdown(CUSTOM_CSS, unsafe_allow_html=True)


def xp_info():
    """XP = 100 per completed topic + 10 per correct quiz answer."""
    completed_total = sum(len(v) for v in st.session_state.completed.values())
    correct_total = sum(r["score"] for r in st.session_state.practice_results)

    xp = completed_total * 100 + correct_total * 10
    level = xp // XP_PER_LEVEL + 1
    rank = RANKS[min(level - 1, len(RANKS) - 1)]
    into_level = xp % XP_PER_LEVEL

    return {
        "xp": xp,
        "level": level,
        "rank": rank,
        "into": into_level,
        "to_next": XP_PER_LEVEL - into_level,
    }


def sidebar_status():
    info = xp_info()
    percent = info["into"] / XP_PER_LEVEL * 100

    st.sidebar.markdown(
        '<div class="brand">⟡ SMART EXAM TUTOR</div>'
        f'<div class="brand-sub">v{APP_VERSION} · NEON EDITION</div>'
        '<div class="xp">'
        f'<div class="top"><span>RANK: {esc(info["rank"]).upper()}</span><span>LV {info["level"]}</span></div>'
        f'<div class="bar"><div style="width:{percent:.0f}%"></div></div>'
        f'<div class="sub">{info["xp"]} XP · {info["to_next"]} to next level</div>'
        '</div>',
        unsafe_allow_html=True
    )


def hero(title, subtitle, icon="🛰️", badge=None):
    badge_text = badge or f"v{APP_VERSION}"
    st.markdown(
        '<div class="hero"><div class="scan"></div>'
        f'<div class="float">{icon}</div>'
        f'<span class="badge">{esc(badge_text)}</span>'
        f'<div class="hero-title">{esc(title)}</div>'
        f'<p>{esc(subtitle)}</p>'
        '<span class="boot">▌ SYSTEMS ONLINE · AI CO-PILOT READY</span>'
        '</div>',
        unsafe_allow_html=True
    )


def section_title(tag, name, subtitle):
    st.markdown(
        '<div class="sect">'
        f'<div class="tag">// {esc(tag)}</div>'
        f'<div class="name">{esc(name)}</div>'
        f'<div class="sub">{esc(subtitle)}</div>'
        '</div>',
        unsafe_allow_html=True
    )


def countdown_ring(days):
    """Animated T-minus ring. days=None shows dashes."""
    if days is None:
        percent, text = 0, "--"
    else:
        percent = round(min(max(days, 0), 30) / 30 * 100)
        text = str(max(days, 0))

    return (
        '<div class="ringbox">'
        f'<div class="ring" style="--p:{percent}"></div>'
        '<div class="ringtext">'
        '<div class="small">T-MINUS</div>'
        f'<div class="big">{esc(text)}</div>'
        '<div class="unit">DAYS</div>'
        '</div></div>'
    )


def stat_tiles(items):
    """items: list of (icon, label, value, pulse)."""
    cells = "".join(
        f'<div class="tile{" pulse" if pulse else ""}" '
        f'style="animation-delay:{index * 0.08:.2f}s">'
        f'<div class="icon">{icon}</div>'
        f'<div class="value">{esc(value)}</div>'
        f'<div class="label">{esc(label)}</div></div>'
        for index, (icon, label, value, pulse) in enumerate(items)
    )
    st.markdown(f'<div class="tiles">{cells}</div>', unsafe_allow_html=True)


def coach_say(message_html, typing=False):
    """AI co-pilot speech bubble. Callers must escape variable text with esc()."""
    dots = (
        ' <span class="typing"><span></span><span></span><span></span></span>'
        if typing else ""
    )
    st.markdown(
        '<div class="coach"><div class="avatar">🤖</div>'
        f'<div class="bubble"><span class="who">AI CO-PILOT</span>{message_html}{dots}</div></div>',
        unsafe_allow_html=True
    )


def step_dots(current, total):
    dots = "".join(
        f'<div class="dot {"done" if i < current else "now" if i == current else ""}"></div>'
        for i in range(total)
    )
    st.markdown(f'<div class="steps">{dots}</div>', unsafe_allow_html=True)


def mission_card(number, title, subtitle, hours, revision=False):
    tag = "REVISION" if revision else f"MISSION {number:02d}"
    st.markdown(
        f'<div class="mission{" rev" if revision else ""}" '
        f'style="animation-delay:{(number - 1) * 0.12:.2f}s">'
        f'<div class="num">{number:02d}</div>'
        f'<div><div class="tag">{tag}</div>'
        f'<div class="t">{esc(title)}</div><div class="s">{esc(subtitle)}</div></div>'
        f'<div class="hrs">{hours:g} H</div></div>',
        unsafe_allow_html=True
    )


# =====================================================================
# STUDY COACH - lesson and quiz helpers
# =====================================================================

def builtin_lesson_steps(key):
    """Turn a built-in Python lesson into coach steps (works without the API)."""
    lesson = lessons[key]

    return [
        {
            "title": "The big idea",
            "content": f"{lesson['explanation']}\n\n**Definition:** {lesson['definition']}",
            "check": None,
        },
        {
            "title": "A real-life example",
            "content": lesson["real_life"],
            "check": None,
        },
        {
            "title": "See it in code",
            "content": (
                f"```python\n{lesson['example']}\n```\n\n"
                f"**Output:**\n```\n{lesson['output']}\n```"
            ),
            "check": None,
        },
        {
            "title": "Exam points",
            "content": (
                "\n".join(f"- {point}" for point in lesson["exam_points"])
                + f"\n\n**Quick revision:** {lesson['revision']}"
            ),
            "check": None,
        },
    ]


def generate_lesson_steps(subject, topic, unit):
    prompt = f"""
You are a patient exam coach. Teach the topic below to a student in 4 short steps.

Subject: {subject['name']}
Unit: {unit}
Topic: {topic}
Question paper pattern: {pattern_text(subject)}

Steps:
1. The big idea in simple words, with a real-life analogy.
2. How it works, with a worked example (include code only if the subject is programming).
3. Exam points: definitions, formulas or typical mistakes.
4. A quick recap in 4 or 5 bullet points.

Every step must contain:
- "title": a short title
- "content": markdown text of 70 to 140 words
- "check": one multiple-choice question with the keys
  "question", "options" (exactly 4 strings), "answer" (must be exactly one of the options), "explanation"

Return ONLY JSON in this form: {{"steps": [ ... ]}}
"""

    data = json.loads(call_gemini(prompt, json_mode=True))
    raw_steps = data.get("steps") if isinstance(data, dict) else data

    steps = []

    for item in raw_steps or []:
        if not isinstance(item, dict) or not item.get("content"):
            continue

        check = item.get("check")

        if (
            isinstance(check, dict)
            and check.get("question")
            and isinstance(check.get("options"), list)
            and len(check["options"]) >= 2
            and check.get("answer") in check["options"]
        ):
            check = {
                "question": str(check["question"]),
                "options": [str(o) for o in check["options"]],
                "answer": str(check["answer"]),
                "explanation": str(check.get("explanation", "")),
            }
        else:
            check = None

        steps.append({
            "title": str(item.get("title") or f"Step {len(steps) + 1}"),
            "content": str(item["content"]),
            "check": check,
        })

    return steps


def get_lesson_steps(subject, topic, unit):
    """Returns (steps, notice). Uses Gemini when possible, else the built-in lesson."""
    notice = ""

    if os.getenv("GEMINI_API_KEY"):
        try:
            steps = generate_lesson_steps(subject, topic, unit)
            if steps:
                return steps, ""
            notice = "The AI lesson came back empty, so a built-in lesson is shown."
        except Exception as error:
            notice = f"The AI lesson could not be created ({error})."
    else:
        notice = "Gemini key not found, so only built-in lessons are available."

    key = find_lesson_key(subject, topic)

    if key:
        return builtin_lesson_steps(key), notice

    return [], notice


def get_quiz_questions(subject, topic):
    """Returns (questions, notice) for the end-of-lesson quiz."""
    store_key = f"{subject['name']}||{topic}"
    cached = st.session_state.ai_questions.get(store_key)

    if cached:
        return cached, ""

    notice = ""

    if os.getenv("GEMINI_API_KEY"):
        try:
            questions = generate_ai_questions(subject, topic, 5)
            if questions:
                st.session_state.ai_questions[store_key] = questions
                return questions, ""
        except Exception as error:
            notice = f"AI questions could not be created ({error})."

    key = find_lesson_key(subject, topic)

    if key:
        return practice_questions[key], notice

    return [], notice


def mark_complete(subject, topic):
    done = st.session_state.completed.setdefault(subject["name"], [])

    if topic not in done:
        done.append(topic)

    # Make the Progress Tracker's selector pick up the change
    st.session_state.pop(
        f"completed_selector_{subject['name']}_{subject['version']}", None
    )


def record_quiz(subject, topic, questions, answers, update_weak=True):
    score = 0
    feedback = []

    for i, question in enumerate(questions):
        is_correct = answers[i] == question["answer"]
        score += 1 if is_correct else 0

        feedback.append({
            "question": question["question"],
            "selected": answers[i],
            "correct": question["answer"],
            "is_correct": is_correct,
            "explanation": question["explanation"],
        })

    percentage = score / len(questions) * 100

    st.session_state.practice_results.append({
        "subject": subject["name"],
        "topic": topic,
        "score": score,
        "total": len(questions),
        "percentage": percentage,
    })

    if update_weak:
        weak_key = f"{subject['name']} › {topic}"

        if score < len(questions):
            if weak_key not in st.session_state.weak_topics:
                st.session_state.weak_topics.append(weak_key)
        elif weak_key in st.session_state.weak_topics:
            st.session_state.weak_topics.remove(weak_key)

    return {
        "topic": topic,
        "score": score,
        "total": len(questions),
        "percentage": percentage,
        "feedback": feedback,
        "celebrated": False,
    }


def weak_topic_names(subject):
    prefix = f"{subject['name']} › "
    return [
        w[len(prefix):] for w in st.session_state.weak_topics
        if w.startswith(prefix)
    ]


# =====================================================================
# STUDY COACH - screens
# =====================================================================

def start_learning(subject, cs, topic):
    unit = next(
        (t["unit"] for t in subject["topics"] if t["name"] == topic), ""
    )

    with st.spinner("Your coach is preparing the lesson..."):
        steps, notice = get_lesson_steps(subject, topic, unit)

    if not steps:
        st.error(
            "I couldn't prepare a lesson for this topic. "
            f"{notice} Check your Gemini key and try again."
        )
        return

    cs.update({
        "phase": "learn",
        "topic": topic,
        "steps": steps,
        "step": 0,
        "checks": {},
        "notice": notice,
        "mock": False,
        "attempt": 0,
        "doubt": None,
    })
    st.rerun()


def start_quiz(subject, cs):
    with st.spinner("Your coach is preparing a short quiz..."):
        questions, notice = get_quiz_questions(subject, cs["topic"])

    cs.update({
        "phase": "quiz",
        "quiz": questions,
        "quiz_notice": notice,
        "mock": False,
    })
    st.rerun()


def coach_mission(subject, cs):
    name = st.session_state.student_name.strip() or "there"
    remaining = days_left(subject["exam_date"])
    days = max(1, remaining)

    rows = priority_rows(subject)
    done = set(completed_for(subject))
    pending = [r for r in rows if r["Topic"] not in done]

    plan = build_timetable(subject, rows, done, days)
    today_rows = [p for p in plan if p["Date"] == date.today()]
    learn_rows = [p for p in today_rows if p["Activity"] == "Learn / Practice"]
    revision_hours = next(
        (p["Duration (Hours)"] for p in today_rows if p["Activity"] == "Revision"),
        0
    )

    hour = datetime.now().hour
    greeting = (
        "Good morning" if hour < 12
        else "Good afternoon" if hour < 17
        else "Good evening"
    )

    subject_html = esc(subject["name"])

    if not pending:
        message = (
            f"{greeting}, <b>{esc(name)}</b>! You have covered every topic in "
            f"<b>{subject_html}</b>. Now it's all about revision and mock tests."
        )
    elif remaining <= 3:
        message = (
            f"{greeting}, <b>{esc(name)}</b>. Final stretch: only "
            f"<b>{remaining} day(s)</b> left for {subject_html}. "
            f"Focus on the highest-priority topics and take a mock test."
        )
    else:
        message = (
            f"{greeting}, <b>{esc(name)}</b>! <b>{remaining} days</b> to "
            f"{subject_html} and <b>{len(pending)} topics</b> still to cover. "
            f"Here is today's mission."
        )

    coach_say(message, typing=True)

    pace = len(pending) / days

    stat_tiles([
        ("⏳", "Days left", remaining, True),
        ("📖", "Topics left", len(pending), False),
        ("🕒", "Study time today", f"{subject['daily_hours']:g} h", False),
        ("⚡", "Pace needed", f"{pace:.1f}/day", False),
    ])

    if pending and pace > 4:
        st.warning(
            "You need to cover more than 4 topics per day to finish in time. "
            "Consider raising your daily study hours, or focus on the "
            "top-priority topics first."
        )

    percent = completion_percent(subject)
    st.progress(percent / 100)
    st.caption(f"Syllabus covered: {percent:.0f}%")

    st.subheader("🎯 Today's mission")

    if learn_rows:
        for index, row in enumerate(learn_rows, start=1):
            mission_card(
                index,
                row["Topic"],
                row["Unit"] or "Learn and practise",
                row["Duration (Hours)"]
            )

        if revision_hours:
            mission_card(
                len(learn_rows) + 1,
                "Revision",
                "Go over what you studied earlier",
                revision_hours,
                revision=True
            )

        for index, row in enumerate(learn_rows):
            if st.button(
                f"▶ Start: {row['Topic']}",
                key=f"coach_start_{subject['name']}_{index}",
                type="primary" if index == 0 else "secondary",
                use_container_width=True
            ):
                start_learning(subject, cs, row["Topic"])
    else:
        st.success("No new topics left to learn. Use revision and a mock test below.")

    st.divider()
    st.subheader("🔁 Revise and test yourself")

    weak = weak_topic_names(subject)

    if weak:
        st.write("Your coach suggests revising: " + ", ".join(f"**{w}**" for w in weak))

        pick_weak = st.selectbox(
            "Weak topic to revisit", weak, key=f"coach_weak_{subject['name']}"
        )

        if st.button("📖 Revise this topic", key=f"coach_revise_{subject['name']}"):
            start_learning(subject, cs, pick_weak)

    c1, c2 = st.columns(2)

    with c1:
        any_topic = st.selectbox(
            "Or choose any topic to learn",
            [t["name"] for t in subject["topics"]],
            key=f"coach_any_{subject['name']}"
        )

        if st.button("▶ Learn this topic", key=f"coach_any_btn_{subject['name']}"):
            start_learning(subject, cs, any_topic)

    with c2:
        st.write("**Mock test**")
        st.caption("8 mixed questions from the topics you have studied (or the top priorities).")

        if st.button("🧪 Take a mock test", key=f"coach_mock_{subject['name']}"):
            if not os.getenv("GEMINI_API_KEY"):
                st.warning("Mock tests need your Gemini API key.")
            else:
                topics = list(dict.fromkeys(weak + completed_for(subject)))

                if not topics:
                    topics = [r["Topic"] for r in rows[:5]]

                with st.spinner("Building your mock test..."):
                    try:
                        questions = generate_ai_questions(
                            subject,
                            "Mixed revision across: " + ", ".join(topics[:6]),
                            8
                        )
                    except Exception as error:
                        questions = []
                        st.error(f"Could not create the mock test: {error}")

                if questions:
                    cs.update({
                        "phase": "quiz",
                        "topic": "Mock test",
                        "quiz": questions,
                        "quiz_notice": "",
                        "mock": True,
                        "attempt": cs.get("attempt", 0) + 1,
                    })
                    st.rerun()


def coach_learn(subject, cs):
    steps = cs["steps"]
    index = cs["step"]
    topic = cs["topic"]
    step = steps[index]

    st.markdown(f"### 📘 {topic}")

    if cs.get("notice"):
        st.caption(cs["notice"])

    step_dots(index, len(steps))

    coach_say(f"Step {index + 1} of {len(steps)}: <b>{esc(step['title'])}</b>")

    with st.container(border=True):
        st.markdown(step["content"])

    check = step.get("check")
    can_continue = True

    if check:
        st.markdown("**⚡ Quick check**")
        result = cs["checks"].get(index)

        choice = st.radio(
            check["question"],
            check["options"],
            index=None,
            key=f"coach_check_{subject['name']}_{topic}_{index}",
            disabled=result is not None
        )

        if result is None:
            can_continue = False

            if st.button("Check my answer", key=f"coach_check_btn_{subject['name']}_{topic}_{index}"):
                if choice is None:
                    st.warning("Pick an answer first.")
                else:
                    cs["checks"][index] = {
                        "choice": choice,
                        "correct": choice == check["answer"],
                    }
                    st.rerun()
        elif result["correct"]:
            st.success(f"✅ Correct! {check['explanation']}")
        else:
            st.error(
                f"Not quite. The correct answer is **{check['answer']}**. "
                f"{check['explanation']}"
            )

    nav_back, nav_next = st.columns(2)

    with nav_back:
        if index > 0 and st.button("⬅ Previous step", key=f"coach_prev_{subject['name']}_{index}",
                                   use_container_width=True):
            cs["step"] -= 1
            st.rerun()

    with nav_next:
        if can_continue:
            last = index == len(steps) - 1
            label = "🧪 Start the quiz" if last else "Next step ➡"

            if st.button(label, type="primary", key=f"coach_next_{subject['name']}_{index}",
                         use_container_width=True):
                if last:
                    start_quiz(subject, cs)
                else:
                    cs["step"] += 1
                    st.rerun()

    with st.expander("🙋 Stuck? Ask your coach a doubt"):
        doubt = st.text_input("Your doubt", key=f"coach_doubt_{subject['name']}_{topic}_{index}")

        if st.button("Ask", key=f"coach_doubt_btn_{subject['name']}_{topic}_{index}"):
            if not doubt.strip():
                st.warning("Type your doubt first.")
            elif not os.getenv("GEMINI_API_KEY"):
                st.warning("Gemini API key not detected.")
            else:
                with st.spinner("Thinking..."):
                    try:
                        cs["doubt"] = call_gemini(
                            doubt.strip(),
                            system_instruction=(
                                f"You are a patient exam coach for '{subject['name']}'. "
                                f"The student is learning '{topic}'. Answer simply in "
                                f"under 150 words, with one short example."
                            )
                        )
                    except Exception as error:
                        cs["doubt"] = f"Sorry, I couldn't answer right now ({error})."

        if cs.get("doubt"):
            st.info(cs["doubt"])

    if st.button("← Back to today's mission", key=f"coach_back_{subject['name']}"):
        cs["phase"] = "mission"
        st.rerun()


def coach_quiz(subject, cs):
    topic = cs["topic"]
    questions = cs.get("quiz") or []

    st.markdown(f"### 🧪 {topic}: quiz")

    if cs.get("quiz_notice"):
        st.caption(cs["quiz_notice"])

    if not questions:
        coach_say("I couldn't create a quiz for this topic right now.")

        if st.button("✅ I understood this topic", type="primary",
                     key=f"coach_self_{subject['name']}"):
            mark_complete(subject, topic)
            cs["phase"] = "mission"
            st.rerun()

        if st.button("← Back to today's mission", key=f"coach_back_q_{subject['name']}"):
            cs["phase"] = "mission"
            st.rerun()
        return

    coach_say("Answer every question, then submit. Don't worry, it's practice!")

    attempt = cs.get("attempt", 0)
    answers = {}

    with st.form(key=f"coach_quiz_form_{subject['name']}_{topic}_{attempt}"):
        for i, question in enumerate(questions):
            st.markdown(f"**Question {i + 1}: {question['question']}**")

            answers[i] = st.radio(
                "Choose your answer:",
                question["options"],
                index=None,
                key=f"coach_q_{subject['name']}_{topic}_{attempt}_{i}"
            )
            st.write("")

        submitted = st.form_submit_button("Submit quiz", use_container_width=True)

    if submitted:
        if any(a is None for a in answers.values()):
            st.warning("Please answer all questions before submitting.")
        else:
            mock = cs.get("mock", False)
            result = record_quiz(subject, topic, questions, answers,
                                 update_weak=not mock)

            if result["percentage"] >= 60 and not mock:
                mark_complete(subject, topic)
                result["mastered"] = True
            else:
                result["mastered"] = False

            cs["result"] = result
            cs["phase"] = "result"
            st.rerun()

    if st.button("← Back to today's mission", key=f"coach_back_q2_{subject['name']}"):
        cs["phase"] = "mission"
        st.rerun()


def coach_result(subject, cs):
    result = cs["result"]
    mock = cs.get("mock", False)

    st.markdown(f"### 📊 Result: {result['topic']}")

    stat_tiles([
        ("🎯", "Score", f"{result['score']}/{result['total']}", True),
        ("📈", "Percentage", f"{result['percentage']:.0f}%", False),
    ])

    st.progress(result["percentage"] / 100)

    if result["percentage"] == 100 and not result.get("celebrated"):
        st.balloons()
        result["celebrated"] = True

    if mock:
        coach_say(
            "Mock test done! Check the review below and revisit any topic "
            "where you lost marks."
        )
    elif result["mastered"]:
        coach_say(
            f"Great work! I've marked <b>{esc(result['topic'])}</b> as completed "
            f"and updated your plan."
        )
    else:
        coach_say(
            f"Let's go over <b>{esc(result['topic'])}</b> once more. I've added it "
            f"to your revision list. You can re-read the lesson and try again."
        )

    st.subheader("📖 Answer review")

    for i, item in enumerate(result["feedback"]):
        status = "Correct" if item["is_correct"] else "Incorrect"

        with st.expander(f"Question {i + 1} - {status}"):
            st.write(item["question"])
            st.write(f"Your answer: {item['selected']}")
            st.write(f"Correct answer: {item['correct']}")

            if item["explanation"]:
                st.info(item["explanation"])

    b1, b2, b3 = st.columns(3)

    with b1:
        if st.button("➡ Continue", type="primary", use_container_width=True,
                     key=f"coach_continue_{subject['name']}"):
            cs["phase"] = "mission"
            st.rerun()

    with b2:
        if st.button("🔁 Retry the quiz", use_container_width=True,
                     key=f"coach_retry_{subject['name']}"):
            cs["attempt"] = cs.get("attempt", 0) + 1
            cs["phase"] = "quiz"
            st.rerun()

    with b3:
        if not mock and cs.get("steps"):
            if st.button("📖 Review the lesson", use_container_width=True,
                         key=f"coach_review_{subject['name']}"):
                cs["step"] = 0
                cs["phase"] = "learn"
                st.rerun()


def page_coach(subject):
    cs = st.session_state.coach.setdefault(subject["name"], {"phase": "mission"})

    section_title(
        "AI CO-PILOT",
        "Study Coach",
        f"Your guided preparation for {subject['name']}"
    )

    if days_left(subject["exam_date"]) < 0:
        st.error("This exam date has already passed.")
        return

    phase = cs.get("phase", "mission")

    # Safety net: a phase that lost its data returns to the mission screen
    if phase == "learn" and not cs.get("steps"):
        phase = cs["phase"] = "mission"
    if phase == "quiz" and "quiz" not in cs:
        phase = cs["phase"] = "mission"
    if phase == "result" and "result" not in cs:
        phase = cs["phase"] = "mission"

    if phase == "learn":
        coach_learn(subject, cs)
    elif phase == "quiz":
        coach_quiz(subject, cs)
    elif phase == "result":
        coach_result(subject, cs)
    else:
        coach_mission(subject, cs)


# =====================================================================
# MAIN: TITLE AND NAVIGATION
# =====================================================================

inject_css()

sidebar_status()

hero(
    "Smart Exam Tutor",
    "Tell me your exam date and syllabus. I'll prepare you, step by step.",
    icon="🛰️",
    badge=f"v{APP_VERSION} · NEON EDITION"
)

PAGES = [
    "🏠 Dashboard",
    "🧠 Study Coach",
    "🗂️ Subjects",
    "📅 Study Planner",
    "🤖 Learning Tutor",
    "📝 Practice Centre",
    "📊 Progress Tracker",
]

page = st.sidebar.radio("Choose a module", PAGES, key="nav_page")

SUBJECT_PAGES = {
    "🧠 Study Coach": page_coach,
    "📅 Study Planner": page_planner,
    "🤖 Learning Tutor": page_tutor,
    "📝 Practice Centre": page_practice,
    "📊 Progress Tracker": page_progress,
}

if page == "🏠 Dashboard":
    page_dashboard()

elif page == "🗂️ Subjects":
    page_subjects()

else:
    subject_names = [s["name"] for s in st.session_state.subjects]

    if not subject_names:
        st.warning("Please add a subject in the Subjects page first.")
    else:
        chosen = st.sidebar.selectbox(
            "Active subject", subject_names, key="active_subject"
        )
        SUBJECT_PAGES[page](get_subject(chosen))