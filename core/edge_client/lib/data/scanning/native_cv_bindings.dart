import 'dart:ffi';
import 'dart:io';
import 'package:ffi/ffi.dart';

// Matches include/ffi_api.h exactly:
//   int run_exam_pipeline(const char* image_path, const char* config_path,
//                         const char* output_dir, const char* bin_dir);
//
// Returns 0 = success, 1 = invalid arguments, 2 = pipeline failure.
// Nothing is malloc'd by the native side — no free_string needed.
typedef _RunPipelineC = Int32 Function(
  Pointer<Utf8> imagePath,
  Pointer<Utf8> configPath,
  Pointer<Utf8> outputDir,
  Pointer<Utf8> binDir,
);
typedef _RunPipelineDart = int Function(
  Pointer<Utf8> imagePath,
  Pointer<Utf8> configPath,
  Pointer<Utf8> outputDir,
  Pointer<Utf8> binDir,
);

/// Isolate-safe wrapper layered on top in cv_engine_service.dart.
/// Blocking FFI calls run via compute()/Isolate.run() — never on the
/// UI isolate directly.
class NativeCvBindings {
  static NativeCvBindings? _instance;
  static NativeCvBindings get instance => _instance!;

  late final DynamicLibrary _lib;
  late final _RunPipelineDart _runPipeline;

  NativeCvBindings._(this._lib) {
    _runPipeline = _lib.lookupFunction<_RunPipelineC, _RunPipelineDart>(
      'run_exam_pipeline',
    );
  }

  static String _resolveLibName() {
    if (Platform.isWindows) return 'exam_scanner_ffi.dll';
    if (Platform.isMacOS) return 'libexam_scanner_ffi.dylib';
    if (Platform.isLinux || Platform.isAndroid) return 'libexam_scanner_ffi.so';
    throw UnsupportedError('Unsupported platform for native CV engine.');
  }

  /// Returns null on success, error string on failure. Call once at
  /// startup on the main isolate — forces resolution of the exported symbol
  /// so a stale/incomplete native library is caught here, not on first scan.
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
  /// idempotent at the OS level, so each background isolate calls this once.
  static NativeCvBindings forCurrentIsolate() {
    final lib = DynamicLibrary.open(_resolveLibName());
    return NativeCvBindings._(lib);
  }

  /// Launches the 6-stage pipeline as subprocesses and waits for completion.
  ///
  /// Returns:
  ///   0 — success; results written to `outputDir/<imageStem>/stage6/summary.json`
  ///   1 — invalid arguments (missing paths)
  ///   2 — pipeline failure (one of the stage binaries exited non-zero)
  ///
  /// Always run this via compute()/Isolate.run() — it blocks while the
  /// C++ side shells out to 6 separate executables (can take several seconds).
  int callRunPipeline({
    required String imagePath,
    required String configPath,
    required String outputDir,
    required String binDir,
  }) {
    final cImagePath  = imagePath.toNativeUtf8();
    final cConfigPath = configPath.toNativeUtf8();
    final cOutputDir  = outputDir.toNativeUtf8();
    final cBinDir     = binDir.toNativeUtf8();
    try {
      return _runPipeline(cImagePath, cConfigPath, cOutputDir, cBinDir);
    } finally {
      malloc.free(cImagePath);
      malloc.free(cConfigPath);
      malloc.free(cOutputDir);
      malloc.free(cBinDir);
    }
  }
}
