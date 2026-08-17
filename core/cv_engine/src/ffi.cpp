#include "ffi.h"
#include "preprocessing.h"
#include "inference.h"
#include "questions.h"
#include "config.h"
#include "result.h"
#include <nlohmann/json.hpp>
#include <iostream>
#include <cstring>
#include <vector>
#include <opencv2/opencv.hpp>

static char* allocate_string(const std::string& str) {
    char* cstr = new char[str.length() + 1];
    std::strcpy(cstr, str.c_str());
    return cstr;
}

static void finalize_cause(nlohmann::json& out, const nlohmann::json* explicit_cause = nullptr) {
    if (explicit_cause) {
        out["cause"] = *explicit_cause;
        return;
    }

    const std::string status = out.value("status", "");
    if (status == "SUCCESS") {
        out["cause"] = nullptr;
        return;
    }

    nlohmann::json flagged = nlohmann::json::array();
    
    bool want_mismatch = (status == "QUESTIONS_NUMBER_MISMATCH");
    bool want_review = (status == "REVIEW_NEEDED");
    
    if (want_mismatch || want_review) {
        auto check_and_add = [&](const nlohmann::json& item, bool is_id) {
            if (!item.contains("state")) return;
            std::string state = item["state"];
            
            if (want_mismatch && state == "QUESTIONS_NUMBER_MISMATCH") {
                flagged.push_back(item);
            } else if (want_review) {
                float conf = item.value("answer_confidence", 1.0f);
                if ((state == "ANSWERED" || state == "MULTIPLE" || state == "BLANK") && conf < 0.55f) {
                    flagged.push_back(item);
                }
            }
        };

        if (out.contains("questions") && out["questions"].is_array()) {
            for (const auto& q : out["questions"]) check_and_add(q, false);
        }
        if (out.contains("id_columns") && out["id_columns"].is_array()) {
            for (const auto& c : out["id_columns"]) check_and_add(c, true);
        }
    }

    out["cause"] = flagged.empty() ? nlohmann::json(status) : flagged;
}

extern "C" {

const char* process_exam_in_memory(const char* image_path, const char* config_json_str) {
    try {
        nlohmann::json j = nlohmann::json::parse(config_json_str);
        ExamConfig config;
        from_json(j, config);

        // Step 1: In-memory panel extraction
        auto [id_final, mcq_final, quality] = preprocessing::prepare_from_raw(image_path, config);

        CorrectionResult final_result;
        
        // Pass step 1 signals through to the final payload
        nlohmann::json result = final_result.to_json_obj();
        
        // Early exit: if the image quality is bad (e.g., BLUR or NO_ID_PANEL),
        // skip the heavy ONNX inference completely to save CPU!
        if (!quality.is_ok) {
            result["status"] = "FAILED";
            nlohmann::json explicit_cause = quality.warnings.empty() ? nlohmann::json("UNKNOWN") : nlohmann::json(quality.warnings.front());
            finalize_cause(result, &explicit_cause);
            return allocate_string(result.dump());
        }

        // Step 2: Direct inference without writing images
        if (id_final) {
            std::vector<int> col_sizes;
            for (int i = 0; i < config.id.num_letters; ++i) col_sizes.push_back(config.id.letters.size());
            for (int i = 0; i < config.id.num_digits; ++i) col_sizes.push_back(10);
            
            auto id_dets = inference::run_inference_id_adaptive(*id_final, config.model_path, col_sizes, 0.25f, 0.10f, 0.45f);
            auto [id_cols, id_str, id_letter, id_review] = questions::process_student_id(id_dets, config.id.num_digits, config.id.num_letters, config.id.letters, config.id.column_labels(), config.row_tolerance_px);
            
            final_result.id_columns = id_cols;
            final_result.student_id_string = id_str;
            final_result.student_id_letter = id_letter;
            final_result.id_needs_review = id_review;
        }

        if (mcq_final) {
            auto mcq_dets = inference::run_inference_mcq_adaptive(*mcq_final, config.model_path, config.num_questions, config.num_question_columns, config.num_choices, 0.25f, 0.05f, 0.45f, config.mcq_column_sizes);
            final_result.questions = questions::process_questions(mcq_dets, config.num_questions, config.num_question_columns, config.choice_labels(), config.row_tolerance_px, config.mcq_column_sizes);
        }

        for (const auto& q : final_result.questions) {
            if (q.state == "QUESTIONS_NUMBER_MISMATCH") final_result.has_missing_rows = true;
        }
        for (const auto& c : final_result.id_columns) {
            if (c.state == "QUESTIONS_NUMBER_MISMATCH") final_result.has_missing_rows = true;
        }

        final_result.update_status(); // Compute overall status (RETAKE, MISMATCH, REVIEW_NEEDED, SUCCESS)

        // Merge inference results into the final payload
        nlohmann::json inference_json = final_result.to_json_obj();
        result.update(inference_json);
        
        finalize_cause(result);

        return allocate_string(result.dump());
    } catch (const std::exception& e) {
        std::cerr << "Error in process_exam_in_memory: " << e.what() << std::endl;
        nlohmann::json err;
        err["status"] = "FAILED";
        err["cause"] = "ERROR_INTERNAL";
        return allocate_string(err.dump());
    }
}

void free_string(char* str) {
    if (str) {
        delete[] str;
    }
}

} // extern "C"
