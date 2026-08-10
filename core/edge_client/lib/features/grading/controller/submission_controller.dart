import '../../../domain/entities/exam_models.dart';
import '../../../domain/repositories/connection_repository.dart';
import '../../../domain/repositories/session_cache_repository.dart';
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

  SubmissionController(this._connection, this._sessionCache, this._scanController);

  Future<SubmitResult> commitCurrentScan() async {
    final timestamp = DateTime.now().toLocal().toString().split('.')[0];
    return submitCurrentScan(timestamp);
  }

  Future<SubmitResult> submitCurrentScan(String timestamp) async {
    final scan = _scanController.currentScan;
    if (scan == null || scan.studentId == null || scan.studentId!.isEmpty) {
      return SubmitFailed('Student ID is missing. Please enter it manually.');
    }

    final payload = scan.toSubmitScorePayload(timestamp);

    try {
      final response = await _connection.submitScoreAndAwait(payload);
      final status = response['status'] as String?;

      if (status == 'duplicate') {
        final comparison = DuplicateComparison.fromJson(response);
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
