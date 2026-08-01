import 'package:flutter_test/flutter_test.dart';
import 'package:nexus_edge_mobile/services/alignment_heuristic.dart';
import 'dart:typed_data';

void main() {
  group('AlignmentHeuristic - looksAlignedFromBytes', () {
    const int width = 200;
    const int height = 200;
    const int rowStride = 200;
    const int pixelStride = 1;

    test('Uniform bright frame, no edges', () {
      // 200 is bright enough (60 < 200 < 240)
      final bytes = Uint8List.fromList(List.filled(width * height, 200));
      final result = AlignmentHeuristic.looksAlignedFromBytes(
        bytes: bytes, width: width, height: height, 
        rowStride: rowStride, pixelStride: pixelStride
      );
      // Fails due to variance < 200 and no edge transitions
      expect(result, isFalse);
    });

    test('Too dark', () {
      final bytes = Uint8List.fromList(List.filled(width * height, 10)); // < 60
      // We will make some noise and edges to ensure it fails ON brightness
      for (int i = 0; i < bytes.length; i++) {
        if (i % 2 == 0) bytes[i] = 50; // Add variance
      }
      final result = AlignmentHeuristic.looksAlignedFromBytes(
        bytes: bytes, width: width, height: height, 
        rowStride: rowStride, pixelStride: pixelStride
      );
      expect(result, isFalse);
    });

    test('Too bright', () {
      final bytes = Uint8List.fromList(List.filled(width * height, 250)); // > 240
      // Add variance
      for (int i = 0; i < bytes.length; i++) {
        if (i % 2 == 0) bytes[i] = 200; 
      }
      final result = AlignmentHeuristic.looksAlignedFromBytes(
        bytes: bytes, width: width, height: height, 
        rowStride: rowStride, pixelStride: pixelStride
      );
      expect(result, isFalse);
    });

    test('Sharp edges present in both axes', () {
      // Create a background of 50, and a central rectangle of 200
      final bytes = Uint8List.fromList(List.filled(width * height, 50));
      for (int y = 75; y < 125; y++) {
        for (int x = 75; x < 125; x++) {
          bytes[y * rowStride + x] = 200;
        }
      }
      final result = AlignmentHeuristic.looksAlignedFromBytes(
        bytes: bytes, width: width, height: height, 
        rowStride: rowStride, pixelStride: pixelStride
      );
      // It should pass all gates: brightness is in range, variance is high, 2 edges per axis
      expect(result, isTrue);
    });

    test('Sharp edges in only one axis', () {
      // Vertical transitions present, but horizontal is flat
      final bytes = Uint8List.fromList(List.filled(width * height, 50));
      for (int y = 0; y < height; y++) { // across all y
        for (int x = 50; x < 150; x++) { // block in the middle horizontally
          bytes[y * rowStride + x] = 200;
        }
      }
      final result = AlignmentHeuristic.looksAlignedFromBytes(
        bytes: bytes, width: width, height: height, 
        rowStride: rowStride, pixelStride: pixelStride
      );
      // Horizontal transitions will be 2, but Vertical transitions will be 0 (middle column is solid 200)
      expect(result, isFalse);
    });

    test('Gradual gradient, no sharp edges', () {
      final bytes = Uint8List.fromList(List.filled(width * height, 0));
      // Gradual ramp from left to right (0 to 200)
      for (int y = 0; y < height; y++) {
        for (int x = 0; x < width; x++) {
          bytes[y * rowStride + x] = x;
        }
      }
      // Difference between adjacent pixels is 1, threshold is 40. Edge count should be 0.
      final result = AlignmentHeuristic.looksAlignedFromBytes(
        bytes: bytes, width: width, height: height, 
        rowStride: rowStride, pixelStride: pixelStride
      );
      expect(result, isFalse);
    });
  });
}
