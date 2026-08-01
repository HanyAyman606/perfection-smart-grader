import 'dart:async';
import 'dart:convert';
import 'package:web_socket_channel/web_socket_channel.dart';
import '../models/exam_models.dart';

enum ConnectionStatus { disconnected, connecting, authenticating, connected, error }

abstract class ServerEvent {}

class AuthSuccess extends ServerEvent {
  final MasterPacket masterPacket;
  AuthSuccess(this.masterPacket);
}

class AuthFailure extends ServerEvent {
  final String message;
  AuthFailure(this.message);
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

class WebSocketClient {
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

  Stream<ConnectionStatus> get statusStream => _statusController.stream;
  Stream<ServerEvent> get eventStream => _eventController.stream;

  ConnectionStatus _currentStatus = ConnectionStatus.disconnected;

  WebSocketClient() {
    _updateStatus(ConnectionStatus.disconnected);
  }

  void _updateStatus(ConnectionStatus status) {
    _currentStatus = status;
    _statusController.add(status);
  }

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

  Future<void> _doConnect() async {
    _cleanupConnections();
    _updateStatus(ConnectionStatus.connecting);

    try {
      final wsUrl = Uri.parse('ws://$_lastHost:$_lastPort');
      _channel = WebSocketChannel.connect(wsUrl);
      
      // Wait for connection to be ready and listen
      await _channel!.ready;

      _updateStatus(ConnectionStatus.authenticating);
      _subscription = _channel!.stream.listen(
        _onMessage,
        onDone: _onDisconnected,
        onError: (error) {
          _onDisconnected();
        },
      );

      // Send Auth Message immediately
      _channel!.sink.add(jsonEncode({
        "type": "auth",
        "name": _lastName,
        "password": _lastPassword,
      }));
    } catch (e) {
      _onDisconnected();
    }
  }

  void _onMessage(dynamic message) {
    try {
      final data = jsonDecode(message as String) as Map<String, dynamic>;
      final type = data['type'] as String?;

      if (type == 'pong') {
        // Just heartbeat reply, nothing to do
      } else if (type == 'auth_result') {
        final status = data['status'];
        if (status == 'success') {
          _reconnectAttempt = 0; // Reset backoff on successful auth
          _updateStatus(ConnectionStatus.connected);
          _startPingTimer();
          
          final packet = MasterPacket.fromJson(data['master_packet'] as Map<String, dynamic>);
          _eventController.add(AuthSuccess(packet));
        } else {
          // Explicit auth failure (wrong password), DO NOT auto-retry
          _updateStatus(ConnectionStatus.error);
          _intentionalDisconnect = true; 
          _cleanupConnections();
          _eventController.add(AuthFailure(data['message'] ?? 'Authentication failed'));
        }
      } else if (type == 'score_result') {
        final status = data['status'];
        final studentId = data['student_id'] as String;
        if (status == 'success') {
          _eventController.add(ScoreSaved(studentId));
        } else if (status == 'duplicate') {
          final comparison = DuplicateComparison.fromJson(data);
          _eventController.add(ScoreDuplicate(comparison));
        }
      } else if (type == 'resolve_duplicate_result') {
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
      if (_currentStatus == ConnectionStatus.connected) {
        _channel?.sink.add(jsonEncode({"type": "ping"}));
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
  }

  void submitScore(Map<String, dynamic> payload) {
    if (_currentStatus == ConnectionStatus.connected) {
      _channel?.sink.add(jsonEncode(payload));
    }
  }

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
      _channel?.sink.add(jsonEncode(payload));
    }
  }

  void disconnect() {
    _intentionalDisconnect = true;
    _cleanupConnections();
    _updateStatus(ConnectionStatus.disconnected);
  }

  void dispose() {
    disconnect();
    _statusController.close();
    _eventController.close();
  }
}
