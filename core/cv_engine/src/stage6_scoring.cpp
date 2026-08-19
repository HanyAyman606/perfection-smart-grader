// STAGE 6: Grid Assignment & Scoring
// Reads YOLO-format text files from Stage 5 and reads the exam's LAYOUT
// TRUTH from exam_config.json. Purely trusts the ONNX model's cls_id and
// score.
//
// C++ port of stage6_scoring.py -- see that file's module docstring for
// the full rationale behind anchor_rows_to_slots() (position-anchored row
// assignment so a missing row becomes an explicit BLANK at the correct
// index instead of silently shifting every question after it).
#include <algorithm>
#include <cmath>
#include <fstream>
#include <iostream>
#include <map>
#include <numeric>
#include <optional>
#include <set>
#include <sstream>
#include <string>
#include <vector>

#include <nlohmann/json.hpp>

#include "common.hpp"
#include "exam_config.hpp"

// ordered_json preserves insertion order for object keys, matching
// Python dict/json.dump behaviour (Q1, Q2, ... in the order assigned,
// not alphabetically re-sorted).
using ojson = nlohmann::ordered_json;
namespace fs = std::filesystem;

namespace {

fs::path WORK_DIR, TXT_DIR, OUT_DIR;

// Trust the ONNX model completely if score > 0.55
constexpr double MODEL_CONF_TRUST = 0.55;

// Within-row jitter tolerance when clustering by y (boxes at "the same
// row" won't have byte-identical cy, this just merges near-duplicates).
constexpr double ROW_Y_JITTER_PX = 15.0;

enum class FillStatus { Filled, Empty, Review };

struct Box {
    int cls;
    double x1, y1, x2, y2;
    double score;
    double cx, cy;
    FillStatus status;
};

// =======================================================================
// GEOMETRY DISCOVERY -- columns & lanes (forced-count split is safe here)
// =======================================================================
template <typename KeyFn>
std::vector<std::vector<Box*>> split_by_biggest_gaps(std::vector<Box*> boxes, KeyFn key, int n_groups) {
    if (boxes.empty()) return {};
    if (n_groups <= 1 || static_cast<int>(boxes.size()) <= n_groups) {
        std::sort(boxes.begin(), boxes.end(), [&](Box* a, Box* b) { return key(a) < key(b); });
        if (n_groups <= 1) return {boxes};
        std::vector<std::vector<Box*>> groups;
        for (Box* b : boxes) groups.push_back({b});
        return groups;
    }

    std::vector<Box*> ordered = boxes;
    std::sort(ordered.begin(), ordered.end(), [&](Box* a, Box* b) { return key(a) < key(b); });

    std::vector<std::pair<double, int>> gaps;  // (gap size, index)
    for (size_t i = 0; i + 1 < ordered.size(); ++i) {
        gaps.emplace_back(key(ordered[i + 1]) - key(ordered[i]), static_cast<int>(i));
    }
    std::sort(gaps.begin(), gaps.end(), [](auto& a, auto& b) { return a.first > b.first; });

    std::vector<int> split_after;
    for (int i = 0; i < n_groups - 1 && i < static_cast<int>(gaps.size()); ++i) split_after.push_back(gaps[i].second);
    std::sort(split_after.begin(), split_after.end());

    std::vector<std::vector<Box*>> groups;
    size_t start = 0;
    for (int pos : split_after) {
        groups.emplace_back(ordered.begin() + start, ordered.begin() + pos + 1);
        start = pos + 1;
    }
    groups.emplace_back(ordered.begin() + start, ordered.end());
    return groups;
}

std::vector<std::vector<Box*>> cluster_columns(std::vector<Box*> boxes, int expected_n) {
    if (boxes.empty()) return {};
    return split_by_biggest_gaps(boxes, [](Box* b) { return b->cx; }, expected_n);
}

// =======================================================================
// GEOMETRY DISCOVERY -- rows (position-anchored, NOT forced-count)
// =======================================================================
template <typename KeyFn>
std::vector<std::vector<Box*>> natural_clusters(std::vector<Box*> boxes, KeyFn key, double jitter) {
    if (boxes.empty()) return {};
    std::vector<Box*> ordered = boxes;
    std::sort(ordered.begin(), ordered.end(), [&](Box* a, Box* b) { return key(a) < key(b); });
    std::vector<std::vector<Box*>> clusters = {{ordered[0]}};
    for (size_t i = 1; i < ordered.size(); ++i) {
        Box* b = ordered[i];
        if (std::abs(key(b) - key(clusters.back().back())) < jitter) {
            clusters.back().push_back(b);
        } else {
            clusters.push_back({b});
        }
    }
    return clusters;
}

// Assigns detected boxes to exactly `expected_rows` row SLOTS by absolute
// position, not by forcing that many groups out of whatever boxes exist.
// Returns a vector of length expected_rows; slot i is the boxes for row
// i, or empty if that row had no detections at all (a real, correctly
// positioned blank).
template <typename KeyFn>
std::vector<std::vector<Box*>> anchor_rows_to_slots(std::vector<Box*> boxes, KeyFn key_y, int expected_rows,
                                                     double jitter = ROW_Y_JITTER_PX) {
    if (expected_rows <= 0) return {};
    std::vector<std::vector<Box*>> slots(expected_rows);
    if (boxes.empty()) return slots;

    auto clusters = natural_clusters(boxes, key_y, jitter);
    std::vector<double> cluster_ys;
    for (auto& c : clusters) {
        double sum = 0;
        for (Box* b : c) sum += key_y(b);
        cluster_ys.push_back(sum / c.size());
    }

    if (clusters.size() == 1) {
        slots[0] = clusters[0];
        return slots;
    }

    std::vector<double> gaps;
    for (size_t i = 0; i + 1 < cluster_ys.size(); ++i) gaps.push_back(cluster_ys[i + 1] - cluster_ys[i]);
    std::vector<double> sorted_gaps = gaps;
    std::sort(sorted_gaps.begin(), sorted_gaps.end());
    double row_pitch;
    size_t ng = sorted_gaps.size();
    if (ng % 2 == 1) row_pitch = sorted_gaps[ng / 2];
    else row_pitch = (sorted_gaps[ng / 2 - 1] + sorted_gaps[ng / 2]) / 2.0;
    if (row_pitch <= 0) row_pitch = 1.0;

    double anchor_y = cluster_ys[0];
    std::set<int> used_slots;

    for (size_t ci = 0; ci < clusters.size(); ++ci) {
        double cy = cluster_ys[ci];
        int slot_idx = static_cast<int>(std::lround((cy - anchor_y) / row_pitch));
        slot_idx = std::clamp(slot_idx, 0, expected_rows - 1);

        if (used_slots.count(slot_idx)) {
            bool placed = false;
            for (int delta = 1; delta < expected_rows && !placed; ++delta) {
                for (int cand : {slot_idx - delta, slot_idx + delta}) {
                    if (cand >= 0 && cand < expected_rows && !used_slots.count(cand)) {
                        slot_idx = cand;
                        placed = true;
                        break;
                    }
                }
            }
        }

        used_slots.insert(slot_idx);
        slots[slot_idx] = clusters[ci];
    }

    return slots;
}

// =======================================================================
// FILL DECISION (Trusts ONNX purely)
// =======================================================================
// If the model says filled (cls 0), we trust it. The ONNX model natively
// handles X marks by classifying them as empty (cls 1).
FillStatus decide_fill_status(const Box& box) {
    if (box.cls == 0) return box.score >= MODEL_CONF_TRUST ? FillStatus::Filled : FillStatus::Review;
    return FillStatus::Empty;
}

// =======================================================================
// PARSING
// =======================================================================
std::vector<Box> parse_txt_file(const fs::path& filepath) {
    std::vector<Box> boxes;
    std::ifstream f(filepath);
    if (!f) return boxes;
    std::string line;
    while (std::getline(f, line)) {
        std::istringstream iss(line);
        std::vector<std::string> parts;
        std::string tok;
        while (iss >> tok) parts.push_back(tok);
        if (parts.size() >= 6) {
            Box b;
            b.cls = std::stoi(parts[0]);
            b.x1 = std::stod(parts[1]);
            b.y1 = std::stod(parts[2]);
            b.x2 = std::stod(parts[3]);
            b.y2 = std::stod(parts[4]);
            b.score = std::stod(parts[5]);
            b.cx = (b.x1 + b.x2) / 2.0;
            b.cy = (b.y1 + b.y2) / 2.0;
            boxes.push_back(b);
        }
    }
    return boxes;
}

double median_of(std::vector<double> v) {
    std::sort(v.begin(), v.end());
    size_t n = v.size();
    if (n == 0) return 0.0;
    if (n % 2 == 1) return v[n / 2];
    return (v[n / 2 - 1] + v[n / 2]) / 2.0;
}

struct McqResult {
    ojson answers = ojson::object();
    int total_questions_detected = 0;
    ojson review_flags = ojson::array();
    std::vector<std::string> warnings;
};

// =======================================================================
// MCQ PROCESSING (Absolute Lanes & Position-Anchored Rows)
// =======================================================================
McqResult process_mcq(std::vector<Box>& boxes, const examcfg::ExamConfig& config) {
    for (auto& b : boxes) b.status = decide_fill_status(b);

    int expected_cols = config.num_mcq_columns;
    std::vector<int> expected_counts = config.mcq_column_counts();
    std::vector<std::string> mcq_options = config.mcq_options();
    int num_options = static_cast<int>(mcq_options.size());

    std::vector<Box*> all_ptrs;
    for (auto& b : boxes) all_ptrs.push_back(&b);
    auto col_groups = cluster_columns(all_ptrs, expected_cols);

    McqResult out;
    if (static_cast<int>(col_groups.size()) != expected_cols) {
        out.warnings.push_back("expected " + std::to_string(expected_cols) + " MCQ columns per config, detected " +
                                std::to_string(col_groups.size()));
    }

    int current_q_num = 1;

    for (size_t col_idx = 0; col_idx < col_groups.size(); ++col_idx) {
        auto& col_boxes = col_groups[col_idx];
        int expected_rows = col_idx < expected_counts.size() ? expected_counts[col_idx] : 0;
        if (expected_rows == 0) continue;

        if (col_boxes.empty()) {
            for (int i = 0; i < expected_rows; ++i) {
                out.answers["Q" + std::to_string(current_q_num)] = "BLANK";
                current_q_num++;
            }
            out.warnings.push_back("MCQ col " + std::to_string(col_idx + 1) + ": no detections at all (expected " +
                                    std::to_string(expected_rows) + " rows)");
            continue;
        }

        // 1. Reject stray noise boxes far outside the column's horizontal center
        std::vector<double> cxs;
        for (Box* b : col_boxes) cxs.push_back(b->cx);
        double col_median_x = median_of(cxs);
        double col_w_approx = 1400.0 / expected_cols;
        std::vector<Box*> valid_boxes;
        for (Box* b : col_boxes)
            if (std::abs(b->cx - col_median_x) < col_w_approx * 0.45) valid_boxes.push_back(b);

        if (valid_boxes.empty()) {
            for (int i = 0; i < expected_rows; ++i) {
                out.answers["Q" + std::to_string(current_q_num)] = "BLANK";
                current_q_num++;
            }
            out.warnings.push_back("MCQ col " + std::to_string(col_idx + 1) +
                                    ": all detections rejected as noise (expected " + std::to_string(expected_rows) +
                                    " rows)");
            continue;
        }

        // 2. Establish Absolute X-Lanes (A, B, C, D, ...).
        auto lanes = split_by_biggest_gaps(valid_boxes, [](Box* b) { return b->cx; }, num_options);
        std::vector<double> lane_centers;
        for (auto& lane : lanes) {
            if (lane.empty()) continue;
            std::vector<double> lc;
            for (Box* b : lane) lc.push_back(b->cx);
            lane_centers.push_back(median_of(lc));
        }

        if (static_cast<int>(lane_centers.size()) != num_options) {
            double min_x = valid_boxes[0]->cx, max_x = valid_boxes[0]->cx;
            for (Box* b : valid_boxes) { min_x = std::min(min_x, b->cx); max_x = std::max(max_x, b->cx); }
            double step = (max_x - min_x) / std::max(1, num_options - 1);
            lane_centers.clear();
            for (int i = 0; i < num_options; ++i) lane_centers.push_back(min_x + i * step);
        }

        // 3. Position-anchored Y-Rows
        auto row_slots = anchor_rows_to_slots(valid_boxes, [](Box* b) { return b->cy; }, expected_rows);

        int real_row_count = 0;
        for (auto& s : row_slots) if (!s.empty()) real_row_count++;
        if (real_row_count != expected_rows) {
            out.warnings.push_back("MCQ col " + std::to_string(col_idx + 1) + ": expected " +
                                    std::to_string(expected_rows) + " rows, found real data for " +
                                    std::to_string(real_row_count) + " (rest reported BLANK at correct position)");
        }

        // 4. Assign Answers to Lanes, slot by slot (empty slot -> BLANK)
        for (auto& row : row_slots) {
            std::vector<std::string> filled_opts, review_opts;

            for (Box* b : row) {
                int lane_idx = 0;
                double best_d = std::numeric_limits<double>::infinity();
                for (size_t li = 0; li < lane_centers.size(); ++li) {
                    double d = std::abs(b->cx - lane_centers[li]);
                    if (d < best_d) { best_d = d; lane_idx = static_cast<int>(li); }
                }
                std::string opt_letter = mcq_options[lane_idx];

                if (b->status == FillStatus::Filled) {
                    if (std::find(filled_opts.begin(), filled_opts.end(), opt_letter) == filled_opts.end())
                        filled_opts.push_back(opt_letter);
                } else if (b->status == FillStatus::Review) {
                    if (std::find(review_opts.begin(), review_opts.end(), opt_letter) == review_opts.end())
                        review_opts.push_back(opt_letter);
                }
            }
            std::sort(filled_opts.begin(), filled_opts.end());
            std::sort(review_opts.begin(), review_opts.end());

            std::string final_ans;
            if (!review_opts.empty() && filled_opts.empty()) {
                final_ans = "REVIEW";
                ojson rf;
                rf["question"] = "Q" + std::to_string(current_q_num);
                rf["candidates"] = review_opts;
                out.review_flags.push_back(rf);
            } else if (filled_opts.size() == 1 && review_opts.empty()) {
                final_ans = filled_opts[0];
            } else if (!filled_opts.empty() && !review_opts.empty()) {
                final_ans = "REVIEW";
                std::set<std::string> merged(filled_opts.begin(), filled_opts.end());
                merged.insert(review_opts.begin(), review_opts.end());
                ojson rf;
                rf["question"] = "Q" + std::to_string(current_q_num);
                rf["candidates"] = std::vector<std::string>(merged.begin(), merged.end());
                out.review_flags.push_back(rf);
            } else if (filled_opts.size() > 1) {
                final_ans = "MULTI";
            } else {
                final_ans = "BLANK";
            }

            out.answers["Q" + std::to_string(current_q_num)] = final_ans;
            current_q_num++;
        }
    }

    out.total_questions_detected = current_q_num - 1;
    if (out.total_questions_detected != config.num_questions) {
        out.warnings.push_back("expected " + std::to_string(config.num_questions) + " total questions, detected " +
                                std::to_string(out.total_questions_detected));
    }

    return out;
}

struct IdResult {
    std::string id_string;
    ojson review_flags = ojson::array();
    std::vector<std::string> warnings;
};

// =======================================================================
// ID PROCESSING (Position-Anchored Rows, same fix applied)
// =======================================================================
IdResult process_id(std::vector<Box>& boxes, const examcfg::ExamConfig& config) {
    for (auto& b : boxes) b.status = decide_fill_status(b);

    std::vector<Box*> all_ptrs;
    for (auto& b : boxes) all_ptrs.push_back(&b);
    int id_num_columns = config.id_num_columns();
    auto col_groups = cluster_columns(all_ptrs, id_num_columns);

    IdResult out;
    if (static_cast<int>(col_groups.size()) != id_num_columns) {
        out.warnings.push_back("expected " + std::to_string(id_num_columns) + " ID columns, detected " +
                                std::to_string(col_groups.size()));
    }

    const std::string& id_letters = config.id_letters;
    std::string id_result;

    for (int col_idx = 0; col_idx < id_num_columns; ++col_idx) {
        std::vector<Box*> col_boxes = col_idx < static_cast<int>(col_groups.size()) ? col_groups[col_idx]
                                                                                     : std::vector<Box*>{};
        bool is_letter_col = col_idx < config.id_letter_cols;
        int expected_rows = is_letter_col ? static_cast<int>(id_letters.size()) : 10;

        auto row_slots = anchor_rows_to_slots(col_boxes, [](Box* b) { return b->cy; }, expected_rows);

        int real_row_count = 0;
        for (auto& s : row_slots) if (!s.empty()) real_row_count++;
        if (real_row_count != expected_rows) {
            std::string kind = is_letter_col ? "letter" : "digit";
            out.warnings.push_back("ID " + kind + " column " + std::to_string(col_idx) + ": expected " +
                                    std::to_string(expected_rows) + " rows, found real data for " +
                                    std::to_string(real_row_count) + " (rest reported blank at correct position)");
        }

        std::vector<std::string> filled_vals, review_vals;
        for (int row_idx = 0; row_idx < static_cast<int>(row_slots.size()); ++row_idx) {
            auto& row = row_slots[row_idx];
            if (row.empty()) continue;
            std::vector<Box*> row_sorted = row;
            std::sort(row_sorted.begin(), row_sorted.end(), [](Box* a, Box* b) { return a->score > b->score; });
            Box* b = row_sorted[0];

            std::string label = (is_letter_col && row_idx < static_cast<int>(id_letters.size()))
                                     ? std::string(1, id_letters[row_idx])
                                     : std::to_string(row_idx);
            if (b->status == FillStatus::Filled) {
                filled_vals.push_back(label);
            } else if (b->status == FillStatus::Review) {
                review_vals.push_back(label);
            }
        }

        if (!review_vals.empty()) {
            id_result += "?";
            ojson rf;
            rf["column"] = col_idx;
            std::vector<std::string> candidates = filled_vals;
            candidates.insert(candidates.end(), review_vals.begin(), review_vals.end());
            rf["candidates"] = candidates;
            out.review_flags.push_back(rf);
        } else if (filled_vals.size() == 1) {
            id_result += filled_vals[0];
        } else if (filled_vals.size() > 1) {
            id_result += "?";
            ojson rf;
            rf["column"] = col_idx;
            rf["candidates"] = filled_vals;
            out.review_flags.push_back(rf);
        } else {
            id_result += "-";
        }
    }

    out.id_string = id_result;
    return out;
}

}  // namespace

// =======================================================================
// SUMMARY JSON: merge quality signals from stage1 + stage3 with grades
// =======================================================================
// Reads stage1/_results.json and stage3/_results.json then writes
// stage6/summary.json so the Flutter caller has one file to read instead
// of three. Structure:
// {
//   "status": "SUCCESS" | "ERROR",
//   "id_found": bool,
//   "mcq_found": bool,
//   "orientation_confidence": "HIGH" | "LOW",
//   "orientation_reason": string,
//   "has_missing_rows": bool,
//   "grading": <the full final_grades.json object>
// }
namespace {

void write_summary(const fs::path& work_dir, const fs::path& out_dir, const ojson& grades) {
    ojson summary;
    summary["status"] = "SUCCESS";

    // ── Stage 1 quality signals ──────────────────────────────────────────
    bool id_found = false, mcq_found = false;
    fs::path s1_path = work_dir / "stage1" / "_results.json";
    if (fs::exists(s1_path)) {
        std::ifstream s1f(s1_path);
        nlohmann::json s1;
        if (s1f >> s1 && s1.is_array() && !s1.empty()) {
            const auto& first = s1[0];
            id_found  = first.value("id_found",  false);
            mcq_found = first.value("mcq_found", false);
        }
    }
    summary["id_found"]  = id_found;
    summary["mcq_found"] = mcq_found;

    // ── Stage 3 orientation signals ──────────────────────────────────────
    std::string orient_confidence = "HIGH";
    std::string orient_reason;
    fs::path s3_path = work_dir / "stage3" / "_results.json";
    if (fs::exists(s3_path)) {
        std::ifstream s3f(s3_path);
        nlohmann::json s3;
        if (s3f >> s3 && s3.is_array() && !s3.empty()) {
            const auto& first = s3[0];
            orient_reason = first.value("reason", "");
            // "LOW CONFIDENCE" appears in the reason string when stage3 had to guess.
            if (orient_reason.find("LOW CONFIDENCE") != std::string::npos ||
                orient_reason.find("MANUAL CHECK") != std::string::npos) {
                orient_confidence = "LOW";
            }
        }
    }
    summary["orientation_confidence"] = orient_confidence;
    summary["orientation_reason"]     = orient_reason;

    // ── Missing rows detection ───────────────────────────────────────────
    // Look for geometry warnings in the grades' warnings arrays that
    // contain "expected N rows, found real data for M" — the presence of
    // any such warning means at least one row was entirely undetected.
    bool has_missing_rows = false;
    if (grades.contains("sheets") && grades["sheets"].is_object()) {
        for (const auto& [key, sheet] : grades["sheets"].items()) {
            if (!sheet.contains("warnings") || !sheet["warnings"].is_array()) continue;
            for (const auto& w : sheet["warnings"]) {
                std::string ws = w.is_string() ? w.get<std::string>() : "";
                if (ws.find("expected") != std::string::npos &&
                    ws.find("rows") != std::string::npos) {
                    has_missing_rows = true;
                    break;
                }
            }
            if (has_missing_rows) break;
        }
    }
    summary["has_missing_rows"] = has_missing_rows;

    // ── Embed full grading payload ────────────────────────────────────────
    summary["grading"] = grades;

    fs::path summary_path = out_dir / "summary.json";
    std::ofstream sf(summary_path);
    sf << summary.dump(4);
    std::cout << "Summary written to " << summary_path << "\n";
}

}  // namespace (summary helpers)

int main(int argc, char** argv) {
    fs::path here = argc > 1 ? fs::path(argv[1]) : fs::current_path();
    WORK_DIR = fs::absolute(here);
    TXT_DIR = WORK_DIR / "stage5";
    OUT_DIR = WORK_DIR / "stage6";
    common::ensure_dir(OUT_DIR);

    fs::path config_path = WORK_DIR / "exam_config.json";
    if (!fs::exists(config_path)) {
        std::cerr << "No exam_config.json found at " << config_path << ".\n";
        return 1;
    }

    examcfg::ExamConfig config;
    try {
        config = examcfg::load_exam_config_from_file(config_path.string());
    } catch (const std::exception& e) {
        std::cerr << e.what() << "\n";
        return 1;
    }

    ojson final_output;
    final_output["exam_id"] = config.exam_id;
    final_output["exam_type"] = config.exam_type;
    final_output["num_questions_expected"] = config.num_questions;
    final_output["sheets"] = ojson::object();
    ojson& sheets = final_output["sheets"];

    auto txt_files = common::glob_ext(TXT_DIR, {".txt"});

    for (const auto& txt_path : txt_files) {
        std::string basename = txt_path.filename().string();

        std::vector<Box> boxes = parse_txt_file(txt_path);
        if (boxes.empty()) continue;

        std::string base_filename = common::strip_suffix(basename, "_id_final.txt");
        base_filename = common::strip_suffix(base_filename, "_mcq_final.txt");

        if (!sheets.contains(base_filename)) {
            ojson sheet;
            sheet["ID"] = "";
            sheet["MCQ"] = ojson::object();
            sheet["needs_review"] = ojson::array();
            sheet["warnings"] = ojson::array();
            sheets[base_filename] = sheet;
        }
        ojson& sheet = sheets[base_filename];

        if (common::contains(basename, "_mcq_")) {
            McqResult r = process_mcq(boxes, config);
            sheet["MCQ"] = r.answers;
            sheet["total_questions_detected"] = r.total_questions_detected;
            for (auto& rf : r.review_flags) {
                ojson entry;
                entry["panel"] = "MCQ";
                for (auto it = rf.begin(); it != rf.end(); ++it) entry[it.key()] = it.value();
                sheet["needs_review"].push_back(entry);
            }
            for (auto& w : r.warnings) sheet["warnings"].push_back("MCQ: " + w);
        } else if (common::contains(basename, "_id_")) {
            IdResult r = process_id(boxes, config);
            sheet["ID"] = r.id_string;
            for (auto& rf : r.review_flags) {
                ojson entry;
                entry["panel"] = "ID";
                for (auto it = rf.begin(); it != rf.end(); ++it) entry[it.key()] = it.value();
                sheet["needs_review"].push_back(entry);
            }
            for (auto& w : r.warnings) sheet["warnings"].push_back("ID: " + w);
        }
    }

    fs::path report_path = OUT_DIR / "final_grades.json";
    std::ofstream f(report_path);
    f << final_output.dump(4);
    f.close();

    std::cout << "Scoring complete. Results saved to " << report_path << "\n";

    int total_reviews = 0, total_warnings = 0;
    for (auto& [key, v] : sheets.items()) {
        total_reviews += static_cast<int>(v["needs_review"].size());
        total_warnings += static_cast<int>(v["warnings"].size());
    }
    if (total_reviews) std::cout << "NOTE: " << total_reviews << " bubble(s) across all sheets flagged for manual review.\n";
    if (total_warnings) std::cout << "NOTE: " << total_warnings << " geometry warning(s) across all sheets -- check final_grades.json.\n";

    write_summary(WORK_DIR, OUT_DIR, final_output);

    return 0;
}
