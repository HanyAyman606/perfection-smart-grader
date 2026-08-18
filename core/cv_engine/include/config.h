#pragma once

#include <string>
#include <vector>
#include <map>
#include <stdexcept>
#include <nlohmann/json.hpp>

struct IDConfig {
    int num_digits = 0;
    int num_letters = 0;
    std::vector<std::string> letters;

    std::vector<std::string> column_labels() const {
        std::vector<std::string> labels;
        for (int i = 0; i < num_letters; ++i) {
            labels.push_back(num_letters == 1 ? "LETTER" : "LETTER_" + std::to_string(i + 1));
        }
        for (int i = 0; i < num_digits; ++i) {
            labels.push_back("DIGIT_" + std::to_string(i + 1));
        }
        return labels;
    }

    int total_columns() const {
        return num_letters + num_digits;
    }
};

// Version panel config (shamel mode only).
// exam_models: labels for each bubble in the first row (e.g. ["A","B","C","D"])
// exam_days:   labels for each bubble in the second row (e.g. ["1","2","3"])
struct VersionConfig {
    std::vector<std::string> exam_models;
    std::vector<std::string> exam_days;
};

// MCQ columns configuration (quiz mode).
// Specifies the number of questions per column for flexible layouts.
// Example: 3 columns with [5, 5, 6] questions = 16 total
struct MCQColumnsConfig {
    int num_cols = 1;  // number of columns
    std::map<std::string, int> columns;  // column_id -> question_count mapping
    
    int total_questions() const {
        int total = 0;
        for (const auto& [_, count] : columns) {
            total += count;
        }
        return total;
    }
    
    // Get question count for a specific column (1-indexed)
    int get_column_questions(int col_idx) const {
        std::string key = std::to_string(col_idx);
        auto it = columns.find(key);
        return it != columns.end() ? it->second : 0;
    }
};

struct ExamConfig {
    std::string mode = "quiz"; // "quiz" or "shamel"
    std::string model_path;
    int num_questions = 0;
    int num_choices = 0;
    int num_question_columns = 1; // quiz only (legacy, for backward compatibility)
    int row_tolerance_px = 15;
    IDConfig id;
    VersionConfig version; // shamel only
    MCQColumnsConfig mcq_columns; // quiz only (new per-column layout)

    std::vector<std::string> choice_labels() const {
        std::vector<std::string> labels;
        for (int i = 0; i < num_choices; ++i) {
            labels.push_back(std::string(1, 'A' + i));
        }
        return labels;
    }
};

inline void from_json(const nlohmann::json& j, IDConfig& c) {
    if (j.contains("num_digits"))  j.at("num_digits").get_to(c.num_digits);
    if (j.contains("num_letters")) j.at("num_letters").get_to(c.num_letters);
    if (j.contains("letters"))     j.at("letters").get_to(c.letters);
}

inline void from_json(const nlohmann::json& j, VersionConfig& c) {
    if (j.contains("exam_models")) j.at("exam_models").get_to(c.exam_models);
    if (j.contains("exam_days"))   j.at("exam_days").get_to(c.exam_days);
}

inline void from_json(const nlohmann::json& j, MCQColumnsConfig& c) {
    if (j.contains("num_cols")) j.at("num_cols").get_to(c.num_cols);
    if (j.contains("columns")) {
        // Parse the columns object: {"1": 5, "2": 5, "3": 6}
        auto cols_obj = j.at("columns");
        for (auto& [key, value] : cols_obj.items()) {
            c.columns[key] = value.get<int>();
        }
    }
}

inline void from_json(const nlohmann::json& j, ExamConfig& c) {
    j.at("model_path").get_to(c.model_path);
    j.at("num_questions").get_to(c.num_questions);
    j.at("num_choices").get_to(c.num_choices);
    if (j.contains("mode"))                 j.at("mode").get_to(c.mode);
    if (j.contains("num_question_columns")) j.at("num_question_columns").get_to(c.num_question_columns);
    if (j.contains("row_tolerance_px"))     j.at("row_tolerance_px").get_to(c.row_tolerance_px);
    if (j.contains("id"))                   j.at("id").get_to(c.id);
    if (j.contains("version"))              j.at("version").get_to(c.version);
    if (j.contains("mcq_columns")) {
        j.at("mcq_columns").get_to(c.mcq_columns);
        // For new quiz layouts the per-column count is authoritative even when the
        // legacy field is omitted. Keep the legacy value unless it was explicitly set.
        if (c.num_question_columns <= 1 && c.mcq_columns.num_cols > 1) {
            c.num_question_columns = c.mcq_columns.num_cols;
        }
    }
}
