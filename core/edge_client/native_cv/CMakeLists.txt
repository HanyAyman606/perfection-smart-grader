cmake_minimum_required(VERSION 3.10)
project(native_cv)

# native_cv — the OMR (bubble-sheet) grading engine.
#
# Builds two targets from the same source (src/omr_engine.cpp):
#   - omr_cli    : standalone CLI (`omr_cli status|calibrate|run ...`), useful for
#                  testing/calibrating a template without going through Flutter.
#   - omr_engine : shared library exposing the FFI surface declared in
#                  include/omr_engine/ffi_api.h — this is what the Flutter app
#                  (../lib) loads via dart:ffi.
#
# Requires OpenCV (core, imgproc, imgcodecs, highgui). On Windows, build with the
# MSYS2 UCRT64 toolchain (the same g++/cmake this project has always been built
# with) so find_package(OpenCV) resolves without extra hints; on Linux/macOS a
# system OpenCV install (or -DCMAKE_PREFIX_PATH=<opencv install>) is enough.
find_package(OpenCV REQUIRED COMPONENTS core imgproc imgcodecs highgui)

set(OMR_ENGINE_SOURCES src/omr_engine.cpp)

add_executable(omr_cli ${OMR_ENGINE_SOURCES})
target_include_directories(omr_cli PRIVATE include)
target_link_libraries(omr_cli PRIVATE ${OpenCV_LIBS})

add_library(omr_engine SHARED ${OMR_ENGINE_SOURCES})
target_include_directories(omr_engine PUBLIC include)
target_link_libraries(omr_engine PRIVATE ${OpenCV_LIBS})

# Force a plain "omr_engine.{dll,so,dylib}" name on every platform (MinGW would
# otherwise prepend "lib" on Windows) so the Dart-side DynamicLibrary.open() path is
# one predictable string instead of needing a per-platform prefix guess.
set_target_properties(omr_engine PROPERTIES PREFIX "" OUTPUT_NAME "omr_engine")
