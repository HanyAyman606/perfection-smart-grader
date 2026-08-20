// STAGE 3: Correct final 0/90/180/270 orientation of the warped ID panel.
//
// No OCR, no color. Pure geometry, using a landmark that is unique to this
// template and always present: the 4 EMPTY (unfilled) squares where the
// student writes/fills the Letter + Digit1 + Digit2 + Digit3 of their ID.
// See stage3_orient.py's module docstring for the full rationale -- this
// is a straight behavioural port, including the primary MCQ-aspect-ratio
// signal and the ID-square landmark as a secondary/tie-breaking signal.
//
// Input : stage2/<name>_id_warped.jpg (+ optional <name>_mcq_warped.jpg)
// Output: stage3/<name>_id_oriented.jpg (+ <name>_mcq_oriented.jpg)
//         stage3/_results.json (rotation applied + confidence/reason)
#include <opencv2/opencv.hpp>

#include <cmath>
#include <fstream>
#include <iostream>
#include <optional>
#include <string>
#include <vector>

#include <nlohmann/json.hpp>

#include "common.hpp"

using json = nlohmann::json;
namespace fs = std::filesystem;

namespace {

fs::path WORK_DIR, IN_DIR, OUT_DIR;

double stddev(const std::vector<float>& v) {
    if (v.empty()) return 0.0;
    double mean = 0;
    for (float x : v) mean += x;
    mean /= v.size();
    double var = 0;
    for (float x : v) var += (x - mean) * (x - mean);
    var /= v.size();  // numpy .std() default ddof=0
    return std::sqrt(var);
}

// find_id_squares: returns cluster centroids (x,y) if >=3 clusters found
// among the ~square hollow contours, else nullopt. Mirrors stage3's
// find_id_squares() exactly (including thresholds).
std::optional<std::vector<cv::Point2f>> find_id_squares(const cv::Mat& img) {
    int h = img.rows, w = img.cols;
    cv::Mat gray, blurred, th;
    cv::cvtColor(img, gray, cv::COLOR_BGR2GRAY);
    cv::GaussianBlur(gray, blurred, cv::Size(3, 3), 0);
    cv::adaptiveThreshold(blurred, th, 255, cv::ADAPTIVE_THRESH_GAUSSIAN_C, cv::THRESH_BINARY_INV, 25, 10);

    std::vector<std::vector<cv::Point>> contours;
    cv::findContours(th, contours, cv::RETR_LIST, cv::CHAIN_APPROX_SIMPLE);

    struct Box { double cx, cy, area; };
    std::vector<Box> boxes;
    for (const auto& c : contours) {
        double a = cv::contourArea(c);
        if (a < 0.004 * h * w || a > 0.06 * h * w) continue;
        double peri = cv::arcLength(c, true);
        std::vector<cv::Point> approx;
        cv::approxPolyDP(c, approx, 0.03 * peri, true);
        if (approx.size() != 4 || !cv::isContourConvex(approx)) continue;
        cv::Rect br = cv::boundingRect(approx);
        double ar = static_cast<double>(br.width) / br.height;
        if (ar > 0.6 && ar < 1.6) {
            boxes.push_back({br.x + br.width / 2.0, br.y + br.height / 2.0,
                              static_cast<double>(br.width) * br.height});
        }
    }

    if (boxes.size() >= 3) {
        std::sort(boxes.begin(), boxes.end(), [](const Box& a, const Box& b) { return a.area > b.area; });

        struct Cluster { double cx, cy; std::vector<std::pair<double, double>> pts; };
        std::vector<Cluster> clusters;
        double thresh = 0.04 * std::max(h, w);
        for (const auto& b : boxes) {
            bool placed = false;
            for (auto& cl : clusters) {
                if (std::hypot(b.cx - cl.cx, b.cy - cl.cy) < thresh) {
                    cl.pts.emplace_back(b.cx, b.cy);
                    double sx = 0, sy = 0;
                    for (auto& p : cl.pts) { sx += p.first; sy += p.second; }
                    cl.cx = sx / cl.pts.size();
                    cl.cy = sy / cl.pts.size();
                    placed = true;
                    break;
                }
            }
            if (!placed) clusters.push_back({b.cx, b.cy, {{b.cx, b.cy}}});
        }

        if (clusters.size() >= 3) {
            std::vector<cv::Point2f> pts;
            for (auto& cl : clusters) pts.emplace_back(static_cast<float>(cl.cx), static_cast<float>(cl.cy));
            return pts;
        }
    }

    // Fallback: contour-based square isolation failed (common when
    // blur/JPEG artifacts merge the square strokes into the outer frame
    // line). Deliberately do NOT guess here -- see stage3_orient.py's
    // module docstring: an earlier density-based heuristic was tried and
    // found unreliable, so pass through unrotated and flag for manual
    // confirmation instead of silently mis-rotating.
    return std::nullopt;
}

// decide_rotation: returns {angle, reason}. angle in {0, 90, -90, 180}.
std::pair<int, std::string> decide_rotation(const std::vector<cv::Point2f>& pts, int h, int w) {
    std::vector<float> xs, ys;
    for (auto& p : pts) { xs.push_back(p.x); ys.push_back(p.y); }
    double std_x = stddev(xs), std_y = stddev(ys);

    bool horizontal = std_x > std_y;

    if (horizontal) {
        double mean_y = 0;
        for (float y : ys) mean_y += y;
        mean_y /= ys.size();
        if (mean_y < h / 2.0) return {0, "squares horizontal, top half -> already correct"};
        return {180, "squares horizontal, bottom half -> rotate 180"};
    } else {
        double mean_x = 0;
        for (float x : xs) mean_x += x;
        mean_x /= xs.size();
        if (mean_x < w / 2.0) return {-90, "squares vertical, left side -> rotate 90 CW"};
        return {90, "squares vertical, right side -> rotate 90 CCW"};
    }
}

cv::Mat rotate_image(const cv::Mat& img, int angle) {
    // angle in {0, 90, -90, 180}; positive = counter-clockwise (matches
    // the Python convention used throughout this stage).
    cv::Mat out;
    if (angle == 0) return img;
    if (angle == 180) { cv::rotate(img, out, cv::ROTATE_180); return out; }
    if (angle == 90) { cv::rotate(img, out, cv::ROTATE_90_COUNTERCLOCKWISE); return out; }
    if (angle == -90) { cv::rotate(img, out, cv::ROTATE_90_CLOCKWISE); return out; }
    throw std::runtime_error("invalid rotation angle: " + std::to_string(angle));
}

std::optional<json> process(const fs::path& id_path) {
    std::string name = common::strip_suffix(id_path.filename().string(), "_id_warped.jpg");
    cv::Mat img = cv::imread(id_path.string());
    if (img.empty()) return std::nullopt;

    fs::path mcq_path = IN_DIR / (name + "_mcq_warped.jpg");
    cv::Mat mcq_img;
    bool has_mcq = fs::exists(mcq_path);
    if (has_mcq) mcq_img = cv::imread(mcq_path.string());
    has_mcq = has_mcq && !mcq_img.empty();

    json result;
    result["file"] = name;

    std::optional<int> angle;
    std::string reason;

    if (has_mcq) {
        int mh = mcq_img.rows, mw = mcq_img.cols;
        if (mw < mh) {
            // currently portrait -> must rotate 90 either way.
            auto pts = find_id_squares(img);
            if (pts) {
                std::vector<float> xs, ys;
                for (auto& p : *pts) { xs.push_back(p.x); ys.push_back(p.y); }
                double std_x = stddev(xs), std_y = stddev(ys);
                if (std_x <= std_y) {
                    auto [a2, r2] = decide_rotation(*pts, img.rows, img.cols);
                    angle = a2; reason = "mcq=portrait + " + r2;
                } else {
                    angle = -90;
                    reason = "mcq=portrait, squares ambiguous -> default 90 CW (LOW CONFIDENCE)";
                }
            } else {
                angle = -90;
                reason = "mcq=portrait, squares not found -> default 90 CW (LOW CONFIDENCE, MANUAL CHECK)";
            }
        } else {
            // already landscape -> only need to check upside-down (0 vs 180)
            auto pts = find_id_squares(img);
            if (pts) {
                std::vector<float> xs, ys;
                for (auto& p : *pts) { xs.push_back(p.x); ys.push_back(p.y); }
                double std_x = stddev(xs), std_y = stddev(ys);
                if (std_x > std_y) {
                    auto [a2, r2] = decide_rotation(*pts, img.rows, img.cols);
                    angle = a2; reason = "mcq=landscape + " + r2;
                } else {
                    auto [a2, r2] = decide_rotation(*pts, img.rows, img.cols);
                    angle = a2;
                    reason = "mcq=landscape(borderline) but squares vertical, trusting squares: " + r2;
                }
            } else {
                angle = 0;
                reason = "mcq=landscape, squares not found -> assume already correct (LOW CONFIDENCE, MANUAL CHECK)";
            }
        }
    } else {
        // no MCQ panel available at all -> fall back to ID-square-only logic
        auto pts = find_id_squares(img);
        if (pts) {
            auto [a2, r2] = decide_rotation(*pts, img.rows, img.cols);
            angle = a2; reason = r2;
        } else {
            angle = std::nullopt;
            reason = "no mcq panel, squares not found - MANUAL CHECK NEEDED";
        }
    }

    result["rotation_applied"] = angle ? json(*angle) : json(nullptr);
    result["reason"] = reason;

    if (!angle) {
        cv::imwrite((OUT_DIR / (name + "_id_oriented.jpg")).string(), img);
        return result;
    }

    cv::Mat oriented = rotate_image(img, *angle);
    cv::imwrite((OUT_DIR / (name + "_id_oriented.jpg")).string(), oriented);

    if (has_mcq) {
        cv::Mat mcq_oriented = rotate_image(mcq_img, *angle);
        cv::imwrite((OUT_DIR / (name + "_mcq_oriented.jpg")).string(), mcq_oriented);
        result["mcq_also_rotated"] = true;
    } else {
        result["mcq_also_rotated"] = false;
    }

    return result;
}

}  // namespace

int main(int argc, char** argv) {
    fs::path here = argc > 1 ? fs::path(argv[1]) : fs::current_path();
    WORK_DIR = fs::absolute(here);
    IN_DIR = WORK_DIR / "stage2";
    OUT_DIR = WORK_DIR / "stage3";
    common::ensure_dir(OUT_DIR);

    auto files = common::glob_suffix(IN_DIR, "_id_warped.jpg");

    json results = json::array();
    for (const auto& f : files) {
        auto r = process(f);
        if (!r) continue;
        results.push_back(*r);
        std::cout << (*r)["file"].get<std::string>() << " rot: " << (*r)["rotation_applied"].dump()
                  << " - " << (*r)["reason"].get<std::string>() << "\n";
    }

    std::ofstream out(OUT_DIR / "_results.json");
    out << results.dump(2);
    return 0;
}
