"""
tests/manual_smoke_test.py
-----------------------------
NOT a pytest test — this is not auto-discovered/run by `pytest`, and
deliberately so: it needs a real compiled ai_corrector.{so,dll,dylib},
a real bubble.onnx, and a real sample scan image, none of which belong
in the automated suite (see tests/README.md for why).

Run this ONCE, manually, after building cv_engine for desktop and
before pointing real phones at a live session — it's the fastest way
to confirm the actual C++ ABI matches what cv_engine_bridge.py assumes
(pointer ownership via free_string, JSON field names, error handling)
using the real binary instead of the faked-out ctypes.CDLL the
automated tests use.

Usage:
    python -m admin_dashboard.tests.manual_smoke_test <path_to_sample_scan.jpg>

If no image path is given, it falls back to cv_engine_bin/warmup_sample.jpg
(fine for confirming the binary loads and returns *something* well-formed,
but that image has no real bubbles on it — use an actual scanned exam
photo to confirm accuracy, not just plumbing).
"""
import json
import sys

from admin_dashboard.cv_engine_bridge import (
    CVEngineBridge, CVEngineError, default_lib_path, default_model_path, default_warmup_image_path,
)


def main():
    image_path = sys.argv[1] if len(sys.argv) > 1 else default_warmup_image_path()

    print(f"Loading engine:")
    print(f"  lib:   {default_lib_path()}")
    print(f"  model: {default_model_path()}")
    print(f"  image: {image_path}")
    print()

    try:
        bridge = CVEngineBridge(
            lib_path=default_lib_path(),
            model_path=default_model_path(),
            warmup_image_path=None,  # skip — we're about to call process_exam directly anyway
        )
    except CVEngineError as e:
        print(f"FAILED TO LOAD ENGINE: {e}")
        print("Check that cv_engine_bin/ contains the compiled binary and bubble.onnx")
        print("for THIS platform (Linux/.so, Windows/.dll, macOS/.dylib).")
        sys.exit(1)

    print("Engine loaded successfully.")
    print()

    # A generic 5-question config — adjust num_questions/mcq_columns to
    # match whatever your sample image actually is if you want a
    # meaningful (not just non-crashing) result.
    config_kwargs = dict(
        num_questions=15, num_choices=4,
        mcq_columns={"num_cols": 3, "columns": {"1": 5, "2": 5, "3": 5}},
        id_digits=3, id_letters=6, id_letter_values=["C", "D", "E", "F", "M", "W"],
    )

    try:
        result = bridge.process_exam(image_path, **config_kwargs)
    except CVEngineError as e:
        print(f"process_exam() RAISED: {e}")
        print("If this is a real scan and you expected success, check:")
        print("  - config_kwargs above actually matches this image's layout")
        print("  - the image path is readable by the engine (absolute path, valid file)")
        sys.exit(1)

    print("process_exam() returned:")
    print(json.dumps(result, indent=2))
    print()

    # Sanity checks on the shape cv_engine_bridge.py / websocket_server.py
    # actually depend on — these are the fields that matter downstream.
    checks = [
        ("confidence" in result, "result has a 'confidence' field"),
        ("questions" in result, "result has a 'questions' field"),
        (isinstance(result.get("questions"), list), "'questions' is a list"),
    ]
    if result.get("questions"):
        q0 = result["questions"][0]
        checks.append(("question_number" in q0 and "state" in q0, "question entries have question_number/state"))

    print("Shape checks:")
    all_ok = True
    for ok, label in checks:
        print(f"  [{'OK' if ok else 'FAIL'}] {label}")
        all_ok = all_ok and ok

    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
