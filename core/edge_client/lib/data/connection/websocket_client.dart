import 'dart:async';
import 'dart:convert';
import 'package:web_socket_channel/web_socket_channel.dart';
import '../../domain/entities/exam_models.dart';
import '../../domain/repositories/connection_repository.dart';
import '../../domain/repositories/log_repository.dart';

enum ConnectionStatus { disconnected, connecting, authenticating, connected, error }

abstract class ServerEvent {}

class AuthSuccess extends ServerEvent {
  final MasterPacket masterPacket;
  AuthSuccess(this.masterPacket);
}

class AuthFailure extends ServerEvent {
  final String message;
  // True when the server rejected auth for a reason that WebSocketClient
  // will automatically retry (currently: "name_taken" — see the
  // isRetryable branch in _onMessage). Lets listeners like
  // ConnectionController avoid showing a stuck error phase for a failure
  // that's already being retried in the background.
  final bool isRetryable;
  AuthFailure(this.message, {this.isRetryable = false});
}

class ScoreSaved extends ServerEvent {
  final String studentId;
  ScoreSaved(this.studentId);
}

class ScoreDuplicate extends ServerEvent {
  final DuplicateComparison comparison;
  ScoreDuplicate(this.comparison);
}

class DuplicateResolved extends ServerEvent {
  final String studentId;
  final String actionTaken;
  DuplicateResolved(this.studentId, this.actionTaken);
}

class ConnectionLost extends ServerEvent {
  final String reason;
  ConnectionLost(this.reason);
}

class SessionEnded extends ServerEvent {}

class WebSocketClient implements ConnectionRepository {
  WebSocketClient(this._log, {WebSocketChannel Function(Uri uri)? channelFactory})
      : _channelFactory = channelFactory ?? WebSocketChannel.connect {
    _updateStatus(ConnectionStatus.disconnected);
  }

  final LogRepository _log;

  // Seam for tests: production code always uses the default
  // (WebSocketChannel.connect), tests inject a fake channel factory so
  // _doConnect can be exercised without opening a real socket. See
  // test/websocket_client_test.dart.
  final WebSocketChannel Function(Uri uri) _channelFactory;

  WebSocketChannel? _channel;
  StreamSubscription? _subscription;
  Timer? _pingTimer;
  Timer? _reconnectTimer;

  int _reconnectAttempt = 0;
  bool _intentionalDisconnect = false;

  String? _lastHost;
  int? _lastPort;
  String? _lastName;
  String? _lastPassword;

  final _statusController = StreamController<ConnectionStatus>.broadcast();
  final _eventController = StreamController<ServerEvent>.broadcast();

  @override
  String? get lastHost => _lastHost;
  @override
  String? get lastPassword => _lastPassword;
  String? get lastName => _lastName;
  @override
  Stream<ConnectionStatus> get statusStream => _statusController.stream;
  @override
  Stream<ServerEvent> get eventStream => _eventController.stream;

  ConnectionStatus _currentStatus = ConnectionStatus.disconnected;
  @override
  ConnectionStatus get status => _currentStatus;

  /// Monotonic request-ID generator — uniqueness only needed within one
  /// phone's connection lifetime, not globally, so no uuid package needed.
  int _requestCounter = 0;
  String _nextRequestId() => '${DateTime.now().microsecondsSinceEpoch}_${_requestCounter++}';

  /// Pending request/response correlations. Keyed by request_id,
  /// completed when a score_result or resolve_duplicate_result with
  /// matching request_id arrives.
  final Map<String, Completer<Map<String, dynamic>>> _pendingRequests = {};

  void _updateStatus(ConnectionStatus status) {
    _currentStatus = status;
    _log.log('WebSocket', 'Status updated to $status');
    _statusController.add(status);
  }

  @override
  Future<void> connect({
    required String host,
    required String name,
    required String password,
    int port = 8765,
  }) async {
    _intentionalDisconnect = false;
    _lastHost = host;
    _lastPort = port;
    _lastName = name;
    _lastPassword = password;

    _reconnectAttempt = 0;
    await _doConnect();
  }

  @override
  Future<void> reconnect() async {
    if (_lastHost != null && _lastName != null && _lastPassword != null) {
      _intentionalDisconnect = false;
      _reconnectAttempt = 0;
      await _doConnect();
    }
  }

  Future<void> _doConnect() async {
    _cleanupConnections();
    _updateStatus(ConnectionStatus.connecting);
    try {
      final wsUrl = Uri.parse('ws://$_lastHost:$_lastPort');
      _channel = _channelFactory(wsUrl);

      // A wrong/unreachable IP on a LAN typically gets no response at all
      // (dropped SYN) — without an explicit timeout this can hang for the
      // OS's default TCP connect timeout instead of failing fast with
      // something the proctor can actually see and act on.
      await _channel!.ready.timeout(const Duration(seconds: 2));
      _updateStatus(ConnectionStatus.authenticating);
      _subscription = _channel!.stream.listen(
        _onMessage,
        onDone: _onDisconnected,
        onError: (error) {
          _onDisconnected();
        },
      );
      // Send Auth Message immediately
      final authMsg = jsonEncode({
        "type": "auth",
        "name": _lastName,
        "password": _lastPassword,
      });
      _log.log('WebSocket', 'SEND: $authMsg');
      _channel!.sink.add(authMsg);
    } on TimeoutException {
      // Unreachable host — fail fast and visibly instead of silently
      // retrying forever in the background with no feedback.
      _intentionalDisconnect = true;
      _cleanupConnections();
      _updateStatus(ConnectionStatus.error);
      _eventController.add(AuthFailure(
        "Couldn't reach $_lastHost — check the IP address and that both devices are on the same network.",
      ));
    } catch (e) {
      _onDisconnected();
    }
  }

  void _onMessage(dynamic message) {
    _log.log('WebSocket', 'RECV: $message');
    try {
      final data = jsonDecode(message as String) as Map<String, dynamic>;
      final type = data['type'] as String?;

      if (type == 'pong') {
        // Just heartbeat reply, nothing to do
      } else if (type == 'session_ending') {
        _eventController.add(SessionEnded());
      } else if (type == 'auth_result') {
        final status = data['status'] as String?;
        if (status == 'success') {
          _reconnectAttempt = 0; // Reset backoff on successful auth
          _updateStatus(ConnectionStatus.connected);
          _startPingTimer();

          final packet = MasterPacket.fromJson(data['master_packet'] as Map<String, dynamic>);
          _eventController.add(AuthSuccess(packet));
        } else {
          // The server rejects auth for exactly two reasons (see
          // websocket_server.py::_authenticate): bad credentials, or
          // "name_taken" — it still believes an old connection under this
          // name is alive. Only the credentials case is truly fatal; a
          // name_taken rejection is often just a timing race (the old
          // socket is actually dead but hasn't been reaped yet) that
          // resolves itself on the next attempt, so it should retry with
          // the normal backoff instead of stranding the proctor on a
          // manual-login screen. Treat a missing/unrecognized `reason`
          // (e.g. an older server build) as fatal too — that's the safer
          // default over silently retrying forever against an unknown
          // problem.
          final reason = data['reason'] as String?;
          final isRetryable = reason == 'name_taken';
          _eventController.add(AuthFailure(
            data['message'] ?? 'Authentication failed',
            isRetryable: isRetryable,
          ));
          if (isRetryable) {
            _cleanupConnections();
            _scheduleReconnect();
          } else {
            _updateStatus(ConnectionStatus.error);
            _intentionalDisconnect = true;
            _cleanupConnections();
          }
        }
      } else if (type == 'score_result') {
        final requestId = data['request_id'] as String?;
        if (requestId != null && _pendingRequests.containsKey(requestId)) {
          _pendingRequests.remove(requestId)!.complete(data);
        }

        final status = data['status'];
        final studentId = data['student_id'] as String;
        if (status == 'success') {
          _eventController.add(ScoreSaved(studentId));
        } else if (status == 'duplicate') {
          final comparison = DuplicateComparison.fromJson(data);
          _eventController.add(ScoreDuplicate(comparison));
        }
      } else if (type == 'resolve_duplicate_result') {
        final requestId = data['request_id'] as String?;
        if (requestId != null && _pendingRequests.containsKey(requestId)) {
          _pendingRequests.remove(requestId)!.complete(data);
        }

        if (data['status'] == 'success') {
          _eventController.add(DuplicateResolved(
            data['student_id'] as String,
            data['action_taken'] as String,
          ));
        }
      }
    } catch (e) {
      // Ignore malformed messages to be forward compatible
    }
  }

  void _onDisconnected() {
    _cleanupConnections();

    if (_intentionalDisconnect) {
      _updateStatus(ConnectionStatus.disconnected);
      return;
    }

    if (_currentStatus != ConnectionStatus.connecting && _currentStatus != ConnectionStatus.authenticating) {
      _eventController.add(ConnectionLost("Connection unexpectedly closed"));
    }

    _scheduleReconnect();
  }

  void _scheduleReconnect() {
    _updateStatus(ConnectionStatus.connecting);

    // Backoff: 3, 6, 12, 24, 30... (min(3 * 2^attempt, 30))
    int delaySecs = 3 * (1 << _reconnectAttempt);
    if (delaySecs > 30) delaySecs = 30;

    _reconnectAttempt++;

    _reconnectTimer = Timer(Duration(seconds: delaySecs), () {
      if (!_intentionalDisconnect) {
        _doConnect();
      }
    });
  }

  void _startPingTimer() {
    _pingTimer?.cancel();
    _pingTimer = Timer.periodic(const Duration(seconds: 10), (timer) {
      _log.log('WebSocketClient', 'Ping timer fired at ${DateTime.now()}');
      if (_currentStatus == ConnectionStatus.connected) {
        final pingMsg = jsonEncode({"type": "ping"});
        _log.log('WebSocket', 'SEND: $pingMsg');
        _channel?.sink.add(pingMsg);
      }
    });
  }

  void _cleanupConnections() {
    _pingTimer?.cancel();
    _reconnectTimer?.cancel();
    _subscription?.cancel();
    _channel?.sink.close();

    _pingTimer = null;
    _reconnectTimer = null;
    _subscription = null;
    _channel = null;

    // Fail any in-flight request/response completers
    for (final completer in _pendingRequests.values) {
      if (!completer.isCompleted) {
        completer.completeError(StateError('Connection closed'));
      }
    }
    _pendingRequests.clear();
  }

  @override
  void submitScore(Map<String, dynamic> payload) {
    if (_currentStatus == ConnectionStatus.connected) {
      final msg = jsonEncode(payload);
      _log.log('WebSocket', 'SEND: $msg');
      _channel?.sink.add(msg);
    }
  }

  /// Submit a score and await the server's response (score_result).
  /// Throws [TimeoutException] if no response within 10 seconds.
  /// Throws [StateError] if not connected.
  @override
  Future<Map<String, dynamic>> submitScoreAndAwait(Map<String, dynamic> payload) async {
    if (_currentStatus != ConnectionStatus.connected) {
      throw StateError('Not connected to server');
    }

    final requestId = _nextRequestId();
    payload['request_id'] = requestId;

    final completer = Completer<Map<String, dynamic>>();
    _pendingRequests[requestId] = completer;

    final msg = jsonEncode(payload);
    _log.log('WebSocket', 'SEND: $msg');
    _channel?.sink.add(msg);

    try {
      return await completer.future.timeout(
        const Duration(seconds: 10),
        onTimeout: () {
          _pendingRequests.remove(requestId);
          throw TimeoutException('Server did not respond within 10 seconds', const Duration(seconds: 10));
        },
      );
    } catch (e) {
      _pendingRequests.remove(requestId);
      rethrow;
    }
  }

  /// Fire-and-forget resolve for backward compat — NOT used by the
  /// provider anymore, but kept as a safety fallback.
  void resolveDuplicate({
    required String studentId,
    required DuplicateAction action,
    Map<String, dynamic>? newScorePayload,
  }) {
    if (_currentStatus == ConnectionStatus.connected) {
      final payload = <String, dynamic>{
        "type": "resolve_duplicate",
        "student_id": studentId,
        "action": action.wireValue,
      };
      if (action == DuplicateAction.overwrite && newScorePayload != null) {
        payload["new_score_payload"] = newScorePayload;
      }
      final msg = jsonEncode(payload);
      _log.log('WebSocket', 'SEND: $msg');
      _channel?.sink.add(msg);
    }
  }

  /// Resolve duplicate with ack — same request_id/Completer pattern as
  /// submitScoreAndAwait, listening for resolve_duplicate_result.
  @override
  Future<Map<String, dynamic>> resolveDuplicateAndAwait({
    required String studentId,
    required DuplicateAction action,
    Map<String, dynamic>? newScorePayload,
  }) async {
    if (_currentStatus != ConnectionStatus.connected) {
      throw StateError('Not connected to server');
    }

    final requestId = _nextRequestId();
    final payload = <String, dynamic>{
      "type": "resolve_duplicate",
      "student_id": studentId,
      "action": action.wireValue,
      "request_id": requestId,
    };
    if (action == DuplicateAction.overwrite && newScorePayload != null) {
      payload["new_score_payload"] = newScorePayload;
    }

    final completer = Completer<Map<String, dynamic>>();
    _pendingRequests[requestId] = completer;

    final msg = jsonEncode(payload);
    _log.log('WebSocket', 'SEND: $msg');
    _channel?.sink.add(msg);

    try {
      return await completer.future.timeout(
        const Duration(seconds: 10),
        onTimeout: () {
          _pendingRequests.remove(requestId);
          throw TimeoutException('Server did not respond within 10 seconds', const Duration(seconds: 10));
        },
      );
    } catch (e) {
      _pendingRequests.remove(requestId);
      rethrow;
    }
  }

  @override
  void disconnect() {
    _intentionalDisconnect = true;
    _cleanupConnections();
    _updateStatus(ConnectionStatus.disconnected);
  }

  @override
  void dispose() {
    disconnect();
    _statusController.close();
    _eventController.close();
  }
}