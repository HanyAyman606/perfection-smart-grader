// STAGE 6: Grid Assignment & Scoring
//
// Reads YOLO-format text files from Stage 5 and reads the exam layout from
// exam_config.json. Assigns each detected bubble to a question slot and
// choice lane, then emits per-question status objects and a global quiz
// status.
//
// Per-question statuses (priority order within a row):
//   "number_mismatch" — extra/missing rows detected
//   "canceled"        — any bubble in the row has cls == CLS_CANCELED
//   "review_needed"   — any bubble with cls==0 score < MODEL_CONF_TRUST
//   "answered"        — exactly one filled bubble, no review
//   "multiple"        — more than one filled bubble
//   "blank"           — all detected bubbles are empty
//
// Missing-row noise rule:
//   If a row slot has 0 detections AND num_choices >= MIN_CHOICES_FOR_NOISE:
//     → the slot is silently skipped (noise, not a question)
//   If num_choices < MIN_CHOICES_FOR_NOISE and 0 detections:
//     → "blank" (theoretically unreachable since config enforces >= 2 choices)
//
// Global status (priority order):
//   "failed"           — preprocessing_result.json written by an upstream stage
//   "number_mismatch"  — any extra/missing row in MCQ or extra letter row in ID
//   "retake"           — review_needed question count > RETAKE_REVIEW_THRESHOLD
//   "review_needed"    — at least one review_needed question
//   "success"          — no issues
//
// C++ port / overhaul of stage6_scoring.py.
#include <algorithm>
#include <cmath>
#include <fstream>
#include <iostream>
#include <limits>
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

// ordered_json preserves insertion order for object keys (Q1, Q2, ... in
// the order assigned, not alphabetically re-sorted).
using ojson = nlohmann::ordered_json;
namespace fs = std::filesystem;

namespace {

fs::path WORK_DIR, TXT_DIR, OUT_DIR;

// ============================================================
// TUNING CONSTANTS — grep "TUNING:" to find every threshold
// ============================================================

// TUNING: confidence threshold — cls 0 below this → review_needed
constexpr double MODEL_CONF_TRUST = 0.55;

// TUNING: within-row y-jitter; two boxes within this y-distance → same row
constexpr double ROW_Y_JITTER_PX = 15.0;

// TUNING: model class IDs
constexpr int CLS_FILLED   = 0;
constexpr int CLS_EMPTY    = 1;
constexpr int CLS_CANCELED = 2;

// TUNING: review_needed count strictly above this → global status "retake"
constexpr int RETAKE_REVIEW_THRESHOLD = 3;

// TUNING: MCQ panel geometry — at rest (correct bubble count), the left
// margin (leftmost lane center -> column's LEFT WALL) is expected to be
// about this many times the right margin (rightmost lane center -> column's
// RIGHT WALL), since each row's "Qn." label eats into the left side of the
// column but not the right. Only consulted when a row is over-detected:
//   observed ratio < this  -> left margin has collapsed -> extra bubble is
//                              on the LEFT (drop the leftmost lane)
//   observed ratio >= this -> right margin has collapsed -> extra bubble is
//                              on the RIGHT (drop the rightmost lane)
constexpr double EXTRA_BUBBLE_RATIO_MCQ = 2.0;

// TUNING: ID panel geometry — expected left/right margin ratio ≈ 1:1.
// Same decision rule as EXTRA_BUBBLE_RATIO_MCQ above.
constexpr double EXTRA_BUBBLE_RATIO_ID = 1.0;

// TUNING: if num_choices >= this and 0 bubbles detected in a slot → noise,
// silently skip (no question entry emitted).
// Since config enforces num_choices >= 2, this effectively means every
// completely empty MCQ slot is noise-skipped.
constexpr int MIN_CHOICES_FOR_NOISE = 2;

// TUNING: digit column y-alignment — slot cy deviation beyond this multiple
// of row_pitch across columns is flagged as a mismatch.
constexpr double DIGIT_ALIGN_TOLERANCE = 1.5;

// TUNING: per-row distance-ratio dedup.
// When a row has more detected boxes than expected choices, we sort them by cx
// and look at consecutive gaps. If the smallest gap is less than this fraction
// of the median gap, the two adjacent boxes are considered duplicates of the
// same bubble slot. The lower-confidence one is dropped.
// A value of 0.55 means: if any gap is < 55% of typical spacing → noise pair.
constexpr double ROW_GAP_RATIO_THRESHOLD = 0.55;

// ============================================================

enum class FillStatus { Filled, Empty, Review };

struct Box {
    int cls;
    double x1, y1, x2, y2;
    double score;
    double cx, cy;
    FillStatus status;
};

// =======================================================================
// GEOMETRY — forced-count column split
// =======================================================================
template <typename KeyFn>
std::vector<std::vector<Box*>> split_by_biggest_gaps(std::vector<Box*> boxes, KeyFn key,
                                                      int n_groups) {
    if (boxes.empty()) return {};
    if (n_groups <= 1 || static_cast<int>(boxes.size()) <= n_groups) {
        std::sort(boxes.begin(), boxes.end(),
                  [&](Box* a, Box* b) { return key(a) < key(b); });
        if (n_groups <= 1) return {boxes};
        std::vector<std::vector<Box*>> groups;
        for (Box* b : boxes) groups.push_back({b});
        return groups;
    }

    std::vector<Box*> ordered = boxes;
    std::sort(ordered.begin(), ordered.end(),
              [&](Box* a, Box* b) { return key(a) < key(b); });

    std::vector<std::pair<double, int>> gaps;
    for (size_t i = 0; i + 1 < ordered.size(); ++i)
        gaps.emplace_back(key(ordered[i + 1]) - key(ordered[i]),
                          static_cast<int>(i));
    std::sort(gaps.begin(), gaps.end(),
              [](auto& a, auto& b) { return a.first > b.first; });

    std::vector<int> split_after;
    for (int i = 0; i < n_groups - 1 && i < static_cast<int>(gaps.size()); ++i)
        split_after.push_back(gaps[i].second);
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

std::vector<std::vector<Box*>> cluster_columns(std::vector<Box*> boxes,
                                                int expected_n) {
    if (boxes.empty()) return {};
    return split_by_biggest_gaps(
        boxes, [](Box* b) { return b->cx; }, expected_n);
}

// =======================================================================
// GEOMETRY — natural row clustering (jitter-based, NOT forced count)
// =======================================================================
template <typename KeyFn>
std::vector<std::vector<Box*>> natural_clusters(std::vector<Box*> boxes, KeyFn key,
                                                 double jitter) {
    if (boxes.empty()) return {};
    std::vector<Box*> ordered = boxes;
    std::sort(ordered.begin(), ordered.end(),
              [&](Box* a, Box* b) { return key(a) < key(b); });
    std::vector<std::vector<Box*>> clusters = {{ordered[0]}};
    for (size_t i = 1; i < ordered.size(); ++i) {
        Box* b = ordered[i];
        if (std::abs(key(b) - key(clusters.back().back())) < jitter)
            clusters.back().push_back(b);
        else
            clusters.push_back({b});
    }
    return clusters;
}

// Position-anchored slot assignment: maps detected clusters to exactly
// expected_rows slots by absolute y-position. Slots with no detection
// return an empty vector (caller decides blank vs. noise).
template <typename KeyFn>
std::vector<std::vector<Box*>> anchor_rows_to_slots(std::vector<Box*> boxes,
                                                     KeyFn key_y,
                                                     int expected_rows,
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
    for (size_t i = 0; i + 1 < cluster_ys.size(); ++i)
        gaps.push_back(cluster_ys[i + 1] - cluster_ys[i]);
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
        int slot_idx = static_cast<int>(
            std::lround((cy - anchor_y) / row_pitch));
        slot_idx = std::clamp(slot_idx, 0, expected_rows - 1);

        if (used_slots.count(slot_idx)) {
            bool placed = false;
            for (int delta = 1; delta < expected_rows && !placed; ++delta) {
                for (int cand : {slot_idx - delta, slot_idx + delta}) {
                    if (cand >= 0 && cand < expected_rows &&
                        !used_slots.count(cand)) {
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
// FILL DECISION
// =======================================================================
FillStatus decide_fill_status(const Box& box) {
    if (box.cls == CLS_FILLED)
        return box.score >= MODEL_CONF_TRUST ? FillStatus::Filled
                                              : FillStatus::Review;
    return FillStatus::Empty;  // CLS_EMPTY, CLS_CANCELED, or anything else
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
            b.cls   = std::stoi(parts[0]);
            b.x1    = std::stod(parts[1]);
            b.y1    = std::stod(parts[2]);
            b.x2    = std::stod(parts[3]);
            b.y2    = std::stod(parts[4]);
            b.score = std::stod(parts[5]);
            b.cx    = (b.x1 + b.x2) / 2.0;
            b.cy    = (b.y1 + b.y2) / 2.0;
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

// =======================================================================
// GEOMETRY FILTER — discard extra lanes using margin ratio rule.
// Only called when detected lane count > target_count.
//
// left_edge/right_edge MUST be a fixed physical wall (e.g. the column's
// designed boundary), NOT derived from the same boxes being filtered —
// otherwise the outermost box always defines its own "wall" and the
// margin collapses to ~half a box-width regardless of whether it's noise.
//
// extra_ratio_threshold: the at-rest (correct-count) left/right margin
//   ratio. observed ratio < this -> extra bubble is on the LEFT (its
//   margin collapsed); observed ratio >= this -> extra is on the RIGHT.
// =======================================================================
void filter_extra_lanes(std::vector<std::vector<Box*>>& lanes,
                         double left_edge, double right_edge,
                         int target_count,
                         double extra_ratio_threshold) {
    auto lane_center = [](const std::vector<Box*>& lane) {
        double sum = 0;
        for (const Box* b : lane) sum += b->cx;
        return sum / lane.size();
    };

    while (static_cast<int>(lanes.size()) > target_count) {
        double left_center  = lane_center(lanes.front());
        double right_center = lane_center(lanes.back());
        double left_margin  = left_center - left_edge;
        double right_margin = right_edge - right_center;
        if (left_margin  < 1.0) left_margin  = 1.0;
        if (right_margin < 1.0) right_margin = 1.0;
        double ratio = left_margin / right_margin;

        if (ratio < extra_ratio_threshold)
            lanes.erase(lanes.begin());  // left margin collapsed -> extra on left
        else
            lanes.pop_back();            // right margin collapsed -> extra on right
    }
}

// =======================================================================
// PER-ROW DISTANCE-RATIO DEDUP
// When a row contains more detected boxes than expected choices we use two
// complementary strategies:
//
// 1. Gap-ratio filter: sort by cx, find the pair with smallest consecutive
//    gap. If min_gap / median_gap < ROW_GAP_RATIO_THRESHOLD the two boxes
//    are near-duplicates of the same slot — drop the lower-score one.
//
// 2. Margin-to-wall ratio filter: if (1) didn't help (gaps are all roughly
//    even, so there's no obvious duplicate pair — the extra bubble is a
//    genuinely separate stray lane, typically at one end of the row), fall
//    back to the same rule used column-wide in filter_extra_lanes: compare
//    the leftmost bubble's distance to the column's LEFT WALL against the
//    rightmost bubble's distance to the RIGHT WALL. At rest, left_margin /
//    right_margin ≈ extra_ratio_threshold (2.0 for MCQ, since each row's
//    "Qn." label eats into the left side of the column but not the right;
//    1.0 for the ID panel, which is symmetric). Whichever side's margin
//    has collapsed relative to that expected ratio is where the extra
//    bubble sits, so that end box is dropped.
//
//    col_left/col_right MUST be the column's fixed physical wall (same
//    values used for filter_extra_lanes), NOT derived from this row's own
//    boxes — otherwise the outermost box always defines its own "wall" and
//    the margin ratio collapses to ~1 regardless of whether it's noise.
//
// Both passes repeat until the row is down to num_options.
// =======================================================================

void dedup_row_by_gap_ratio(std::vector<Box*>& row, int num_options,
                             const std::vector<double>& lane_centers,
                             double col_left, double col_right,
                             double extra_ratio_threshold) {
    (void)lane_centers;  // kept in the signature for call-site compatibility

    // ── Pass 1: gap-ratio ──────────────────────────────────────────────────
    while (static_cast<int>(row.size()) > num_options) {
        std::sort(row.begin(), row.end(),
                  [](const Box* a, const Box* b) { return a->cx < b->cx; });

        std::vector<double> gaps;
        for (size_t i = 0; i + 1 < row.size(); ++i)
            gaps.push_back(row[i + 1]->cx - row[i]->cx);

        if (gaps.empty()) break;

        std::vector<double> sorted_gaps = gaps;
        std::sort(sorted_gaps.begin(), sorted_gaps.end());
        double median_gap = sorted_gaps[sorted_gaps.size() / 2];
        if (median_gap < 1.0) break;

        int min_idx = static_cast<int>(
            std::min_element(gaps.begin(), gaps.end()) - gaps.begin());
        double min_gap = gaps[min_idx];

        if (min_gap / median_gap >= ROW_GAP_RATIO_THRESHOLD) break;

        Box* left  = row[min_idx];
        Box* right = row[min_idx + 1];
        if (left->score >= right->score)
            row.erase(row.begin() + min_idx + 1);
        else
            row.erase(row.begin() + min_idx);
    }

    // ── Pass 2: margin-to-wall ratio drop ───────────────────────────────────
    // Still over-count → the extra bubble is an isolated stray at one end
    // (gap-ratio pass found no adjacent duplicate). Decide which end using
    // the same wall-margin ratio rule as filter_extra_lanes, applied to
    // this row's actual end boxes instead of column-wide lane groups.
    while (static_cast<int>(row.size()) > num_options) {
        std::sort(row.begin(), row.end(),
                  [](const Box* a, const Box* b) { return a->cx < b->cx; });

        double left_margin  = row.front()->cx - col_left;
        double right_margin = col_right - row.back()->cx;
        if (left_margin  < 1.0) left_margin  = 1.0;
        if (right_margin < 1.0) right_margin = 1.0;
        double ratio = left_margin / right_margin;

        if (ratio < extra_ratio_threshold)
            row.erase(row.begin());   // left margin collapsed -> extra on left
        else
            row.pop_back();           // right margin collapsed -> extra on right
    }
}

// =======================================================================
// QUESTION BUILDER — creates the standard per-question JSON object
// =======================================================================
ojson make_question(const std::string& status, const ojson& answer) {
    ojson q;
    q["status"] = status;
    q["answer"] = answer;
    return q;
}

// =======================================================================
// MCQ RESULT
// =======================================================================
struct McqResult {
    ojson questions     = ojson::object();  // {"Q1":{status,answer}, ...}
    int review_count    = 0;
    std::vector<std::string> mismatch_causes;
    ojson review_flags  = ojson::array();   // legacy: [{panel,question,candidates}]
};

// =======================================================================
// MCQ PROCESSING
// =======================================================================
McqResult process_mcq(std::vector<Box>& boxes,
                       const examcfg::ExamConfig& config) {
    for (auto& b : boxes) b.status = decide_fill_status(b);

    int expected_cols = config.num_mcq_columns;
    std::vector<int>         expected_counts = config.mcq_column_counts();
    std::vector<std::string> mcq_options     = config.mcq_options();
    int num_options = static_cast<int>(mcq_options.size());

    std::vector<Box*> all_ptrs;
    for (auto& b : boxes) all_ptrs.push_back(&b);
    auto col_groups = cluster_columns(all_ptrs, expected_cols);

    McqResult out;
    if (static_cast<int>(col_groups.size()) < expected_cols) {
        out.mismatch_causes.push_back("missing mcq column(s)");
    } else if (static_cast<int>(col_groups.size()) > expected_cols) {
        out.mismatch_causes.push_back("extra mcq column(s)");
    }

    // Panel-wide left/right edges (for geometry filter on the whole image)
    double panel_left  = std::numeric_limits<double>::max();
    double panel_right = std::numeric_limits<double>::lowest();
    for (const auto& b : boxes) {
        panel_left  = std::min(panel_left,  b.x1);
        panel_right = std::max(panel_right, b.x2);
    }
    double col_w_approx = (panel_right - panel_left) /
                           std::max(1, expected_cols);

    int current_q_num = 1;

    for (size_t col_idx = 0; col_idx < col_groups.size(); ++col_idx) {
        auto& col_boxes   = col_groups[col_idx];
        int expected_rows = col_idx < expected_counts.size()
                                ? expected_counts[col_idx] : 0;
        if (expected_rows == 0) continue;

        // ── No detections at all in this column ──────────────────────────
        if (col_boxes.empty()) {
            // Skip all rows — they are noise (no bubbles found at all)
            current_q_num += expected_rows;
            continue;
        }

        // ── Reject stray noise boxes far outside this column's X center ──
        std::vector<double> cxs;
        for (Box* b : col_boxes) cxs.push_back(b->cx);
        double col_median_x = median_of(cxs);
        std::vector<Box*> valid_boxes;
        for (Box* b : col_boxes)
            if (std::abs(b->cx - col_median_x) < col_w_approx * 0.45)
                valid_boxes.push_back(b);

        if (valid_boxes.empty()) {
            current_q_num += expected_rows;
            continue;
        }

        // ── Column-local left/right WALLS for geometry filter ──────────────
        // Fixed physical boundary of this column, derived from the panel-wide
        // edges (aggregated over every box on the whole MCQ image, so one
        // noisy row can't skew it) split evenly by column index. Deliberately
        // NOT taken from valid_boxes here — that would make each lane define
        // its own wall and collapse the margin ratio to ~1 regardless of
        // whether a lane is genuine or noise (see filter_extra_lanes above).
        double col_left  = panel_left + col_idx * col_w_approx;
        double col_right = col_left + col_w_approx;

        // ── Establish X-lanes (A, B, C, D, ...) ──────────────────────────
        auto lanes = split_by_biggest_gaps(
            valid_boxes, [](Box* b) { return b->cx; }, num_options);

        // Geometry filter: discard extra lanes (only when detected > expected)
        if (static_cast<int>(lanes.size()) > num_options) {
            filter_extra_lanes(lanes, col_left, col_right,
                               num_options, EXTRA_BUBBLE_RATIO_MCQ);
            // If still mismatched after geometry pass, force-truncate
            if (static_cast<int>(lanes.size()) > num_options) {
                while (static_cast<int>(lanes.size()) > num_options)
                    lanes.pop_back();
            }
        }

        // Lane centers
        std::vector<double> lane_centers;
        for (auto& lane : lanes) {
            if (lane.empty()) continue;
            std::vector<double> lc;
            for (Box* b : lane) lc.push_back(b->cx);
            lane_centers.push_back(median_of(lc));
        }
        if (static_cast<int>(lane_centers.size()) != num_options) {
            // Fallback: synthesize evenly-spaced lane centers
            double min_x = valid_boxes[0]->cx, max_x = valid_boxes[0]->cx;
            for (Box* b : valid_boxes) {
                min_x = std::min(min_x, b->cx);
                max_x = std::max(max_x, b->cx);
            }
            double step = (max_x - min_x) / std::max(1, num_options - 1);
            lane_centers.clear();
            for (int i = 0; i < num_options; ++i)
                lane_centers.push_back(min_x + i * step);
        }

        // ── Count detected rows (natural clustering) ──────────────────────
        auto raw_clusters = natural_clusters(
            valid_boxes, [](Box* b) { return b->cy; }, ROW_Y_JITTER_PX);
        
        // Filter out fake/noise rows (rows with < expected_choices - 1 bubbles)
        std::vector<std::vector<Box*>> valid_clusters;
        for (auto& cluster : raw_clusters) {
            if (static_cast<int>(cluster.size()) >= num_options - 1) {
                valid_clusters.push_back(cluster);
            }
        }
        int detected_rows = static_cast<int>(valid_clusters.size());

        // Process sequentially
        for (int row_idx = 0; row_idx < expected_rows; ++row_idx) {
            std::string q_key = "Q" + std::to_string(current_q_num + row_idx);

            if (row_idx >= detected_rows) {
                break;
            }

            auto& row = valid_clusters[row_idx];

            // ── Per-row distance-ratio dedup ──────────────────────────────
            // If the model detected more boxes in this row than expected choices,
            // use cx-gap analysis + lane-outlier drop to remove the noise box.
            if (static_cast<int>(row.size()) > num_options)
                dedup_row_by_gap_ratio(row, num_options, lane_centers,
                                       col_left, col_right,
                                       EXTRA_BUBBLE_RATIO_MCQ);

            // Assign each detected box to the nearest lane,
            // but first establish lane grid bounds so we can reject strays.
            double lane_pitch = 0;
            double lane_grid_left  = 0;
            double lane_grid_right = std::numeric_limits<double>::max();
            if (lane_centers.size() >= 2) {
                std::vector<double> lc_sorted = lane_centers;
                std::sort(lc_sorted.begin(), lc_sorted.end());
                std::vector<double> lc_gaps_row;
                for (size_t i = 0; i + 1 < lc_sorted.size(); ++i)
                    lc_gaps_row.push_back(lc_sorted[i + 1] - lc_sorted[i]);
                std::sort(lc_gaps_row.begin(), lc_gaps_row.end());
                lane_pitch = lc_gaps_row[lc_gaps_row.size() / 2];
                // A box is in-grid if it falls within half a pitch of the outermost lanes.
                lane_grid_left  = lc_sorted.front() - lane_pitch * 0.5;
                lane_grid_right = lc_sorted.back()  + lane_pitch * 0.5;
            }

            std::vector<std::string> filled_opts, review_opts;
            for (Box* b : row) {
                // Guard: if this box is outside the established lane grid entirely,
                // it's a stray smudge / noise outside the bubble columns — skip it.
                if (lane_pitch > 0 &&
                    (b->cx < lane_grid_left || b->cx > lane_grid_right))
                    continue;

                int lane_idx = 0;
                double best_d = std::numeric_limits<double>::infinity();
                for (size_t li = 0; li < lane_centers.size(); ++li) {
                    double d = std::abs(b->cx - lane_centers[li]);
                    if (d < best_d) { best_d = d; lane_idx = static_cast<int>(li); }
                }

                const std::string& opt = mcq_options[lane_idx];

                switch (b->status) {
                    case FillStatus::Filled:
                        if (std::find(filled_opts.begin(), filled_opts.end(), opt) ==
                            filled_opts.end())
                            filled_opts.push_back(opt);
                        break;
                    case FillStatus::Review:
                        if (std::find(review_opts.begin(), review_opts.end(), opt) ==
                            review_opts.end())
                            review_opts.push_back(opt);
                        break;
                    default: break;
                }
            }
            std::sort(filled_opts.begin(), filled_opts.end());
            std::sort(review_opts.begin(), review_opts.end());

            ojson q_entry;

            if (!review_opts.empty() && filled_opts.empty()) {
                // Only review-confidence bubbles
                q_entry = make_question("review_needed", ojson(review_opts));
                out.review_count++;
                ojson rf;
                rf["question"]   = q_key;
                rf["candidates"] = review_opts;
                out.review_flags.push_back(rf);

            } else if (!filled_opts.empty() && !review_opts.empty()) {
                // Mixed filled + review → review_needed
                std::set<std::string> merged(filled_opts.begin(), filled_opts.end());
                merged.insert(review_opts.begin(), review_opts.end());
                ojson ans = std::vector<std::string>(merged.begin(), merged.end());
                q_entry = make_question("review_needed", ans);
                out.review_count++;
                ojson rf;
                rf["question"]   = q_key;
                rf["candidates"] = std::vector<std::string>(merged.begin(),
                                                             merged.end());
                out.review_flags.push_back(rf);

            } else if (filled_opts.size() == 1 && review_opts.empty()) {
                q_entry = make_question("answered", ojson(filled_opts[0]));

            } else if (filled_opts.size() > 1) {
                q_entry = make_question("multiple", ojson(filled_opts));

            } else {
                // All detected bubbles are empty
                q_entry = make_question("blank", ojson(nullptr));
            }

            out.questions[q_key] = q_entry;
        }

        // ── Extra or Missing rows beyond expected ───────────────────────────
        if (detected_rows > expected_rows) {
            int extra = detected_rows - expected_rows;
            out.mismatch_causes.push_back("extra mcq row(s)");
            for (int e = 0; e < extra; ++e) {
                std::string q_key = "Q" + std::to_string(current_q_num + expected_rows + e);
                out.questions[q_key] = make_question("number_mismatch",
                                                     ojson(nullptr));
            }
        } else if (detected_rows < expected_rows) {
            out.mismatch_causes.push_back("missing mcq row(s)");
        }
        
        current_q_num += std::max(expected_rows, detected_rows);
    }  // end column loop

    return out;
}

// =======================================================================
// ID RESULT
// =======================================================================
struct IdResult {
    std::string id_string;
    std::vector<std::string> mismatch_causes;
    std::vector<std::string> id_errors;
    ojson review_flags  = ojson::array();
};

// =======================================================================
// ID PROCESSING
// =======================================================================
IdResult process_id(std::vector<Box>& boxes, const examcfg::ExamConfig& config) {
    for (auto& b : boxes) b.status = decide_fill_status(b);

    std::vector<Box*> all_ptrs;
    for (auto& b : boxes) all_ptrs.push_back(&b);

    int id_num_columns = config.id_num_columns();
    auto col_groups    = cluster_columns(all_ptrs, id_num_columns);

    IdResult out;
    if (static_cast<int>(col_groups.size()) < id_num_columns) {
        out.mismatch_causes.push_back("missing id column(s)");
    } else if (static_cast<int>(col_groups.size()) > id_num_columns) {
        out.mismatch_causes.push_back("extra id column(s)");
    }

    const std::string& id_letters = config.id_letters;

    // Panel-wide left/right edges for geometry filter
    double panel_left  = std::numeric_limits<double>::max();
    double panel_right = std::numeric_limits<double>::lowest();
    for (const auto& b : boxes) {
        panel_left  = std::min(panel_left,  b.x1);
        panel_right = std::max(panel_right, b.x2);
    }

    std::string id_result;

    for (int col_idx = 0; col_idx < id_num_columns; ++col_idx) {
        std::vector<Box*> col_boxes =
            col_idx < static_cast<int>(col_groups.size())
                ? col_groups[col_idx]
                : std::vector<Box*>{};

        bool is_letter_col = col_idx < config.id_letter_cols;
        int  expected_rows = is_letter_col
                                 ? static_cast<int>(id_letters.size())
                                 : 10;  // digit columns always 0-9

        // ── Column-local edges for geometry filter ────────────────────────
        double col_left  = std::numeric_limits<double>::max();
        double col_right = std::numeric_limits<double>::lowest();
        for (Box* b : col_boxes) {
            col_left  = std::min(col_left,  b->x1);
            col_right = std::max(col_right, b->x2);
        }

        // ── Detect row count (natural clustering) ─────────────────────────
        auto raw_clusters = natural_clusters(
            col_boxes, [](Box* b) { return b->cy; }, ROW_Y_JITTER_PX);
        int detected_rows = static_cast<int>(raw_clusters.size());

        if (is_letter_col) {
            // Extra letter rows → number_mismatch
            if (detected_rows > expected_rows) {
                out.mismatch_causes.push_back("extra id row(s)");
                id_result += "!";  // mismatch placeholder in id string
                continue;
            }
        } else {
            // Digit column: silently discard trailing rows beyond 9 (noise)
            // anchor_rows_to_slots will naturally clamp to expected_rows=10
        }

        // ── Position-anchored slot assignment ─────────────────────────────
        auto row_slots = anchor_rows_to_slots(
            col_boxes, [](Box* b) { return b->cy; }, expected_rows);

        // ── Collect filled / review values for this column ────────────────
        // For ID columns each row represents one value (1 bubble per row).
        // We look for the highest-score filled bubble across the column.
        std::vector<std::string> filled_vals, review_vals;

        for (int row_idx = 0;
             row_idx < static_cast<int>(row_slots.size()); ++row_idx) {
            auto& row = row_slots[row_idx];
            if (row.empty()) continue;  // missing slot → blank (normal for ID)

            // Pick the highest-score box in this row
            std::vector<Box*> row_sorted = row;
            std::sort(row_sorted.begin(), row_sorted.end(),
                      [](Box* a, Box* b) { return a->score > b->score; });
            Box* best = row_sorted[0];

            std::string label =
                (is_letter_col &&
                 row_idx < static_cast<int>(id_letters.size()))
                    ? std::string(1, id_letters[row_idx])
                    : std::to_string(row_idx);

            if (best->status == FillStatus::Filled)
                filled_vals.push_back(label);
            else if (best->status == FillStatus::Review)
                review_vals.push_back(label);
            // Empty / Canceled → not selected
        }

        // ── Determine column result ───────────────────────────────────────
        std::vector<std::string> candidates = filled_vals;
        candidates.insert(candidates.end(), review_vals.begin(), review_vals.end());

        if (candidates.empty()) {
            id_result += "-";
            out.id_errors.push_back(is_letter_col ? "id_letter_blank" : "id_digit_blank");
        } else if (candidates.size() > 1) {
            id_result += "?";
            ojson rf;
            rf["column"] = col_idx;
            rf["candidates"] = candidates;
            out.review_flags.push_back(rf);
            
            out.id_errors.push_back(is_letter_col ? "id_letter_multiple" : "id_digit_multiple");
        } else if (!review_vals.empty()) {
            id_result += "?";
            ojson rf;
            rf["column"] = col_idx;
            rf["candidates"] = candidates;
            out.review_flags.push_back(rf);
        } else {
            id_result += filled_vals[0];
        }
    }  // end column loop

    out.id_string = id_result;
    return out;
}

}  // namespace

// =======================================================================
// MAIN
// =======================================================================
int main(int argc, char** argv) {
    fs::path here = argc > 1 ? fs::path(argv[1]) : fs::current_path();
    WORK_DIR = fs::absolute(here);
    TXT_DIR  = WORK_DIR / "stage5";
    OUT_DIR  = WORK_DIR / "stage6";
    common::ensure_dir(OUT_DIR);

    // ── Check for upstream preprocessing failure ──────────────────────────
    fs::path preproc_path = WORK_DIR / "preprocessing_result.json";
    std::string preproc_status, preproc_cause;
    if (fs::exists(preproc_path)) {
        try {
            std::ifstream pf(preproc_path);
            auto pj = nlohmann::json::parse(pf);
            preproc_status = pj.value("status", "");
            preproc_cause  = pj.value("cause",  "");
        } catch (...) {}
    }

    // ── Load exam config ──────────────────────────────────────────────────
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

    // ── Build output skeleton ─────────────────────────────────────────────
    ojson final_output;
    final_output["exam_id"]               = config.exam_id;
    final_output["exam_type"]             = config.exam_type;
    final_output["num_questions_expected"] = config.num_questions;
    final_output["sheets"]                = ojson::object();
    ojson& sheets = final_output["sheets"];

    // ── If preprocessing failed, emit a minimal failed result and exit ────
    if (preproc_status == "failed") {
        // Find the image name
        std::string sheet_name = "sheet";
        fs::path input_dir = WORK_DIR / "input";
        if (fs::exists(input_dir)) {
            for (const auto& entry : fs::directory_iterator(input_dir)) {
                if (entry.is_regular_file()) {
                    sheet_name = entry.path().stem().string();
                    break;
                }
            }
        }

        // Create one "sheet" entry representing the single processed image
        ojson sheet;
        sheet["global_status"] = "failed";
        sheet["cause"]         = preproc_cause.empty() ? "preprocessing_error"
                                                        : preproc_cause;
        sheets[sheet_name] = sheet;

        fs::path report_path = OUT_DIR / "final_grades.json";
        std::ofstream rf(report_path);
        rf << final_output.dump(4);
        std::cout << "Pipeline stopped: preprocessing failed (" << preproc_cause
                  << "). Result at " << report_path << "\n";
        return 0;
    }

    // ── Process all stage-5 text files ───────────────────────────────────
    auto txt_files = common::glob_ext(TXT_DIR, {".txt"});

    for (const auto& txt_path : txt_files) {
        std::string basename     = txt_path.filename().string();
        std::vector<Box> boxes   = parse_txt_file(txt_path);
        if (boxes.empty()) continue;

        std::string base_filename =
            common::strip_suffix(basename, "_id_final.txt");
        base_filename =
            common::strip_suffix(base_filename, "_mcq_final.txt");

        if (!sheets.contains(base_filename)) {
            ojson sheet;
            sheet["global_status"] = "success";
            sheet["cause"]         = ojson(nullptr);
            sheet["ID"]            = "";
            sheet["MCQ"]           = ojson::object();
            sheet["needs_review"]  = ojson::array();
            // Internal tracking (removed before final dump isn't needed —
            // we use these fields directly)
            sheet["_review_count"] = 0;
            sheet["_mismatch_causes"] = ojson::array();
            sheet["_id_errors"] = ojson::array();
            sheets[base_filename]  = sheet;
        }
        ojson& sheet = sheets[base_filename];

        if (common::contains(basename, "_mcq_")) {
            McqResult r = process_mcq(boxes, config);
            sheet["MCQ"] = r.questions;

            // Accumulate review count and mismatch tracking
            int prev_review = sheet["_review_count"].get<int>();
            sheet["_review_count"] = prev_review + r.review_count;

            for (const auto& mc : r.mismatch_causes) {
                sheet["_mismatch_causes"].push_back(mc);
            }

            for (auto& rf : r.review_flags) {
                ojson entry;
                entry["panel"] = "MCQ";
                for (auto it = rf.begin(); it != rf.end(); ++it)
                    entry[it.key()] = it.value();
                sheet["needs_review"].push_back(entry);
            }

        } else if (common::contains(basename, "_id_")) {
            IdResult r = process_id(boxes, config);
            sheet["ID"] = r.id_string;

            for (const auto& mc : r.mismatch_causes) {
                sheet["_mismatch_causes"].push_back(mc);
            }

            for (const auto& err : r.id_errors) {
                sheet["_id_errors"].push_back(err);
            }

            for (auto& rf : r.review_flags) {
                ojson entry;
                entry["panel"] = "ID";
                for (auto it = rf.begin(); it != rf.end(); ++it)
                    entry[it.key()] = it.value();
                sheet["needs_review"].push_back(entry);
            }
        }
    }

    // ── Compute global_status for each sheet, then clean up internal keys ─
    for (auto& [key, sheet] : sheets.items()) {
        // Skip sheets that were already set to "failed"
        if (sheet["global_status"] == "failed") continue;

        int   review_count  = sheet["_review_count"].get<int>();
        auto  mcauses       = sheet["_mismatch_causes"];
        auto  id_errors     = sheet["_id_errors"];

        std::string gstatus;
        // Sort and unique the mismatch_causes array
        std::vector<std::string> unique_mcauses = mcauses.get<std::vector<std::string>>();
        std::sort(unique_mcauses.begin(), unique_mcauses.end());
        unique_mcauses.erase(std::unique(unique_mcauses.begin(), unique_mcauses.end()), unique_mcauses.end());
        
        // Sort and unique the id_errors array
        std::vector<std::string> unique_id_errors = id_errors.get<std::vector<std::string>>();
        std::sort(unique_id_errors.begin(), unique_id_errors.end());
        unique_id_errors.erase(std::unique(unique_id_errors.begin(), unique_id_errors.end()), unique_id_errors.end());

        ojson gcause = ojson(nullptr);

        if (!unique_mcauses.empty()) {
            gstatus = "number_mismatch";
            if (unique_mcauses.size() == 1) {
                gcause = unique_mcauses[0];
            } else {
                gcause = unique_mcauses;
            }
        } else if (!unique_id_errors.empty()) {
            gstatus = "invalid_id";
            if (unique_id_errors.size() == 1) {
                gcause = unique_id_errors[0];
            } else {
                gcause = unique_id_errors;
            }
        } else if (review_count > RETAKE_REVIEW_THRESHOLD) {
            gstatus = "retake";
            gcause  = "retake_threshold_exceeded";
        } else if (review_count > 0) {
            gstatus = "review_needed";
            gcause  = "review_needed";
        } else {
            gstatus = "success";
            gcause  = ojson(nullptr);
        }

        sheet["global_status"] = gstatus;
        sheet["cause"]         = gcause;

        // Remove internal tracking keys
        sheet.erase("_review_count");
        sheet.erase("_mismatch_causes");
        sheet.erase("_id_errors");
    }

    // ── Write final JSON ──────────────────────────────────────────────────
    fs::path report_path = OUT_DIR / "final_grades.json";
    std::ofstream f(report_path);
    f << final_output.dump(4);

    std::cout << "Scoring complete. Results saved to " << report_path << "\n";

    // Summary stats
    int total_reviews = 0, total_warnings = 0, total_mismatches = 0;
    for (auto& [key, v] : sheets.items()) {
        total_reviews   += static_cast<int>(v["needs_review"].size());
        total_warnings  += static_cast<int>(v["warnings"].size());
        if (v["global_status"] == "number_mismatch" ||
            v["global_status"] == "retake")
            total_mismatches++;
    }
    if (total_mismatches)
        std::cout << "NOTE: " << total_mismatches
                  << " sheet(s) have number_mismatch or retake status.\n";
    if (total_reviews)
        std::cout << "NOTE: " << total_reviews
                  << " bubble(s) flagged for manual review.\n";
    if (total_warnings)
        std::cout << "NOTE: " << total_warnings
                  << " geometry warning(s) — check final_grades.json.\n";

    return 0;
}