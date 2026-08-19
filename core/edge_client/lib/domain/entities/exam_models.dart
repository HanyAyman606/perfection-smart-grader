import 'dart:convert';

/// Per-column question counts as computed by the desktop dashboard's
/// template printer (ProjectManager.compute_mcq_column_layout). This is
/// the AUTHORITATIVE layout — it reflects the physically printed paper,
/// not a guess. e.g. {numCols: 3, columns: {1: 9, 2: 8, 3: 8}} for 25
/// questions in 3 columns.
class McqColumnLayout {
  final int numCols;
  final Map<int, int> columns; // 1-indexed column number -> question count

  McqColumnLayout({required this.numCols, required this.columns});

  factory McqColumnLayout.fromJson(Map<String, dynamic> json) {
    final rawCols = json['columns'] as Map? ?? {};
    final columns = <int, int>{};
    rawCols.forEach((k, v) {
      final colIndex = int.tryParse(k.toString());
      if (colIndex != null) columns[colIndex] = (v as num).toInt();
    });
    return McqColumnLayout(
      numCols: json['num_cols'] as int? ?? columns.length,
      columns: columns,
    );
  }

  /// Ordered list of per-column sizes, e.g. [9, 8, 8] — exactly the shape
  /// the C++ engine's ExamConfig.mcq_column_sizes expects.
  List<int> get orderedSizes {
    return List.generate(numCols, (i) => columns[i + 1] ?? 0);
  }

  Map<String, dynamic> toJson() => {
        'num_cols': numCols,
        'columns': columns.map((k, v) => MapEntry(k.toString(), v)),
      };
}

class McqRange {
  final int start;
  final int end;
  final double points;

  McqRange({required this.start, required this.end, required this.points});

  bool covers(int questionNumber) => questionNumber >= start && questionNumber <= end;

  factory McqRange.fromJson(Map<String, dynamic> json) {
    return McqRange(
      start: json['start'] as int,
      end: json['end'] as int,
      points: (json['points'] as num).toDouble(),
    );
  }
}

/// The exam configuration pushed down from the PySide dashboard on auth
/// success. Field shapes here match project_manager.py's
/// build_sync_packet() exactly — this is NOT the same shape as the C++
/// engine's ExamConfig; see toExamConfigJson() for that translation.
class MasterPacket {
  /// Physical sheet layout constants — these describe how many ID columns
  /// are actually printed on the bubble sheet, and must NEVER be derived
  /// from idNumLetters/idNumDigits. Those two fields describe pool sizes
  /// (idNumLetters = how many distinct letters exist to choose from, e.g.
  /// 6 for "CDEFMW"; idNumDigits = how many digit values exist per column,
  /// always 10 for 0-9) — NOT how many columns are printed. Conflating the
  /// two caused id_letter_cols to be sent as 6 instead of 1, which made the
  /// FFI/C++ side read 6 letter columns instead of 1, corrupting every ID
  /// read (e.g. "D---C-2-5" instead of a clean "E123").
  static const int idLetterCols = 1; // always exactly one printed letter column
  static const int idDigitCols = 3;  // always exactly three printed digit columns

  final String examName;
  final String examMode; // "quiz" | "shamel"
  final int mcqCount;
  final List<McqRange> mcqRanges;
  final bool hasEssays;
  final Map<String, double> essayPointsMap;
  final String groupName;
  final List<String> answerVersions;
  final Map<String, Map<String, String>> modelAnswers; // {version: {"1":"A",...}}
  final Map<String, List<int>> voidedQuestions; // {version: [4,7]}

  final int choicesPerQuestion; // e.g. 4 for A-D
  final McqColumnLayout mcqColumns; // authoritative per-column layout
  final int idNumDigits; // derived server-side from exam mode (quiz=3, shamel=4)
  final int idNumLetters;
  final List<String> idLetters;

  MasterPacket({
    required this.examName,
    required this.examMode,
    required this.mcqCount,
    required this.mcqRanges,
    required this.hasEssays,
    required this.essayPointsMap,
    required this.groupName,
    required this.answerVersions,
    required this.modelAnswers,
    required this.voidedQuestions,
    required this.choicesPerQuestion,
    required this.mcqColumns,
    required this.idNumDigits,
    required this.idNumLetters,
    required this.idLetters,
  });

  bool get supportsMultipleVersions => answerVersions.length > 1;

  double get essayMaxTotal => essayPointsMap.values.fold(0.0, (sum, val) => sum + val);

  McqRange? rangeForQuestion(int questionNumber) {
    for (final range in mcqRanges) {
      if (range.covers(questionNumber)) return range;
    }
    return null;
  }

  factory MasterPacket.fromJson(Map<String, dynamic> json) {
    final idJson = json['id'] as Map<String, dynamic>? ?? {};
    return MasterPacket(
      examName: json['exam_name'] as String? ?? 'Untitled Exam',
      examMode: json['exam_mode'] as String? ?? 'quiz',
      mcqCount: json['mcq_count'] as int? ?? 0,
      mcqRanges: (json['mcq_ranges'] as List? ?? [])
          .map((e) => McqRange.fromJson(e as Map<String, dynamic>))
          .toList(),
      hasEssays: json['has_essays'] as bool? ?? false,
      essayPointsMap: (json['essay_points_map'] as Map? ?? {})
          .map((k, v) => MapEntry(k as String, (v as num).toDouble())),
      groupName: json['group_name'] as String? ?? '',
      answerVersions: (json['answer_versions'] as List? ?? ['A']).map((e) => e as String).toList(),
      modelAnswers: (json['model_answers'] as Map? ?? {}).map(
        (k, v) => MapEntry(k as String, (v as Map).map((k2, v2) => MapEntry(k2 as String, v2 as String))),
      ),
      voidedQuestions: (json['voided_questions'] as Map? ?? {}).map(
        (k, v) => MapEntry(k as String, (v as List).map((e) => e as int).toList()),
      ),
      choicesPerQuestion: json['choices_per_question'] as int? ?? 4,
      mcqColumns: json.containsKey('mcq_columns')
          ? McqColumnLayout.fromJson(json['mcq_columns'] as Map<String, dynamic>)
          : McqColumnLayout(numCols: 1, columns: {1: json['mcq_count'] as int? ?? 0}),
      idNumDigits: idJson['num_digits'] as int? ?? 3,
      idNumLetters: idJson['num_letters'] as int? ?? 0,
      idLetters: (idJson['letters'] as List? ?? []).map((e) => e as String).toList(),
    );
  }

  /// Translates the dashboard's [MasterPacket] into the JSON file that
  /// `run_exam_pipeline` expects on disk as `exam_config.json`.
  ///
  /// Matches the schema declared in `cv_engine/include/exam_config.hpp`:
  ///   exam_id, exam_type, num_questions (REQUIRED)
  ///   id_letters (string, e.g. "CDEFMW"), id_digit_cols (REQUIRED)
  ///   num_choices, id_letter_cols, num_mcq_columns (optional, have defaults)
  ///   model_path (optional, defaults to "shamel.onnx" in C++)
  ///
  /// Column question counts are NOT sent — the engine derives them via
  /// ceil-division from num_questions / num_mcq_columns, matching the same
  /// rule used by bubble_sheet_studio.html.
  String toExamConfigJson({required String modelPath}) {
    return jsonEncode({
      // Required fields
      'exam_id':       examName,
      'exam_type':     examMode,   // "quiz" | "shamel"
      'num_questions': mcqCount,
      // id_letters: join the List<String> into a single string e.g. "CDEFMW"
      'id_letters':    idLetters.join(''),
      // id_digit_cols / id_letter_cols describe the PRINTED sheet layout —
      // always 3 digit columns and 1 letter column. idNumDigits/idNumLetters
      // are pool sizes (how many values are valid per column / how many
      // distinct letters exist), not column counts, and must not be reused
      // here — see the constants' doc comment above.
      'id_digit_cols': idDigitCols,
      // Optional overrides (C++ defaults: num_choices=4, id_letter_cols=1, num_mcq_columns=3)
      'num_choices':     choicesPerQuestion,
      'id_letter_cols':  idLetterCols,
      'num_mcq_columns': mcqColumns.numCols,
      // Model path — must match the filename copied to app support by ModelPathService.
      // "shamel.onnx" is the C++ default; we pass it explicitly so the
      // value is always visible in logs regardless of C++ defaults.
      'model_path': modelPath,
    });
  }
}

class Mistake {
  final int question;
  final String correct;
  final String given;

  Mistake({required this.question, required this.correct, required this.given});

  factory Mistake.fromJson(Map<String, dynamic> json) {
    return Mistake(
      question: json['question'] as int,
      correct: json['correct'] as String,
      given: json['given'] as String,
    );
  }

  Map<String, dynamic> toJson() => {'question': question, 'correct': correct, 'given': given};
}

/// A single ID column's read result, straight from the Step 2 JSON's
/// id_columns array (see API_DOCUMENTATION.md).
class IdColumnResult {
  final String columnLabel;
  final String state; // ANSWERED | BLANK | MULTIPLE
  final dynamic answer; // String? or List<String>?

  IdColumnResult({required this.columnLabel, required this.state, this.answer});

  factory IdColumnResult.fromJson(Map<String, dynamic> json) {
    return IdColumnResult(
      columnLabel: json['column_label'] as String,
      state: json['state'] as String,
      answer: json['answer'],
    );
  }
}

/// A single question's read result, straight from Step 2's questions array.
class QuestionResult {
  final int questionNumber;
  final String state; // ANSWERED | BLANK | MULTIPLE
  final dynamic answer; // String? or List<String>?

  QuestionResult({required this.questionNumber, required this.state, this.answer});

  factory QuestionResult.fromJson(Map<String, dynamic> json) {
    return QuestionResult(
      questionNumber: json['question_number'] as int,
      state: json['state'] as String,
      answer: json['answer'],
    );
  }
}

/// Everything the grading review screen needs, assembled from Step 2's
/// raw JSON + the model answer key. Mutable fields mirror the old
/// GradeResult shape so the review screen's manual-correction flow
/// carries over unchanged.
class GradeResult {
  String? studentId;
  String idSource; // "ocr" | "manual"
  String? groupType;
  final String answerVersion;
  final double mcqScore;
  final List<Mistake> mistakes;
  final List<QuestionResult> questions;
  final List<IdColumnResult> idColumns;
  final bool idNeedsReview; // true when any ID column was not cleanly ANSWERED (partial read)
  final String? annotatedIdImagePath;
  final String? annotatedMcqImagePath;
  final String? rawImagePath; // deleted once submit is acknowledged — see SessionCacheManager
  double essayTotal;

  GradeResult({
    this.studentId,
    required this.idSource,
    this.groupType,
    required this.answerVersion,
    required this.mcqScore,
    required this.mistakes,
    required this.questions,
    required this.idColumns,
    this.idNeedsReview = false,
    this.annotatedIdImagePath,
    this.annotatedMcqImagePath,
    this.rawImagePath,
    required this.essayTotal,
  });

  double get totalScore => mcqScore + essayTotal;

  bool get needsReview => mistakes.any((m) => m.given == 'MULTIPLE' || m.given == 'rejected') ||
      idColumns.any((c) => c.state == 'MULTIPLE');

  Map<String, dynamic> toSubmitScorePayload(String timestamp) {
    return {
      'type': 'submit_score',
      'student_id': studentId,
      'group_type': groupType,
      'answer_version': answerVersion,
      'mcq_score': mcqScore,
      'essay_total': essayTotal,
      'total_score': totalScore,
      'mistakes': mistakes.map((m) => m.toJson()).toList(),
      'id_source': idSource,
      'timestamp': timestamp,
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
        return 'overwrite';
      case DuplicateAction.keepPrevious:
        return 'keep_previous';
      case DuplicateAction.discardBoth:
        return 'discard_both';
    }
  }
}

sealed class SubmitResult {}
class SubmitSuccess extends SubmitResult {}
class SubmitFailed extends SubmitResult {
  final String? reason;
  SubmitFailed([this.reason]);
}
class SubmitDuplicate extends SubmitResult {
  final DuplicateComparison comparison;
  SubmitDuplicate(this.comparison);
}