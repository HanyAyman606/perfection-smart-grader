#include "ffi.h"
#include "shamel_phases.h"
#include <nlohmann/json.hpp>
#include <iostream>
#include <fstream>
#include <sstream>
#include <filesystem>
#include <algorithm>

using json = nlohmann::json;

namespace {

std::string resolve_runtime_path(const std::string& raw_path) {
    if (raw_path.empty()) return raw_path;
    std::filesystem::path p(raw_path);
    if (p.is_absolute()) return raw_path;

    std::vector<std::filesystem::path> candidates;
    std::filesystem::path cwd = std::filesystem::current_path();
    candidates.push_back(cwd / p);

    for (std::filesystem::path parent = cwd; !parent.empty(); parent = parent.parent_path()) {
        candidates.push_back(parent / p);
        if (parent == parent.parent_path()) break;
    }

    for (const auto& candidate : candidates) {
        std::error_code ec;
        if (std::filesystem::exists(candidate, ec) && std::filesystem::is_regular_file(candidate, ec)) {
            return std::filesystem::weakly_canonical(candidate).string();
        }
    }

    return raw_path;
}

} // namespace

int main(int argc, char** argv) {
    std::vector<std::string> global_errors;

    if (argc < 3) {
        std::cerr << "Usage: " << argv[0] << " <image_path> <config_json> [--mode quiz|shamel]" << std::endl;
        return 1;
    }

    std::string image_path = argv[1];
    std::string config_json_path = argv[2];
    std::string mode = "quiz";  // default to the standard quiz pipeline

    std::ifstream t(config_json_path);
    std::stringstream buffer;
    buffer << t.rdbuf();
    std::string config_json_str = buffer.str();

    json config = json::parse(config_json_str);
    if (config.contains("mode") && config["mode"].is_string()) {
        mode = config["mode"].get<std::string>();
    }

    if (argc > 3) {
        std::string mode_arg = argv[3];
        if (mode_arg == "--mode" && argc > 4) {
            mode = argv[4];
        }
    }
    if (config.contains("model_path")) {
        config["model_path"] = resolve_runtime_path(config["model_path"].get<std::string>());
    }
    if (config.contains("output_dir")) {
        std::string output_val = config["output_dir"].get<std::string>();
        if (!std::filesystem::path(output_val).is_absolute()) {
            std::filesystem::path abs_out = std::filesystem::weakly_canonical(std::filesystem::current_path() / output_val);
            config["output_dir"] = abs_out.string();
        }
    }
    config_json_str = config.dump();

    // Determine output directory
    std::string output_dir = "./output";
    if (config.contains("output_dir")) {
        output_dir = config["output_dir"];
    }

    std::cout << "==========================================\n";
    if (mode == "shamel") {
        std::cout << " SHAMEL PIPELINE: 5-PHASE FLOW\n";
    } else {
        std::cout << " QUIZ PIPELINE: TWO-PANEL FLOW\n";
    }
    std::cout << "==========================================\n";

    try {
        if (mode == "shamel") {
            // ─────────────────────────────────────────────────────────────────────
            // PHASE 1: Blur Detection
            // ─────────────────────────────────────────────────────────────────────
            std::cout << "\n[*] Running PHASE 1: Blur Detection...\n";
            auto phase1 = shamel_phases::phase1_blur_detection(image_path, output_dir);
            
            json phase1_json;
            phase1_json["phase"] = 1;
            phase1_json["is_ok"] = phase1.is_ok;
            phase1_json["blur_score"] = phase1.blur_score;
            phase1_json["output_path"] = phase1.output_path;
            
            std::cout << "[+] Phase 1 Success: blur_score=" << phase1.blur_score << std::endl;
            
            if (!phase1.is_ok) {
                std::cerr << "[!] Phase 1 Failed: Image is too blurry." << std::endl;
                std::cout << phase1_json.dump(2) << std::endl;
                return 1;
            }
            
            // ─────────────────────────────────────────────────────────────────────
            // PHASE 2: Frame Detection
            // ─────────────────────────────────────────────────────────────────────
            std::cout << "\n[*] Running PHASE 2: Frame Detection...\n";
            cv::Mat img = cv::imread(image_path);
            auto phase2 = shamel_phases::phase2_frame_detection(image_path, output_dir);
            
            json phase2_json;
            phase2_json["phase"] = 2;
            phase2_json["status"] = phase2.status;
            phase2_json["confidence_score"] = phase2.confidence_score;
            phase2_json["errors"] = phase2.errors;
            phase2_json["retake_reasons"] = phase2.retake_reasons;
            
            std::cout << "[+] Phase 2 Status: " << phase2.status << std::endl;
            
            // Save annotated Phase 2 output for debugging
            shamel_phases::draw_and_save_quads_annotated(img, phase2, image_path, output_dir);
            
            if (phase2.status == "RETAKE") {
                std::cerr << "[!] Phase 2 RETAKE: ";
                for (const auto& reason : phase2.retake_reasons) {
                    std::cerr << reason << "; ";
                }
                std::cerr << std::endl;
                std::cout << phase2_json.dump(2) << std::endl;
                return 1;
            }
            
            // ─────────────────────────────────────────────────────────────────────
            // PHASE 3: Perspective Warping
            // ─────────────────────────────────────────────────────────────────────
            std::cout << "\n[*] Running PHASE 3: Perspective Warping...\n";
            auto phase3 = shamel_phases::phase3_perspective_warp(img, phase2, image_path, output_dir);
            
            json phase3_json;
            phase3_json["phase"] = 3;
            if (!phase3.id_path.empty()) phase3_json["id_path"] = phase3.id_path;
            if (!phase3.version_path.empty()) phase3_json["version_path"] = phase3.version_path;
            if (!phase3.mcq_path.empty()) phase3_json["mcq_path"] = phase3.mcq_path;
            
            std::cout << "[+] Phase 3 Success: panels warped\n";
            
            // ─────────────────────────────────────────────────────────────────────
            // Phase 3.5: Split MCQ into 6 regions
            // ─────────────────────────────────────────────────────────────────────
            std::cout << "\n[*] Splitting MCQ into 6 regions...\n";
            auto mcq_regions = shamel_phases::split_mcq_into_regions(img, phase2, image_path, output_dir);
            
            json mcq_regions_json = json::array();
            for (const auto& path : mcq_regions.region_paths) {
                mcq_regions_json.push_back(path);
            }
            phase3_json["mcq_regions"] = mcq_regions_json;
            
            std::cout << "[+] MCQ split into " << mcq_regions.regions.size() << " regions\n";
            
            // Validate MCQ split geometry (divider lines must have produced a
            // balanced 6-region grid). Question-count validation is no longer done
            // here with a circle-counting heuristic - Phase 5 runs the actual
            // trained bubble-detection model on these regions, so that's the
            // authoritative source for count mismatches (checked below).
            int expected_questions = config.contains("num_questions") ?
                                     config["num_questions"].get<int>() : 44;
            auto mcq_validation = shamel_phases::validate_mcq_regions(mcq_regions, expected_questions);
            
            if (!mcq_validation.is_valid) {
                std::cout << "[!] MCQ split geometry failed: " << mcq_validation.error_message << "\n";
                phase3_json["mcq_validation_status"] = "FAILED";
                phase3_json["mcq_validation_error"] = mcq_validation.error_message;
                global_errors.push_back("MCQ_SPLIT_GEOMETRY_FAILED");
            } else {
                phase3_json["mcq_validation_status"] = "OK";
                std::cout << "[+] MCQ split geometry OK\n";
            }
            
            // ─────────────────────────────────────────────────────────────────────
            // PHASE 4: YOLO Letterbox Preparation
            // ─────────────────────────────────────────────────────────────────────
            std::cout << "\n[*] Running PHASE 4: YOLO Letterbox Preparation...\n";
            auto phase4 = shamel_phases::phase4_yolo_letterbox(phase3, image_path, output_dir);
            
            json phase4_json;
            phase4_json["phase"] = 4;
            if (!phase4.id_yolo_path.empty()) phase4_json["id_yolo_path"] = phase4.id_yolo_path;
            if (!phase4.version_yolo_path.empty()) phase4_json["version_yolo_path"] = phase4.version_yolo_path;
            if (!phase4.mcq_yolo_path.empty()) phase4_json["mcq_yolo_path"] = phase4.mcq_yolo_path;
            
            json mcq_region_yolo_paths = json::array();
            for (const auto& path : phase4.mcq_region_yolo_paths) {
                mcq_region_yolo_paths.push_back(path);
            }
            if (!phase4.mcq_region_yolo_paths.empty()) {
                phase4_json["mcq_region_yolo_paths"] = mcq_region_yolo_paths;
            }
            
            std::cout << "[+] Phase 4 Success: YOLO frames ready\n";
            
            // ─────────────────────────────────────────────────────────────────────
            // PHASE 5: ONNX Inference (deferred to ffi.h step2_infer_and_score)
            // ─────────────────────────────────────────────────────────────────────
            std::cout << "\n[*] Running PHASE 5: ONNX Inference...\n";
            
            // Build step1 result for compatibility with existing inference pipeline
            json step1_result;
            step1_result["status"] = "SUCCESS";
            step1_result["mode"] = "shamel";
            if (!phase3.id_path.empty()) step1_result["id_panel_path"] = phase3.id_path;
            if (!phase3.version_path.empty()) step1_result["version_panel_path"] = phase3.version_path;
            if (!phase3.mcq_path.empty()) step1_result["mcq_panel_path"] = phase3.mcq_path;
            step1_result["mcq_regions"] = mcq_regions_json;
            // A genuine split-geometry failure (garbage region crops) is the only
            // way validate_mcq_regions() reports invalid now, so it's correct to
            // forward it as a hard "FAILED" into step2 and skip trusting anything
            // computed from those crops.
            bool mcq_split_geometry_failed =
                mcq_regions.regions.empty() || !mcq_validation.is_valid;
            step1_result["mcq_validation_status"] = mcq_split_geometry_failed ? "FAILED" : "OK";
            
            const char* step2_res_cstr = step2_infer_and_score(
                step1_result.dump().c_str(), config_json_str.c_str());
            if (!step2_res_cstr) {
                std::cerr << "Error: step2_infer_and_score returned null" << std::endl;
                return 1;
            }
            
            std::string step2_res = step2_res_cstr;
            free_string(const_cast<char*>(step2_res_cstr));

            json phase5_json = json::parse(step2_res);
            // mcq_split_failed here is purely informational metadata describing whether
            // the perpendicular-divider split itself was structurally broken
            // (MCQ_SPLIT_GEOMETRY_FAILED). It must not clobber phase5_json["status"]:
            // a genuine geometry failure was already forwarded as authoritative into
            // step2 above (and step2 sets its own status accordingly); a mere
            // QUESTIONS_NUMBER_MISMATCH pre-check miss is not a split failure and the
            // real status already reflects the actual per-question inference results.
            phase5_json["mcq_split_failed"] = mcq_split_geometry_failed;

            // ─────────────────────────────────────────────────────────────────────
            // Real question-count mismatch check: this is the authoritative signal,
            // based on actual per-cell YOLO detections rather than the Hough-circle
            // pre-count. If process_questions()/classify_column() couldn't find a
            // detection where the declared layout expected one, it marks that cell
            // QUESTIONS_NUMBER_MISMATCH (see questions.cpp) - collect any of those
            // here so a real mismatch still surfaces in the overall status even
            // though the noisy Phase 3 heuristic no longer does.
            // ─────────────────────────────────────────────────────────────────────
            bool real_question_mismatch = false;
            if (phase5_json.contains("questions") && phase5_json["questions"].is_array()) {
                for (const auto& q : phase5_json["questions"]) {
                    if (q.contains("state") && q["state"] == "QUESTIONS_NUMBER_MISMATCH") {
                        real_question_mismatch = true;
                        break;
                    }
                }
            }
            if (!real_question_mismatch && phase5_json.contains("id_columns") && phase5_json["id_columns"].is_array()) {
                for (const auto& c : phase5_json["id_columns"]) {
                    if (c.contains("state") && c["state"] == "QUESTIONS_NUMBER_MISMATCH") {
                        real_question_mismatch = true;
                        break;
                    }
                }
            }
            if (real_question_mismatch) {
                global_errors.push_back("QUESTIONS_NUMBER_MISMATCH");
            }

            // ─────────────────────────────────────────────────────────────────────
            // Final Summary
            // ─────────────────────────────────────────────────────────────────────
            json final_result;
            final_result["phase_1"] = phase1_json;
            final_result["phase_2"] = phase2_json;
            final_result["phase_3"] = phase3_json;
            final_result["phase_4"] = phase4_json;
            final_result["phase_5"] = phase5_json;

            // ─────────────────────────────────────────────────────────────────────
            // Overall status: fold in errors collected across phases (in particular
            // MCQ_SPLIT_VALIDATION_FAILED from the Phase 3 region-count/geometry
            // check) instead of silently discarding them. Phase 5's own per-question
            // STATE_QUESTIONS_NUMBER_MISMATCH does NOT catch a wrong config
            // num_questions value on a fixed physical template (bubbles exist at
            // every row regardless of the configured total), so this check must
            // stay authoritative rather than being treated as a redundant heuristic.
            // ─────────────────────────────────────────────────────────────────────
            final_result["errors"] = global_errors;
            if (global_errors.empty()) {
                final_result["status"] = "SUCCESS";
            } else if (std::find(global_errors.begin(), global_errors.end(), "MCQ_SPLIT_GEOMETRY_FAILED") != global_errors.end()) {
                final_result["status"] = "MCQ_SPLIT_GEOMETRY_FAILED";
            } else {
                final_result["status"] = "QUESTIONS_NUMBER_MISMATCH";
            }

            // Add debugging information
            json debug_info;
            debug_info["image_input"] = image_path;
            debug_info["output_directory"] = output_dir;
            debug_info["mode"] = "shamel";
            debug_info["annotations"] = json::array();
            
            // List all annotated images created
            if (phase5_json.contains("results")) {
                if (phase5_json["results"].contains("id") && phase5_json["results"]["id"].contains("annotated_image")) {
                    debug_info["annotations"].push_back({
                        {"type", "id_panel"},
                        {"path", phase5_json["results"]["id"]["annotated_image"]}
                    });
                }
                if (phase5_json["results"].contains("version") && phase5_json["results"]["version"].contains("annotated_image")) {
                    debug_info["annotations"].push_back({
                        {"type", "version_panel"},
                        {"path", phase5_json["results"]["version"]["annotated_image"]}
                    });
                }
                if (phase5_json["results"].contains("mcq_regions")) {
                    auto& mcq_regions = phase5_json["results"]["mcq_regions"];
                    if (mcq_regions.is_object()) {
                        for (auto& [region_name, region_data] : mcq_regions.items()) {
                            if (region_data.contains("annotated_image")) {
                                debug_info["annotations"].push_back({
                                    {"type", "mcq_region_" + region_name},
                                    {"path", region_data["annotated_image"]}
                                });
                            }
                        }
                    }
                }
            }
            
            final_result["debug"] = debug_info;
            
            bool pipeline_ok = final_result["status"] == "SUCCESS";

            std::cout << "\n==========================================\n";
            if (pipeline_ok) {
                std::cout << " SUCCESS! PIPELINE COMPLETE\n";
            } else {
                std::cout << " PIPELINE COMPLETE WITH ERRORS: "
                           << final_result["status"].get<std::string>() << "\n";
                for (const auto& err : global_errors) {
                    std::cerr << "[!] " << err << std::endl;
                }
            }
            std::cout << "==========================================\n";
            std::cout << "\n[DEBUG IMAGES CREATED]:\n";
            for (const auto& ann : debug_info["annotations"]) {
                std::cout << "  • " << ann["type"].get<std::string>() << ": " 
                    << ann["path"].get<std::string>() << std::endl;
            }
            std::cout << "\n[OUTPUT DIRECTORY]: " << output_dir << std::endl;
            std::cout << "\nFull JSON output:\n" << final_result.dump(2) << std::endl;

            if (!pipeline_ok) return 2;

        } else {
            // ─────────────────────────────────────────────────────────────────────
            // QUIZ MODE: Use existing two-panel logic
            // ─────────────────────────────────────────────────────────────────────
            std::cout << "\n[*] Using QUIZ mode (two-panel pipeline)...\n";
            
            const char* step1_res_cstr = step1_extract_panels(image_path.c_str(), config_json_str.c_str());
            if (!step1_res_cstr) {
                std::cerr << "Error: step1_extract_panels returned null" << std::endl;
                return 1;
            }

            std::string step1_res = step1_res_cstr;
            free_string(const_cast<char*>(step1_res_cstr));

            json s1_json = json::parse(step1_res);
            std::string s1_status = s1_json.value("status", "");

            if (s1_status == "RETAKE") {
                // Terminal output = exactly the payload Flutter receives from
                // step1_extract_panels. "cause" is always present on it:
                // for RETAKE it's the retake reason.
                std::cout << s1_json.dump(2) << std::endl;
                return 3; // distinct exit code so callers can special-case "please retake" vs a hard error
            }
            if (s1_status != "SUCCESS") {
                std::cout << s1_json.dump(2) << std::endl;
                return 1;
            }

            const char* step2_res_cstr = step2_infer_and_score(step1_res.c_str(), config_json_str.c_str());
            if (!step2_res_cstr) {
                std::cerr << "Error: step2_infer_and_score returned null" << std::endl;
                return 1;
            }

            std::string step2_res = step2_res_cstr;
            free_string(const_cast<char*>(step2_res_cstr));

            json s2_json = json::parse(step2_res);
            std::string s2_status = s2_json.value("status", "");

            // Terminal output = exactly the payload Flutter receives from
            // step2_infer_and_score. "cause" is always present on it:
            //   SUCCESS                    -> cause: null
            //   RETAKE                     -> cause: the retake reason
            //   QUESTIONS_NUMBER_MISMATCH  -> cause: the mismatched questions/ID columns
            std::cout << s2_json.dump(2) << std::endl;

            if (s2_status == "RETAKE") {
                return 3;
            }
        }

    } catch (const std::exception& e) {
        std::cerr << "Error: " << e.what() << std::endl;
        return 1;
    }

    return 0;
}