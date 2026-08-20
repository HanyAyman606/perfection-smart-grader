# Exam Scanner C++ FFI Integration Guide

This document outlines how to wire the C++ Exam Scanner pipeline into the Flutter application using Dart FFI. 

## 1. The C API Specification

The shared library (`libexam_scanner_ffi.dll` on Windows) exposes a single `extern "C"` function that encapsulates the entire pipeline.

### Function Signature
```c
int run_exam_pipeline(
    const char* image_path, 
    const char* config_path, 
    const char* output_dir, 
    const char* bin_dir
);
```

### Parameters
* **`image_path`**: Absolute path to the raw input photo (`.jpg` / `.png`).
* **`config_path`**: Absolute path to the `exam_config.json` file for this specific scan.
* **`output_dir`**: Absolute path to the folder where the pipeline should dump the final results and intermediate stages.
* **`bin_dir`**: Absolute path to the folder containing the compiled `stage1_boxes.exe` ... `stage6_scoring.exe` binaries **and** the `shamel.onnx` model file. 

### Return Codes
* `0`: **Success**. The pipeline completed all 6 stages and results are written to `output_dir`.
* `1`: **Invalid Arguments**. A provided path is null, or the input image/config files do not exist on disk.
* `2`: **Pipeline Failure**. One of the internal stages crashed or failed to process the image.

---

## 2. Dart FFI Implementation

You can copy and paste this directly into your Flutter project to interface with the C++ library. 

**Prerequisites**: Add the [`ffi`](https://pub.dev/packages/ffi) package to your `pubspec.yaml`:
```yaml
dependencies:
  ffi: ^2.1.0
```

**Implementation:**
```dart
import 'dart:ffi' as ffi;
import 'package:ffi/ffi.dart';

// 1. Define the FFI function signature
typedef RunExamPipelineC = ffi.Int32 Function(
  ffi.Pointer<Utf8> imagePath,
  ffi.Pointer<Utf8> configPath,
  ffi.Pointer<Utf8> outputDir,
  ffi.Pointer<Utf8> binDir,
);

typedef RunExamPipelineDart = int Function(
  ffi.Pointer<Utf8> imagePath,
  ffi.Pointer<Utf8> configPath,
  ffi.Pointer<Utf8> outputDir,
  ffi.Pointer<Utf8> binDir,
);

class ExamScanner {
  late final ffi.DynamicLibrary _lib;
  late final RunExamPipelineDart _runPipeline;

  /// Initializes the FFI binding. 
  /// [libraryPath] should be the path to the loaded DLL/SO (e.g., 'libexam_scanner_ffi.dll')
  ExamScanner(String libraryPath) {
    _lib = ffi.DynamicLibrary.open(libraryPath);
    _runPipeline = _lib.lookupFunction<RunExamPipelineC, RunExamPipelineDart>('run_exam_pipeline');
  }

  /// Runs the C++ scanner pipeline.
  /// Returns true on success, false if the pipeline failed or arguments were invalid.
  bool processExam({
    required String imagePath,
    required String configPath,
    required String outputDir,
    required String binDir,
  }) {
    // Convert Dart strings to C-compatible UTF-8 pointers
    final cImagePath = imagePath.toNativeUtf8();
    final cConfigPath = configPath.toNativeUtf8();
    final cOutputDir = outputDir.toNativeUtf8();
    final cBinDir = binDir.toNativeUtf8();

    try {
      // Execute the C++ function
      final result = _runPipeline(cImagePath, cConfigPath, cOutputDir, cBinDir);
      
      if (result == 1) {
        print("ExamScanner FFI: Invalid arguments or missing input files.");
      } else if (result == 2) {
        print("ExamScanner FFI: Pipeline execution failed internally.");
      }
      
      return result == 0; 
    } finally {
      // Free the allocated C-strings to prevent memory leaks
      calloc.free(cImagePath);
      calloc.free(cConfigPath);
      calloc.free(cOutputDir);
      calloc.free(cBinDir);
    }
  }
}
```

## 3. Bundle Requirements

For the FFI call to succeed, the Flutter app must ship with the following assets accessible on the device's filesystem. The path to this folder must be passed as `binDir`:
* `libexam_scanner_ffi.dll` (The shared library you load in Dart)
* `stage1_boxes.exe`
* `stage2_warp.exe`
* `stage3_orient.exe`
* `stage4_final.exe`
* `stage5_inference.exe`
* `stage6_scoring.exe`
* `shamel.onnx` (The neural network weights)
