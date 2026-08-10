/// Contract for on-device lifecycle of scan photo files — start of a
/// grading session, cleanup of rejected attempts, and post-submit
/// retention policy. Today backed by [SessionCacheManager]'s in-memory
/// + filesystem implementation; abstracted so controllers can be
/// tested without touching real disk I/O.
///
/// Mirrors SessionCacheManager's current public API exactly — no
/// behavior change in this step.
abstract class SessionCacheRepository {
  /// Call once per new session (successful auth / new connection).
  /// Purges anything left over from a previous session.
  Future<void> startNewSession(String sessionId);

  /// Deletes a failed/rejected scan attempt's files immediately.
  Future<void> discardFailedAttempt(List<String?> paths);

  /// Call once a scan's submission is acknowledged by the server.
  /// Deletes raw/cropped images, retains only the annotated pair
  /// (subject to the retention cap).
  Future<void> onSubmitAcknowledged({
    String? rawImagePath,
    String? idPanelPath,
    String? mcqPanelPath,
    String? annotatedIdPath,
    String? annotatedMcqPath,
  });

  /// Full manual wipe (settings/debug action).
  Future<void> clearAll();
}
