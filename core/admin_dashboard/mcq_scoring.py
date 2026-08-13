"""
mcq_scoring.py
----------------
Python port of the scoring block that used to run on the phone
(cv_engine_service.dart, comparing raw detected answers against
masterPacket.modelAnswers). Now that process_exam_in_memory only
*detects* bubbled answers and doesn't grade them, this piece has to run
somewhere — this is that somewhere, using the exact same master_packet
dict the lab already builds via ProjectManager.build_sync_packet() and
already sends to phones on auth, so no new data has to be threaded
through.

Field names below match build_sync_packet()'s actual snake_case output:
model_answers, voided_questions, mcq_ranges — NOT the camelCase the Dart
client used internally.
"""


def score_mcq(questions: list[dict], master_packet: dict, answer_version: str) -> tuple[float, list[dict]]:
    """questions: the "questions" list from process_exam_in_memory's
    result JSON — each item has question_number, state ("ANSWERED" /
    "BLANK" / "MULTI_MARKED"), and answer (only meaningful when
    state == "ANSWERED").

    Returns (mcq_score, mistakes) where mistakes is a list of
    {"question": int, "correct": str, "given": str} for the review UI —
    same shape the phone used to send up in submit_score.
    """
    model_answers = master_packet.get("model_answers", {}).get(answer_version, {})
    voided = set(master_packet.get("voided_questions", {}).get(answer_version, []))
    ranges = master_packet.get("mcq_ranges", [])

    mcq_score = 0.0
    mistakes = []

    for q in questions:
        qnum = q["question_number"]
        if qnum in voided:
            continue

        correct = model_answers.get(str(qnum))
        if not correct:
            # No model answer configured for this question — nothing to
            # grade against, skip rather than silently counting it wrong.
            continue

        state = q.get("state", "BLANK")
        given = q.get("answer") if state == "ANSWERED" else state

        if state == "ANSWERED" and given == correct:
            mcq_score += _points_for_question(ranges, qnum)
        else:
            mistakes.append({"question": qnum, "correct": correct, "given": given})

    return mcq_score, mistakes


def _points_for_question(ranges: list[dict], question_number: int) -> float:
    for r in ranges:
        if r.get("start", 0) <= question_number <= r.get("end", 0):
            return r.get("points", 0.0)
    return 0.0
