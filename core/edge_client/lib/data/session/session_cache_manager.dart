import 'dart:io';
import 'package:flutter/foundation.dart';
import 'package:path_provider/path_provider.dart';
import '../../domain/repositories/session_cache_repository.dart';

/// Bounds on-device storage for scan photos so a long grading session
/// doesn't fill the phone or slow down file I/O. Policy (see design
/// discussion): once a scan is submitted and acknowledged, its raw photo
/// is deleted immediately — only the small annotated (boxes-drawn) images
/// are kept, since those are what a dispute review actually needs. Raw
/// photos and unsubmitted scans are kept until resolved. A hard ceiling
/// on kept annotated-image pairs evicts oldest-first as a safety net.
class SessionCacheManager implements SessionCacheRepository {
  SessionCacheManager();

  static const int _maxKeptAnnotatedPairs = 300;
  static const String _manifestFileName = 'scan_cache_manifest.json';

  String? _sessionId;
  final List<String> _keptAnnotatedPaths = []; // oldest first

  /// Call once per new session (on successful auth / new connection) —
  /// purges anything left over from a previous session so old exams
  /// never silently linger on disk.
  @override
  Future<void> startNewSession(String sessionId) async {
    if (_sessionId == sessionId) return; // already active, don't re-purge
    _sessionId = sessionId;
    _keptAnnotatedPaths.clear();
    await _purgeAllScanFiles();
  }

  Future<Directory> _scanDir() async {
    final docDir = await getApplicationDocumentsDirectory();
    final dir = Directory('${docDir.path}/scans');
    if (!await dir.exists()) await dir.create(recursive: true);
    return dir;
  }

  /// Deletes a failed/rejected Step 1 attempt immediately — a blurry
  /// retake photo has zero future value, no reason to keep it even
  /// briefly.
  @override
  Future<void> discardFailedAttempt(List<String?> paths) async {
    for (final p in paths) {
      if (p == null) continue;
      try {
        final f = File(p);
        if (await f.exists()) await f.delete();
      } catch (e) {
        debugPrint('SessionCacheManager: failed to delete $p: $e');
      }
    }
  }

  /// Call once a scan's submit_score is acknowledged successfully.
  /// Deletes the raw + cropped panel images, keeps only the annotated
  /// pair (small, boxes-drawn, useful for later dispute review), and
  /// enforces the hard cap by evicting the oldest kept pair if needed.
  @override
  Future<void> onSubmitAcknowledged({
    String? rawImagePath,
    String? idPanelPath,
    String? mcqPanelPath,
    String? annotatedIdPath,
    String? annotatedMcqPath,
  }) async {
    for (final p in [rawImagePath, idPanelPath, mcqPanelPath]) {
      if (p == null) continue;
      try {
        final f = File(p);
        if (await f.exists()) await f.delete();
      } catch (e) {
        debugPrint('SessionCacheManager: failed to delete $p: $e');
      }
    }

    if (annotatedIdPath != null) _keptAnnotatedPaths.add(annotatedIdPath);
    if (annotatedMcqPath != null) _keptAnnotatedPaths.add(annotatedMcqPath);

    await _enforceCap();
  }

  Future<void> _enforceCap() async {
    // Each "pair" is 2 paths (id + mcq); cap counts pairs, not raw files.
    const maxPaths = _maxKeptAnnotatedPairs * 2;
    while (_keptAnnotatedPaths.length > maxPaths) {
      final oldest = _keptAnnotatedPaths.removeAt(0);
      try {
        final f = File(oldest);
        if (await f.exists()) await f.delete();
      } catch (e) {
        debugPrint('SessionCacheManager: eviction failed for $oldest: $e');
      }
    }
  }

  Future<void> _purgeAllScanFiles() async {
    try {
      final dir = await _scanDir();
      if (await dir.exists()) {
        await dir.delete(recursive: true);
        await dir.create(recursive: true);
      }
    } catch (e) {
      debugPrint('SessionCacheManager: session-boundary purge failed: $e');
    }
  }

  /// Full manual wipe, exposed as a settings/debug action if needed.
  @override
  Future<void> clearAll() async {
    _keptAnnotatedPaths.clear();
    await _purgeAllScanFiles();
  }
}

/// Tracks retake attempts per in-progress student scan. Surfaces a
/// manual-entry fallback after 3+ retakes so grading isn't blocked by a
/// persistently bad scan environment (bad lighting, damaged sheet, etc).
class RetryCounter {
  static const int manualEntryThreshold = 3;

  int _attempts = 0;

  int get attempts => _attempts;
  bool get shouldOfferManualEntry => _attempts >= manualEntryThreshold;

  void recordRetake() => _attempts++;
  void reset() => _attempts = 0;
}
