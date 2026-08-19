cmake_minimum_required(VERSION 3.16)
project(exam_scanner_cpp CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()

find_package(OpenCV REQUIRED COMPONENTS core imgproc imgcodecs dnn)
include(FetchContent)
FetchContent_Declare(
  nlohmann_json
  URL https://github.com/nlohmann/json/releases/download/v3.11.3/json.tar.xz
)
FetchContent_MakeAvailable(nlohmann_json)

# ---------------------------------------------------------------------------
# ONNX Runtime: point ONNXRUNTIME_ROOT at an extracted onnxruntime-linux-x64
# release (https://github.com/microsoft/onnxruntime/releases), e.g.:
#   cmake -B build -DONNXRUNTIME_ROOT=/path/to/onnxruntime-linux-x64-1.18.0
# ---------------------------------------------------------------------------
if(WIN32)
  if(NOT ONNXRUNTIME_ROOT)
    set(ONNXRUNTIME_ROOT "${CMAKE_SOURCE_DIR}/third_party/onnxruntime-win-x64-1.18.0")
  endif()
  add_library(onnxruntime SHARED IMPORTED)
  set_target_properties(onnxruntime PROPERTIES
    IMPORTED_LOCATION "${ONNXRUNTIME_ROOT}/lib/onnxruntime.dll"
    IMPORTED_IMPLIB "${ONNXRUNTIME_ROOT}/lib/onnxruntime.lib"
    INTERFACE_INCLUDE_DIRECTORIES "${ONNXRUNTIME_ROOT}/include"
  )
elseif(ANDROID)
  # Pass -DONNXRUNTIME_ROOT=/path/to/onnxruntime-android-<version> from
  # Gradle's externalNativeBuild.cmake.arguments. Expects
  # ${ONNXRUNTIME_ROOT}/jni/${ANDROID_ABI}/libonnxruntime.so (the layout
  # of Microsoft's official onnxruntime-android AAR contents), NOT
  # lib/libonnxruntime.so like the desktop builds.
  if(NOT ONNXRUNTIME_ROOT)
    message(FATAL_ERROR "ONNXRUNTIME_ROOT must be set for Android builds (point it at an extracted onnxruntime-android package).")
  endif()
  add_library(onnxruntime SHARED IMPORTED)
  set_target_properties(onnxruntime PROPERTIES
    IMPORTED_LOCATION "${ONNXRUNTIME_ROOT}/jni/${ANDROID_ABI}/libonnxruntime.so"
    INTERFACE_INCLUDE_DIRECTORIES "${ONNXRUNTIME_ROOT}/headers"
  )
else()
  if(NOT ONNXRUNTIME_ROOT)
    set(ONNXRUNTIME_ROOT "${CMAKE_SOURCE_DIR}/third_party/onnxruntime-linux-x64-1.18.0")
  endif()
  add_library(onnxruntime SHARED IMPORTED)
  set_target_properties(onnxruntime PROPERTIES
    IMPORTED_LOCATION "${ONNXRUNTIME_ROOT}/lib/libonnxruntime.so"
    INTERFACE_INCLUDE_DIRECTORIES "${ONNXRUNTIME_ROOT}/include"
  )
endif()

include_directories(${CMAKE_SOURCE_DIR}/include)

# Stages that don't need ONNX Runtime.
foreach(stage IN ITEMS stage1_boxes stage2_warp stage3_orient stage4_final)
  add_executable(${stage} src/${stage}.cpp)
  target_link_libraries(${stage} PRIVATE ${OpenCV_LIBS} nlohmann_json::nlohmann_json)
endforeach()

# Stage 5 needs ONNX Runtime.
add_executable(stage5_inference src/stage5_inference.cpp)
target_link_libraries(stage5_inference PRIVATE ${OpenCV_LIBS} nlohmann_json::nlohmann_json onnxruntime)
if(WIN32)
  add_custom_command(TARGET stage5_inference POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E copy_if_different
    "${ONNXRUNTIME_ROOT}/lib/onnxruntime.dll"
    $<TARGET_FILE_DIR:stage5_inference>
  )
endif()

# Stage 6 needs exam_config.hpp but no OpenCV image ops.
add_executable(stage6_scoring src/stage6_scoring.cpp)
target_link_libraries(stage6_scoring PRIVATE nlohmann_json::nlohmann_json)

# Master pipeline runner (invokes the stage binaries above as subprocesses).
add_executable(run_pipeline src/run_pipeline.cpp)
target_link_libraries(run_pipeline PRIVATE nlohmann_json::nlohmann_json)

message(STATUS "ONNX Runtime root: ${ONNXRUNTIME_ROOT}")
message(STATUS "OpenCV version: ${OpenCV_VERSION}")

# FFI Shared Library for Flutter
add_library(exam_scanner_ffi SHARED src/ffi_api.cpp)
target_include_directories(exam_scanner_ffi PRIVATE include)
target_link_libraries(exam_scanner_ffi PRIVATE nlohmann_json::nlohmann_json)

# ---------------------------------------------------------------------------
# ANDROID ONLY: run_exam_pipeline shells out to the 6 stage executables as
# subprocesses (see ffi_api.cpp). Gradle's CMake integration only auto-copies
# real SHARED library targets into the APK's jniLibs/<abi>/ — plain
# add_executable() outputs above are silently left out of the package.
#
# Workaround: copy each stage executable (still a normal ELF executable,
# NOT an actual shared object) into this build's library output directory
# under a lib*.so name, so Gradle's packaging step picks it up alongside
# exam_scanner_ffi.so. With android:extractNativeLibs="true" in the
# manifest, the OS extracts it to nativeLibraryDir with execute permission
# at install time, same as a real .so.
#
# UNVERIFIED: this is a known trick (used by e.g. Termux-style apps) but
# has NOT been confirmed to work on a real device/API level for this
# project. Things worth checking on-device before trusting this in
# production: (1) SELinux policy on the target API level may still refuse
# to exec files from nativeLibraryDir despite extraction succeeding —
# newer Android versions have tightened this; (2) std::system() inside
# ffi_api.cpp needs the renamed libstage1_boxes.so path (not stage1_boxes)
# — PipelinePathService.resolveBinDir() + ffi_api.cpp's run_stage_ffi()
# will need to look for "lib${stage}.so" on Android specifically, which
# is not yet wired up on either the C++ or Dart side.
# ---------------------------------------------------------------------------
if(ANDROID)
  foreach(stage IN ITEMS stage1_boxes stage2_warp stage3_orient stage4_final stage5_inference stage6_scoring run_pipeline)
    add_custom_command(TARGET ${stage} POST_BUILD
      COMMAND ${CMAKE_COMMAND} -E copy
      "$<TARGET_FILE:${stage}>"
      "$<TARGET_FILE_DIR:exam_scanner_ffi>/lib${stage}.so"
      COMMENT "Repackaging ${stage} as lib${stage}.so for Android APK bundling (unverified — see comment above)"
    )
  endforeach()
endif()