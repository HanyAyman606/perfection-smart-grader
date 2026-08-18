#pragma once

#include <string>
#include <vector>
#include <optional>
#include <variant>
#include <iostream>
#include <nlohmann/json.hpp>

constexpr int CLASS_EMPTY    = 0;
constexpr int CLASS_FILLED   = 1;
constexpr int CLASS_CANCELED = 2;

inline std::string get_class_name(int class_id) {
    switch (class_id) {
        case CLASS_EMPTY:    return "empty";
        case CLASS_FILLED:   return "filled";
        case CLASS_CANCELED: return "canceled";
        default:             return "unknown";
    }
}

const std::string STATE_BLANK                     = "BLANK";
const std::string STATE_ANSWERED                  = "ANSWERED";
const std::string STATE_MULTIPLE                  = "MULTIPLE";
const std::string STATE_ERROR_MISSING             = "ERROR_MISSING";
const std::string STATE_QUESTIONS_NUMBER_MISMATCH = "QUESTIONS_NUMBER_MISMATCH";
const std::string STATE_FAILED                    = "FAILED";

struct Detection {
    float cx, cy, w, h, confidence;
    int class_id;

    bool is_filled()  const { return class_id == CLASS_FILLED; }
    bool is_empty()   const { return class_id == CLASS_EMPTY || class_id == CLASS_CANCELED; }
    std::string class_name() const { return get_class_name(class_id); }
};

using AnswerVariant = std::variant<std::monostate, std::string, std::vector<std::string>>;

struct QuestionResult {
    int question_number;
    std::string state;
    AnswerVariant answer;
    float answer_confidence = 1.0f;  // min confidence of detected answer (0-1)

    nlohmann::json to_json() const {
        nlohmann::json j;
        j["question_number"] = question_number;
        j["state"] = state;
        j["answer_confidence"] = answer_confidence;
        if      (std::holds_alternative<std::string>(answer))              j["answer"] = std::get<std::string>(answer);
        else if (std::holds_alternative<std::vector<std::string>>(answer)) j["answer"] = std::get<std::vector<std::string>>(answer);
        else                                                               j["answer"] = nullptr;
        return j;
    }
};

struct IDColumnResult {
    std::string column_label;
    std::string state;
    AnswerVariant answer;
    float answer_confidence = 1.0f;  // min confidence of detected answer (0-1)

    nlohmann::json to_json() const {
        nlohmann::json j;
        j["column_label"] = column_label;
        j["state"] = state;
        j["answer_confidence"] = answer_confidence;
        if      (std::holds_alternative<std::string>(answer))              j["answer"] = std::get<std::string>(answer);
        else if (std::holds_alternative<std::vector<std::string>>(answer)) j["answer"] = std::get<std::vector<std::string>>(answer);
        else                                                               j["answer"] = nullptr;
        return j;
    }
};

// Result for the shamel version panel (exam model + exam day).
struct VersionResult {
    std::string exam_model_state = STATE_ERROR_MISSING;
    std::optional<std::string> exam_model;
    float exam_model_confidence = 1.0f;  // min confidence of detected answer (0-1)
    std::string exam_day_state   = STATE_ERROR_MISSING;
    std::optional<std::string> exam_day;
    float exam_day_confidence = 1.0f;  // min confidence of detected answer (0-1)

    nlohmann::json to_json() const {
        nlohmann::json j;
        j["exam_model"] = exam_model.has_value() ? nlohmann::json(exam_model.value()) : nlohmann::json(nullptr);
        j["exam_model_state"] = exam_model_state;
        j["exam_model_confidence"] = exam_model_confidence;
        j["exam_day"]   = exam_day.has_value()   ? nlohmann::json(exam_day.value())   : nlohmann::json(nullptr);
        j["exam_day_state"] = exam_day_state;
        j["exam_day_confidence"] = exam_day_confidence;
        return j;
    }
};

struct CorrectionResult {
    std::string                  status = "SUCCESS";
    bool                         mcq_split_failed = false;
    std::vector<QuestionResult>  questions;
    std::vector<IDColumnResult>  id_columns;
    std::optional<std::string>   student_id_string;
    std::optional<std::string>   student_id_letter;
    std::optional<VersionResult> version;
    std::optional<std::string>   annotated_id_image_path;
    std::optional<std::string>   annotated_mcq_image_path;
    std::vector<std::string>     annotated_mcq_region_paths;
    std::optional<std::string>   annotated_version_image_path;

    void update_status() {
        // Stage 1/2: Fatal Pipeline Errors
        if (status == STATE_FAILED || status == "ERROR_BLURRY" || status == "ERROR_EXPOSURE" || status == "ERROR_GEOMETRY" || mcq_split_failed) {
            // Keep the exact early-exit status if it was set explicitly, else generic FAILED
            if (status != "ERROR_BLURRY" && status != "ERROR_EXPOSURE" && status != "ERROR_GEOMETRY") {
                status = STATE_FAILED;
            }
            return;
        }

        bool has_missing = false;
        bool has_number_mismatch = false;
        int low_confidence_count = 0;
        const float LOW_CONFIDENCE_THRESHOLD = 0.55f;

        for (const auto& q : questions) {
            if (q.state == STATE_QUESTIONS_NUMBER_MISMATCH) has_number_mismatch = true;
            else if (q.state == STATE_ERROR_MISSING) has_missing = true;
            else if (q.state == STATE_ANSWERED || q.state == STATE_MULTIPLE || q.state == STATE_BLANK) {
                if (q.answer_confidence < LOW_CONFIDENCE_THRESHOLD) {
                    low_confidence_count++;
                }
            }
        }
        for (const auto& c : id_columns) {
            if (c.state == STATE_ERROR_MISSING) has_missing = true;
            else if (c.state == STATE_ANSWERED || c.state == STATE_MULTIPLE || c.state == STATE_BLANK) {
                if (c.answer_confidence < LOW_CONFIDENCE_THRESHOLD) {
                    low_confidence_count++;
                }
            }
        }
        if (version) {
            if (version->exam_model_state == STATE_ERROR_MISSING) has_missing = true;
            else if (version->exam_model_state == STATE_ANSWERED || version->exam_model_state == STATE_MULTIPLE) {
                if (version->exam_model_confidence < LOW_CONFIDENCE_THRESHOLD) {
                    low_confidence_count++;
                }
            }

            if (version->exam_day_state == STATE_ERROR_MISSING) has_missing = true;
            else if (version->exam_day_state == STATE_ANSWERED || version->exam_day_state == STATE_MULTIPLE) {
                if (version->exam_day_confidence < LOW_CONFIDENCE_THRESHOLD) {
                    low_confidence_count++;
                }
            }
        }

        // Stage 3: Number Mismatch
        if (has_number_mismatch) {
            status = STATE_QUESTIONS_NUMBER_MISMATCH;
            return;
        }
        
        // Stage 4: Missing Elements
        if (has_missing) {
            status = "ERROR_MISSING";
            return;
        }
        
        // Stage 5: Low Confidence / Retake
        if (low_confidence_count > 3) {
            status = "RETAKE";
            return;
        } else if (low_confidence_count > 0) {
            status = "REVIEW_NEEDED";
            return;
        }

        // Stage 6: Success
        status = "SUCCESS";
    }

    nlohmann::json to_json_obj() const {
        nlohmann::json j;
        j["status"]            = status;
        j["student_id"]        = student_id_string.has_value() ? nlohmann::json(student_id_string.value()) : nlohmann::json(nullptr);
        j["student_id_letter"] = student_id_letter.has_value() ? nlohmann::json(student_id_letter.value()) : nlohmann::json(nullptr);

        nlohmann::json id_cols = nlohmann::json::array();
        for (const auto& c : id_columns) id_cols.push_back(c.to_json());
        j["id_columns"] = id_cols;

        nlohmann::json qs = nlohmann::json::array();
        for (const auto& q : questions) qs.push_back(q.to_json());
        j["questions"] = qs;

        if (version)                  j["version"]                      = version->to_json();
        if (annotated_id_image_path)  j["annotated_id_image_path"]      = annotated_id_image_path.value();
        if (annotated_mcq_image_path) j["annotated_mcq_image_path"]     = annotated_mcq_image_path.value();
        if (!annotated_mcq_region_paths.empty()) j["annotated_mcq_region_paths"] = annotated_mcq_region_paths;
        if (annotated_version_image_path) j["annotated_version_image_path"] = annotated_version_image_path.value();

        return j;
    }

    std::string to_json(int indent = 2) const {
        return to_json_obj().dump(indent);
    }
};