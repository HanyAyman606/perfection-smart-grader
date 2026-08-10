import 'dart:io';
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../connection/controller/connection_controller.dart';
import '../controller/scan_controller.dart';
import '../../../data/scanning/cv_engine_service.dart' show PanelExtractionResult;
import '../../../domain/repositories/scan_engine_repository.dart';
import '../../../domain/repositories/model_path_repository.dart';
import '../../../domain/repositories/session_cache_repository.dart';
import '../../grading/presentation/grading_review_screen.dart';

/// Runs Step 1 (extract + confidence-score panels) on entry, then shows
/// the proctor a stacked, full-width, pinch-zoomable preview of the ID
/// and MCQ crops. Stacked rather than side-by-side so neither crop loses
/// legibility to a halved-width layout — the whole point of this screen
/// is letting the proctor actually read small print before committing.
///
/// If confidence is LOW (blur, missing panel, or a processing error),
/// Continue is disabled and only Retake is offered — matches the
/// binary retake-vs-continue design with no silent low-quality path
/// through to grading.
class PanelPreviewScreen extends StatefulWidget {
  final String rawImagePath;
  const PanelPreviewScreen({super.key, required this.rawImagePath});

  @override
  State<PanelPreviewScreen> createState() => _PanelPreviewScreenState();
}

enum _LoadState { loading, ready, error }

class _PanelPreviewScreenState extends State<PanelPreviewScreen> {
  _LoadState _state = _LoadState.loading;
  PanelExtractionResult? _result;
  bool _isAdvancing = false;

  @override
  void initState() {
    super.initState();
    _runStep1();
  }

  Future<void> _runStep1() async {
    setState(() => _state = _LoadState.loading);

    try {
      final connection = Provider.of<ConnectionController>(context, listen: false);
      final master = connection.masterPacket!;
      final modelPath = await Provider.of<ModelPathRepository>(context, listen: false).resolveBubbleModelPath();

      final result = await Provider.of<ScanEngineRepository>(context, listen: false).extractPanels(
        rawImagePath: widget.rawImagePath,
        modelPath: modelPath,
        masterPacket: master,
      );

      if (!mounted) return;
      setState(() {
        _result = result;
        _state = _LoadState.ready;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() => _state = _LoadState.error);
    }
  }

  Future<void> _retake() async {
    final scan = Provider.of<ScanController>(context, listen: false);
    scan.retryCounter.recordRetake();

    await Provider.of<SessionCacheRepository>(context, listen: false).discardFailedAttempt([
      widget.rawImagePath,
      _result?.idPanelPath,
      _result?.mcqPanelPath,
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

  Future<void> _continue() async {
    if (_isAdvancing) return;
    setState(() => _isAdvancing = true);

    try {
      final connection = Provider.of<ConnectionController>(context, listen: false);
      final master = connection.masterPacket!;
      final version = connection.selectedVersion ?? master.answerVersions.first;
      final modelPath = await Provider.of<ModelPathRepository>(context, listen: false).resolveBubbleModelPath();

      final gradeResult = await Provider.of<ScanEngineRepository>(context, listen: false).inferAndScore(
        idPanelPath: _result!.idPanelPath!,
        mcqPanelPath: _result!.mcqPanelPath!,
        modelPath: modelPath,
        masterPacket: master,
        selectedVersion: version,
        rawImagePath: widget.rawImagePath,
      );

      if (!mounted) return;
      Provider.of<ScanController>(context, listen: false).setCurrentScan(gradeResult);

      if (!mounted) return;
      Navigator.of(context).pushReplacement(
        MaterialPageRoute(builder: (_) => const GradingReviewScreen()),
      );
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Grading failed: $e'), duration: const Duration(seconds: 5)),
        );
      }
    } finally {
      if (mounted) setState(() => _isAdvancing = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Review scan'), automaticallyImplyLeading: false),
      body: switch (_state) {
        _LoadState.loading => const Center(child: CircularProgressIndicator()),
        _LoadState.error => _buildErrorBody(),
        _LoadState.ready => _buildReadyBody(),
      },
    );
  }

  Widget _buildErrorBody() {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(24.0),
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            const Icon(Icons.error_outline, color: Colors.redAccent, size: 64),
            const SizedBox(height: 16),
            const Text('Something went wrong processing this photo.', textAlign: TextAlign.center),
            const SizedBox(height: 24),
            ElevatedButton.icon(
              onPressed: _retake,
              icon: const Icon(Icons.camera_alt),
              label: const Text('Retake'),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildReadyBody() {
    final result = _result!;
    final mustRetake = result.mustRetake;
    final warningMsg = result.primaryWarningMessage;

    return Column(
      children: [
        if (warningMsg.isNotEmpty)
          Container(
            width: double.infinity,
            color: mustRetake ? Colors.red.shade900 : Colors.orange.shade900,
            padding: const EdgeInsets.symmetric(vertical: 12, horizontal: 16),
            child: Row(
              children: [
                Icon(mustRetake ? Icons.block : Icons.warning_amber, color: Colors.white, size: 20),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(warningMsg, style: const TextStyle(color: Colors.white)),
                ),
              ],
            ),
          ),
        Expanded(
          child: SingleChildScrollView(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                if (result.idPanelPath != null) ...[
                  const Text('Student ID panel', style: TextStyle(fontWeight: FontWeight.bold)),
                  const SizedBox(height: 8),
                  _ZoomableCrop(imagePath: result.idPanelPath!),
                  const SizedBox(height: 24),
                ],
                if (result.mcqPanelPath != null) ...[
                  const Text('Answer panel', style: TextStyle(fontWeight: FontWeight.bold)),
                  const SizedBox(height: 8),
                  _ZoomableCrop(imagePath: result.mcqPanelPath!),
                ],
                if (result.idPanelPath == null && result.mcqPanelPath == null)
                  const Padding(
                    padding: EdgeInsets.symmetric(vertical: 48),
                    child: Center(child: Text('No panels could be detected in this photo.')),
                  ),
              ],
            ),
          ),
        ),
        SafeArea(
          top: false,
          child: Padding(
            padding: const EdgeInsets.all(16.0),
            child: Row(
              children: [
                Expanded(
                  child: OutlinedButton.icon(
                    onPressed: _isAdvancing ? null : _retake,
                    icon: const Icon(Icons.refresh),
                    label: const Text('Retake'),
                    style: OutlinedButton.styleFrom(padding: const EdgeInsets.symmetric(vertical: 16)),
                  ),
                ),
                const SizedBox(width: 16),
                Expanded(
                  child: ElevatedButton.icon(
                    onPressed: (mustRetake || _isAdvancing) ? null : _continue,
                    icon: _isAdvancing
                        ? const SizedBox(
                            width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2))
                        : const Icon(Icons.check_circle),
                    label: Text(_isAdvancing ? 'Processing...' : 'Continue'),
                    style: ElevatedButton.styleFrom(
                      padding: const EdgeInsets.symmetric(vertical: 16),
                      backgroundColor: mustRetake ? Colors.grey : Colors.green,
                    ),
                  ),
                ),
              ],
            ),
          ),
        ),
      ],
    );
  }
}

class _ZoomableCrop extends StatelessWidget {
  final String imagePath;
  const _ZoomableCrop({required this.imagePath});

  @override
  Widget build(BuildContext context) {
    return ClipRRect(
      borderRadius: BorderRadius.circular(8),
      child: Container(
        decoration: BoxDecoration(border: Border.all(color: Colors.grey.shade700)),
        child: InteractiveViewer(
          maxScale: 5,
          child: Image.file(File(imagePath), fit: BoxFit.contain, width: double.infinity),
        ),
      ),
    );
  }
}
