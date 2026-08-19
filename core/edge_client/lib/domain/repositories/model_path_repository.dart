/// Contract for resolving filesystem paths needed by the scan pipeline.
///
/// Backed by [PipelinePathService] which:
///   - Copies the bundled ONNX model to app-support storage on first use.
///   - Resolves the directory containing the 6 stage executables per platform.
///   - Provides a writable scratch output directory for pipeline results.
abstract class ModelPathRepository {
  /// Absolute path to the ONNX model file on disk (copied from assets on
  /// first use). Passed to `run_exam_pipeline` via `exam_config.json`.
  Future<String> resolveBubbleModelPath();

  /// Absolute path to the directory containing the stage executables
  /// (`stage1_boxes` … `stage6_scoring`) on the current device.
  /// Platform-specific — see [PipelinePathService] for per-platform logic.
  Future<String> resolveBinDir();

  /// Absolute path to a writable scratch directory. The pipeline writes
  /// `<outputDir>/<imageStem>/stage1/` … `stage6/summary.json` here.
  Future<String> resolveOutputDir();
}
