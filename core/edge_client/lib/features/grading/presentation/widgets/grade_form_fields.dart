import 'package:flutter/material.dart';
import 'id_review_banner.dart';

/// The editable Student ID / (manual) MCQ / Essay fields. Purely
/// presentational — all state lives in the passed-in TextEditingControllers
/// (owned by GradingReviewController), all behavior is "enabled or not".
///
/// Group type is derived automatically from the first letter of the student
/// ID (see GradingReviewController._applyFieldsToScan) — no manual input
/// is needed or shown here.
///
/// Essay field is hidden when [hasEssay] is false (controlled by the
/// has_essays flag sent in the MasterPacket from the dashboard).
class GradeFormFields extends StatelessWidget {
  const GradeFormFields({
    super.key,
    required this.idController,
    required this.essayController,
    required this.mcqScoreController,
    required this.enabled,
    required this.isManual,
    required this.showIdReviewWarning,
    required this.hasEssay,
    this.essayMaxTotal,
  });

  final TextEditingController idController;
  final TextEditingController essayController;
  final TextEditingController mcqScoreController;
  final bool enabled;
  final bool isManual;
  final bool showIdReviewWarning;

  /// When false the essay field is hidden entirely. Driven by the
  /// MasterPacket.hasEssays flag set on the PySide dashboard.
  final bool hasEssay;

  /// Sum of essay marks configured by the admin — shown as a hint, and
  /// enforced as the field's ceiling by GradingReviewController.
  final double? essayMaxTotal;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        TextField(
          controller: idController,
          enabled: enabled,
          textCapitalization: TextCapitalization.characters,
          decoration: const InputDecoration(labelText: 'Student ID', border: OutlineInputBorder()),
        ),
        if (showIdReviewWarning) ...[
          const SizedBox(height: 8),
          const IdReviewBanner(),
        ],
        const SizedBox(height: 16),
        if (isManual) ...[
          TextField(
            controller: mcqScoreController,
            enabled: enabled,
            keyboardType: TextInputType.number,
            decoration: const InputDecoration(labelText: 'MCQ Score', border: OutlineInputBorder()),
          ),
          const SizedBox(height: 16),
        ],
        if (hasEssay) ...[
          TextField(
            controller: essayController,
            enabled: enabled,
            keyboardType: TextInputType.number,
            decoration: InputDecoration(
              labelText: 'Essay Score',
              helperText: essayMaxTotal != null ? 'Max: $essayMaxTotal' : null,
              border: const OutlineInputBorder(),
            ),
          ),
        ],
      ],
    );
  }
}