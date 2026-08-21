import 'dart:async';
import 'package:sqflite/sqflite.dart';
import 'package:path/path.dart' as p;
import 'package:path_provider/path_provider.dart';
import '../../domain/entities/queue_models.dart';
import '../../domain/repositories/offline_queue_repository.dart';

/// sqflite-backed offline scan queue. One row per queued scan; see
/// QueuedScan.toDbRow/fromDbRow for the exact column mapping.
///
/// A single broadcast stream (`_pendingController`) backs `watchPending()`
/// — re-queried from the DB on every mutating call rather than diffed in
/// memory, since queue sizes here (tens, maybe low hundreds of scans) make
/// a full re-read trivially cheap and this avoids an entire class of
/// "stream got out of sync with the DB" bugs.
class OfflineQueueManager implements OfflineQueueRepository {
  static const String _table = 'queued_scans';
  static const int _dbVersion = 1;

  Database? _db;
  final _pendingController = StreamController<List<QueuedScan>>.broadcast();

  /// Overridable for tests (sqflite_common_ffi's in-memory/test factory) —
  /// production code path (no override) uses the real on-device sqflite
  /// plugin via getDatabasesPath().
  final Future<Database> Function()? _openOverride;

  OfflineQueueManager({Future<Database> Function()? openOverride}) : _openOverride = openOverride;

  Future<Database> _database() async {
    if (_db != null) return _db!;
    if (_openOverride != null) {
      _db = await _openOverride!();
      return _db!;
    }
    final docsDir = await getApplicationDocumentsDirectory();
    final dbPath = p.join(docsDir.path, 'offline_scan_queue.db');
    _db = await openDatabase(
      dbPath,
      version: _dbVersion,
      onCreate: (db, version) => db.execute('''
        CREATE TABLE $_table (
          queue_id TEXT PRIMARY KEY,
          payload_json TEXT NOT NULL,
          raw_image_path TEXT,
          annotated_id_path TEXT,
          annotated_mcq_path TEXT,
          queued_at INTEGER NOT NULL,
          sync_state TEXT NOT NULL,
          attempt_count INTEGER NOT NULL DEFAULT 0
        )
      '''),
    );
    return _db!;
  }

  Future<void> _notifyPendingChanged() async {
    if (_pendingController.hasListener) {
      _pendingController.add(await pending());
    }
  }

  @override
  Future<String> enqueue(QueuedScan scan) async {
    final db = await _database();
    await db.insert(_table, scan.toDbRow(), conflictAlgorithm: ConflictAlgorithm.replace);
    await _notifyPendingChanged();
    return scan.queueId;
  }

  @override
  Future<List<QueuedScan>> pending() async {
    final db = await _database();
    final rows = await db.query(_table, orderBy: 'queued_at ASC');
    return rows.map(QueuedScan.fromDbRow).toList();
  }

  @override
  Future<QueuedScan?> findByStudentId(String studentId) async {
    // student_id lives inside payload_json, not its own column — queue
    // sizes here don't justify a denormalized column + migration for a
    // lookup that happens once per scan attempt, not in a hot loop.
    final all = await pending();
    for (final scan in all) {
      if (scan.studentId == studentId) return scan;
    }
    return null;
  }

  @override
  Future<void> markSyncing(String queueId) async {
    final db = await _database();
    await db.update(
      _table,
      {'sync_state': QueueSyncState.syncing.name},
      where: 'queue_id = ?',
      whereArgs: [queueId],
    );
    await _notifyPendingChanged();
  }

  @override
  Future<void> markAttemptFailed(String queueId) async {
    final db = await _database();
    final rows = await db.query(_table, where: 'queue_id = ?', whereArgs: [queueId]);
    if (rows.isEmpty) return;
    final current = QueuedScan.fromDbRow(rows.first);
    await db.update(
      _table,
      {
        'sync_state': QueueSyncState.failed.name,
        'attempt_count': current.attemptCount + 1,
      },
      where: 'queue_id = ?',
      whereArgs: [queueId],
    );
    await _notifyPendingChanged();
  }

  @override
  Future<void> markSynced(String queueId) async {
    final db = await _database();
    await db.delete(_table, where: 'queue_id = ?', whereArgs: [queueId]);
    await _notifyPendingChanged();
  }

  @override
  Future<void> discard(List<String> queueIds) async {
    if (queueIds.isEmpty) return;
    final db = await _database();
    final placeholders = List.filled(queueIds.length, '?').join(',');
    await db.delete(_table, where: 'queue_id IN ($placeholders)', whereArgs: queueIds);
    await _notifyPendingChanged();
  }

  @override
  Stream<List<QueuedScan>> watchPending() {
    // Fire the current state to new listeners immediately, then let
    // subsequent mutations push updates.
    pending().then((list) {
      if (!_pendingController.isClosed) _pendingController.add(list);
    });
    return _pendingController.stream;
  }

  @override
  Future<void> dispose() async {
    await _pendingController.close();
    await _db?.close();
    _db = null;
  }
}