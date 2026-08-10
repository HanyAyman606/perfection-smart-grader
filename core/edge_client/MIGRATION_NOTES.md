# Nexus Edge — Flutter client rewrite notes

This replaces the old `edge_client` app (manual-calibration + single-step
`bs_run` FFI) with a client wired against the new `cv_engine` two-step
FFI architecture (`step1_extract_panels` / `step2_infer_and_score`).

## What changed and why

### Dropped entirely
- **Calibration screen and flow** (`calibration_screen.dart`,
  `calibration_service.dart`, the 4-tap anchor system, `.yml` profiles,
  `bs_calibrate`/`bs_profile_status`). The new engine detects panels via
  YOLO + quad detection every scan — there's no fixed template to
  calibrate against anymore.
- **`camera` package + live preview + `AlignmentHeuristic`**
  (`alignment_heuristic.dart`). Capture now goes through the phone's
  native camera app via `image_picker`, which generally out-performs a
  custom Flutter camera preview for image quality. This is why Step 1's
  blur/confidence gate is load-bearing, not optional — input variance is
  higher across OEM camera apps than a locked custom preview would give.
- **Landscape lock.** No fixed calibration frame to align against anymore,
  so the phone can be held either way; the C++ engine's orientation
  reasoning handles rotation. (`SystemChrome.setPreferredOrientations` is
  simply not called anywhere in the new scanner flow.)
- **`native_omr_bindings.dart` / `omr_service.dart`.** Replaced by
  `native_cv_bindings.dart` (raw FFI surface matching `ffi.h` exactly) and
  `cv_engine_service.dart` (isolate-safe wrapper + grade assembly).

### New
- **`panel_preview_screen.dart`** — the Step 1 retake/continue screen.
  Stacked, full-width, pinch-zoomable ID + MCQ crops (not side-by-side —
  legibility on a phone screen matters more than seeing both at once).
  Continue is disabled whenever `confidence != "OK"`; the warning banner
  surfaces the specific reason code from `PanelExtractionResult`.
- **`session_cache_manager.dart`** — bounds on-device photo storage. Raw
  photos are deleted immediately on retake (zero future value) and
  immediately after a scan's `submit_score` is sent (only the small
  annotated images are kept, for dispute review). A session-boundary
  purge runs on every new auth success, plus a hard cap
  (`_maxKeptAnnotatedPairs`) evicts oldest-first as a backstop. All I/O
  runs off the UI isolate implicitly (nothing here blocks the main
  isolate directly — file ops are naturally async).
- **`RetryCounter`** (in `session_cache_manager.dart`) — 3+ retakes on the
  same student surfaces a manual-entry fallback dialog, routing into
  `GradingReviewScreen(manualEntry: true)`.
- **QR scan-to-connect** (`widgets/qr_scan_sheet.dart`) — reads the
  desktop dashboard's connect QR, which encodes a bare IP string (see
  `admin_dashboard/widgets/qr_code.py` +
  `dashboard.py:generate_qr_pixmap(get_local_ip())`). No JSON, no `ws://`
  scheme, no port in the QR — the app's own default port is used
  regardless. Manual IP entry remains available as a fallback.
- **`model_path_service.dart`** — copies the bundled `bubble.onnx` asset
  out to app-support storage once, since the C++ engine needs a real
  filesystem path (`ExamConfig.model_path`), not a Flutter asset handle.

### Carried over unchanged
- `websocket_client.dart` — the `auth`/`ping`/`submit_score`/
  `resolve_duplicate` message contract matches the new PySide
  `workers/websocket_server.py` exactly, verified field-by-field.
- `printer_service.dart`, `duplicate_resolution_dialog.dart`,
  `scanner_guide_painter.dart`, `utils/image_size.dart`.

### Config translation (the mismatch we found and fixed)
`MasterPacket.toExamConfigJson()` in `models/exam_models.dart` is the
translation layer between the dashboard's wire format
(`mcq_columns: {num_cols, columns: {"1": n, ...}}`) and the C++ engine's
`ExamConfig::from_json`. The C++ side was updated (see the `config.h` /
`questions.cpp` / `ffi.cpp` patch delivered earlier) to consume the
dashboard's authoritative per-column question counts directly instead of
re-deriving an even split — the two formulas happen to agree for the
common 3-column case but aren't guaranteed to in general, and only one of
them reflects the actual printed template. **You need to rebuild
`cv_engine` with that patch** for `mcq_column_sizes` to actually be used;
without it, `ExamConfig` still parses fine (`num_question_columns` alone
is enough for back-compat), it just falls back to the even-split
derivation.

## Still needed on your end (platform wiring, not app logic)

1. **Bundle `libai_corrector.so` (Android) / the Windows/macOS
   equivalent** into the native platform project — this was true for the
   old `omr_engine` library too and isn't something Dart code can do;
   it's a Gradle/Xcode/CMake step per platform.
2. **Add `assets/models/bubble.onnx`** to the Flutter project directory
   at that exact path (referenced in `pubspec.yaml`).
3. **Android camera + storage permissions** for `image_picker` (camera
   permission) — same permissions the old `camera` package needed, so
   this should already be in your `AndroidManifest.xml`/`Info.plist`; add
   camera permission for `mobile_scanner` too if not already covered by
   the same entry.
4. Rebuild `cv_engine` with the `mcq_column_sizes` patch (4 files:
   `config.h`, `questions.h`, `questions.cpp`, `ffi.cpp`) delivered
   earlier in this conversation.
