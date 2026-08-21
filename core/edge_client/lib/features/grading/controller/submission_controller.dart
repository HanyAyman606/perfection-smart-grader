import 'dart:io';

import 'package:uuid/uuid.dart';
import '../../../domain/entities/exam_models.dart';
import '../../../domain/repositories/connection_repository.dart';
import '../../../domain/repositories/session_cache_repository.dart';
import '../../../domain/repositories/offline_queue_repository.dart';
import '../../../domain/entities/queue_models.dart';
import '../../../data/connection/websocket_client.dart' show ConnectionStatus;
import '../../scanning/controller/scan_controller.dart';

/// Orchestrates submitting the current scan (and resolving duplicate-
/// student-id conflicts) to the server.
///
/// Deliberately NOT a ChangeNotifier — unlike ConnectionController and
/// ScanController, nothing here is reactive UI state a screen watches;
/// it's pure request/response orchestration that hands back a result
/// value (the existing SubmitResult sealed hierarchy from
/// exam_models.dart, unchanged). It reads the current scan from, and
/// tells, ScanController — that's an intentional direct dependency
/// (submission needs to know what's being submitted and needs to clear
/// it afterward), unlike Connection/ScanController which stay
/// decoupled from each other.
class SubmissionController {
  final ConnectionRepository _connection;
  final SessionCacheRepository _sessionCache;
  final ScanController _scanController;
  final OfflineQueueRepository _offlineQueue;
  final Uuid _uuid = const Uuid();

  /// Set when submitCurrentScan()'s local-duplicate check (against the
  /// offline queue) is what produced the pending SubmitDuplicate — as
  /// opposed to a server response while online. resolveDuplicate() reads
  /// this to decide whether to resolve locally (no network involved) or
  /// go through the existing network path. Cleared as soon as it's
  /// consumed. This is necessarily a bit of mutable state riding
  /// alongside the "current pending duplicate" the UI is showing —
  /// SubmissionController already relies on ScanController.currentScan
  /// playing the equivalent role for "what scan is this decision about."
  String? _pendingLocalDuplicateQueueId;

  SubmissionController(
    this._connection,
    this._sessionCache,
    this._scanController,
    this._offlineQueue,
  );

  Future<SubmitResult> commitCurrentScan() async {
    final timestamp = DateTime.now().toLocal().toString().split('.')[0];
    return submitCurrentScan(timestamp);
  }

  Future<SubmitResult> submitCurrentScan(String timestamp) async {
    final scan = _scanController.currentScan;
    if (scan == null || scan.studentId == null || scan.studentId!.isEmpty) {
      return SubmitFailed('Student ID is missing. Please enter it manually.');
    }

    // Phase 5: local-duplicate check runs regardless of connection state —
    // a scan queued earlier while offline is still a duplicate now, online
    // or not, and this catches it before a network round-trip is even
    // attempted.
    final localDup = await _offlineQueue.findByStudentId(scan.studentId!);
    if (localDup != null) {
      _pendingLocalDuplicateQueueId = localDup.queueId;
      final incomingPayload = scan.toSubmitScorePayload(timestamp);
      return SubmitDuplicate(localDup.asDuplicateComparisonAgainst(incomingPayload));
    }
    _pendingLocalDuplicateQueueId = null;

    if (_connection.status != ConnectionStatus.connected) {
      // Offline path: enqueue instead of attempting a network call that
      // would just sit until WebSocketClient's own timeout fires.
      final queueId = _uuid.v4();
      await _offlineQueue.enqueue(
        QueuedScan.fromGradeResult(scan, timestamp, queueId: queueId),
      );
      // Deliberately does NOT call _sessionCache.onSubmitAcknowledged here
      // — the raw image must survive on disk until OfflineSyncWorker (5.6)
      // actually confirms the server has this scan; purging now would
      // delete the only copy of the source image before it's ever sent.
      _scanController.clearCurrentScanSilently();
      return SubmitQueued(queueId);
    }

    final payload = scan.toSubmitScorePayload(timestamp);

    try {
      final response = await _connection.submitScoreAndAwait(payload);
      final status = response['status'] as String?;

      if (status == 'duplicate') {
        final comparison = DuplicateComparison.fromJson(response);
        _pendingLocalDuplicateQueueId = null; // this is a server-side duplicate, not local
        return SubmitDuplicate(comparison);
      }
      if (status != 'success') return SubmitFailed('Server rejected the submission.');
    } catch (e) {
      return SubmitFailed('Network timeout or connection error.');
    }

    // Only purge on genuinely final "success" outcome.
    await _sessionCache.onSubmitAcknowledged(
      rawImagePath: scan.rawImagePath,
      annotatedIdPath: scan.annotatedIdImagePath,
      annotatedMcqPath: scan.annotatedMcqImagePath,
    );
    _scanController.clearCurrentScanSilently();
    return SubmitSuccess();
  }

  Future<bool> resolveDuplicate(String studentId, DuplicateAction action, String timestamp) async {
    // The duplicate this is resolving might be against another item
    // already sitting in the offline queue (found by submitCurrentScan's
    // local-duplicate check) rather than against the server. That case
    // must be resolved entirely locally — there's no network call to
    // make, and trying one (the old behavior) always failed when
    // offline, which is exactly why this branch exists.
    final localQueueId = _pendingLocalDuplicateQueueId;
    if (localQueueId != null) {
      return _resolveLocalDuplicate(localQueueId, action, timestamp);
    }
    return _resolveServerDuplicate(studentId, action, timestamp);
  }

  Future<bool> _resolveLocalDuplicate(String oldQueueId, DuplicateAction action, String timestamp) async {
    _pendingLocalDuplicateQueueId = null;
    final scan = _scanController.currentScan;

    // Need the old entry's own image paths to delete/keep them
    // correctly — findByStudentId only returns null-or-match, doesn't
    // give us direct lookup by queueId, so pull it from pending().
    QueuedScan? oldEntry;
    for (final q in await _offlineQueue.pending()) {
      if (q.queueId == oldQueueId) {
        oldEntry = q;
        break;
      }
    }

    switch (action) {
      case DuplicateAction.keepPrevious:
        // Keep the already-queued entry untouched; the new scan just
        // captured isn't going anywhere, so its images are dead weight.
        if (scan != null) {
          await _sessionCache.discardFailedAttempt(
            [scan.rawImagePath, scan.annotatedIdImagePath, scan.annotatedMcqImagePath],
          );
        }
        await _scanController.clearCurrentScan();
        return true;

      case DuplicateAction.discardBoth:
        if (oldEntry != null) {
          await _deleteQueuedScanImages(oldEntry);
          await _offlineQueue.discard([oldQueueId]);
        }
        if (scan != null) {
          await _sessionCache.discardFailedAttempt(
            [scan.rawImagePath, scan.annotatedIdImagePath, scan.annotatedMcqImagePath],
          );
        }
        await _scanController.clearCurrentScan();
        return true;

      case DuplicateAction.overwrite:
        if (oldEntry != null) {
          await _deleteQueuedScanImages(oldEntry);
          await _offlineQueue.discard([oldQueueId]);
        }
        if (scan != null) {
          await _offlineQueue.enqueue(
            QueuedScan.fromGradeResult(scan, timestamp, queueId: _uuid.v4()),
          );
          _scanController.clearCurrentScanSilently();
        }
        return true;
    }
  }

  Future<void> _deleteQueuedScanImages(QueuedScan entry) async {
    for (final p in [entry.rawImagePath, entry.annotatedIdImagePath, entry.annotatedMcqImagePath]) {
      if (p == null) continue;
      try {
        final f = File(p);
        if (await f.exists()) await f.delete();
      } catch (_) {
        // Best-effort — same tolerance SessionCacheManager/OfflineQueueScreen
        // already apply to file deletion failures elsewhere.
      }
    }
  }

  Future<bool> _resolveServerDuplicate(String studentId, DuplicateAction action, String timestamp) async {
    final scan = _scanController.currentScan;
    Map<String, dynamic>? newPayload;
    if (action == DuplicateAction.overwrite && scan != null) {
      newPayload = scan.toSubmitScorePayload(timestamp);
    }

    try {
      final response = await _connection.resolveDuplicateAndAwait(
        studentId: studentId,
        action: action,
        newScorePayload: newPayload,
      );
      if (response['status'] != 'success') {
        return false;
      }
    } catch (e) {
      // Timeout or connection error — resolution not confirmed.
      return false;
    }

    // Resolution confirmed by server — safe to clean up.
    if (action != DuplicateAction.overwrite) {
      await _scanController.clearCurrentScan();
    } else if (scan != null) {
      // Overwrite succeeded — purge files same as a normal success.
      await _sessionCache.onSubmitAcknowledged(
        rawImagePath: scan.rawImagePath,
        annotatedIdPath: scan.annotatedIdImagePath,
        annotatedMcqPath: scan.annotatedMcqImagePath,
      );
      _scanController.clearCurrentScanSilently();
    }
    return true;
  }
}