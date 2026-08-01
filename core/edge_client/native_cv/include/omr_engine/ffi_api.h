#pragma once

// =============================================================================
// OMR ENGINE — PUBLIC FFI API (FOR FLUTTER / DART INTEROP)
// =============================================================================
// This header is the entire contract between the native OMR engine and any FFI
// caller (Dart's dart:ffi, or a C/C++ test harness). It has no dependency on OpenCV
// or the engine's internals — only plain C types — so it can be handed to Dart's
// ffigen (or read on its own) without pulling in the rest of this library.
//
// For the FULL, exhaustive contract — every JSON field, every possible value each
// one can take, every error/warning message string and exactly when it appears —
// see ../../API.md (native_cv/API.md). This header documents signatures and inputs;
// API.md documents outputs field-by-field, value-by-value.
//
// Build native_cv as a shared library (see native_cv/CMakeLists.txt — the
// `omr_engine` target) and load it from Dart with
// `DynamicLibrary.open('libomr_engine.so' / 'omr_engine.dll' / ...)` + `dart:ffi`
// bindings for the four functions below. The CLI (`omr_cli`, built from the same
// source) still works unchanged — the FFI functions are a thin additional entry
// point into the same logic, not a separate implementation.
//
// GENERAL RULES for every function here:
//   - All `const char*` parameters are null-terminated, UTF-8, absolute filesystem
//     paths (or, for bs_calibrate, a path to a request file — see below). Passing a
//     null pointer where a path is required returns a JSON error rather than crashing.
//   - Every function that returns `const char*` heap-allocates that string with
//     malloc(). The caller (Dart) MUST call bs_free_string() on it exactly once, or
//     the memory leaks. Never call free()/dart:ffi's `calloc.free` on it directly —
//     always go through bs_free_string so the allocator stays symmetric even if this
//     library's internals change later.
//   - Every returned string is a single JSON object (never an array/scalar at the top
//     level), so Dart can always start with `jsonDecode(...) as Map<String, dynamic>`.

#if defined(_WIN32)
  #define BS_API extern "C" __declspec(dllexport)
#else
  #define BS_API extern "C" __attribute__((visibility("default")))
#endif

// --------------------------------------------------------------------------------
// bs_calibrate(requestPath) — run once per NEW physical template (a new sheet layout
// or a materially different camera/lighting setup), NOT once per scanned sheet.
// --------------------------------------------------------------------------------
// Flutter's job before calling this: collect the two-click (or combined-click)
// calibration anchors from the user (tapping the corners of each block on a photo of
// a BLANK reference sheet) and WRITE them to a calibration_request file on disk in
// this exact shape (YAML shown; a .json file with the same keys/structure also works
// — cv::FileStorage auto-detects the format from the file extension):
//
//   image_path: "<absolute path to the BLANK reference photo used for calibration>"
//   profile_path: "<absolute path where the learned profile should be written>"
//   num_questions: <total scored questions on the sheet, e.g. 25>
//   choices_per_question: <options per question, e.g. 4 for A-D>
//   questions_per_block: <rows in one visual answer group before it wraps to a new
//                         column, e.g. 10 for a Q1-10 | Q11-20 | Q21-25 layout>
//   id_letter_count: <rows in the student ID's LETTER bubble column, 0 if the sheet
//                     has no letter column>
//   id_digit_columns: <how many digit columns the student ID has (0-9 bubbles each),
//                      0 if the sheet has no numeric ID>
//   id_letter_labels: [<id_letter_count strings, one per row, top to bottom — the
//                       actual printed letters, e.g. ["C","D","E","F","M","W"]; omit
//                       or leave empty to default to A,B,C,...>]
//   blocks_clicks:
//     # EITHER the reduced form (recommended — 2 clicks total regardless of how many
//     # ID columns / answer sub-blocks the layout has):
//     - name: "id"        # only if id_letter_count>0 or id_digit_columns>0
//       x1: <px>, y1: <px>   # center of row0 of the LETTER column (or digit_1 if no letter)
//       x2: <px>, y2: <px>   # center of the very last visible ID bubble (bottom of
//                            # whichever ID column is tallest)
//     - name: "answers"   # only if num_questions>0
//       x1: <px>, y1: <px>   # center of Q1's option A
//       x2: <px>, y2: <px>   # center of the very last question's last option (e.g. Q25/D)
//     # OR the explicit per-block form (one click-pair per column/sub-block by name:
//     # "letter", "digit_1", "digit_2", ..., "answers_1", "answers_2", ...) — only
//     # needed if the reduced form's divider/column auto-detection fails on an
//     # unusual layout; supplying ANY of these names suppresses reduced-form
//     # expansion for that group entirely, so don't mix a partial explicit set with
//     # the reduced click for the same group.
//
// All x/y are PIXEL coordinates in image_path's own resolution (not normalized) —
// exactly what a Flutter tap handler reads off the displayed image, remapped to the
// original image's pixel space if the image was scaled down for display.
//
// Returns JSON: { "success": bool, "error": string, "log": [string, ...] }
//   "log" is the same diagnostic/warning trail the CLI prints to stdout (per-block
//   fill statistics, click-snap distances, missing-bubble warnings, border-box
//   detection) — surface it in a "calibration details" panel; none of it is fatal by
//   itself (fatal problems are reported via "success": false + "error" instead).
// On success, the learned profile is already written to disk at the request's
// profile_path — that path is the only thing Flutter needs to remember (e.g. store it
// alongside the template's name/id in its own database) to call bs_run later.
BS_API const char* bs_calibrate(const char* requestPath);

// --------------------------------------------------------------------------------
// bs_run(imagePath, profilePath, debugImagePath) — run once per SCANNED sheet.
// --------------------------------------------------------------------------------
// imagePath: absolute path to the photo of a filled-in sheet to grade.
// profilePath: absolute path to the profile.yml written by a prior bs_calibrate call
//   for this template.
// debugImagePath: absolute path to write a visual overlay PNG to (green = accepted
//   answer, blue = matched-but-unmarked bubble, red = no contour matched at all,
//   orange = rejected — ink present but not bubble-shaped, e.g. an X/checkmark/slash).
//   Pass "" (empty string, NOT null) to skip generating this image entirely.
//
// Returns JSON:
//   {
//     "success": bool, "error": string,
//     "letter": string,           // one of profile's id_letter_labels, or "blank" (no
//                                  // mark cleared threshold), "multiple_marks" (two-plus
//                                  // clean marks with comparable ink — needs a human),
//                                  // "rejected" (marked but not bubble-shaped)
//     "digits": [string, ...],    // one entry per id_digit_columns, left to right;
//                                  // each is "0".."9", "blank", "multiple_marks", or "rejected"
//     "answers": [string, ...],   // one entry per num_questions, in question order;
//                                  // each is "A".."?" (up to choices_per_question letters),
//                                  // "blank", "multiple_marks", or "rejected"
//     "warnings": [string, ...],  // human-readable manual-review flags (which question,
//                                  // which options, why) — never fatal; "success" can be
//                                  // true with a non-empty warnings list
//     "registration": {
//       "matched": bool,          // false means no border-box anchors could be matched
//                                  // against the calibration photo, so this photo's
//                                  // framing is ASSUMED identical to calibration's —
//                                  // results are more trustworthy when true
//       "matched_boxes": int,
//       "scale_x": double, "scale_y": double, "offset_x": double, "offset_y": double
//     }
//   }
// "multiple_marks" and "rejected" are deliberately never resolved into a guessed
// letter — Flutter should route both to manual review rather than auto-scoring them.
BS_API const char* bs_run(const char* imagePath, const char* profilePath, const char* debugImagePath);

// --------------------------------------------------------------------------------
// bs_profile_status(profilePath) — cheap check for whether a template has already
// been calibrated, e.g. to decide whether the UI should offer "scan" or force
// "calibrate first".
// --------------------------------------------------------------------------------
// Returns 1 if profilePath exists, is a well-formed profile, and defines at least one
// block; 0 otherwise (including: file missing, unreadable, or empty).
BS_API int bs_profile_status(const char* profilePath);

// --------------------------------------------------------------------------------
// bs_free_string(ptr) — release a string returned by bs_calibrate or bs_run.
// --------------------------------------------------------------------------------
// Must be called exactly once per returned pointer. Safe to call with the exact
// pointer bs_calibrate/bs_run returned; never call it twice on the same pointer or
// pass it a pointer that didn't come from one of those two functions.
BS_API void bs_free_string(const char* ptr);
