import 'dart:io';
import 'package:flutter/services.dart' show rootBundle, MethodChannel;
import 'package:path_provider/path_provider.dart';
import '../../domain/repositories/model_path_repository.dart';

/// Resolves the ONNX model path, the pipeline output directory, and the
/// stage executables directory for the current platform.
///
/// Model path
/// ----------
/// The ONNX model ships as a Flutter asset (bundled at build time), but
/// the C++ engine reads it via a plain filesystem path, not the asset
/// bundle. This service copies it out to app support storage once and
/// caches the path.
///
/// Bin dir (stage executables)
/// ---------------------------
/// `run_exam_pipeline` needs to know where `stage1_boxes` … `stage6_scoring`
/// live at runtime. The resolution strategy is:
///
///  • **Linux desktop**: the stage binaries are installed next to the app
///    executable by `linux/CMakeLists.txt`. We look for them in the same
///    directory as the running process executable.
///
///  • **Android**: the stage binaries are packaged as native libs
///    (`libstage1_boxes.so` etc.) and extracted to the app's
///    `nativeLibraryDir` at install time. This directory is NOT derivable
///    from `path_provider` (it is not `applicationSupportDirectory`'s
///    parent + "/lib" — that was a broken heuristic that caused every
///    pipeline run to fail with "file not found"). We fetch the real path
///    from Android via `ApplicationInfo.nativeLibraryDir`, exposed through
///    a small MethodChannel implemented in MainActivity.kt.
///    NOTE: subprocess execution from `nativeLibraryDir` may still be
///    blocked by SELinux/`noexec` policy on some API levels — this fixes
///    path resolution, not necessarily the exec step itself. Verify on
///    device; if `run_exam_pipeline` still fails after this fix, check
///    `adb logcat` for a permission-denied error instead of a
///    file-not-found error.
///
///  • **Windows**: same idea as Linux, binaries next to the executable.
///
///  • **iOS**: subprocess exec of bundled binaries is NOT permitted by the
///    App Store sandbox. In-process linking would be required — tracked as
///    a separate task.
///
/// Output dir
/// ----------
/// A writable scratch directory under `getApplicationSupportDirectory()`.
/// Cleaned up between sessions by the caller as needed.
class PipelinePathService implements ModelPathRepository {
  PipelinePathService();

  static const String _modelAssetPath = 'assets/models/shamel.onnx';
  static const String _modelFileName  = 'shamel.onnx';

  static const MethodChannel _nativeLibChannel =
      MethodChannel('com.example.nexus_edge/native_lib_dir');

  String? _cachedModelPath;
  String? _cachedBinDir;
  String? _cachedOutputDir;

  @override
  Future<String> resolveBubbleModelPath() async {
    if (_cachedModelPath != null && await File(_cachedModelPath!).exists()) {
      return _cachedModelPath!;
    }

    final appDir  = await getApplicationSupportDirectory();
    final outPath = '${appDir.path}/$_modelFileName';
    final outFile = File(outPath);

    if (!await outFile.exists()) {
      final data = await rootBundle.load(_modelAssetPath);
      await outFile.writeAsBytes(data.buffer.asUint8List(), flush: true);
    }

    _cachedModelPath = outPath;
    return outPath;
  }

  /// Resolves the directory containing the 6 stage executables for the
  /// current platform. Call once at startup and cache the result.
  Future<String> resolveBinDir() async {
    if (_cachedBinDir != null) return _cachedBinDir!;

    if (Platform.isLinux || Platform.isWindows) {
      // On desktop the stage binaries are installed next to (or in a subdir
      // of) the Flutter app bundle by the platform CMakeLists.txt.
      final exeDir = File(Platform.resolvedExecutable).parent.path;
      _cachedBinDir = exeDir;
      return _cachedBinDir!;
    }

    if (Platform.isAndroid) {
      // Ask Android for the real nativeLibraryDir instead of guessing it.
      final String libDir =
          await _nativeLibChannel.invokeMethod('getNativeLibDir');
      _cachedBinDir = libDir;
      return _cachedBinDir!;
    }

    throw UnsupportedError(
      'PipelinePathService.resolveBinDir(): platform not supported. '
      'iOS requires in-process linking; Windows/macOS needs verification.',
    );
  }

  /// Writable scratch directory for pipeline output. The pipeline writes
  /// `<outputDir>/<imageStem>/stage1/` … `stage6/summary.json` here.
  Future<String> resolveOutputDir() async {
    if (_cachedOutputDir != null) return _cachedOutputDir!;
    final appDir = await getApplicationSupportDirectory();
    final outDir = Directory('${appDir.path}/pipeline_output');
    await outDir.create(recursive: true);
    _cachedOutputDir = outDir.path;
    return _cachedOutputDir!;
  }
}