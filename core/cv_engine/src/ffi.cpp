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

static void draw_and_save_debug(cv::Mat& img, const std::vector<Detection>& dets, const std::string& path) {
    for (const auto& d : dets) {
        cv::Scalar color = d.is_filled() ? cv::Scalar(0, 255, 0) : cv::Scalar(0, 0, 255);
        if (d.class_id == CLASS_CANCELED) color = cv::Scalar(255, 0, 0); // blue for canceled
        cv::Point pt1(d.cx - d.w / 2, d.cy - d.h / 2);
        cv::Point pt2(d.cx + d.w / 2, d.cy + d.h / 2);
        cv::rectangle(img, pt1, pt2, color, 2);
        cv::putText(img, std::to_string(d.class_id), cv::Point(pt1.x, pt1.y - 5), cv::FONT_HERSHEY_SIMPLEX, 0.5, color, 1);
    }
    cv::imwrite(path, img);
}

static char* allocate_string(const std::string& str) {
    char* cstr = new char[str.length() + 1];
    std::strcpy(cstr, str.c_str());
    return cstr;
}

extern "C" {

const char* step1_extract_panels(const char* image_path, const char* config_json_str) {
    try {
        nlohmann::json j = nlohmann::json::parse(config_json_str);
        ExamConfig config;
        from_json(j, config);

        auto [id_final, mcq_final, quality] = preprocessing::prepare_from_raw(image_path);

        nlohmann::json result;
        result["status"] = "SUCCESS";

        if (id_final) {
            std::string id_path = std::string(image_path) + "_cropped_id.jpg";
            cv::imwrite(id_path, *id_final);
            result["id_panel_path"] = id_path;
        }

        if (mcq_final) {
            std::string mcq_path = std::string(image_path) + "_cropped_mcq.jpg";
            cv::imwrite(mcq_path, *mcq_final);
            result["mcq_panel_path"] = mcq_path;
        }

        // Confidence/quality signal for the frontend to decide retake vs.
        // preview-and-continue. "confidence" is the single field to branch
        // on; "warnings" gives the specific reason(s) for anything less
        // than a clean OK, so the UI can show something more actionable
        // than a generic "bad photo" message.
        result["confidence"] = quality.is_ok ? "OK" : "LOW";
        result["warnings"] = quality.warnings;
        result["orientation_reason"] = quality.orientation_reason;
        result["blur_score"] = quality.blur_score;
        result["id_panel_blur_score"] = quality.id_panel_blur_score;
        result["mcq_panel_blur_score"] = quality.mcq_panel_blur_score;
        result["num_quad_candidates"] = quality.num_quad_candidates;

        return allocate_string(result.dump());
    } catch (const std::exception& e) {
        std::cerr << "Error in step1_extract_panels: " << e.what() << std::endl;
        nlohmann::json err;
        err["status"] = "ERROR";
        err["message"] = e.what();
        err["confidence"] = "LOW";
        err["warnings"] = nlohmann::json::array({"PROCESSING_ERROR"});
        return allocate_string(err.dump());
    }
}

const char* step2_infer_and_score(const char* id_panel_path, const char* mcq_panel_path, const char* config_json_str) {
    try {
        nlohmann::json j = nlohmann::json::parse(config_json_str);
        ExamConfig config;
        from_json(j, config);

        std::cerr << "[ID_CONFIG_DEBUG] num_letters=" << config.id.num_letters
                  << " num_digits=" << config.id.num_digits
                  << " letters=[";
        for (auto& l : config.id.letters) std::cerr << l << ",";
        std::cerr << "]" << std::endl;
        std::cerr << "[RAW_CONFIG_JSON] " << config_json_str << std::endl;

        CorrectionResult final_result;

        if (id_panel_path && std::strlen(id_panel_path) > 0) {
            cv::Mat id_final = cv::imread(id_panel_path);
            if (!id_final.empty()) {
                std::vector<int> col_sizes;
                for (int i = 0; i < config.id.num_letters; ++i) col_sizes.push_back(config.id.letters.size());
                for (int i = 0; i < config.id.num_digits; ++i) col_sizes.push_back(10);
                
                auto id_dets = inference::run_inference_id_adaptive(id_final, config.model_path, col_sizes, 0.25f, 0.10f, 0.45f);
                auto [id_cols, id_str, id_letter, id_review] = questions::process_student_id(id_dets, config.id.num_digits, config.id.num_letters, config.id.letters, config.id.column_labels(), config.row_tolerance_px);
                
                final_result.id_columns = id_cols;
                final_result.student_id_string = id_str;
                final_result.student_id_letter = id_letter;
                final_result.id_needs_review = id_review;

                std::string annotated_path = std::string(id_panel_path) + "_annotated.jpg";
                draw_and_save_debug(id_final, id_dets, annotated_path);
                final_result.annotated_id_image_path = annotated_path;
            }
        }

        if (mcq_panel_path && std::strlen(mcq_panel_path) > 0) {
            cv::Mat mcq_final = cv::imread(mcq_panel_path);
            if (!mcq_final.empty()) {
                auto mcq_dets = inference::run_inference_mcq_adaptive(mcq_final, config.model_path, config.num_questions, config.num_question_columns, config.num_choices, 0.25f, 0.05f, 0.45f, config.mcq_column_sizes);
                final_result.questions = questions::process_questions(mcq_dets, config.num_questions, config.num_question_columns, config.choice_labels(), config.row_tolerance_px, config.mcq_column_sizes);
                
                std::string annotated_path = std::string(mcq_panel_path) + "_annotated.jpg";
                draw_and_save_debug(mcq_final, mcq_dets, annotated_path);
                final_result.annotated_mcq_image_path = annotated_path;
            }
        }

        for (const auto& q : final_result.questions) {
            if (q.state == "ERROR_MISSING") final_result.has_missing_rows = true;
        }
        for (const auto& c : final_result.id_columns) {
            if (c.state == "ERROR_MISSING") final_result.has_missing_rows = true;
        }

        return allocate_string(final_result.to_json());
    } catch (const std::exception& e) {
        std::cerr << "Error in step2_infer_and_score: " << e.what() << std::endl;
        nlohmann::json err;
        err["status"] = "ERROR";
        err["message"] = e.what();
        return allocate_string(err.dump());
    }
}

void free_string(char* str) {
    if (str) {
        delete[] str;
    }
}

} // extern "C"
