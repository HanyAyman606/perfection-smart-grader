import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../../connection/controller/connection_controller.dart';
import '../../scanning/controller/scan_controller.dart';
import '../controller/grading_review_controller.dart';
import '../controller/submission_controller.dart';
import '../../../domain/entities/exam_models.dart';
import 'duplicate_resolution_dialog.dart';
import 'widgets/annotated_image_viewer.dart';
import 'widgets/grade_form_fields.dart';
import 'widgets/mistakes_list.dart';
import 'widgets/receipt_card.dart';
import 'widgets/view_scan_buttons.dart';

/// Review/edit a scanned (or manually entered) grade before submitting it.
/// Presentation-only: all state and orchestration live in
/// [GradingReviewController]; this widget wires that controller to small,
/// stateless section widgets and handles the things only a BuildContext
/// can do (dialogs, snackbars, navigation).
class GradingReviewScreen extends StatelessWidget {
  /// True when reached via the 3+ retry manual-entry fallback rather than
  /// a successful Step 2 result.
  final bool manualEntry;

  const GradingReviewScreen({super.key, this.manualEntry = false});

  @override
  Widget build(BuildContext context) {
    return ChangeNotifierProvider<GradingReviewController>(
      create: (context) => GradingReviewController(
        scanController: context.read<ScanController>(),
        submissionController: context.read<SubmissionController>(),
        connectionController: context.read<ConnectionController>(),
        manualEntry: manualEntry,
      ),
      child: const _GradingReviewView(),
    );
  }
}

class _GradingReviewView extends StatelessWidget {
  const _GradingReviewView();

  Future<void> _retakePhoto(BuildContext context) async {
    final controller = context.read<GradingReviewController>();
    await controller.retakePhoto();
    if (context.mounted) Navigator.of(context).pop();
  }

  Future<void> _skipPaper(BuildContext context) async {
    final confirm = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Skip this paper?'),
        content: const Text('This scan will be discarded and not saved — continue?'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('CANCEL')),
          TextButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('SKIP', style: TextStyle(color: Colors.red)),
          ),
        ],
      ),
    );

    if (confirm != true || !context.mounted) return;
    final controller = context.read<GradingReviewController>();
    await controller.skipPaper();
    if (context.mounted) Navigator.of(context).pop();
  }

  Future<void> _submit(BuildContext context) async {
    final controller = context.read<GradingReviewController>();

    final validationError = controller.submitValidationError;
    if (validationError != null) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(validationError), backgroundColor: Colors.red),
      );
      return;
    }

    final result = await controller.submit();
    if (!context.mounted) return;

    switch (result) {
      case SubmitFailed(reason: final reason):
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text('Cannot submit: ${reason ?? "Unknown error"}'),
            backgroundColor: Colors.red,
          ),
        );
        return; // stay on screen

      case SubmitDuplicate(comparison: final comparison):
        final action = await showDialog<DuplicateAction>(
          context: context,
          barrierDismissible: false,
          builder: (_) => DuplicateResolutionDialog(comparison: comparison),
        );
        if (action == null || !context.mounted) return;

        final resolved = await controller.resolveDuplicate(comparison.studentId, action);
        if (!context.mounted) return;

        if (!resolved) {
          ScaffoldMessenger.of(context).showSnackBar(
            const SnackBar(content: Text('Failed to resolve duplicate. Try again.'), backgroundColor: Colors.red),
          );
          return;
        }

        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Grade resolved successfully!'), backgroundColor: Colors.green),
        );
        Navigator.of(context).pop();
        return;

      case SubmitSuccess():
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Grade saved successfully!'), backgroundColor: Colors.green),
        );
        Navigator.of(context).pop();
        return;
    }
  }

  @override
  Widget build(BuildContext context) {
    final controller = context.watch<GradingReviewController>();
    final scan = controller.scan;
    if (scan == null) return const SizedBox.shrink();

    final isManual = controller.manualEntry;
    final isReceiptGenerated = controller.isReceiptGenerated;

    return Scaffold(
      appBar: AppBar(
        title: Text(isManual ? 'Manual grade entry' : 'Review grade'),
        automaticallyImplyLeading: false,
      ),
      body: SingleChildScrollView(
        padding: const EdgeInsets.all(16.0),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            GradeFormFields(
              idController: controller.idController,
              groupTypeController: controller.groupTypeController,
              essayController: controller.essayController,
              mcqScoreController: controller.mcqScoreController,
              enabled: !isReceiptGenerated,
              isManual: isManual,
              showIdReviewWarning: scan.idNeedsReview,
            ),
            const SizedBox(height: 24),
            if (!isManual) Text('MCQ Score: ${scan.mcqScore}', style: const TextStyle(fontSize: 18)),
            const SizedBox(height: 24),
            Row(
              children: [
                Expanded(
                  child: OutlinedButton.icon(
                    onPressed: () => _retakePhoto(context),
                    icon: const Icon(Icons.camera_alt),
                    label: Text(isManual ? 'Back to scanner' : 'Retake Photo'),
                    style: OutlinedButton.styleFrom(padding: const EdgeInsets.symmetric(vertical: 16)),
                  ),
                ),
                if (!isReceiptGenerated) const SizedBox(width: 16),
                if (!isReceiptGenerated)
                  Expanded(
                    child: ElevatedButton.icon(
                      onPressed: controller.generateReceipt,
                      icon: const Icon(Icons.receipt),
                      label: const Text('Generate Receipt'),
                      style: ElevatedButton.styleFrom(padding: const EdgeInsets.symmetric(vertical: 16)),
                    ),
                  ),
              ],
            ),
            if (isReceiptGenerated) ...[
              const SizedBox(height: 24),
              const Divider(),
              const SizedBox(height: 16),
              ReceiptCard(
                scan: scan,
                isManual: isManual,
                total: controller.total,
                timestamp: controller.timestamp,
              ),
              const SizedBox(height: 16),
              if (!isManual)
                ViewScanButtons(
                  annotatedIdImagePath: scan.annotatedIdImagePath,
                  annotatedMcqImagePath: scan.annotatedMcqImagePath,
                  needsReview: scan.needsReview,
                  onViewId: () => AnnotatedImageViewer.show(context, path: scan.annotatedIdImagePath!, title: 'ID panel'),
                  onViewMcq: () => AnnotatedImageViewer.show(context, path: scan.annotatedMcqImagePath!, title: 'Answer panel'),
                ),
              const SizedBox(height: 16),
              MistakesList(mistakes: scan.mistakes),
              Row(
                children: [
                  Expanded(
                    child: OutlinedButton.icon(
                      onPressed: () => _skipPaper(context),
                      icon: const Icon(Icons.skip_next, color: Colors.redAccent),
                      label: const Text('Skip Paper', style: TextStyle(color: Colors.redAccent)),
                      style: OutlinedButton.styleFrom(
                        padding: const EdgeInsets.symmetric(vertical: 16),
                        side: const BorderSide(color: Colors.redAccent),
                      ),
                    ),
                  ),
                  const SizedBox(width: 16),
                  Expanded(
                    child: ElevatedButton.icon(
                      onPressed: () => _submit(context),
                      icon: const Icon(Icons.check),
                      label: const Text('Submit & Next'),
                      style: ElevatedButton.styleFrom(padding: const EdgeInsets.symmetric(vertical: 16), backgroundColor: Colors.blueAccent),
                    ),
                  ),
                ],
              ),
            ],
          ],
        ),
      ),
    );
  }
}
