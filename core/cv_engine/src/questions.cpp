#include "questions.h"
#include "grouping.h"
#include <cmath>
#include <algorithm>
#include <numeric>
#include <iostream>
#include <limits>

namespace questions {

namespace {

std::vector<std::vector<Detection>> split_mega_row(const std::vector<Detection>& row, int num_groups) {
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

    return groups;
}

std::vector<Detection> trim_to_best_subset(const std::vector<Detection>& group, int num_choices) {
    if (group.size() <= (size_t)num_choices) return group;

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

float mean_cy_of_row(const std::vector<Detection>& row) {
    float sum = 0.0f;
    for (const auto& d : row) sum += d.cy;
    return row.empty() ? 0.0f : sum / row.size();
}

// If more candidate rows were found than the configured layout expects, some of
// them are geometric noise (stray marks, print artifacts) rather than real
// question rows - see process_questions for the full rationale. Collapses
// noise-adjacent pairs in place until either mega_rows.size() == max_rows, or no
// remaining gap looks anomalous (in which case the leftover extras are treated
// as a genuine mismatch, not noise, and left for the caller to report).
void collapse_noise_rows(std::vector<std::vector<Detection>>& mega_rows, size_t max_rows) {
    const float NOISE_GAP_RATIO = 0.5f; // a real row's gap to its neighbor must be
                                         // at least half the sheet's typical pitch;
                                         // tighter than that means one side of the
                                         // pair is noise sitting close to a real row.
    while (mega_rows.size() > max_rows) {
        std::vector<float> cys;
        for (auto& r : mega_rows) cys.push_back(mean_cy_of_row(r));

        std::vector<float> gaps;
        for (size_t i = 0; i + 1 < cys.size(); ++i) gaps.push_back(cys[i + 1] - cys[i]);
        if (gaps.empty()) break;

        std::vector<float> sorted_gaps = gaps;
        std::sort(sorted_gaps.begin(), sorted_gaps.end());
        float median_gap = sorted_gaps[sorted_gaps.size() / 2];

        size_t worst_idx = 0;
        float smallest = gaps[0];
        for (size_t i = 1; i < gaps.size(); ++i) {
            if (gaps[i] < smallest) { smallest = gaps[i]; worst_idx = i; }
        }

        if (median_gap <= 0.0f || smallest >= NOISE_GAP_RATIO * median_gap) {
            break; // no anomalously tight gap left - remaining extras are real, not noise
        }

        size_t drop_idx = (mega_rows[worst_idx].size() <= mega_rows[worst_idx + 1].size())
            ? worst_idx : worst_idx + 1;
        mega_rows.erase(mega_rows.begin() + drop_idx);
    }
}

std::vector<float> cluster_centers(const std::vector<float>& values, float tolerance) {
    std::vector<float> sorted_vals = values;
    std::sort(sorted_vals.begin(), sorted_vals.end());

    std::vector<float> centers;
    std::vector<float> current;
    float running_mean = 0.0f;
    for (float v : sorted_vals) {
        if (current.empty() || std::abs(v - running_mean) <= tolerance) {
            current.push_back(v);
            float sum = 0.0f;
            for (float c : current) sum += c;
            running_mean = sum / current.size();
        } else {
            float sum = 0.0f;
            for (float c : current) sum += c;
            centers.push_back(sum / current.size());
            current = {v};
            running_mean = v;
        }
    }
    if (!current.empty()) {
        float sum = 0.0f;
        for (float c : current) sum += c;
        centers.push_back(sum / current.size());
    }
    return centers;
}

// Drops detections that don't look like real bubbles before any row/column
// grouping happens, using two purely geometric rules (no reliance on model
// confidence):
//   1. Area: a real bubble's contour area should be close to the sheet's
//      typical bubble size. Anything well below the median is a stray mark,
//      not a bubble.
//   2. Grid intersection: a real bubble sits where one of the sheet's row
//      lines crosses one of its column lines. A detection near a real row but
//      off every real column (or vice versa) is noise - actual bubbles never
//      float off-grid.
// Falls back to the less-filtered set at each stage if a rule would remove
// everything, since that means the rule's assumption (e.g. a sane median)
// broke down rather than that every detection is actually noise.
std::vector<Detection> filter_grid_and_area_noise(
    const std::vector<Detection>& detections,
    int row_tolerance_px,
    int col_tolerance_px
) {
    if (detections.empty()) return detections;

    std::vector<float> areas;
    for (const auto& d : detections) areas.push_back(d.w * d.h);
    std::vector<float> sorted_areas = areas;
    std::sort(sorted_areas.begin(), sorted_areas.end());
    float median_area = sorted_areas[sorted_areas.size() / 2];
    const float MIN_AREA_RATIO = 0.5f; // tune per model/DPI if this proves too strict/loose

    std::vector<Detection> area_ok;
    for (const auto& d : detections) {
        if (median_area <= 0.0f || (d.w * d.h) >= MIN_AREA_RATIO * median_area) {
            area_ok.push_back(d);
        }
    }
    if (area_ok.empty()) return detections;

    std::vector<float> cys, cxs;
    for (const auto& d : area_ok) { cys.push_back(d.cy); cxs.push_back(d.cx); }
    auto row_centers = cluster_centers(cys, (float)row_tolerance_px);
    auto col_centers = cluster_centers(cxs, (float)col_tolerance_px);

    // A genuine MCQ lattice must have a real row grid and a real column grid.
    // If all surviving detections collapse into a single x-cluster, they are just
    // one stray vertical column with no actual MCQ columns, so they are noise.
    if (col_centers.size() <= 1) {
        return {};
    }

    auto near_any = [](float v, const std::vector<float>& centers, float tol) {
        for (float c : centers) {
            if (std::abs(v - c) <= tol) return true;
        }
        return false;
    };

    std::vector<Detection> grid_ok;
    for (const auto& d : area_ok) {
        if (near_any(d.cy, row_centers, (float)row_tolerance_px) &&
            near_any(d.cx, col_centers, (float)col_tolerance_px)) {
            grid_ok.push_back(d);
        }
    }
    return grid_ok.empty() ? area_ok : grid_ok;
}


std::string label_at(int col_index, const std::vector<std::string>& choice_labels) {
    if (col_index < (int)choice_labels.size()) return choice_labels[col_index];
    return "COL_" + std::to_string(col_index);
}

QuestionResult classify_row(int question_number, const std::vector<Detection>& row, const std::vector<std::string>& choice_labels) {
    std::vector<int> filled_indices;
    float min_conf = 1.0f;
    for (size_t i = 0; i < row.size(); ++i) {
        min_conf = std::min(min_conf, row[i].confidence);
        if (row[i].is_filled()) filled_indices.push_back(i);
    }

    if (filled_indices.empty()) {
        return {question_number, STATE_BLANK, std::monostate{}, min_conf};
    }

    if (filled_indices.size() == 1) {
        return {question_number, STATE_ANSWERED, label_at(filled_indices[0], choice_labels), min_conf};
    }

    std::vector<std::string> answers;
    for (int idx : filled_indices) answers.push_back(label_at(idx, choice_labels));
    return {question_number, STATE_MULTIPLE, answers, min_conf};
}

// Digit ID columns are always exactly one of 0-9 — there is no such thing
// as a valid "digit" label outside that range. If a column's grouped
// detections don't land at 10 here, something upstream (duplicate/
// spurious detections, a misgrouped row) inflated or shrank it, and any
// positional label built off the *actual* detection count would be
// fabricated rather than real. See classify_column's count check below.
constexpr int kIdDigitRowCount = 10;

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

IDColumnResult classify_column(const std::string& column_label, const std::vector<Detection>& col_dets, const std::vector<std::string>& row_labels, size_t expected_row_count) {
    float min_conf = 1.0f;
    for (const auto& d : col_dets) min_conf = std::min(min_conf, d.confidence);

    // Guard against fabricated labels: row_labels is sized to the fixed
    // template (10 for digits, letters.size() for letters), not to
    // col_dets.size(). If the grouped detection count for this column
    // doesn't match that template exactly, positional indexing into
    // row_labels can't be trusted — a shifted or overflowing index would
    // silently report the wrong digit/letter, or (before this check) a
    // digit like "16" that can't physically exist. Flag it for review
    // instead of guessing.
    if (col_dets.size() != expected_row_count) {
        return {column_label, STATE_QUESTIONS_NUMBER_MISMATCH, std::monostate{}, min_conf};
    }

    std::vector<int> filled_indices;
    for (size_t i = 0; i < col_dets.size(); ++i) {
        if (col_dets[i].is_filled()) filled_indices.push_back(i);
    }

    if (filled_indices.empty()) {
        return {column_label, STATE_BLANK, std::monostate{}, min_conf};
    }

    if (filled_indices.size() == 1) {
        int idx = filled_indices[0];
        std::string ans = idx < (int)row_labels.size() ? row_labels[idx] : "ROW_" + std::to_string(idx);
        return {column_label, STATE_ANSWERED, ans, min_conf};
    }

    std::vector<std::string> answers;
    for (int idx : filled_indices) {
        answers.push_back(idx < (int)row_labels.size() ? row_labels[idx] : "ROW_" + std::to_string(idx));
    }
    return {column_label, STATE_MULTIPLE, answers, min_conf};
}

std::tuple<std::optional<std::string>, std::optional<std::string>> assemble_id(const std::vector<IDColumnResult>& columns, int num_letters) {
    std::string id_str, id_letter;
    for (int i = 0; i < (int)columns.size(); ++i) {
        const auto& col = columns[i];
        if (col.state != STATE_ANSWERED || !std::holds_alternative<std::string>(col.answer)) {
            return {std::nullopt, std::nullopt};
        }
        std::string ans = std::get<std::string>(col.answer);
        id_str += ans;
        if (i < num_letters) id_letter += ans;
    }
    return {id_str, id_letter};
}

} // namespace

std::vector<QuestionResult> process_questions(
    const std::vector<Detection>& detections,
    int num_questions,
    int num_question_columns,
    const std::vector<std::string>& choice_labels,
    int row_tolerance_px,
    int column_capacity // 0 = generic layout, even split across columns (legacy
                         // behavior). >0 = physically fixed max questions per
                         // column (e.g. shamel: two printed 10-row blocks = 20),
                         // filled front-to-back so only the LAST column(s) are
                         // ever partially/fully empty. For shamel mode this MUST
                         // match shamel_phases::validate_mcq_regions' expected-
                         // distribution rule (MAX_QUESTIONS_PER_REGION * blocks
                         // per column) - that function already validates this
                         // same front-loaded/capped-at-10-per-block layout on the
                         // raw image before detection ever runs; if the two
                         // layers disagree, a sound sheet can still get flagged
                         // QUESTIONS_NUMBER_MISMATCH here after already passing
                         // validate_mcq_regions's geometry + count check.
) {
    // Geometric noise rejection happens first, on the raw detections themselves -
    // before any row/column grouping - so stray marks never get a chance to form
    // a bogus row or corrupt a real one. No reliance on model confidence: area
    // and grid-intersection are purely positional/geometric rules.
    auto cleaned_detections = filter_grid_and_area_noise(detections, row_tolerance_px, row_tolerance_px);

    auto raw_mega_rows = grouping::group_by_rows(cleaned_detections, row_tolerance_px);

    int expected_bubbles = choice_labels.size() * num_question_columns;
    int min_bubbles = std::max(num_question_columns > 1 ? 2 : 1, expected_bubbles / 3);

    std::vector<std::vector<Detection>> mega_rows;
    for (const auto& r : raw_mega_rows) {
        if (r.size() >= (size_t)min_bubbles) mega_rows.push_back(r);
    }

    std::vector<int> cols_q(num_question_columns, 0);
    if (column_capacity > 0) {
        // Physically-capped layout (e.g. shamel): fill columns front-to-back, each
        // capped at column_capacity. A trailing column legitimately ending up at 0
        // (or partially filled) is expected paper geometry, not noise or a
        // mismatch - mirrors validate_mcq_regions' expected-distribution rule.
        int remaining = num_questions;
        for (int i = 0; i < num_question_columns; ++i) {
            int q = std::min(remaining, column_capacity);
            cols_q[i] = q;
            remaining -= q;
        }
        // If remaining > 0 here, num_questions exceeds num_question_columns *
        // column_capacity - the sheet's total physical capacity. That's a config
        // error upstream (should have been caught before this ever runs, e.g. by
        // validate_mcq_regions), not something this function can silently fix by
        // reshuffling counts across columns.
    } else {
        // Generic layout: no known physical per-column cap, so distribute as
        // evenly as possible across columns (original behavior).
        int base_q = num_questions / num_question_columns;
        int rem_q = num_questions % num_question_columns;
        for (int i = 0; i < num_question_columns; ++i) cols_q[i] = base_q;
        for (int i = 0; i < rem_q; ++i) cols_q[i]++;
    }

    int max_rows = *std::max_element(cols_q.begin(), cols_q.end());

    // Remove geometric noise rows now, before any row index gets turned into a
    // question number - a spurious extra row here doesn't just add one bad cell,
    // it shifts every subsequent row's index and corrupts the rest of the grid
    // (see collapse_noise_rows above for the detection rule).
    collapse_noise_rows(mega_rows, (size_t)max_rows);

    std::vector<std::vector<std::optional<std::vector<Detection>>>> question_grid(
        num_question_columns, std::vector<std::optional<std::vector<Detection>>>(max_rows, std::nullopt)
    );

    for (size_t row_idx = 0; row_idx < mega_rows.size(); ++row_idx) {
        if (row_idx >= (size_t)max_rows) break;

        auto groups = split_mega_row(mega_rows[row_idx], num_question_columns);
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
            if (!cell.has_value()) {
                // The config-declared layout expected a question here (based on
                // num_questions / num_question_columns) but grouping found no
                // detection row for this slot - i.e. a real mismatch between the
                // configured question count and what was actually detected.
                results.push_back({current_q, STATE_QUESTIONS_NUMBER_MISMATCH, std::monostate{}});
            } else {
                results.push_back(classify_row(current_q, *cell, choice_labels));
            }
            current_q++;
        }
    }

    // Any row still beyond max_rows here has already survived collapse_noise_rows'
    // geometric noise check above, so it's real content the configured layout
    // didn't account for - a genuine config-vs-detected mismatch, symmetric to the
    // "expected but not detected" case above. Give each leftover real row its own
    // MISMATCH entry (continuing the numbering) instead of either discarding it
    // silently or overwriting an unrelated already-classified entry.
    for (size_t row_idx = (size_t)max_rows; row_idx < mega_rows.size(); ++row_idx) {
        results.push_back({current_q, STATE_QUESTIONS_NUMBER_MISMATCH, std::monostate{}});
        current_q++;
    }

    std::sort(results.begin(), results.end(), [](const QuestionResult& a, const QuestionResult& b){
        return a.question_number < b.question_number;
    });

    return results;
}

// Quiz mode: per-column question counts (new architecture)
std::vector<QuestionResult> process_questions_per_column(
    const std::vector<Detection>& detections,
    int num_question_columns,
    const std::map<std::string, int>& per_column_questions,
    const std::vector<std::string>& choice_labels,
    int row_tolerance_px
) {
    // Calculate total questions from per-column counts
    int num_questions = 0;
    int max_rows = 0;
    std::vector<int> cols_q(num_question_columns, 0);
    
    for (int col = 0; col < num_question_columns; ++col) {
        // Config uses 1-indexed column keys ("1", "2", "3"); map col 0 -> "1", etc.
        std::string key = std::to_string(col + 1);
        auto it = per_column_questions.find(key);
        int col_count = (it != per_column_questions.end()) ? it->second : 0;
        cols_q[col] = col_count;
        num_questions += col_count;
        max_rows = std::max(max_rows, col_count);
    }

    auto cleaned_detections = filter_grid_and_area_noise(detections, row_tolerance_px, row_tolerance_px);
    auto raw_mega_rows = grouping::group_by_rows(cleaned_detections, row_tolerance_px);

    int expected_bubbles = choice_labels.size() * num_question_columns;
    int min_bubbles = std::max(num_question_columns > 1 ? 2 : 1, expected_bubbles / 3);

    std::vector<std::vector<Detection>> mega_rows;
    for (const auto& r : raw_mega_rows) {
        if (r.size() >= (size_t)min_bubbles) mega_rows.push_back(r);
    }

    // Remove noise rows based on max_rows from per-column layout
    collapse_noise_rows(mega_rows, (size_t)max_rows);

    std::vector<std::vector<std::optional<std::vector<Detection>>>> question_grid(
        num_question_columns, std::vector<std::optional<std::vector<Detection>>>(max_rows, std::nullopt)
    );

    for (size_t row_idx = 0; row_idx < mega_rows.size(); ++row_idx) {
        if (row_idx >= (size_t)max_rows) break;

        auto groups = split_mega_row(mega_rows[row_idx], num_question_columns);
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
            if (!cell.has_value()) {
                // Expected a question here (per column config) but found no detections
                results.push_back({current_q, STATE_QUESTIONS_NUMBER_MISMATCH, std::monostate{}});
            } else {
                results.push_back(classify_row(current_q, *cell, choice_labels));
            }
            current_q++;
        }
    }

    // Check for extra physical questions detected in columns that were shorter than max_rows
    for (size_t row_idx = 0; row_idx < mega_rows.size(); ++row_idx) {
        if (row_idx >= (size_t)max_rows) break;
        auto groups = split_mega_row(mega_rows[row_idx], num_question_columns);
        for (size_t col_idx = 0; col_idx < groups.size(); ++col_idx) {
            if (col_idx < (size_t)num_question_columns) {
                // If this cell is beyond the configured column length, but there are physical detections
                if (row_idx >= (size_t)cols_q[col_idx] && !groups[col_idx].empty()) {
                    results.push_back({current_q, STATE_QUESTIONS_NUMBER_MISMATCH, std::monostate{}});
                    current_q++;
                }
            }
        }
    }

    // Any extra rows beyond max_rows are unexpected
    for (size_t row_idx = (size_t)max_rows; row_idx < mega_rows.size(); ++row_idx) {
        results.push_back({current_q, STATE_QUESTIONS_NUMBER_MISMATCH, std::monostate{}});
        current_q++;
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
    bool needs_review = false;

    for (int col_idx = 0; col_idx < total_cols; ++col_idx) {
        std::string label = col_idx < (int)column_labels.size() ? column_labels[col_idx] : "COL_" + std::to_string(col_idx);

        if (col_idx >= (int)columns.size()) {
            id_col_results.push_back({label, STATE_QUESTIONS_NUMBER_MISMATCH, std::monostate{}});
            needs_review = true;
            continue;
        }

        const auto& col_dets = columns[col_idx];
        bool is_letter_col = col_idx < num_letters;
        size_t expected_row_count = is_letter_col ? letters.size() : (size_t)kIdDigitRowCount;
        auto row_labels = build_row_labels(is_letter_col, letters, (int)expected_row_count);

        auto res = classify_column(label, col_dets, row_labels, expected_row_count);
        if (res.state == STATE_BLANK || res.state == STATE_MULTIPLE || res.state == STATE_QUESTIONS_NUMBER_MISMATCH) {
            needs_review = true;
        }
        id_col_results.push_back(res);
    }

    auto [id_str, id_letter] = assemble_id(id_col_results, num_letters);
    return {id_col_results, id_str, id_letter, needs_review};
}

} // namespace questions