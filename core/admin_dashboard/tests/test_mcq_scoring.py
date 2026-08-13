"""
tests/test_mcq_scoring.py
---------------------------
Unit tests for mcq_scoring.score_mcq — the ported Dart scoring logic.
No I/O, no mocks needed; pure function over dicts.
"""
from admin_dashboard.mcq_scoring import score_mcq
from admin_dashboard.tests.conftest import clean_questions


def test_all_correct_scores_full_points(master_packet):
    questions = clean_questions({1: "A", 2: "B", 4: "D", 5: "A"})  # 3 is voided, skip
    score, mistakes = score_mcq(questions, master_packet, "A")
    assert score == 8.0  # 4 answered questions * 2.0 pts each
    assert mistakes == []


def test_wrong_answer_recorded_as_mistake(master_packet):
    questions = clean_questions({1: "A", 2: "C", 4: "D", 5: "A"})  # Q2 wrong (should be B)
    score, mistakes = score_mcq(questions, master_packet, "A")
    assert score == 6.0
    assert mistakes == [{"question": 2, "correct": "B", "given": "C"}]


def test_blank_answer_recorded_as_mistake_with_state(master_packet):
    questions = clean_questions({1: "A", 2: None, 4: "D", 5: "A"})
    score, mistakes = score_mcq(questions, master_packet, "A")
    assert score == 6.0
    assert mistakes == [{"question": 2, "correct": "B", "given": "BLANK"}]


def test_voided_question_never_counted_either_way(master_packet):
    # Q3 is voided in the fixture — even if "answered correctly" it must
    # neither add points nor appear as a mistake.
    questions = clean_questions({1: "A", 2: "B", 3: "C", 4: "D", 5: "A"})
    score, mistakes = score_mcq(questions, master_packet, "A")
    assert score == 8.0
    assert all(m["question"] != 3 for m in mistakes)


def test_question_with_no_model_answer_is_skipped(master_packet):
    questions = clean_questions({1: "A", 6: "A"})  # Q6 doesn't exist in model_answers
    score, mistakes = score_mcq(questions, master_packet, "A")
    assert score == 2.0
    assert mistakes == []


def test_unknown_answer_version_grades_everything_as_wrong(master_packet):
    # No model_answers under version "Z" -> every question has no
    # `correct` value -> current behavior is to skip (not penalize) —
    # documents the actual behavior so a future change to it is a
    # deliberate decision, not a silent regression.
    questions = clean_questions({1: "A"})
    score, mistakes = score_mcq(questions, master_packet, "Z")
    assert score == 0.0
    assert mistakes == []


def test_multi_marked_state_recorded_verbatim(master_packet):
    questions = [{"question_number": 1, "state": "MULTI_MARKED", "answer": None}]
    score, mistakes = score_mcq(questions, master_packet, "A")
    assert score == 0.0
    assert mistakes == [{"question": 1, "correct": "A", "given": "MULTI_MARKED"}]


def test_points_use_correct_range_for_question_number():
    packet = {
        "model_answers": {"A": {"1": "A", "10": "B"}},
        "voided_questions": {"A": []},
        "mcq_ranges": [{"start": 1, "end": 5, "points": 1.0}, {"start": 6, "end": 10, "points": 3.0}],
    }
    questions = clean_questions({1: "A", 10: "B"})
    score, mistakes = score_mcq(questions, packet, "A")
    assert score == 4.0  # 1.0 (Q1, first range) + 3.0 (Q10, second range)
