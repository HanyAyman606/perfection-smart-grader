# Nexus Edge Mobile (Flutter Companion App)

This is the mobile companion application for the Nexus Edge Grading System. It acts as the scanning and grading client that connects to the desktop admin application over a local WebSocket.

## Prerequisites

- **Flutter SDK:** ^3.12.2 (or compatible 3.x version)
- **Android NDK:** Required to compile the `native_cv` OpenCV C++ engine for Android. (e.g. NDK version 25.1.8937393 or similar LTS).
- **OpenCV Android SDK:** Download the OpenCV Android SDK (e.g., 4.x) from [opencv.org/releases](https://opencv.org/releases/).

## Building the Native Library (`native_cv`)

The OMR grading engine is written in C++ and must be cross-compiled into a shared library (`libomr_engine.so`) for each target Android ABI.

1.  **Set up OpenCV:** Extract the OpenCV Android SDK. Set the `OpenCV_DIR` environment variable to point to the `sdk/native/jni` folder inside it.
2.  **Build with CMake:**
    From the `native_cv` directory, run CMake using the Android NDK toolchain. You will typically run this for each ABI (e.g., `arm64-v8a`, `armeabi-v7a`).

    Example for `arm64-v8a`:
    ```bash
    mkdir -p build_android/arm64-v8a
    cd build_android/arm64-v8a
    cmake ../.. \
        -DCMAKE_TOOLCHAIN_FILE=$ANDROID_NDK_HOME/build/cmake/android.toolchain.cmake \
        -DANDROID_ABI=arm64-v8a \
        -DANDROID_PLATFORM=android-28 \
        -DOpenCV_DIR=/path/to/opencv/sdk/native/jni
    make
    ```
3.  **Copy the Library:**
    Copy the resulting `libomr_engine.so` to the Flutter project's `android/app/src/main/jniLibs/arm64-v8a/` directory (create the directory if it doesn't exist).
4.  **Important:** Also copy `libc++_shared.so` from your NDK (typically found at `$ANDROID_NDK_HOME/toolchains/llvm/prebuilt/linux-x86_64/sysroot/usr/lib/aarch64-linux-android/libc++_shared.so`) into the same `jniLibs/arm64-v8a/` folder so it is bundled with the APK.

## Running the App

1.  Get all Dart dependencies:
    ```bash
    flutter pub get
    ```
2.  Run the application on a connected device or emulator:
    ```bash
    flutter run
    ```

## Running Unit Tests

To execute the suite of unit tests verifying all core logic (grading, JSON models, state management, etc.) without a device:

```bash
flutter test
```

## Important Notes

*   **Runtime Configuration:** The desktop host IP, proctor name, and session password are **not** hardcoded in the source. They are entered at runtime on the app's initial Connection Screen. (The session password defaults to `12345678` out-of-the-box for convenience).
*   **EXIF Latency Tradeoff:** The app manually normalizes EXIF image rotation using pure Dart before passing the image to OpenCV. This ensures accurate template matching without modifying the highly-tuned C++ pipeline, but may introduce a ~500ms processing delay per capture on mid-range devices.
