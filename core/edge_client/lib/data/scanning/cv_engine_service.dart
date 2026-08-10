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

/// Step 1 result: confidence/warnings contract straight from the C++
/// engine's QualityInfo (see preprocessing.h / ffi.cpp). "confidence" is
/// the single field the UI branches on for forced-retake vs
/// preview-and-continue; "warnings" gives specific reason codes so the
/// retake screen can say something more useful than "bad photo".
class PanelExtractionResult {
  final bool success;
  final String? idPanelPath;
  final String? mcqPanelPath;
  final bool isOk; // confidence == "OK"
  final List<String> warnings;
  final String orientationReason;
  final double blurScore;
  final String? errorMessage;

  PanelExtractionResult({
    required this.success,
    this.idPanelPath,
    this.mcqPanelPath,
    required this.isOk,
    required this.warnings,
    this.orientationReason = '',
    this.blurScore = 0.0,
    this.errorMessage,
  });

  factory PanelExtractionResult.fromJson(Map<String, dynamic> json) {
    final status = json['status'] as String? ?? 'ERROR';
    return PanelExtractionResult(
      success: status == 'SUCCESS',
      idPanelPath: json['id_panel_path'] as String?,
      mcqPanelPath: json['mcq_panel_path'] as String?,
      isOk: (json['confidence'] as String? ?? 'LOW') == 'OK',
      warnings: (json['warnings'] as List? ?? []).map((e) => e.toString()).toList(),
      orientationReason: json['orientation_reason'] as String? ?? '',
      blurScore: (json['blur_score'] as num?)?.toDouble() ?? 0.0,
      errorMessage: json['message'] as String?,
    );
  }

  /// Whether the UI must force a retake (no continue option at all).
  /// Structural failures (missing panels, processing errors) always force
  /// retake; a plain confidence=LOW without a panel present does too.
  bool get mustRetake =>
      !success || idPanelPath == null || mcqPanelPath == null || !isOk;

  String get primaryWarningMessage {
    if (!success) return errorMessage ?? 'Could not process the photo.';
    if (warnings.contains('BLUR')) return 'Photo looks blurry — hold steady and retake.';
    if (warnings.contains('NO_ID_PANEL')) return 'Could not find the ID panel — retake with the full sheet visible.';
    if (warnings.contains('NO_MCQ_PANEL')) return 'Could not find the answer panel — retake with the full sheet visible.';
    if (warnings.contains('ORIENTATION_LOW_CONFIDENCE')) {
      return 'Orientation guessed with low confidence — double check the preview below.';
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
  /// captured. Must run before Step 1, not just before display.
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

  /// Step 1, off the UI isolate. Returns the confidence/warnings contract
  /// the retake-or-preview screen branches on.
  @override
  Future<PanelExtractionResult> extractPanels({
    required String rawImagePath,
    required String modelPath,
    required MasterPacket masterPacket,
  }) async {
    final orientedPath = await fixExifOrientation(rawImagePath);
    final configJson = masterPacket.toExamConfigJson(modelPath: modelPath);
    _log.log('CvEngineService', 'extractPanels config: $configJson');

    final resultJson = await compute(_runStep1, _Step1Args(orientedPath, configJson));
    _log.log('CvEngineService', 'extractPanels result: $resultJson');
    final parsed = jsonDecode(resultJson) as Map<String, dynamic>;
    return PanelExtractionResult.fromJson(parsed);
  }

  /// Step 2, off the UI isolate. YOLO inference is not instant on a phone
  /// — this is the call most likely to cause a UI hitch if run inline.
  @override
  Future<GradeResult> inferAndScore({
    required String idPanelPath,
    required String mcqPanelPath,
    required String modelPath,
    required MasterPacket masterPacket,
    required String selectedVersion,
    String? rawImagePath,
  }) async {
    final configJson = masterPacket.toExamConfigJson(modelPath: modelPath);
    _log.log('CvEngineService', 'inferAndScore config: $configJson');

    final resultJson = await compute(
      _runStep2,
      _Step2Args(idPanelPath, mcqPanelPath, configJson),
    );
    _log.log('CvEngineService', 'inferAndScore result: $resultJson');
    final raw = jsonDecode(resultJson) as Map<String, dynamic>;

    // Mirror step1's error contract — the C++ side now returns
    // {"status":"ERROR","message":"..."} instead of nullptr on exception.
    if (raw['status'] == 'ERROR') {
      throw CvEngineException(raw['message'] as String? ?? 'Unknown CV engine error in step 2');
    }

    return _buildGradeResult(raw, masterPacket, selectedVersion, rawImagePath);
  }

  GradeResult _buildGradeResult(
    Map<String, dynamic> raw,
    MasterPacket masterPacket,
    String selectedVersion,
    String? rawImagePath,
  ) {
    final questions = (raw['questions'] as List? ?? [])
        .map((e) => QuestionResult.fromJson(e as Map<String, dynamic>))
        .toList();
    final idColumns = (raw['id_columns'] as List? ?? [])
        .map((e) => IdColumnResult.fromJson(e as Map<String, dynamic>))
        .toList();

    final studentIdRaw = raw['student_id'] as String?;
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
      idNeedsReview: raw['id_needs_review'] as bool? ?? false,
      annotatedIdImagePath: raw['annotated_id_image_path'] as String?,
      annotatedMcqImagePath: raw['annotated_mcq_image_path'] as String?,
      rawImagePath: rawImagePath,
      essayTotal: 0.0,
    );
  }
}

// Top-level functions required by compute() — must be static/top-level,
// not instance methods, since they run in a separate isolate with no
// access to `this`.

class _Step1Args {
  final String imagePath;
  final String configJson;
  _Step1Args(this.imagePath, this.configJson);
}

String _runStep1(_Step1Args args) {
  // Runs in a background isolate spawned by compute() — must get its own
  // binding, see NativeCvBindings.forCurrentIsolate().
  return NativeCvBindings.forCurrentIsolate().callStep1(args.imagePath, args.configJson);
}

class _Step2Args {
  final String idPanelPath;
  final String mcqPanelPath;
  final String configJson;
  _Step2Args(this.idPanelPath, this.mcqPanelPath, this.configJson);
}

String _runStep2(_Step2Args args) {
  return NativeCvBindings.forCurrentIsolate()
      .callStep2(args.idPanelPath, args.mcqPanelPath, args.configJson);
}
