import 'package:flutter/material.dart';
import 'id_review_banner.dart';

/// The editable Student ID / Group / (manual) MCQ / Essay fields. Purely
/// presentational — all state lives in the passed-in TextEditingControllers
/// (owned by GradingReviewController), all behavior is "enabled or not".
class GradeFormFields extends StatelessWidget {
  const GradeFormFields({
    super.key,
    required this.idController,
    required this.groupTypeController,
    required this.essayController,
    required this.mcqScoreController,
    required this.enabled,
    required this.isManual,
    required this.showIdReviewWarning,
  });

  final TextEditingController idController;
  final TextEditingController groupTypeController;
  final TextEditingController essayController;
  final TextEditingController mcqScoreController;
  final bool enabled;
  final bool isManual;
  final bool showIdReviewWarning;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        TextField(
          controller: idController,
          enabled: enabled,
          decoration: const InputDecoration(labelText: 'Student ID', border: OutlineInputBorder()),
        ),
        if (showIdReviewWarning) ...[
          const SizedBox(height: 8),
          const IdReviewBanner(),
        ],
        const SizedBox(height: 16),
        TextField(
          controller: groupTypeController,
          enabled: enabled,
          textCapitalization: TextCapitalization.characters,
          decoration: const InputDecoration(labelText: 'Group Type (e.g. M, N, W)', border: OutlineInputBorder()),
        ),
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
        TextField(
          controller: essayController,
          enabled: enabled,
          keyboardType: TextInputType.number,
          decoration: const InputDecoration(labelText: 'Essay Score', border: OutlineInputBorder()),
        ),
      ],
    );
  }
}
