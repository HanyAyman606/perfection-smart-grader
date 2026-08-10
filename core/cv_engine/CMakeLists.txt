cmake_minimum_required(VERSION 3.14)
project(ai_corrector_cpp)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# --- OpenCV --------------------------------------------------------------
# Android: the OpenCV Android SDK ships a real OpenCVConfig.cmake (unlike
# the Windows package), so find_package() works normally once OpenCV_DIR
# points at sdk/native/jni.
#
# STATIC, not shared. The default OpenCV_DIR=.../sdk/native/jni resolves
# to the SDK's shared target, which on Android is the single combined
# libopencv_java4.so (~23MB) — a prebuilt binary containing every module
# plus its JNI/Java bindings, none of which this C++-only code calls.
# Requesting only the modules actually used (core/imgproc/imgcodecs —
# confirmed by grep across src/ + include/: nothing here touches calib3d,
# dnn, ml, video, or highgui) only helps if we link the SDK's STATIC
# per-module libs instead (sdk/native/staticlibs/<abi>/libopencv_*.a) —
# those get baked directly into ai_corrector.so, -Wl,--gc-sections below
# then drops whatever object code turns out unreachable, and the fat
# shared .so no longer needs to ship in the APK at all.
# Android side: point OpenCV_DIR at the SDK's static-libs config instead
# of native/jni's default shared one (see build.gradle.kts for the actual
# -DOpenCV_DIR path) and pass -DBUILD_SHARED_LIBS=OFF via the same
# arguments block. NOTE: this assumes the SDK you're using actually ships
# static libs for arm64-v8a under sdk/native/staticlibs/ — recent OpenCV
# Android SDK releases do, but verify the folder exists before relying on
# this; if it's missing, fall back to shared linking and just keep
# libopencv_java4.so bundled (still smaller than shipping the previous
# orphaned 38MB debug engine on top of it).
find_package(OpenCV REQUIRED COMPONENTS core imgproc imgcodecs)

if(ANDROID)
    # Trim unreachable code from the statically-linked-in OpenCV object
    # code — without this, --gc-sections has nothing to actually remove
    # since the linker keeps whole .a members that are pulled in at all.
    add_compile_options(-ffunction-sections -fdata-sections)
    add_link_options(-Wl,--gc-sections)
endif()

# Ship a release build, not a debug one. Nothing below previously set
# CMAKE_BUILD_TYPE, so Ninja/Make defaulted to an unoptimized build with
# full debug info baked in — confirmed on Android (the old engine's
# leftover libomr_engine.so shipped `with debug_info, not stripped`,
# ~39MB) and is exactly what the Windows notes further down already flag
# happening to the OpenCV DLL choice. This does NOT override a build
# invoked with an explicit -DCMAKE_BUILD_TYPE=... (e.g. Android's
# externalNativeBuild passing its own), only the case where nothing was
# specified at all.
if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
    set(CMAKE_BUILD_TYPE Release CACHE STRING "" FORCE)
endif()

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

    # Strip debug symbols from the built .so in Release configs. AGP's
    # automatic post-build stripping only reliably kicks in for libraries
    # it fully owns the build graph for; belt-and-suspenders this instead
    # of relying on it, since the previous unstripped 39MB debug .so
    # shipped straight to users made that assumption once already.
    if(CMAKE_BUILD_TYPE STREQUAL "Release" AND CMAKE_STRIP)
        add_custom_command(TARGET ai_corrector POST_BUILD
            COMMAND ${CMAKE_STRIP} --strip-unneeded $<TARGET_FILE:ai_corrector>
            COMMENT "Stripping ai_corrector.so for release"
        )
    endif()
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
if(WIN32)
    add_custom_command(TARGET ai_corrector POST_BUILD
        COMMAND ${CMAKE_COMMAND} -E copy_if_different
            "${ONNXRUNTIME_ROOT}/lib/onnxruntime.dll"
            "$<TARGET_FILE_DIR:ai_corrector>"
        COMMENT "Copying onnxruntime.dll next to ai_corrector.dll"
    )

    get_filename_component(OPENCV_BIN_DIR "${OpenCV_DIR}/../bin" ABSOLUTE)

    add_custom_command(TARGET ai_corrector POST_BUILD
        COMMAND ${CMAKE_COMMAND} -E copy_if_different
            "${OPENCV_BIN_DIR}/opencv_world4120d.dll"
            "$<TARGET_FILE_DIR:ai_corrector>"
        COMMENT "Copying OpenCV runtime DLL next to ai_corrector.dll"
    )
endif()
