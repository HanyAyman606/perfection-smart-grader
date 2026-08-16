import 'package:flutter/material.dart';
import '../../../../domain/entities/exam_models.dart';

/// The "Mistakes:" heading + list. Returns an empty widget when there are
/// none — callers don't need to check emptiness themselves.
///
/// Redesigned so the question number is large and unmissable, and the
/// correct answer vs. the student's given answer are clearly labeled and
/// color-coded (green = correct, red = what the student actually wrote) —
/// a corrector should never have to squint to tell which is which.
class MistakesList extends StatelessWidget {
  const MistakesList({super.key, required this.mistakes});

  final List<Mistake> mistakes;

  @override
  Widget build(BuildContext context) {
    if (mistakes.isEmpty) return const SizedBox.shrink();

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Row(
          children: [
            const Text(
              'Mistakes',
              style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
            ),
            const SizedBox(width: 8),
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
              decoration: BoxDecoration(
                color: Colors.redAccent.withOpacity(0.15),
                borderRadius: BorderRadius.circular(12),
              ),
              child: Text(
                '${mistakes.length}',
                style: const TextStyle(
                  fontSize: 13,
                  fontWeight: FontWeight.bold,
                  color: Colors.redAccent,
                ),
              ),
            ),
          ],
        ),
        const SizedBox(height: 10),
        ListView.separated(
          shrinkWrap: true,
          physics: const NeverScrollableScrollPhysics(),
          itemCount: mistakes.length,
          separatorBuilder: (_, __) => const SizedBox(height: 8),
          itemBuilder: (context, i) {
            final m = mistakes[i];
            return _MistakeRow(mistake: m);
          },
        ),
        const SizedBox(height: 16),
      ],
    );
  }
}

class _MistakeRow extends StatelessWidget {
  const _MistakeRow({required this.mistake});

  final Mistake mistake;

  @override
  Widget build(BuildContext context) {
    return Container(
      decoration: BoxDecoration(
        border: Border.all(color: Colors.grey.withOpacity(0.3)),
        borderRadius: BorderRadius.circular(10),
      ),
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
      child: Row(
        children: [
          // Large, unmissable question number.
          SizedBox(
            width: 56,
            child: Text(
              'Q${mistake.question}',
              style: const TextStyle(
                fontSize: 20,
                fontWeight: FontWeight.bold,
              ),
            ),
          ),
          Container(width: 1, height: 36, color: Colors.grey.withOpacity(0.3)),
          const SizedBox(width: 14),
          // Correct answer — clearly labeled, green.
          Expanded(
            child: _AnswerBlock(
              label: 'CORRECT',
              value: mistake.correct,
              color: Colors.green,
            ),
          ),
          const SizedBox(width: 10),
          const Icon(Icons.arrow_forward, size: 16, color: Colors.grey),
          const SizedBox(width: 10),
          // What the student actually wrote — clearly labeled, red.
          Expanded(
            child: _AnswerBlock(
              label: 'GIVEN',
              value: mistake.given,
              color: Colors.redAccent,
            ),
          ),
        ],
      ),
    );
  }
}

class _AnswerBlock extends StatelessWidget {
  const _AnswerBlock({
    required this.label,
    required this.value,
    required this.color,
  });

  final String label;
  final String value;
  final Color color;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          label,
          style: TextStyle(
            fontSize: 10,
            fontWeight: FontWeight.bold,
            letterSpacing: 0.5,
            color: color.withOpacity(0.8),
          ),
        ),
        const SizedBox(height: 2),
        Text(
          value,
          style: TextStyle(
            fontSize: 18,
            fontWeight: FontWeight.bold,
            color: color,
          ),
        ),
      ],
    );
  }
}