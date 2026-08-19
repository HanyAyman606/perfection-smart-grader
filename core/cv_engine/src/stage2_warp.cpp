// STAGE 2: Perspective-correct (deskew/flatten) and crop each detected panel
// (ID box, MCQ box) from Stage 1 into its own clean top-down rectangular
// image, using each panel's own 4 corners.
//
// Input : input/<name>.jpg (original photo, for pixel data)
//         stage1/<name>_id_quad.bin / _mcq_quad.bin
// Output: stage2/<name>_id_warped.jpg
//         stage2/<name>_mcq_warped.jpg
//         stage2/_results.json
//
// C++ port of stage2_warp.py.
#include <opencv2/opencv.hpp>

#include <algorithm>
#include <cmath>
#include <fstream>
#include <iostream>
#include <optional>
#include <set>
#include <string>

#include <nlohmann/json.hpp>

#include "common.hpp"
#include "quad_io.hpp"

using json = nlohmann::json;
namespace fs = std::filesystem;

namespace {

fs::path WORK_DIR, IN_IMG_DIR, QUAD_DIR, OUT_DIR;

cv::Mat warp_quad(const cv::Mat& img, const Quad& quad, double out_scale = 1.0) {
    cv::Point2f tl = quad.tl, tr = quad.tr, br = quad.br, bl = quad.bl;
    double wA = cv::norm(br - bl);
    double wB = cv::norm(tr - tl);
    int maxW = static_cast<int>(std::max(wA, wB) * out_scale);
    double hA = cv::norm(tr - br);
    double hB = cv::norm(tl - bl);
    int maxH = static_cast<int>(std::max(hA, hB) * out_scale);
    maxW = std::max(maxW, 10);
    maxH = std::max(maxH, 10);

    std::vector<cv::Point2f> src = {tl, tr, br, bl};
    std::vector<cv::Point2f> dst = {
        {0, 0}, {static_cast<float>(maxW - 1), 0},
        {static_cast<float>(maxW - 1), static_cast<float>(maxH - 1)}, {0, static_cast<float>(maxH - 1)}};

    cv::Mat M = cv::getPerspectiveTransform(src, dst);
    cv::Mat warped;
    cv::warpPerspective(img, warped, M, cv::Size(maxW, maxH));
    return warped;
}

std::optional<fs::path> find_input_image(const std::string& name) {
    for (const std::string& ext : {".jpg", ".jpeg", ".png"}) {
        fs::path p = IN_IMG_DIR / (name + ext);
        if (fs::exists(p)) return p;
    }
    return std::nullopt;
}

std::optional<json> process(const std::string& name) {
    auto img_path = find_input_image(name);
    if (!img_path) return std::nullopt;
    cv::Mat img = cv::imread(img_path->string());
    if (img.empty()) return std::nullopt;

    json result;
    result["file"] = name;
    result["id_warped"] = false;
    result["mcq_warped"] = false;

    fs::path id_quad_path = QUAD_DIR / (name + "_id_quad.bin");
    fs::path mcq_quad_path = QUAD_DIR / (name + "_mcq_quad.bin");

    Quad q;
    if (fs::exists(id_quad_path) && read_quad(id_quad_path, q)) {
        cv::Mat warped = warp_quad(img, q);
        cv::imwrite((OUT_DIR / (name + "_id_warped.jpg")).string(), warped);
        result["id_warped"] = true;
    }
    if (fs::exists(mcq_quad_path) && read_quad(mcq_quad_path, q)) {
        cv::Mat warped = warp_quad(img, q);
        cv::imwrite((OUT_DIR / (name + "_mcq_warped.jpg")).string(), warped);
        result["mcq_warped"] = true;
    }

    return result;
}

}  // namespace

int main(int argc, char** argv) {
    fs::path here = argc > 1 ? fs::path(argv[1]) : fs::current_path();
    WORK_DIR = fs::absolute(here);
    IN_IMG_DIR = WORK_DIR / "input";
    QUAD_DIR = WORK_DIR / "stage1";
    OUT_DIR = WORK_DIR / "stage2";
    common::ensure_dir(OUT_DIR);

    // names = sorted(set of basenames with _id_quad/_mcq_quad stripped)
    std::set<std::string> names;
    for (const auto& p : common::glob_suffix(QUAD_DIR, "_id_quad.bin"))
        names.insert(common::strip_suffix(p.filename().string(), "_id_quad.bin"));
    for (const auto& p : common::glob_suffix(QUAD_DIR, "_mcq_quad.bin"))
        names.insert(common::strip_suffix(p.filename().string(), "_mcq_quad.bin"));

    json results = json::array();
    for (const auto& name : names) {
        auto r = process(name);
        if (!r) continue;
        results.push_back(*r);
        std::cout << (*r)["file"].get<std::string>() << " ID warped: " << (*r)["id_warped"].dump()
                  << " MCQ warped: " << (*r)["mcq_warped"].dump() << "\n";
    }

    std::ofstream out(OUT_DIR / "_results.json");
    out << results.dump(2);
    return 0;
}
