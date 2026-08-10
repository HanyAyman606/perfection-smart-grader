import '../../domain/repositories/log_repository.dart';

/// In-memory ring buffer of recent log entries, surfaced via
/// DebugLogScreen for on-device diagnostics without a debugger attached.
/// No longer a singleton — one instance is created at the composition
/// root (main.dart) and injected wherever logging is needed, including
/// into DebugLogScreen itself.
class DebugLog implements LogRepository {
  DebugLog();

  final List<String> _entries = [];
  static const int _maxEntries = 500;

  @override
  void log(String tag, String message) {
    final entry = '${DateTime.now().toIso8601String()} [$tag] $message';
    _entries.add(entry);
    if (_entries.length > _maxEntries) _entries.removeAt(0);
  }

  List<String> get entries => List.unmodifiable(_entries);
  String get asText => _entries.join('\n');

  void clear() {
    _entries.clear();
  }
}
