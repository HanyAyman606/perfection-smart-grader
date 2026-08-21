import 'dart:async';
import 'package:smart_grader/data/connection/websocket_client.dart'
    show ConnectionStatus, ServerEvent;
import 'package:smart_grader/domain/repositories/connection_repository.dart';
import 'package:smart_grader/domain/entities/exam_models.dart' show DuplicateAction;

/// Minimal fake of [ConnectionRepository] for testing [ConnectionController]
/// and, transitively, [LifecycleManager]'s Phase 3 grace-period timer —
/// without a real socket or real WebSocketClient backoff timers involved.
///
/// Exposes [disconnectCallCount] and [reconnectCallCount] so tests can
/// assert on exactly what LifecycleManager triggered, and [emitStatus]/
/// [emitEvent] so tests can drive ConnectionController into whatever
/// SessionPhase they need (in particular, `scanning`, which only happens
/// via a real AuthSuccess event per ConnectionController's own logic —
/// there's no shortcut setter for it, deliberately, so this fake has to
/// go through the same path production code does).
class FakeConnectionRepository implements ConnectionRepository {
  final _statusController = StreamController<ConnectionStatus>.broadcast();
  final _eventController = StreamController<ServerEvent>.broadcast();

  ConnectionStatus _status = ConnectionStatus.disconnected;

  int disconnectCallCount = 0;
  int reconnectCallCount = 0;
  int connectCallCount = 0;

  /// Records every payload passed to [submitScoreAndAwait], in call
  /// order — lets OfflineSyncWorker tests assert on drain order without
  /// needing a real server.
  final List<Map<String, dynamic>> submitScoreAndAwaitCalls = [];

  /// Overridable per-test response/behavior for [submitScoreAndAwait].
  /// Defaults to always returning `{'status': 'success'}` (a slightly
  /// more useful default than an empty map, which no real server
  /// response looks like); set this to control success/duplicate/error
  /// outcomes or to throw, per test.
  Future<Map<String, dynamic>> Function(Map<String, dynamic> payload)?
      submitScoreAndAwaitHandler;

  @override
  Stream<ConnectionStatus> get statusStream => _statusController.stream;

  @override
  Stream<ServerEvent> get eventStream => _eventController.stream;

  @override
  ConnectionStatus get status => _status;

  @override
  String? get lastHost => 'fake-host';

  @override
  String? get lastPassword => 'fake-password';

  void emitStatus(ConnectionStatus status) {
    _status = status;
    _statusController.add(status);
  }

  void emitEvent(ServerEvent event) => _eventController.add(event);

  @override
  Future<void> connect({
    required String host,
    required String name,
    required String password,
  }) async {
    connectCallCount++;
    _status = ConnectionStatus.connecting;
    _statusController.add(_status);
  }

  @override
  Future<void> reconnect() async {
    reconnectCallCount++;
  }

  @override
  void disconnect() {
    disconnectCallCount++;
    _status = ConnectionStatus.disconnected;
    _statusController.add(_status);
  }

  @override
  void submitScore(Map<String, dynamic> payload) {}

  @override
  Future<Map<String, dynamic>> submitScoreAndAwait(Map<String, dynamic> payload) async {
    submitScoreAndAwaitCalls.add(payload);
    if (submitScoreAndAwaitHandler != null) {
      return submitScoreAndAwaitHandler!(payload);
    }
    return {'status': 'success'};
  }

  int resolveDuplicateAndAwaitCallCount = 0;
  final List<Map<String, dynamic>> resolveDuplicateAndAwaitCalls = [];
  Future<Map<String, dynamic>> Function({
    required String studentId,
    required DuplicateAction action,
    Map<String, dynamic>? newScorePayload,
  })? resolveDuplicateAndAwaitHandler;

  @override
  Future<Map<String, dynamic>> resolveDuplicateAndAwait({
    required String studentId,
    required DuplicateAction action,
    Map<String, dynamic>? newScorePayload,
  }) async {
    resolveDuplicateAndAwaitCallCount++;
    resolveDuplicateAndAwaitCalls.add({
      'student_id': studentId,
      'action': action.name,
      'new_score_payload': newScorePayload,
    });
    if (resolveDuplicateAndAwaitHandler != null) {
      return resolveDuplicateAndAwaitHandler!(
        studentId: studentId,
        action: action,
        newScorePayload: newScorePayload,
      );
    }
    return {'status': 'success'};
  }

  @override
  void dispose() {
    _statusController.close();
    _eventController.close();
  }
}