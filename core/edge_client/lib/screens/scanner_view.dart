import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:camera/camera.dart';
import 'package:provider/provider.dart';

import '../providers/exam_session_provider.dart';
import '../services/alignment_heuristic.dart';
import '../widgets/scanner_guide_painter.dart';
import '../services/omr_service.dart';
import 'grading_review_screen.dart';
import 'calibration_screen.dart'; // Needed for routing

class ScannerView extends StatefulWidget {
  const ScannerView({super.key});

  @override
  State<ScannerView> createState() => _ScannerViewState();
}

class _ScannerViewState extends State<ScannerView> {
  CameraController? _controller;
  bool _isAligned = false;
  bool _isProcessing = false;

  @override
  void initState() {
    super.initState();
    SystemChrome.setPreferredOrientations([
      DeviceOrientation.landscapeLeft,
      DeviceOrientation.landscapeRight,
    ]);
    _initCamera();
  }

  Future<void> _initCamera() async {
    final cameras = await availableCameras();
    if (cameras.isEmpty) return;

    final backCamera = cameras.firstWhere(
      (c) => c.lensDirection == CameraLensDirection.back,
      orElse: () => cameras.first,
    );

    _controller = CameraController(
      backCamera,
      ResolutionPreset.high,
      enableAudio: false,
      imageFormatGroup: ImageFormatGroup.yuv420,
    );
    await _controller!.initialize();
    if (mounted) {
      setState(() {});
      _controller!.startImageStream(_processCameraFrame);
    }
  }

  void _processCameraFrame(CameraImage image) {
    if (_isProcessing) return;
    final aligned = AlignmentHeuristic.looksAligned(image);
    if (aligned != _isAligned) {
      if (mounted) setState(() { _isAligned = aligned; });
    }
  }

  void _triggerRecalibration() {
    showDialog(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Recalibrate Template?'),
        content: const Text('Recalibrating won\'t affect scores you\'ve already submitted. Continue?'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context), child: const Text('Cancel')),
          TextButton(
            onPressed: () {
              Navigator.pop(context); // Close dialog
              Navigator.of(context).pushReplacement(
                MaterialPageRoute(builder: (_) => const CalibrationScreen()),
              );
            },
            child: const Text('Recalibrate', style: TextStyle(color: Colors.red)),
          ),
        ],
      ),
    );
  }

  Future<void> _captureAndGrade() async {
    if (_controller == null || !_controller!.value.isInitialized) return;
    if (_isProcessing) return;

    setState(() { _isProcessing = true; });

    try {
      await _controller!.stopImageStream();
      await Future.delayed(const Duration(milliseconds: 200));

      final photo = await _controller!.takePicture();
      final session = Provider.of<ExamSessionProvider>(context, listen: false);
      final master = session.masterPacket!;
      final version = session.selectedVersion ?? master.answerVersions.first;

      final result = await OmrService.instance.runGrading(
        imagePath: photo.path,
        masterPacket: master,
        selectedVersion: version,
      );

      session.setCurrentScan(result);

      if (mounted) {
        Navigator.of(context).push(
          MaterialPageRoute(builder: (_) => const GradingReviewScreen()),
        ).then((_) {
          if (mounted) {
            _controller?.dispose();
            _initCamera();
          }
        });
      }
    } catch (e) {
      if (mounted) {
        final String message;
        if (e is OmrEngineException && e.needsRetake) {
          message = "Couldn't align the sheet — please retake the photo.";
        } else if (e is OmrEngineException && e.needsRecalibration) {
          message = "This template needs to be recalibrated before scanning can continue.";
        } else {
          message = 'Grading failed: $e';
        }
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(message), duration: const Duration(seconds: 5)));
        _controller?.startImageStream(_processCameraFrame);
      }
    } finally {
      if (mounted) setState(() { _isProcessing = false; });
    }
  }

  @override
  void dispose() {
    SystemChrome.setPreferredOrientations([
      DeviceOrientation.portraitUp, DeviceOrientation.portraitDown,
      DeviceOrientation.landscapeLeft, DeviceOrientation.landscapeRight,
    ]);
    _controller?.stopImageStream();
    _controller?.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    if (_controller == null || !_controller!.value.isInitialized) {
      return const Scaffold(body: Center(child: CircularProgressIndicator()));
    }

    final guideColor = _isAligned ? Colors.green : Colors.redAccent;
    final session = context.watch<ExamSessionProvider>();

    final media = MediaQuery.of(context).size;
    final isPortrait = media.height > media.width;
    final guideHeight = media.height * 0.82;
    final guideWidth = guideHeight * session.fiducialRatio;

    return Scaffold(
      appBar: AppBar(
        title: Text('Scanning: ${session.masterPacket?.examName ?? ""}'),
        actions: [
          IconButton(
            icon: const Icon(Icons.tune),
            tooltip: 'Recalibrate Template',
            onPressed: _triggerRecalibration,
          ),
          if (session.masterPacket != null && session.masterPacket!.supportsMultipleVersions)
            DropdownButton<String>(
              value: session.selectedVersion,
              dropdownColor: Colors.grey[850],
              items: session.masterPacket!.answerVersions.map((v) {
                return DropdownMenuItem(value: v, child: Text('Version $v'));
              }).toList(),
              onChanged: (val) {
                if (val != null) session.selectVersion(val);
              },
            ),
        ],
      ),
      body: Stack(
        children: [
          Positioned.fill(child: CameraPreview(_controller!)),
          if (isPortrait)
            Positioned.fill(
              child: Container(
                color: Colors.red.withOpacity(0.8),
                alignment: Alignment.center,
                child: const Text(
                  'Please hold your phone sideways\n(Landscape) to scan.',
                  textAlign: TextAlign.center,
                  style: TextStyle(color: Colors.white, fontSize: 24, fontWeight: FontWeight.bold),
                ),
              ),
            ),
          if (!isPortrait) ...[
            Positioned.fill(
              child: CustomPaint(
                painter: ScannerGuidePainter(
                  cutoutSize: Size(guideWidth, guideHeight),
                  bracketColor: guideColor,
                ),
              ),
            ),
            if (_isProcessing)
              const Positioned.fill(child: Center(child: CircularProgressIndicator())),
            Positioned(
              bottom: 40, left: 0, right: 0,
              child: Center(
                child: FloatingActionButton(
                  onPressed: _isProcessing ? null : _captureAndGrade,
                  backgroundColor: _isAligned ? Colors.green : Colors.grey,
                  child: const Icon(Icons.camera),
                ),
              ),
            ),
          ],
        ],
      ),
    );
  }
}
