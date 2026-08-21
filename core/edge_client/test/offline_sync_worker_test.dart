// Phase 5.7 tests — see connection_fix_plan.md.
import 'package:flutter_test/flutter_test.dart';
import 'package:smart_grader/data/connection/websocket_client.dart' show ConnectionStatus;
import 'package:smart_grader/data/queue/offline_sync_worker.dart';
import 'package:smart_grader/domain/entities/queue_models.dart';
import 'package:smart_grader/domain/entities/exam_models.dart' show DuplicateAction;

import 'fakes/fake_connection_repository.dart';
import 'fakes/fake_offline_queue_repository.dart';
import 'fakes/fake_support_repositories.dart';

QueuedScan _queued(String id, {String studentId = 'S001', DateTime? queuedAt}) {
  return QueuedScan(
    queueId: id,
    payload: {'student_id': studentId, 'total_score': 10.0, 'timestamp': '2026-08-21 10:00:00'},
    queuedAt: queuedAt ?? DateTime.now(),
    rawImagePath: '/fake/raw_$id.jpg',
    annotatedIdImagePath: '/fake/id_$id.jpg',
    annotatedMcqImagePath: '/fake/mcq_$id.jpg',
  );
}

Map<String, dynamic> _duplicateResponse({
  String studentId = 'S001',
  double previousScore = 15.0,
  double incomingScore = 18.0,
}) {
  return {
    'status': 'duplicate',
    'student_id': studentId,
    'previous': {'score': previousScore, 'answer_version': 'A', 'timestamp': '2026-08-20 09:00:00'},
    'incoming': {'score': incomingScore, 'answer_version': 'A', 'timestamp': '2026-08-21 10:00:00'},
  };
}

void main() {
  late FakeConnectionRepository fakeConnection;
  late FakeOfflineQueueRepository fakeQueue;
  late FakeSessionCacheRepository fakeSessionCache;
  late FakeLogRepository fakeLog;
  late OfflineSyncWorker worker;

  setUp(() {
    fakeConnection = FakeConnectionRepository();
    fakeQueue = FakeOfflineQueueRepository();
    fakeSessionCache = FakeSessionCacheRepository();
    fakeLog = FakeLogRepository();
    worker = OfflineSyncWorker(fakeConnection, fakeQueue, fakeSessionCache, log: fakeLog);
  });

  tearDown(() {
    worker.dispose();
  });

  test('drains queue in oldest-first order on reconnect', () async {
    final now = DateTime.now();
    await fakeQueue.enqueue(_queued('a', studentId: 'S001', queuedAt: now.subtract(const Duration(minutes: 2))));
    await fakeQueue.enqueue(_queued('b', studentId: 'S002', queuedAt: now.subtract(const Duration(minutes: 1))));
    await fakeQueue.enqueue(_queued('c', studentId: 'S003', queuedAt: now));

    worker.start();
    fakeConnection.emitStatus(ConnectionStatus.connected);
    await Future.delayed(Duration.zero);
    await Future.delayed(Duration.zero);
    await Future.delayed(Duration.zero);

    expect(
      fakeConnection.submitScoreAndAwaitCalls.map((p) => p['student_id']).toList(),
      ['S001', 'S002', 'S003'],
    );
  });

  test('success purges the image and marks synced; duplicate does neither '
      '(see dedicated duplicate test below for that case in isolation)', () async {
    await fakeQueue.enqueue(_queued('a', studentId: 'S001'));
    await fakeQueue.enqueue(_queued('b', studentId: 'S002'));

    fakeConnection.submitScoreAndAwaitHandler = (payload) async {
      if (payload['student_id'] == 'S001') return {'status': 'success'};
      return {'status': 'duplicate'};
    };

    worker.start();
    fakeConnection.emitStatus(ConnectionStatus.connected);
    await Future.delayed(Duration.zero);
    await Future.delayed(Duration.zero);
    await Future.delayed(Duration.zero);

    // Only S001 (success) synced; S002 (duplicate) stays queued — see
    // the "server duplicate status does NOT silently purge" test for
    // the full assertion on that path.
    expect(fakeQueue.markSyncedCallCount, 1);
    expect(fakeSessionCache.onSubmitAcknowledgedCallCount, 1);
    final remaining = await fakeQueue.pending();
    expect(remaining.length, 1);
    expect(remaining.first.studentId, 'S002');
  });

  test('error/timeout leaves item pending and does not block later items', () async {
    await fakeQueue.enqueue(_queued('a', studentId: 'S001'));
    await fakeQueue.enqueue(_queued('b', studentId: 'S002'));

    fakeConnection.submitScoreAndAwaitHandler = (payload) async {
      if (payload['student_id'] == 'S001') {
        throw Exception('simulated timeout');
      }
      return {'status': 'success'};
    };

    worker.start();
    fakeConnection.emitStatus(ConnectionStatus.connected);
    await Future.delayed(Duration.zero);
    await Future.delayed(Duration.zero);
    await Future.delayed(Duration.zero);

    // S002 still got attempted and synced despite S001 failing first.
    expect(
      fakeConnection.submitScoreAndAwaitCalls.map((p) => p['student_id']),
      containsAll(['S001', 'S002']),
    );
    expect(fakeQueue.markSyncedCallCount, 1);

    final remaining = await fakeQueue.pending();
    expect(remaining.length, 1);
    expect(remaining.first.studentId, 'S001');
  });

  test('a disconnect mid-drain stops the worker cleanly, no double-send on resume', () async {
    await fakeQueue.enqueue(_queued('a', studentId: 'S001'));
    await fakeQueue.enqueue(_queued('b', studentId: 'S002'));

    fakeConnection.submitScoreAndAwaitHandler = (payload) async {
      if (payload['student_id'] == 'S001') {
        // Simulate the connection dropping the instant this item's
        // submission finishes, before the worker asks for the next one.
        fakeConnection.emitStatus(ConnectionStatus.disconnected);
        return {'status': 'success'};
      }
      return {'status': 'success'};
    };

    worker.start();
    fakeConnection.emitStatus(ConnectionStatus.connected);
    await Future.delayed(Duration.zero);
    await Future.delayed(Duration.zero);
    await Future.delayed(Duration.zero);

    // Only S001 was sent — worker stopped once it saw the disconnect,
    // never touched S002.
    expect(
      fakeConnection.submitScoreAndAwaitCalls.map((p) => p['student_id']).toList(),
      ['S001'],
    );
    final remaining = await fakeQueue.pending();
    expect(remaining.length, 1);
    expect(remaining.first.studentId, 'S002');

    // Reconnect: resumes and finishes the rest, without re-sending S001
    // (already marked synced and removed from the queue).
    fakeConnection.emitStatus(ConnectionStatus.connected);
    await Future.delayed(Duration.zero);
    await Future.delayed(Duration.zero);

    expect(
      fakeConnection.submitScoreAndAwaitCalls.map((p) => p['student_id']).toList(),
      ['S001', 'S002'],
    );
    expect(await fakeQueue.pending(), isEmpty);
  });

  test('server "duplicate" status does NOT silently purge images or mark '
      'synced — it must stay queued and visible, not vanish with no trace', () async {
    await fakeQueue.enqueue(_queued('a', studentId: 'S001'));

    fakeConnection.submitScoreAndAwaitHandler = (payload) async => {'status': 'duplicate'};

    worker.start();
    fakeConnection.emitStatus(ConnectionStatus.connected);
    await Future.delayed(Duration.zero);
    await Future.delayed(Duration.zero);

    // The old bug: this used to call markSynced + purge the image,
    // silently discarding the scan with no record it ever existed.
    expect(fakeQueue.markSyncedCallCount, 0);
    expect(fakeSessionCache.onSubmitAcknowledgedCallCount, 0);

    // Item stays in the queue, visible for manual resolution, not lost.
    final remaining = await fakeQueue.pending();
    expect(remaining.length, 1);
    expect(remaining.first.queueId, 'a');
  });

  // Regression coverage for the follow-up fix: instead of just flagging
  // a server duplicate for later manual review, the worker now offers a
  // resolveDuplicate callback (wired to the real DuplicateResolutionDialog
  // in main.dart) so it can be resolved right then, the same three ways
  // (overwrite/keepPrevious/discardBoth) the online flow already supports.
  group('with a duplicate resolver configured', () {
    late OfflineSyncWorker resolverWorker;

    tearDown(() {
      resolverWorker.dispose();
    });

    test('overwrite: calls resolveDuplicateAndAwait with the item\'s '
        'payload, then purges images and marks synced on success', () async {
      await fakeQueue.enqueue(_queued('a', studentId: 'S001'));
      fakeConnection.submitScoreAndAwaitHandler = (payload) async => _duplicateResponse(studentId: 'S001');

      resolverWorker = OfflineSyncWorker(
        fakeConnection,
        fakeQueue,
        fakeSessionCache,
        log: fakeLog,
        resolveDuplicate: (comparison) async {
          expect(comparison.studentId, 'S001');
          return DuplicateAction.overwrite;
        },
      );
      resolverWorker.start();
      fakeConnection.emitStatus(ConnectionStatus.connected);
      await Future.delayed(Duration.zero);
      await Future.delayed(Duration.zero);
      await Future.delayed(Duration.zero);

      expect(fakeConnection.resolveDuplicateAndAwaitCallCount, 1);
      expect(fakeConnection.resolveDuplicateAndAwaitCalls.first['action'], 'overwrite');
      expect(fakeConnection.resolveDuplicateAndAwaitCalls.first['new_score_payload'], isNotNull);
      expect(fakeQueue.markSyncedCallCount, 1);
      expect(fakeSessionCache.onSubmitAcknowledgedCallCount, 1);
      expect(await fakeQueue.pending(), isEmpty);
    });

    test('keepPrevious: calls resolveDuplicateAndAwait with no payload, '
        'still purges and marks synced (this item is resolved either way)', () async {
      await fakeQueue.enqueue(_queued('a', studentId: 'S001'));
      fakeConnection.submitScoreAndAwaitHandler = (payload) async => _duplicateResponse(studentId: 'S001');

      resolverWorker = OfflineSyncWorker(
        fakeConnection,
        fakeQueue,
        fakeSessionCache,
        log: fakeLog,
        resolveDuplicate: (comparison) async => DuplicateAction.keepPrevious,
      );
      resolverWorker.start();
      fakeConnection.emitStatus(ConnectionStatus.connected);
      await Future.delayed(Duration.zero);
      await Future.delayed(Duration.zero);
      await Future.delayed(Duration.zero);

      expect(fakeConnection.resolveDuplicateAndAwaitCallCount, 1);
      expect(fakeConnection.resolveDuplicateAndAwaitCalls.first['action'], 'keepPrevious');
      expect(fakeConnection.resolveDuplicateAndAwaitCalls.first['new_score_payload'], isNull);
      expect(fakeQueue.markSyncedCallCount, 1);
      expect(await fakeQueue.pending(), isEmpty);
    });

    test('discardBoth: same as keepPrevious — resolved and removed from '
        'the queue, no payload sent', () async {
      await fakeQueue.enqueue(_queued('a', studentId: 'S001'));
      fakeConnection.submitScoreAndAwaitHandler = (payload) async => _duplicateResponse(studentId: 'S001');

      resolverWorker = OfflineSyncWorker(
        fakeConnection,
        fakeQueue,
        fakeSessionCache,
        log: fakeLog,
        resolveDuplicate: (comparison) async => DuplicateAction.discardBoth,
      );
      resolverWorker.start();
      fakeConnection.emitStatus(ConnectionStatus.connected);
      await Future.delayed(Duration.zero);
      await Future.delayed(Duration.zero);
      await Future.delayed(Duration.zero);

      expect(fakeConnection.resolveDuplicateAndAwaitCalls.first['action'], 'discardBoth');
      expect(fakeQueue.markSyncedCallCount, 1);
      expect(await fakeQueue.pending(), isEmpty);
    });

    test('if the resolver cannot get a decision (throws — e.g. no '
        'navigator context), falls back to the safe flagged-and-queued '
        'state, same as no resolver at all', () async {
      await fakeQueue.enqueue(_queued('a', studentId: 'S001'));
      fakeConnection.submitScoreAndAwaitHandler = (payload) async => _duplicateResponse(studentId: 'S001');

      resolverWorker = OfflineSyncWorker(
        fakeConnection,
        fakeQueue,
        fakeSessionCache,
        log: fakeLog,
        resolveDuplicate: (comparison) async => throw StateError('no navigator context'),
      );
      resolverWorker.start();
      fakeConnection.emitStatus(ConnectionStatus.connected);
      await Future.delayed(Duration.zero);
      await Future.delayed(Duration.zero);
      await Future.delayed(Duration.zero);

      expect(fakeConnection.resolveDuplicateAndAwaitCallCount, 0); // never even attempted
      expect(fakeQueue.markSyncedCallCount, 0);
      expect(fakeSessionCache.onSubmitAcknowledgedCallCount, 0);
      final remaining = await fakeQueue.pending();
      expect(remaining.length, 1); // stays queued, not lost
    });

    test('if resolveDuplicateAndAwait itself reports failure, also falls '
        'back to the safe flagged-and-queued state', () async {
      await fakeQueue.enqueue(_queued('a', studentId: 'S001'));
      fakeConnection.submitScoreAndAwaitHandler = (payload) async => _duplicateResponse(studentId: 'S001');
      fakeConnection.resolveDuplicateAndAwaitHandler = ({
        required studentId,
        required action,
        newScorePayload,
      }) async =>
          {'status': 'error', 'message': 'server rejected'};

      resolverWorker = OfflineSyncWorker(
        fakeConnection,
        fakeQueue,
        fakeSessionCache,
        log: fakeLog,
        resolveDuplicate: (comparison) async => DuplicateAction.overwrite,
      );
      resolverWorker.start();
      fakeConnection.emitStatus(ConnectionStatus.connected);
      await Future.delayed(Duration.zero);
      await Future.delayed(Duration.zero);
      await Future.delayed(Duration.zero);

      expect(fakeQueue.markSyncedCallCount, 0);
      final remaining = await fakeQueue.pending();
      expect(remaining.length, 1);
    });
  });
}