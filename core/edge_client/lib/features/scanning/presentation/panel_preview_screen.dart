import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../connection/controller/connection_controller.dart';
import '../controller/scan_controller.dart';
import '../../../data/scanning/cv_engine_service.dart' show ProcessExamResult, CvPipelineError;
import '../../../data/scanning/model_path_service.dart' show PipelinePathService;
import '../../../domain/repositories/scan_engine_repository.dart';
import '../../../domain/repositories/model_path_repository.dart';
import '../../../domain/repositories/session_cache_repository.dart';
import '../../../domain/entities/exam_models.dart';
import '../../grading/presentation/grading_review_screen.dart';

/// Runs the `run_exam_pipeline` scan pipeline on entry.
///
/// Happy path (quality OK): auto-advances straight to GradingReviewScreen
/// with no visible stop — proctor just sees "Processing…" spinner.
///
/// Error path (missing panel, pipeline failure, etc.): stays on this
/// screen showing a specific, actionable message and a full-width Retake
/// button. The 3-strike → manual entry fallback is preserved.
class PanelPreviewScreen extends StatefulWidget {
  final String rawImagePath;
  const PanelPreviewScreen({super.key, required this.rawImagePath});

  @override
  State<PanelPreviewScreen> createState() => _PanelPreviewScreenState();
}

enum _LoadState { error }

class _PanelPreviewScreenState extends State<PanelPreviewScreen> {
  _LoadState? _state;
  ProcessExamResult? _result;
  String _errorTitle   = 'Scan Failed';
  String _errorMessage = 'Something went wrong processing this photo.';

  final DateTime _screenEnteredAt = DateTime.now();

  @override
  void initState() {
    super.initState();
    _runPipeline();
  }

  Future<void> _runPipeline() async {
    setState(() => _state = null); // null == loading

    // Capture context-dependent objects synchronously before the first await.
    final pathRepo = Provider.of<ModelPathRepository>(context, listen: false)
        as PipelinePathService;
    final master = Provider.of<ConnectionController>(context, listen: false).masterPacket!;
    final scanRepo = Provider.of<ScanEngineRepository>(context, listen: false);

    final navToStartGapMs = DateTime.now().difference(_screenEnteredAt).inMilliseconds;
    final pipelineSw = Stopwatch()..start();

    try {
      final modelPathSw = Stopwatch()..start();
      final modelPath = await pathRepo.resolveBubbleModelPath();
      final binDir    = await pathRepo.resolveBinDir();
      final outputDir = await pathRepo.resolveOutputDir();
      final modelPathMs = modelPathSw.elapsedMilliseconds;

      final extractSw = Stopwatch()..start();
      final result = await scanRepo.extractPanels(
        rawImagePath: widget.rawImagePath,
        modelPath:    modelPath,
        masterPacket: master,
        outputDir:    outputDir,
        binDir:       binDir,
      );
      final extractMs = extractSw.elapsedMilliseconds;

      pipelineSw.stop();
      debugPrint(
        '[PanelPreviewScreen] TIMING screen_entered_to_pipeline_start=${navToStartGapMs}ms '
        'resolve_paths=${modelPathMs}ms extractPanels_call=${extractMs}ms '
        'screen_total=${pipelineSw.elapsedMilliseconds}ms',
      );

      if (!mounted) return;

      if (result.mustRetake) {
        setState(() {
          _result = result;
          _state  = _LoadState.error;
          _applyErrorStrings(result);
        });
      } else {
        _result = result;
        await _advance();
      }
    } catch (e) {
      pipelineSw.stop();
      debugPrint('[PanelPreviewScreen] TIMING pipeline failed after ${pipelineSw.elapsedMilliseconds}ms: $e');
      if (!mounted) return;
      setState(() {
        _state        = _LoadState.error;
        _errorTitle   = 'Unexpected Error';
        _errorMessage = 'An unexpected error occurred.\n$e';
      });
    }
  }

  void _applyErrorStrings(ProcessExamResult result) {
    if (!result.success) {
      switch (result.errorType) {
        case CvPipelineError.invalidArguments:
          _errorTitle   = 'Configuration Error';
          _errorMessage = 'The scanner could not find the required files. '
              'Contact your administrator.';
        case CvPipelineError.pipelineFailed:
          _errorTitle   = 'Processing Failed';
          _errorMessage = 'The scanning pipeline failed on this photo.\n'
              'Retake with better lighting and hold the device steady.';
        case CvPipelineError.outputMissing:
          _errorTitle   = 'Output Missing';
          _errorMessage = 'Processing appeared to succeed but no result was '
              'found. Please retake.';
        default:
          _errorTitle   = 'Scan Failed';
          _errorMessage = result.primaryWarningMessage.isEmpty
              ? 'Could not process the photo.'
              : result.primaryWarningMessage;
      }
      return;
    }

    // Quality-gate failures: success==true but isOk==false
    if (result.warnings.contains('NO_ID_PANEL') && result.warnings.contains('NO_MCQ_PANEL')) {
      _errorTitle   = 'Sheet Not Found';
      _errorMessage = 'Neither the ID nor answer panel was detected.\n'
          'Make sure the full answer sheet is in frame and well-lit, then retake.';
    } else if (result.warnings.contains('NO_ID_PANEL')) {
      _errorTitle   = 'ID Panel Missing';
      _errorMessage = 'Could not locate the student ID panel.\n'
          'Ensure the top portion of the sheet is clearly visible, then retake.';
    } else if (result.warnings.contains('NO_MCQ_PANEL')) {
      _errorTitle   = 'Answer Panel Missing';
      _errorMessage = 'Could not locate the MCQ answer panel.\n'
          'Ensure the answer grid is fully in frame, then retake.';
    } else if (result.warnings.contains('MISSING_ROWS')) {
      _errorTitle   = 'Detection Incomplete';
      _errorMessage = 'Some answer rows could not be detected (low quality photo).\n'
          'Hold steady, improve lighting, and retake.';
    } else {
      _errorTitle   = 'Scan Quality Issue';
      _errorMessage = result.primaryWarningMessage.isEmpty
          ? 'Scan quality was too low to process reliably. Please retake.'
          : result.primaryWarningMessage;
    }
  }

  /// Converts the ProcessExamResult into a GradeResult and navigates to
  /// the grading review screen.
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
      debugPrint('[PanelPreviewScreen] TIMING _advance (grade assembly) took ${advanceSw.elapsedMilliseconds}ms');

      if (!mounted) return;
      Navigator.of(context).pushReplacement(
        MaterialPageRoute(builder: (_) => const GradingReviewScreen()),
      );
    } catch (e) {
      if (mounted) {
        setState(() {
          _state        = _LoadState.error;
          _errorTitle   = 'Grading Error';
          _errorMessage = 'Grading failed unexpectedly: $e';
        });
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
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(24.0),
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            _buildErrorIcon(),
            const SizedBox(height: 16),
            Text(
              _errorTitle,
              style: Theme.of(context).textTheme.titleLarge?.copyWith(
                    color: Colors.redAccent,
                    fontWeight: FontWeight.bold,
                  ),
              textAlign: TextAlign.center,
            ),
            const SizedBox(height: 12),
            Text(
              _errorMessage,
              textAlign: TextAlign.center,
              style: Theme.of(context).textTheme.bodyMedium,
            ),
            const SizedBox(height: 32),
            SizedBox(
              width: double.infinity,
              child: ElevatedButton.icon(
                onPressed: _retake,
                icon: const Icon(Icons.camera_alt),
                label: const Text('Retake Photo'),
                style: ElevatedButton.styleFrom(
                  padding: const EdgeInsets.symmetric(vertical: 16),
                  backgroundColor: Colors.redAccent,
                  foregroundColor: Colors.white,
                ),
              ),
            ),
            const SizedBox(height: 12),
            TextButton(
              onPressed: () => Navigator.of(context).pop(),
              child: const Text('Cancel'),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildErrorIcon() {
    final errorType = _result?.errorType;
    if (errorType == CvPipelineError.noIdPanel || errorType == CvPipelineError.noMcqPanel) {
      return const Icon(Icons.document_scanner_outlined, color: Colors.orangeAccent, size: 72);
    }
    if (errorType == CvPipelineError.pipelineFailed || errorType == CvPipelineError.invalidArguments) {
      return const Icon(Icons.warning_amber_rounded, color: Colors.amber, size: 72);
    }
    return const Icon(Icons.error_outline, color: Colors.redAccent, size: 72);
  }
}
