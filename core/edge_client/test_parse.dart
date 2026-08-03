import "dart:convert";
import "package:nexus_edge_mobile/models/exam_models.dart";

void main() {
  final jsonStr = """
  {
    "exam_name": "Test Project",
    "exam_mode": "quiz",
    "mcq_count": 10,
    "mcq_ranges": [],
    "has_essays": false,
    "essay_points_map": {},
    "template_path": "",
    "roi_coordinates": {},
    "group_name": "Group A",
    "roster": [],
    "answer_versions": ["A"],
    "model_answers": {},
    "voided_questions": {},
    "choices_per_question": 5,
    "questions_per_block": 8,
    "id_letter_count": 8,
    "id_digit_columns": 3
  }
  """;

  final packet = MasterPacket.fromJson(jsonDecode(jsonStr));
  print("Parsed from JSON:");
  print("Choices: ${packet.choicesPerQuestion}");
  print("Questions per block: ${packet.questionsPerBlock}");
  print("ID Letter count: ${packet.idLetterCount}");
  print("ID Digit columns: ${packet.idDigitColumns}");
}
