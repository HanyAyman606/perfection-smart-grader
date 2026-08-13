import 'package:flutter/material.dart';
import 'package:flutter/foundation.dart';
import 'package:provider/provider.dart';

import '../../connection/controller/connection_controller.dart';
import '../controller/scan_controller.dart';
import '../../../data/scanning/cv_engine_service.dart' show ProcessExamResult;
import '../../../domain/repositories/scan_engine_repository.dart';
import '../../../domain/repositories/model_path_repository.dart';
import '../../../domain/repositories/session_cache_repository.dart';
import '../../../domain/entities/exam_models.dart';
import '../../grading/presentation/grading_review_screen.dart';

/// Runs the single process_exam_in_memory pipeline on entry.
///
/// Happy path (quality OK): auto-advances straight to GradingReviewScreen
/// with no visible stop — proctor just sees "Processing…" spinner.
///
/// Error path (blur, missing panel, processing error): stays on this screen
/// showing the specific warning message and a full-width Retake button.
/// The retake/manual-entry fallback logic (3-strike → manual entry dialog)
/// is preserved exactly as before.
///
/// TIMING INSTRUMENTATION: added to find where wall-clock time goes
/// between "photo captured" and "result shown", since the native
/// engine's own timing_ms (see ffi.cpp) only covers process_exam_in_memory
/// itself and consistently measures ~1-1.5s -- far less than the ~7s the
/// end-to-end scan feels like. This screen and CvEngineService.extractPanels
/// both log their own phase timings so the full gap can be accounted for:
/// screen-entry-to-pipeline-start (navigation/build overhead), EXIF fix
/// (JPEG decode+rotate+re-encode, Dart-side, NOT covered by native
/// timing_ms), the compute() isolate call overhead, and the native call
/// itself. Look for lines tagged "[PanelPreviewScreen] TIMING" and
/// "extractPanels timing_ms" in the log output (DebugLogScreen or
/// `flutter run`'s console) after a real scan.
class PanelPreviewScreen extends StatefulWidget {
  final String rawImagePath;
  const PanelPreviewScreen({super.key, required this.rawImagePath});

  @override
  State<PanelPreviewScreen> createState() => _PanelPreviewScreenState();
}

enum _LoadState { loading, error }

class _PanelPreviewScreenState extends State<PanelPreviewScreen> {
  _LoadState? _state;
  ProcessExamResult? _result;

  // Marks the moment this screen's State object was actually created
  // (widget pushed onto the navigator) -- compared against when
  // _runPipeline() actually starts doing work, this isolates any stall
  // caused by navigation/screen transition/build overhead rather than
  // CV work itself.
  final DateTime _screenEnteredAt = DateTime.now();

  @override
  void initState() {
    super.initState();
    _runPipeline();
  }

  Future<void> _runPipeline() async {
    setState(() => _state = null); // null == loading

    final navToStartGapMs = DateTime.now().difference(_screenEnteredAt).inMilliseconds;
    final pipelineSw = Stopwatch()..start();

    try {
      final connection = Provider.of<ConnectionController>(context, listen: false);
      final master = connection.masterPacket!;

      final modelPathSw = Stopwatch()..start();
      final modelPath = await Provider.of<ModelPathRepository>(context, listen: false).resolveBubbleModelPath();
      final modelPathMs = modelPathSw.elapsedMilliseconds;

      final extractSw = Stopwatch()..start();
      final result = await Provider.of<ScanEngineRepository>(context, listen: false).extractPanels(
        rawImagePath: widget.rawImagePath,
        modelPath: modelPath,
        masterPacket: master,
      );
      final extractMs = extractSw.elapsedMilliseconds;

      pipelineSw.stop();
      debugPrint(
        '[PanelPreviewScreen] TIMING screen_entered_to_pipeline_start=${navToStartGapMs}ms '
        'resolve_model_path=${modelPathMs}ms extractPanels_call=${extractMs}ms '
        'screen_total=${pipelineSw.elapsedMilliseconds}ms',
      );

      if (!mounted) return;

      if (result.mustRetake) {
        setState(() {
          _result = result;
          _state = _LoadState.error;
        });
      } else {
        _result = result;
        await _advance();
      }
    } catch (e) {
      pipelineSw.stop();
      debugPrint('[PanelPreviewScreen] TIMING pipeline failed after ${pipelineSw.elapsedMilliseconds}ms: $e');
      if (!mounted) return;
      setState(() => _state = _LoadState.error);
    }
  }

  /// Converts the ProcessExamResult into a GradeResult and navigates to
  /// the grading review screen. No extra FFI call needed — all grading
  /// data came back in the single process_exam_in_memory response.
  Future<void> _advance() async {
    final advanceSw = Stopwatch()..start();
    try {
      final connection = Provider.of<ConnectionController>(context, listen: false);
      final master = connection.masterPacket!;
      final version = connection.selectedVersion ?? master.answerVersions.first;

      final examResult = _result!;
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
      final modelAnswers = master.modelAnswers[version] ?? {};
      final voided = master.voidedQuestions[version] ?? [];

      for (final q in questions) {
        if (voided.contains(q.questionNumber)) continue;
        final correct = modelAnswers[q.questionNumber.toString()] ?? '';
        if (correct.isEmpty) continue;
        final given = q.state == 'ANSWERED' ? (q.answer as String? ?? '') : q.state;
        if (q.state == 'ANSWERED' && given == correct) {
          final range = master.rangeForQuestion(q.questionNumber);
          if (range != null) mcqScore += range.points;
        } else {
          mistakes.add(Mistake(question: q.questionNumber, correct: correct, given: given));
        }
      }

      final gradeResult = GradeResult(
        studentId: studentId,
        idSource: studentId == null ? 'manual' : 'ocr',
        answerVersion: version,
        mcqScore: mcqScore,
        mistakes: mistakes,
        questions: questions,
        idColumns: idColumns,
        idNeedsReview: examResult.idNeedsReview,
        rawImagePath: widget.rawImagePath,
        essayTotal: 0.0,
      );

      if (!mounted) return;
      Provider.of<ScanController>(context, listen: false).setCurrentScan(gradeResult);

      advanceSw.stop();
      debugPrint('[PanelPreviewScreen] TIMING _advance (grade assembly, no CV work) took ${advanceSw.elapsedMilliseconds}ms');

      if (!mounted) return;
      Navigator.of(context).pushReplacement(
        MaterialPageRoute(builder: (_) => const GradingReviewScreen()),
      );
    } catch (e) {
      if (mounted) {
        setState(() => _state = _LoadState.error);
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Grading failed: $e'), duration: const Duration(seconds: 5)),
        );
      }
    }
  }

  Future<void> _retake() async {
    final scan = Provider.of<ScanController>(context, listen: false);
    scan.retryCounter.recordRetake();

    await Provider.of<SessionCacheRepository>(context, listen: false).discardFailedAttempt([
      widget.rawImagePath,
    ]);

    if (!mounted) return;

    if (scan.retryCounter.shouldOfferManualEntry) {
      _offerManualEntryFallback();
      return;
    }

    Navigator.of(context).pop(); // back to ScannerView to capture again
  }

  void _offerManualEntryFallback() {
    showDialog(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Having trouble scanning?'),
        content: const Text(
          "This sheet has failed to scan 3 times. You can keep retaking, "
          "or enter this student's grade manually instead.",
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(context).pop(),
            child: const Text('Keep trying'),
          ),
          TextButton(
            onPressed: () {
              Navigator.of(context).pop(); // close dialog
              Provider.of<ScanController>(context, listen: false).retryCounter.reset();
              Navigator.of(context).pop(); // back to ScannerView
              Navigator.of(context).push(
                MaterialPageRoute(builder: (_) => const GradingReviewScreen(manualEntry: true)),
              );
            },
            child: const Text('Enter manually'),
          ),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Processing…'), automaticallyImplyLeading: false),
      body: _state == _LoadState.error ? _buildErrorBody() : const Center(child: CircularProgressIndicator()),
    );
  }

  Widget _buildErrorBody() {
    final warningMsg = _result?.primaryWarningMessage ?? 'Something went wrong processing this photo.';

    return Center(
      child: Padding(
        padding: const EdgeInsets.all(24.0),
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            const Icon(Icons.error_outline, color: Colors.redAccent, size: 64),
            const SizedBox(height: 16),
            Text(warningMsg, textAlign: TextAlign.center),
            const SizedBox(height: 24),
            SizedBox(
              width: double.infinity,
              child: ElevatedButton.icon(
                onPressed: _retake,
                icon: const Icon(Icons.camera_alt),
                label: const Text('Retake'),
                style: ElevatedButton.styleFrom(padding: const EdgeInsets.symmetric(vertical: 16)),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
