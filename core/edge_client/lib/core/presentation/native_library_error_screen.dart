import 'package:flutter/material.dart';

class NativeLibraryErrorScreen extends StatelessWidget {
  final String error;

  const NativeLibraryErrorScreen({super.key, required this.error});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Initialization Error'),
        backgroundColor: Colors.red.shade900,
      ),
      body: Padding(
        padding: const EdgeInsets.all(24.0),
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            const Icon(
              Icons.error_outline,
              color: Colors.redAccent,
              size: 80,
            ),
            const SizedBox(height: 24),
            const Text(
              "Native Library Missing",
              style: TextStyle(fontSize: 24, fontWeight: FontWeight.bold),
              textAlign: TextAlign.center,
            ),
            const SizedBox(height: 16),
            const Text(
              "The compiled native OMR library (omr_engine) could not be loaded for this device's architecture. Please ensure it was built and bundled correctly for your ABI.",
              textAlign: TextAlign.center,
              style: TextStyle(fontSize: 16),
            ),
            const SizedBox(height: 32),
            Container(
              padding: const EdgeInsets.all(12),
              color: Colors.black26,
              child: SingleChildScrollView(
                child: Text(
                  error,
                  style: const TextStyle(fontFamily: 'monospace', color: Colors.orangeAccent),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
