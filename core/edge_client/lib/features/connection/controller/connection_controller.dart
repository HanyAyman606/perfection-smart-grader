import 'dart:async';
import 'package:flutter/foundation.dart';
import '../../../domain/entities/exam_models.dart';
import '../../../domain/repositories/connection_repository.dart';
import '../../../domain/repositories/credentials_repository.dart';
import '../../../domain/repositories/session_cache_repository.dart';
import '../../../data/connection/websocket_client.dart'
    show ConnectionStatus, ServerEvent, AuthSuccess, AuthFailure, ConnectionLost, SessionEnded;

enum SessionPhase { disconnected, connecting, scanning, error }

/// Owns connection/authentication lifecycle and the exam config
/// (MasterPacket) pushed down on successful auth.
///
/// Deliberately does NOT own in-progress scan state (see
/// [ScanController]) or submission orchestration (see
/// [SubmissionController]) — this is step 2's split of what used to be
/// the single ExamSessionProvider god-class. Depends only on domain
/// interfaces (step 1), not on WebSocketClient/CredentialsCache
/// concretes directly.
class ConnectionController extends ChangeNotifier {
  final ConnectionRepository _connection;
  final CredentialsRepository _credentials;
  final SessionCacheRepository _sessionCache;

  SessionPhase _phase = SessionPhase.disconnected;
  String? _errorMessage;
  MasterPacket? _masterPacket;
  String? _selectedVersion;
  String _proctorName = '';

  StreamSubscription? _statusSub;
  StreamSubscription? _eventSub;

  // Set only by [disconnectForBackground] (Phase 3's grace-period timer).
  // Lets _onConnectionStatusChanged tell "we intentionally dropped the
  // socket to free the server slot while backgrounded, but the session is
  // still logically alive" apart from a real disconnect/session-end, so it
  // can leave `_phase` at `scanning` instead of resetting it — that's what
  // lets `reconnectIfNecessary()` do a real reconnect on the next resume
  // instead of requiring the proctor to log back in.
  bool _backgroundSoftDisconnect = false;

  ConnectionController(this._connection, this._credentials, this._sessionCache) {
    _statusSub = _connection.statusStream.listen(_onConnectionStatusChanged);
    _eventSub = _connection.eventStream.listen(_onServerEvent);
  }

  SessionPhase get phase => _phase;
  String? get errorMessage => _errorMessage;
  MasterPacket? get masterPacket => _masterPacket;
  String? get selectedVersion => _selectedVersion;
  String get proctorName => _proctorName;

  void _onConnectionStatusChanged(ConnectionStatus status) {
    switch (status) {
      case ConnectionStatus.disconnected:
        if (_backgroundSoftDisconnect) {
          // Consume the flag; deliberately leave `_phase` alone (stays
          // `scanning`) so reconnectIfNecessary() treats this like "still
          // mid-session, just needs its socket back" on the next resume.
          _backgroundSoftDisconnect = false;
        } else {
          _phase = SessionPhase.disconnected;
        }
        break;
      case ConnectionStatus.connecting:
      case ConnectionStatus.authenticating:
        _phase = SessionPhase.connecting;
        break;
      case ConnectionStatus.connected:
        break; // handled by AuthSuccess
      case ConnectionStatus.error:
        _phase = SessionPhase.error;
        break;
    }
    notifyListeners();
  }

  void _onServerEvent(ServerEvent event) {
    if (event is AuthSuccess) {
      _masterPacket = event.masterPacket;
      _errorMessage = null;
      if (_masterPacket!.answerVersions.isNotEmpty) {
        _selectedVersion = _masterPacket!.answerVersions.first;
      }
      _phase = SessionPhase.scanning;

      // New session boundary — purge any leftover scan files from a
      // previous exam/connection. Keyed on exam name + group; see
      // original ExamSessionProvider for the same rationale.
      final sessionKey = '${_masterPacket!.examName}_${_masterPacket!.groupName}';
      _sessionCache.startNewSession(sessionKey);

      _credentials.saveCredentials(
        host: _connection.lastHost ?? '',
        name: _proctorName,
        password: _connection.lastPassword ?? '',
      );
    } else if (event is AuthFailure) {
      _errorMessage = event.message;
      // Only force the error phase for a genuinely fatal failure. A
      // retryable one (see AuthFailure.isRetryable) is already being
      // auto-retried by WebSocketClient's backoff — the status stream will
      // move _phase to connecting on its own, so overwriting it with
      // SessionPhase.error here would show a stuck error screen for a
      // problem that's actively resolving itself in the background.
      if (!event.isRetryable) {
        _phase = SessionPhase.error;
      }
    } else if (event is ConnectionLost) {
      _errorMessage = event.reason;
    } else if (event is SessionEnded) {
      _errorMessage = 'This grading session was closed by the admin.';
      _phase = SessionPhase.disconnected;
      _masterPacket = null;
      // Mark this as an intentional disconnect so the socket's imminent
      // close (the server closes right after sending session_ending)
      // doesn't trigger the automatic reconnect/backoff loop — the
      // admin closed the server on purpose, we shouldn't fight that.
      _connection.disconnect();
      // NOTE: intentionally does not touch in-progress scan state here.
      // ScanController listens to this same event stream independently
      // and clears its own state — see scan_controller.dart. Keeping
      // that reaction there (not here) avoids this controller needing
      // to know ScanController exists at all.
    }
    notifyListeners();
  }

  Future<void> connect({required String host, required String name, required String password}) async {
    _proctorName = name;
    _errorMessage = null;
    await _connection.connect(host: host, name: name, password: password);
  }

  void disconnect() => _connection.disconnect();

  /// Used by LifecycleManager's Phase-3 background grace-period timer.
  /// Intentionally tears down the socket (frees the server's slot, stops
  /// any reconnect backoff loop) exactly like [disconnect] — but keeps
  /// [phase] at `scanning` and keeps [masterPacket], so the next
  /// `reconnectIfNecessary()` call (on app resume) performs a real
  /// reconnect instead of dropping the proctor back to the login screen.
  void disconnectForBackground() {
    _backgroundSoftDisconnect = true;
    _connection.disconnect();
  }

  /// Disconnects and forgets saved credentials. Does NOT clear
  /// in-progress scan state/files — callers that need the old
  /// "endSession" behavior should also call
  /// ScanController.clearCurrentScan(). See scanner_view.dart.
  Future<void> disconnectAndForgetCredentials() async {
    await _credentials.clearCredentials();
    disconnect();
  }

  bool reconnectIfNecessary() {
    if (_phase == SessionPhase.scanning &&
        _connection.status != ConnectionStatus.connected &&
        _proctorName.isNotEmpty) {
      _connection.reconnect();
      return true;
    }
    return false;
  }

  void selectVersion(String version) {
    _selectedVersion = version;
    notifyListeners();
  }

  @override
  void dispose() {
    _statusSub?.cancel();
    _eventSub?.cancel();
    super.dispose();
  }
}