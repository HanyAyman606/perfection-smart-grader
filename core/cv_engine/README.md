# CV Engine (AI Corrector)

This is the core C++ OpenCV and ONNX Runtime backend for the Smart Grader application. It is designed to be highly performant, robust to noisy inputs, and easily integrable into mobile/frontend applications via FFI (Foreign Function Interface).

## Directory Structure

This module follows a standard C++ library best-practice hierarchy:

```
cv_engine/
├── include/           # Public headers containing structures and FFI API
├── src/               # Private C++ implementation files
├── models/            # Pre-trained ONNX models for inference
├── CMakeLists.txt     # Build configuration
└── README.md          # This documentation
```

## Features

- **Advanced Preprocessing**: Uses OpenCV to automatically detect the edges of an exam paper, apply perspective warping to perfectly flatten it, and crop out the student ID and MCQ panels using intelligent anchor detection.
- **Adaptive ONNX Inference**: Replaces traditional pixel-thresholding with an intelligent AI model (`bubble.onnx`) that detects filled/empty/canceled bubbles under vastly different lighting conditions and pen types.
- **Dynamic Layout Grouping**: Mathematical algorithms dynamically group uneven columns (e.g. 14 questions across 3 columns = `[5, 5, 4]`) and assemble them into a structured JSON response.
- **Two-Step FFI Architecture**: Explicitly designed for seamless integration with interactive UI frameworks like Flutter.

## FFI Integration (Flutter/Dart)

The library exposes two `extern "C"` functions in `include/ffi.h` that accept and return raw `const char*` memory pointers (JSON strings).

### Step 1: Extract Panels
```cpp
const char* step1_extract_panels(const char* image_path, const char* config_json_str);
```
Pass the raw camera image path and the exam configuration. The library will return a JSON string with the absolute paths to the cropped ID and MCQ panels. The frontend should display these to the user for validation.

### Step 2: Infer and Score
```cpp
const char* step2_infer_and_score(const char* id_panel_path, const char* mcq_panel_path, const char* config_json_str);
```
Pass the paths to the extracted panels from Step 1. The library will run the ONNX AI inference, draw colored bounding boxes on the images, and return a comprehensive JSON response containing:
1. The paths to the newly annotated images.
2. The parsed `student_id` (along with isolated `student_id_letter`).
3. An array of `questions` detailing the state of each (`ANSWERED`, `BLANK`, `MULTIPLE`).

*Note: After reading the returned JSON strings in your frontend, you MUST call `free_string(str)` via FFI to prevent memory leaks!*

## Building from Source (Local Testing)

You can compile this library as a `.dll` (Windows), `.so` (Linux), or `.dylib` (macOS).

**Dependencies:**
- CMake (3.10+)
- OpenCV (4.x)
- ONNX Runtime C++ API
- Nlohmann JSON (Header-only)

**Build Instructions (MSYS2 / Windows):**
```bash
mkdir build && cd build
cmake .. -G Ninja
ninja
```

This will produce `libai_corrector.dll` and a CLI tester `ai_corrector_cli.exe`.

**Run CLI Tester:**
```bash
./ai_corrector_cli.exe "path/to/test.jpeg" "path/to/config.json"
```
