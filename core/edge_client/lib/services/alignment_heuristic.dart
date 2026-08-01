import 'dart:math';
import 'package:camera/camera.dart';

class AlignmentHeuristic {
  /// Analyzes a live camera frame to determine if it "looks like" an aligned document.
  /// This is a cheap, non-authoritative check meant only to drive a UI green/red guide frame.
  /// It requires:
  /// 1. Acceptable mean brightness.
  /// 2. Acceptable local pixel variance (not a blank wall).
  /// 3. Clear horizontal and vertical edge transitions (gradients) indicating a document boundary.
  static bool looksAligned(CameraImage image) {
    // Only YUV420 (Android) and BGRA8888 (iOS) are understood.
    // Any other format would produce meaningless brightness/variance numbers.
    if (image.format.group != ImageFormatGroup.yuv420 &&
        image.format.group != ImageFormatGroup.bgra8888) {
      return false;
    }

    final Plane plane = image.planes.first;
    return looksAlignedFromBytes(
      bytes: plane.bytes,
      width: image.width,
      height: image.height,
      rowStride: plane.bytesPerRow,
      pixelStride: plane.bytesPerPixel ?? 1,
    );
  }

  static bool looksAlignedFromBytes({
    required List<int> bytes,
    required int width,
    required int height,
    required int rowStride,
    required int pixelStride,
  }) {
    // We only sample a center crop to save time
    final int cropWidth = (width * 0.5).toInt();
    final int cropHeight = (height * 0.5).toInt();
    final int startX = (width - cropWidth) ~/ 2;
    final int startY = (height - cropHeight) ~/ 2;

    int sum = 0;
    int sumSquares = 0;
    int pixelCount = 0;

    // 1 & 2: Mean brightness and Variance
    // Sample every 4th pixel to make it cheap
    for (int y = startY; y < startY + cropHeight; y += 4) {
      final int rowOffset = y * rowStride;
      for (int x = startX; x < startX + cropWidth; x += 4) {
        final int index = rowOffset + (x * pixelStride);
        if (index >= bytes.length) continue;
        
        final int val = bytes[index];
        sum += val;
        sumSquares += (val * val);
        pixelCount++;
      }
    }

    if (pixelCount == 0) return false;

    final double mean = sum / pixelCount;
    // Fast variance: E[X^2] - (E[X])^2
    final double variance = (sumSquares / pixelCount) - (mean * mean);

    // Tune these thresholds based on typical lighting and paper texturing
    if (mean < 60 || mean > 240) return false;
    if (variance < 200) return false;

    // 3: Edge Density (Gradient Transitions)
    // We scan a single horizontal line and a single vertical line through the center
    // looking for sharp changes in luminance (edges of paper).
    int horizontalTransitions = 0;
    int verticalTransitions = 0;
    const int gradientThreshold = 40; // Minimum luminance difference to be an "edge"

    // Horizontal scanline (middle row)
    final int midY = height ~/ 2;
    int prevValH = -1;
    final int hRowOffset = midY * rowStride;
    for (int x = 0; x < width; x += 2) { // step by 2
      final int index = hRowOffset + (x * pixelStride);
      if (index >= bytes.length) break;
      final int val = bytes[index];
      if (prevValH != -1) {
        if ((val - prevValH).abs() > gradientThreshold) {
          horizontalTransitions++;
        }
      }
      prevValH = val;
    }

    // Vertical scanline (middle column)
    final int midX = width ~/ 2;
    int prevValV = -1;
    for (int y = 0; y < height; y += 2) { // step by 2
      final int index = (y * rowStride) + (midX * pixelStride);
      if (index >= bytes.length) break;
      final int val = bytes[index];
      if (prevValV != -1) {
        if ((val - prevValV).abs() > gradientThreshold) {
          verticalTransitions++;
        }
      }
      prevValV = val;
    }

    // We expect at least 2 transitions per axis (entering the paper, and leaving the paper).
    // If it's too high, it might be heavily textured noise, but we won't penalize it strictly here.
    if (horizontalTransitions < 2 || verticalTransitions < 2) {
      return false;
    }

    return true;
  }
}
