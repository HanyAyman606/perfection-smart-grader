import 'dart:convert';
import 'dart:io';
import 'package:flutter/foundation.dart';
import 'package:image/image.dart' as img;
import '../../domain/entities/exam_models.dart';
import '../../domain/repositories/scan_engine_repository.dart';
import 'native_cv_bindings.dart';
import '../../domain/repositories/log_repository.dart';

class CvEngineException implements Exception {
  final String message;
  CvEngineException(this.message);
  @override
  String toString() => message;
}

/// The merged response from process_exam_in_memory.
///
/// On a clean scan quality.is_ok is true and the grading fields
/// (questions, id_columns, student_id, …) are also populated in the
/// same JSON payload — no second call needed.
///
/// On a bad-quality scan (BLUR, NO_ID_PANEL, …) the engine short-circuits
/// before inference and returns confidence=LOW with warnings but no
/// grading data. mustRetake drives the Dart retake-or-continue branch.
class ProcessExamResult {
  final bool success;
  final bool isOk; // confidence == "OK"
  final List<String> warnings;
  final String orientationReason;
  final double blurScore;
  final String? errorMessage;

  // Grading data — only present when isOk == true
  final String? studentId;
  final String? studentIdLetter;
  final bool idNeedsReview;
  final List<dynamic> questions; // raw JSON list, parsed in _buildGradeResult
  final List<dynamic> idColumns; // raw JSON list, parsed in _buildGradeResult
  final bool hasMissingRows;

  ProcessExamResult({
    required this.success,
    required this.isOk,
    required this.warnings,
    this.orientationReason = '',
    this.blurScore = 0.0,
    this.errorMessage,
    this.studentId,
    this.studentIdLetter,
    this.idNeedsReview = false,
    this.questions = const [],
    this.idColumns = const [],
    this.hasMissingRows = false,
  });

  factory ProcessExamResult.fromJson(Map<String, dynamic> json) {
    final status = json['status'] as String? ?? 'ERROR';
    return ProcessExamResult(
      success: status == 'SUCCESS',
      isOk: (json['confidence'] as String? ?? 'LOW') == 'OK',
      warnings: (json['warnings'] as List? ?? []).map((e) => e.toString()).toList(),
      orientationReason: json['orientation_reason'] as String? ?? '',
      blurScore: (json['blur_score'] as num?)?.toDouble() ?? 0.0,
      errorMessage: json['message'] as String?,
      studentId: json['student_id'] as String?,
      studentIdLetter: json['student_id_letter'] as String?,
      idNeedsReview: json['id_needs_review'] as bool? ?? false,
      questions: json['questions'] as List? ?? [],
      idColumns: json['id_columns'] as List? ?? [],
      hasMissingRows: json['has_missing_rows'] as bool? ?? false,
    );
  }

  /// Whether the UI must force a retake (no continue option).
  bool get mustRetake => !success || !isOk;

  String get primaryWarningMessage {
    if (!success) return errorMessage ?? 'Could not process the photo.';
    if (warnings.contains('BLUR')) return 'Photo looks blurry — hold steady and retake.';
    if (warnings.contains('NO_ID_PANEL')) return 'Could not find the ID panel — retake with the full sheet visible.';
    if (warnings.contains('NO_MCQ_PANEL')) return 'Could not find the answer panel — retake with the full sheet visible.';
    if (warnings.contains('ORIENTATION_LOW_CONFIDENCE')) {
      return 'Orientation guessed with low confidence — retake if the result looks wrong.';
    }
    if (warnings.isNotEmpty) return warnings.first;
    return '';
  }
}

class CvEngineService implements ScanEngineRepository {
  CvEngineService(this._log);

  final LogRepository _log;

  /// Fixes EXIF rotation by physically rotating pixels and stripping EXIF
  /// orientation, so the C++ side (which reads raw pixel buffers via
  /// OpenCV, not EXIF-aware) sees the photo right-side-up exactly as
  /// captured. Must run before the engine call, not just before display.
  Future<String> fixExifOrientation(String originalPath) async {
    final bytes = await File(originalPath).readAsBytes();
    final image = img.decodeJpg(bytes);
    if (image == null) return originalPath;

    final orientedImage = img.bakeOrientation(image);
    orientedImage.exif.clear();

    final fixedBytes = img.encodeJpg(orientedImage);
    final newPath = originalPath.replaceAll('.jpg', '_oriented.jpg');
    await File(newPath).writeAsBytes(fixedBytes);
    return newPath;
  }

  /// Single-step pipeline: perspective-correct + crop panels + YOLO
  /// inference + scoring, all in one native call off the UI isolate.
  ///
  /// Returns a [ProcessExamResult] containing both the quality signal and
  /// the full grading payload. The caller checks [mustRetake] to decide
  /// whether to show an error screen or go straight to grading review.
  @override
  Future<ProcessExamResult> extractPanels({
    required String rawImagePath,
    required String modelPath,
    required MasterPacket masterPacket,
  }) async {
    final orientedPath = await fixExifOrientation(rawImagePath);
    final configJson = masterPacket.toExamConfigJson(modelPath: modelPath);
    _log.log('CvEngineService', 'processExam config: $configJson');

    final resultJson = await compute(_runProcessExam, _ProcessExamArgs(orientedPath, configJson));
    _log.log('CvEngineService', 'processExam result: $resultJson');
    final parsed = jsonDecode(resultJson) as Map<String, dynamic>;

    if (parsed['status'] == 'ERROR') {
      throw CvEngineException(parsed['message'] as String? ?? 'Unknown CV engine error');
    }

    return ProcessExamResult.fromJson(parsed);
  }

  /// Builds a [GradeResult] from a successful [ProcessExamResult].
  /// Called by panel_preview_screen after a clean quality check.
  @override
  Future<GradeResult> inferAndScore({
    required String idPanelPath,
    required String mcqPanelPath,
    required String modelPath,
    required MasterPacket masterPacket,
    required String selectedVersion,
    String? rawImagePath,
  }) {
    // This method is kept to satisfy the ScanEngineRepository interface.
    // The actual work is done inside extractPanels() via process_exam_in_memory.
    // panel_preview_screen.dart now calls buildGradeResult() directly instead.
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

// Top-level functions required by compute() — must be static/top-level,
// not instance methods, since they run in a separate isolate with no
// access to `this`.

class _ProcessExamArgs {
  final String imagePath;
  final String configJson;
  _ProcessExamArgs(this.imagePath, this.configJson);
}

String _runProcessExam(_ProcessExamArgs args) {
  // Runs in a background isolate spawned by compute() — must get its own
  // binding, see NativeCvBindings.forCurrentIsolate().
  return NativeCvBindings.forCurrentIsolate().callProcessExam(args.imagePath, args.configJson);
}
