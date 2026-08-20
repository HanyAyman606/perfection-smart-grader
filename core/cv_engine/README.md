# Exam Scanner Pipeline — C++ Port

A line-by-line C++ port of the Python bubble-sheet scanning/grading pipeline
(`stage1_boxes.py` .. `stage6_scoring.py`, `run_pipeline.py`, `exam_config.py`).
Behavior — thresholds, geometry rules, fallback logic, the exam config
schema/validation — is kept identical to the Python originals. Comments in
each `.cpp` point back to the Python function they mirror.

## Layout

```
include/
  exam_config.hpp   # port of exam_config.py (ExamConfig, load_exam_config, errors)
  quad_io.hpp        # internal quad interchange format (replaces numpy .npy)
  common.hpp          # small fs/glob/string helpers used by every stage
src/
  stage1_boxes.cpp      # port of stage1_boxes.py
  stage2_warp.cpp        # port of stage2_warp.py
  stage3_orient.cpp      # port of stage3_orient.py
  stage4_final.cpp       # port of stage4_final.py
  stage5_inference.cpp   # port of stage5_inference.py (ONNX Runtime C++ API)
  stage6_scoring.cpp     # port of stage6_scoring.py
  run_pipeline.cpp       # port of run_pipeline.py (runs all stages in order)
CMakeLists.txt
setup_deps.sh
```

## One difference from the Python version (by necessity)

Stage 1 hands its detected panel quads (4 corners) to Stage 2 via a file on
disk. The Python version used `numpy.save(...)`, which is numpy-specific.
The C++ version writes the same 4 `(x, y)` points as raw `float64` pairs to
a small `.bin` file (`quad_io.hpp`) — same information, no numpy dependency.
Nothing about the detection/warping *logic* changed, only this internal
file format between two stages of the same pipeline.

Everything else — thresholds, contour/geometry logic, the shadow-removal
math, the letterbox/NMS/decode logic in Stage 5, the row/column
anchoring and scoring rules in Stage 6, and the `exam_config.json` schema —
matches the Python source exactly.

## Build

```bash
./setup_deps.sh                                   # fetches ONNX Runtime, installs opencv/json via apt
cmake -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j$(nproc)
```

This produces 7 executables in `build/`: `stage1_boxes`, `stage2_warp`,
`stage3_orient`, `stage4_final`, `stage5_inference`, `stage6_scoring`, and
`run_pipeline`.

If you already have ONNX Runtime installed elsewhere, point CMake at it
instead of the bundled `third_party/` copy:

```bash
cmake -B build -DONNXRUNTIME_ROOT=/path/to/onnxruntime-linux-x64-1.18.0
```

## Run

Each stage binary and `run_pipeline` take one optional argument: the exam
working directory (defaults to the current directory). That directory
should contain:

```
<work_dir>/
  input/              # raw photos (.jpg/.jpeg/.png) -- you provide these
  shamel.onnx         # the trained detector (same file as the Python version used)
  exam_config.json    # same payload/schema the Flutter app already sends
```

Then:

```bash
export LD_LIBRARY_PATH=/path/to/onnxruntime-linux-x64-1.18.0/lib:$LD_LIBRARY_PATH
./build/run_pipeline /path/to/exam_working_dir
```

This runs stage1 → stage2 → stage3 → stage4 → stage5 → stage6 in order,
exactly like `run_pipeline.py`, writing `stage1/` .. `stage6/` folders and
finally `stage6/final_grades.json`. You can also re-run any single stage
binary directly while iterating, same as the Python originals.

## `exam_config.json` / `exam_config.hpp`

Same required/optional fields, same validation errors, same derived
`mcq_column_counts` ceil-division rule as `exam_config.py`:

```cpp
#include "exam_config.hpp"
auto cfg = examcfg::load_exam_config_from_file("exam_config.json");
// cfg.exam_id, cfg.num_questions, cfg.mcq_column_counts(), ...
```

`examcfg::ExamConfigError` mirrors Python's `ExamConfigError`;
`examcfg::NotImplementedExamType` mirrors the `NotImplementedError` raised
for a recognized-but-unbuilt `exam_type` (currently `"shamel"`).

## Notes / things worth knowing before deploying

- **Stage 5 (ONNX inference)** needs `libonnxruntime.so` at runtime — set
  `LD_LIBRARY_PATH` (see above), or `sudo cp` it into `/usr/local/lib` and
  run `ldconfig`.
- All stages were verified to **compile and run end-to-end** against a
  synthetic test image and a synthetic Stage-5 detections file, matching
  the Python logic's branch behavior (single-panel detection, blank-row
  handling, `mcq_column_counts` math, `NotImplementedExamType`, etc.). They
  have **not** been validated against real scanned exam photos — do that
  comparison (same photos through both pipelines) before relying on this
  in production, since OpenCV's C++ and Python bindings can occasionally
  differ in edge-case rounding.
