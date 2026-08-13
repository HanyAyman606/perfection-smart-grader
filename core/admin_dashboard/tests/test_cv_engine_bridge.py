"""
tests/test_cv_engine_bridge.py
---------------------------------
Tests CVEngineBridge's Python-side logic (config building, error
handling, pointer freeing) without needing a real compiled cv_engine
binary — ctypes.CDLL is monkeypatched with a fake that mimics the FFI
contract in ffi.h. This does NOT test the C++ side itself (that needs
the real .so once it's compiled for desktop — see test_manual_smoke.py
for a script to run once that exists), only that this wrapper calls it
correctly and doesn't leak/crash on the documented contract.
"""
import ctypes
import json
import os

import pytest

from admin_dashboard.cv_engine_bridge import CVEngineBridge, CVEngineError


class FakeCLib:
    """Mimics the subset of the compiled lib's ABI CVEngineBridge calls.
    Tracks free_string calls so we can assert every successful
    process_exam_in_memory call is paired with exactly one free."""

    def __init__(self, response: dict | None = None, return_null=False):
        self.response = response if response is not None else {"status": "OK", "questions": []}
        self.return_null = return_null
        self.free_calls = 0
        self.last_config = None
        self._buffers = []  # keep ctypes buffers alive for the duration of a call

        # Plain function objects, not bound methods — CVEngineBridge.__init__
        # sets .argtypes/.restype on these, and (unlike bound methods)
        # ordinary functions support arbitrary attribute assignment.
        def process_exam_in_memory(image_path_bytes, config_bytes):
            return self._process_exam_in_memory(image_path_bytes, config_bytes)

        def free_string(ptr):
            return self._free_string(ptr)

        self.process_exam_in_memory = process_exam_in_memory
        self.free_string = free_string

    def _process_exam_in_memory(self, image_path_bytes, config_bytes):
        self.last_config = json.loads(config_bytes.decode("utf-8"))
        if self.return_null:
            return None
        payload = json.dumps(self.response).encode("utf-8")
        buf = ctypes.create_string_buffer(payload)
        self._buffers.append(buf)  # simulates heap allocation the C side owns until free_string
        return ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))

    def _free_string(self, ptr):
        self.free_calls += 1


@pytest.fixture
def bridge_with_fake_lib(tmp_path, monkeypatch):
    lib_path = tmp_path / "fake_cv_engine.so"
    model_path = tmp_path / "bubble.onnx"
    lib_path.write_bytes(b"not a real lib, existence check only")
    model_path.write_bytes(b"not a real model, existence check only")

    fake_lib = FakeCLib()
    monkeypatch.setattr(ctypes, "CDLL", lambda path: fake_lib)

    bridge = CVEngineBridge(str(lib_path), str(model_path), warmup_image_path=None)
    return bridge, fake_lib


def test_missing_lib_file_raises_before_touching_ctypes(tmp_path):
    with pytest.raises(CVEngineError, match="shared library not found"):
        CVEngineBridge(str(tmp_path / "nope.so"), str(tmp_path / "nope.onnx"))


def test_missing_model_file_raises(tmp_path):
    lib_path = tmp_path / "cv.so"
    lib_path.write_bytes(b"x")
    with pytest.raises(CVEngineError, match="bubble.onnx model not found"):
        CVEngineBridge(str(lib_path), str(tmp_path / "nope.onnx"))


def test_process_exam_returns_parsed_json(bridge_with_fake_lib):
    bridge, fake_lib = bridge_with_fake_lib
    fake_lib.response = {"status": "OK", "student_id": "1234", "confidence": "HIGH",
                          "questions": [{"question_number": 1, "state": "ANSWERED", "answer": "A"}]}
    result = bridge.process_exam("/tmp/scan.jpg", num_questions=5, num_choices=4,
                                  mcq_columns={"num_cols": 1, "columns": {}},
                                  id_digits=4, id_letters=0, id_letter_values=[])
    assert result["student_id"] == "1234"
    assert result["questions"][0]["answer"] == "A"


def test_every_call_frees_its_pointer(bridge_with_fake_lib):
    bridge, fake_lib = bridge_with_fake_lib
    for _ in range(5):
        bridge.process_exam("/tmp/scan.jpg", num_questions=1, num_choices=4,
                             mcq_columns={"num_cols": 1, "columns": {}},
                             id_digits=1, id_letters=0, id_letter_values=[])
    assert fake_lib.free_calls == 5


def test_null_pointer_raises_cv_engine_error(bridge_with_fake_lib):
    bridge, fake_lib = bridge_with_fake_lib
    fake_lib.return_null = True
    with pytest.raises(CVEngineError, match="NULL"):
        bridge.process_exam("/tmp/scan.jpg", num_questions=1, num_choices=4,
                             mcq_columns={"num_cols": 1, "columns": {}},
                             id_digits=1, id_letters=0, id_letter_values=[])


def test_error_status_in_response_raises(bridge_with_fake_lib):
    bridge, fake_lib = bridge_with_fake_lib
    fake_lib.response = {"status": "ERROR", "message": "unreadable image"}
    with pytest.raises(CVEngineError, match="unreadable image"):
        bridge.process_exam("/tmp/scan.jpg", num_questions=1, num_choices=4,
                             mcq_columns={"num_cols": 1, "columns": {}},
                             id_digits=1, id_letters=0, id_letter_values=[])


def test_config_json_matches_documented_ffi_shape(bridge_with_fake_lib):
    bridge, fake_lib = bridge_with_fake_lib
    bridge.process_exam("/tmp/scan.jpg", num_questions=5, num_choices=4,
                         mcq_columns={"num_cols": 2, "columns": {"1": 3, "2": 2}},
                         id_digits=4, id_letters=2, id_letter_values=["C", "D"],
                         row_tolerance_px=20)
    cfg = fake_lib.last_config
    assert cfg["num_questions"] == 5
    assert cfg["mcq_columns"]["num_cols"] == 2
    assert cfg["id"]["num_digits"] == 4
    assert cfg["id"]["letters"] == ["C", "D"]
    assert cfg["row_tolerance_px"] == 20


def test_config_kwargs_from_master_packet_maps_fields_correctly(bridge_with_fake_lib, master_packet):
    bridge, _ = bridge_with_fake_lib
    kwargs = bridge.config_kwargs_from_master_packet(master_packet)
    assert kwargs["num_questions"] == master_packet["mcq_count"]
    assert kwargs["num_choices"] == master_packet["choices_per_question"]
    assert kwargs["id_digits"] == master_packet["id"]["num_digits"]
    assert kwargs["mcq_columns"] == master_packet["mcq_columns"]


def test_warmup_failure_does_not_raise(tmp_path, monkeypatch):
    lib_path = tmp_path / "cv.so"
    model_path = tmp_path / "bubble.onnx"
    warmup_path = tmp_path / "warmup.jpg"
    lib_path.write_bytes(b"x")
    model_path.write_bytes(b"x")
    warmup_path.write_bytes(b"x")

    fake_lib = FakeCLib(return_null=True)  # warmup call "fails" internally
    monkeypatch.setattr(ctypes, "CDLL", lambda path: fake_lib)

    # Must not raise — a bad warmup image should defer the cost to the
    # first real scan, not block server startup entirely.
    CVEngineBridge(str(lib_path), str(model_path), warmup_image_path=str(warmup_path))
