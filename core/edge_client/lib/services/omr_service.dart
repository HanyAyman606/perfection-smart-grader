import 'dart:convert';
import 'dart:io';
import 'package:image/image.dart' as img;
import 'package:path_provider/path_provider.dart';
import '../models/exam_models.dart';
import 'native_omr_bindings.dart';

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
    // 1. Fix EXIF orientation
    final orientedImagePath = await _fixExifOrientation(imagePath);

    // 2. Locate Profile
    final appDir = await getApplicationSupportDirectory();
    final profilePath = '${appDir.path}/active_profile.yml';

    // 3. FFI Call
    final resultJsonStr = NativeOmrBindings.instance.callRun(orientedImagePath, profilePath, "");
    final rawResult = jsonDecode(resultJsonStr) as Map<String, dynamic>;

    return buildGradeResult(rawResult, masterPacket, selectedVersion);
  }

  GradeResult buildGradeResult(
    Map<String, dynamic> raw,
    MasterPacket masterPacket,
    String selectedVersion,
  ) {
    if (raw['success'] != true) {
      throw Exception("OMR Engine Error: ${raw['error']}");
    }

    // --- Process ID ---
    String? finalStudentId;
    bool idReadable = true;

    final String letter = raw['letter'] as String? ?? "blank";

    // Gate letter readability check on idLetterCount from the real MasterPacket field
    if (masterPacket.idLetterCount > 0) {
      if (letter == 'blank' || letter == 'multiple_marks' || letter == 'rejected') {
        idReadable = false;
      }
    }

    final List<String> digits = (raw['digits'] as List?)?.map((e) => e as String).toList() ?? [];
    for (final d in digits) {
      if (d == 'blank' || d == 'multiple_marks' || d == 'rejected') {
        idReadable = false;
      }
    }

    if (idReadable) {
      final buffer = StringBuffer();
      // Only include the letter if the template actually has a letter column
      if (masterPacket.idLetterCount > 0 && letter != "blank" && letter.isNotEmpty) {
        buffer.write(letter);
      }
      buffer.write(digits.join(""));
      finalStudentId = buffer.toString();
      if (finalStudentId.isEmpty) {
        finalStudentId = null;
      }
    }

    // --- Process Answers ---
    double mcqScore = 0.0;
    List<Mistake> mistakes = [];
    final List<String> answers = (raw['answers'] as List?)?.map((e) => e as String).toList() ?? [];

    final modelAnswers = masterPacket.modelAnswers[selectedVersion] ?? {};
    final voidedList = masterPacket.voidedQuestions[selectedVersion] ?? [];

    for (int i = 0; i < answers.length; i++) {
      final qNum = i + 1;

      // Skip voided questions entirely — no points, no mistake
      if (voidedList.contains(qNum)) {
        continue;
      }

      final given = answers[i];
      final correct = modelAnswers[qNum.toString()] ?? "";

      // Skip questions with no model answer configured — same as voided:
      // no points awarded, no mistake recorded
      if (correct.isEmpty) {
        continue;
      }

      if (given == correct) {
        final range = masterPacket.rangeForQuestion(qNum);
        if (range != null) {
          mcqScore += range.points;
        }
      } else {
        mistakes.add(Mistake(question: qNum, correct: correct, given: given));
      }
    }

    return GradeResult(
      studentId: finalStudentId,
      idSource: finalStudentId == null ? "manual" : "ocr",
      answerVersion: selectedVersion,
      mcqScore: mcqScore,
      mistakes: mistakes,
      essayTotal: 0.0, // Set later by grading review screen
    );
  }
}
