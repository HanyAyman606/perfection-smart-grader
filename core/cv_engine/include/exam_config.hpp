// exam_config.hpp
//
// EXAM CONFIG CONNECTOR (external boundary file) -- C++ port of exam_config.py
//
// This file's ONLY job is to receive and validate the exam layout payload
// sent from the Flutter app, and hand back a clean, typed ExamConfig object
// for stage6_scoring to consume. It contains NO vision/scoring logic on
// purpose -- that all stays in stage6. Keeping this as its own boundary
// means the day the real connection changes (an HTTP POST from Flutter
// instead of a JSON file dropped on disk, a different field name, etc.)
// only THIS file needs to change, never stage6's scoring logic.
//
// ============================================================================
// PAYLOAD (sent from Flutter, once per exam/batch -- every scanned sheet in
// that batch is graded against this same config):
// ============================================================================
// {
//   "exam_id":        string, REQUIRED. Any identifier for this exam/quiz.
//   "exam_type":      string, REQUIRED. "quiz" (only one implemented so far).
//                              "shamel" is a recognized-but-not-built-yet type;
//                              passing it throws NotImplementedExamType on
//                              purpose instead of silently mis-scoring.
//   "num_questions":  int,    REQUIRED. Total MCQ question count for this exam.
//   "id_letters":     string, REQUIRED. Pool of letters printed in the ID
//                              letter column, IN PRINTED ORDER (e.g. "CDEFMW").
//                              This is the one thing that can never be
//                              recovered from bubble geometry alone -- it has
//                              to come from whoever generated the sheet.
//   "id_digit_cols":  int,    REQUIRED. Number of ID digit columns. Each digit
//                              column is always 0-9 (10 rows) by definition.
//
//   "num_choices":     int,   optional, default 4.  Choices per question (A/B/C/D/...).
//   "id_letter_cols":  int,   optional, default 1.   Number of ID letter columns.
//   "num_mcq_columns": int,   optional, default 3.   MCQ columns (fixed rule today).
// }
//
// Derived (never sent, always computed here with the SAME ceil-division rule
// used by bubble_sheet_studio.html's JS and stage5's get_mcq_column_math,
// generalized to num_mcq_columns instead of a fixed 3):
//     col1 = ceil(remaining / columns_left); remaining -= col1; columns_left -= 1
//     ... repeat ...; last column takes whatever remains.
// ============================================================================
#pragma once

#include <algorithm>
#include <cmath>
#include <fstream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

#include <nlohmann/json.hpp>

namespace examcfg {

using nlohmann::json;

// Raised when the incoming payload is missing/invalid/malformed.
// Mirrors Python's ExamConfigError(ValueError).
class ExamConfigError : public std::runtime_error {
public:
    explicit ExamConfigError(const std::string& msg) : std::runtime_error(msg) {}
};

// Raised for a recognized-but-not-yet-implemented exam_type (e.g. "shamel").
// Mirrors Python's NotImplementedError in this same spot.
class NotImplementedExamType : public std::runtime_error {
public:
    explicit NotImplementedExamType(const std::string& msg) : std::runtime_error(msg) {}
};

inline const std::vector<std::string>& supported_exam_types() {
    static const std::vector<std::string> v = {"quiz"};
    return v;
}

inline const std::vector<std::string>& not_yet_implemented_exam_types() {
    static const std::vector<std::string> v = {"shamel"};
    return v;
}

struct ExamConfig {
    std::string exam_id;
    std::string exam_type;
    int num_questions = 0;
    std::string id_letters;
    int id_digit_cols = 0;
    int num_choices = 4;
    int id_letter_cols = 1;
    int num_mcq_columns = 3;
    std::string model_path = "shamel.onnx";

    // Per-column question counts via the shared ceil-division rule.
    std::vector<int> mcq_column_counts() const {
        int cols_left = num_mcq_columns;
        int remaining = num_questions;
        std::vector<int> counts;
        for (int i = 0; i < num_mcq_columns; ++i) {
            if (i == num_mcq_columns - 1) {
                counts.push_back(remaining);
            } else {
                int share = static_cast<int>(std::ceil(static_cast<double>(remaining) / cols_left));
                counts.push_back(share);
                remaining -= share;
                cols_left -= 1;
            }
        }
        return counts;
    }

    int id_num_columns() const { return id_letter_cols + id_digit_cols; }

    std::vector<std::string> mcq_options() const {
        std::vector<std::string> opts;
        for (int i = 0; i < num_choices; ++i) {
            opts.push_back(std::string(1, static_cast<char>('A' + i)));
        }
        return opts;
    }
};

namespace detail {

inline bool is_present(const json& payload, const std::string& key) {
    if (!payload.contains(key)) return false;
    const json& v = payload.at(key);
    if (v.is_null()) return false;
    if (v.is_string() && v.get<std::string>().empty()) return false;
    return true;
}

inline const json& require(const json& payload, const std::string& key) {
    if (!is_present(payload, key)) {
        throw ExamConfigError("exam config payload missing required field: '" + key + "'");
    }
    return payload.at(key);
}

// Mirrors Python str.isalnum() (ASCII letters/digits only, matching this
// codebase's usage -- id_letters is always plain ASCII letters).
inline bool is_alnum(const std::string& s) {
    if (s.empty()) return false;
    for (unsigned char c : s) {
        if (!std::isalnum(c)) return false;
    }
    return true;
}

}  // namespace detail

// Loads and validates an exam config payload from an already-parsed JSON
// object. Mirrors Python's load_exam_config() when given a dict.
inline ExamConfig load_exam_config(const json& payload) {
    std::string exam_id = detail::require(payload, "exam_id").get<std::string>();
    std::string exam_type = detail::require(payload, "exam_type").get<std::string>();
    int num_questions = detail::require(payload, "num_questions").get<int>();
    std::string id_letters = detail::require(payload, "id_letters").get<std::string>();
    int id_digit_cols = detail::require(payload, "id_digit_cols").get<int>();

    const auto& not_impl = not_yet_implemented_exam_types();
    if (std::find(not_impl.begin(), not_impl.end(), exam_type) != not_impl.end()) {
        std::ostringstream oss;
        oss << "exam_type '" << exam_type << "' is reserved but not implemented yet -- "
            << "only [quiz] is supported right now.";
        throw NotImplementedExamType(oss.str());
    }
    const auto& supported = supported_exam_types();
    if (std::find(supported.begin(), supported.end(), exam_type) == supported.end()) {
        throw ExamConfigError("unknown exam_type '" + exam_type + "', expected one of [quiz]");
    }
    if (num_questions < 1) {
        throw ExamConfigError("num_questions must be >= 1, got " + std::to_string(num_questions));
    }
    if (id_digit_cols < 0) {
        throw ExamConfigError("id_digit_cols must be >= 0, got " + std::to_string(id_digit_cols));
    }
    if (!detail::is_alnum(id_letters)) {
        throw ExamConfigError("id_letters must be a non-empty alnum string, got '" + id_letters + "'");
    }

    // TUNING: accept "choices" (Flutter key) or legacy "num_choices"; "choices" takes priority.
    int num_choices = 4;
    if (payload.contains("choices"))          num_choices = payload.at("choices").get<int>();
    else if (payload.contains("num_choices")) num_choices = payload.at("num_choices").get<int>();
    int id_letter_cols = payload.value("id_letter_cols", 1);
    int num_mcq_columns = payload.value("num_mcq_columns", 3);

    if (num_choices < 2) {
        throw ExamConfigError("num_choices must be >= 2, got " + std::to_string(num_choices));
    }
    if (num_mcq_columns < 1) {
        throw ExamConfigError("num_mcq_columns must be >= 1, got " + std::to_string(num_mcq_columns));
    }

    ExamConfig cfg;
    cfg.exam_id = exam_id;
    cfg.exam_type = exam_type;
    cfg.num_questions = num_questions;
    cfg.id_letters = id_letters;
    cfg.id_digit_cols = id_digit_cols;
    cfg.num_choices = num_choices;
    cfg.id_letter_cols = id_letter_cols;
    cfg.num_mcq_columns = num_mcq_columns;
    if (detail::is_present(payload, "model_path")) {
        cfg.model_path = payload.at("model_path").get<std::string>();
    }
    return cfg;
}

// Loads and validates an exam config payload from a JSON string.
inline ExamConfig load_exam_config_from_string(const std::string& text) {
    json payload;
    try {
        payload = json::parse(text);
    } catch (const json::parse_error& e) {
        throw ExamConfigError(std::string("invalid JSON payload: ") + e.what());
    }
    return load_exam_config(payload);
}

// Loads and validates an exam config payload from a path to a .json file
// on disk. Mirrors Python's load_exam_config() when given a path string.
inline ExamConfig load_exam_config_from_file(const std::string& path) {
    std::ifstream f(path);
    if (!f) {
        throw ExamConfigError("could not open exam config file: '" + path + "'");
    }
    std::ostringstream buf;
    buf << f.rdbuf();
    return load_exam_config_from_string(buf.str());
}

}  // namespace examcfg
