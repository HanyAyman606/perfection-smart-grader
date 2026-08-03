import 'dart:io';
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:path_provider/path_provider.dart';
import '../providers/exam_session_provider.dart';
import '../models/exam_models.dart';

class GradingReviewScreen extends StatefulWidget {
  const GradingReviewScreen({super.key});

  @override
  State<GradingReviewScreen> createState() => _GradingReviewScreenState();
}

class _GradingReviewScreenState extends State<GradingReviewScreen> {
  late TextEditingController _idController;
  late TextEditingController _essayController;
    late TextEditingController _groupTypeController;
  String _timestamp = "";
  bool _isReceiptGenerated = false;

  @override
  void initState() {
    super.initState();
    final session = Provider.of<ExamSessionProvider>(context, listen: false);
    final scan = session.currentScan;
    _idController = TextEditingController(text: scan?.studentId ?? '');
    _essayController = TextEditingController(text: scan?.essayTotal.toString() ?? '0.0');
    _groupTypeController = TextEditingController(text: scan?.groupType ?? '');
  }

  @override
  void dispose() {
    _idController.dispose();
    _essayController.dispose();
    _groupTypeController.dispose();   // <-- add
    super.dispose();
  }

  void _retakePhoto() {
    final session = Provider.of<ExamSessionProvider>(context, listen: false);
    session.clearCurrentScan();
    Navigator.of(context).pop();
  }

  void _generateReceipt() {
    final session = Provider.of<ExamSessionProvider>(context, listen: false);
    final groupTypeText = _groupTypeController.text.trim();
    session.updateCurrentScan(
      studentId: _idController.text.trim(),
      essayTotal: double.tryParse(_essayController.text) ?? 0.0,
      groupType: groupTypeText.isEmpty ? null : groupTypeText.toUpperCase(),   // <-- add
    );
    setState(() {
      _timestamp = DateTime.now().toLocal().toString().split('.')[0];
      _isReceiptGenerated = true;
    });
  }

  Future<void> _submit() async {
    final session = Provider.of<ExamSessionProvider>(context, listen: false);
    await session.commitCurrentScan();
    if (mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Grade saved successfully!'), backgroundColor: Colors.green),
      );
      Navigator.of(context).pop();
    }
  }

  void _viewEngineAnalysis() async {
    final docDir = await getApplicationDocumentsDirectory();
    final debugImagePath = '${docDir.path}/latest_scan_debug.png';
    final file = File(debugImagePath);

    if (await file.exists()) {
      if (mounted) {
        showDialog(
          context: context,
          builder: (_) => Dialog(
            insetPadding: EdgeInsets.zero,
            backgroundColor: Colors.black,
            child: Stack(
              children: [
                InteractiveViewer(
                  maxScale: 6.0,
                  child: Image.file(file, fit: BoxFit.contain, width: double.infinity, height: double.infinity),
                ),
                Positioned(
                  top: 16, right: 16,
                  child: IconButton(
                    icon: const Icon(Icons.close, color: Colors.white, size: 32),
                    onPressed: () => Navigator.of(context).pop(),
                  ),
                ),
              ],
            ),
          ),
        );
      }
    } else {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('No debug image found.')));
    }
  }

  @override
  Widget build(BuildContext context) {
    final session = context.watch<ExamSessionProvider>();
    final scan = session.currentScan;
    if (scan == null) return const SizedBox.shrink();

    bool needsReview = false;
    for (var m in scan.mistakes) {
      if (m.given == 'rejected' || m.given == 'multiple_marks') needsReview = true;
    }

    final total = scan.mcqScore + scan.essayTotal;

    return Scaffold(
      appBar: AppBar(
        title: const Text('Review Grade'),
        automaticallyImplyLeading: false,
      ),
      body: SingleChildScrollView(
        padding: const EdgeInsets.all(16.0),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            // Top Section
            TextField(
              controller: _idController,
              enabled: !_isReceiptGenerated,
              decoration: const InputDecoration(labelText: 'Student ID', border: OutlineInputBorder()),
            ),
            const SizedBox(height: 16),
            TextField(                                                          // <-- add this whole block
              controller: _groupTypeController,
              enabled: !_isReceiptGenerated,
              textCapitalization: TextCapitalization.characters,
              decoration: const InputDecoration(labelText: 'Group Type (e.g. M, N, W)', border: OutlineInputBorder()),
            ),
            const SizedBox(height: 16),
            TextField(
              controller: _essayController,
              enabled: !_isReceiptGenerated,
              keyboardType: TextInputType.number,
              decoration: const InputDecoration(labelText: 'Essay Score', border: OutlineInputBorder()),
            ),
            const SizedBox(height: 24),
            Text('MCQ Score: ${scan.mcqScore}', style: const TextStyle(fontSize: 18)),
            const SizedBox(height: 24),
            Row(
              children: [
                Expanded(
                  child: OutlinedButton.icon(
                    onPressed: _retakePhoto,
                    icon: const Icon(Icons.camera_alt),
                    label: const Text('Retake Photo'),
                    style: OutlinedButton.styleFrom(padding: const EdgeInsets.symmetric(vertical: 16)),
                  ),
                ),
                if (!_isReceiptGenerated) const SizedBox(width: 16),
                if (!_isReceiptGenerated)
                  Expanded(
                    child: ElevatedButton.icon(
                      onPressed: _generateReceipt,
                      icon: const Icon(Icons.receipt),
                      label: const Text('Generate Receipt'),
                      style: ElevatedButton.styleFrom(padding: const EdgeInsets.symmetric(vertical: 16)),
                    ),
                  ),
              ],
            ),

            // Bottom Section (Conditionally Visible)
            if (_isReceiptGenerated) ...[
              const SizedBox(height: 24),
              const Divider(),
              const SizedBox(height: 16),
              Card(
                child: Padding(
                  padding: const EdgeInsets.all(16.0),
                  child: Column(
                    children: [
                      Text('Student ID: ${scan.studentId ?? "MISSING"}', style: const TextStyle(fontSize: 20, fontWeight: FontWeight.bold)),
                      if (scan.groupType != null && scan.groupType!.isNotEmpty)
                        Text('Group: ${scan.groupType}', style: const TextStyle(fontSize: 16, color: Colors.grey)),
                      const Divider(),
                      Text('MCQ Score: ${scan.mcqScore}', style: const TextStyle(fontSize: 18)),
                      Text('Essay Score: ${scan.essayTotal}', style: const TextStyle(fontSize: 18)),
                      const Divider(),
                      Text('TOTAL: $total', style: const TextStyle(fontSize: 24, fontWeight: FontWeight.bold, color: Colors.green)),
                      const SizedBox(height: 8),
                      Text('Timestamp: $_timestamp', style: const TextStyle(color: Colors.grey)),
                    ],
                  ),
                ),
              ),
              const SizedBox(height: 16),
              ElevatedButton.icon(
                style: ElevatedButton.styleFrom(
                  backgroundColor: needsReview ? Colors.red.shade700 : Colors.blueGrey,
                  padding: const EdgeInsets.symmetric(vertical: 16)
                ),
                onPressed: _viewEngineAnalysis,
                icon: const Icon(Icons.troubleshoot, color: Colors.white),
                label: Text(
                  needsReview ? 'Suspicious Marks Detected — View Engine Analysis' : 'View Engine Analysis',
                  style: const TextStyle(color: Colors.white)
                ),
              ),
              const SizedBox(height: 16),
              if (scan.mistakes.isNotEmpty) ...[
                const Text('Mistakes:', style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold)),
                const SizedBox(height: 8),
                ListView.builder(
                  shrinkWrap: true,
                  physics: const NeverScrollableScrollPhysics(),
                  itemCount: scan.mistakes.length,
                  itemBuilder: (context, i) {
                    final m = scan.mistakes[i];
                    return ListTile(
                      title: Text('Q${m.question}'),
                      subtitle: Text('Correct: ${m.correct} | Given: ${m.given}'),
                    );
                  },
                ),
                const SizedBox(height: 16),
              ],
              Row(
                children: [
                  Expanded(
                    child: OutlinedButton.icon(
                      onPressed: () { /* Trigger Print Later */ },
                      icon: const Icon(Icons.print),
                      label: const Text('Print Receipt'),
                      style: OutlinedButton.styleFrom(padding: const EdgeInsets.symmetric(vertical: 16)),
                    ),
                  ),
                  const SizedBox(width: 16),
                  Expanded(
                    child: ElevatedButton.icon(
                      onPressed: _submit,
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
