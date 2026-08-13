#include "ffi.h"
#include <nlohmann/json.hpp>
#include <iostream>
#include <fstream>
#include <sstream>

// Desktop CLI wrapper around the real production entry point,
// process_exam_in_memory (see ffi.h) -- runs the FULL raw-photo pipeline
// (quad detect, warp, quality gate, inference, classification) exactly
// as the Flutter app calls it via native_cv_bindings.dart, useful for
// testing a real device photo end-to-end without a phone.
//
// IMPORTANT: this runs on WHATEVER MACHINE YOU BUILD/RUN IT ON (desktop
// CPU), not the phone. Its timing numbers are a desktop reference point,
// NOT the on-device number -- for the real "does this hit 3 seconds on
// the phone" answer, you need the timing_ms block (added to ffi.cpp) to
// come back from an actual scan run through the real Flutter app on the
// actual Android device.
//
// This previously called step1_extract_panels()/step2_infer_and_score(),
// a two-call API that no longer exists -- the engine was consolidated
// into the single process_exam_in_memory() call (see MIGRATION_NOTES.md).
// That left this file failing to compile against the current ffi.h.
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

    std::string result_str = result_cstr;
    free_string(const_cast<char*>(result_cstr));

    // Pretty-print if it parses as JSON; fall back to raw output otherwise
    // so a malformed response is still visible rather than swallowed.
    try {
        nlohmann::json result = nlohmann::json::parse(result_str);
        std::cout << result.dump(2) << std::endl;

        if (result.contains("timing_ms")) {
            std::cout << "\n--- Timing breakdown (DESKTOP, not on-device) ---" << std::endl;
            for (auto& [phase, ms] : result["timing_ms"].items()) {
                std::cout << "  " << phase << ": " << ms << " ms" << std::endl;
            }
        }

        if (result.value("status", "") != "SUCCESS") {
            return 1;
        }
    } catch (const std::exception&) {
        std::cout << result_str << std::endl;
    }

    return 0;
}
