"""
cv_engine_bridge.py
---------------------
ctypes wrapper around the compiled cv_engine shared library (the same
process_exam_in_memory/free_string FFI the Dart client used to call over
dart:ffi — see cv_engine/include/ffi.h). One instance is created once,
when a live session starts, and shared read-only across every worker
thread in the WebSocketServer's ThreadPoolExecutor.

Thread-safety note: inference.cpp caches its Ort::Session in a plain
std::unordered_map<std::string, Session*> with no lock guarding the
insertion. Session::Run() is safe to call concurrently once a session
already exists in that map, but the *first* call that creates/inserts it
is not safe to race. warmup() forces that insertion single-threaded,
before the server ever accepts a phone connection, so every later call
from the executor's threads is just a lookup + Run(), which is safe.

ctypes note on process_exam_in_memory's return value: the C side hands
back a heap-allocated char* that must be freed with free_string(). If
restype were set to plain ctypes.c_char_p, ctypes would silently copy the
bytes into a Python bytes object and hand back a NEW pointer — the
original C pointer would be lost and free_string() would never run,
leaking memory on every single scan over a multi-hour session. To avoid
that, restype is POINTER(c_char): we decode manually, then free the
exact pointer the C side gave us.
"""
import ctypes
import json
import os
import platform


class CVEngineError(RuntimeError):
    pass


class CVEngineBridge:
    def __init__(self, lib_path: str, model_path: str, warmup_image_path: str | None = None):
        if not os.path.exists(lib_path):
            raise CVEngineError(f"cv_engine shared library not found at: {lib_path}")
        if not os.path.exists(model_path):
            raise CVEngineError(f"bubble.onnx model not found at: {model_path}")

        self.lib = ctypes.CDLL(lib_path)
        self.lib.process_exam_in_memory.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
        self.lib.process_exam_in_memory.restype = ctypes.POINTER(ctypes.c_char)
        self.lib.free_string.argtypes = [ctypes.POINTER(ctypes.c_char)]
        self.lib.free_string.restype = None

        self.model_path = model_path

        if warmup_image_path and os.path.exists(warmup_image_path):
            self._warmup(warmup_image_path)

    def _warmup(self, warmup_image_path: str):
        """Forces Ort::Session construction on this thread, before the
        server starts accepting phones, so the executor pool never races
        on session creation. Config values here are throwaway — only the
        model_path matters, since that's what determines which session
        gets created/cached on the C++ side."""
        try:
            self.process_exam(
                warmup_image_path,
                num_questions=1, num_choices=4,
                mcq_columns={"num_cols": 1, "columns": {"1": 1}},
                id_digits=1, id_letters=0, id_letter_values=[],
            )
        except CVEngineError:
            # A bad warmup image shouldn't block server startup — it just
            # means the *first real* scan pays the session-creation cost
            # instead of paying it now. Log-worthy, not fatal.
            pass

    def process_exam(self, image_path: str, *, num_questions: int, num_choices: int,
                      mcq_columns: dict, id_digits: int, id_letters: int,
                      id_letter_values: list, row_tolerance_px: int = 15) -> dict:
        """Blocking, CPU-bound call — always invoke this via
        loop.run_in_executor(pool, ...) from async code, never awaited
        directly, or it stalls every connected phone for the duration of
        one scan."""
        config = {
            "model_path": self.model_path,
            "num_questions": num_questions,
            "num_choices": num_choices,
            "mcq_columns": mcq_columns,
            "row_tolerance_px": row_tolerance_px,
            "id": {
                "num_digits": id_digits,
                "num_letters": id_letters,
                "letters": id_letter_values,
            },
        }
        config_bytes = json.dumps(config).encode("utf-8")
        image_bytes = image_path.encode("utf-8")

        result_ptr = self.lib.process_exam_in_memory(image_bytes, config_bytes)
        if not result_ptr:
            raise CVEngineError("process_exam_in_memory returned NULL — check image path/config JSON.")

        try:
            raw = ctypes.string_at(result_ptr)  # copies bytes out before we free the original pointer
            result = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise CVEngineError(f"Malformed response from cv_engine: {e}")
        finally:
            self.lib.free_string(result_ptr)

        if result.get("status") == "ERROR":
            raise CVEngineError(result.get("message", "cv_engine reported an unspecified error."))

        return result

    def config_kwargs_from_master_packet(self, master_packet: dict) -> dict:
        """Pulls the process_exam() kwargs out of the same master_packet
        shape ProjectManager.build_sync_packet() already produces, so the
        caller doesn't need to know the engine's config field names."""
        id_cfg = master_packet.get("id", {})
        return dict(
            num_questions=master_packet.get("mcq_count", 0),
            num_choices=master_packet.get("choices_per_question", 4),
            mcq_columns=master_packet.get("mcq_columns", {"num_cols": 1, "columns": {}}),
            id_digits=id_cfg.get("num_digits", 0),
            id_letters=id_cfg.get("num_letters", 0),
            id_letter_values=id_cfg.get("letters", []),
        )


def default_lib_path() -> str:
    # CMakeLists.txt's target is named "ai_corrector" (not "cv_engine") —
    # confirmed against CMakeLists.txt's add_library(ai_corrector SHARED ...).
    # These are the real filenames the desktop build produces.
    system = platform.system()
    name = {"Windows": "ai_corrector.dll", "Darwin": "libai_corrector.dylib"}.get(system, "libai_corrector.so")
    return os.path.join(os.path.dirname(__file__), "cv_engine_bin", name)


def default_model_path() -> str:
    return os.path.join(os.path.dirname(__file__), "cv_engine_bin", "bubble.onnx")


def default_warmup_image_path() -> str:
    # A fixed app asset (any legible sample bubble sheet works — its
    # content doesn't matter, only that process_exam() runs once on it
    # single-threaded before the executor pool starts accepting phones).
    # Ships alongside the engine binary and model, not per-workspace.
    return os.path.join(os.path.dirname(__file__), "cv_engine_bin", "warmup_sample.jpg")
