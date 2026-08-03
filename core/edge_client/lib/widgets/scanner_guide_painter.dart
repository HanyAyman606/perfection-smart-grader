import 'package:flutter/material.dart';

class ScannerGuidePainter extends CustomPainter {
  final Size cutoutSize;
  final Color bracketColor;

  ScannerGuidePainter({required this.cutoutSize, required this.bracketColor});

  @override
  void paint(Canvas canvas, Size size) {
    // Fill the whole screen with dark overlay
    final backgroundPaint = Paint()..color = Colors.black54;
    canvas.drawRect(Rect.fromLTWH(0, 0, size.width, size.height), backgroundPaint);

    // Calculate cutout rect centered on screen
    final cutoutRect = Rect.fromCenter(
      center: Offset(size.width / 2, size.height / 2),
      width: cutoutSize.width,
      height: cutoutSize.height,
    );

    // Clear the cutout area to make it fully transparent
    canvas.drawRect(cutoutRect, Paint()..blendMode = BlendMode.clear);

    // Draw the 4 brackets
    final bracketPaint = Paint()
      ..color = bracketColor
      ..strokeWidth = 6.0
      ..style = PaintingStyle.stroke;

    final double l = 40.0; // length of bracket

    // Top-left
    canvas.drawPath(
      Path()
        ..moveTo(cutoutRect.left, cutoutRect.top + l)
        ..lineTo(cutoutRect.left, cutoutRect.top)
        ..lineTo(cutoutRect.left + l, cutoutRect.top),
      bracketPaint,
    );

    // Top-right
    canvas.drawPath(
      Path()
        ..moveTo(cutoutRect.right - l, cutoutRect.top)
        ..lineTo(cutoutRect.right, cutoutRect.top)
        ..lineTo(cutoutRect.right, cutoutRect.top + l),
      bracketPaint,
    );

    // Bottom-right
    canvas.drawPath(
      Path()
        ..moveTo(cutoutRect.right, cutoutRect.bottom - l)
        ..lineTo(cutoutRect.right, cutoutRect.bottom)
        ..lineTo(cutoutRect.right - l, cutoutRect.bottom),
      bracketPaint,
    );

    // Bottom-left
    canvas.drawPath(
      Path()
        ..moveTo(cutoutRect.left + l, cutoutRect.bottom)
        ..lineTo(cutoutRect.left, cutoutRect.bottom)
        ..lineTo(cutoutRect.left, cutoutRect.bottom - l),
      bracketPaint,
    );
  }

  @override
  bool shouldRepaint(covariant ScannerGuidePainter oldDelegate) {
    return oldDelegate.cutoutSize != cutoutSize || oldDelegate.bracketColor != bracketColor;
  }
}
