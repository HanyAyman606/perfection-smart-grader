// MASTER PIPELINE: runs stage1 -> stage2 -> stage3 -> stage4 -> stage5 ->
// stage6 in order, exactly mirroring run_pipeline.py.
//
// Folder layout (all relative to the exam working directory given as
// argv[1], defaulting to the current directory):
//     input/            <- put raw photos here (.jpg/.jpeg/.png)
//     stage1/           <- box detection output
//     stage2/           <- warped/flattened panels
//     stage3/           <- orientation-corrected panels
//     stage4/           <- final grayscale output for the model
//     stage5/           <- YOLO detections (txt) + annotated crops
//     stage6/           <- final_grades.json
//
// Usage:
//     ./run_pipeline [exam_working_dir]
//
// Re-run individual stageN_* binaries directly while iterating on just
// one step (faster than rerunning everything).
#include <cstdlib>
#include <ctime>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <string>
#include <vector>

#include <nlohmann/json.hpp>

using json = nlohmann::json;
namespace fs = std::filesystem;

namespace {

const std::vector<std::string> STAGES = {
    "stage1_boxes", "stage2_warp", "stage3_orient", "stage4_final", "stage5_inference", "stage6_scoring",
};

void run_stage(const fs::path& bin_dir, const std::string& stage, const fs::path& work_dir) {
    std::cout << "\n" << std::string(60, '=') << "\nRUNNING " << stage << "\n" << std::string(60, '=') << "\n";
#ifdef _WIN32
    fs::path exe = bin_dir / (stage + ".exe");
#else
    fs::path exe = bin_dir / stage;
#endif
    std::string cmd = "\"" + exe.string() + "\" \"" + work_dir.string() + "\"";
#ifdef _WIN32
    cmd = "\"" + cmd + "\"";
#endif
    int rc = std::system(cmd.c_str());
    if (rc != 0) {
        std::cout << "!!! " << stage << " FAILED (exit " << rc << ") - stopping pipeline\n";
        std::exit(1);
    }
}

void print_flags(const fs::path& work_dir) {
    fs::path path = work_dir / "stage3" / "_results.json";
    if (!fs::exists(path)) return;
    std::ifstream f(path);
    json results;
    f >> results;

    std::vector<json> flagged;
    for (auto& r : results) {
        if (r.contains("reason") && !r["reason"].is_null()) {
            std::string reason = r["reason"].get<std::string>();
            if (reason.find("MANUAL") != std::string::npos || reason.find("LOW CONFIDENCE") != std::string::npos) {
                flagged.push_back(r);
            }
        }
    }
    if (!flagged.empty()) {
        std::cout << "\n" << std::string(60, '!') << "\n";
        std::cout << "FLAGGED FOR MANUAL REVIEW (low-confidence orientation guess):\n";
        for (auto& r : flagged) {
            std::cout << "  - " << r["file"].get<std::string>() << ": " << r["reason"].get<std::string>() << "\n";
        }
        std::cout << std::string(60, '!') << "\n";
    }
}

}  // namespace

int main(int argc, char** argv) {
    if (argc < 2) {
        std::cerr << "Usage: " << argv[0] << " [<image_path>] <config_path> [output_dir]\n";
        return 1;
    }

    fs::path image_path, config_path, output_dir;
    
    std::string arg1 = argv[1];
    // If the first argument is a json file, assume it's the config
    if (arg1.size() > 5 && arg1.substr(arg1.size() - 5) == ".json") {
        config_path = fs::absolute(arg1);
        output_dir = argc > 2 ? fs::absolute(argv[2]) : fs::current_path() / "results";
    } else {
        if (argc < 3) {
            std::cerr << "Usage: " << argv[0] << " <image_path> <config_path> [output_dir]\n";
            return 1;
        }
        image_path = fs::absolute(argv[1]);
        config_path = fs::absolute(argv[2]);
        output_dir = argc > 3 ? fs::absolute(argv[3]) : fs::current_path() / "results";
    }

    if (!fs::exists(config_path)) {
        std::cerr << "Error: Config not found at " << config_path << "\n";
        return 1;
    }

    // Parse config to find model_path and potentially image_path
    std::ifstream cfg_file(config_path);
    json cfg_json;
    if (cfg_file) {
        cfg_file >> cfg_json;
    }

    if (image_path.empty() && cfg_json.contains("image_path")) {
        image_path = fs::absolute(cfg_json["image_path"].get<std::string>());
    }

    if (image_path.empty() || !fs::exists(image_path)) {
        std::cerr << "Error: Image not found at " << image_path << "\n";
        return 1;
    }

    // Stage binaries and model are expected alongside this executable.
    fs::path bin_dir = fs::canonical(fs::path(argv[0])).parent_path();
    // Create a unique temporary workspace
    std::srand(static_cast<unsigned>(std::time(nullptr)));
    std::error_code ec;
    fs::path work_dir;
    do {
        work_dir = fs::temp_directory_path() / ("exam_run_" + std::to_string(std::time(nullptr)) + "_" + std::to_string(std::rand()));
    } while (fs::exists(work_dir));
    
    fs::create_directories(work_dir / "input", ec);

    // Copy files into workspace
    fs::copy_file(image_path, work_dir / "input" / image_path.filename(), fs::copy_options::overwrite_existing, ec);
    fs::copy_file(config_path, work_dir / "exam_config.json", fs::copy_options::overwrite_existing, ec);

    // Use previously parsed config to find model_path
    std::string model_rel = cfg_json.value("model_path", "shamel.onnx");
    fs::path model_p(model_rel);

    if (!model_p.is_absolute()) {
        fs::path model_src = bin_dir / model_p;
        if (!fs::exists(model_src) && fs::exists(bin_dir.parent_path() / model_p)) {
            model_src = bin_dir.parent_path() / model_p;
        }
        if (fs::exists(model_src)) {
            fs::copy_file(model_src, work_dir / model_p, fs::copy_options::overwrite_existing, ec);
        } else {
            std::cerr << "Warning: model " << model_p << " not found alongside executable.\n";
        }
    }

    std::cout << "Created temporary workspace at: " << work_dir << "\n";

    for (const auto& s : STAGES) {
        run_stage(bin_dir, s, work_dir);
        
        // If preprocessing failed in stage 1, skip straight to scoring to generate the final JSON
        if (s == "stage1_boxes" && fs::exists(work_dir / "preprocessing_result.json")) {
            run_stage(bin_dir, "stage6_scoring", work_dir);
            break;
        }
    }
    print_flags(work_dir);

    // Copy results
    fs::create_directories(output_dir, ec);
    fs::path final_output = output_dir / image_path.stem();
    fs::create_directories(final_output, ec);
    
    // Copy all stage folders
    for (const auto& entry : fs::directory_iterator(work_dir)) {
        if (entry.is_directory() && entry.path().filename().string().find("stage") == 0) {
            fs::copy(entry.path(), final_output / entry.path().filename(), fs::copy_options::recursive | fs::copy_options::overwrite_existing, ec);
        }
    }

    std::cout << "\nDone. Results saved to: " << final_output << "\n";
    
    // Cleanup
    fs::remove_all(work_dir, ec);
    return 0;
}
