import '../../data/connection/websocket_client.dart' show ConnectionStatus, ServerEvent;
import '../entities/exam_models.dart' show DuplicateAction;

/// Contract for talking to the grading server, whatever the transport
/// happens to be. Today the only implementation is [WebSocketClient],
/// but nothing above this layer should know that — it should be
/// possible to swap in a fake for tests, or a different transport
/// later, without touching a single controller or screen.
///
/// This intentionally mirrors WebSocketClient's current public API
/// exactly — step 1 is introducing the seam, not changing behavior.
abstract class ConnectionRepository {
  Stream<ConnectionStatus> get statusStream;
  Stream<ServerEvent> get eventStream;
  ConnectionStatus get status;

  String? get lastHost;
  String? get lastPassword;

  Future<void> connect({
    required String host,
    required String name,
    required String password,
  });

  Future<void> reconnect();

  void disconnect();

  /// Fire-and-forget submit — kept for parity with the current client;
  /// prefer [submitScoreAndAwait] for anything that needs a result.
  void submitScore(Map<String, dynamic> payload);

  /// Submits a score and awaits the server's `score_result`.
  /// Throws [StateError] if not connected, [TimeoutException] if the
  /// server doesn't respond within the transport's timeout window.
  Future<Map<String, dynamic>> submitScoreAndAwait(Map<String, dynamic> payload);

  /// Resolves a duplicate-student-id conflict and awaits the server's
  /// `resolve_duplicate_result`. Same failure modes as
  /// [submitScoreAndAwait].
  Future<Map<String, dynamic>> resolveDuplicateAndAwait({
    required String studentId,
    required DuplicateAction action,
    Map<String, dynamic>? newScorePayload,
  });

  void dispose();
}
