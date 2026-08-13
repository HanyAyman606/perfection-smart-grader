# Running the lab-side grading pipeline tests

These test cv_engine_bridge.py, mcq_scoring.py, workers/websocket_server.py,
and live_session_controller.py — the four files that changed for the
lab-side (was: on-phone) grading redesign.

They do NOT require a real compiled cv_engine .so/.dll — ctypes.CDLL is
faked out in test_cv_engine_bridge.py, and a duck-typed FakeCVEngine
stands in for it everywhere else (see conftest.py). They also don't
start a real QThread or open a real network socket — WebSocketServer's
handler coroutines are awaited directly.

## Setup (one time)

    pip install pytest PySide6 websockets

## Run

From the project root (the directory containing the `admin_dashboard/`
package):

    QT_QPA_PLATFORM=offscreen python -m pytest admin_dashboard/tests/ -v

`QT_QPA_PLATFORM=offscreen` avoids needing a real display — PySide6
still gets imported (WebSocketServer subclasses QThread) but nothing is
actually shown. On a normal dev machine with a display you can drop
that env var.

## What's covered

- `test_mcq_scoring.py` — pure scoring logic: correct/wrong/blank
  answers, voided questions, per-range points, unknown answer version.
- `test_cv_engine_bridge.py` — the ctypes wrapper: missing lib/model
  files, NULL pointer handling, `free_string` pairing (no leaks),
  ERROR-status responses, config JSON shape, warmup failure tolerance.
- `test_websocket_server.py` — the actual pipeline, against a real
  temp-file sqlite db: success path, low-confidence retake, missing
  student ID, oversized/missing image, engine errors, engine timeout,
  temp file cleanup, duplicate detection, all three duplicate
  resolutions (overwrite/discard/keep), and stale-duplicate expiry via
  the heartbeat sweep.
- `test_live_session_controller.py` — engine-load failure surfaces as
  `session_start_failed` (not a crash), success path wires the engine
  into `WebSocketServer` correctly, double-start guard, `stop()` clears
  the engine reference.

## What's NOT covered (needs the real compiled engine)

Whether the actual C++ `process_exam_in_memory` ABI matches what
`cv_engine_bridge.py` assumes — pointer ownership, calling convention,
JSON field names on the C++ side. That can only be verified once
`cv_engine` is compiled for desktop. Once it exists, the fastest sanity
check is: point `CVEngineBridge` at the real `.so`/`.dll` and
`bubble.onnx`, run `process_exam()` on one real scanned image outside
of pytest, and confirm the returned dict matches what
`API_DOCUMENTATION.md` documents. Recommend doing that as a manual
smoke test before the first real live session, not as an automated
test — it needs an actual sample scan image checked into the repo.
