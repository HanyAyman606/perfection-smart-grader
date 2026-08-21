import 'dart:async';
import 'package:smart_grader/domain/entities/queue_models.dart';
import 'package:smart_grader/domain/repositories/offline_queue_repository.dart';

class FakeOfflineQueueRepository implements OfflineQueueRepository {
  final Map<String, QueuedScan> _byId = {};
  final _pendingController = StreamController<List<QueuedScan>>.broadcast();

  int enqueueCallCount = 0;
  int markSyncedCallCount = 0;

  List<QueuedScan> get entriesInInsertOrder => _byId.values.toList();

  @override
  Future<String> enqueue(QueuedScan scan) async {
    enqueueCallCount++;
    _byId[scan.queueId] = scan;
    return scan.queueId;
  }

  @override
  Future<List<QueuedScan>> pending() async {
    final list = _byId.values.toList()..sort((a, b) => a.queuedAt.compareTo(b.queuedAt));
    return list;
  }

  @override
  Future<QueuedScan?> findByStudentId(String studentId) async {
    for (final scan in _byId.values) {
      if (scan.studentId == studentId) return scan;
    }
    return null;
  }

  @override
  Future<void> markSyncing(String queueId) async {
    final s = _byId[queueId];
    if (s != null) _byId[queueId] = s.copyWith(syncState: QueueSyncState.syncing);
  }

  @override
  Future<void> markAttemptFailed(String queueId) async {
    final s = _byId[queueId];
    if (s != null) {
      _byId[queueId] = s.copyWith(
        syncState: QueueSyncState.failed,
        attemptCount: s.attemptCount + 1,
      );
    }
  }

  @override
  Future<void> markSynced(String queueId) async {
    markSyncedCallCount++;
    _byId.remove(queueId);
  }

  @override
  Future<void> discard(List<String> queueIds) async {
    for (final id in queueIds) {
      _byId.remove(id);
    }
  }

  @override
  Stream<List<QueuedScan>> watchPending() => _pendingController.stream;

  @override
  Future<void> dispose() async {
    await _pendingController.close();
  }
}