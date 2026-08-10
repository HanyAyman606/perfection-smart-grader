import 'dart:ffi';
import 'dart:io';
import 'package:ffi/ffi.dart';

// Matches include/ffi.h exactly:
//   const char* process_exam_in_memory(const char* image_path, const char* config_json);
//   void free_string(char* str);
typedef _ProcessExamC = Pointer<Utf8> Function(Pointer<Utf8> imagePath, Pointer<Utf8> configJson);
typedef _ProcessExamDart = Pointer<Utf8> Function(Pointer<Utf8> imagePath, Pointer<Utf8> configJson);

typedef _FreeStringC = Void Function(Pointer<Utf8> ptr);
typedef _FreeStringDart = void Function(Pointer<Utf8> ptr);

/// Isolate-safe wrapper is layered on top in cv_engine_service.dart (these
/// blocking FFI calls must run via Dart's compute()/Isolate.run(), never
/// directly on the UI isolate — YOLO inference is not instant on a phone).
class NativeCvBindings {
  static NativeCvBindings? _instance;
  static NativeCvBindings get instance => _instance!;

  late final DynamicLibrary _lib;
  late final _ProcessExamDart _processExam;
  late final _FreeStringDart _freeString;

  NativeCvBindings._(this._lib) {
    _processExam = _lib.lookupFunction<_ProcessExamC, _ProcessExamDart>('process_exam_in_memory');
    _freeString = _lib.lookupFunction<_FreeStringC, _FreeStringDart>('free_string');
  }

  static String _resolveLibName() {
    if (Platform.isWindows) return 'ai_corrector.dll';
    if (Platform.isMacOS) return 'libai_corrector.dylib';
    if (Platform.isLinux || Platform.isAndroid) return 'libai_corrector.so';
    throw UnsupportedError('Unsupported platform for native CV engine.');
  }

  /// Returns null on success, error string on failure. Call once at
  /// startup on the main isolate — forces resolution of all exported
  /// symbols so a stale/incomplete native library is caught here, not on
  /// first scan.
  static String? probeAvailability() {
    try {
      final lib = DynamicLibrary.open(_resolveLibName());
      _instance = NativeCvBindings._(lib);
      return null;
    } catch (e) {
      return 'Failed to load native library:\n$e';
    }
  }

  /// compute()/Isolate.run() spawns a fresh isolate with its own memory
  /// space — the `_instance` set by probeAvailability() on the main
  /// isolate is NOT visible there. DynamicLibrary.open() is cheap and
  /// idempotent at the OS level (the loader just returns the
  /// already-mapped handle), so each background isolate calls this once
  /// to get its own binding rather than sharing the main isolate's.
  static NativeCvBindings forCurrentIsolate() {
    final lib = DynamicLibrary.open(_resolveLibName());
    return NativeCvBindings._(lib);
  }

  /// Single-call pipeline: perspective-correct, crop panels, run YOLO
  /// inference, and score — all in one native call. Returns a JSON string
  /// containing both the quality/confidence signal and the full grading
  /// result. Always run this via compute()/Isolate.run() — see
  /// cv_engine_service.dart.
  String callProcessExam(String imagePath, String configJson) {
    final cImagePath = imagePath.toNativeUtf8();
    final cConfigJson = configJson.toNativeUtf8();
    Pointer<Utf8>? resultPtr;
    try {
      resultPtr = _processExam(cImagePath, cConfigJson);
      return resultPtr.toDartString();
    } finally {
      malloc.free(cImagePath);
      malloc.free(cConfigJson);
      if (resultPtr != null) _freeString(resultPtr);
    }
  }
}
