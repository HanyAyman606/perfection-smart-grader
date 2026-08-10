import 'package:flutter/material.dart';
import 'package:mobile_scanner/mobile_scanner.dart';

/// Bottom sheet that scans the desktop dashboard's "connect QR" — which
/// encodes just the raw LAN IP string (see admin_dashboard's
/// widgets/qr_code.py + dashboard.py: generate_qr_pixmap(get_local_ip())).
/// No JSON, no ws:// scheme, no port — the app's own default port (8765)
/// is used regardless of what's scanned.
///
/// Returns the scanned IP string via Navigator.pop, or null if dismissed.
Future<String?> showQrScanSheet(BuildContext context) {
  return showModalBottomSheet<String>(
    context: context,
    isScrollControlled: true,
    backgroundColor: Colors.black,
    builder: (context) => const _QrScanSheetContent(),
  );
}

class _QrScanSheetContent extends StatefulWidget {
  const _QrScanSheetContent();

  @override
  State<_QrScanSheetContent> createState() => _QrScanSheetContentState();
}

class _QrScanSheetContentState extends State<_QrScanSheetContent> {
  final MobileScannerController _controller = MobileScannerController(
    detectionSpeed: DetectionSpeed.noDuplicates,
  );
  bool _handled = false;

  void _onDetect(BarcodeCapture capture) {
    if (_handled) return;
    final barcodes = capture.barcodes;
    if (barcodes.isEmpty) return;

    final raw = barcodes.first.rawValue?.trim();
    if (raw == null || raw.isEmpty) return;

    // Basic sanity check — the dashboard only ever encodes an IPv4
    // address. Reject anything that clearly isn't one rather than
    // silently feeding garbage into the host field.
    final ipPattern = RegExp(r'^\d{1,3}(\.\d{1,3}){3}$');
    if (!ipPattern.hasMatch(raw)) return;

    _handled = true;
    Navigator.of(context).pop(raw);
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      height: MediaQuery.of(context).size.height * 0.75,
      child: Column(
        children: [
          Padding(
            padding: const EdgeInsets.all(16),
            child: Row(
              children: [
                const Expanded(
                  child: Text(
                    'Scan the connect QR on the dashboard',
                    style: TextStyle(color: Colors.white, fontSize: 16, fontWeight: FontWeight.bold),
                  ),
                ),
                IconButton(
                  icon: const Icon(Icons.close, color: Colors.white),
                  onPressed: () => Navigator.of(context).pop(),
                ),
              ],
            ),
          ),
          Expanded(
            child: Stack(
              alignment: Alignment.center,
              children: [
                MobileScanner(controller: _controller, onDetect: _onDetect),
                Container(
                  width: 220,
                  height: 220,
                  decoration: BoxDecoration(
                    border: Border.all(color: Colors.greenAccent, width: 2),
                    borderRadius: BorderRadius.circular(12),
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}