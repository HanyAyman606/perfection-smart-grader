#pragma once

#include <opencv2/opencv.hpp>
#include <optional>
#include <tuple>
#include <string>

namespace preprocessing {

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
 * @return std::tuple<std::optional<cv::Mat>, std::optional<cv::Mat>> (id_image, mcq_image)
 * @throws std::runtime_error if raw image cannot be loaded
 */
std::tuple<std::optional<cv::Mat>, std::optional<cv::Mat>> prepare_from_raw(const std::string& raw_image_path);

} // namespace preprocessing
