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
  "confidence": "OK",
  "warnings": [],
  "student_id": "E038",
  "student_id_letter": "E",
  "id_needs_review": false,
  "has_missing_rows": false,
  "id_columns": [
    {
      "column_label": "LETTER",
      "state": "ANSWERED",
      "answer": "E"
    }
  ],
  "questions": [
    {
      "question_number": 1,
      "state": "MULTIPLE",
      "answer": ["A", "D"]
    },
    {
      "question_number": 16,
      "state": "ERROR_MISSING",
      "answer": null
    }
  ]
}
```

### Early Exit / Failed Camera Frame Example:
If the C++ engine detects an unreadable photo, it will instantly abort the heavy AI inference and return a short-circuited JSON.

```json
{
  "status": "SUCCESS",
  "confidence": "LOW",
  "warnings": ["BLUR", "NO_ID_PANEL"],
  "id_columns": [],
  "questions": [],
  "has_missing_rows": false
}
```

### Important Flags
* `confidence`: "OK" or "LOW". If "LOW", you must prompt the user to review the `warnings` and retake the photo. The `questions` array will be empty because AI inference was safely skipped to save battery.
* `id_needs_review`: (Boolean) True if any column in the ID panel is blank or has multiple bubbles filled. The UI should highlight the student ID field for the teacher to manually verify.
* `has_missing_rows`: (Boolean) True if the number of bubbles detected in a column doesn't match the expected rows from `config.json`. This usually means the paper is cut off or the config is wrong.

### Understanding States
Every item in `questions` and `id_columns` has a `state` field. You should use this in Flutter to color-code UI feedback for the teacher:
* `"ANSWERED"`: The student successfully bubbled exactly one choice. The `answer` is a String.
* `"MULTIPLE"`: The student bubbled more than one choice. The `answer` is an Array of Strings.
* `"BLANK"`: No choices were bubbled, or they were too faint to be read. The `answer` is `null`.
* `"ERROR_MISSING"`: The expected row of bubbles was completely missing from the physical paper.

---

## 3. Warnings & UI Messages

The Flutter frontend should check the `warnings` array. If warnings exist, display a dialog to the user prompting them to retake the photo.

### Camera & Quality Issues (Causes Early Exit)
* **`BLUR`**: "The photo is too blurry. Please hold the camera steady and try again."
* **`ID_PANEL_BLUR`**: "The Student ID panel is blurry. Please hold the camera steady and try again."
* **`MCQ_PANEL_BLUR`**: "The answers panel is blurry. Please hold the camera steady and try again."
* **`NO_ID_PANEL`**: "Could not find the Student ID panel. Make sure the entire exam paper is visible in the frame."
* **`NO_MCQ_PANEL`**: "Could not find the answers panel. Make sure the entire exam paper is visible in the frame."
* **`ID_PANEL_GEOMETRY_INVALID`**: "The Student ID panel appears distorted or cut off. Make sure the paper is flat and fully visible."
* **`MCQ_PANEL_GEOMETRY_INVALID`**: "The answers panel appears distorted or cut off. Make sure the paper is flat and fully visible."
* **`EXPOSURE_TOO_HIGH`**: "The photo is too bright or washed out, making the pencil marks invisible. Please avoid harsh glare or use a darker pencil."
