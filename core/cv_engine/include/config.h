#pragma once

#include <string>
#include <vector>
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

struct ExamConfig {
    std::string model_path;
    int num_questions = 0;
    int num_choices = 0;
    int num_question_columns = 1;
    int row_tolerance_px = 15;
    IDConfig id;

    std::vector<std::string> choice_labels() const {
        std::vector<std::string> labels;
        for (int i = 0; i < num_choices; ++i) {
            labels.push_back(std::string(1, 'A' + i));
        }
        return labels;
    }
};

inline void from_json(const nlohmann::json& j, IDConfig& c) {
    if (j.contains("num_digits")) j.at("num_digits").get_to(c.num_digits);
    if (j.contains("num_letters")) j.at("num_letters").get_to(c.num_letters);
    if (j.contains("letters")) j.at("letters").get_to(c.letters);
}

inline void from_json(const nlohmann::json& j, ExamConfig& c) {
    j.at("model_path").get_to(c.model_path);
    j.at("num_questions").get_to(c.num_questions);
    j.at("num_choices").get_to(c.num_choices);
    if (j.contains("num_question_columns")) j.at("num_question_columns").get_to(c.num_question_columns);
    if (j.contains("row_tolerance_px")) j.at("row_tolerance_px").get_to(c.row_tolerance_px);
    if (j.contains("id")) j.at("id").get_to(c.id);
}
