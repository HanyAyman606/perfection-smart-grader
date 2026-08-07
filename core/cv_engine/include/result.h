#pragma once

#include <string>
#include <vector>
#include <optional>
#include <variant>
#include <iostream>
#include <nlohmann/json.hpp>

constexpr int CLASS_EMPTY = 0;
constexpr int CLASS_FILLED = 1;
constexpr int CLASS_CANCELED = 2;

inline std::string get_class_name(int class_id) {
    switch (class_id) {
        case CLASS_EMPTY: return "empty";
        case CLASS_FILLED: return "filled";
        case CLASS_CANCELED: return "canceled";
        default: return "unknown";
    }
}

const std::string STATE_BLANK = "BLANK";
const std::string STATE_ANSWERED = "ANSWERED";
const std::string STATE_MULTIPLE = "MULTIPLE";

struct Detection {
    float cx;
    float cy;
    float w;
    float h;
    float confidence;
    int class_id;

    bool is_filled() const { return class_id == CLASS_FILLED; }
    bool is_empty() const { return class_id == CLASS_EMPTY || class_id == CLASS_CANCELED; }
    std::string class_name() const { return get_class_name(class_id); }
};

using AnswerVariant = std::variant<std::monostate, std::string, std::vector<std::string>>;

struct QuestionResult {
    int question_number;
    std::string state;
    AnswerVariant answer;

    nlohmann::json to_json() const {
        nlohmann::json j;
        j["question_number"] = question_number;
        j["state"] = state;
        if (std::holds_alternative<std::string>(answer)) j["answer"] = std::get<std::string>(answer);
        else if (std::holds_alternative<std::vector<std::string>>(answer)) j["answer"] = std::get<std::vector<std::string>>(answer);
        else j["answer"] = nullptr;
        return j;
    }
};

struct IDColumnResult {
    std::string column_label;
    std::string state;
    AnswerVariant answer;

    nlohmann::json to_json() const {
        nlohmann::json j;
        j["column_label"] = column_label;
        j["state"] = state;
        if (std::holds_alternative<std::string>(answer)) j["answer"] = std::get<std::string>(answer);
        else if (std::holds_alternative<std::vector<std::string>>(answer)) j["answer"] = std::get<std::vector<std::string>>(answer);
        else j["answer"] = nullptr;
        return j;
    }
};

struct CorrectionResult {
    std::vector<QuestionResult> questions;
    std::vector<IDColumnResult> id_columns;
    std::optional<std::string> student_id_string;
    std::optional<std::string> student_id_letter;
    std::optional<std::string> annotated_id_image_path;
    std::optional<std::string> annotated_mcq_image_path;

    nlohmann::json to_json_obj() const {
        nlohmann::json j;
        j["student_id"] = student_id_string.has_value() ? nlohmann::json(student_id_string.value()) : nlohmann::json(nullptr);
        j["student_id_letter"] = student_id_letter.has_value() ? nlohmann::json(student_id_letter.value()) : nlohmann::json(nullptr);
        nlohmann::json id_cols = nlohmann::json::array();
        for (const auto& c : id_columns) id_cols.push_back(c.to_json());
        j["id_columns"] = id_cols;
        nlohmann::json qs = nlohmann::json::array();
        for (const auto& q : questions) qs.push_back(q.to_json());
        j["questions"] = qs;
        if (annotated_id_image_path) j["annotated_id_image_path"] = annotated_id_image_path.value();
        if (annotated_mcq_image_path) j["annotated_mcq_image_path"] = annotated_mcq_image_path.value();
        return j;
    }

    std::string to_json(int indent = 2) const {
        return to_json_obj().dump(indent);
    }
};
