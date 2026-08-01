import 'dart:io';
import 'dart:ui' as ui;
import 'package:flutter/material.dart';
import 'package:camera/camera.dart';
import 'package:provider/provider.dart';

import '../providers/exam_session_provider.dart';
import '../services/calibration_service.dart';
import '../utils/image_size.dart';
import 'scanner_view.dart';

/// Two-phase calibration screen:
/// Phase 1: Live camera preview → proctor captures a photo of the reference sheet.
/// Phase 2: Captured photo shown full-screen → proctor taps 4 anchor points:
///   Tap 1: ID block — top-left corner of first ID bubble
///   Tap 2: ID block — bottom-right corner of last ID bubble
///   Tap 3: Answers block — top-left corner of Q1 option A
///   Tap 4: Answers block — bottom-right corner of last question's last option
/// Taps are remapped from display coordinates to real pixel space.
class CalibrationScreen extends StatefulWidget {
  const CalibrationScreen({super.key});

  @override
  State<CalibrationScreen> createState() => _CalibrationScreenState();
}

enum _CalibPhase { camera, tapping }

class _CalibrationScreenState extends State<CalibrationScreen> {
  CameraController? _controller;
  bool _isCalibrating = false;

  // Phase 2 state
  _CalibPhase _phase = _CalibPhase.camera;
  String? _capturedPhotoPath;
  ui.Size? _naturalImageSize; // Real pixel dimensions of the captured photo
  final List<Offset> _taps = []; // Display-space tap coordinates (up to 4)

  static const _tapLabels = [
    'Tap 1/4: Top-left of ID bubble grid',
    'Tap 2/4: Bottom-right of ID bubble grid',
    'Tap 3/4: Top-left of Q1 option A',
    'Tap 4/4: Bottom-right of last question\'s last option',
  ];

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
    );
    await _controller!.initialize();
    if (mounted) setState(() {});
  }

  @override
  void dispose() {
    _controller?.dispose();
    super.dispose();
  }

  Future<void> _captureReference() async {
    if (_controller == null || !_controller!.value.isInitialized) return;

    final photo = await _controller!.takePicture();
    final imageSize = await ImageSize.decodeImageSize(photo.path);

    setState(() {
      _capturedPhotoPath = photo.path;
      _naturalImageSize = imageSize;
      _phase = _CalibPhase.tapping;
      _taps.clear();
    });
  }

  void _onImageTap(TapDownDetails details, BoxConstraints constraints) {
    if (_taps.length >= 4) return;

    setState(() {
      _taps.add(details.localPosition);
    });

    if (_taps.length == 4) {
      // All 4 anchors collected — run calibration
      _runCalibration(constraints);
    }
  }

  void _undoLastTap() {
    if (_taps.isNotEmpty) {
      setState(() {
        _taps.removeLast();
      });
    }
  }

  void _retakePhoto() {
    setState(() {
      _phase = _CalibPhase.camera;
      _capturedPhotoPath = null;
      _naturalImageSize = null;
      _taps.clear();
    });
  }

  /// Remap a display-space tap coordinate to the photo's real pixel space.
  Offset _remapToPixelSpace(Offset displayTap, Size displaySize) {
    final scaleX = _naturalImageSize!.width / displaySize.width;
    final scaleY = _naturalImageSize!.height / displaySize.height;
    return Offset(displayTap.dx * scaleX, displayTap.dy * scaleY);
  }

  Future<void> _runCalibration(BoxConstraints constraints) async {
    if (_isCalibrating) return;
    setState(() { _isCalibrating = true; });

    try {
      final session = Provider.of<ExamSessionProvider>(context, listen: false);
      final master = session.masterPacket!;

      final displaySize = Size(constraints.maxWidth, constraints.maxHeight);

      // Remap all 4 taps to real pixel coordinates
      final idTopLeft = _remapToPixelSpace(_taps[0], displaySize);
      final idBottomRight = _remapToPixelSpace(_taps[1], displaySize);
      final ansTopLeft = _remapToPixelSpace(_taps[2], displaySize);
      final ansBottomRight = _remapToPixelSpace(_taps[3], displaySize);

      final idBlock = {
        "name": "id",
        "x1": idTopLeft.dx.toInt(),
        "y1": idTopLeft.dy.toInt(),
        "x2": idBottomRight.dx.toInt(),
        "y2": idBottomRight.dy.toInt(),
      };

      final ansBlock = {
        "name": "answers",
        "x1": ansTopLeft.dx.toInt(),
        "y1": ansTopLeft.dy.toInt(),
        "x2": ansBottomRight.dx.toInt(),
        "y2": ansBottomRight.dy.toInt(),
      };

      final result = await CalibrationService.instance.calibrate(
        rawImagePath: _capturedPhotoPath!,
        numQuestions: master.mcqCount,
        choicesPerQuestion: master.choicesPerQuestion,
        questionsPerBlock: master.questionsPerBlock,
        idLetterCount: master.idLetterCount,
        idDigitColumns: master.idDigitColumns,
        idBlockClicks: idBlock,
        answersBlockClicks: ansBlock,
      );

      if (result['success'] == true) {
        session.markCalibrated();
        if (mounted) {
          Navigator.of(context).pushReplacement(
            MaterialPageRoute(builder: (_) => const ScannerView()),
          );
        }
      } else {
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(content: Text('Calibration failed: ${result['error']}')),
          );
          // Let them re-tap
          setState(() { _taps.clear(); });
        }
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Error: $e')),
        );
        setState(() { _taps.clear(); });
      }
    } finally {
      if (mounted) {
        setState(() { _isCalibrating = false; });
      }
    }
  }

  // ======================= BUILD =======================

  @override
  Widget build(BuildContext context) {
    if (_phase == _CalibPhase.camera) {
      return _buildCameraPhase();
    } else {
      return _buildTappingPhase();
    }
  }

  Widget _buildCameraPhase() {
    if (_controller == null || !_controller!.value.isInitialized) {
      return const Scaffold(body: Center(child: CircularProgressIndicator()));
    }

    return Scaffold(
      appBar: AppBar(title: const Text('Calibrate — Capture Reference')),
      body: Stack(
        children: [
          Positioned.fill(child: CameraPreview(_controller!)),
          // Landscape guide overlay
          Center(
            child: Container(
              width: MediaQuery.of(context).size.width * 0.9,
              height: MediaQuery.of(context).size.width * 0.9 / 1.414,
              decoration: BoxDecoration(
                border: Border.all(color: Colors.blueAccent, width: 3),
                color: Colors.blueAccent.withOpacity(0.08),
              ),
            ),
          ),
          Positioned(
            bottom: 40, left: 0, right: 0,
            child: Center(
              child: FloatingActionButton.extended(
                onPressed: _captureReference,
                label: const Text('Capture Reference Sheet'),
                icon: const Icon(Icons.camera_alt),
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildTappingPhase() {
    final currentLabel = _taps.length < 4
        ? _tapLabels[_taps.length]
        : 'Calibrating...';

    return Scaffold(
      appBar: AppBar(
        title: const Text('Calibrate — Tap Anchors'),
        actions: [
          if (_taps.isNotEmpty)
            IconButton(
              icon: const Icon(Icons.undo),
              tooltip: 'Undo last tap',
              onPressed: _undoLastTap,
            ),
          IconButton(
            icon: const Icon(Icons.refresh),
            tooltip: 'Retake photo',
            onPressed: _retakePhoto,
          ),
        ],
      ),
      body: Column(
        children: [
          // Instruction bar
          Container(
            width: double.infinity,
            padding: const EdgeInsets.symmetric(vertical: 12, horizontal: 16),
            color: _taps.length < 4 ? Colors.blueAccent : Colors.green,
            child: Text(
              currentLabel,
              style: const TextStyle(color: Colors.white, fontSize: 16, fontWeight: FontWeight.bold),
              textAlign: TextAlign.center,
            ),
          ),
          // Full-screen tappable image
          Expanded(
            child: LayoutBuilder(
              builder: (context, constraints) {
                return GestureDetector(
                  onTapDown: (details) => _onImageTap(details, constraints),
                  child: Stack(
                    children: [
                      Positioned.fill(
                        child: Image.file(
                          File(_capturedPhotoPath!),
                          fit: BoxFit.contain,
                          width: constraints.maxWidth,
                          height: constraints.maxHeight,
                        ),
                      ),
                      // Render tap markers
                      for (int i = 0; i < _taps.length; i++)
                        Positioned(
                          left: _taps[i].dx - 12,
                          top: _taps[i].dy - 12,
                          child: Container(
                            width: 24,
                            height: 24,
                            decoration: BoxDecoration(
                              shape: BoxShape.circle,
                              color: i < 2 ? Colors.orange : Colors.green,
                              border: Border.all(color: Colors.white, width: 2),
                            ),
                            child: Center(
                              child: Text(
                                '${i + 1}',
                                style: const TextStyle(color: Colors.white, fontSize: 12, fontWeight: FontWeight.bold),
                              ),
                            ),
                          ),
                        ),
                      // Processing overlay
                      if (_isCalibrating)
                        Positioned.fill(
                          child: Container(
                            color: Colors.black54,
                            child: const Center(child: CircularProgressIndicator()),
                          ),
                        ),
                    ],
                  ),
                );
              },
            ),
          ),
        ],
      ),
    );
  }
}
