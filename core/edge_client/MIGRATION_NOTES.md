# cv_engine FFI Migration — Current Architecture

> **Status:** Migration to `run_exam_pipeline` complete.  
> Last updated: 2026-08-19

## What changed

The Flutter edge client (`edge_client`) now calls **`run_exam_pipeline`** from
`libexam_scanner_ffi.so` instead of the old `process_exam_in_memory` contract.

### Old contract (removed)
```
const char* process_exam_in_memory(image_path, config_json_string)
void        free_string(char*)
```
One blocking call, in-memory, returned a JSON **string** with everything
bundled. Caller had to `free_string()` the returned pointer.

### New contract (current)
```c
int run_exam_pipeline(image_path, config_path, output_dir, bin_dir)
// Returns: 0 = success, 1 = invalid args, 2 = pipeline failure
```
- No return string, nothing to free.
- Shells out to 6 stage executables (`stage1_boxes` … `stage6_scoring`).
- Writes output to `output_dir/<image_stem>/stage1/` … `stage6/`.
- **Dart reads `stage6/summary.json`** — a merged file written by stage6_scoring
  that contains quality signals + grading payload in one place.

## summary.json schema

Written by `stage6_scoring.cpp`'s `write_summary()` after every successful run:

```json
{
  "status": "SUCCESS",
  "id_found": true,
  "mcq_found": true,
  "orientation_confidence": "HIGH",
  "orientation_reason": "mcq=landscape + squares horizontal, top half -> already correct",
  "has_missing_rows": false,
  "grading": {
    "exam_id": "...",
    "exam_type": "quiz",
    "num_questions_expected": 25,
    "sheets": {
      "<image_stem>": {
        "ID": "C042",
        "MCQ": { "Q1": "A", "Q2": "C", ... },
        "needs_review": [],
        "warnings": []
      }
    }
  }
}
```

## exam_config.json schema (new)

Written by `MasterPacket.toExamConfigJson()` in `exam_models.dart`:

```json
{
  "exam_id":        "My Exam",
  "exam_type":      "quiz",
  "num_questions":  25,
  "id_letters":     "CDEFMW",
  "id_digit_cols":  3,
  "num_choices":    4,
  "id_letter_cols": 1,
  "num_mcq_columns": 3,
  "model_path":     "/path/to/shamel.onnx"
}
```

The engine derives per-column question counts itself via ceil-division.
**None of the old tuning fields** (`tuning_blur`, `mcq_column_sizes`, etc.)
exist in this schema — they are unused and must not be sent.

## Quality gate

`blurScore` and a single `confidence` field **do not exist** in the new pipeline.
Quality is determined by:

| Signal                   | Source file            | Action if bad             |
|--------------------------|------------------------|---------------------------|
| `id_found`               | stage1/_results.json   | Retake (NO_ID_PANEL)      |
| `mcq_found`              | stage1/_results.json   | Retake (NO_MCQ_PANEL)     |
| `orientation_confidence` | stage3/_results.json   | Warn, still proceed       |
| `has_missing_rows`       | stage6 warnings        | Warn, still proceed       |

Blur gating is **not implemented** in the new pipeline. If needed in future,
add a Laplacian variance check to `stage1_boxes.cpp` and include `blur_score`
in `summary.json`.

## Files changed

| File | Change |
|------|--------|
| `cv_engine/src/stage6_scoring.cpp` | Writes `stage6/summary.json` merging stage1+stage3 quality signals |
| `edge_client/lib/data/scanning/native_cv_bindings.dart` | Full rewrite — new typedef, `libexam_scanner_ffi.so`, no free_string |
| `edge_client/lib/data/scanning/cv_engine_service.dart` | Full rewrite — writes config to disk, calls pipeline, reads summary.json |
| `edge_client/lib/data/scanning/model_path_service.dart` | Replaced `ModelPathService` with `PipelinePathService` (adds `resolveBinDir`, `resolveOutputDir`) |
| `edge_client/lib/domain/repositories/model_path_repository.dart` | Added `resolveBinDir()` / `resolveOutputDir()` to interface |
| `edge_client/lib/domain/repositories/scan_engine_repository.dart` | Added `outputDir`/`binDir` params; updated docstring |
| `edge_client/lib/domain/entities/exam_models.dart` | Rewrote `toExamConfigJson()` for new schema |
| `edge_client/lib/features/scanning/presentation/panel_preview_screen.dart` | Passes binDir/outputDir; typed error messages per `CvPipelineError` |
| `edge_client/lib/core/presentation/native_library_error_screen.dart` | Updated library name in error text |
| `edge_client/linux/CMakeLists.txt` | Added install() rules for .so + stage binaries + shamel.onnx |
| `edge_client/pubspec.yaml` | shamel.onnx asset, libexam_scanner_ffi comment, added `path` dep |

## Platform status

| Platform | Status |
|----------|--------|
| Linux desktop | ✅ Supported — binaries installed by CMakeLists.txt |
| Windows desktop | ⚠️ CMakeLists not yet updated — same pattern as Linux |
| Android | ⚠️ Needs real-device verification (`std::system()` + SELinux on API 29+) |
| iOS | ❌ Not supported — App Store bans subprocess exec; in-process linking required |

## Model filename

**`shamel.onnx`** — this is the new engine default and must be consistent across:
- `assets/models/shamel.onnx` (Flutter asset)
- `cv_engine/models/shamel.onnx` (C++ build output dir)
- `PipelinePathService._modelFileName` (Dart constant)
- `exam_config.hpp`'s `model_path` default
