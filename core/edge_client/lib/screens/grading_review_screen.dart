import 'dart:async';
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../providers/exam_session_provider.dart';
import '../models/exam_models.dart';
import '../services/websocket_client.dart';
import '../services/printer_service.dart';
import 'duplicate_resolution_dialog.dart';

enum _ReviewState { editing, submitting, saved }

class GradingReviewScreen extends StatefulWidget {
  const GradingReviewScreen({super.key});

  @override
  State<GradingReviewScreen> createState() => _GradingReviewScreenState();
}

class _GradingReviewScreenState extends State<GradingReviewScreen> {
  final _studentIdController = TextEditingController();
  final _essayScoreController = TextEditingController();

  _ReviewState _state = _ReviewState.editing;
  String? _originalStudentId; // Captured before proctor edits, for idSource comparison
  String? _savedTimestamp;

  StreamSubscription? _eventSub;

  @override
  void initState() {
    super.initState();
    final session = Provider.of<ExamSessionProvider>(context, listen: false);
    if (session.currentScan != null) {
      _originalStudentId = session.currentScan!.studentId;
      _studentIdController.text = session.currentScan!.studentId ?? "";
      // Show blank instead of "0.0" for essay score when it's zero
      final essay = session.currentScan!.essayTotal;
      _essayScoreController.text = essay == 0.0 ? "" : essay.toString();
    }

    // Listen to raw websocket events for ScoreSaved / ScoreDuplicate
    final client = Provider.of<WebSocketClient>(context, listen: false);
    _eventSub = client.eventStream.listen(_onServerEvent);
  }

  void _onServerEvent(ServerEvent event) {
    if (!mounted) return;

    if (event is ScoreSaved) {
      setState(() {
        _state = _ReviewState.saved;
      });
    } else if (event is ScoreDuplicate) {
      _showDuplicateDialog(event.comparison);
    } else if (event is DuplicateResolved) {
      final session = Provider.of<ExamSessionProvider>(context, listen: false);
      // For overwrite: stay in saved state so proctor can print
      // For keep_previous / discard_both: pop back to scanner
      // The provider already clears currentScan for non-overwrite
      if (session.currentScan != null) {
        // Overwrite was chosen — we're still showing this scan
        setState(() {
          _state = _ReviewState.saved;
        });
      } else {
        // keep_previous or discard_both — go back to scanner
        if (mounted) Navigator.of(context).pop();
      }
    }
  }

  Future<void> _showDuplicateDialog(DuplicateComparison comparison) async {
    final result = await showDialog<DuplicateAction>(
      context: context,
      barrierDismissible: false,
      builder: (context) => DuplicateResolutionDialog(comparison: comparison),
    );

    if (result != null && mounted) {
      final session = Provider.of<ExamSessionProvider>(context, listen: false);
      final timestamp = DateTime.now().toIso8601String();
      session.resolveDuplicate(result, timestamp);
      // Don't pop here — wait for DuplicateResolved event from server
    }
  }

  @override
  void dispose() {
    _eventSub?.cancel();
    _studentIdController.dispose();
    _essayScoreController.dispose();
    super.dispose();
  }

  void _submit() {
    final session = Provider.of<ExamSessionProvider>(context, listen: false);
    if (session.currentScan == null) return;

    final studentId = _studentIdController.text.trim();
    if (studentId.isEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Student ID is required')),
      );
      return;
    }

    // Fix idSource: compare against the ORIGINAL OCR value captured in initState,
    // not against the field we're about to overwrite
    if (_originalStudentId != null && studentId != _originalStudentId) {
      session.currentScan!.idSource = "manual";
    } else if (_originalStudentId == null) {
      session.currentScan!.idSource = "manual";
    }
    // else: idSource stays as "ocr" (proctor didn't change the OCR value)

    session.currentScan!.studentId = studentId;

    final essayVal = double.tryParse(_essayScoreController.text) ?? 0.0;
    session.currentScan!.essayTotal = essayVal;

    setState(() {
      _state = _ReviewState.submitting;
    });

    _savedTimestamp = DateTime.now().toIso8601String();
    session.submitCurrentScan(_savedTimestamp!);
    // Don't pop or print here — wait for ScoreSaved event from the server
  }

  void _printReceipt() {
    final session = Provider.of<ExamSessionProvider>(context, listen: false);
    if (session.currentScan == null) return;

    PrinterService.instance.printReceipt(
      examName: session.masterPacket!.examName,
      studentId: session.currentScan!.studentId ?? "",
      totalScore: session.currentScan!.totalScore,
      mcqScore: session.currentScan!.mcqScore,
      essayScore: session.currentScan!.essayTotal,
      mistakes: session.currentScan!.mistakes,
      timestamp: _savedTimestamp ?? DateTime.now().toIso8601String(),
    );

    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(content: Text('Receipt sent to printer')),
    );
  }

  void _nextStudent() {
    final session = Provider.of<ExamSessionProvider>(context, listen: false);
    session.clearCurrentScan();
    Navigator.of(context).pop();
  }

  @override
  Widget build(BuildContext context) {
    final session = context.watch<ExamSessionProvider>();
    final scan = session.currentScan;

    if (scan == null) {
      return const Scaffold(body: Center(child: Text("No scan active")));
    }

    final isSaved = _state == _ReviewState.saved;
    final isSubmitting = _state == _ReviewState.submitting;

    return Scaffold(
      appBar: AppBar(
        title: Text(isSaved ? 'Grade Saved ✓' : 'Review Grade'),
        leading: isSaved
            ? null // No back button when saved — use "Next Student"
            : IconButton(
                icon: const Icon(Icons.close),
                onPressed: () {
                  session.clearCurrentScan();
                  Navigator.of(context).pop();
                },
              ),
      ),
      body: Padding(
        padding: const EdgeInsets.all(24.0),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            TextField(
              controller: _studentIdController,
              decoration: InputDecoration(
                labelText: 'Student ID',
                border: const OutlineInputBorder(),
                suffixIcon: scan.idSource == 'ocr'
                    ? const Icon(Icons.check_circle, color: Colors.green)
                    : const Icon(Icons.edit, color: Colors.orange),
              ),
              keyboardType: TextInputType.text,
              enabled: !isSaved && !isSubmitting,
            ),
            const SizedBox(height: 24),
            Text(
              'MCQ Score: ${scan.mcqScore}',
              style: const TextStyle(fontSize: 20, fontWeight: FontWeight.bold),
            ),
            if (session.masterPacket!.hasEssays)
              Padding(
                padding: const EdgeInsets.only(top: 24.0),
                child: TextField(
                  controller: _essayScoreController,
                  decoration: const InputDecoration(
                    labelText: 'Essay Score',
                    border: OutlineInputBorder(),
                  ),
                  keyboardType: const TextInputType.numberWithOptions(decimal: true),
                  enabled: !isSaved && !isSubmitting,
                ),
              ),
            const SizedBox(height: 24),
            const Text(
              'Mistakes:',
              style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
            ),
            Expanded(
              child: ListView.builder(
                itemCount: scan.mistakes.length,
                itemBuilder: (context, index) {
                  final m = scan.mistakes[index];
                  return ListTile(
                    title: Text('Q${m.question}'),
                    subtitle: Text('Correct: ${m.correct} | Given: ${m.given}'),
                  );
                },
              ),
            ),
            const SizedBox(height: 24),

            // --- Action buttons change based on state ---
            if (!isSaved) ...[
              ElevatedButton(
                onPressed: isSubmitting ? null : _submit,
                style: ElevatedButton.styleFrom(
                  padding: const EdgeInsets.symmetric(vertical: 16),
                  backgroundColor: Colors.blueAccent,
                ),
                child: isSubmitting
                    ? const SizedBox(
                        height: 20, width: 20,
                        child: CircularProgressIndicator(color: Colors.white, strokeWidth: 2),
                      )
                    : const Text('SUBMIT'),
              ),
            ] else ...[
              // Saved state: Print (repeatable) + Next Student
              Row(
                children: [
                  Expanded(
                    child: ElevatedButton.icon(
                      onPressed: _printReceipt,
                      icon: const Icon(Icons.print),
                      label: const Text('PRINT RECEIPT'),
                      style: ElevatedButton.styleFrom(
                        padding: const EdgeInsets.symmetric(vertical: 16),
                        backgroundColor: Colors.teal,
                      ),
                    ),
                  ),
                  const SizedBox(width: 16),
                  Expanded(
                    child: ElevatedButton.icon(
                      onPressed: _nextStudent,
                      icon: const Icon(Icons.arrow_forward),
                      label: const Text('NEXT STUDENT'),
                      style: ElevatedButton.styleFrom(
                        padding: const EdgeInsets.symmetric(vertical: 16),
                        backgroundColor: Colors.blueAccent,
                      ),
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
