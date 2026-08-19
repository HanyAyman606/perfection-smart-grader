import 'dart:convert';
import 'dart:io';
import 'package:flutter/foundation.dart';
import '../../domain/entities/exam_models.dart';
import '../../domain/repositories/scan_engine_repository.dart';
import 'native_cv_bindings.dart';
import '../../domain/repositories/log_repository.dart';
import 'package:path/path.dart' as p;

class CvEngineException implements Exception {
  final String message;
  final CvPipelineError errorType;
  CvEngineException(this.message, {this.errorType = CvPipelineError.unknown});
  @override
  String toString() => message;
}

/// Structured error type so the UI can show the right recovery prompt.
enum CvPipelineError {
  invalidArguments, // FFI returned 1
  pipelineFailed,   // FFI returned 2
  noIdPanel,        // stage1 id_found == false
  noMcqPanel,       // stage1 mcq_found == false
  orientationLowConfidence, // stage3 orientation was a guess
  hasMissingRows,   // stage6 detected fewer rows than expected
  outputMissing,    // summary.json not found on disk after success
  unknown,
}

/// Merged result from the pipeline's `stage6/summary.json`.
///
/// On a clean scan [isOk] is true and all grading fields are populated.
/// On a quality failure [mustRetake] is true and the UI stays on the
/// error/retake screen.
class ProcessExamResult {
  final bool success;
  final bool isOk;
  final List<String> warnings;
  final String orientationReason;
  final String orientationConfidence; // "HIGH" | "LOW"
  final String? errorMessage;
  final CvPipelineError? errorType;

  // Grading data — only present when isOk == true
  final String? studentId;
  final bool idNeedsReview;
  final List<dynamic> questions; // raw JSON list from grading.sheets.<name>.MCQ
  final List<dynamic> idColumns; // raw JSON list from grading.sheets.<name>.ID
  final bool hasMissingRows;

  ProcessExamResult({
    required this.success,
    required this.isOk,
    required this.warnings,
    this.orientationReason = '',
    this.orientationConfidence = 'HIGH',
    this.errorMessage,
    this.errorType,
    this.studentId,
    this.idNeedsReview = false,
    this.questions = const [],
    this.idColumns = const [],
    this.hasMissingRows = false,
  });

  /// Parse the `stage6/summary.json` written by the C++ pipeline.
  ///
  /// Summary schema (as written by stage6_scoring.cpp::write_summary):
  /// {
  ///   "status": "SUCCESS",
  ///   "id_found": bool,
  ///   "mcq_found": bool,
  ///   "orientation_confidence": "HIGH" | "LOW",
  ///   "orientation_reason": string,
  ///   "has_missing_rows": bool,
  ///   "grading": {
  ///     "sheets": {
  ///       "<stem>": {
  ///         "ID": string,        // filled digit+letter string e.g. "C042"
  ///         "MCQ": { "Q1": "A", "Q2": "B", ... },
  ///         "needs_review": [...],
  ///         "warnings": [...]
  ///       }
  ///     }
  ///   }
  /// }
  factory ProcessExamResult.fromSummaryJson(Map<String, dynamic> json) {
    final idFound  = json['id_found']  as bool? ?? false;
    final mcqFound = json['mcq_found'] as bool? ?? false;
    final orientConf   = json['orientation_confidence'] as String? ?? 'HIGH';
    final orientReason = json['orientation_reason']     as String? ?? '';
    final hasMissing   = json['has_missing_rows']       as bool?   ?? false;

    final List<String> warnings = [];
    if (!idFound)  warnings.add('NO_ID_PANEL');
    if (!mcqFound) warnings.add('NO_MCQ_PANEL');
    if (orientConf == 'LOW') warnings.add('ORIENTATION_LOW_CONFIDENCE');
    if (hasMissing) warnings.add('MISSING_ROWS');

    // Quality gate: a scan is only OK when both panels were found.
    // Orientation LOW_CONFIDENCE is a soft warning — we still proceed but warn.
    final isOk = idFound && mcqFound;

    // Pull grading data from the first sheet in grading.sheets.
    String? studentId;
    bool idNeedsReview = false;
    final List<dynamic> questions = [];
    final List<dynamic> idColumns = [];

    final grading = json['grading'] as Map<String, dynamic>?;
    final sheets  = grading?['sheets'] as Map<String, dynamic>?;
    if (sheets != null && sheets.isNotEmpty) {
      final sheetData = sheets.values.first as Map<String, dynamic>?;
      if (sheetData != null) {
        // Build QuestionResult-compatible list from MCQ map { "Q1": "A", ... }
        final mcqMap = sheetData['MCQ'] as Map<String, dynamic>? ?? {};
        int qNum = 1;
        for (final entry in mcqMap.entries) {
          final ans = entry.value as String? ?? 'BLANK';
          final state = (ans == 'BLANK' || ans == 'MULTI' || ans == 'REVIEW')
              ? ans
              : 'ANSWERED';
          questions.add({
            'question_number': qNum,
            'state': state,
            'answer': state == 'ANSWERED' ? ans : null,
          });
          qNum++;
        }

        // Build IdColumnResult-compatible list from the ID string "C042"
        final idStr = sheetData['ID'] as String? ?? '';
        for (int i = 0; i < idStr.length; i++) {
          final ch = idStr[i];
          final isUnknown = ch == '?' || ch == '-';
          if (isUnknown) idNeedsReview = true;
          idColumns.add({
            'column_label': 'col$i',
            'state': isUnknown ? 'MULTIPLE' : 'ANSWERED',
            'answer': isUnknown ? null : ch,
          });
        }
        studentId = idStr.contains('?') || idStr.contains('-') ? null : idStr;

        // Check needs_review array for any flagged bubbles
        final needsReview = sheetData['needs_review'] as List? ?? [];
        if (needsReview.isNotEmpty) idNeedsReview = true;
      }
    }

    return ProcessExamResult(
      success: true,
      isOk: isOk,
      warnings: warnings,
      orientationReason: orientReason,
      orientationConfidence: orientConf,
      studentId: studentId,
      idNeedsReview: idNeedsReview,
      questions: questions,
      idColumns: idColumns,
      hasMissingRows: hasMissing,
    );
  }

  factory ProcessExamResult.error(String message, CvPipelineError type) {
    return ProcessExamResult(
      success: false,
      isOk: false,
      warnings: [],
      errorMessage: message,
      errorType: type,
    );
  }

  /// Whether the UI must force a retake (no continue option).
  bool get mustRetake => !success || !isOk;

  String get primaryWarningMessage {
    if (!success) return errorMessage ?? 'Could not process the photo.';
    if (warnings.contains('NO_ID_PANEL')) {
      return 'Could not find the ID panel — retake with the full sheet visible.';
    }
    if (warnings.contains('NO_MCQ_PANEL')) {
      return 'Could not find the answer panel — retake with the full sheet visible.';
    }
    if (warnings.contains('ORIENTATION_LOW_CONFIDENCE')) {
      return 'Orientation guessed with low confidence — retake if the result looks wrong.';
    }
    if (warnings.contains('MISSING_ROWS')) {
      return 'Some answer rows could not be detected — retake for better accuracy.';
    }
    if (warnings.isNotEmpty) return warnings.first;
    return '';
  }
}

class CvEngineService implements ScanEngineRepository {
  CvEngineService(this._log);

  final LogRepository _log;

  /// Runs the full 6-stage pipeline off the UI isolate, then reads the
  /// merged `stage6/summary.json` back into a [ProcessExamResult].
  @override
  Future<ProcessExamResult> extractPanels({
    required String rawImagePath,
    required String modelPath,
    required MasterPacket masterPacket,
    required String outputDir,
    required String binDir,
  }) async {
    final overallSw = Stopwatch()..start();

    // Write exam_config.json to a temp location (same dir as output to keep
    // it co-located with stage working dirs, consistent with how the C++ FFI
    // layer expects it).
    final imageStem = p.basenameWithoutExtension(rawImagePath);
    final configDir = Directory(p.join(outputDir, '${imageStem}_cfg'));
    await configDir.create(recursive: true);
    final configPath = p.join(configDir.path, 'exam_config.json');
    final configJson = masterPacket.toExamConfigJson(modelPath: modelPath);
    await File(configPath).writeAsString(configJson);

    _log.log('CvEngineService', 'runPipeline config: $configJson');
    _log.log('CvEngineService', 'runPipeline imagePath=$rawImagePath '
        'configPath=$configPath outputDir=$outputDir binDir=$binDir');

    final computeSw = Stopwatch()..start();
    final statusCode = await compute(
      _runPipelineIsolate,
      _PipelineArgs(
        imagePath: rawImagePath,
        configPath: configPath,
        outputDir: outputDir,
        binDir: binDir,
      ),
    );
    final computeMs = computeSw.elapsedMilliseconds;

    overallSw.stop();
    _log.log('CvEngineService',
        'extractPanels compute_ms=$computeMs total_ms=${overallSw.elapsedMilliseconds} statusCode=$statusCode');

    if (statusCode == 1) {
      return ProcessExamResult.error(
        'Pipeline rejected the request — invalid paths or missing files.',
        CvPipelineError.invalidArguments,
      );
    }
    if (statusCode == 2) {
      return ProcessExamResult.error(
        'A stage in the scanning pipeline failed. Try retaking the photo.',
        CvPipelineError.pipelineFailed,
      );
    }

    // statusCode == 0 → read summary.json from disk
    final summaryPath = p.join(outputDir, imageStem, 'stage6', 'summary.json');
    final summaryFile = File(summaryPath);
    if (!await summaryFile.exists()) {
      _log.log('CvEngineService', 'summary.json not found at $summaryPath');
      return ProcessExamResult.error(
        'Pipeline succeeded but output file is missing. Try retaking.',
        CvPipelineError.outputMissing,
      );
    }

    final summaryText = await summaryFile.readAsString();
    _log.log('CvEngineService', 'summary.json: $summaryText');
    final parsed = jsonDecode(summaryText) as Map<String, dynamic>;
    return ProcessExamResult.fromSummaryJson(parsed);
  }

  /// Kept for interface compatibility — all work is done inside [extractPanels].
  @override
  Future<GradeResult> inferAndScore({
    required String idPanelPath,
    required String mcqPanelPath,
    required String modelPath,
    required MasterPacket masterPacket,
    required String selectedVersion,
    String? rawImagePath,
  }) {
    throw UnimplementedError('Use buildGradeResult() with the ProcessExamResult from extractPanels().');
  }

  /// Converts a [ProcessExamResult] (quality+grading payload) into the
  /// [GradeResult] the grading review screen consumes.
  GradeResult buildGradeResult(
    ProcessExamResult examResult,
    MasterPacket masterPacket,
    String selectedVersion,
    String? rawImagePath,
  ) {
    final questions = examResult.questions
        .map((e) => QuestionResult.fromJson(e as Map<String, dynamic>))
        .toList();
    final idColumns = examResult.idColumns
        .map((e) => IdColumnResult.fromJson(e as Map<String, dynamic>))
        .toList();

    final studentIdRaw = examResult.studentId;
    final studentId = (studentIdRaw != null && studentIdRaw.isNotEmpty) ? studentIdRaw : null;

    double mcqScore = 0.0;
    final mistakes = <Mistake>[];
    final modelAnswers = masterPacket.modelAnswers[selectedVersion] ?? {};
    final voided = masterPacket.voidedQuestions[selectedVersion] ?? [];

    for (final q in questions) {
      if (voided.contains(q.questionNumber)) continue;
      final correct = modelAnswers[q.questionNumber.toString()] ?? '';
      if (correct.isEmpty) continue;

      final given = q.state == 'ANSWERED' ? (q.answer as String? ?? '') : q.state;

      if (q.state == 'ANSWERED' && given == correct) {
        final range = masterPacket.rangeForQuestion(q.questionNumber);
        if (range != null) mcqScore += range.points;
      } else {
        mistakes.add(Mistake(question: q.questionNumber, correct: correct, given: given));
      }
    }

    return GradeResult(
      studentId: studentId,
      idSource: studentId == null ? 'manual' : 'ocr',
      answerVersion: selectedVersion,
      mcqScore: mcqScore,
      mistakes: mistakes,
      questions: questions,
      idColumns: idColumns,
      idNeedsReview: examResult.idNeedsReview,
      annotatedIdImagePath: null,
      annotatedMcqImagePath: null,
      rawImagePath: rawImagePath,
      essayTotal: 0.0,
    );
  }
}

class _PipelineArgs {
  final String imagePath;
  final String configPath;
  final String outputDir;
  final String binDir;

  _PipelineArgs({
    required this.imagePath,
    required this.configPath,
    required this.outputDir,
    required this.binDir,
  });
}

int _runPipelineIsolate(_PipelineArgs args) {
  return NativeCvBindings.forCurrentIsolate().callRunPipeline(
    imagePath:  args.imagePath,
    configPath: args.configPath,
    outputDir:  args.outputDir,
    binDir:     args.binDir,
  );
}
