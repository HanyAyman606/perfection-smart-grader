cmake_minimum_required(VERSION 3.14)
project(ai_corrector_cpp)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

find_package(OpenCV REQUIRED)

# Assume nlohmann/json is available in include paths or via target
find_package(nlohmann_json QUIET)

# Find ONNX Runtime (assuming it's installed or provided via CMake config)
find_package(onnxruntime REQUIRED)

add_library(ai_corrector SHARED
    src/preprocessing.cpp
    src/grouping.cpp
    src/inference.cpp
    src/questions.cpp
    src/ffi.cpp
)

target_include_directories(ai_corrector PUBLIC include)
target_link_libraries(ai_corrector PUBLIC ${OpenCV_LIBS} onnxruntime)
if(TARGET nlohmann_json::nlohmann_json)
    target_link_libraries(ai_corrector PUBLIC nlohmann_json::nlohmann_json)
endif()

add_executable(ai_corrector_cli src/main.cpp)
target_link_libraries(ai_corrector_cli PRIVATE ai_corrector)
