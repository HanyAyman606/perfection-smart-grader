import 'package:flutter/material.dart';
import '../../../../domain/entities/exam_models.dart';

/// The "Mistakes:" heading + list. Returns an empty widget when there are
/// none — callers don't need to check emptiness themselves.
class MistakesList extends StatelessWidget {
  const MistakesList({super.key, required this.mistakes});

  final List<Mistake> mistakes;

  @override
  Widget build(BuildContext context) {
    if (mistakes.isEmpty) return const SizedBox.shrink();

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        const Text('Mistakes:', style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold)),
        const SizedBox(height: 8),
        ListView.builder(
          shrinkWrap: true,
          physics: const NeverScrollableScrollPhysics(),
          itemCount: mistakes.length,
          itemBuilder: (context, i) {
            final m = mistakes[i];
            return ListTile(
              title: Text('Q${m.question}'),
              subtitle: Text('Correct: ${m.correct} | Given: ${m.given}'),
            );
          },
        ),
        const SizedBox(height: 16),
      ],
    );
  }
}
