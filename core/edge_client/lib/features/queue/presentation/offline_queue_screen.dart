import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../../domain/entities/queue_models.dart';
import '../../../domain/repositories/offline_queue_repository.dart';

/// Shows every scan currently sitting in the offline queue — student ID
/// (if known), when it was queued, and its sync state — with a per-item
/// erase action and a "clear all" action. Mirrors DebugLogScreen's
/// existing pattern (read the repository via Provider, simple Scaffold +
/// AppBar action).
///
/// Erasing deletes both the queue entry (via
/// [OfflineQueueRepository.discard]) and its backing image files —
/// [OfflineQueueRepository.discard]'s doc comment is explicit that the
/// repository does not delete images itself, so this screen owns that
/// step, same division of responsibility [SessionCacheManager] already
/// uses for the online-path retention policy.
class OfflineQueueScreen extends StatelessWidget {
  const OfflineQueueScreen({super.key});

  Future<void> _deleteImageFiles(QueuedScan scan) async {
    for (final p in [
      scan.rawImagePath,
      scan.annotatedIdImagePath,
      scan.annotatedMcqImagePath,
    ]) {
      if (p == null) continue;
      try {
        final f = File(p);
        if (await f.exists()) await f.delete();
      } catch (e) {
        debugPrint('OfflineQueueScreen: failed to delete $p: $e');
      }
    }
  }

  Future<void> _eraseOne(BuildContext context, QueuedScan scan) async {
    final queue = context.read<OfflineQueueRepository>();
    await _deleteImageFiles(scan);
    await queue.discard([scan.queueId]);
  }

  Future<void> _eraseAll(BuildContext context, List<QueuedScan> scans) async {
    final queue = context.read<OfflineQueueRepository>();
    for (final scan in scans) {
      await _deleteImageFiles(scan);
    }
    await queue.discard(scans.map((s) => s.queueId).toList());
  }

  Future<bool> _confirm(BuildContext context, String title, String message) async {
    final result = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: Text(title),
        content: Text(message),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(dialogContext).pop(false),
            child: const Text('Cancel'),
          ),
          TextButton(
            onPressed: () => Navigator.of(dialogContext).pop(true),
            child: const Text('Erase', style: TextStyle(color: Colors.red)),
          ),
        ],
      ),
    );
    return result ?? false;
  }

  String _formatQueuedAt(DateTime t) {
    final now = DateTime.now();
    final diff = now.difference(t);
    if (diff.inMinutes < 1) return 'just now';
    if (diff.inMinutes < 60) return '${diff.inMinutes}m ago';
    if (diff.inHours < 24) return '${diff.inHours}h ago';
    return '${t.year}-${t.month.toString().padLeft(2, '0')}-${t.day.toString().padLeft(2, '0')} '
        '${t.hour.toString().padLeft(2, '0')}:${t.minute.toString().padLeft(2, '0')}';
  }

  ({IconData icon, Color color, String label}) _stateVisuals(QueueSyncState state) {
    switch (state) {
      case QueueSyncState.pending:
        return (icon: Icons.schedule, color: Colors.orange, label: 'Waiting to sync');
      case QueueSyncState.syncing:
        return (icon: Icons.sync, color: Colors.blue, label: 'Syncing…');
      case QueueSyncState.failed:
        return (icon: Icons.error_outline, color: Colors.red, label: 'Sync failed — will retry');
    }
  }

  @override
  Widget build(BuildContext context) {
    final queue = context.read<OfflineQueueRepository>();

    return Scaffold(
      appBar: AppBar(
        title: const Text('Offline Queue'),
      ),
      body: StreamBuilder<List<QueuedScan>>(
        stream: queue.watchPending(),
        builder: (context, snapshot) {
          final scans = snapshot.data ?? const <QueuedScan>[];

          if (!snapshot.hasData) {
            return const Center(child: CircularProgressIndicator());
          }

          if (scans.isEmpty) {
            return const Center(
              child: Padding(
                padding: EdgeInsets.all(24.0),
                child: Text(
                  'Nothing queued — every scan has synced to the dashboard.',
                  textAlign: TextAlign.center,
                  style: TextStyle(color: Colors.grey),
                ),
              ),
            );
          }

          return Column(
            children: [
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 16.0, vertical: 8.0),
                child: Row(
                  mainAxisAlignment: MainAxisAlignment.spaceBetween,
                  children: [
                    Text(
                      '${scans.length} scan${scans.length == 1 ? '' : 's'} queued',
                      style: Theme.of(context).textTheme.titleMedium,
                    ),
                    TextButton.icon(
                      icon: const Icon(Icons.delete_sweep, color: Colors.red),
                      label: const Text('Clear all', style: TextStyle(color: Colors.red)),
                      onPressed: () async {
                        final ok = await _confirm(
                          context,
                          'Erase all queued scans?',
                          'This permanently deletes ${scans.length} queued scan${scans.length == 1 ? '' : 's'} '
                              'and their images. This cannot be undone, and unsynced scans '
                              'will never reach the dashboard.',
                        );
                        if (ok) await _eraseAll(context, scans);
                      },
                    ),
                  ],
                ),
              ),
              const Divider(height: 1),
              Expanded(
                child: ListView.separated(
                  itemCount: scans.length,
                  separatorBuilder: (_, __) => const Divider(height: 1),
                  itemBuilder: (context, index) {
                    final scan = scans[index];
                    final visuals = _stateVisuals(scan.syncState);
                    final studentId = scan.studentId;

                    return ListTile(
                      leading: Icon(visuals.icon, color: visuals.color),
                      title: Text(
                        studentId == null || studentId.isEmpty
                            ? 'Unknown student ID'
                            : 'Student $studentId',
                      ),
                      subtitle: Text(
                        'Queued ${_formatQueuedAt(scan.queuedAt)} · ${visuals.label}'
                        '${scan.attemptCount > 0 ? ' · ${scan.attemptCount} attempt${scan.attemptCount == 1 ? '' : 's'}' : ''}',
                      ),
                      trailing: IconButton(
                        icon: const Icon(Icons.delete_outline),
                        tooltip: 'Erase this scan',
                        onPressed: () async {
                          final ok = await _confirm(
                            context,
                            'Erase this scan?',
                            'This permanently deletes the queued scan for '
                                '${studentId == null || studentId.isEmpty ? 'this student' : 'student $studentId'} '
                                'and its images. This cannot be undone.',
                          );
                          if (ok) await _eraseOne(context, scan);
                        },
                      ),
                    );
                  },
                ),
              ),
            ],
          );
        },
      ),
    );
  }
}