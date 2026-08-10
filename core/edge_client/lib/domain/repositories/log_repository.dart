/// Contract for lightweight structured logging used across the data
/// layer (WebSocket traffic, CV engine calls) for on-device diagnostics.
/// Abstracted so those classes depend on a logging capability, not a
/// concrete singleton — swappable/mockable in tests.
///
/// Mirrors DebugLog's current public API exactly — no behavior change
/// in this step.
abstract class LogRepository {
  void log(String tag, String message);
}
