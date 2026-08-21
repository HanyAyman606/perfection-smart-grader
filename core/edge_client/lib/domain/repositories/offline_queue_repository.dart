import '../entities/queue_models.dart';

/// Contract for the local offline scan queue. Backed by
/// [OfflineQueueManager] (sqflite) in production; abstracted so
/// SubmissionController and the sync worker can be tested without a real
/// on-device database.
abstract class OfflineQueueRepository {
  /// Persists a completed scan's submission payload + its image paths for
  /// later sync. Assigns and returns the new entry's queueId.
  Future<String> enqueue(QueuedScan scan);

  /// All scans currently queued, oldest first (sync order == scan order).
  Future<List<QueuedScan>> pending();

  /// Local-duplicate check: is there already a queued (not yet synced)
  /// scan for this student ID? Returns it if so, else null. Callers pass
  /// this into `QueuedScan.asDuplicateComparisonAgainst` to build the UI
  /// comparison.
  Future<QueuedScan?> findByStudentId(String studentId);

  /// Marks an attempt in progress (sync worker sets this before calling
  /// the network, so a killed app mid-sync can be told apart from one
  /// that never tried).
  Future<void> markSyncing(String queueId);

  /// Records a failed sync attempt — bumps attemptCount, sets state back
  /// to `failed` (still retried on the next reconnect, this is
  /// informational only — see QueueSyncState doc comment).
  Future<void> markAttemptFailed(String queueId);

  /// Called once the server has genuinely acknowledged this scan
  /// (`status: success` or `status: duplicate` — both mean "the server
  /// has this data"). Removes the entry from the queue. Does NOT delete
  /// the entry's image files — the caller (SubmissionController /
  /// OfflineSyncWorker) is responsible for calling
  /// SessionCacheRepository.onSubmitAcknowledged with the queued paths
  /// first, same as the online path.
  Future<void> markSynced(String queueId);

  /// Manual purge — the "erase old scans" feature. Deletes the queue
  /// entries themselves; callers are responsible for deleting the
  /// entries' backing image files first (OfflineQueueManager exposes the
  /// paths via `pending()`/`watchPending()` for that).
  Future<void> discard(List<String> queueIds);

  /// For the queue-status UI (5.8).
  Stream<List<QueuedScan>> watchPending();

  Future<void> dispose();
}