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

    // Authoritative per-column question counts, e.g. [9, 8, 8] for 25
    // questions in 3 columns. This reflects the actual printed template
    // (see project_manager.py::compute_mcq_column_layout, which is the
    // same math used by the Bubble Sheet Studio template generator), not
    // a re-derived guess. Empty means "not provided" — callers should
    // fall back to an even split across num_question_columns in that
    // case (e.g. old flat-int config payloads, or the CLI/test harness).
    std::vector<int> mcq_column_sizes;

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
    if (j.contains("row_tolerance_px")) j.at("row_tolerance_px").get_to(c.row_tolerance_px);
    if (j.contains("id")) j.at("id").get_to(c.id);

    // Preferred path: the real wire shape sent by MasterPacket.toExamConfigJson()
    // and mirrored by project_manager.py's build_sync_packet — e.g.
    //   "mcq_columns": {"num_cols": 3, "columns": {"1": 9, "2": 8, "3": 8}}
    // This is the authoritative printed-template layout; both
    // num_question_columns and mcq_column_sizes are derived from it so
    // they can never disagree with each other or with the paper.
    if (j.contains("mcq_columns")) {
        const auto& mc = j.at("mcq_columns");
        int num_cols = mc.value("num_cols", 1);
        c.num_question_columns = num_cols;
        c.mcq_column_sizes.assign(num_cols, 0);
        if (mc.contains("columns")) {
            const auto& cols = mc.at("columns");
            for (int i = 0; i < num_cols; ++i) {
                std::string key = std::to_string(i + 1);
                if (cols.contains(key)) {
                    c.mcq_column_sizes[i] = cols.at(key).get<int>();
                }
            }
        }
        
        int sum = 0;
        for (int size : c.mcq_column_sizes) {
            sum += size;
        }
        if (sum != c.num_questions) {
            throw std::runtime_error("mcq_columns sums to " + std::to_string(sum) + " but num_questions is " + std::to_string(c.num_questions) + " — config is malformed");
        }
    } else if (j.contains("num_question_columns")) {
        // Back-compat: old flat-int payloads (CLI/test harness, older
        // callers). No authoritative per-column split available, so
        // leave mcq_column_sizes empty — downstream code falls back to
        // an even split in this case.
        j.at("num_question_columns").get_to(c.num_question_columns);
    }
}
