import 'dart:io';
import 'dart:ui' as ui;
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:camera/camera.dart';
import 'package:provider/provider.dart';
import 'package:path_provider/path_provider.dart';

import '../providers/exam_session_provider.dart';
import '../services/calibration_service.dart';
import '../widgets/scanner_guide_painter.dart';
import '../utils/image_size.dart';
import 'scanner_view.dart';

class CalibrationScreen extends StatefulWidget {
  const CalibrationScreen({super.key});

  @override
  State<CalibrationScreen> createState() => _CalibrationScreenState();
}

enum _CalibPhase { camera, tapping }

class _CalibrationScreenState extends State<CalibrationScreen> {
  CameraController? _controller;
  bool _isCalibrating = false;
  final TransformationController _transformationController = TransformationController();

  _CalibPhase _phase = _CalibPhase.camera;
  String? _capturedPhotoPath;
  ui.Size? _naturalImageSize;
  final List<Offset> _taps = [];

  static const _tapLabels = [
    'Tap 1/4: Top-left of ID bubble grid',
    'Tap 2/4: Bottom-right of ID bubble grid',
    'Tap 3/4: Top-left of Q1 option A',
    'Tap 4/4: Bottom-right of last question\'s last option',
  ];

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

    _controller = CameraController(backCamera, ResolutionPreset.high, enableAudio: false);
    await _controller!.initialize();
    if (mounted) setState(() {});
  }

  @override
  void dispose() {
    SystemChrome.setPreferredOrientations([
      DeviceOrientation.portraitUp, DeviceOrientation.portraitDown,
      DeviceOrientation.landscapeLeft, DeviceOrientation.landscapeRight,
    ]);
    _controller?.dispose();
    super.dispose();
  }

  Future<void> _captureReference() async {
    if (_controller == null || !_controller!.value.isInitialized) return;

    final photo = await _controller!.takePicture();
    final appDir = await getApplicationDocumentsDirectory();
    final savedPath = '${appDir.path}/omr_calibration_ref.jpg';
    await File(photo.path).copy(savedPath);

    final imageSize = await ImageSize.decodeImageSize(photo.path);
    await _controller!.dispose();
    _controller = null;

    setState(() {
      _capturedPhotoPath = photo.path;
      _naturalImageSize = imageSize;
      _phase = _CalibPhase.tapping;
      _taps.clear();
    });
  }

  void _dropAnchor(BoxConstraints constraints) {
    if (_taps.length >= 4) return;
    final displaySize = Size(constraints.maxWidth, constraints.maxHeight);
    final centerViewport = Offset(displaySize.width / 2, displaySize.height / 2);
    final scenePoint = _transformationController.toScene(centerViewport);

    setState(() { _taps.add(scenePoint); });
  }

  void _undoLastTap() {
    if (_taps.isNotEmpty) setState(() { _taps.removeLast(); });
  }

  void _retakePhoto() {
    setState(() {
      _phase = _CalibPhase.camera;
      _capturedPhotoPath = null;
      _naturalImageSize = null;
      _taps.clear();
    });
    _initCamera();
  }

  Offset _remapToPixelSpace(Offset displayTap, Size displaySize) {
    final double imgW = _naturalImageSize!.width;
    final double imgH = _naturalImageSize!.height;
    final double boxW = displaySize.width;
    final double boxH = displaySize.height;

    final double imgAspect = imgW / imgH;
    final double boxAspect = boxW / boxH;

    double renderedW, renderedH, offsetX, offsetY;
    if (imgAspect > boxAspect) {
      renderedW = boxW;
      renderedH = boxW / imgAspect;
      offsetX = 0.0;
      offsetY = (boxH - renderedH) / 2.0;
    } else {
      renderedH = boxH;
      renderedW = boxH * imgAspect;
      offsetX = (boxW - renderedW) / 2.0;
      offsetY = 0.0;
    }

    final double tapX = displayTap.dx - offsetX;
    final double tapY = displayTap.dy - offsetY;
    final double pixelX = tapX * (imgW / renderedW);
    final double pixelY = tapY * (imgH / renderedH);

    return Offset(pixelX.clamp(0.0, imgW), pixelY.clamp(0.0, imgH));
  }

  Future<void> _runCalibration(BoxConstraints constraints) async {
    if (_isCalibrating) return;
    setState(() { _isCalibrating = true; });

    try {
      final session = Provider.of<ExamSessionProvider>(context, listen: false);
      final master = session.masterPacket!;
      final displaySize = Size(constraints.maxWidth, constraints.maxHeight);

      final idTopLeft = _remapToPixelSpace(_taps[0], displaySize);
      final idBottomRight = _remapToPixelSpace(_taps[1], displaySize);
      final ansTopLeft = _remapToPixelSpace(_taps[2], displaySize);
      final ansBottomRight = _remapToPixelSpace(_taps[3], displaySize);

      final idBlock = {
        "name": "id",
        "x1": idTopLeft.dx.toInt(), "y1": idTopLeft.dy.toInt(),
        "x2": idBottomRight.dx.toInt(), "y2": idBottomRight.dy.toInt(),
      };

      final ansBlock = {
        "name": "answers",
        "x1": ansTopLeft.dx.toInt(), "y1": ansTopLeft.dy.toInt(),
        "x2": ansBottomRight.dx.toInt(), "y2": ansBottomRight.dy.toInt(),
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
        session.markCalibrated(_naturalImageSize!);
        if (mounted) {
          Navigator.of(context).pushReplacement(MaterialPageRoute(builder: (_) => const ScannerView()));
        }
      } else {
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Calibration failed: ${result['error']}')));
          setState(() { _taps.clear(); });
        }
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Error: $e')));
        setState(() { _taps.clear(); });
      }
    } finally {
      if (mounted) setState(() { _isCalibrating = false; });
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_phase == _CalibPhase.camera) return _buildCameraPhase();
    return _buildTappingPhase();
  }

  Widget _buildCameraPhase() {
    if (_controller == null || !_controller!.value.isInitialized) {
      return const Scaffold(body: Center(child: CircularProgressIndicator()));
    }

    final media = MediaQuery.of(context).size;
    final isPortrait = media.height > media.width;
    final boxHeight = media.height * 0.80;
    final boxWidth = boxHeight * 1.414;

    return Scaffold(
      appBar: AppBar(title: const Text('Calibrate — Capture Reference'), toolbarHeight: 48),
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
                  cutoutSize: Size(boxWidth, boxHeight),
                  bracketColor: Colors.blueAccent,
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
        ],
      ),
    );
  }

  Widget _buildTappingPhase() {
    final currentLabel = _isCalibrating ? 'Calibrating...' : (_taps.length < 4 ? _tapLabels[_taps.length] : 'All 4 anchors placed — review, then confirm below');

    return Scaffold(
      appBar: AppBar(
        title: const Text('Calibrate — Tap Anchors'),
        actions: [
          if (_taps.isNotEmpty) IconButton(icon: const Icon(Icons.undo), tooltip: 'Undo last tap', onPressed: _undoLastTap),
          IconButton(icon: const Icon(Icons.refresh), tooltip: 'Retake photo', onPressed: _retakePhoto),
        ],
      ),
      body: Column(
        children: [
          Container(
            width: double.infinity,
            padding: const EdgeInsets.symmetric(vertical: 12, horizontal: 16),
            color: _taps.length < 4 ? Colors.blueAccent : Colors.green,
            child: Text(currentLabel, style: const TextStyle(color: Colors.white, fontSize: 16, fontWeight: FontWeight.bold), textAlign: TextAlign.center),
          ),
          Expanded(
            child: LayoutBuilder(
              builder: (context, constraints) {
                return Stack(
                  children: [
                    Positioned.fill(
                      child: InteractiveViewer(
                        transformationController: _transformationController,
                        maxScale: 6.0, minScale: 1.0,
                        child: Stack(
                          children: [
                            Positioned.fill(
                              child: Image.file(
                                File(_capturedPhotoPath!),
                                fit: BoxFit.contain,
                                width: constraints.maxWidth, height: constraints.maxHeight,
                              ),
                            ),
                            for (int i = 0; i < _taps.length; i++)
                              Positioned(
                                left: _taps[i].dx - 3, top: _taps[i].dy - 3,
                                child: Container(
                                  width: 6, height: 6,
                                  decoration: BoxDecoration(
                                    shape: BoxShape.circle,
                                    color: i < 2 ? Colors.orange : Colors.green,
                                    border: Border.all(color: Colors.white, width: 1),
                                  ),
                                ),
                              ),
                          ],
                        ),
                      ),
                    ),
                    const Center(child: Icon(Icons.add, color: Colors.red, size: 32)),
                    if (!_isCalibrating)
                      Positioned(
                        bottom: 24, left: 0, right: 0,
                        child: Center(
                          child: _taps.length < 4
                              ? FloatingActionButton.extended(onPressed: () => _dropAnchor(constraints), icon: const Icon(Icons.gps_fixed), label: const Text('Drop Anchor'))
                              : FloatingActionButton.extended(onPressed: () => _runCalibration(constraints), backgroundColor: Colors.green, icon: const Icon(Icons.check_circle), label: const Text('Confirm & Calibrate')),
                        ),
                      ),
                    if (_isCalibrating)
                      Positioned.fill(child: Container(color: Colors.black54, child: const Center(child: CircularProgressIndicator()))),
                  ],
                );
              },
            ),
          ),
        ],
      ),
    );
  }
}
