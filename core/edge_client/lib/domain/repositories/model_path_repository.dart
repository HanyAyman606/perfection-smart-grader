/// Contract for resolving the filesystem path to the bundled ONNX
/// bubble-detection model. Today backed by [ModelPathService], which
/// copies the Flutter asset out to app-support storage once and caches
/// the result; abstracted so callers don't need to know that detail.
///
/// Mirrors ModelPathService's current public API exactly — no behavior
/// change in this step.
abstract class ModelPathRepository {
  Future<String> resolveBubbleModelPath();
}
