import 'dart:convert';
import 'exam_models.dart' show GradeResult, DuplicateComparison;

/// Sync lifecycle state of a queued scan. Persisted as TEXT in sqflite —
/// see OfflineQueueManager's schema. `failed` is not a dead end: the sync
/// worker still retries `failed` items on the *next* reconnect, this is
/// purely informational for the retention/erase UI (5.8) to show "this one
/// has had trouble" rather than silently identical to `pending`.
enum QueueSyncState { pending, syncing, failed }

QueueSyncState _syncStateFromString(String s) => QueueSyncState.values.firstWhere(
      (v) => v.name == s,
      orElse: () => QueueSyncState.pending,
    );

/// A completed scan waiting to be sent to the server, persisted locally
/// because the phone was offline (or the connection dropped) at the
/// moment the proctor tried to submit it.
///
/// Deliberately keyed by [queueId] (a uuid generated at enqueue time), NOT
/// by [studentId] — a proctor can edit a manually-entered ID after a scan
/// is already queued (retake handling, correction), and queueId needs to
/// stay stable across that so the sync worker and the erase UI are always
/// talking about the same physical entry.
class QueuedScan {
  final String queueId;

  /// Exactly the same shape `GradeResult.toSubmitScorePayload()` produces
  /// — the sync worker replays this byte-for-byte through the normal
  /// `submitScoreAndAwait` path, so server-side handling (including
  /// duplicate detection) is identical to the online case.
  final Map<String, dynamic> payload;

  /// Image paths kept alive until this entry is marked synced — normal
  /// online submission purges the raw image immediately via
  /// SessionCacheRepository.onSubmitAcknowledged, but a queued scan can't
  /// go through that until the server has actually acknowledged it, or a
  /// later retry/dispute-review would have nothing to show.
  final String? rawImagePath;
  final String? annotatedIdImagePath;
  final String? annotatedMcqImagePath;

  final DateTime queuedAt;
  final QueueSyncState syncState;

  /// Count of failed sync attempts so far — used to decide when to flip
  /// `syncState` to `failed` (purely informational, still retried).
  final int attemptCount;

  const QueuedScan({
    required this.queueId,
    required this.payload,
    required this.queuedAt,
    this.rawImagePath,
    this.annotatedIdImagePath,
    this.annotatedMcqImagePath,
    this.syncState = QueueSyncState.pending,
    this.attemptCount = 0,
  });

  String? get studentId => payload['student_id'] as String?;

  QueuedScan copyWith({QueueSyncState? syncState, int? attemptCount}) {
    return QueuedScan(
      queueId: queueId,
      payload: payload,
      queuedAt: queuedAt,
      rawImagePath: rawImagePath,
      annotatedIdImagePath: annotatedIdImagePath,
      annotatedMcqImagePath: annotatedMcqImagePath,
      syncState: syncState ?? this.syncState,
      attemptCount: attemptCount ?? this.attemptCount,
    );
  }

  /// Builds the local-duplicate-check comparison shown via the same
  /// SubmitDuplicate UI path the online case uses — "previous" here is
  /// what's already sitting in the local queue, not (necessarily) what's
  /// on the server yet.
  DuplicateComparison asDuplicateComparisonAgainst(Map<String, dynamic> incomingPayload) {
    return DuplicateComparison(
      studentId: studentId ?? '',
      previousScore: (payload['total_score'] as num?)?.toDouble() ?? 0.0,
      incomingScore: (incomingPayload['total_score'] as num?)?.toDouble() ?? 0.0,
      previousVersion: payload['answer_version'] as String?,
      incomingVersion: incomingPayload['answer_version'] as String?,
      previousTimestamp: payload['timestamp'] as String? ?? '',
      incomingTimestamp: incomingPayload['timestamp'] as String? ?? '',
    );
  }

  factory QueuedScan.fromGradeResult(
    GradeResult scan,
    String timestamp, {
    required String queueId,
    DateTime? queuedAt,
  }) {
    return QueuedScan(
      queueId: queueId,
      payload: scan.toSubmitScorePayload(timestamp),
      queuedAt: queuedAt ?? DateTime.now(),
      rawImagePath: scan.rawImagePath,
      annotatedIdImagePath: scan.annotatedIdImagePath,
      annotatedMcqImagePath: scan.annotatedMcqImagePath,
    );
  }

  /// Row shape for OfflineQueueManager's sqflite table.
  Map<String, Object?> toDbRow() {
    return {
      'queue_id': queueId,
      'payload_json': jsonEncode(payload),
      'raw_image_path': rawImagePath,
      'annotated_id_path': annotatedIdImagePath,
      'annotated_mcq_path': annotatedMcqImagePath,
      'queued_at': queuedAt.millisecondsSinceEpoch,
      'sync_state': syncState.name,
      'attempt_count': attemptCount,
    };
  }

  factory QueuedScan.fromDbRow(Map<String, Object?> row) {
    return QueuedScan(
      queueId: row['queue_id'] as String,
      payload: jsonDecode(row['payload_json'] as String) as Map<String, dynamic>,
      rawImagePath: row['raw_image_path'] as String?,
      annotatedIdImagePath: row['annotated_id_path'] as String?,
      annotatedMcqImagePath: row['annotated_mcq_path'] as String?,
      queuedAt: DateTime.fromMillisecondsSinceEpoch(row['queued_at'] as int),
      syncState: _syncStateFromString(row['sync_state'] as String),
      attemptCount: row['attempt_count'] as int? ?? 0,
    );
  }
}