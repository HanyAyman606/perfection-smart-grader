#include "grouping.h"
#include <algorithm>
#include <cmath>

namespace grouping {

namespace {

std::vector<Detection> sort_by_cx(std::vector<Detection> dets) {
    std::sort(dets.begin(), dets.end(), [](const Detection& a, const Detection& b) {
        return a.cx < b.cx;
    });
    return dets;
}

std::vector<Detection> sort_by_cy(std::vector<Detection> dets) {
    std::sort(dets.begin(), dets.end(), [](const Detection& a, const Detection& b) {
        return a.cy < b.cy;
    });
    return dets;
}

} // namespace

// ─────────────────────────────────────────────────────────────────────────
// Both grouping functions below use gap-based clustering off actual box
// edges, not distance from a running mean of centers.
//
// The old approach compared each new point only to the *mean* of the
// group built so far. That mean drifts as points are added, so:
//   - a stray/misdetected box sitting between two real
//     columns/rows can peel off into its own tiny group (it's far enough
//     from the current group's mean to split), and then a *real* next
//     column/row's points land close enough to that stray group's mean to
//     get folded into it instead of starting their own group — silently
//     shifting every group index after the stray detection.
//   - because indexing downstream (process_student_id, process_questions)
//     is purely positional (columns[col_idx]), one spurious extra group
//     anywhere before the end pushes a real trailing column/row out past
//     the expected count and it gets dropped/misread entirely.
//
// Gap-based clustering avoids the drift: we track the furthest-right
// (or furthest-down) edge seen so far in the current group, and compare
// only that fixed edge to the next point's near edge — never a floating
// average. Two boxes that overlap can never end up in different groups
// (overlap ⇒ negative gap ⇒ always merged), and a new group only starts
// once an actual gap of at least `tolerance` pixels is seen.
// ─────────────────────────────────────────────────────────────────────────

std::vector<std::vector<Detection>> group_by_rows(const std::vector<Detection>& detections, int tolerance) {
    if (detections.empty()) return {};

    std::vector<Detection> sorted_dets = detections;
    std::sort(sorted_dets.begin(), sorted_dets.end(), [](const Detection& a, const Detection& b) {
        return a.cy < b.cy;
    });

    std::vector<std::vector<Detection>> rows;
    std::vector<Detection> current_row = {sorted_dets[0]};
    float row_max_bottom = sorted_dets[0].cy + sorted_dets[0].h / 2.0f;

    for (size_t i = 1; i < sorted_dets.size(); ++i) {
        const auto& det = sorted_dets[i];
        float det_top = det.cy - det.h / 2.0f;
        float gap = det_top - row_max_bottom;  // negative/zero => boxes overlap or touch

        if (gap <= tolerance) {
            current_row.push_back(det);
            row_max_bottom = std::max(row_max_bottom, det.cy + det.h / 2.0f);
        } else {
            rows.push_back(sort_by_cx(current_row));
            current_row = {det};
            row_max_bottom = det.cy + det.h / 2.0f;
        }
    }

    if (!current_row.empty()) {
        rows.push_back(sort_by_cx(current_row));
    }

    return rows;
}

std::vector<std::vector<Detection>> group_by_columns(const std::vector<Detection>& detections, int tolerance) {
    if (detections.empty()) return {};

    std::vector<Detection> sorted_dets = detections;
    std::sort(sorted_dets.begin(), sorted_dets.end(), [](const Detection& a, const Detection& b) {
        return a.cx < b.cx;
    });

    std::vector<std::vector<Detection>> columns;
    std::vector<Detection> current_col = {sorted_dets[0]};
    float col_max_right = sorted_dets[0].cx + sorted_dets[0].w / 2.0f;

    for (size_t i = 1; i < sorted_dets.size(); ++i) {
        const auto& det = sorted_dets[i];
        float det_left = det.cx - det.w / 2.0f;
        float gap = det_left - col_max_right;  // negative/zero => boxes overlap or touch

        if (gap <= tolerance) {
            current_col.push_back(det);
            col_max_right = std::max(col_max_right, det.cx + det.w / 2.0f);
        } else {
            columns.push_back(sort_by_cy(current_col));
            current_col = {det};
            col_max_right = det.cx + det.w / 2.0f;
        }
    }

    if (!current_col.empty()) {
        columns.push_back(sort_by_cy(current_col));
    }

    return columns;
}

} // namespace grouping