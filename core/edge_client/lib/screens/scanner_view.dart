<<<<<<< HEAD
// Camera scanner UI
=======
import 'package:flutter/material.dart';
import 'package:camera/camera.dart';
import 'package:provider/provider.dart';

import '../providers/exam_session_provider.dart';
import '../services/alignment_heuristic.dart';
import '../services/omr_service.dart';
import 'grading_review_screen.dart';

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
      if (mounted) {
        setState(() {
          _isAligned = aligned;
        });
      }
    }
  }

  Future<void> _captureAndGrade() async {
    if (_controller == null || !_controller!.value.isInitialized) return;
    if (_isProcessing) return;

    setState(() {
      _isProcessing = true;
    });

    try {
      await _controller!.stopImageStream();
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
          // Restart stream when coming back
          if (mounted && _controller != null) {
            _controller!.startImageStream(_processCameraFrame);
          }
        });
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Grading failed: $e')),
        );
        _controller?.startImageStream(_processCameraFrame);
      }
    } finally {
      if (mounted) {
        setState(() {
          _isProcessing = false;
        });
      }
    }
  }

  @override
  void dispose() {
    _controller?.stopImageStream();
    _controller?.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    if (_controller == null || !_controller!.value.isInitialized) {
      return const Scaffold(
        body: Center(child: CircularProgressIndicator()),
      );
    }

    final guideColor = _isAligned ? Colors.green : Colors.redAccent;
    final session = context.watch<ExamSessionProvider>();

    // Guide frame: landscape default (width > height).
    // Uses 1.414 landscape ratio (A4 sideways) as a default before calibration.
    // TODO: Once calibrated, derive the exact aspect ratio from the calibration
    // reference photo's real dimensions via image_size.dart.
    final guideWidth = MediaQuery.of(context).size.width * 0.9;
    final guideHeight = guideWidth / 1.414; // Landscape: wider than tall

    return Scaffold(
      appBar: AppBar(
        title: Text('Scanning: ${session.masterPacket?.examName ?? ""}'),
        actions: [
          if (session.masterPacket != null && session.masterPacket!.supportsMultipleVersions)
            DropdownButton<String>(
              value: session.selectedVersion,
              dropdownColor: Colors.grey[850],
              items: session.masterPacket!.answerVersions.map((v) {
                return DropdownMenuItem(
                  value: v,
                  child: Text('Version $v'),
                );
              }).toList(),
              onChanged: (val) {
                if (val != null) session.selectVersion(val);
              },
            ),
        ],
      ),
      body: Stack(
        children: [
          Positioned.fill(
            child: CameraPreview(_controller!),
          ),
          Center(
            child: Container(
              width: guideWidth,
              height: guideHeight,
              decoration: BoxDecoration(
                border: Border.all(color: guideColor, width: 4),
                borderRadius: BorderRadius.circular(8),
              ),
            ),
          ),
          if (_isProcessing)
            const Positioned.fill(
              child: Center(
                child: CircularProgressIndicator(),
              ),
            ),
          Positioned(
            bottom: 40,
            left: 0,
            right: 0,
            child: Center(
              child: FloatingActionButton(
                onPressed: _isProcessing ? null : _captureAndGrade,
                backgroundColor: _isAligned ? Colors.green : Colors.grey,
                child: const Icon(Icons.camera),
              ),
            ),
          ),
        ],
      ),
    );
  }
}
>>>>>>> af9284c712fd3317b7fecb1c4c6ba726ec81c9a6
