class McqRange {
  final int start;
  final int end;
  final double points;

  McqRange({
    required this.start,
    required this.end,
    required this.points,
  });

  bool covers(int questionNumber) {
    return questionNumber >= start && questionNumber <= end;
  }

  factory McqRange.fromJson(Map<String, dynamic> json) {
    return McqRange(
      start: json['start'] as int,
      end: json['end'] as int,
      points: (json['points'] as num).toDouble(),
    );
  }

  Map<String, dynamic> toJson() {
    return {
      'start': start,
      'end': end,
      'points': points,
    };
  }
}


class MasterPacket {
  final String examName;
  final String examMode; // "quiz" | "shamel"
  final int mcqCount;
  final List<McqRange> mcqRanges;
  final bool hasEssays;
  final Map<String, double> essayPointsMap;
  final String templatePath;
  final Map<String, dynamic> roiCoordinates;
  final String groupName;
  final List<String> answerVersions; // ["A"] for quiz mode
  final Map<String, Map<String, String>> modelAnswers; // {version: {"1":"A",...}}
  final Map<String, List<int>> voidedQuestions; // {version: [4,7]}

  // Blueprint/template layout parameters for calibration
  final int choicesPerQuestion;  // e.g. 4 for A-D, 5 for A-E
  final int questionsPerBlock;   // e.g. 10 questions per physical row block
  final int idLetterCount;       // e.g. 6 letter columns
  final int idDigitColumns;      // e.g. 3 digit columns

  MasterPacket({
    required this.examName,
    required this.examMode,
    required this.mcqCount,
    required this.mcqRanges,
    required this.hasEssays,
    required this.essayPointsMap,
    required this.templatePath,
    required this.roiCoordinates,
    required this.groupName,
    required this.answerVersions,
    required this.modelAnswers,
    required this.voidedQuestions,
    required this.choicesPerQuestion,
    required this.questionsPerBlock,
    required this.idLetterCount,
    required this.idDigitColumns,
  });

  bool get supportsMultipleVersions => answerVersions.length > 1;

  double get essayMaxTotal {
    return essayPointsMap.values.fold(0.0, (sum, val) => sum + val);
  }

  McqRange? rangeForQuestion(int questionNumber) {
    for (final range in mcqRanges) {
      if (range.covers(questionNumber)) {
        return range;
      }
    }
    return null;
  }

  factory MasterPacket.fromJson(Map<String, dynamic> json) {
    return MasterPacket(
      examName: json['exam_name'] as String? ?? json['examName'] as String,
      examMode: json['exam_mode'] as String? ?? json['examMode'] as String? ?? "quiz",
      mcqCount: json['mcq_count'] as int? ?? json['mcqCount'] as int,
      mcqRanges: (json['mcq_ranges'] as List? ?? json['mcqRanges'] as List? ?? [])
          .map((e) => McqRange.fromJson(e as Map<String, dynamic>))
          .toList(),
      hasEssays: json['has_essays'] as bool? ?? json['hasEssays'] as bool? ?? false,
      essayPointsMap: (json['essay_points_map'] as Map? ?? json['essayPointsMap'] as Map? ?? {}).map(
        (k, v) => MapEntry(k as String, (v as num).toDouble()),
      ),
      templatePath: json['template_path'] as String? ?? json['templatePath'] as String? ?? "",
      roiCoordinates: json['roi_coordinates'] as Map<String, dynamic>? ?? json['roiCoordinates'] as Map<String, dynamic>? ?? {},
      groupName: json['group_name'] as String? ?? json['groupName'] as String? ?? "",
      answerVersions: (json['answer_versions'] as List? ?? json['answerVersions'] as List? ?? []).map((e) => e as String).toList(),
      modelAnswers: (json['model_answers'] as Map? ?? json['modelAnswers'] as Map? ?? {}).map(
        (k, v) => MapEntry(
          k as String,
          (v as Map).map((k2, v2) => MapEntry(k2 as String, v2 as String)),
        ),
      ),
      voidedQuestions: (json['voided_questions'] as Map? ?? json['voidedQuestions'] as Map? ?? {}).map(
        (k, v) => MapEntry(
          k as String,
          (v as List).map((e) => e as int).toList(),
        ),
      ),
      // Blueprint layout params — fall back to safe defaults if the desktop
      // hasn't been updated to include them yet, but log a warning.
      choicesPerQuestion: json['choices_per_question'] as int? ?? json['choicesPerQuestion'] as int? ?? 4,
      questionsPerBlock: json['questions_per_block'] as int? ?? json['questionsPerBlock'] as int? ?? 10,
      idLetterCount: json['id_letter_count'] as int? ?? json['idLetterCount'] as int? ?? 6,
      idDigitColumns: json['id_digit_columns'] as int? ?? json['idDigitColumns'] as int? ?? 0,
    );
  }

  Map<String, dynamic> toJson() {
    return {
      'exam_name': examName,
      'exam_mode': examMode,
      'mcq_count': mcqCount,
      'mcq_ranges': mcqRanges.map((e) => e.toJson()).toList(),
      'has_essays': hasEssays,
      'essay_points_map': essayPointsMap,
      'template_path': templatePath,
      'roi_coordinates': roiCoordinates,
      'group_name': groupName,
      'answer_versions': answerVersions,
      'model_answers': modelAnswers,
      'voided_questions': voidedQuestions,
      'choices_per_question': choicesPerQuestion,
      'questions_per_block': questionsPerBlock,
      'id_letter_count': idLetterCount,
      'id_digit_columns': idDigitColumns,
    };
  }
}

class Mistake {
  final int question;
  final String correct;
  final String given;

  Mistake({
    required this.question,
    required this.correct,
    required this.given,
  });

  factory Mistake.fromJson(Map<String, dynamic> json) {
    return Mistake(
      question: json['question'] as int,
      correct: json['correct'] as String,
      given: json['given'] as String,
    );
  }

  Map<String, dynamic> toJson() {
    return {
      'question': question,
      'correct': correct,
      'given': given,
    };
  }
}

class GradeResult {
  String? studentId; // MUTABLE — null until OCR or manual entry resolves it
  String idSource; // "ocr" | "manual"
  String? groupType; // MUTABLE — null until OCR (bubble read) or manual entry resolves it
  final String answerVersion;
  final double mcqScore;
  final List<Mistake> mistakes;
  final String? imagePath;
  final String? debugImagePath; // unique per scan — never reuse a shared filename, Flutter's image cache keys on path
  double essayTotal; // MUTABLE — proctor types this in

  GradeResult({
    this.studentId,
    required this.idSource,
    this.groupType,
    required this.answerVersion,
    required this.mcqScore,
    required this.mistakes,
    required this.essayTotal,
    this.imagePath,
    this.debugImagePath,
  });

  double get totalScore => mcqScore + essayTotal;

  Map<String, dynamic> toSubmitScorePayload(String timestamp) {
    return {
      "type": "submit_score",
      "student_id": studentId,
      "group_type": groupType,
      "answer_version": answerVersion,
      "mcq_score": mcqScore,
      "essay_total": essayTotal,
      "total_score": totalScore,
      "mistakes": mistakes.map((m) => m.toJson()).toList(),
      "id_source": idSource,
      "timestamp": timestamp,
    };
  }
}

class DuplicateComparison {
  final String studentId;
  final double previousScore;
  final double incomingScore;
  final String? previousVersion;
  final String? incomingVersion;
  final String previousTimestamp;
  final String incomingTimestamp;

  DuplicateComparison({
    required this.studentId,
    required this.previousScore,
    required this.incomingScore,
    this.previousVersion,
    this.incomingVersion,
    required this.previousTimestamp,
    required this.incomingTimestamp,
  });

  factory DuplicateComparison.fromJson(Map<String, dynamic> json) {
    final previous = json['previous'] as Map<String, dynamic>;
    final incoming = json['incoming'] as Map<String, dynamic>;
    return DuplicateComparison(
      studentId: json['student_id'] as String,
      previousScore: (previous['score'] as num).toDouble(),
      incomingScore: (incoming['score'] as num).toDouble(),
      previousVersion: previous['answer_version'] as String?,
      incomingVersion: incoming['answer_version'] as String?,
      previousTimestamp: previous['timestamp'] as String,
      incomingTimestamp: incoming['timestamp'] as String,
    );
  }
}

enum DuplicateAction {
  overwrite,
  keepPrevious,
  discardBoth;

  String get wireValue {
    switch (this) {
      case DuplicateAction.overwrite:
        return "overwrite";
      case DuplicateAction.keepPrevious:
        return "keep_previous";
      case DuplicateAction.discardBoth:
        return "discard_both";
    }
  }
}