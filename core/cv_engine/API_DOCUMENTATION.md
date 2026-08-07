# CV Engine API Documentation

This document outlines the JSON schemas required to communicate between the Flutter frontend and the C++ `cv_engine` backend via FFI.

---

## 1. Configuration Payload (Input)

Whenever you call an FFI function, you must provide the configuration JSON as a raw `String`. This tells the AI how to interpret the physical exam sheet.

```json
{
  "model_path": "path/to/bubble.onnx",
  "num_questions": 15,
  "num_choices": 4,
  "num_question_columns": 3,
  "row_tolerance_px": 15,
  "id": {
    "num_digits": 3,
    "num_letters": 1,
    "letters": [
      "A", "B", "C", "D", "E", "F"
    ]
  }
}
```

### Field Definitions
* `model_path`: (String) The absolute path to the `.onnx` AI model on the device.
* `num_questions`: (Integer) Total questions on this specific exam.
* `num_choices`: (Integer) Number of choices per question (e.g. 4 means A, B, C, D).
* `num_question_columns`: (Integer) The number of vertical columns the questions are split into on the physical paper.
* `row_tolerance_px`: (Integer) Pixel variance allowed to group bubbles into a single horizontal row. (Default: 15).
* `id.num_digits`: (Integer) Number of columns dedicated to student ID numbers.
* `id.num_letters`: (Integer) Number of columns dedicated to student ID letters.
* `id.letters`: (Array of Strings) The exact ordered letters printed in the ID letter column.

---

## 2. Step 1: Extract Panels (Output)

**FFI Signature:** 
`Pointer<Utf8> step1_extract_panels(Pointer<Utf8> image_path, Pointer<Utf8> config_json_str);`

This step straightens the image and crops the MCQ and ID panels. It returns the absolute paths to these temporary cropped images so Flutter can display them.

```json
{
  "status": "SUCCESS",
  "id_panel_path": "/absolute/path/to/image_cropped_id.jpg",
  "mcq_panel_path": "/absolute/path/to/image_cropped_mcq.jpg"
}
```

*(Note: If a panel could not be found, its path key will be omitted from the JSON).*

---

## 3. Step 2: Infer & Score (Output)

**FFI Signature:** 
`Pointer<Utf8> step2_infer_and_score(Pointer<Utf8> id_panel_path, Pointer<Utf8> mcq_panel_path, Pointer<Utf8> config_json_str);`

This step runs the AI on the cropped panels. It draws bounding boxes on the images and returns a comprehensive JSON result containing the extracted answers and the paths to the newly annotated images.

```json
{
  "annotated_id_image_path": "/absolute/path/to/image_cropped_id.jpg_annotated.jpg",
  "annotated_mcq_image_path": "/absolute/path/to/image_cropped_mcq.jpg_annotated.jpg",
  "student_id": "E038",
  "student_id_letter": "E",
  "id_columns": [
    {
      "column_label": "LETTER",
      "state": "ANSWERED",
      "answer": "E"
    },
    {
      "column_label": "DIGIT_1",
      "state": "ANSWERED",
      "answer": "0"
    },
    {
      "column_label": "DIGIT_2",
      "state": "ANSWERED",
      "answer": "3"
    }
  ],
  "questions": [
    {
      "question_number": 1,
      "state": "ANSWERED",
      "answer": "A"
    },
    {
      "question_number": 2,
      "state": "MULTIPLE",
      "answer": ["A", "D"]
    },
    {
      "question_number": 3,
      "state": "BLANK",
      "answer": null
    }
  ]
}
```

### Understanding States
Every item in `questions` and `id_columns` has a `state` field. You should use this in Flutter to color-code UI feedback for the teacher:
* `"ANSWERED"`: The student successfully bubbled exactly one choice. The `answer` is a String.
* `"MULTIPLE"`: The student bubbled more than one choice. The `answer` is an Array of Strings.
* `"BLANK"`: No choices were bubbled, or all choices were mathematically canceled out. The `answer` is `null`.
