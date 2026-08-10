import '../entities/exam_models.dart' show MasterPacket, GradeResult;
import '../../data/scanning/cv_engine_service.dart' show PanelExtractionResult;

/// Contract for the two-step scan pipeline (panel extraction, then
/// inference/scoring). Today backed by [CvEngineService], which talks
/// to the native cv_engine library over FFI — but nothing above this
/// layer should need to know FFI is involved, only that it can hand
/// over an image and get a result back.
///
/// Mirrors CvEngineService's current public API exactly — no behavior
/// change in this step.
abstract class ScanEngineRepository {
  /// Step 1: locate and crop the ID/MCQ panels from a raw photo and
  /// report scan quality (blur, orientation confidence, etc).
  Future<PanelExtractionResult> extractPanels({
    required String rawImagePath,
    required String modelPath,
    required MasterPacket masterPacket,
  });

  /// Step 2: run OCR/bubble inference on the extracted panels and
  /// score against the selected answer version.
  Future<GradeResult> inferAndScore({
    required String idPanelPath,
    required String mcqPanelPath,
    required String modelPath,
    required MasterPacket masterPacket,
    required String selectedVersion,
    String? rawImagePath,
  });
}
