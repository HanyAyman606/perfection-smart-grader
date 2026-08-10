cmake_minimum_required(VERSION 3.14)
project(ai_corrector_cpp)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# --- OpenCV --------------------------------------------------------------
# Android: the OpenCV Android SDK ships a real OpenCVConfig.cmake (unlike
# the Windows package), so find_package() works normally once OpenCV_DIR
# points at sdk/native/jni. Windows: same find_package() call, but
# OpenCV_DIR must point at the specific vcXX/lib folder (see the Windows
# build notes) since the top-level build/ folder's version-detection logic
# doesn't recognize newer MSVC toolsets.
find_package(OpenCV REQUIRED)

# --- nlohmann/json (header-only) ------------------------------------------
# Already present at include/nlohmann/json.hpp in this project -- no extra
# setup needed, it's covered by the existing `include` directory added to
# target_include_directories() below.

# --- ONNX Runtime ----------------------------------------------------------
# Neither the Windows prebuilt zip nor the Android .aar ships a CMake
# package config, so find_package(onnxruntime) doesn't work against
# either -- point directly at each platform's extracted package instead.
#
# Windows: set -DONNXRUNTIME_ROOT="<path to extracted onnxruntime-win-x64 folder>"
#          (expects ONNXRUNTIME_ROOT/include/*.h and ONNXRUNTIME_ROOT/lib/onnxruntime.{lib,dll})
# Android: set -DONNXRUNTIME_ROOT="<path to extracted onnxruntime-android .aar-as-zip>"
#          (expects ONNXRUNTIME_ROOT/headers/*.h and ONNXRUNTIME_ROOT/jni/<abi>/libonnxruntime.so)
if(NOT DEFINED ONNXRUNTIME_ROOT)
    message(FATAL_ERROR "ONNXRUNTIME_ROOT is not set. Pass -DONNXRUNTIME_ROOT=\"<path to extracted ONNX Runtime package>\" when configuring.")
endif()

if(ANDROID)
    set(ONNXRUNTIME_HEADER_DIR "${ONNXRUNTIME_ROOT}/headers")
    set(ONNXRUNTIME_LIB_DIR "${ONNXRUNTIME_ROOT}/jni/${ANDROID_ABI}")
    set(ONNXRUNTIME_LIB_FILE "${ONNXRUNTIME_LIB_DIR}/libonnxruntime.so")
else()
    set(ONNXRUNTIME_HEADER_DIR "${ONNXRUNTIME_ROOT}/include")
    set(ONNXRUNTIME_LIB_DIR "${ONNXRUNTIME_ROOT}/lib")
endif()

# The source does `#include <onnxruntime/onnxruntime_cxx_api.h>` (a nested
# path), but both prebuilt packages ship headers FLAT (no onnxruntime/
# subfolder) -- confirmed against both the Windows zip and the extracted
# Android .aar. Stage a copy under the expected nested path at configure
# time rather than editing the source's #include directives.
set(ONNXRUNTIME_STAGE_DIR "${CMAKE_BINARY_DIR}/onnxruntime_include_staged")
file(MAKE_DIRECTORY "${ONNXRUNTIME_STAGE_DIR}/onnxruntime")
file(GLOB ONNXRUNTIME_HEADERS "${ONNXRUNTIME_HEADER_DIR}/*.h")
file(COPY ${ONNXRUNTIME_HEADERS} DESTINATION "${ONNXRUNTIME_STAGE_DIR}/onnxruntime")

add_library(ai_corrector SHARED
    src/preprocessing.cpp
    src/grouping.cpp
    src/inference.cpp
    src/questions.cpp
    src/ffi.cpp
)

target_include_directories(ai_corrector PUBLIC
    include
    "${ONNXRUNTIME_HEADER_DIR}"
    "${ONNXRUNTIME_STAGE_DIR}"
)

# See include/ffi.h -- AI_CORRECTOR_BUILDING_DLL selects dllexport vs.
# dllimport on Windows; it's a no-op on Android/Linux (GCC/Clang export
# public symbols from shared libraries by default), but defining it
# unconditionally here is harmless on both platforms.
target_compile_definitions(ai_corrector PRIVATE AI_CORRECTOR_BUILDING_DLL)

target_link_directories(ai_corrector PUBLIC "${ONNXRUNTIME_LIB_DIR}")

if(ANDROID)
    # Link the .so directly by full path -- Android's linker doesn't do
    # the same bare-name-to-lib-file resolution MSVC's link.exe does via
    # target_link_directories, and the file is literally named
    # libonnxruntime.so (not onnxruntime.so), so the bare "onnxruntime"
    # target_link_libraries name that works on Windows wouldn't resolve
    # correctly here.
    target_link_libraries(ai_corrector PUBLIC ${OpenCV_LIBS} "${ONNXRUNTIME_LIB_FILE}" log)
else()
    target_link_libraries(ai_corrector PUBLIC ${OpenCV_LIBS} onnxruntime)
endif()

# --- CLI test executable (desktop only; not built/used on Android) -------
if(NOT ANDROID)
    add_executable(ai_corrector_cli src/main.cpp)
    target_link_libraries(ai_corrector_cli PRIVATE ai_corrector)
    target_include_directories(ai_corrector_cli PRIVATE
        include
        "${ONNXRUNTIME_HEADER_DIR}"
        "${ONNXRUNTIME_STAGE_DIR}"
    )
endif()

# --- Post-build: copy required DLLs next to the built binaries (Windows only) ---
# Windows resolves DLL dependencies by looking next to the .exe/.dll that
# needs them (no rpath like Linux/macOS) -- without this, running
# ai_corrector_cli.exe or loading ai_corrector.dll from Flutter will fail
# with a missing-DLL error even though the build itself succeeded.
#
# Android doesn't need this: Gradle's externalNativeBuild automatically
# packages the built .so (and any .so it links against, if placed under
# jniLibs) into the APK, and the Android linker resolves shared library
# dependencies from within the APK's native library directory at
# app-install/load time -- there's no "copy next to the exe" step needed.
if(NOT ANDROID)
    add_custom_command(TARGET ai_corrector POST_BUILD
        COMMAND ${CMAKE_COMMAND} -E copy_if_different
            "${ONNXRUNTIME_ROOT}/lib/onnxruntime.dll"
            "$<TARGET_FILE_DIR:ai_corrector>"
        COMMENT "Copying onnxruntime.dll next to ai_corrector.dll"
    )

    # OpenCV's Windows package ships its runtime DLL in bin/, separate from
    # lib/ (which only has the .lib import library) -- confirmed via
    # directory listing. This project uses plain Ninja with no
    # CMAKE_BUILD_TYPE set, which is why OpenCVConfig.cmake defaulted to
    # linking the Debug variant (opencv_world4120d.lib, confirmed in the
    # actual link command) -- so the matching Debug DLL is what needs to sit
    # next to the binaries. If you later configure with
    # -DCMAKE_BUILD_TYPE=Release, change the filename below from
    # opencv_world4120d.dll to opencv_world4120.dll (no 'd' suffix) to match.
    # get_filename_component(OPENCV_BIN_DIR "${OpenCV_DIR}/../bin" ABSOLUTE)
    # add_custom_command(TARGET ai_corrector POST_BUILD
    #     COMMAND ${CMAKE_COMMAND} -E copy_if_different
    #         "${OPENCV_BIN_DIR}/opencv_world4120d.dll"
    #         "$<TARGET_FILE_DIR:ai_corrector>"
    #     COMMENT "Copying OpenCV runtime DLL next to ai_corrector.dll"
    # )
endif()
