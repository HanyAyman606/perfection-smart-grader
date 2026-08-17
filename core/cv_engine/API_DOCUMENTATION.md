# CV Engine API Documentation

This document outlines the JSON schemas required to communicate between the Flutter frontend and the C++ `cv_engine` backend via FFI.

---

## 1. Configuration Payload (Input)

Whenever you call an FFI function, you must provide the configuration JSON as a raw `String`. This tells the AI how to interpret the physical exam sheet. You can also pass a `tuning` block to dynamically tweak image processing thresholds on the fly.

```dart
  /// Builds the JSON string passed as `config_json_str` into the
  /// process_exam_in_memory C++ FFI endpoint.
  String toExamConfigJson({required String modelPath, int rowTolerancePx = 15}) {
    return jsonEncode({
      'model_path': modelPath,
      'num_questions': mcqCount,
      'num_choices': choicesPerQuestion,
      'row_tolerance_px': rowTolerancePx,
      'mcq_columns': mcqColumns.toJson(),
      'id': {
        'num_digits': idNumDigits,
        'num_letters': idNumLetters,
        'letters': idLetters,
      },
      // Optional tuning block to test thresholds without rebuilding C++
      'tuning': {
        'blur': 15.0,
        'exposure': 0.02
      }
    });
  }
```

### Field Definitions
* `model_path`: (String) The absolute path to the `.onnx` AI model on the device.
* `num_questions`: (Integer) Total questions on this specific exam.
* `num_choices`: (Integer) Number of choices per question (e.g. 4 means A, B, C, D).
* `mcq_columns`: (Object) Defines the exact layout of the MCQ panel.
* `row_tolerance_px`: (Integer) Pixel variance allowed to group bubbles into a single horizontal row. (Default: 15).
* `tuning`: (Object) Optional override block for engine thresholds. If a field is omitted, the engine uses these hardcoded defaults:
  * `blur`: (Double) Threshold for Laplacian variance. (Default: `15.0`)
  * `exposure`: (Double) Minimum ratio of dark pixels required on a panel to not be considered washed out. (Default: `0.02`)
  * `min_panel_area_ratio`: (Double) Minimum area a panel quad must take up relative to the whole image. (Default: `0.005`)
  * `max_panel_area_ratio`: (Double) Maximum area a panel quad can take up relative to the whole image. (Default: `0.45`)
  * `max_quad_side_ratio`: (Double) Maximum allowed ratio between opposite sides of a detected panel to ensure it isn't wildly distorted. (Default: `2.2`)
  * `min_quad_angle_deg`: (Double) Minimum allowed interior angle for a detected panel corner. (Default: `35.0`)
  * `max_quad_angle_deg`: (Double) Maximum allowed interior angle for a detected panel corner. (Default: `145.0`)

---

## 2. Process Exam (Output)

**FFI Signature:** 
`Pointer<Utf8> process_exam_in_memory(Pointer<Utf8> image_path, Pointer<Utf8> config_json_str);`

This is a blazing fast, single-step function that straightens the image, runs AI inference, and generates a score report completely in RAM without ever saving intermediate images to disk.

### Success / Parsed Payload Example:
```json
{
  "status": "SUCCESS",
  "cause": null,
  "student_id": "E038",
  "student_id_letter": "E",
  "id_needs_review": false,
  "has_missing_rows": false,
  "id_columns": [
    {
      "column_label": "LETTER",
      "state": "ANSWERED",
      "answer": "E",
      "answer_confidence": 0.94
    }
  ],
  "questions": [
    {
      "question_number": 1,
      "state": "MULTIPLE",
      "answer": ["A", "D"],
      "answer_confidence": 0.88
    }
  ]
}
```

### Review Needed Payload Example:
```json
{
  "status": "REVIEW_NEEDED",
  "cause": [
    {
      "question_number": 4,
      "state": "ANSWERED",
      "answer": "B",
      "answer_confidence": 0.45
    }
  ],
  "student_id": "E038",
  "id_columns": [],
  "questions": [...]
}
```

### Early Exit / Failed Camera Frame Example:
If the C++ engine detects an unreadable photo or fails during inference, it will instantly abort and return a short-circuited JSON. The `cause` field will contain the specific error string.

```json
{
  "status": "FAILED",
  "cause": "BLUR",
  "id_columns": [],
  "questions": [],
  "has_missing_rows": false
}
```

### Important Flags
* `status`: 
  * `"SUCCESS"`: Clean run.
  * `"REVIEW_NEEDED"`: Run succeeded, but 1-3 questions had low confidence. Teacher should manually verify them.
  * `"RETAKE"`: Run succeeded, but >3 questions had low confidence. Too many errors to safely correct, force a retake.
  * `"QUESTIONS_NUMBER_MISMATCH"`: The number of questions detected physically on the page does not match `config.json`.
  * `"FAILED"`: Fatal pipeline error (bad camera, internal error).
* `cause`: 
  * `null` if `status == "SUCCESS"`.
  * Array of offending questions/ID columns if `status == "REVIEW_NEEDED"` or `"QUESTIONS_NUMBER_MISMATCH"`.
  * Specific string (e.g., `"BLUR"`, `"NO_ID_PANEL"`, `"EXPOSURE_TOO_HIGH"`) if `status == "FAILED"`.
* `id_needs_review`: (Boolean) True if any column in the ID panel is blank or has multiple bubbles filled.
* `has_missing_rows`: (Boolean) True if a row was completely missing.

### Understanding States
Every item in `questions` and `id_columns` has a `state` field:
* `"ANSWERED"`: Exactly one choice bubbled. `answer` is a String.
* `"MULTIPLE"`: More than one choice bubbled. `answer` is an Array of Strings.
* `"BLANK"`: No choices bubbled. `answer` is `null`.
* `"QUESTIONS_NUMBER_MISMATCH"`: Row was completely missing, or an extra row was detected beyond the configured limit.

---

## 3. Failure Causes

The Flutter frontend should check the `status` and `cause`. If the status is `FAILED`, `cause` will be a string denoting the camera/quality issue.

### Camera & Quality Issues (status = "FAILED")
* **`BLUR`**: The photo is too blurry.
* **`ID_PANEL_BLUR`**: The Student ID panel is blurry.
* **`MCQ_PANEL_BLUR`**: The answers panel is blurry.
* **`NO_ID_PANEL`**: Could not find the Student ID panel.
* **`NO_MCQ_PANEL`**: Could not find the answers panel.
* **`ID_PANEL_GEOMETRY_INVALID`**: The Student ID panel appears distorted or cut off.
* **`MCQ_PANEL_GEOMETRY_INVALID`**: The answers panel appears distorted or cut off.
* **`EXPOSURE_TOO_HIGH`**: The photo is too bright or washed out.
* **`ERROR_INTERNAL`**: Internal engine exception.
