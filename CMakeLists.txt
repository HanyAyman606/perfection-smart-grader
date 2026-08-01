cmake_minimum_required(VERSION 3.16)
project(pergection_smart_grader CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
  set(CMAKE_BUILD_TYPE Release CACHE STRING "Build type (Debug/Release/...)" FORCE)
endif()

# The only buildable component today is the native OMR engine (native_cv), consumed
# both as a CLI tool and as the shared library the Flutter app (core/edge_client)
# loads via dart:ffi. The Python admin_dashboard and the Flutter app itself are
# separate toolchains (pip / flutter) — see the root README for how to run those.
add_subdirectory(core/edge_client/native_cv)
