# CV Engine API Documentation

This document outlines the JSON schemas required to communicate between the Flutter frontend and the C++ `cv_engine` backend via FFI.

---

## 1. Configuration Payload (Input)

Whenever you call an FFI function, you must provide the configuration JSON as a raw `String`. This tells the AI how to interpret the physical exam sheet.

```dart
  /// Builds the JSON string passed as `config_json_str` into both
  /// step1_extract_panels and step2_infer_and_score. This is the
  /// translation layer between the dashboard's wire format and the C++
  /// engine's ExamConfig::from_json (see config.h) — field names and
  /// shapes intentionally differ from the dashboard packet above because
  /// each side owns its own contract; this is the one place they meet.
  String toExamConfigJson({required String modelPath, int rowTolerancePx = 15}) {
    return jsonEncode({
      'model_path': modelPath,
      'num_questions': mcqCount,
      'num_choices': choicesPerQuestion,
      'row_tolerance_px': rowTolerancePx,
      // Authoritative shape — matches config.h's preferred parse path
      // exactly, so num_question_columns AND mcq_column_sizes are both
      // derived from the real printed layout, never re-guessed.
      'mcq_columns': mcqColumns.toJson(),
      'id': {
        'num_digits': idNumDigits,
        'num_letters': idNumLetters,
        'letters': idLetters,
      },
    });
  }
```

### Field Definitions
* `model_path`: (String) The absolute path to the `.onnx` AI model on the device.
* `num_questions`: (Integer) Total questions on this specific exam.
* `num_choices`: (Integer) Number of choices per question (e.g. 4 means A, B, C, D).
* `mcq_columns`: (Object) Defines the exact layout of the MCQ panel.
  * `num_cols`: (Integer) The number of vertical columns the questions are split into.
  * `columns`: (Map of String to Integer) Key is the 1-based column index, value is the number of questions in that column.
* `row_tolerance_px`: (Integer) Pixel variance allowed to group bubbles into a single horizontal row. (Default: 15).
* `id.num_digits`: (Integer) Number of columns dedicated to student ID numbers.
* `id.num_letters`: (Integer) Number of letters in the pool (e.g. 6 for A-F). Note: The engine statically assumes there is exactly 1 letter column on the paper.
* `id.letters`: (Array of Strings) The exact ordered letters printed in the ID letter pool.

---

## 2. Step 1: Extract Panels (Output)

**FFI Signature:** 
`Pointer<Utf8> step1_extract_panels(Pointer<Utf8> image_path, Pointer<Utf8> config_json_str);`

This step straightens the image, crops the MCQ and ID panels, and checks for image quality (blur, exposure, and missing panels).

```json
{
  "status": "SUCCESS",
  "confidence": "LOW",
  "warnings": ["NO_ID_PANEL", "EXPOSURE_TOO_HIGH"],
  "id_panel_path": "/absolute/path/to/image_cropped_id.jpg",
  "mcq_panel_path": "/absolute/path/to/image_cropped_mcq.jpg"
}
```

* `status`: "SUCCESS" or "ERROR". (ERROR means a fatal crash or malformed JSON).
* `confidence`: "OK" or "LOW". If "LOW", the user should be prompted to review the warnings and retake the photo.
* `warnings`: An array of string codes indicating specific problems with the photo (see the Warnings section below).

---

## 3. Step 2: Infer & Score (Output)

**FFI Signature:** 
`Pointer<Utf8> step2_infer_and_score(Pointer<Utf8> id_panel_path, Pointer<Utf8> mcq_panel_path, Pointer<Utf8> config_json_str);`

This step runs the AI on the cropped panels. It draws bounding boxes on the images and returns a comprehensive JSON result containing the extracted answers.

```json
{
  "annotated_id_image_path": "/absolute/path/to/image_cropped_id.jpg_annotated.jpg",
  "annotated_mcq_image_path": "/absolute/path/to/image_cropped_mcq.jpg_annotated.jpg",
  "student_id": "E038",
  "student_id_letter": "E",
  "id_needs_review": false,
  "has_missing_rows": false,
  "warnings": [],
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

### Important Flags
* `id_needs_review`: (Boolean) True if any column in the ID panel is blank or has multiple bubbles filled. The UI should highlight the student ID field for the teacher to manually verify.
* `has_missing_rows`: (Boolean) True if the number of bubbles detected in a column doesn't match the expected rows from `config.json`. This usually means the paper is cut off or the config is wrong.

### Understanding States
Every item in `questions` and `id_columns` has a `state` field. You should use this in Flutter to color-code UI feedback for the teacher:
* `"ANSWERED"`: The student successfully bubbled exactly one choice. The `answer` is a String.
* `"MULTIPLE"`: The student bubbled more than one choice. The `answer` is an Array of Strings.
* `"BLANK"`: No choices were bubbled, or they were too faint to be read. The `answer` is `null`.
* `"ERROR_MISSING"`: The expected row of bubbles was completely missing from the physical paper (e.g. paper was cut off, or config expects 16 questions but paper only has 15).

---

## 4. Warnings & UI Messages

The Flutter frontend should check the `warnings` array in both Step 1 and Step 2. If warnings exist, display a dialog to the user prompting them to retake the photo.

Here are all possible warnings and the recommended UI messages to show the user:

### Step 1 Warnings (Camera & Quality Issues)
* **`BLUR`**: "The photo is too blurry. Please hold the camera steady and try again."
* **`ID_PANEL_BLUR`**: "The Student ID panel is blurry. Please hold the camera steady and try again."
* **`MCQ_PANEL_BLUR`**: "The answers panel is blurry. Please hold the camera steady and try again."
* **`NO_ID_PANEL`**: "Could not find the Student ID panel. Make sure the entire exam paper is visible in the frame."
* **`NO_MCQ_PANEL`**: "Could not find the answers panel. Make sure the entire exam paper is visible in the frame."
* **`ID_PANEL_GEOMETRY_INVALID`**: "The Student ID panel appears distorted or cut off. Make sure the paper is flat and fully visible."
* **`MCQ_PANEL_GEOMETRY_INVALID`**: "The answers panel appears distorted or cut off. Make sure the paper is flat and fully visible."
* **`EXPOSURE_TOO_HIGH`**: "The photo is too bright or washed out, making the pencil marks invisible. Please avoid harsh glare or use a darker pencil."
* **`ORIENTATION_LOW_CONFIDENCE`**: "Could not confidently determine the orientation of the paper. Please review the results carefully." *(Note: This does not force confidence="LOW", but is good to surface as a hint).*

### Step 2 Warnings (Data Mismatch Issues)
* **`MISSING_EXPECTED_BUBBLES`**: "Some bubbles are missing from the page. Either the bottom of the page was cut off in the photo, or the exam template does not match the configured number of questions."
