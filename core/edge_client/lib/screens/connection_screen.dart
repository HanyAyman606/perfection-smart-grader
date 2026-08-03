import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../providers/exam_session_provider.dart';
import 'calibration_screen.dart';
import 'scanner_view.dart';

class ConnectionScreen extends StatefulWidget {
  const ConnectionScreen({super.key});

  @override
  State<ConnectionScreen> createState() => _ConnectionScreenState();
}

class _ConnectionScreenState extends State<ConnectionScreen> {
  final _hostController = TextEditingController(text: "192.168.1.100");
  final _nameController = TextEditingController();
  final _passwordController = TextEditingController();

  @override
  void initState() {
    super.initState();
    // Use addPostFrameCallback to listen to provider changes safely
    WidgetsBinding.instance.addPostFrameCallback((_) {
      final session = Provider.of<ExamSessionProvider>(context, listen: false);
      session.addListener(_onSessionPhaseChanged);
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
    final session = Provider.of<ExamSessionProvider>(context, listen: false);
    
    if (session.phase == SessionPhase.needsCalibration) {
      session.removeListener(_onSessionPhaseChanged);
      Navigator.of(context).pushReplacement(
        MaterialPageRoute(builder: (_) => const CalibrationScreen()),
      );
    } else if (session.phase == SessionPhase.scanning) {
      session.removeListener(_onSessionPhaseChanged);
      Navigator.of(context).pushReplacement(
        MaterialPageRoute(builder: (_) => const ScannerView()),
      );
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

    Provider.of<ExamSessionProvider>(context, listen: false)
        .connect(host: host, name: name, password: password);
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Nexus Edge Connect'),
      ),
      body: Consumer<ExamSessionProvider>(
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
                    child: Text(
                      session.errorMessage!,
                      style: const TextStyle(color: Colors.white),
                    ),
                  ),
                TextField(
                  controller: _nameController,
                  decoration: const InputDecoration(
                    labelText: 'Proctor Name',
                    border: OutlineInputBorder(),
                  ),
                  enabled: !isConnecting,
                ),
                const SizedBox(height: 16),
                TextField(
                  controller: _hostController,
                  decoration: const InputDecoration(
                    labelText: 'Desktop Host IP',
                    border: OutlineInputBorder(),
                  ),
                  enabled: !isConnecting,
                ),
                const SizedBox(height: 16),
                TextField(
                  controller: _passwordController,
                  decoration: const InputDecoration(
                    labelText: 'Session Password',
                    border: OutlineInputBorder(),
                  ),
                  obscureText: true,
                  enabled: !isConnecting,
                ),
                const SizedBox(height: 32),
                ElevatedButton(
                  onPressed: isConnecting ? null : _handleConnect,
                  style: ElevatedButton.styleFrom(
                    padding: const EdgeInsets.symmetric(vertical: 16),
                  ),
                  child: isConnecting
                      ? const SizedBox(
                          height: 20,
                          width: 20,
                          child: CircularProgressIndicator(strokeWidth: 2),
                        )
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
