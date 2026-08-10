#pragma once

#include <opencv2/opencv.hpp>
#include <optional>
#include <tuple>
#include <string>
#include <vector>
#include "config.h"

namespace preprocessing {

/**
 * @brief Quality/confidence signal produced alongside the cropped panels,
 * so the frontend can decide whether to force a retake or let the proctor
 * preview-and-continue.
 *
 * warnings: machine-readable reason codes, e.g.
 *   "BLUR"                       - image too blurry (low Laplacian variance)
 *   "NO_ID_PANEL"                - ID panel quad not found
 *   "NO_MCQ_PANEL"                - MCQ panel quad not found
 *   "LOW_CONTOUR_CONFIDENCE"     - too many/ambiguous quad candidates found
 *   "ORIENTATION_LOW_CONFIDENCE" - orientation guessed without a reliable landmark
 *
 * orientation_reason: human-readable string describing how the rotation
 * decision was made (ported from the original Python stage3_orient logic),
 * useful for debugging or a proctor-facing detail view.
 */
struct QualityInfo {
    bool is_ok = true;                       // false => force retake
    std::vector<std::string> warnings;       // reason codes, see above
    std::string orientation_reason;          // human-readable, may be empty

    // Laplacian variance, computed after resizing to a fixed reference
    // width (see BLUR_REFERENCE_WIDTH in preprocessing.cpp) so the score
    // means the same thing regardless of the capturing phone's native
    // camera resolution -- raw Laplacian variance is not scale-invariant,
    // so comparing un-normalized scores across different phones/photos
    // would be comparing different things.
    double blur_score = 0.0;                 // normalized score of the full raw input image
    double id_panel_blur_score = 0.0;        // normalized score of the cropped+warped ID panel (0 if panel not found)
    double mcq_panel_blur_score = 0.0;       // normalized score of the cropped+warped MCQ panel (0 if panel not found)

    int num_quad_candidates = 0;             // how many quad candidates stage1 found post-dedup
};

/**
 * @brief Load a pre-processed image from disk.
 *
 * @param image_path Path to the image
 * @return cv::Mat BGR image
 * @throws std::runtime_error if image cannot be loaded
 */
cv::Mat prepare_image(const std::string& image_path);

/**
 * @brief Run the complete 4-stage preprocessing pipeline on a single raw photo
 * entirely in memory (no intermediate files written).
 *
 * @param raw_image_path Path to the raw exam sheet photo
 * @param config Configuration containing tuning thresholds
 * @return std::tuple<std::optional<cv::Mat>, std::optional<cv::Mat>, QualityInfo>
 *         (id_image, mcq_image, quality)
 * @throws std::runtime_error if raw image cannot be loaded
 */
std::tuple<std::optional<cv::Mat>, std::optional<cv::Mat>, QualityInfo> prepare_from_raw(const std::string& raw_image_path, const ExamConfig& config);

} // namespace preprocessing