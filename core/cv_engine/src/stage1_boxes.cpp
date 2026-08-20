// STAGE 1: Detect the ID-panel box and MCQ-panel box directly from a raw
// input photo. Includes salvage path for folded/creased pages.
//
// C++ port of stage1_boxes.py. Behaviour (thresholds, contour logic,
// classify/dedupe rules) is kept identical to the Python original.
//
// Quad interchange format: instead of numpy .npy files (Python-specific),
// each detected quad is written as a raw binary file of 4 (x,y) float64
// pairs in TL,TR,BR,BL order -- see common_quad.hpp. This is internal to
// this C++ pipeline and consumed by stage2_warp.cpp.
#include <opencv2/opencv.hpp>

#include <algorithm>
#include <cmath>
#include <fstream>
#include <iostream>
#include <string>
#include <vector>

#include <nlohmann/json.hpp>

#include "common.hpp"
#include "quad_io.hpp"

// ============================================================
// TUNING: Contour / quad detection thresholds
// ============================================================
// Min contour area as fraction of image area to be a panel candidate
constexpr double MIN_QUAD_AREA_RATIO = 0.03;
// Max contour area as fraction of image area
constexpr double MAX_QUAD_AREA_RATIO = 0.45;
// Min IoU between contour fill and its min-area-rect to accept salvage path
constexpr double SALVAGE_IOU_THRESHOLD = 0.80;
// Max aspect ratio for a salvaged quad (guards against picking up phones etc.)
constexpr double SALVAGE_AR_MAX = 2.2;
// IoU above which two candidate quads are considered duplicates and deduplicated
constexpr double DEDUP_IOU_THRESHOLD = 0.75;
// ============================================================

using json = nlohmann::json;
namespace fs = std::filesystem;

namespace {

fs::path WORK_DIR;
fs::path IN_DIR;
fs::path OUT_DIR;

// order_pts: TL, TR, BR, BL from an unordered set of 4 points.
Quad order_pts(const std::vector<cv::Point2f>& pts_in) {
    std::vector<cv::Point2f> pts = pts_in;
    std::vector<double> s(pts.size()), d(pts.size());
    for (size_t i = 0; i < pts.size(); ++i) {
        s[i] = pts[i].x + pts[i].y;
        d[i] = pts[i].y - pts[i].x;  // np.diff([x,y]) == y - x
    }
    auto argmin = [](const std::vector<double>& v) {
        return static_cast<size_t>(std::min_element(v.begin(), v.end()) - v.begin());
    };
    auto argmax = [](const std::vector<double>& v) {
        return static_cast<size_t>(std::max_element(v.begin(), v.end()) - v.begin());
    };
    cv::Point2f tl = pts[argmin(s)];
    cv::Point2f br = pts[argmax(s)];
    cv::Point2f tr = pts[argmin(d)];
    cv::Point2f bl = pts[argmax(d)];
    Quad q;
    q.tl = tl; q.tr = tr; q.br = br; q.bl = bl;
    return q;
}

struct KeptQuad {
    double area;
    Quad quad;
    std::string method;
};

cv::Mat quad_mask(const Quad& q, int h, int w) {
    cv::Mat m = cv::Mat::zeros(h, w, CV_8UC1);
    std::vector<cv::Point> pts = {
        cv::Point(cvRound(q.tl.x), cvRound(q.tl.y)),
        cv::Point(cvRound(q.tr.x), cvRound(q.tr.y)),
        cv::Point(cvRound(q.br.x), cvRound(q.br.y)),
        cv::Point(cvRound(q.bl.x), cvRound(q.bl.y)),
    };
    std::vector<std::vector<cv::Point>> polys = {pts};
    cv::fillPoly(m, polys, cv::Scalar(255));
    return m;
}

double mask_iou(const cv::Mat& m1, const cv::Mat& m2) {
    cv::Mat inter_m, union_m;
    cv::bitwise_and(m1, m2, inter_m);
    cv::bitwise_or(m1, m2, union_m);
    double inter = cv::countNonZero(inter_m);
    double uni = std::max(cv::countNonZero(union_m), 1);
    return inter / uni;
}

std::vector<KeptQuad> find_panel_quads(const cv::Mat& img) {
    int h = img.rows, w = img.cols;
    double img_area = static_cast<double>(h) * w;

    cv::Mat gray, blurred, edges;
    cv::cvtColor(img, gray, cv::COLOR_BGR2GRAY);
    cv::medianBlur(gray, blurred, 5);
    cv::Canny(blurred, edges, 30, 100);
    cv::Mat kernel5 = cv::getStructuringElement(cv::MORPH_RECT, cv::Size(5, 5));
    cv::dilate(edges, edges, kernel5, cv::Point(-1, -1), 1);

    std::vector<std::vector<cv::Point>> contours;
    cv::findContours(edges, contours, cv::RETR_LIST, cv::CHAIN_APPROX_SIMPLE);

    std::vector<KeptQuad> quads;

    for (const auto& c : contours) {
        double a = cv::contourArea(c);
        if (a < MIN_QUAD_AREA_RATIO * img_area || a > MAX_QUAD_AREA_RATIO * img_area) continue;

        double peri = cv::arcLength(c, true);
        std::vector<cv::Point> approx;
        cv::approxPolyDP(c, approx, 0.02 * peri, true);

        // --- STRICT PATH ---
        if (approx.size() == 4 && cv::isContourConvex(approx)) {
            std::vector<cv::Point2f> pf;
            for (auto& p : approx) pf.emplace_back(static_cast<float>(p.x), static_cast<float>(p.y));
            Quad quad = order_pts(pf);
            quads.push_back({a, quad, "exact"});
            continue;
        }

        // --- SALVAGE PATH for folded/creased pages ---
        cv::RotatedRect rect = cv::minAreaRect(c);
        cv::Point2f box[4];
        rect.points(box);

        cv::Mat m1 = cv::Mat::zeros(h, w, CV_8UC1);
        std::vector<std::vector<cv::Point>> c_polys = {c};
        cv::drawContours(m1, c_polys, -1, cv::Scalar(255), cv::FILLED);

        cv::Mat m2 = cv::Mat::zeros(h, w, CV_8UC1);
        std::vector<cv::Point> box_i;
        for (auto& p : box) box_i.emplace_back(cvRound(p.x), cvRound(p.y));
        std::vector<std::vector<cv::Point>> box_polys = {box_i};
        cv::drawContours(m2, box_polys, -1, cv::Scalar(255), cv::FILLED);

        double iou = mask_iou(m1, m2);

        if (iou > SALVAGE_IOU_THRESHOLD) {
            float rw = rect.size.width, rh = rect.size.height;
            double ar = std::max(rw, rh) / std::max(std::min(rw, rh), 1.0f);
            // Aspect ratio guard to prevent picking up random clutter like phones
            if (ar > 1.0 && ar < SALVAGE_AR_MAX) {
                std::vector<cv::Point2f> pf(box, box + 4);
                Quad quad = order_pts(pf);
                char buf[48];
                std::snprintf(buf, sizeof(buf), "salvaged(iou=%.2f)", iou);
                quads.push_back({a, quad, buf});
            }
        }
    }

    std::sort(quads.begin(), quads.end(), [](const KeptQuad& x, const KeptQuad& y) { return x.area > y.area; });

    std::vector<KeptQuad> kept;
    std::vector<cv::Mat> kept_masks;
    for (const auto& kq : quads) {
        cv::Mat m1 = quad_mask(kq.quad, h, w);
        bool dup = false;
        for (const auto& m2 : kept_masks) {
            if (mask_iou(m1, m2) > DEDUP_IOU_THRESHOLD) { dup = true; break; }
        }
        if (!dup) {
            kept.push_back(kq);
            kept_masks.push_back(m1);
        }
    }
    return kept;
}

struct ClassifyResult {
    bool id_found = false, mcq_found = false;
    Quad id_quad, mcq_quad;
    std::string id_method, mcq_method;
};

ClassifyResult classify(const std::vector<KeptQuad>& kept, int h, int w) {
    ClassifyResult res;
    if (kept.empty()) return res;

    std::vector<cv::Mat> masks;
    for (const auto& kq : kept) masks.push_back(quad_mask(kq.quad, h, w));

    auto is_superset = [&](size_t i, size_t j) {
        cv::Mat inter_m;
        cv::bitwise_and(masks[i], masks[j], inter_m);
        double inter = cv::countNonZero(inter_m);
        double aj = std::max(cv::countNonZero(masks[j]), 1);
        return inter / aj > 0.85;
    };

    size_t n = kept.size();
    std::vector<bool> is_container(n, false);
    for (size_t i = 0; i < n; ++i)
        for (size_t j = 0; j < n; ++j)
            if (i != j && is_superset(i, j)) is_container[i] = true;

    std::vector<KeptQuad> leaves;
    for (size_t i = 0; i < n; ++i)
        if (!is_container[i]) leaves.push_back(kept[i]);
    std::sort(leaves.begin(), leaves.end(), [](const KeptQuad& x, const KeptQuad& y) { return x.area > y.area; });

    if (leaves.empty()) return res;
    if (leaves.size() == 1) {
        res.mcq_found = true;
        res.mcq_quad = leaves[0].quad;
        res.mcq_method = leaves[0].method;
        return res;
    }

    res.mcq_found = true;
    res.mcq_quad = leaves[0].quad;
    res.mcq_method = leaves[0].method;
    res.id_found = true;
    res.id_quad = leaves[1].quad;
    res.id_method = leaves[1].method;
    return res;
}

json process(const fs::path& path) {
    std::string name = path.stem().string();
    cv::Mat img = cv::imread(path.string());
    if (img.empty()) return nullptr;

    auto kept = find_panel_quads(img);
    ClassifyResult cls = classify(kept, img.rows, img.cols);

    cv::Mat vis = img.clone();
    if (cls.id_found) {
        std::vector<cv::Point> pts = cls.id_quad.as_points_i();
        std::vector<std::vector<cv::Point>> polys = {pts};
        cv::polylines(vis, polys, true, cv::Scalar(255, 0, 0), 5);
        cv::putText(vis, "ID (" + cls.id_method + ")",
                    cv::Point(pts[0].x, pts[0].y - 15), cv::FONT_HERSHEY_SIMPLEX, 1.2,
                    cv::Scalar(255, 0, 0), 3);
        write_quad(OUT_DIR / (name + "_id_quad.bin"), cls.id_quad);
    }
    if (cls.mcq_found) {
        std::vector<cv::Point> pts = cls.mcq_quad.as_points_i();
        std::vector<std::vector<cv::Point>> polys = {pts};
        cv::polylines(vis, polys, true, cv::Scalar(0, 0, 255), 5);
        cv::putText(vis, "MCQ (" + cls.mcq_method + ")",
                    cv::Point(pts[0].x, pts[0].y - 15), cv::FONT_HERSHEY_SIMPLEX, 1.2,
                    cv::Scalar(0, 0, 255), 3);
        write_quad(OUT_DIR / (name + "_mcq_quad.bin"), cls.mcq_quad);
    }

    if (!cls.id_found || !cls.mcq_found) {
        std::string cause_str;
        if (!cls.id_found && !cls.mcq_found) cause_str = "panels_missing";
        else if (!cls.id_found) cause_str = "id_panel_missing";
        else cause_str = "mcq_panel_missing";

        cv::putText(vis, "MISSING PANEL - " + cause_str, cv::Point(30, 60),
                    cv::FONT_HERSHEY_SIMPLEX, 1.2, cv::Scalar(0, 0, 255), 3);
                    
        // Write preprocessing failure sentinel so stage 6 can emit global_status=failed
        // without running any scoring logic on a bad image.
        nlohmann::json fail_json;
        fail_json["status"] = "failed";
        fail_json["cause"]  = cause_str;
        std::ofstream fail_f(WORK_DIR / "preprocessing_result.json");
        fail_f << fail_json.dump(2);
        fail_f.close();
    }

    cv::imwrite((OUT_DIR / (name + "_boxes.jpg")).string(), vis);

    json r;
    r["file"] = name;
    r["id_found"] = cls.id_found;
    r["id_method"] = cls.id_found ? json(cls.id_method) : json(nullptr);
    r["mcq_found"] = cls.mcq_found;
    r["mcq_method"] = cls.mcq_found ? json(cls.mcq_method) : json(nullptr);
    r["num_candidates"] = static_cast<int>(kept.size());
    return r;
}

}  // namespace

int main(int argc, char** argv) {
    fs::path here = argc > 1 ? fs::path(argv[1]) : fs::current_path();
    WORK_DIR = fs::absolute(here);
    IN_DIR = WORK_DIR / "input";
    OUT_DIR = WORK_DIR / "stage1";
    common::ensure_dir(OUT_DIR);

    auto files = common::glob_ext(IN_DIR, {".jpg", ".jpeg", ".png"});

    json results = json::array();
    for (const auto& f : files) {
        json r = process(f);
        if (r.is_null()) continue;
        results.push_back(r);
        std::cout << r["file"].get<std::string>()
                  << " | ID: " << r["id_found"].dump()
                  << " (" << (r["id_method"].is_null() ? "null" : r["id_method"].get<std::string>()) << ")"
                  << " | MCQ: " << r["mcq_found"].dump()
                  << " (" << (r["mcq_method"].is_null() ? "null" : r["mcq_method"].get<std::string>()) << ")"
                  << " | cands: " << r["num_candidates"].get<int>() << "\n";
    }

    std::ofstream out(OUT_DIR / "_results.json");
    out << results.dump(2);

    // If any image failed panel detection, signal it (but return 0 so pipeline can jump to stage6)
    if (std::filesystem::exists(WORK_DIR / "preprocessing_result.json")) {
        std::cerr << "WARNING: preprocessing_result.json written — pipeline will skip to stage 6.\n";
    }
    return 0;
}
