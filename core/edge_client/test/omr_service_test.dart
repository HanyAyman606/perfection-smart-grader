import 'package:flutter_test/flutter_test.dart';
import 'package:nexus_edge_mobile/services/omr_service.dart';
import 'package:nexus_edge_mobile/models/exam_models.dart';

void main() {
  group('OmrService - buildGradeResult', () {
    late MasterPacket masterPacket;

    setUp(() {
      masterPacket = MasterPacket.fromJson({
        'exam_name': 'Test',
        'exam_mode': 'quiz',
        'mcq_count': 5,
        'mcq_ranges': [
          {'start': 1, 'end': 3, 'points': 1.0},
          {'start': 4, 'end': 5, 'points': 2.0},
        ],
        'answer_versions': ['A'],
        'model_answers': {
          'A': {'1': 'A', '2': 'B', '3': 'C', '4': 'D', '5': 'A'}
        },
        'voided_questions': {
          'A': [3] // Q3 is voided
        },
        'id_letter_count': 1,
        'id_digit_columns': 3,
      });
    });

    test('All correct, nothing voided (except Q3 which is voided by config)', () {
      final raw = {
        'success': true,
        'letter': 'A',
        'digits': ['1', '2', '3'],
        'answers': ['A', 'B', 'X', 'D', 'A'] // Index 2 (Q3) is 'X' but it's voided
      };

      final result = OmrService.instance.buildGradeResult(raw, masterPacket, 'A');
      
      // Expected score: Q1(1) + Q2(1) + Q4(2) + Q5(2) = 6
      expect(result.mcqScore, 6.0);
      expect(result.mistakes, isEmpty);
    });

    test('A genuine wrong answer', () {
      final raw = {
        'success': true,
        'letter': 'A',
        'digits': ['1', '2', '3'],
        'answers': ['B', 'B', 'C', 'D', 'A'] // Q1 is 'B' but correct is 'A'
      };

      final result = OmrService.instance.buildGradeResult(raw, masterPacket, 'A');
      
      // Expected score: Q2(1) + Q4(2) + Q5(2) = 5
      expect(result.mcqScore, 5.0);
      expect(result.mistakes.length, 1);
      expect(result.mistakes.first.question, 1);
      expect(result.mistakes.first.correct, 'A');
      expect(result.mistakes.first.given, 'B');
    });

    test('Voided question with a wrong-looking answer never scores or mistakes', () {
      final rawIncorrect = {
        'success': true,
        'letter': 'A',
        'digits': ['1', '2', '3'],
        'answers': ['A', 'B', 'Z', 'D', 'A'] // Z for voided Q3
      };
      final resultIncorrect = OmrService.instance.buildGradeResult(rawIncorrect, masterPacket, 'A');
      expect(resultIncorrect.mcqScore, 6.0);
      expect(resultIncorrect.mistakes, isEmpty);

      final rawCorrect = {
        'success': true,
        'letter': 'A',
        'digits': ['1', '2', '3'],
        'answers': ['A', 'B', 'C', 'D', 'A'] // C for voided Q3
      };
      final resultCorrect = OmrService.instance.buildGradeResult(rawCorrect, masterPacket, 'A');
      expect(resultCorrect.mcqScore, 6.0);
      expect(resultCorrect.mistakes, isEmpty);
    });

    test('Question with no model answer at all is skipped', () {
      final masterNoAnsQ5 = MasterPacket.fromJson({
        'exam_name': 'Test',
        'exam_mode': 'quiz',
        'mcq_count': 5,
        'mcq_ranges': [
          {'start': 1, 'end': 3, 'points': 1.0},
          {'start': 4, 'end': 5, 'points': 2.0},
        ],
        'answer_versions': ['A'],
        'model_answers': {
          'A': {'1': 'A', '2': 'B', '3': 'C', '4': 'D'} // 5 is missing entirely
        },
        'voided_questions': {'A': []},
        'id_letter_count': 1,
        'id_digit_columns': 3,
      });

      final raw = {
        'success': true,
        'letter': 'A',
        'digits': ['1', '2', '3'],
        'answers': ['A', 'B', 'C', 'D', 'A'] 
      };

      final result = OmrService.instance.buildGradeResult(raw, masterNoAnsQ5, 'A');
      
      // Expected score: Q1(1) + Q2(1) + Q3(1) + Q4(2) = 5
      expect(result.mcqScore, 5.0);
      expect(result.mistakes, isEmpty); // Q5 should not generate a mistake
    });

    test('blank, multiple_marks, rejected as given values', () {
      final raw = {
        'success': true,
        'letter': 'A',
        'digits': ['1', '2', '3'],
        'answers': ['blank', 'multiple_marks', 'C', 'rejected', 'A'] 
      };

      final result = OmrService.instance.buildGradeResult(raw, masterPacket, 'A');
      
      // Q1: blank -> Mistake
      // Q2: multiple_marks -> Mistake
      // Q3: voided -> skipped
      // Q4: rejected -> Mistake
      // Q5: A -> correct
      
      expect(result.mcqScore, 2.0); // Only Q5 gives 2 points
      expect(result.mistakes.length, 3);
      
      expect(result.mistakes[0].question, 1);
      expect(result.mistakes[0].given, 'blank');
      
      expect(result.mistakes[1].question, 2);
      expect(result.mistakes[1].given, 'multiple_marks');
      
      expect(result.mistakes[2].question, 4);
      expect(result.mistakes[2].given, 'rejected');
    });

    test('ID readability - clean read', () {
      final raw = {
        'success': true,
        'letter': 'C',
        'digits': ['1', '2', '7'],
        'answers': ['A', 'B', 'C', 'D', 'A']
      };

      final result = OmrService.instance.buildGradeResult(raw, masterPacket, 'A');
      expect(result.studentId, 'C127');
      expect(result.idSource, 'ocr');
    });

    test('ID readability - unreadable letter', () {
      final raw = {
        'success': true,
        'letter': 'blank',
        'digits': ['1', '2', '7'],
        'answers': ['A', 'B', 'C', 'D', 'A']
      };

      final result = OmrService.instance.buildGradeResult(raw, masterPacket, 'A');
      expect(result.studentId, isNull);
      expect(result.idSource, 'manual');
    });

    test('ID readability - unreadable digit', () {
      final raw = {
        'success': true,
        'letter': 'C',
        'digits': ['1', 'multiple_marks', '7'],
        'answers': ['A', 'B', 'C', 'D', 'A']
      };

      final result = OmrService.instance.buildGradeResult(raw, masterPacket, 'A');
      expect(result.studentId, isNull);
      expect(result.idSource, 'manual');
    });

    test('Points-per-range boundary scores correctly', () {
      final raw = {
        'success': true,
        'letter': 'A',
        'digits': ['1', '2', '3'],
        'answers': ['B', 'B', 'A', 'A', 'A'] 
        // Q1: B (wrong) -> 0/1pt
        // Q2: B (correct) -> 1pt
        // Q3: voided -> skipped
        // Q4: A (wrong) -> 0/2pts
        // Q5: A (correct) -> 2pts
      };

      final result = OmrService.instance.buildGradeResult(raw, masterPacket, 'A');
      
      expect(result.mcqScore, 3.0);
      expect(result.mistakes.length, 2);
      expect(result.mistakes[0].question, 1); // missed 1pt
      expect(result.mistakes[1].question, 4); // missed 2pts
    });
  });
}
