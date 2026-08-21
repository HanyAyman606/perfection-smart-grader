import 'dart:async';

import '../../domain/entities/exam_models.dart' show DuplicateAction, DuplicateComparison;
import '../../domain/repositories/connection_repository.dart';
import '../../domain/repositories/offline_queue_repository.dart';
import '../../domain/repositories/session_cache_repository.dart';
import '../../domain/repositories/log_repository.dart';
import '../../domain/entities/queue_models.dart';
import '../connection/websocket_client.dart' show ConnectionStatus;

/// Asks the person which [DuplicateAction] to take for a duplicate found
/// while background-syncing a queued scan. Implementations typically pop
/// the same [DuplicateResolutionDialog] the online submission flow uses
/// (via a global navigator key, since a background worker has no
/// [BuildContext] of its own) — see the wiring in main.dart.
///
/// Should throw (any exception) if a decision genuinely can't be
/// obtained right now (e.g. no navigator context available because
/// nothing is on screen) — [OfflineSyncWorker] treats that exactly like
/// "no resolver configured": the item stays queued and flagged, safe and
/// visible, never silently discarded.
typedef DuplicateResolver = Future<DuplicateAction> Function(DuplicateComparison comparison);

/// Drains the offline scan queue whenever the connection comes back.
///
/// Listens to [ConnectionRepository.statusStream] for a transition INTO
/// [ConnectionStatus.connected], then walks the queue oldest-first,
/// replaying each item through the exact same [ConnectionRepository
/// .submitScoreAndAwait] call path the online submission flow uses — so
/// server-side behavior (including duplicate detection) is identical
/// whether a scan was submitted live or synced later.
///
/// Runs strictly sequentially, never in parallel: the server's duplicate
/// check is per-submission and order matters for correctness (draining
/// out of order could make a genuinely later scan look like the
/// "existing" one when the server resolves a real duplicate).
///
/// If the connection drops again mid-drain, the worker stops cleanly
/// after the in-flight item finishes (it does not start a new item once
/// `connected` isn't showing) and simply resumes from the top of
/// `pending()` on the next `connected` transition — items already marked
/// `synced` are gone from the queue, so nothing already-sent gets
/// double-submitted.
class OfflineSyncWorker {
  final ConnectionRepository _connection;
  final OfflineQueueRepository _offlineQueue;
  final SessionCacheRepository _sessionCache;
  final LogRepository? _log;
  final DuplicateResolver? _resolveDuplicate;

  StreamSubscription<ConnectionStatus>? _statusSub;
  ConnectionStatus? _lastStatus;

  /// Guards against overlapping drains — e.g. a rapid
  /// disconnected→connected→disconnected→connected flutter shouldn't
  /// spin up a second concurrent drain loop while one is already
  /// running.
  bool _draining = false;

  /// Set when a disconnect is observed mid-drain — checked between
  /// items so the loop stops promptly instead of finishing the whole
  /// queue against a connection that's already gone.
  bool _stopRequested = false;

  OfflineSyncWorker(
    this._connection,
    this._offlineQueue,
    this._sessionCache, {
    LogRepository? log,
    DuplicateResolver? resolveDuplicate,
  })  : _log = log,
        _resolveDuplicate = resolveDuplicate;

  /// Starts listening for reconnects. Call once, e.g. right after the
  /// composition root wires up the repositories. Also drains immediately
  /// if the connection already happens to be `connected` at start time
  /// (covers the case where items were left over from a previous run
  /// and the app launches already online).
  void start() {
    _lastStatus = _connection.status;
    _statusSub = _connection.statusStream.listen(_onStatusChanged);
    if (_connection.status == ConnectionStatus.connected) {
      unawaited(_drainQueue());
    }
  }

  void _onStatusChanged(ConnectionStatus status) {
    final previous = _lastStatus;
    _lastStatus = status;

    if (status != ConnectionStatus.connected) {
      // Mid-drain disconnect: ask the loop to stop after its current
      // item instead of trying to keep pushing into a dead socket.
      _stopRequested = true;
      return;
    }

    if (previous == ConnectionStatus.connected) {
      // Already connected, nothing changed (duplicate event) — no new
      // drain needed.
      return;
    }

    unawaited(_drainQueue());
  }

  Future<void> _drainQueue() async {
    if (_draining) return;
    _draining = true;
    _stopRequested = false;

    try {
      // Snapshot the queue at the start of this drain pass and walk it
      // in order, rather than re-querying pending() and taking .first
      // each time — a failed item stays in the queue (by design, it's
      // still retried on the *next* reconnect), so re-fetching .first
      // every loop would just pick the same failed item forever and
      // never reach anything after it.
      final snapshot = await _offlineQueue.pending();

      for (final item in snapshot) {
        if (_stopRequested || _connection.status != ConnectionStatus.connected) {
          break;
        }
        await _syncOne(item);
      }
    } finally {
      _draining = false;
    }
  }

  Future<void> _syncOne(QueuedScan item) async {
    await _offlineQueue.markSyncing(item.queueId);

    Map<String, dynamic>? response;
    try {
      response = await _connection.submitScoreAndAwait(item.payload);
    } catch (e) {
      _log?.log('OfflineSyncWorker', 'sync failed for ${item.queueId}: $e');
      await _offlineQueue.markAttemptFailed(item.queueId);
      return;
    }

    final status = response['status'] as String?;
    if (status == 'success') {
      // Server now genuinely has this data — safe to purge the raw
      // image, same retention rule the online path follows.
      await _sessionCache.onSubmitAcknowledged(
        rawImagePath: item.rawImagePath,
        annotatedIdPath: item.annotatedIdImagePath,
        annotatedMcqPath: item.annotatedMcqImagePath,
      );
      await _offlineQueue.markSynced(item.queueId);
    } else if (status == 'duplicate') {
      // The server already has a score for this student ID from
      // somewhere else (another proctor, an earlier online submission,
      // etc). This is NOT the same as success — the server has
      // deliberately withheld this submission pending a resolution
      // decision (overwrite / keep previous / discard both), same as
      // the online path's SubmitDuplicate flow.
      await _handleDuplicate(item, response);
    } else {
      // Any other outcome (explicit error, unexpected payload shape)
      // — leave it queued, don't drop it. Retried on the next
      // `connected` transition, never silently lost.
      _log?.log('OfflineSyncWorker', 'server rejected ${item.queueId}: status=$status');
      await _offlineQueue.markAttemptFailed(item.queueId);
    }
  }

  Future<void> _handleDuplicate(QueuedScan item, Map<String, dynamic> response) async {
    if (_resolveDuplicate == null) {
      await _flagDuplicateNeedsReview(item, 'no duplicate resolver configured');
      return;
    }

    final DuplicateComparison comparison;
    try {
      comparison = DuplicateComparison.fromJson(response);
    } catch (e) {
      // Malformed payload — can't build the dialog's content. Same safe
      // fallback: stays queued and visible, not silently dropped.
      await _flagDuplicateNeedsReview(item, 'malformed duplicate payload: $e');
      return;
    }

    final DuplicateAction action;
    try {
      action = await _resolveDuplicate!(comparison);
    } catch (e) {
      // Couldn't get a decision right now (e.g. no navigator context —
      // nothing on screen to show the dialog against). Leave it queued;
      // the next reconnect (or the offline queue screen, once it grows
      // a manual "resolve now" affordance) gets another chance.
      await _flagDuplicateNeedsReview(item, 'could not obtain a resolution decision: $e');
      return;
    }

    Map<String, dynamic>? overwritePayload;
    if (action == DuplicateAction.overwrite) {
      overwritePayload = item.payload;
    }

    Map<String, dynamic> resolveResponse;
    try {
      resolveResponse = await _connection.resolveDuplicateAndAwait(
        studentId: item.studentId ?? '',
        action: action,
        newScorePayload: overwritePayload,
      );
    } catch (e) {
      _log?.log('OfflineSyncWorker', 'resolveDuplicateAndAwait failed for ${item.queueId}: $e');
      await _flagDuplicateNeedsReview(item, 'resolve call failed: $e');
      return;
    }

    if (resolveResponse['status'] != 'success') {
      _log?.log(
        'OfflineSyncWorker',
        'server rejected duplicate resolution for ${item.queueId}: $resolveResponse',
      );
      await _flagDuplicateNeedsReview(item, 'server rejected the resolution');
      return;
    }

    // Whichever action was chosen, the server now has a final answer for
    // this student ID — this queue item is done either way: overwrite
    // means the new data was saved (so purge, same as a plain success);
    // keepPrevious/discardBoth both mean this scan's data was
    // deliberately NOT kept, so there's equally nothing left to retry —
    // purge and remove from the queue rather than leaving a phantom
    // entry with no path to ever "finish" syncing.
    await _sessionCache.onSubmitAcknowledged(
      rawImagePath: item.rawImagePath,
      annotatedIdPath: item.annotatedIdImagePath,
      annotatedMcqPath: item.annotatedMcqImagePath,
    );
    await _offlineQueue.markSynced(item.queueId);
    _log?.log('OfflineSyncWorker', 'duplicate resolved for ${item.queueId}: ${action.name}');
  }

  Future<void> _flagDuplicateNeedsReview(QueuedScan item, String reason) async {
    // A background sync worker has no UI to show that dialog to, so it
    // must NOT silently discard the images or mark this synced — doing
    // so was the original bug: the scan would vanish with no record and
    // no way to tell the proctor their data may not have been saved.
    // Instead: leave it queued, keep its images intact, and flag it
    // distinctly so the offline queue screen can surface it for manual
    // resolution.
    _log?.log(
      'OfflineSyncWorker',
      'server reports duplicate for ${item.queueId} (student '
          '${item.studentId}) — $reason, not auto-resolved',
    );
    await _offlineQueue.markAttemptFailed(item.queueId);
  }

  void dispose() {
    _statusSub?.cancel();
  }
}