import 'package:camera/camera.dart';
import 'package:flutter/material.dart';

/// Full-screen live camera preview with a manual shutter button — the
/// in-app replacement for redirecting out to the phone's native camera
/// app via image_picker. Trade-off vs. the old approach: we lose the
/// OEM camera app's own tuned quality/stabilization pipeline, so if
/// scans start failing Step 1's blur/confidence gate more often on some
/// devices, that gate (see PanelExtractionResult.mustRetake downstream)
/// is the place to look first — it was already load-bearing before this
/// change and remains so now.
///
/// Returns the captured photo's file path via Navigator.pop when the
/// proctor takes a shot, or null if they back out.
class CameraCaptureScreen extends StatefulWidget {
  const CameraCaptureScreen({super.key});

  @override
  State<CameraCaptureScreen> createState() => _CameraCaptureScreenState();
}

class _CameraCaptureScreenState extends State<CameraCaptureScreen> with WidgetsBindingObserver {
  CameraController? _controller;
  Future<void>? _initializeFuture;
  bool _isCapturing = false;
  String? _errorMessage;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _initializeFuture = _initCamera();
  }

  Future<void> _initCamera() async {
    try {
      final cameras = await availableCameras();
      if (cameras.isEmpty) {
        setState(() => _errorMessage = 'No camera found on this device.');
        return;
      }

      final backCamera = cameras.firstWhere(
        (c) => c.lensDirection == CameraLensDirection.back,
        orElse: () => cameras.first,
      );

      final controller = CameraController(
        backCamera,
        ResolutionPreset.max, // full quality into Step 1 — no re-compression
        enableAudio: false,
      );
      await controller.initialize();
      if (!mounted) {
        await controller.dispose();
        return;
      }
      setState(() => _controller = controller);
    } catch (e) {
      setState(() => _errorMessage = 'Could not start camera: $e');
    }
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    // A backgrounded camera controller holds the hardware open, which
    // both drains battery and can block other apps from using the
    // camera. Tear it down on pause and rebuild it on resume rather
    // than trying to keep a single controller alive across the gap.
    final controller = _controller;
    if (controller == null) return;

    if (state == AppLifecycleState.inactive || state == AppLifecycleState.paused) {
      controller.dispose();
      setState(() => _controller = null);
    } else if (state == AppLifecycleState.resumed) {
      setState(() => _initializeFuture = _initCamera());
    }
  }

  Future<void> _capture() async {
    final controller = _controller;
    if (controller == null || !controller.value.isInitialized || _isCapturing) return;

    setState(() => _isCapturing = true);
    try {
      final photo = await controller.takePicture();
      if (mounted) Navigator.of(context).pop(photo.path);
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Capture failed: $e')),
        );
      }
    } finally {
      if (mounted) setState(() => _isCapturing = false);
    }
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _controller?.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.black,
      appBar: AppBar(
        backgroundColor: Colors.black,
        foregroundColor: Colors.white,
        title: const Text('Capture answer sheet'),
      ),
      body: FutureBuilder<void>(
        future: _initializeFuture,
        builder: (context, snapshot) {
          if (_errorMessage != null) {
            return Center(
              child: Padding(
                padding: const EdgeInsets.all(24),
                child: Text(
                  _errorMessage!,
                  style: const TextStyle(color: Colors.white),
                  textAlign: TextAlign.center,
                ),
              ),
            );
          }

          final controller = _controller;
          if (snapshot.connectionState != ConnectionState.done ||
              controller == null ||
              !controller.value.isInitialized) {
            return const Center(child: CircularProgressIndicator());
          }

          return Stack(
            fit: StackFit.expand,
            children: [
              Center(child: CameraPreview(controller)),
              Positioned(
                left: 0,
                right: 0,
                bottom: 32,
                child: Center(
                  child: GestureDetector(
                    onTap: _isCapturing ? null : _capture,
                    child: Container(
                      width: 76,
                      height: 76,
                      decoration: BoxDecoration(
                        shape: BoxShape.circle,
                        color: Colors.white,
                        border: Border.all(color: Colors.white54, width: 4),
                      ),
                      child: _isCapturing
                          ? const Padding(
                              padding: EdgeInsets.all(20),
                              child: CircularProgressIndicator(strokeWidth: 2),
                            )
                          : null,
                    ),
                  ),
                ),
              ),
            ],
          );
        },
      ),
    );
  }
}