// STAGE 4 (v6 - YOLO Optimized with Pencil Boost):
// Converts oriented crops to grayscale using background subtraction.
// This flattens extreme localized shadows without washing out pencil marks,
// and uses an alpha multiplier to heavily darken the filled bubbles for YOLOv7.
//
// C++ port of stage4_final.py.
#include <opencv2/opencv.hpp>

#include <algorithm>
#include <fstream>
#include <iostream>
#include <map>
#include <optional>
#include <set>
#include <string>

#include <nlohmann/json.hpp>

#include "common.hpp"

using json = nlohmann::json;
namespace fs = std::filesystem;

namespace {

fs::path WORK_DIR, IN_DIR, OUT_DIR;

// (width, height) per panel type, matching TARGET_SIZE in stage4_final.py.
const std::map<std::string, cv::Size> TARGET_SIZE = {
    {"id", cv::Size(700, 900)},
    {"mcq", cv::Size(1400, 900)},
};

cv::Mat remove_shadow_subtraction(const cv::Mat& gray, double alpha = 2.5) {
    int h = gray.rows, w = gray.cols;
    int k = std::max(35, (std::min(h, w) / 6) | 1);
    cv::Mat kernel = cv::getStructuringElement(cv::MORPH_ELLIPSE, cv::Size(k, k));

    // 1. Estimate the background (paper + lighting gradient)
    cv::Mat bg;
    cv::morphologyEx(gray, bg, cv::MORPH_CLOSE, kernel);
    cv::GaussianBlur(bg, bg, cv::Size(0, 0), k / 3.0);

    // 2. Extract ONLY the foreground (dark ink and pencil) by subtraction.
    cv::Mat diff;
    cv::subtract(bg, gray, diff);

    // 3. BOOST THE PENCIL: multiply the difference to darken the strokes,
    // capped/clamped to [0,255] the same way cv2.convertScaleAbs does.
    cv::Mat diff_boosted;
    cv::convertScaleAbs(diff, diff_boosted, alpha, 0);

    // 4. Invert the difference. The shadow is gone, the paper is white
    // (255), and the boosted pencil strokes become a deep, dark gray/black.
    cv::Mat norm = cv::Scalar(255) - diff_boosted;
    return norm;
}

cv::Mat to_yolo_grayscale(const cv::Mat& gray) {
    cv::Mat flat = remove_shadow_subtraction(gray, 2.5);

    cv::Mat denoised;
    cv::bilateralFilter(flat, denoised, 7, 45, 45);

    cv::Ptr<cv::CLAHE> clahe = cv::createCLAHE(1.5, cv::Size(16, 16));
    cv::Mat enhanced;
    clahe->apply(denoised, enhanced);
    return enhanced;
}

cv::Mat fit_on_canvas(const cv::Mat& gray, int target_w, int target_h, int margin = 10) {
    int h = gray.rows, w = gray.cols;
    int avail_w = target_w - 2 * margin, avail_h = target_h - 2 * margin;
    double scale = std::min(static_cast<double>(avail_w) / w, static_cast<double>(avail_h) / h);
    int new_w = std::max(1, static_cast<int>(w * scale));
    int new_h = std::max(1, static_cast<int>(h * scale));
    int interp = scale > 1 ? cv::INTER_CUBIC : cv::INTER_AREA;
    cv::Mat resized;
    cv::resize(gray, resized, cv::Size(new_w, new_h), 0, 0, interp);

    // Pad with neutral mid-gray (128) for YOLO bounding box stability
    cv::Mat canvas(target_h, target_w, CV_8UC1, cv::Scalar(128));
    int x0 = (target_w - new_w) / 2, y0 = (target_h - new_h) / 2;
    resized.copyTo(canvas(cv::Rect(x0, y0, new_w, new_h)));
    return canvas;
}

std::optional<cv::Mat> process_one(const fs::path& path, const std::string& panel_type) {
    cv::Mat img = cv::imread(path.string());
    if (img.empty()) return std::nullopt;
    cv::Mat gray;
    cv::cvtColor(img, gray, cv::COLOR_BGR2GRAY);

    int h = gray.rows, w = gray.cols;
    int trim = std::max(2, static_cast<int>(0.015 * std::min(h, w)));
    if (h > 4 * trim && w > 4 * trim) {
        gray = gray(cv::Rect(trim, trim, w - 2 * trim, h - 2 * trim)).clone();
    }

    cv::Size target = TARGET_SIZE.at(panel_type);
    cv::Mat canvas_gray = fit_on_canvas(gray, target.width, target.height);
    return to_yolo_grayscale(canvas_gray);
}

json process(const std::string& name) {
    json result;
    result["file"] = name;
    result["id_done"] = false;
    result["mcq_done"] = false;

    fs::path id_path = IN_DIR / (name + "_id_oriented.jpg");
    if (fs::exists(id_path)) {
        auto final_img = process_one(id_path, "id");
        if (final_img) {
            cv::imwrite((OUT_DIR / (name + "_id_final.jpg")).string(), *final_img);
            result["id_done"] = true;
        }
    }

    fs::path mcq_path = IN_DIR / (name + "_mcq_oriented.jpg");
    if (fs::exists(mcq_path)) {
        auto final_img = process_one(mcq_path, "mcq");
        if (final_img) {
            cv::imwrite((OUT_DIR / (name + "_mcq_final.jpg")).string(), *final_img);
            result["mcq_done"] = true;
        }
    }

    return result;
}

}  // namespace

int main(int argc, char** argv) {
    fs::path here = argc > 1 ? fs::path(argv[1]) : fs::current_path();
    WORK_DIR = fs::absolute(here);
    IN_DIR = WORK_DIR / "stage3";
    OUT_DIR = WORK_DIR / "stage4";
    common::ensure_dir(OUT_DIR);

    std::set<std::string> names;
    for (const auto& p : common::glob_suffix(IN_DIR, "_id_oriented.jpg"))
        names.insert(common::strip_suffix(p.filename().string(), "_id_oriented.jpg"));
    for (const auto& p : common::glob_suffix(IN_DIR, "_mcq_oriented.jpg"))
        names.insert(common::strip_suffix(p.filename().string(), "_mcq_oriented.jpg"));

    json results = json::array();
    for (const auto& name : names) {
        json r = process(name);
        results.push_back(r);
        std::cout << r["file"].get<std::string>() << " ID: " << r["id_done"].dump()
                  << " MCQ: " << r["mcq_done"].dump() << "\n";
    }

    std::ofstream out(OUT_DIR / "_results.json");
    out << results.dump(2);
    return 0;
}
