// main.cpp — CLI smoke-test tool for the desktop build.
//
// REPLACES the previous main.cpp, which called step1_extract_panels()
// and step2_infer_and_score() — those matched an older two-call API
// documented in README.md/API_DOCUMENTATION.md, but the engine was
// refactored to the single process_exam_in_memory() call declared in
// ffi.h. The old main.cpp does not compile against the current ffi.h
// (undeclared functions) — this file replaces it with the corrected
// equivalent, using the actual current API.
//
// Usage: ai_corrector_cli <image_path> <config_json_path>
//   config_json_path points at a JSON file, e.g.:
//     {
//       "model_path": "/path/to/bubble.onnx",
//       "num_questions": 5,
//       "num_choices": 4,
//       "mcq_columns": {"num_cols": 1, "columns": {"1": 5}},
//       "id": {"num_digits": 4, "num_letters": 6, "letters": ["C","D","E","F","M","W"]}
//     }
//   (mcq_columns.columns must sum to exactly num_questions, or the
//   engine returns status: "ERROR" — see config.h's from_json.)

#include "ffi.h"
#include <nlohmann/json.hpp>
#include <iostream>
#include <fstream>
#include <sstream>

int main(int argc, char** argv) {
    if (argc < 3) {
        std::cerr << "Usage: " << argv[0] << " <image_path> <config_json_path>" << std::endl;
        return 1;
    }

    std::string image_path = argv[1];
    std::string config_json_path = argv[2];

    std::ifstream t(config_json_path);
    if (!t) {
        std::cerr << "Could not open config file: " << config_json_path << std::endl;
        return 1;
    }
    std::stringstream buffer;
    buffer << t.rdbuf();
    std::string config_json_str = buffer.str();

    const char* result_cstr = process_exam_in_memory(image_path.c_str(), config_json_str.c_str());
    if (!result_cstr) {
        std::cerr << "Error: process_exam_in_memory returned null" << std::endl;
        return 1;
    }

    std::string result = result_cstr;
    free_string(const_cast<char*>(result_cstr));

    std::cout << result << std::endl;

    try {
        nlohmann::json parsed = nlohmann::json::parse(result);
        std::string status = parsed.value("status", "");
        return (status == "SUCCESS") ? 0 : 1;
    } catch (...) {
        return 1;
    }
}
