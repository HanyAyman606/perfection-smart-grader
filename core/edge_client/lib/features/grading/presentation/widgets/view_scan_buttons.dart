import 'package:flutter/material.dart';

/// The "View ID scan" / "View answers" (or "Review marks") button row.
/// Hides itself when there's nothing to show.
class ViewScanButtons extends StatelessWidget {
  const ViewScanButtons({
    super.key,
    required this.annotatedIdImagePath,
    required this.annotatedMcqImagePath,
    required this.needsReview,
    required this.onViewId,
    required this.onViewMcq,
  });

  final String? annotatedIdImagePath;
  final String? annotatedMcqImagePath;
  final bool needsReview;
  final VoidCallback onViewId;
  final VoidCallback onViewMcq;

  @override
  Widget build(BuildContext context) {
    if (annotatedIdImagePath == null && annotatedMcqImagePath == null) {
      return const SizedBox.shrink();
    }

    return Row(
      children: [
        if (annotatedIdImagePath != null)
          Expanded(
            child: OutlinedButton.icon(
              onPressed: onViewId,
              icon: const Icon(Icons.badge_outlined),
              label: const Text('View ID scan'),
              style: OutlinedButton.styleFrom(padding: const EdgeInsets.symmetric(vertical: 14)),
            ),
          ),
        if (annotatedIdImagePath != null && annotatedMcqImagePath != null) const SizedBox(width: 12),
        if (annotatedMcqImagePath != null)
          Expanded(
            child: ElevatedButton.icon(
              style: ElevatedButton.styleFrom(
                backgroundColor: needsReview ? Colors.red.shade700 : Colors.blueGrey,
                padding: const EdgeInsets.symmetric(vertical: 14),
              ),
              onPressed: onViewMcq,
              icon: const Icon(Icons.troubleshoot, color: Colors.white),
              label: Text(
                needsReview ? 'Review marks' : 'View answers',
                style: const TextStyle(color: Colors.white),
              ),
            ),
          ),
      ],
    );
  }
}
