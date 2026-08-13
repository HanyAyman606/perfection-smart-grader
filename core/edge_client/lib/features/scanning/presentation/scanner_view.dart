import 'package:flutter/material.dart';
import 'package:flutter/foundation.dart';
import 'package:provider/provider.dart';

import '../../connection/controller/connection_controller.dart';
import '../controller/scan_controller.dart';
import 'camera_capture_screen.dart';
import 'panel_preview_screen.dart';
import '../../connection/presentation/connection_screen.dart';

/// Entry point for capturing a student's answer sheet, using an in-app
/// camera preview (CameraCaptureScreen) rather than redirecting out to
/// the phone's native camera app. The C++ engine's perspective-correction
/// + orientation-reasoning stage handles rotation, so the proctor can
/// hold the phone however is natural — no landscape lock or live
/// alignment heuristic needed here.
///
/// TIMING INSTRUMENTATION: added alongside cv_engine_service.dart and
/// panel_preview_screen.dart to find where the ~7s a scan "feels like"
/// actually goes, given the native engine's own timing_ms consistently
/// measures only ~1-1.5s. This file specifically times the camera
/// capture step itself (screen open -> shutter -> photo path returned),
/// which is the one phase none of the other instrumentation can see,
/// since it happens entirely before CvEngineService or
/// PanelPreviewScreen exist for this scan.
class ScannerView extends StatefulWidget {
  const ScannerView({super.key});

  @override
  State<ScannerView> createState() => _ScannerViewState();
}

class _ScannerViewState extends State<ScannerView> {
  bool _isCapturing = false;

  Future<void> _captureAndProcess() async {
    if (_isCapturing) return;
    setState(() => _isCapturing = true);

    // TIMED: how long the in-app camera screen itself takes, from the
    // moment the proctor taps "Capture answer sheet" to the moment a
    // photo path comes back. This covers CameraCaptureScreen's own
    // camera init (if not already warm) + the actual shutter press +
    // file write -- NOTE this necessarily also includes however long the
    // proctor spends physically framing/positioning the sheet before
    // tapping the shutter button, which is real time but not "stall" in
    // the software sense -- worth eyeballing whether this number is
    // dominated by camera init/write cost or by human positioning time
    // when reading the log.
    final captureSw = Stopwatch()..start();

    try {
      final imagePath = await Navigator.of(context).push<String>(
        MaterialPageRoute(builder: (_) => const CameraCaptureScreen()),
      );
      final captureMs = captureSw.elapsedMilliseconds;
      debugPrint(
        '[ScannerView] TIMING camera_screen_open_to_photo_path=${captureMs}ms '
        '(includes camera init + proctor framing/positioning time + shutter + file write, not pure overhead)',
      );

      if (imagePath == null) {
        // Proctor backed out of the camera screen.
        return;
      }

      if (mounted) {
        final navSw = Stopwatch()..start();
        await Navigator.of(context).push(
          MaterialPageRoute(builder: (_) => PanelPreviewScreen(rawImagePath: imagePath)),
        );
        debugPrint(
          '[ScannerView] TIMING PanelPreviewScreen_route_lifetime_until_popped_back=${navSw.elapsedMilliseconds}ms',
        );
      }
    } finally {
      if (mounted) setState(() => _isCapturing = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final session = context.watch<ConnectionController>();
    final master = session.masterPacket;

    return Scaffold(
      appBar: AppBar(
        title: Text('Scanning: ${master?.examName ?? ""}'),
        actions: [
          if (master != null && master.supportsMultipleVersions)
            DropdownButton<String>(
              value: session.selectedVersion,
              dropdownColor: Colors.grey[850],
              underline: const SizedBox.shrink(),
              items: master.answerVersions
                  .map((v) => DropdownMenuItem(value: v, child: Text('Version $v')))
                  .toList(),
              onChanged: (val) {
                if (val != null) session.selectVersion(val);
              },
            ),
          const SizedBox(width: 12),
          IconButton(
            icon: const Icon(Icons.logout),
            tooltip: 'Log Out',
            onPressed: () async {
              final confirmed = await showDialog<bool>(
                context: context,
                builder: (ctx) => AlertDialog(
                  title: const Text('Disconnect?'),
                  content: const Text('Disconnect from this session? Any unsaved scan will be lost.'),
                  actions: [
                    TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Cancel')),
                    TextButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('Log Out', style: TextStyle(color: Colors.red))),
                  ],
                ),
              );
              if (confirmed == true && mounted) {
                // Old ExamSessionProvider.endSession() combined both of
                // these — now that scan state and connection state are
                // separate controllers, the caller (here) orchestrates
                // clearing both, since neither controller should know
                // the other exists. Grab both controllers before the
                // first await so nothing touches `context` again until
                // the final `mounted`-guarded navigation call.
                final scanController = Provider.of<ScanController>(context, listen: false);
                final connectionController = Provider.of<ConnectionController>(context, listen: false);
                await scanController.clearCurrentScan();
                await connectionController.disconnectAndForgetCredentials();
                if (!mounted) return;
                Navigator.of(context).pushAndRemoveUntil(
                  MaterialPageRoute(builder: (_) => const ConnectionScreen()),
                  (route) => false,
                );
              }
            },
          ),
        ],
      ),
      body: Center(
        child: Padding(
          padding: const EdgeInsets.all(32.0),
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              Icon(Icons.document_scanner_outlined, size: 96, color: Colors.blueGrey.shade300),
              const SizedBox(height: 24),
              Text(
                'Ready to scan',
                style: Theme.of(context).textTheme.headlineSmall,
              ),
              const SizedBox(height: 8),
              const Text(
                'Capture the full answer sheet — the app will detect and\n'
                'straighten the ID and answer panels automatically.',
                textAlign: TextAlign.center,
                style: TextStyle(color: Colors.grey),
              ),
              const SizedBox(height: 40),
              SizedBox(
                width: double.infinity,
                child: ElevatedButton.icon(
                  onPressed: _isCapturing ? null : _captureAndProcess,
                  icon: _isCapturing
                      ? const SizedBox(
                          width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))
                      : const Icon(Icons.camera_alt),
                  label: Text(_isCapturing ? 'Opening camera...' : 'Capture answer sheet'),
                  style: ElevatedButton.styleFrom(padding: const EdgeInsets.symmetric(vertical: 18)),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}