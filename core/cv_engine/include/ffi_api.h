#pragma once

#ifdef __cplusplus
extern "C" {
#endif

#if defined(_WIN32)
#define FFI_EXPORT __declspec(dllexport)
#else
#define FFI_EXPORT __attribute__((visibility("default")))
#endif

// Runs the entire scanning pipeline.
// image_path: absolute path to the input image.
// config_path: absolute path to the exam_config.json.
// output_dir: absolute path to the directory where results will be written.
// bin_dir: absolute path to the directory containing stage executables and shamel.onnx.
// 
// Returns 0 on success, 1 on invalid arguments, 2 on pipeline failure.
FFI_EXPORT int run_exam_pipeline(const char* image_path, const char* config_path, const char* output_dir, const char* bin_dir);

#ifdef __cplusplus
}
#endif
