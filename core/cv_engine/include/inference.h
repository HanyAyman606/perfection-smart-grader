#pragma once

#include <vector>
#include <string>
#include <map>
#include <opencv2/opencv.hpp>
#include <onnxruntime/onnxruntime_cxx_api.h>
#include "result.h"

namespace inference {

std::vector<Detection> run_inference(
    const cv::Mat& image,
    const std::string& model_path,
    float conf_threshold = 0.25f,
    float iou_threshold = 0.45f
);

std::vector<Detection> run_inference_id_adaptive(
    const cv::Mat& image,
    const std::string& model_path,
    const std::vector<int>& col_sizes,
    float conf_threshold = 0.25f,
    float fill_conf = 0.10f,
    float iou_threshold = 0.45f
);

std::vector<Detection> run_inference_mcq_adaptive(
    const cv::Mat& image,
    const std::string& model_path,
    int num_questions,
    int num_question_columns,
    int num_choices,
    float conf_threshold = 0.25f,
    float fill_conf = 0.05f,
    float iou_threshold = 0.45f
);

// Per-column variant: uses explicit question counts per column (1-indexed keys,
// e.g. {"1": 5, "2": 5, "3": 5}) for more accurate grid estimation when columns
// have unequal or precisely-known row counts.
std::vector<Detection> run_inference_mcq_adaptive_per_column(
    const cv::Mat& image,
    const std::string& model_path,
    const std::map<std::string, int>& per_column_questions, // 1-indexed: "1"->n1, "2"->n2, ...
    int num_choices,
    float conf_threshold = 0.25f,
    float fill_conf = 0.05f,
    float iou_threshold = 0.45f
);

} // namespace inference
