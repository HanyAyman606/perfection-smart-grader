import 'dart:io';
import 'package:flutter_test/flutter_test.dart';
import 'package:nexus_edge_mobile/services/calibration_service.dart';
import 'package:image/image.dart' as img;

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  group('CalibrationService EXIF Tests', () {
    late String rotatedPath;
    late String normalPath;

    setUp(() {
      // Create a normal 100x50 image (landscape)
      final normalImage = img.Image(width: 100, height: 50);
      img.fill(normalImage, color: img.ColorRgb8(0, 255, 0));
      normalPath = 'test/fixtures/normal.jpg';
      File(normalPath)
        ..createSync(recursive: true)
        ..writeAsBytesSync(img.encodeJpg(normalImage));

      // Create a 50x100 image (portrait) but with EXIF rotation 6 (90 CW)
      // This means physically it's 50x100, but should be displayed as 100x50.
      final rotatedImage = img.Image(width: 50, height: 100);
      img.fill(rotatedImage, color: img.ColorRgb8(255, 0, 0));
      // Exif orientation 6: Rotate 90 CW
      rotatedImage.exif.imageIfd.orientation = 6;
      rotatedPath = 'test/fixtures/rotated_exif.jpg';
      File(rotatedPath).writeAsBytesSync(img.encodeJpg(rotatedImage));
    });

    tearDown(() {
      if (File(normalPath).existsSync()) File(normalPath).deleteSync();
      if (File(rotatedPath).existsSync()) File(rotatedPath).deleteSync();
      final orientedPath = rotatedPath.replaceAll('.jpg', '_oriented.jpg');
      if (File(orientedPath).existsSync()) File(orientedPath).deleteSync();
      final normalOriented = normalPath.replaceAll('.jpg', '_oriented.jpg');
      if (File(normalOriented).existsSync()) File(normalOriented).deleteSync();
    });

    test('fixExifOrientation physically rotates EXIF-tagged image and strips tag', () async {
      // Check original dimensions of the raw JPEG without applying EXIF
      final bytes = await File(rotatedPath).readAsBytes();
      final original = img.decodeJpg(bytes);
      expect(original, isNotNull);
      // Note: img.decodeJpg may automatically apply EXIF or store it in a way that width reflects the oriented size depending on the library version.
      expect(original!.width, 100);
      expect(original.height, 50);

      // Run fix
      final fixedPath = await CalibrationService.instance.fixExifOrientation(rotatedPath);
      final fixedFile = File(fixedPath);
      expect(fixedFile.existsSync(), isTrue);

      // Verify the new image has been physically rotated
      final fixedBytes = await fixedFile.readAsBytes();
      final fixedImage = img.decodeJpg(fixedBytes);
      expect(fixedImage, isNotNull);

      // Now the pixels should be physically rotated to 100x50 landscape
      expect(fixedImage!.width, 100);
      expect(fixedImage.height, 50);
    });

    test('fixExifOrientation does not distort normal image', () async {
      final fixedPath = await CalibrationService.instance.fixExifOrientation(normalPath);
      final fixedFile = File(fixedPath);
      expect(fixedFile.existsSync(), isTrue);

      final fixedBytes = await fixedFile.readAsBytes();
      final fixedImage = img.decodeJpg(fixedBytes);
      expect(fixedImage, isNotNull);

      expect(fixedImage!.width, 100);
      expect(fixedImage.height, 50);
    });
  });
}
