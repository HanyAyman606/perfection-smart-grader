import '../entities/exam_models.dart' show MasterPacket, GradeResult;
import '../../data/scanning/cv_engine_service.dart' show ProcessExamResult;

/// Contract for the single-call scan pipeline (panel extraction +
/// inference/scoring in one native call). Today backed by [CvEngineService],
/// which talks to the native cv_engine library over FFI via
/// process_exam_in_memory — but nothing above this layer should need to
/// know FFI is involved, only that it can hand over a raw image and get
/// a result back.
abstract class ScanEngineRepository {
  /// Single pipeline call: locate and crop the ID/MCQ panels, run OCR/bubble
  /// inference, and return both the scan quality signal and grading payload.
  /// The caller checks [ProcessExamResult.mustRetake] to decide whether to
  /// show an error screen or build a [GradeResult] and proceed to review.
  Future<ProcessExamResult> extractPanels({
    required String rawImagePath,
    required String modelPath,
    required MasterPacket masterPacket,
  });

  /// Kept for interface compatibility. The actual work is done inside
  /// [extractPanels] via process_exam_in_memory. Use
  /// [CvEngineService.buildGradeResult] to convert a [ProcessExamResult]
  /// into a [GradeResult] for the grading review screen.
  Future<GradeResult> inferAndScore({
    required String idPanelPath,
    required String mcqPanelPath,
    required String modelPath,
    required MasterPacket masterPacket,
    required String selectedVersion,
    String? rawImagePath,
  });
}
