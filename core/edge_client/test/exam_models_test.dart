import 'package:flutter_test/flutter_test.dart';
import 'package:smart_grader/domain/entities/exam_models.dart';

void main() {
  group('McqRange', () {
    test('normal case and boundaries', () {
      final range = McqRange.fromJson({'start': 5, 'end': 10, 'points': 2.5});
      expect(range.start, 5);
      expect(range.end, 10);
      expect(range.points, 2.5);

      expect(range.covers(5), isTrue);
      expect(range.covers(10), isTrue);
      expect(range.covers(7), isTrue);
      expect(range.covers(4), isFalse);
      expect(range.covers(11), isFalse);

      final json = range.toJson();
      expect(json, {'start': 5, 'end': 10, 'points': 2.5});
    });
  });

  group('RosterEntry', () {
    test('present 1', () {
      final entry = RosterEntry.fromJson({'id': 'S001', 'name': 'Alice', 'present': 1});
      expect(entry.id, 'S001');
      expect(entry.name, 'Alice');
      expect(entry.present, 1);

      expect(entry.toJson(), {'id': 'S001', 'name': 'Alice', 'present': 1});
    });

    test('present 0', () {
      final entry = RosterEntry.fromJson({'id': 'S002', 'name': 'Bob', 'present': 0});
      expect(entry.present, 0);
      expect(entry.toJson(), {'id': 'S002', 'name': 'Bob', 'present': 0});
    });
  });

  group('MasterPacket', () {
    test('Full realistic packet', () {
      final json = {
        'exam_name': 'Midterm',
        'exam_mode': 'quiz',
        'mcq_count': 30,
        'mcq_ranges': [
          {'start': 1, 'end': 30, 'points': 1.0}
        ],
        'has_essays': false,
        'essay_points_map': {},
        'template_path': 'foo.jpg',
        'roi_coordinates': {'x': 0, 'y': 0, 'w': 100, 'h': 100},
        'group_name': 'Sec A',
        'roster': [{'id': 'S1', 'name': 'A', 'present': 1}],
        'answer_versions': ['A'],
        'model_answers': {'A': {'1': 'A'}},
        'voided_questions': {'A': []},
        'choices_per_question': 5,
        'questions_per_block': 5,
        'id_letter_count': 0,
        'id_digit_columns': 4
      };

      final packet = MasterPacket.fromJson(json);
      expect(packet.examName, 'Midterm');
      expect(packet.examMode, 'quiz');
      expect(packet.mcqCount, 30);
      expect(packet.supportsMultipleVersions, isFalse);
      expect(packet.hasEssays, false);
      expect(packet.essayMaxTotal, 0.0);

      final outJson = packet.toJson();
      expect(outJson['exam_name'], 'Midterm');
      expect(outJson['choices_per_question'], 5);
      expect(outJson['id_digit_columns'], 4);
      expect(outJson['answer_versions'], ['A']);
    });

    test('Shamel-style multiple versions', () {
      final json = {
        'exam_name': 'Final',
        'mcq_count': 10,
        'mcq_ranges': [],
        'answer_versions': ['A', 'B', 'C'],
        'model_answers': {},
        'voided_questions': {}
      };
      final packet = MasterPacket.fromJson(json);
      expect(packet.supportsMultipleVersions, isTrue);
      expect(packet.toJson()['answer_versions'], ['A', 'B', 'C']);
    });

    test('Empty roster and empty mcqRanges', () {
      final json = {
        'exam_name': 'Test',
        'mcq_count': 10,
        'mcq_ranges': [],
        'roster': [],
        'answer_versions': ['A'],
        'model_answers': {}
      };
      final packet = MasterPacket.fromJson(json);
      expect(packet.mcqRanges, isEmpty);
      expect(packet.roster, isEmpty);
    });

    test('hasEssays with essayPointsMap sums correctly', () {
      final json = {
        'exam_name': 'Test',
        'mcq_count': 10,
        'has_essays': true,
        'essay_points_map': {'1': 5.0, '2': 10.0},
        'mcq_ranges': [],
        'answer_versions': ['A'],
        'model_answers': {}
      };
      final packet = MasterPacket.fromJson(json);
      expect(packet.essayMaxTotal, 15.0);
    });

    test('rangeForQuestion', () {
      final json = {
        'exam_name': 'Test',
        'mcq_count': 20,
        'mcq_ranges': [
          {'start': 1, 'end': 5, 'points': 1.0},
          {'start': 10, 'end': 15, 'points': 2.0}
        ],
        'answer_versions': ['A'],
        'model_answers': {}
      };
      final packet = MasterPacket.fromJson(json);
      expect(packet.rangeForQuestion(3)?.points, 1.0);
      expect(packet.rangeForQuestion(12)?.points, 2.0);
      expect(packet.rangeForQuestion(7), isNull); // Gap
    });
  });

  group('Mistake', () {
    test('normal case', () {
      final m = Mistake.fromJson({'question': 1, 'correct': 'A', 'given': 'B'});
      expect(m.question, 1);
      expect(m.correct, 'A');
      expect(m.given, 'B');
      expect(m.toJson(), {'question': 1, 'correct': 'A', 'given': 'B'});
    });
  });

  group('GradeResult', () {
    test('totalScore, mutation, and toSubmitScorePayload', () {
      final result = GradeResult(
        studentId: 'S001',
        idSource: 'ocr',
        answerVersion: 'A',
        mcqScore: 20.0,
        mistakes: [Mistake(question: 1, correct: 'A', given: 'B')],
        essayTotal: 5.0
      );

      expect(result.totalScore, 25.0);

      // Mutate
      result.studentId = 'S002';
      result.essayTotal = 10.0;
      expect(result.studentId, 'S002');
      expect(result.totalScore, 30.0);

      final payload = result.toSubmitScorePayload('2026-08-01T12:00:00Z');
      expect(payload, {
        'type': 'submit_score',
        'student_id': 'S002',
        'answer_version': 'A',
        'mcq_score': 20.0,
        'essay_total': 10.0,
        'total_score': 30.0,
        'mistakes': [{'question': 1, 'correct': 'A', 'given': 'B'}],
        'id_source': 'ocr',
        'timestamp': '2026-08-01T12:00:00Z'
      });
    });
  });

  group('DuplicateComparison', () {
    test('parses realistic shape', () {
      final json = {
        'student_id': 'S001',
        'previous': {
          'score': 90.0,
          'answer_version': 'A',
          'timestamp': '2026-08-01'
        },
        'incoming': {
          'score': 95.0,
          'answer_version': null,
          'timestamp': '2026-08-02'
        }
      };
      final comp = DuplicateComparison.fromJson(json);
      expect(comp.studentId, 'S001');
      expect(comp.previousScore, 90.0);
      expect(comp.previousVersion, 'A');
      expect(comp.incomingScore, 95.0);
      expect(comp.incomingVersion, isNull);
    });
  });

  group('DuplicateAction', () {
    test('wireValue', () {
      expect(DuplicateAction.overwrite.wireValue, 'overwrite');
      expect(DuplicateAction.keepPrevious.wireValue, 'keep_previous');
      expect(DuplicateAction.discardBoth.wireValue, 'discard_both');
    });
  });
}