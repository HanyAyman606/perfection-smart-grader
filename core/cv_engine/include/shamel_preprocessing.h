#pragma once

#include <opencv2/opencv.hpp>
#include <string>
#include <vector>
#include <optional>
#include <tuple>

// All panel extraction helpers for the Shamel exam layout.
// The Shamel sheet has 3 detected quads:
//   - MCQ: large box in the lower portion of the image, split into a 2x3 grid of 6 sub-regions
//   - ID:  smaller box top-left (above MCQ)
//   - Version: smaller box top-right (above MCQ), contains 2 bubble rows
//
// Column-major fill order for the 6 MCQ sub-regions (as requested):
//   top_left, bottom_left, top_mid, bottom_mid, top_right, bottom_right
namespace shamel_preprocessing {

// Maximum questions per MCQ sub-region.
constexpr int MCQ_REGION_CAP = 10;

// Names of the 6 MCQ sub-regions in column-major fill order.
// Index 0 = first region to be filled with questions.
inline const std::vector<std::string>& region_fill_order() {
    static const std::vector<std::string> ORDER = {
        "top_left", "bottom_left",
        "top_mid",  "bottom_mid",
        "top_right","bottom_right"
    };
    return ORDER;
}

// Compute how many questions go into each of the 6 regions (column-major, cap 10).
// Returns a vector<int> of size 6 in fill order.
inline std::vector<int> compute_region_sizes(int num_questions) {
    std::vector<int> sizes;
    int remaining = num_questions;
    for (int i = 0; i < 6; ++i) {
        int take = std::max(0, std::min(MCQ_REGION_CAP, remaining));
        sizes.push_back(take);
        remaining -= take;
    }
    return sizes;
}

struct ShamelPanels {
    std::optional<cv::Mat> id_mat;
    std::optional<cv::Mat> version_mat;
    std::optional<cv::Mat> mcq_mat;
    // The 6 MCQ sub-region crops in column-major fill order.
    // Only includes non-empty regions (size > 0 questions).
    std::vector<cv::Mat> mcq_regions;        // up to 6, column-major
    std::vector<std::string> mcq_region_names; // parallel to mcq_regions
};

// Step 1: Extract and perspective-correct the three panels from the raw image,
// then split the MCQ panel into its 6 sub-regions. Save all crops to disk.
// Returns the paths wrapped in JSON fields matching the documented API.
//
// Saved file naming:
//   {image_path}_shamel_id.jpg
//   {image_path}_shamel_version.jpg
//   {image_path}_shamel_mcq.jpg
//   {image_path}_shamel_mcq_1_top_left.jpg
//   {image_path}_shamel_mcq_2_bottom_left.jpg
//   ... (column-major)
ShamelPanels extract_shamel_panels(const std::string& image_path);

// Step 2: Given the already-warped ID mat, run grouping to detect two single-row sets.
// The version panel has exactly 2 rows of bubbles:
//   row 0 = exam model (labels: exam_models from config)
//   row 1 = exam day   (labels: exam_days   from config)
// Returns a VersionResult (defined in result.h).

} // namespace shamel_preprocessing
