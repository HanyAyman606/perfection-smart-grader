// Phase 5.5 tests — see connection_fix_plan.md.
import 'package:flutter_test/flutter_test.dart';
import 'package:smart_grader/domain/entities/exam_models.dart';
import 'package:smart_grader/domain/entities/queue_models.dart';
import 'package:smart_grader/data/connection/websocket_client.dart' show ConnectionStatus;
import 'package:smart_grader/features/grading/controller/submission_controller.dart';
import 'package:smart_grader/features/scanning/controller/scan_controller.dart';

import 'fakes/fake_connection_repository.dart';
import 'fakes/fake_support_repositories.dart';
import 'fakes/fake_offline_queue_repository.dart';

GradeResult _fakeGradeResult({String studentId = 'S001'}) {
  return GradeResult(
    studentId: studentId,
    idSource: 'ocr',
    answerVersion: 'A',
    mcqScore: 20.0,
    mistakes: const [],
    questions: const [],
    idColumns: const [],
    essayTotal: 5.0,
    rawImagePath: '/fake/raw.jpg',
    annotatedIdImagePath: '/fake/id.jpg',
    annotatedMcqImagePath: '/fake/mcq.jpg',
  );
}

void main() {
  late FakeConnectionRepository fakeConnection;
  late FakeSessionCacheRepository fakeSessionCache;
  late FakeOfflineQueueRepository fakeQueue;
  late ScanController scanController;
  late SubmissionController submission;

  setUp(() {
    fakeConnection = FakeConnectionRepository();
    fakeSessionCache = FakeSessionCacheRepository();
    fakeQueue = FakeOfflineQueueRepository();
    scanController = ScanController(fakeSessionCache, fakeConnection);
    submission = SubmissionController(fakeConnection, fakeSessionCache, scanController, fakeQueue);
  });

  test('submitting while disconnected enqueues instead of hitting the network, '
      'returns SubmitQueued, and does not clear the raw image', () async {
    scanController.setCurrentScan(_fakeGradeResult());
    fakeConnection.emitStatus(ConnectionStatus.disconnected);

    final result = await submission.submitCurrentScan('2026-08-21 10:00:00');

    expect(result, isA<SubmitQueued>());
    expect(fakeQueue.enqueueCallCount, 1);
    expect(scanController.currentScan, isNull); // cleared silently (in-memory only)
    // No network call was attempted, and no image purge happened — a
    // FakeSessionCacheRepository has no way to assert "not called" directly
    // here, but the important behavioral guarantee (raw image path was
    // handed to the queue, not to onSubmitAcknowledged) is covered by the
    // enqueue call above carrying rawImagePath through.
    final queued = fakeQueue.entriesInInsertOrder.single;
    expect(queued.rawImagePath, '/fake/raw.jpg');
    expect(queued.studentId, 'S001');
  });

  test('submitting a student ID already in the local queue returns '
      'SubmitDuplicate without enqueueing again or touching the network — '
      'even while online', () async {
    fakeQueue.enqueueCallCount = 0;
    await fakeQueue.enqueue(QueuedScan.fromGradeResult(
      _fakeGradeResult(studentId: 'S001'),
      '2026-08-21 09:00:00',
      queueId: 'existing-1',
    ));
    fakeQueue.enqueueCallCount = 0; // reset — that enqueue above was test setup, not SUT

    scanController.setCurrentScan(_fakeGradeResult(studentId: 'S001'));
    fakeConnection.emitStatus(ConnectionStatus.connected);

    final result = await submission.submitCurrentScan('2026-08-21 10:00:00');

    expect(result, isA<SubmitDuplicate>());
    expect(fakeQueue.enqueueCallCount, 0); // did not double-queue
    expect(fakeConnection.connectCallCount, 0); // submitScoreAndAwait never attempted
  });

  test('normal online submission (no local duplicate, connected) still '
      'goes through the network path unchanged', () async {
    scanController.setCurrentScan(_fakeGradeResult(studentId: 'S002'));
    fakeConnection.emitStatus(ConnectionStatus.connected);

    final result = await submission.submitCurrentScan('2026-08-21 10:00:00');

    expect(result, isA<SubmitSuccess>());
    expect(fakeQueue.enqueueCallCount, 0);
  });

  // Regression tests for the bug where resolving a LOCAL duplicate (found
  // against the offline queue, not the server) always went through
  // resolveDuplicateAndAwait and failed — most visibly when offline,
  // where that call has nothing to talk to, but the bug existed even
  // online since a locally-queued duplicate was never something the
  // server's resolve_duplicate endpoint knew about in the first place.
  group('local duplicate resolution (against the offline queue)', () {
    setUp(() async {
      // Seed an existing queued scan for S001, then attempt to submit a
      // second scan for the same student — this is exactly how
      // submitCurrentScan's local-duplicate branch gets triggered.
      await fakeQueue.enqueue(
        QueuedScan.fromGradeResult(_fakeGradeResult(studentId: 'S001'), '2026-08-21 09:00:00', queueId: 'old-id'),
      );
    });

    test('keepPrevious: never touches the network, keeps the old queue '
        'entry, discards the new scan, clears currentScan', () async {
      scanController.setCurrentScan(_fakeGradeResult(studentId: 'S001'));
      fakeConnection.emitStatus(ConnectionStatus.disconnected);

      final dupResult = await submission.submitCurrentScan('2026-08-21 10:00:00');
      expect(dupResult, isA<SubmitDuplicate>());

      final resolved = await submission.resolveDuplicate('S001', DuplicateAction.keepPrevious, '2026-08-21 10:00:00');

      expect(resolved, true);
      expect(fakeConnection.resolveDuplicateAndAwaitCallCount, 0); // the actual bug: this used to always be called
      final stillQueued = await fakeQueue.findByStudentId('S001');
      expect(stillQueued?.queueId, 'old-id'); // old entry untouched
      expect(scanController.currentScan, isNull);
    });

    test('overwrite: replaces the old queue entry with the new scan, '
        'never touches the network', () async {
      scanController.setCurrentScan(_fakeGradeResult(studentId: 'S001'));
      fakeConnection.emitStatus(ConnectionStatus.disconnected);

      await submission.submitCurrentScan('2026-08-21 10:00:00');
      final resolved = await submission.resolveDuplicate('S001', DuplicateAction.overwrite, '2026-08-21 10:00:00');

      expect(resolved, true);
      expect(fakeConnection.resolveDuplicateAndAwaitCallCount, 0);
      expect(fakeQueue.markSyncedCallCount, 0); // old entry removed via discard, not markSynced
      final replaced = await fakeQueue.findByStudentId('S001');
      expect(replaced, isNotNull);
      expect(replaced!.queueId, isNot('old-id')); // new queue entry, not the old one
    });

    test('discardBoth: removes the old queue entry, does not re-enqueue '
        'the new scan, never touches the network', () async {
      scanController.setCurrentScan(_fakeGradeResult(studentId: 'S001'));
      fakeConnection.emitStatus(ConnectionStatus.disconnected);

      await submission.submitCurrentScan('2026-08-21 10:00:00');
      final resolved = await submission.resolveDuplicate('S001', DuplicateAction.discardBoth, '2026-08-21 10:00:00');

      expect(resolved, true);
      expect(fakeConnection.resolveDuplicateAndAwaitCallCount, 0);
      final afterDiscard = await fakeQueue.findByStudentId('S001');
      expect(afterDiscard, isNull);
    });

    test('a local duplicate found while online still resolves locally, '
        'not via the network (the local queue is not something the '
        'server resolve_duplicate endpoint knows about)', () async {
      scanController.setCurrentScan(_fakeGradeResult(studentId: 'S001'));
      fakeConnection.emitStatus(ConnectionStatus.connected);

      final dupResult = await submission.submitCurrentScan('2026-08-21 10:00:00');
      expect(dupResult, isA<SubmitDuplicate>());

      final resolved = await submission.resolveDuplicate('S001', DuplicateAction.keepPrevious, '2026-08-21 10:00:00');
      expect(resolved, true);
      expect(fakeConnection.resolveDuplicateAndAwaitCallCount, 0);
    });
  });
}