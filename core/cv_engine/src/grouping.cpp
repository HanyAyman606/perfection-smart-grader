#include "grouping.h"
#include <algorithm>
#include <cmath>

namespace grouping {

namespace {

float mean_cy(const std::vector<Detection>& dets) {
    float sum = 0.0f;
    for (const auto& d : dets) sum += d.cy;
    return sum / dets.size();
}

float mean_cx(const std::vector<Detection>& dets) {
    float sum = 0.0f;
    for (const auto& d : dets) sum += d.cx;
    return sum / dets.size();
}

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

std::vector<std::vector<Detection>> group_by_rows(const std::vector<Detection>& detections, int tolerance) {
    if (detections.empty()) return {};

    std::vector<Detection> sorted_dets = detections;
    std::sort(sorted_dets.begin(), sorted_dets.end(), [](const Detection& a, const Detection& b) {
        return a.cy < b.cy;
    });

    std::vector<std::vector<Detection>> rows;
    std::vector<Detection> current_row = {sorted_dets[0]};
    float row_mean_y = sorted_dets[0].cy;

    for (size_t i = 1; i < sorted_dets.size(); ++i) {
        const auto& det = sorted_dets[i];
        if (std::abs(det.cy - row_mean_y) <= tolerance) {
            current_row.push_back(det);
            row_mean_y = mean_cy(current_row);
        } else {
            rows.push_back(sort_by_cx(current_row));
            current_row = {det};
            row_mean_y = det.cy;
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
    float col_mean_x = sorted_dets[0].cx;

    for (size_t i = 1; i < sorted_dets.size(); ++i) {
        const auto& det = sorted_dets[i];
        if (std::abs(det.cx - col_mean_x) <= tolerance) {
            current_col.push_back(det);
            col_mean_x = mean_cx(current_col);
        } else {
            columns.push_back(sort_by_cy(current_col));
            current_col = {det};
            col_mean_x = det.cx;
        }
    }

    if (!current_col.empty()) {
        columns.push_back(sort_by_cy(current_col));
    }

    return columns;
}

} // namespace grouping
