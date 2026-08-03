import 'dart:async';
import 'dart:ui' as ui;
import 'package:flutter_test/flutter_test.dart';
import 'package:nexus_edge_mobile/providers/exam_session_provider.dart';
import 'package:nexus_edge_mobile/services/websocket_client.dart';
import 'package:nexus_edge_mobile/models/exam_models.dart';

class FakeWebSocketClient implements WebSocketClient {
  final statusController = StreamController<ConnectionStatus>.broadcast();
  final eventController = StreamController<ServerEvent>.broadcast();

  @override
  Stream<ConnectionStatus> get statusStream => statusController.stream;

  @override
  Stream<ServerEvent> get eventStream => eventController.stream;

  Map<String, dynamic>? lastSubmittedScore;
  DuplicateAction? lastResolvedAction;

  @override
  Future<void> connect({required String host, required String name, required String password, int port = 8765}) async {}

  @override
  void disconnect() {}

  @override
  void dispose() {}

  @override
  void resolveDuplicate({required String studentId, required DuplicateAction action, Map<String, dynamic>? newScorePayload}) {
    lastResolvedAction = action;
  }

  @override
  void submitScore(Map<String, dynamic> payload) {
    lastSubmittedScore = payload;
  }
}

void main() {
  group('ExamSessionProvider State Transitions', () {
    late FakeWebSocketClient fakeClient;
    late ExamSessionProvider provider;
    late MasterPacket dummyPacket;

    setUp(() {
      fakeClient = FakeWebSocketClient();
      provider = ExamSessionProvider(fakeClient);
      
      dummyPacket = MasterPacket.fromJson({
        'exam_name': 'Test',
        'mcq_count': 10,
        'mcq_ranges': [],
        'answer_versions': ['A', 'B'],
        'model_answers': {},
        'voided_questions': {}
      });
    });

    test('Fresh provider starts disconnected', () {
      expect(provider.phase, SessionPhase.disconnected);
    });

    test('AuthSuccess event, hasCalibratedProfile == false', () async {
      fakeClient.eventController.add(AuthSuccess(dummyPacket));
      await Future.microtask(() {}); // allow stream to process
      
      expect(provider.phase, SessionPhase.needsCalibration);
      expect(provider.masterPacket, isNotNull);
      // verify selectedVersion defaults to answerVersions.first
      expect(provider.selectedVersion, 'A');
    });

    test('AuthSuccess event, hasCalibratedProfile == true', () async {
      // mark calibrated first
      provider.markCalibrated(const ui.Size(100, 100));
      expect(provider.phase, SessionPhase.scanning);
      
      // Simulate reconnect/AuthSuccess
      fakeClient.eventController.add(AuthSuccess(dummyPacket));
      await Future.microtask(() {});
      
      // Phase should go directly to scanning
      expect(provider.phase, SessionPhase.scanning);
    });

    test('AuthFailure event sets error phase', () async {
      fakeClient.eventController.add(AuthFailure('Wrong password'));
      await Future.microtask(() {});
      
      expect(provider.phase, SessionPhase.error);
      expect(provider.errorMessage, 'Wrong password');
    });

    test('requestRecalibrate sets phase to needsCalibration', () {
      provider.markCalibrated(const ui.Size(100, 100));
      expect(provider.phase, SessionPhase.scanning);
      
      provider.requestRecalibrate();
      expect(provider.phase, SessionPhase.needsCalibration);
    });

    test('setCurrentScan and clearCurrentScan', () {
      final scan = GradeResult(
        studentId: '123', idSource: 'ocr', answerVersion: 'A', 
        mcqScore: 10, mistakes: [], essayTotal: 0
      );
      
      provider.setCurrentScan(scan);
      expect(provider.currentScan, isNotNull);
      
      provider.clearCurrentScan();
      expect(provider.currentScan, isNull);
      expect(provider.pendingDuplicate, isNull);
    });

    test('ScoreDuplicate event populates pendingDuplicate', () async {
      final comp = DuplicateComparison.fromJson({
        'student_id': '123',
        'previous': {'score': 90.0, 'answer_version': 'A', 'timestamp': '2026-08-01'},
        'incoming': {'score': 95.0, 'answer_version': 'A', 'timestamp': '2026-08-02'}
      });
      
      fakeClient.eventController.add(ScoreDuplicate(comp));
      await Future.microtask(() {});
      
      expect(provider.pendingDuplicate, isNotNull);
      expect(provider.pendingDuplicate!.studentId, '123');
    });
  });
}
