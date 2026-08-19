# Nexus Edge Grading System

Nexus Edge is a high-performance, client-server Optical Mark Recognition (OMR) system designed to automate the grading of paper-based multiple-choice exams.

The system empowers educators and proctors to scan completed bubble-sheet exams using mobile devices. It processes the sheets locally on the mobile edge via a custom AI computer vision engine, and syncs the structured grading results in real-time over a local network to a central desktop admin dashboard.

---

## 🏛️ Architecture & Tech Stack

Nexus Edge consists of three primary components that work in tandem:

1. **Admin Dashboard (Server / Hub)**
   - **Tech Stack:** Python, PySide6, SQLite.
   - **Role:** A desktop application serving as the source of truth. It manages the exam blueprints, rosters, and local SQLite database (`grading_system.db`). It embeds a WebSocket server to receive live grading submissions from edge clients.

2. **Edge Client (Mobile Scanner)**
   - **Tech Stack:** Flutter, Dart.
   - **Role:** A mobile app utilized by proctors to scan exam sheets. It captures images via the device camera, offloads processing to the native CV engine via FFI, allows proctors to review ambiguous marks, and communicates with the Admin Dashboard via WebSockets.

3. **CV Engine (Native AI Core)**
   - **Tech Stack:** C++, OpenCV 4, ONNX Runtime, YOLO.
   - **Role:** A highly optimized native C++ library loaded by the Flutter app. It handles heavy image preprocessing (perspective warping, noise reduction) and performs fast neural-network inference (YOLO/ONNX) to identify filled bubbles and student IDs locally on the device.

---

## 🚀 Key Features

* **AI-Powered Local OMR:** Utilizes a YOLO ONNX model executed directly on the mobile edge for fast, privacy-preserving bubble detection—no cloud dependency required for processing.
* **Adaptive Thresholding & Shape Analysis:** Differentiates between properly filled bubbles, 'X' marks, and blank spaces by analyzing local pixel density and circularity, heavily mitigating lighting variances and stray scribbles.
* **Auto-Orientation & Perspective Correction:** Automatically detects fiducial markers on the sheet to correct perspective distortion and rotation before grading.
* **Real-time Synchronization:** Graded results are pushed instantly from multiple mobile scanners to the central PySide6 dashboard over a local WebSocket connection.
* **Offline Resilience:** The Edge Client caches scans locally and retries submissions automatically when network connectivity is restored.

---

## 📂 Project Structure

```text
perfection/
├── core/
│   ├── admin_dashboard/      # Python/PySide6 desktop app & WebSocket server
│   │   ├── pages/            # UI Pages (Setup, Model Answers, Session)
│   │   ├── widgets/          # Reusable UI components (IP QR Code share, etc.)
│   │   ├── workers/          # Background tasks (websocket_server.py)
│   │   ├── dashboard.py      # Main dashboard window controller
│   │   └── main.py           # Application entry point
│   │
│   ├── edge_client/          # Flutter mobile application
│   │   ├── lib/              # Dart source code
│   │   │   ├── core/         # Cross-cutting concerns (Logging, Error UI)
│   │   │   ├── data/         # Repositories, FFI bindings, Cache, WebSockets
│   │   │   ├── domain/       # Abstract repository interfaces & Entities
│   │   │   └── features/     # Feature-specific UI & Controllers (Grading, Scanning)
│   │   ├── assets/models/    # Contains the YOLO bubble.onnx model
│   │   └── pubspec.yaml      # Flutter dependencies
│   │
│   ├── cv_engine/            # Native C++ OMR Engine
│   │   ├── include/          # C++ Headers (ffi.h, config.h)
│   │   ├── src/              # C++ Source (ffi.cpp, preprocessing.cpp, inference.cpp)
│   │   └── CMakeLists.txt    # CMake build configuration
│   │
│   ├── native_cv/            # (Deprecated) Older OpenCV heuristic-based engine
│   └── requirements.txt      # Python dependencies for the Admin Dashboard
└── README.md                 # Project documentation (this file)
```

---

## 🛠️ Build & Setup Instructions

### 1. Admin Dashboard (Desktop)
Ensure you have Python 3.9+ installed.
```bash
cd core
pip install -r requirements.txt
python admin_dashboard/main.py
```

### 2. CV Engine (C++ Library)
Requires CMake, OpenCV 4, and ONNX Runtime C++ binaries.
1. Download and extract the ONNX Runtime for your target platform.
2. Build the shared library:
```bash
cd core/cv_engine
mkdir build && cd build
cmake .. -DONNXRUNTIME_ROOT="/path/to/onnxruntime"
cmake --build . --config Release
```
*Note: For Android, you must pass the appropriate Android NDK toolchain file and compile against static OpenCV libraries to minimize the final APK footprint.*

### 3. Edge Client (Flutter Mobile App)
Requires the Flutter SDK.
1. Place the compiled CV Engine native library (`libai_corrector.so` for Android, `.dylib` for iOS/macOS, `.dll` for Windows) into the appropriate Flutter platform directory (e.g., `android/app/src/main/jniLibs/`).
2. Ensure the `shamel.onnx` model is located at `core/edge_client/assets/models/shamel.onnx`.
3. Build and run:
```bash
cd core/edge_client
flutter pub get
flutter run
```

---

## 🔗 Extensibility & API

The FFI boundary between the Flutter Edge Client and the C++ CV Engine relies on passing JSON strings. This loosely coupled design allows the C++ engine to adapt dynamically to varying grid configurations (number of questions, columns, options) defined by the Admin Dashboard, without requiring a C++ recompilation.

For detailed information on the FFI surface, internal architecture, and system limitations, refer to the [Project Documentation](project_documentation.md) generated alongside this README.
