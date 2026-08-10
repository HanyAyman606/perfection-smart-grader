import 'dart:io';
import 'package:flutter/material.dart';

/// Full-screen, pinch-to-zoom viewer for an annotated ID/answer panel
/// image. Use [show] rather than constructing directly — it handles the
/// missing-file case with a snackbar instead of opening a broken dialog.
class AnnotatedImageViewer extends StatelessWidget {
  const AnnotatedImageViewer({super.key, required this.file, required this.title});

  final File file;
  final String title;

  static Future<void> show(BuildContext context, {required String path, required String title}) async {
    final file = File(path);
    imageCache.evict(FileImage(file));

    if (!await file.exists()) {
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Image not found.')));
      }
      return;
    }

    if (!context.mounted) return;
    await showDialog(
      context: context,
      builder: (_) => AnnotatedImageViewer(file: file, title: title),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Dialog(
      insetPadding: EdgeInsets.zero,
      backgroundColor: Colors.black,
      child: Stack(
        children: [
          InteractiveViewer(
            maxScale: 6.0,
            child: Image.file(file, gaplessPlayback: false, fit: BoxFit.contain, width: double.infinity, height: double.infinity),
          ),
          Positioned(
            top: 16,
            left: 16,
            child: Text(title, style: const TextStyle(color: Colors.white70, fontSize: 14)),
          ),
          Positioned(
            top: 16,
            right: 16,
            child: IconButton(
              icon: const Icon(Icons.close, color: Colors.white, size: 32),
              onPressed: () => Navigator.of(context).pop(),
            ),
          ),
        ],
      ),
    );
  }
}
