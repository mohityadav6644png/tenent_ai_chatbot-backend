"""
Unit tests for follow-up resolution deterministic gate and output validation.
"""

import pytest
from app.modules.chat.followup import is_clearly_independent, validate_resolver_output


CATALOG = {
    "files": [
        {
            "name": "Students",
            "slug": "students",
            "columns": [{"key": "class"}, {"key": "section"}, {"key": "fee_status"}],
        },
        {
            "name": "Exam Marks",
            "slug": "exam_marks",
            "columns": [{"key": "marks_obtained"}, {"key": "subject"}],
        },
    ],
    "file_fields": {"class": "string", "section": "string", "marks_obtained": "number"},
}


def test_gate_followup_starters():
    # Call resolver when starting with "and", "also", "only", "now", "same", "what about", "how about", "which of"
    assert not is_clearly_independent("Only for Class 8", CATALOG)
    assert not is_clearly_independent("And by section?", CATALOG)
    assert not is_clearly_independent("Also show fees", CATALOG)
    assert not is_clearly_independent("Now for 2025", CATALOG)
    assert not is_clearly_independent("Same for section B", CATALOG)
    assert not is_clearly_independent("What about only Class 10?", CATALOG)
    assert not is_clearly_independent("How about Science?", CATALOG)
    assert not is_clearly_independent("Which of those is the lowest?", CATALOG)


def test_gate_short_reference_words():
    # Call resolver when <= 6 words and contains reference word (it, its, that, those, them, this, these, they, their)
    assert not is_clearly_independent("What is its price?", CATALOG)
    assert not is_clearly_independent("Show that student", CATALOG)
    assert not is_clearly_independent("Count those", CATALOG)
    assert not is_clearly_independent("Filter them by fee", CATALOG)
    assert not is_clearly_independent("Who are they?", CATALOG)


def test_gate_independent_long_message():
    # Treat message as independent when > 12 words and names a file or column
    long_q = "Please show me the total marks obtained by every student across all different sections in this school year"
    assert is_clearly_independent(long_q, CATALOG)


def test_validate_resolver_output_success():
    raw = {
        "is_followup": True,
        "standalone_question": "How many students are in Class 8?",
        "needs_clarification": False,
        "clarification_question": None,
    }
    validated = validate_resolver_output(raw, "Only for Class 8")
    assert validated is not None
    assert validated["standalone_question"] == "How many students are in Class 8?"
    assert validated["is_followup"] is True


def test_validate_resolver_output_rejects_markup():
    raw = {
        "is_followup": True,
        "standalone_question": "<script>alert(1)</script> Show marks",
        "needs_clarification": False,
    }
    validated = validate_resolver_output(raw, "Show marks")
    assert validated is None
