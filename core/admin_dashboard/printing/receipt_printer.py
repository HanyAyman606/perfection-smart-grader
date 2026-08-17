"""
printing/receipt_printer.py
----------------------------
Prints a physical receipt on the proctor's desktop thermal printer,
immediately after a submit_score is saved successfully — see
workers/websocket_server.py:_handle_submit_score, the only call site.

build_receipt_data() is the ONLY place that maps submit_score's wire
field names (student_id, mcq_score, essay_total, total_score, mistakes)
into this module's own shape — print_ultimate_receipt() itself never
needs to know about the websocket payload format.
"""

from datetime import datetime
from escpos.printer import Win32Raw


def build_receipt_data(
    *,
    student_id: str,
    mcq_score: float,
    essay_score: float,
    total_score: float,
    max_score: float,
    mistakes: list[dict],
    quiz_name: str,
) -> dict:
    """`mistakes` here are exactly what the phone sent — computed
    client-side during scanning (Mistake.toJson() in exam_models.dart) —
    NEVER recomputed against the model answer key on this side."""
    return {
        "quiz_name": quiz_name,
        "student_id": student_id,
        "total_score": total_score,
        "max_score": max_score,
        "mcq_score": mcq_score,
        "essay_score": essay_score,
        "mistakes": [
            {"q": m.get("question"), "correct": m.get("correct"), "given": m.get("given")}
            for m in mistakes
        ],
    }


def print_ultimate_receipt(printer_name, student_data, proctor_name):
    try:
        p = Win32Raw(printer_name)

        NORMAL_FONT = b'\x1b\x4d\x00'
        NORMAL_SIZE = b'\x1d\x21\x00'
        BIG_FONT = b'\x1d\x21\x11'
        HUGE_FONT = b'\x1d\x21\x22'
        BOLD_ON = b'\x1b\x45\x01'
        BOLD_OFF = b'\x1b\x45\x00'
        ALIGN_CENTER = b'\x1b\x61\x01'
        ALIGN_LEFT = b'\x1b\x61\x00'

        current_time = datetime.now().strftime("%Y-%m-%d %H:%M")

        total = student_data['total_score']
        max_s = student_data['max_score']
        quiz_name = student_data.get('quiz_name', 'General Quiz')

        p._raw(NORMAL_FONT + ALIGN_CENTER)
        p.text("\n")

        p._raw(BIG_FONT + BOLD_ON)
        p.text(f"{quiz_name}\n")

        p._raw(NORMAL_SIZE + BOLD_OFF)
        p.text("\n")

        p._raw(HUGE_FONT + BOLD_ON)
        p.text(f"{total}/{max_s}\n")

        p._raw(NORMAL_SIZE + BOLD_OFF)
        p.text("\n")

        p._raw(BOLD_ON)
        p.text(f"ID: {student_data['student_id']}\n")
        p._raw(BOLD_OFF)
        p.text("\n")

        p._raw(ALIGN_LEFT)
        p.text("-" * 42 + "\n")

        p._raw(BOLD_ON)
        p.text(f"MCQ Score: {student_data['mcq_score']} | Essay Score: {student_data['essay_score']}\n")
        p._raw(BOLD_OFF)

        p.text("-" * 42 + "\n")

        p._raw(BOLD_ON)
        p.text("Mistakes Breakdown:\n")
        p.text("-" * 42 + "\n")

        p.text("Question       | Correct | Student \n")
        p.text("-" * 42 + "\n")
        p._raw(BOLD_OFF)

        mistakes = student_data.get('mistakes', [])
        if not mistakes:
            p._raw(ALIGN_CENTER + BOLD_ON)
            p.text("NO MISTAKES\n")
            p._raw(ALIGN_LEFT + BOLD_OFF)
        else:
            for item in mistakes:
                q_str = f"Question {item['q']}"
                correct = f"[{item['correct']}]"
                given = f"[{item['given']}]"
                row = f"{q_str:<14} | {correct:^7} | {given:^7}\n"
                p.text(row)

        p.text("-" * 42 + "\n")

        p.text(f"Proctor: {proctor_name}\n")
        p.text(f"Time: {current_time}\n")
        p.text("-" * 42 + "\n")

        p._raw(ALIGN_CENTER + BOLD_ON)
        p.text("PERFECTION IN PHYSICS\n")

        p._raw(NORMAL_SIZE + BOLD_OFF)
        p.text("\n\n\n")
        p.cut()
        p.close()

    except Exception as e:
        # Must never crash or block a grade save — the grade is already
        # committed to roster.db by the time this runs (see
        # websocket_server.py). A print failure (printer off, out of
        # paper, wrong name) is logged, never raised.
        print(f"Receipt print failed: {e}")