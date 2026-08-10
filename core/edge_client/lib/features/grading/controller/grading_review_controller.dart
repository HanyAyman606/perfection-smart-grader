import 'package:flutter/widgets.dart';
import '../../../domain/entities/exam_models.dart';
import '../../connection/controller/connection_controller.dart';
import '../../scanning/controller/scan_controller.dart';
import 'submission_controller.dart';

/// Owns the Grading Review screen's local UI state — the text field
/// controllers, receipt/timestamp state — and orchestrates the
/// retake/generate-receipt/skip/submit flows against the domain
/// controllers (ScanController, SubmissionController).
///
/// Deliberately stays free of BuildContext: dialogs, snackbars, and
/// navigation are the screen's job, since only the widget owns a
/// BuildContext safely. This controller exposes plain results
/// (SubmitResult, bool, String? validation errors) for the widget to
/// interpret.
class GradingReviewController extends ChangeNotifier {
  GradingReviewController({
    required ScanController scanController,
    required SubmissionController submissionController,
    required ConnectionController connectionController,
    required this.manualEntry,
  })  : _scanController = scanController,
        _submissionController = submissionController,
        _connectionController = connectionController {
    _initFields();
  }

  final ScanController _scanController;
  final SubmissionController _submissionController;
  final ConnectionController _connectionController;

  /// True when reached via the 3+ retry manual-entry fallback rather than
  /// a successful Step 2 result — no scan data to prefill, no annotated
  /// image buttons.
  final bool manualEntry;

  late final TextEditingController idController;
  late final TextEditingController essayController;
  late final TextEditingController groupTypeController;
  late final TextEditingController mcqScoreController; // manualEntry only

  String _timestamp = '';
  String get timestamp => _timestamp;

  bool _isReceiptGenerated = false;
  bool get isReceiptGenerated => _isReceiptGenerated;

  GradeResult? get scan => _scanController.currentScan;

  double get total => manualEntry
      ? (double.tryParse(mcqScoreController.text) ?? 0.0) + (scan?.essayTotal ?? 0.0)
      : (scan?.mcqScore ?? 0.0) + (scan?.essayTotal ?? 0.0);

  void _initFields() {
    final scan = _scanController.currentScan;
    idController = TextEditingController(text: scan?.studentId ?? '');
    essayController = TextEditingController(text: scan?.essayTotal.toString() ?? '0.0');
    groupTypeController = TextEditingController(text: scan?.groupType ?? '');
    mcqScoreController = TextEditingController(text: scan?.mcqScore.toString() ?? '0.0');

    if (manualEntry && scan == null) {
      // No Step 2 result to work from — start an empty manual scan.
      final version = _connectionController.selectedVersion ??
          _connectionController.masterPacket?.answerVersions.first ??
          'A';
      _scanController.setCurrentScan(GradeResult(
        idSource: 'manual',
        answerVersion: version,
        mcqScore: 0.0,
        mistakes: [],
        questions: [],
        idColumns: [],
        essayTotal: 0.0,
      ));
    }
  }

  @override
  void dispose() {
    idController.dispose();
    essayController.dispose();
    groupTypeController.dispose();
    mcqScoreController.dispose();
    super.dispose();
  }

  Future<void> retakePhoto() => _scanController.clearCurrentScan();

  Future<void> skipPaper() => _scanController.clearCurrentScan();

  void generateReceipt() {
    _applyFieldsToScan();
    _timestamp = DateTime.now().toLocal().toString().split('.')[0];
    _isReceiptGenerated = true;
    notifyListeners();
  }

  void _applyFieldsToScan() {
    final idText = idController.text.trim();
    var groupTypeText = groupTypeController.text.trim();

    // Backup plan for when the letter-column OCR read wasn't clean enough
    // to auto-fill the group field (see cv_engine_service.dart — that
    // field only prefills on a 100% unambiguous read, unlike the ID
    // field which shows through partial reads). If the proctor didn't
    // manually fill it in either, fall back to the ID's own first
    // character — the letter column is physically the first character of
    // the assembled ID string (see questions.cpp's assemble_id), so this
    // is the same data, just read from the (possibly proctor-corrected)
    // ID field instead of the raw unedited OCR result.
    if (groupTypeText.isEmpty && idText.isNotEmpty) {
      final firstChar = idText[0];
      if (RegExp(r'^[A-Za-z]$').hasMatch(firstChar)) {
        groupTypeText = firstChar;
      }
    }

    _scanController.updateCurrentScan(
      studentId: idText,
      essayTotal: double.tryParse(essayController.text) ?? 0.0,
      groupType: groupTypeText.isEmpty ? null : groupTypeText.toUpperCase(),
    );
  }

  /// Local validation, checked before calling [submit]. The widget shows
  /// this as a snackbar and stays on screen when non-null.
  String? get submitValidationError =>
      idController.text.trim().isEmpty ? 'Student ID is required' : null;

  Future<SubmitResult> submit() async {
    _applyFieldsToScan();
    return _submissionController.commitCurrentScan();
  }

  Future<bool> resolveDuplicate(String studentId, DuplicateAction action) {
    final timestamp = DateTime.now().toLocal().toString().split('.')[0];
    return _submissionController.resolveDuplicate(studentId, action, timestamp);
  }
}