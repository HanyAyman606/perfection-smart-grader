import 'dart:ffi';
import 'dart:io';
import 'package:ffi/ffi.dart';

// Signatures
typedef _BsCalibrateC = Pointer<Utf8> Function(Pointer<Utf8> requestPath);
typedef _BsCalibrateDart = Pointer<Utf8> Function(Pointer<Utf8> requestPath);

typedef _BsRunC = Pointer<Utf8> Function(Pointer<Utf8> imagePath, Pointer<Utf8> profilePath, Pointer<Utf8> debugImagePath);
typedef _BsRunDart = Pointer<Utf8> Function(Pointer<Utf8> imagePath, Pointer<Utf8> profilePath, Pointer<Utf8> debugImagePath);

typedef _BsProfileStatusC = Int32 Function(Pointer<Utf8> profilePath);
typedef _BsProfileStatusDart = int Function(Pointer<Utf8> profilePath);

typedef _BsFreeStringC = Void Function(Pointer<Utf8> ptr);
typedef _BsFreeStringDart = void Function(Pointer<Utf8> ptr);

class NativeOmrBindings {
  static NativeOmrBindings? _instance;
  static NativeOmrBindings get instance => _instance!;

  late final DynamicLibrary _lib;
  
  late final _BsCalibrateDart _bsCalibrate;
  late final _BsRunDart _bsRun;
  late final _BsProfileStatusDart _bsProfileStatus;
  late final _BsFreeStringDart _bsFreeString;

  NativeOmrBindings._(this._lib) {
    _bsCalibrate = _lib.lookupFunction<_BsCalibrateC, _BsCalibrateDart>('bs_calibrate');
    _bsRun = _lib.lookupFunction<_BsRunC, _BsRunDart>('bs_run');
    _bsProfileStatus = _lib.lookupFunction<_BsProfileStatusC, _BsProfileStatusDart>('bs_profile_status');
    _bsFreeString = _lib.lookupFunction<_BsFreeStringC, _BsFreeStringDart>('bs_free_string');
  }

  /// Returns null on success, error string on failure.
  /// Forces resolution of all 4 exported symbols so a stale/incomplete
  /// native library is caught here at startup, not on first real use.
  static String? probeAvailability() {
    try {
      String libName;
      if (Platform.isWindows) {
        libName = 'omr_engine.dll';
      } else if (Platform.isMacOS) {
        libName = 'omr_engine.dylib';
      } else if (Platform.isLinux || Platform.isAndroid) {
        libName = 'libomr_engine.so';
      } else {
        return "Unsupported platform for native OMR engine.";
      }

      final DynamicLibrary lib = DynamicLibrary.open(libName);
      // Constructor calls lookupFunction for bs_calibrate, bs_run,
      // bs_profile_status, and bs_free_string — if any symbol is
      // missing this will throw immediately.
      _instance = NativeOmrBindings._(lib);
      return null;
    } catch (e) {
      return "Failed to load native library: \n$e";
    }
  }

  String callCalibrate(String requestPath) {
    final cRequestPath = requestPath.toNativeUtf8();
    Pointer<Utf8>? resultPtr;
    try {
      resultPtr = _bsCalibrate(cRequestPath);
      return resultPtr.toDartString();
    } finally {
      malloc.free(cRequestPath);
      if (resultPtr != null) {
        _bsFreeString(resultPtr);
      }
    }
  }

  String callRun(String imagePath, String profilePath, String debugImagePath) {
    final cImagePath = imagePath.toNativeUtf8();
    final cProfilePath = profilePath.toNativeUtf8();
    final cDebugImagePath = debugImagePath.toNativeUtf8();
    Pointer<Utf8>? resultPtr;
    
    try {
      resultPtr = _bsRun(cImagePath, cProfilePath, cDebugImagePath);
      return resultPtr.toDartString();
    } finally {
      malloc.free(cImagePath);
      malloc.free(cProfilePath);
      malloc.free(cDebugImagePath);
      if (resultPtr != null) {
        _bsFreeString(resultPtr);
      }
    }
  }

  bool callProfileStatus(String profilePath) {
    final cProfilePath = profilePath.toNativeUtf8();
    try {
      final status = _bsProfileStatus(cProfilePath);
      return status == 1;
    } finally {
      malloc.free(cProfilePath);
    }
  }
}
