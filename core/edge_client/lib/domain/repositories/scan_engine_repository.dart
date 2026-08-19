import '../entities/exam_models.dart' show MasterPacket, GradeResult;
import '../../data/scanning/cv_engine_service.dart' show ProcessExamResult;

/// Contract for the single-call scan pipeline (panel extraction +
/// inference/scoring in one native call). Backed by [CvEngineService],
/// which calls `run_exam_pipeline` from `libexam_scanner_ffi.so` via
/// FFI. The pipeline shells out to 6 stage executables, writes output
/// to `outputDir`, and Dart reads back `stage6/summary.json`.
abstract class ScanEngineRepository {
  /// Runs the full 6-stage pipeline: locate and crop the ID/MCQ panels,
  /// run bubble inference, score answers, and return a merged quality +
  /// grading result.
  ///
  /// [rawImagePath]  — absolute path to the captured photo.
  /// [modelPath]     — absolute path to `shamel.onnx` (resolved by
  ///                   [ModelPathRepository]).
  /// [masterPacket]  — exam config pushed from the admin dashboard.
  /// [outputDir]     — writable scratch directory; the pipeline writes
  ///                   `<outputDir>/<imageStem>/stage1/` … `stage6/`
  ///                   (resolved by [PipelinePathService]).
  /// [binDir]        — directory containing the 6 stage executables and
  ///                   `shamel.onnx` on the target device (resolved by
  ///                   [PipelinePathService]).
  ///
  /// The caller checks [ProcessExamResult.mustRetake] to decide whether
  /// to show an error/retake screen or build a [GradeResult] and proceed.
  Future<ProcessExamResult> extractPanels({
    required String rawImagePath,
    required String modelPath,
    required MasterPacket masterPacket,
    required String outputDir,
    required String binDir,
  });

  /// Kept for interface compatibility. Not used — all work is done inside
  /// [extractPanels]. Use [CvEngineService.buildGradeResult] to convert a
  /// [ProcessExamResult] into a [GradeResult] for the grading review screen.
  Future<GradeResult> inferAndScore({
    required String idPanelPath,
    required String mcqPanelPath,
    required String modelPath,
    required MasterPacket masterPacket,
    required String selectedVersion,
    String? rawImagePath,
  });
}
