import 'dart:ffi';
import 'dart:io';
import 'package:ffi/ffi.dart';

// Matches include/ffi.h exactly:
//   const char* step1_extract_panels(const char* image_path, const char* config_json);
//   const char* step2_infer_and_score(const char* id_panel_path, const char* mcq_panel_path, const char* config_json);
//   void free_string(char* str);
typedef _Step1C = Pointer<Utf8> Function(Pointer<Utf8> imagePath, Pointer<Utf8> configJson);
typedef _Step1Dart = Pointer<Utf8> Function(Pointer<Utf8> imagePath, Pointer<Utf8> configJson);

typedef _Step2C = Pointer<Utf8> Function(
    Pointer<Utf8> idPanelPath, Pointer<Utf8> mcqPanelPath, Pointer<Utf8> configJson);
typedef _Step2Dart = Pointer<Utf8> Function(
    Pointer<Utf8> idPanelPath, Pointer<Utf8> mcqPanelPath, Pointer<Utf8> configJson);

typedef _FreeStringC = Void Function(Pointer<Utf8> ptr);
typedef _FreeStringDart = void Function(Pointer<Utf8> ptr);

/// Isolate-safe wrapper is layered on top in cv_engine_service.dart (these
/// blocking FFI calls must run via Dart's compute()/Isolate.run(), never
/// directly on the UI isolate — Step 2 in particular runs YOLO inference
/// and is not instant on a phone).
class NativeCvBindings {
  static NativeCvBindings? _instance;
  static NativeCvBindings get instance => _instance!;

  late final DynamicLibrary _lib;
  late final _Step1Dart _step1;
  late final _Step2Dart _step2;
  late final _FreeStringDart _freeString;

  NativeCvBindings._(this._lib) {
    _step1 = _lib.lookupFunction<_Step1C, _Step1Dart>('step1_extract_panels');
    _step2 = _lib.lookupFunction<_Step2C, _Step2Dart>('step2_infer_and_score');
    _freeString = _lib.lookupFunction<_FreeStringC, _FreeStringDart>('free_string');
  }

  static String _resolveLibName() {
    if (Platform.isWindows) return 'ai_corrector.dll';
    if (Platform.isMacOS) return 'libai_corrector.dylib';
    if (Platform.isLinux || Platform.isAndroid) return 'libai_corrector.so';
    throw UnsupportedError('Unsupported platform for native CV engine.');
  }

  /// Returns null on success, error string on failure. Call once at
  /// startup on the main isolate — forces resolution of all 3 exported
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

  /// Step 1: perspective-correct + crop ID/MCQ panels, return confidence
  /// signal. Cheap relative to Step 2 but still native/blocking — run off
  /// the UI isolate for consistency and to avoid a frame hitch on older
  /// phones.
  String callStep1(String imagePath, String configJson) {
    final cImagePath = imagePath.toNativeUtf8();
    final cConfigJson = configJson.toNativeUtf8();
    Pointer<Utf8>? resultPtr;
    try {
      resultPtr = _step1(cImagePath, cConfigJson);
      return resultPtr.toDartString();
    } finally {
      malloc.free(cImagePath);
      malloc.free(cConfigJson);
      if (resultPtr != null) _freeString(resultPtr);
    }
  }

  /// Step 2: run YOLO inference + scoring on the panels from Step 1.
  /// Always run this via compute()/Isolate.run() — see cv_engine_service.
  String callStep2(String idPanelPath, String mcqPanelPath, String configJson) {
    final cIdPath = idPanelPath.toNativeUtf8();
    final cMcqPath = mcqPanelPath.toNativeUtf8();
    final cConfigJson = configJson.toNativeUtf8();
    Pointer<Utf8>? resultPtr;
    try {
      resultPtr = _step2(cIdPath, cMcqPath, cConfigJson);
      return resultPtr.toDartString();
    } finally {
      malloc.free(cIdPath);
      malloc.free(cMcqPath);
      malloc.free(cConfigJson);
      if (resultPtr != null) _freeString(resultPtr);
    }
  }
}
