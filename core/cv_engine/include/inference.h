#pragma once

#include <vector>
#include <string>
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

} // namespace inference
