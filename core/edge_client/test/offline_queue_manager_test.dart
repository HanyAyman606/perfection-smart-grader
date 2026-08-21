// Phase 5.2 tests — see connection_fix_plan.md.
//
// Uses sqflite_common_ffi's in-memory database factory so these run on the
// VM/CI without a real device or platform channel — no need for
// integration_test or a real phone.
import 'package:flutter_test/flutter_test.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';
import 'package:smart_grader/data/queue/offline_queue_manager.dart';
import 'package:smart_grader/domain/entities/queue_models.dart';

QueuedScan _scan(String queueId, String studentId, {DateTime? queuedAt}) {
  return QueuedScan(
    queueId: queueId,
    payload: {
      'type': 'submit_score',
      'student_id': studentId,
      'answer_version': 'A',
      'total_score': 42.0,
      'timestamp': '2026-08-21 10:00:00',
    },
    queuedAt: queuedAt ?? DateTime.now(),
    rawImagePath: '/fake/raw_$queueId.jpg',
    annotatedIdImagePath: '/fake/id_$queueId.jpg',
    annotatedMcqImagePath: '/fake/mcq_$queueId.jpg',
  );
}

void main() {
  setUpAll(() {
    sqfliteFfiInit();
  });

  // Fresh in-memory DB per test — `:memory:` with the ffi factory, no file
  // left behind, no cross-test pollution.
  OfflineQueueManager makeManager() {
    return OfflineQueueManager(
      openOverride: () => databaseFactoryFfi.openDatabase(
        inMemoryDatabasePath,
        options: OpenDatabaseOptions(
          version: 1,
          onCreate: (db, version) => db.execute('''
            CREATE TABLE queued_scans (
              queue_id TEXT PRIMARY KEY,
              payload_json TEXT NOT NULL,
              raw_image_path TEXT,
              annotated_id_path TEXT,
              annotated_mcq_path TEXT,
              queued_at INTEGER NOT NULL,
              sync_state TEXT NOT NULL,
              attempt_count INTEGER NOT NULL DEFAULT 0
            )
          '''),
        ),
      ),
    );
  }

  test('enqueue then pending returns it, oldest first', () async {
    final mgr = makeManager();
    final t0 = DateTime(2026, 8, 21, 10, 0);
    final t1 = DateTime(2026, 8, 21, 10, 5);

    await mgr.enqueue(_scan('q2', 'S002', queuedAt: t1));
    await mgr.enqueue(_scan('q1', 'S001', queuedAt: t0));

    final all = await mgr.pending();
    expect(all.length, 2);
    expect(all[0].queueId, 'q1'); // earlier queuedAt sorts first
    expect(all[1].queueId, 'q2');

    await mgr.dispose();
  });

  test('markSynced removes the entry from pending', () async {
    final mgr = makeManager();
    await mgr.enqueue(_scan('q1', 'S001'));
    expect((await mgr.pending()).length, 1);

    await mgr.markSynced('q1');

    expect(await mgr.pending(), isEmpty);
    await mgr.dispose();
  });

  test('findByStudentId finds a queued (not yet synced) duplicate', () async {
    final mgr = makeManager();
    await mgr.enqueue(_scan('q1', 'S001'));

    final found = await mgr.findByStudentId('S001');
    expect(found, isNotNull);
    expect(found!.queueId, 'q1');

    final notFound = await mgr.findByStudentId('S999');
    expect(notFound, isNull);

    // Once synced, it's no longer a "local duplicate" — a fresh scan of
    // the same ID should hit the server's own duplicate check instead.
    await mgr.markSynced('q1');
    expect(await mgr.findByStudentId('S001'), isNull);

    await mgr.dispose();
  });

  test('discard removes multiple entries at once', () async {
    final mgr = makeManager();
    await mgr.enqueue(_scan('q1', 'S001'));
    await mgr.enqueue(_scan('q2', 'S002'));
    await mgr.enqueue(_scan('q3', 'S003'));

    await mgr.discard(['q1', 'q3']);

    final remaining = await mgr.pending();
    expect(remaining.length, 1);
    expect(remaining.first.queueId, 'q2');

    await mgr.dispose();
  });

  test('markAttemptFailed bumps attemptCount and sets state to failed, '
      'but the entry stays in pending() for retry', () async {
    final mgr = makeManager();
    await mgr.enqueue(_scan('q1', 'S001'));

    await mgr.markAttemptFailed('q1');
    await mgr.markAttemptFailed('q1');

    final all = await mgr.pending();
    expect(all.length, 1); // still queued — failed is not discarded
    expect(all.first.syncState, QueueSyncState.failed);
    expect(all.first.attemptCount, 2);

    await mgr.dispose();
  });

  test('queue survives a simulated app restart (new manager instance, '
      'same backing db)', () async {
    final factory = databaseFactoryFfi;
    const dbPath = 'test_restart_queue.db';
    // Clean slate in case a previous run left this file.
    await factory.deleteDatabase(dbPath);

    Future<void> createSchema(db) => db.execute('''
          CREATE TABLE queued_scans (
            queue_id TEXT PRIMARY KEY,
            payload_json TEXT NOT NULL,
            raw_image_path TEXT,
            annotated_id_path TEXT,
            annotated_mcq_path TEXT,
            queued_at INTEGER NOT NULL,
            sync_state TEXT NOT NULL,
            attempt_count INTEGER NOT NULL DEFAULT 0
          )
        ''');

    final mgr1 = OfflineQueueManager(
      openOverride: () => factory.openDatabase(
        dbPath,
        options: OpenDatabaseOptions(version: 1, onCreate: (db, v) => createSchema(db)),
      ),
    );
    await mgr1.enqueue(_scan('q1', 'S001'));
    await mgr1.dispose(); // simulates the app process dying

    final mgr2 = OfflineQueueManager(
      openOverride: () => factory.openDatabase(
        dbPath,
        options: OpenDatabaseOptions(version: 1, onCreate: (db, v) => createSchema(db)),
      ),
    );
    final resumed = await mgr2.pending();
    expect(resumed.length, 1);
    expect(resumed.first.queueId, 'q1');
    expect(resumed.first.studentId, 'S001');

    await mgr2.dispose();
    await factory.deleteDatabase(dbPath);
  });

  test('watchPending emits current state to a new listener, then updates '
      'on mutation', () async {
    final mgr = makeManager();
    await mgr.enqueue(_scan('q1', 'S001'));

    final events = <int>[]; // track queue length over time
    final sub = mgr.watchPending().listen((list) => events.add(list.length));

    // Let the initial emission land.
    await Future.delayed(Duration.zero);
    await mgr.enqueue(_scan('q2', 'S002'));
    await Future.delayed(Duration.zero);
    await mgr.markSynced('q1');
    await Future.delayed(Duration.zero);

    expect(events, [1, 2, 1]);

    await sub.cancel();
    await mgr.dispose();
  });
}