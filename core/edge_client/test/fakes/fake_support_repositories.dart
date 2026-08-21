import 'package:smart_grader/domain/repositories/credentials_repository.dart';
import 'package:smart_grader/domain/repositories/session_cache_repository.dart';
import 'package:smart_grader/domain/repositories/log_repository.dart';

class FakeCredentialsRepository implements CredentialsRepository {
  @override
  Future<void> saveCredentials({
    required String host,
    required String name,
    required String password,
  }) async {}

  @override
  Future<Map<String, String?>> loadCredentials() async => {};

  @override
  Future<void> clearCredentials() async {}
}

class FakeSessionCacheRepository implements SessionCacheRepository {
  int onSubmitAcknowledgedCallCount = 0;
  final List<String?> acknowledgedRawImagePaths = [];
  int discardFailedAttemptCallCount = 0;
  final List<List<String?>> discardFailedAttemptCalls = [];

  @override
  Future<void> startNewSession(String sessionId) async {}

  @override
  Future<void> discardFailedAttempt(List<String?> paths) async {
    discardFailedAttemptCallCount++;
    discardFailedAttemptCalls.add(paths);
  }

  @override
  Future<void> onSubmitAcknowledged({
    String? rawImagePath,
    String? idPanelPath,
    String? mcqPanelPath,
    String? annotatedIdPath,
    String? annotatedMcqPath,
  }) async {
    onSubmitAcknowledgedCallCount++;
    acknowledgedRawImagePaths.add(rawImagePath);
  }

  @override
  Future<void> clearAll() async {}
}

class FakeLogRepository implements LogRepository {
  final List<String> entries = [];

  @override
  void log(String tag, String message) => entries.add('$tag: $message');
}