import 'dart:async';
import 'package:flutter/foundation.dart';
import '../../../domain/entities/exam_models.dart';
import '../../../domain/repositories/connection_repository.dart';
import '../../../domain/repositories/session_cache_repository.dart';
import '../../../data/session/session_cache_manager.dart' show RetryCounter;
import '../../../data/connection/websocket_client.dart' show ServerEvent, SessionEnded;

/// Owns the in-progress scan (GradeResult) currently under review, and
/// the retry counter that drives the manual-entry fallback after 3+
/// bad retakes.
///
/// Listens to the connection's event stream independently (not
/// through ConnectionController) so an ended session clears any
/// in-progress scan even without a screen around to orchestrate it —
/// this keeps ConnectionController and ScanController decoupled from
/// each other; both just react to the same domain event stream.
///
/// TODO(step 3): RetryCounter currently lives in session_cache_manager.dart,
/// which is a `data/` concern — it's really a scanning-domain concept
/// and should move to features/scanning when files get relocated.
class ScanController extends ChangeNotifier {
  final SessionCacheRepository _sessionCache;
  final RetryCounter retryCounter = RetryCounter();

  GradeResult? _currentScan;
  StreamSubscription? _eventSub;

  ScanController(this._sessionCache, ConnectionRepository connection) {
    _eventSub = connection.eventStream.listen((ServerEvent event) {
      if (event is SessionEnded) clearCurrentScan();
    });
  }

  GradeResult? get currentScan => _currentScan;

  void setCurrentScan(GradeResult result) {
    _currentScan = result;
    retryCounter.reset();
    notifyListeners();
  }

  /// Discards the raw/cropped images for a rejected or abandoned scan
  /// attempt (retake, skip, or leaving the review screen without
  /// submitting) and clears in-memory state.
  Future<void> clearCurrentScan() async {
    if (_currentScan != null) {
      await _sessionCache.discardFailedAttempt([_currentScan!.rawImagePath]);
    }
    _currentScan = null;
    notifyListeners();
  }

  /// Clears in-memory scan state WITHOUT touching files. Used after a
  /// successful submission, where SubmissionController has already
  /// handled file retention/purging via SessionCacheRepository's
  /// onSubmitAcknowledged — calling clearCurrentScan() here would be
  /// wrong, it would delete the raw image a second time and skip the
  /// annotated-pair retention.
  void clearCurrentScanSilently() {
    _currentScan = null;
    notifyListeners();
  }

  void updateCurrentScan({required String studentId, required double essayTotal, String? groupType}) {
    if (_currentScan != null) {
      _currentScan!.studentId = studentId;
      _currentScan!.essayTotal = essayTotal;
      _currentScan!.groupType = groupType;
      notifyListeners();
    }
  }

  @override
  void dispose() {
    _eventSub?.cancel();
    super.dispose();
  }
}
