import 'package:flutter/material.dart';
import '../../../domain/entities/exam_models.dart';

class DuplicateResolutionDialog extends StatelessWidget {
  final DuplicateComparison comparison;

  const DuplicateResolutionDialog({
    super.key,
    required this.comparison,
  });

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: const Text('Duplicate Score Detected'),
      content: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('Student ID: ${comparison.studentId}', style: const TextStyle(fontWeight: FontWeight.bold)),
          const SizedBox(height: 16),
          const Text('Previous Entry:', style: TextStyle(fontWeight: FontWeight.bold, color: Colors.orange)),
          Text('Score: ${comparison.previousScore}'),
          if (comparison.previousVersion != null) Text('Version: ${comparison.previousVersion}'),
          Text('Time: ${comparison.previousTimestamp}'),
          
          const SizedBox(height: 16),
          const Text('Incoming Scan:', style: TextStyle(fontWeight: FontWeight.bold, color: Colors.green)),
          Text('Score: ${comparison.incomingScore}'),
          if (comparison.incomingVersion != null) Text('Version: ${comparison.incomingVersion}'),
          Text('Time: ${comparison.incomingTimestamp}'),
        ],
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.of(context).pop(DuplicateAction.keepPrevious),
          child: const Text('KEEP PREVIOUS'),
        ),
        TextButton(
          onPressed: () => Navigator.of(context).pop(DuplicateAction.overwrite),
          child: const Text('OVERWRITE NEW'),
        ),
        TextButton(
          onPressed: () => Navigator.of(context).pop(DuplicateAction.discardBoth),
          style: TextButton.styleFrom(foregroundColor: Colors.red),
          child: const Text('DISCARD BOTH'),
        ),
      ],
    );
  }
}
