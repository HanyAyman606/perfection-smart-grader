#include "questions.h"
#include "grouping.h"
#include <cmath>
#include <algorithm>
#include <numeric>
#include <iostream>

namespace questions {

namespace {

std::vector<std::vector<Detection>> split_mega_row(
    const std::vector<Detection>& row,
    int num_groups,
    const std::vector<int>& expected_sizes = {},
    int row_index = -1
) {
    if (num_groups <= 1 || row.size() <= (size_t)num_groups) return {row};

    std::vector<Detection> sorted_row = row;
    std::sort(sorted_row.begin(), sorted_row.end(), [](const Detection& a, const Detection& b) { return a.cx < b.cx; });

    struct Gap { float dist; int idx; };
    std::vector<Gap> gaps;
    for (size_t i = 0; i < sorted_row.size() - 1; ++i) {
        gaps.push_back({sorted_row[i+1].cx - sorted_row[i].cx, (int)i});
    }

    std::sort(gaps.begin(), gaps.end(), [](const Gap& a, const Gap& b) { return a.dist > b.dist; });
    
    std::vector<int> split_after;
    for (int i = 0; i < std::min(num_groups - 1, (int)gaps.size()); ++i) {
        split_after.push_back(gaps[i].idx);
    }
    std::sort(split_after.begin(), split_after.end());

    std::vector<std::vector<Detection>> groups;
    int start = 0;
    for (int si : split_after) {
        groups.push_back(std::vector<Detection>(sorted_row.begin() + start, sorted_row.begin() + si + 1));
        start = si + 1;
    }
    groups.push_back(std::vector<Detection>(sorted_row.begin() + start, sorted_row.end()));

    // Validate resulting group sizes against expectations
    if (!expected_sizes.empty()) {
        for (size_t g = 0; g < groups.size() && g < expected_sizes.size(); ++g) {
            int actual = (int)groups[g].size();
            int expected = expected_sizes[g];
            int deviation = std::abs(actual - expected);
            if (deviation > 1) {
                std::cerr << "[SPLIT_WARN] row=" << row_index
                          << " col=" << g
                          << " expected=" << expected
                          << " got=" << actual
                          << " (deviation=" << deviation << ")"
                          << std::endl;
            }
        }
    }

    return groups;
}

std::vector<Detection> trim_to_best_subset(const std::vector<Detection>& group, int num_choices) {
    if (group.size() <= (size_t)num_choices) return group;

    // Combinatorial fallback for noisy rows — C(n, k) for n>12 is
    // too expensive. Pick the num_choices highest-confidence detections.
    if (group.size() > 12) {
        std::cerr << "[TRIM_WARN] group.size()=" << group.size()
                  << " > 12, falling back to top-confidence selection"
                  << std::endl;
        std::vector<Detection> sorted_group = group;
        std::sort(sorted_group.begin(), sorted_group.end(),
            [](const Detection& a, const Detection& b) {
                return a.confidence > b.confidence;
            });
        sorted_group.resize(num_choices);
        std::sort(sorted_group.begin(), sorted_group.end(),
            [](const Detection& a, const Detection& b) {
                return a.cx < b.cx;
            });
        return sorted_group;
    }

    std::vector<Detection> sorted_group = group;
    std::sort(sorted_group.begin(), sorted_group.end(), [](const Detection& a, const Detection& b){ return a.cx < b.cx; });

    std::vector<Detection> best_subset(sorted_group.begin(), sorted_group.begin() + num_choices);
    float best_score = std::numeric_limits<float>::infinity();

    std::vector<int> selectors(sorted_group.size(), 0);
    std::fill(selectors.begin(), selectors.begin() + num_choices, 1);

    do {
        std::vector<Detection> combo;
        for (size_t i = 0; i < sorted_group.size(); ++i) {
            if (selectors[i]) combo.push_back(sorted_group[i]);
        }

        std::vector<float> gaps;
        float mean = 0;
        for (size_t i = 0; i < combo.size() - 1; ++i) {
            float g = combo[i+1].cx - combo[i].cx;
            gaps.push_back(g);
            mean += g;
        }
        if (gaps.empty()) continue;
        mean /= gaps.size();

        float variance = 0;
        for (float g : gaps) variance += (g - mean)*(g - mean);
        float score = std::sqrt(variance / gaps.size());

        if (score < best_score) {
            best_score = score;
            best_subset = combo;
        }

    } while (std::prev_permutation(selectors.begin(), selectors.end()));

    return best_subset;
}

std::string label_at(int col_index, const std::vector<std::string>& choice_labels) {
    if (col_index < (int)choice_labels.size()) return choice_labels[col_index];
    return "COL_" + std::to_string(col_index);
}

QuestionResult classify_row(int question_number, const std::vector<Detection>& row, const std::vector<std::string>& choice_labels) {
    if (row.size() < choice_labels.size()) {
        std::cerr << "[ROW_UNDERCOUNT_WARN] Q" << question_number
                  << " expected " << choice_labels.size() << " detections but only got " << row.size() << ".\n"
                  << "  Detected classes: ";
        for (const auto& d : row) std::cerr << d.class_id << " ";
        std::cerr << std::endl;
    }

    std::vector<int> filled_indices;
    for (size_t i = 0; i < row.size(); ++i) {
        if (row[i].is_filled()) filled_indices.push_back(i);
    }

    if (filled_indices.empty()) {
        return {question_number, STATE_BLANK, std::monostate{}};
    }
    if (filled_indices.size() == 1) {
        return {question_number, STATE_ANSWERED, label_at(filled_indices[0], choice_labels)};
    }

    std::vector<std::string> answers;
    for (int idx : filled_indices) answers.push_back(label_at(idx, choice_labels));
    return {question_number, STATE_MULTIPLE, answers};
}

std::vector<std::string> build_row_labels(bool is_letter_col, const std::vector<std::string>& letters, int num_rows) {
    std::vector<std::string> labels;
    if (is_letter_col) {
        for (int i = 0; i < num_rows; ++i) {
            if (i < (int)letters.size()) labels.push_back(letters[i]);
            else labels.push_back("?" + std::to_string(i));
        }
    } else {
        for (int i = 0; i < num_rows; ++i) labels.push_back(std::to_string(i));
    }
    return labels;
}

IDColumnResult classify_column(const std::string& column_label, const std::vector<Detection>& col_dets, const std::vector<std::string>& row_labels) {
    std::vector<int> filled_indices;
    for (size_t i = 0; i < col_dets.size(); ++i) {
        if (col_dets[i].is_filled()) filled_indices.push_back(i);
    }

    if (filled_indices.empty()) {
        return {column_label, STATE_BLANK, std::monostate{}};
    }
    if (filled_indices.size() == 1) {
        int idx = filled_indices[0];
        std::string ans = idx < (int)row_labels.size() ? row_labels[idx] : "ROW_" + std::to_string(idx);
        return {column_label, STATE_ANSWERED, ans};
    }

    std::vector<std::string> answers;
    for (int idx : filled_indices) {
        answers.push_back(idx < (int)row_labels.size() ? row_labels[idx] : "ROW_" + std::to_string(idx));
    }
    return {column_label, STATE_MULTIPLE, answers};
}

std::tuple<std::optional<std::string>, std::optional<std::string>, bool> assemble_id(const std::vector<IDColumnResult>& columns, int num_letters) {
    std::string id_str, id_letter;
    bool needs_review = false;
    int answered_count = 0;
    for (int i = 0; i < (int)columns.size(); ++i) {
        const auto& col = columns[i];
        if (col.state == STATE_ANSWERED && std::holds_alternative<std::string>(col.answer)) {
            std::string ans = std::get<std::string>(col.answer);
            id_str += ans;
            if (i < num_letters) id_letter += ans;
            answered_count++;
        } else {
            id_str += "?";
            if (i < num_letters) id_letter += "?";
            needs_review = true;
        }
    }
    if (answered_count == 0) return {std::nullopt, std::nullopt, true};
    return {id_str, id_letter, needs_review};
}

} // namespace

std::vector<QuestionResult> process_questions(
    const std::vector<Detection>& detections,
    int num_questions,
    int num_question_columns,
    const std::vector<std::string>& choice_labels,
    int row_tolerance_px,
    const std::vector<int>& mcq_column_sizes
) {
    auto raw_mega_rows = grouping::group_by_rows(detections, row_tolerance_px);

    int expected_bubbles = choice_labels.size() * num_question_columns;
    int min_bubbles = std::max(num_question_columns > 1 ? 2 : 1, expected_bubbles / 3);

    std::vector<std::vector<Detection>> mega_rows;
    for (const auto& r : raw_mega_rows) {
        if (r.size() >= (size_t)min_bubbles) mega_rows.push_back(r);
    }

    // Prefer the authoritative per-column split from the printed template
    // (see config.h::mcq_column_sizes) over re-deriving one here. The two
    // happen to agree for a plain even split, but only the caller-supplied
    // sizes are guaranteed to reflect the actual paper layout.
    std::vector<int> cols_q;
    bool have_explicit_sizes = (int)mcq_column_sizes.size() == num_question_columns;
    if (have_explicit_sizes) {
        cols_q = mcq_column_sizes;
    } else {
        int base_q = num_questions / num_question_columns;
        int rem_q = num_questions % num_question_columns;
        cols_q.assign(num_question_columns, base_q);
        for (int i = 0; i < rem_q; ++i) cols_q[i]++;
    }

    int max_rows = 0;
    for (int c : cols_q) max_rows = std::max(max_rows, c);

    std::vector<std::vector<std::optional<std::vector<Detection>>>> question_grid(
        num_question_columns, std::vector<std::optional<std::vector<Detection>>>(max_rows, std::nullopt)
    );

    for (size_t row_idx = 0; row_idx < mega_rows.size(); ++row_idx) {
        if (row_idx >= (size_t)max_rows) break;

        // Build per-column expected bubble counts for this row
        std::vector<int> row_expected;
        for (int c = 0; c < num_question_columns; ++c) {
            row_expected.push_back(
                (int)row_idx < cols_q[c] ? (int)choice_labels.size() : 0
            );
        }
        auto groups = split_mega_row(mega_rows[row_idx], num_question_columns,
                                     row_expected, (int)row_idx);
        for (size_t col_idx = 0; col_idx < groups.size(); ++col_idx) {
            if (col_idx < (size_t)num_question_columns) {
                if (row_idx < (size_t)cols_q[col_idx]) {
                    auto trimmed = trim_to_best_subset(groups[col_idx], choice_labels.size());
                    question_grid[col_idx][row_idx] = trimmed;
                }
            }
        }
    }

    std::vector<QuestionResult> results;
    int current_q = 1;
    for (int col_idx = 0; col_idx < num_question_columns; ++col_idx) {
        for (int row_idx = 0; row_idx < cols_q[col_idx]; ++row_idx) {
            const auto& cell = question_grid[col_idx][row_idx];
            if (!cell.has_value() || cell->empty()) {
                results.push_back({current_q, STATE_ERROR_MISSING, std::monostate{}});
            } else {
                results.push_back(classify_row(current_q, *cell, choice_labels));
            }
            current_q++;
        }
    }

    std::sort(results.begin(), results.end(), [](const QuestionResult& a, const QuestionResult& b){
        return a.question_number < b.question_number;
    });

    return results;
}

std::tuple<std::vector<IDColumnResult>, std::optional<std::string>, std::optional<std::string>, bool> process_student_id(
    const std::vector<Detection>& detections,
    int num_digits,
    int num_letters,
    const std::vector<std::string>& letters,
    const std::vector<std::string>& column_labels,
    int row_tolerance_px
) {
    int total_cols = num_letters + num_digits;
    auto columns = grouping::group_by_columns(detections, row_tolerance_px);

    std::vector<IDColumnResult> id_col_results;
    for (int col_idx = 0; col_idx < total_cols; ++col_idx) {
        std::string label = col_idx < (int)column_labels.size() ? column_labels[col_idx] : "COL_" + std::to_string(col_idx);

        if (col_idx >= (int)columns.size() || columns[col_idx].empty()) {
            id_col_results.push_back({label, STATE_ERROR_MISSING, std::monostate{}});
            continue;
        }

        const auto& col_dets = columns[col_idx];
        bool is_letter_col = col_idx < num_letters;
        auto row_labels = build_row_labels(is_letter_col, letters, col_dets.size());

        id_col_results.push_back(classify_column(label, col_dets, row_labels));
    }

    auto [id_str, id_letter, needs_review] = assemble_id(id_col_results, num_letters);
    return {id_col_results, id_str, id_letter, needs_review};
}

} // namespace questions
