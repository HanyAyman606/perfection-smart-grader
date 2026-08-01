import 'dart:convert';
import 'dart:io';
import 'package:image/image.dart' as img;
import 'package:path_provider/path_provider.dart';
import 'native_omr_bindings.dart';

class CalibrationService {
  static final CalibrationService instance = CalibrationService._();
  CalibrationService._();

  /// Fixes EXIF rotation by physically rotating pixels and stripping EXIF orientation.
  Future<String> fixExifOrientation(String originalPath) async {
    final bytes = await File(originalPath).readAsBytes();
    final image = img.decodeJpg(bytes);
    if (image == null) return originalPath;

    final orientedImage = img.bakeOrientation(image);
    // Strip EXIF to ensure OpenCV or downstream tools don't double-rotate
    orientedImage.exif.clear();

    final fixedBytes = img.encodeJpg(orientedImage);
    final newPath = originalPath.replaceAll('.jpg', '_oriented.jpg');
    await File(newPath).writeAsBytes(fixedBytes);
    return newPath;
  }

  Future<Map<String, dynamic>> calibrate({
    required String rawImagePath,
    required int numQuestions,
    required int choicesPerQuestion,
    required int questionsPerBlock,
    required int idLetterCount,
    required int idDigitColumns,
    List<String>? idLetterLabels,
    required Map<String, dynamic> idBlockClicks, // { "name": "id", "x1":..., "y1":..., "x2":..., "y2":... }
    required Map<String, dynamic> answersBlockClicks,
  }) async {
    // 1. Fix EXIF orientation
    final orientedImagePath = await fixExifOrientation(rawImagePath);

    // 2. Prepare request JSON
    final appDir = await getApplicationSupportDirectory();
    final profilePath = '${appDir.path}/active_profile.yml';
    final requestPath = '${appDir.path}/calibration_request.json';

    final requestPayload = {
      "image_path": orientedImagePath,
      "profile_path": profilePath,
      "num_questions": numQuestions,
      "choices_per_question": choicesPerQuestion,
      "questions_per_block": questionsPerBlock,
      "id_letter_count": idLetterCount,
      "id_digit_columns": idDigitColumns,
      if (idLetterLabels != null) "id_letter_labels": idLetterLabels,
      "blocks_clicks": [
        if (idLetterCount > 0 || idDigitColumns > 0) idBlockClicks,
        if (numQuestions > 0) answersBlockClicks,
      ],
    };

    await File(requestPath).writeAsString(jsonEncode(requestPayload));

    // 3. Call Native FFI
    final resultJson = NativeOmrBindings.instance.callCalibrate(requestPath);
    return jsonDecode(resultJson) as Map<String, dynamic>;
  }
}
