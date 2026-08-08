import 'dart:convert';
import 'dart:io';
import 'package:image/image.dart' as img;
import 'package:path_provider/path_provider.dart';
import '../models/exam_models.dart';
import 'native_omr_bindings.dart';

class OmrEngineException implements Exception {
  final String code;
  final String message;
  OmrEngineException(this.code, this.message);

  bool get needsRetake => code == 'REGISTRATION_FAILED' || code == 'IMAGE_UNREADABLE';
  bool get needsRecalibration => code == 'PROFILE_OUTDATED' || code == 'PROFILE_UNREADABLE';

  @override
  String toString() => message;
}

class OmrService {
  static final OmrService instance = OmrService._();
  OmrService._();

  Future<String> _fixExifOrientation(String originalPath) async {
    final bytes = await File(originalPath).readAsBytes();
    final image = img.decodeJpg(bytes);
    if (image == null) return originalPath;

    final orientedImage = img.bakeOrientation(image);
    orientedImage.exif.clear();

    final fixedBytes = img.encodeJpg(orientedImage);
    final newPath = originalPath.replaceAll('.jpg', '_oriented.jpg');
    await File(newPath).writeAsBytes(fixedBytes);
    return newPath;
  }

  Future<GradeResult> runGrading({
    required String imagePath,
    required MasterPacket masterPacket,
    required String selectedVersion,
  }) async {
    final orientedImagePath = await _fixExifOrientation(imagePath);
    final appDir = await getApplicationSupportDirectory();
    final profilePath = '${appDir.path}/active_profile.yml';
    final docDir = await getApplicationDocumentsDirectory();
    final scanStamp = DateTime.now().microsecondsSinceEpoch;
    final debugImagePath = '${docDir.path}/scan_debug_$scanStamp.png';

    final resultJsonStr = NativeOmrBindings.instance.callRun(orientedImagePath, profilePath, debugImagePath);
    final rawResult = jsonDecode(resultJsonStr) as Map<String, dynamic>;

    return buildGradeResult(rawResult, masterPacket, selectedVersion, orientedImagePath, debugImagePath);
  }

  GradeResult buildGradeResult(
    Map<String, dynamic> raw,
    MasterPacket masterPacket,
    String selectedVersion,
    String? imagePath, [
    String? debugImagePath,
  ]) {
    if (raw['success'] != true) {
      throw OmrEngineException(
        raw['error_code'] as String? ?? '',
        raw['error'] as String? ?? 'Unknown OMR engine error',
      );
    }

    String? finalStudentId;
    bool idReadable = true;
    final String letter = raw['letter'] as String? ?? "blank";

    if (masterPacket.idLetterCount > 0) {
      if (letter == 'blank' || letter == 'multiple_marks' || letter == 'rejected') idReadable = false;
    }

    final List<String> digits = (raw['digits'] as List?)?.map((e) => e as String).toList() ?? [];
    for (final d in digits) {
      if (d == 'blank' || d == 'multiple_marks' || d == 'rejected') idReadable = false;
    }

    if (idReadable) {
      final buffer = StringBuffer();
      if (masterPacket.idLetterCount > 0 && letter != "blank" && letter.isNotEmpty) buffer.write(letter);
      buffer.write(digits.join(""));
      finalStudentId = buffer.toString();
      if (finalStudentId.isEmpty) finalStudentId = null;
    }

    // Group type reuses the same letter already read for the ID block —
    // it's the same physical bubble, just also surfaced as its own field.
    String? finalGroupType;
    if (masterPacket.idLetterCount > 0 &&
        letter != 'blank' && letter != 'multiple_marks' && letter != 'rejected') {
      finalGroupType = letter;
    }

    double mcqScore = 0.0;
    List<Mistake> mistakes = [];
    final List<String> answers = (raw['answers'] as List?)?.map((e) => e as String).toList() ?? [];
    final modelAnswers = masterPacket.modelAnswers[selectedVersion] ?? {};
    final voidedList = masterPacket.voidedQuestions[selectedVersion] ?? [];

    for (int i = 0; i < answers.length; i++) {
      final qNum = i + 1;
      if (voidedList.contains(qNum)) continue;

      final given = answers[i];
      final correct = modelAnswers[qNum.toString()] ?? "";
      if (correct.isEmpty) continue;

      if (given == correct) {
        final range = masterPacket.rangeForQuestion(qNum);
        if (range != null) mcqScore += range.points;
      } else {
        mistakes.add(Mistake(question: qNum, correct: correct, given: given));
      }
    }

    return GradeResult(
      studentId: finalStudentId,
      idSource: finalStudentId == null ? "manual" : "ocr",
      groupType: finalGroupType,
      answerVersion: selectedVersion,
      mcqScore: mcqScore,
      mistakes: mistakes,
      essayTotal: 0.0,
      imagePath: imagePath,
      debugImagePath: debugImagePath,
    );
  }
}