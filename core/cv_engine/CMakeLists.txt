cmake_minimum_required(VERSION 3.16)
project(exam_scanner_cpp CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()

find_package(OpenCV REQUIRED COMPONENTS core imgproc imgcodecs dnn)
find_package(nlohmann_json REQUIRED)

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
