#include "ffi_api.h"

#include <cstdlib>
#include <ctime>
#include <filesystem>
#include <iostream>
#include <string>
#include <nlohmann/json.hpp>
#include <fstream>
#include <vector>

namespace fs = std::filesystem;

namespace {

const std::vector<std::string> STAGES = {
    "stage1_boxes", "stage2_warp", "stage3_orient", "stage4_final", "stage5_inference", "stage6_scoring",
};

int run_stage_ffi(const fs::path& bin_dir, const std::string& stage, const fs::path& work_dir) {
#ifdef _WIN32
    fs::path exe = bin_dir / (stage + ".exe");
#else
    fs::path exe = bin_dir / stage;
#endif
    std::string cmd = "\"" + exe.string() + "\" \"" + work_dir.string() + "\"";
#ifdef _WIN32
    cmd = "\"" + cmd + "\"";
#endif
    return std::system(cmd.c_str());
}

} // namespace

extern "C" {

FFI_EXPORT int run_exam_pipeline(const char* image_path_c, const char* config_path_c, const char* output_dir_c, const char* bin_dir_c) {
    if (!image_path_c || !config_path_c || !output_dir_c || !bin_dir_c) return 1;

    fs::path image_path = fs::absolute(image_path_c);
    fs::path config_path = fs::absolute(config_path_c);
    fs::path output_dir = fs::absolute(output_dir_c);
    fs::path bin_dir = fs::absolute(bin_dir_c);

    if (!fs::exists(image_path) || !fs::exists(config_path)) {
        std::cerr << "[FFI] Error: Input image or config does not exist.\n";
        return 1;
    }

    std::srand(static_cast<unsigned>(std::time(nullptr)));
    std::error_code ec;
    fs::path work_dir;
    do {
        work_dir = fs::temp_directory_path() / ("exam_run_ffi_" + std::to_string(std::time(nullptr)) + "_" + std::to_string(std::rand()));
    } while (fs::exists(work_dir));
    fs::create_directories(work_dir / "input", ec);

    fs::copy_file(image_path, work_dir / "input" / image_path.filename(), fs::copy_options::overwrite_existing, ec);
    fs::copy_file(config_path, work_dir / "exam_config.json", fs::copy_options::overwrite_existing, ec);

    std::ifstream cfg_file(work_dir / "exam_config.json");
    nlohmann::json cfg_json;
    if (cfg_file) {
        cfg_file >> cfg_json;
    }
    std::string model_rel = cfg_json.value("model_path", "shamel.onnx");
    fs::path model_p(model_rel);

    if (!model_p.is_absolute()) {
        fs::path model_src = bin_dir / model_p;
        if (fs::exists(model_src)) {
            fs::copy_file(model_src, work_dir / model_p, fs::copy_options::overwrite_existing, ec);
        } else {
            std::cerr << "[FFI] Warning: model " << model_p << " not found in " << bin_dir << "\n";
        }
    }

    for (const auto& s : STAGES) {
        if (run_stage_ffi(bin_dir, s, work_dir) != 0) {
            std::cerr << "[FFI] Error: Stage " << s << " failed.\n";
            fs::remove_all(work_dir, ec);
            return 2;
        }
        
        // If preprocessing failed in stage 1, skip straight to scoring to generate the final JSON
        if (s == "stage1_boxes" && fs::exists(work_dir / "preprocessing_result.json")) {
            if (run_stage_ffi(bin_dir, "stage6_scoring", work_dir) != 0) {
                std::cerr << "[FFI] Error: Stage 6 failed during early exit.\n";
                fs::remove_all(work_dir, ec);
                return 2;
            }
            break;
        }
    }

    fs::create_directories(output_dir, ec);
    fs::path final_output = output_dir / image_path.stem();
    fs::create_directories(final_output, ec);
    
    for (const auto& entry : fs::directory_iterator(work_dir)) {
        if (entry.is_directory() && entry.path().filename().string().find("stage") == 0) {
            fs::copy(entry.path(), final_output / entry.path().filename(), fs::copy_options::recursive | fs::copy_options::overwrite_existing, ec);
        }
    }

    fs::remove_all(work_dir, ec);
    return 0; // Success
}

} // extern "C"
