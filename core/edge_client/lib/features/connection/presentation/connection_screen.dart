import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../controller/connection_controller.dart';
import 'widgets/qr_scan_sheet.dart';
import '../../../domain/repositories/credentials_repository.dart';
import '../../scanning/presentation/scanner_view.dart';
import '../../../core/presentation/debug_log_screen.dart';

class ConnectionScreen extends StatefulWidget {
  const ConnectionScreen({super.key});

  @override
  State<ConnectionScreen> createState() => _ConnectionScreenState();
}

class _ConnectionScreenState extends State<ConnectionScreen> {
  final _hostController = TextEditingController();
  final _nameController = TextEditingController();
  final _passwordController = TextEditingController();

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      final session = Provider.of<ConnectionController>(context, listen: false);
      session.addListener(_onSessionPhaseChanged);
    });
    _loadCachedCredentials();
  }

  Future<void> _loadCachedCredentials() async {
    final creds = await Provider.of<CredentialsRepository>(context, listen: false).loadCredentials();
    if (!mounted) return;
    setState(() {
      if (creds['host'] != null) _hostController.text = creds['host']!;
      if (creds['name'] != null) _nameController.text = creds['name']!;
      if (creds['password'] != null) _passwordController.text = creds['password']!;
    });
  }

  @override
  void dispose() {
    _hostController.dispose();
    _nameController.dispose();
    _passwordController.dispose();
    super.dispose();
  }

  void _onSessionPhaseChanged() {
    if (!mounted) return;
    final session = Provider.of<ConnectionController>(context, listen: false);

    if (session.phase == SessionPhase.scanning) {
      session.removeListener(_onSessionPhaseChanged);
      Navigator.of(context).pushReplacement(
        MaterialPageRoute(builder: (_) => const ScannerView()),
      );
    }
  }

  Future<void> _handleScanQr() async {
    final scannedIp = await showQrScanSheet(context);
    if (scannedIp != null && mounted) {
      setState(() => _hostController.text = scannedIp);
    }
  }

  void _handleConnect() {
    final host = _hostController.text.trim();
    final name = _nameController.text.trim();
    final password = _passwordController.text;

    if (host.isEmpty || name.isEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Host and Name are required.')),
      );
      return;
    }

    Provider.of<ConnectionController>(context, listen: false)
        .connect(host: host, name: name, password: password);
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: GestureDetector(
          onLongPress: () {
            Navigator.of(context).push(MaterialPageRoute(builder: (_) => const DebugLogScreen()));
          },
          child: const Text('Perfection Smart Grader Connect'),
        ),
      ),
      body: Consumer<ConnectionController>(
        builder: (context, session, child) {
          final isConnecting = session.phase == SessionPhase.connecting;

          return Padding(
            padding: const EdgeInsets.all(24.0),
            child: Column(
              mainAxisAlignment: MainAxisAlignment.center,
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                if (session.errorMessage != null)
                  Container(
                    margin: const EdgeInsets.only(bottom: 24),
                    padding: const EdgeInsets.all(12),
                    color: Colors.red.shade900,
                    child: Text(session.errorMessage!, style: const TextStyle(color: Colors.white)),
                  ),
                TextField(
                  controller: _nameController,
                  decoration: const InputDecoration(labelText: 'Proctor Name', border: OutlineInputBorder()),
                  enabled: !isConnecting,
                ),
                const SizedBox(height: 16),
                Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Expanded(
                      child: TextField(
                        controller: _hostController,
                        decoration: const InputDecoration(labelText: 'Desktop Host IP', border: OutlineInputBorder()),
                        enabled: !isConnecting,
                        keyboardType: TextInputType.number,
                      ),
                    ),
                    const SizedBox(width: 8),
                    SizedBox(
                      height: 56,
                      child: OutlinedButton(
                        onPressed: isConnecting ? null : _handleScanQr,
                        style: OutlinedButton.styleFrom(padding: const EdgeInsets.symmetric(horizontal: 12)),
                        child: const Icon(Icons.qr_code_scanner),
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 16),
                TextField(
                  controller: _passwordController,
                  decoration: const InputDecoration(labelText: 'Session Password', border: OutlineInputBorder()),
                  obscureText: true,
                  enabled: !isConnecting,
                ),
                const SizedBox(height: 32),
                ElevatedButton(
                  onPressed: isConnecting ? null : _handleConnect,
                  style: ElevatedButton.styleFrom(padding: const EdgeInsets.symmetric(vertical: 16)),
                  child: isConnecting
                      ? const SizedBox(
                          height: 20, width: 20, child: CircularProgressIndicator(strokeWidth: 2))
                      : const Text('CONNECT'),
                ),
              ],
            ),
          );
        },
      ),
    );
  }
}
