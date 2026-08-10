import 'package:flutter/material.dart';
import '../../../../domain/entities/exam_models.dart';

/// The generated-receipt summary card: student ID, group, scores, total,
/// timestamp. Purely presentational — all values are passed in.
class ReceiptCard extends StatelessWidget {
  const ReceiptCard({
    super.key,
    required this.scan,
    required this.isManual,
    required this.total,
    required this.timestamp,
  });

  final GradeResult scan;
  final bool isManual;
  final double total;
  final String timestamp;

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16.0),
        child: Column(
          children: [
            Text(
              'Student ID: ${scan.studentId ?? "MISSING"}',
              style: const TextStyle(fontSize: 20, fontWeight: FontWeight.bold),
            ),
            if (scan.groupType != null && scan.groupType!.isNotEmpty)
              Text('Group: ${scan.groupType}', style: const TextStyle(fontSize: 16, color: Colors.grey)),
            const Divider(),
            if (!isManual) Text('MCQ Score: ${scan.mcqScore}', style: const TextStyle(fontSize: 18)),
            Text('Essay Score: ${scan.essayTotal}', style: const TextStyle(fontSize: 18)),
            const Divider(),
            Text('TOTAL: $total', style: const TextStyle(fontSize: 24, fontWeight: FontWeight.bold, color: Colors.green)),
            const SizedBox(height: 8),
            Text('Timestamp: $timestamp', style: const TextStyle(color: Colors.grey)),
          ],
        ),
      ),
    );
  }
}
