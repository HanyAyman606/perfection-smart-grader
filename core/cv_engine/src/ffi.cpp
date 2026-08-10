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
        result["status"] = "SUCCESS";
        result["confidence"] = quality.is_ok ? "OK" : "LOW";
        result["warnings"] = quality.warnings;
        result["orientation_reason"] = quality.orientation_reason;
        result["blur_score"] = quality.blur_score;
        result["id_panel_blur_score"] = quality.id_panel_blur_score;
        result["mcq_panel_blur_score"] = quality.mcq_panel_blur_score;
        result["num_quad_candidates"] = quality.num_quad_candidates;

        // Early exit: if the image quality is bad (e.g., BLUR or NO_ID_PANEL),
        // skip the heavy ONNX inference completely to save CPU!
        if (!quality.is_ok) {
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
            if (q.state == "ERROR_MISSING") final_result.has_missing_rows = true;
        }
        for (const auto& c : final_result.id_columns) {
            if (c.state == "ERROR_MISSING") final_result.has_missing_rows = true;
        }

        // Merge inference results into the final payload
        nlohmann::json inference_json = final_result.to_json_obj();
        result.update(inference_json);

        return allocate_string(result.dump());
    } catch (const std::exception& e) {
        std::cerr << "Error in process_exam_in_memory: " << e.what() << std::endl;
        nlohmann::json err;
        err["status"] = "ERROR";
        err["message"] = e.what();
        err["confidence"] = "LOW";
        err["warnings"] = nlohmann::json::array({"PROCESSING_ERROR"});
        return allocate_string(err.dump());
    }
}

void free_string(char* str) {
    if (str) {
        delete[] str;
    }
}

} // extern "C"