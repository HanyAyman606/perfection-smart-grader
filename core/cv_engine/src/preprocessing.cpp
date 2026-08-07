#include "preprocessing.h"
#include <vector>
#include <algorithm>
#include <cmath>
#include <stdexcept>

namespace {

cv::Point2f get_centroid(const std::vector<cv::Point>& contour) {
    cv::Moments M = cv::moments(contour);
    if (M.m00 != 0) {
        return cv::Point2f(M.m10 / M.m00, M.m01 / M.m00);
    }
    return cv::Point2f(0.f, 0.f);
}

std::vector<cv::Point2f> order_pts(const std::vector<cv::Point>& pts) {
    std::vector<cv::Point2f> ptsf;
    for (auto& p : pts) ptsf.push_back(cv::Point2f((float)p.x, (float)p.y));
    std::vector<cv::Point2f> ordered(4);
    std::vector<float> sums(4), diffs(4);
    for (int i = 0; i < 4; ++i) {
        sums[i] = ptsf[i].x + ptsf[i].y;
        diffs[i] = ptsf[i].y - ptsf[i].x;
    }
    ordered[0] = ptsf[std::min_element(sums.begin(), sums.end()) - sums.begin()];
    ordered[2] = ptsf[std::max_element(sums.begin(), sums.end()) - sums.begin()];
    ordered[1] = ptsf[std::min_element(diffs.begin(), diffs.end()) - diffs.begin()];
    ordered[3] = ptsf[std::max_element(diffs.begin(), diffs.end()) - diffs.begin()];
    return ordered;
}

cv::Mat get_quad_mask(const std::vector<cv::Point2f>& quad, int h, int w) {
    cv::Mat mask = cv::Mat::zeros(h, w, CV_8UC1);
    std::vector<cv::Point> pts(4);
    for (int i = 0; i < 4; ++i) pts[i] = cv::Point(std::round(quad[i].x), std::round(quad[i].y));
    std::vector<std::vector<cv::Point>> polys = {pts};
    cv::fillPoly(mask, polys, cv::Scalar(255));
    return mask;
}

struct QuadInfo {
    double area;
    std::vector<cv::Point2f> quad;
};

std::vector<QuadInfo> find_panel_quads(const cv::Mat& img) {
    int h = img.rows, w = img.cols;
    double img_area = h * w;
    cv::Mat gray, edges;
    cv::cvtColor(img, gray, cv::COLOR_BGR2GRAY);
    cv::medianBlur(gray, gray, 5);
    cv::Canny(gray, edges, 30, 100);
    cv::Mat kernel = cv::Mat::ones(5, 5, CV_8UC1);
    cv::dilate(edges, edges, kernel, cv::Point(-1, -1), 1);

    std::vector<std::vector<cv::Point>> cnts;
    cv::findContours(edges, cnts, cv::RETR_LIST, cv::CHAIN_APPROX_SIMPLE);
    
    std::vector<QuadInfo> quads;
    for (const auto& c : cnts) {
        double a = cv::contourArea(c);
        if (a < 0.03 * img_area || a > 0.45 * img_area) continue;
        double peri = cv::arcLength(c, true);
        std::vector<cv::Point> approx;
        cv::approxPolyDP(c, approx, 0.02 * peri, true);
        if (approx.size() == 4 && cv::isContourConvex(approx)) {
            quads.push_back({a, order_pts(approx)});
        }
    }

    std::sort(quads.begin(), quads.end(), [](const QuadInfo& a, const QuadInfo& b){ return a.area > b.area; });

    std::vector<QuadInfo> kept;
    std::vector<cv::Mat> kept_masks;
    for (const auto& q : quads) {
        cv::Mat m1 = get_quad_mask(q.quad, h, w);
        bool dup = false;
        for (const auto& m2 : kept_masks) {
            cv::Mat inter, uni;
            cv::bitwise_and(m1, m2, inter);
            cv::bitwise_or(m1, m2, uni);
            int inter_cnt = cv::countNonZero(inter);
            int uni_cnt = cv::countNonZero(uni);
            if ((double)inter_cnt / std::max(uni_cnt, 1) > 0.75) {
                dup = true;
                break;
            }
        }
        if (!dup) {
            kept.push_back(q);
            kept_masks.push_back(m1);
        }
    }
    return kept;
}

std::tuple<std::optional<std::vector<cv::Point2f>>, std::optional<std::vector<cv::Point2f>>> classify_quads(const std::vector<QuadInfo>& kept, int h, int w) {
    if (kept.empty()) return {std::nullopt, std::nullopt};
    std::vector<cv::Mat> masks;
    for (const auto& q : kept) masks.push_back(get_quad_mask(q.quad, h, w));

    int n = kept.size();
    std::vector<bool> is_container(n, false);
    for (int i = 0; i < n; ++i) {
        for (int j = 0; j < n; ++j) {
            if (i != j) {
                cv::Mat inter;
                cv::bitwise_and(masks[i], masks[j], inter);
                int inter_cnt = cv::countNonZero(inter);
                int aj = cv::countNonZero(masks[j]);
                if ((double)inter_cnt / std::max(aj, 1) > 0.85) {
                    is_container[i] = true;
                }
            }
        }
    }

    std::vector<QuadInfo> leaves;
    for (int i = 0; i < n; ++i) {
        if (!is_container[i]) leaves.push_back(kept[i]);
    }
    std::sort(leaves.begin(), leaves.end(), [](const QuadInfo& a, const QuadInfo& b){ return a.area > b.area; });

    if (leaves.empty()) return {std::nullopt, std::nullopt};
    if (leaves.size() == 1) return {std::nullopt, leaves[0].quad};

    return {leaves[1].quad, leaves[0].quad};
}

std::tuple<std::optional<std::vector<cv::Point2f>>, std::optional<std::vector<cv::Point2f>>> stage1_detect_quads(const cv::Mat& img) {
    auto kept = find_panel_quads(img);
    return classify_quads(kept, img.rows, img.cols);
}

cv::Mat stage2_warp(const cv::Mat& img, const std::vector<cv::Point2f>& quad) {
    cv::Point2f tl = quad[0], tr = quad[1], br = quad[2], bl = quad[3];
    double wA = cv::norm(br - bl);
    double wB = cv::norm(tr - tl);
    int maxW = std::max(10, (int)std::max(wA, wB));
    double hA = cv::norm(tr - br);
    double hB = cv::norm(tl - bl);
    int maxH = std::max(10, (int)std::max(hA, hB));

    std::vector<cv::Point2f> dst = {
        cv::Point2f(0, 0),
        cv::Point2f(maxW - 1.f, 0),
        cv::Point2f(maxW - 1.f, maxH - 1.f),
        cv::Point2f(0, maxH - 1.f)
    };

    cv::Mat M = cv::getPerspectiveTransform(quad, dst);
    cv::Mat warped;
    cv::warpPerspective(img, warped, M, cv::Size(maxW, maxH));
    return warped;
}

std::optional<std::vector<cv::Point2f>> find_id_squares(const cv::Mat& img) {
    int h = img.rows, w = img.cols;
    cv::Mat gray, th;
    cv::cvtColor(img, gray, cv::COLOR_BGR2GRAY);
    cv::GaussianBlur(gray, gray, cv::Size(3, 3), 0);
    cv::adaptiveThreshold(gray, th, 255, cv::ADAPTIVE_THRESH_GAUSSIAN_C, cv::THRESH_BINARY_INV, 25, 10);

    std::vector<std::vector<cv::Point>> cnts;
    cv::findContours(th, cnts, cv::RETR_LIST, cv::CHAIN_APPROX_SIMPLE);

    struct Box { float cx, cy, area; };
    std::vector<Box> boxes;

    for (const auto& c : cnts) {
        double a = cv::contourArea(c);
        if (a < 0.004 * h * w || a > 0.06 * h * w) continue;
        double peri = cv::arcLength(c, true);
        std::vector<cv::Point> approx;
        cv::approxPolyDP(c, approx, 0.03 * peri, true);
        if (approx.size() != 4 || !cv::isContourConvex(approx)) continue;
        cv::Rect r = cv::boundingRect(approx);
        float ar = (float)r.width / r.height;
        if (ar > 0.6f && ar < 1.6f) {
            boxes.push_back({r.x + r.width / 2.0f, r.y + r.height / 2.0f, (float)(r.width * r.height)});
        }
    }

    if (boxes.size() < 3) return std::nullopt;

    std::sort(boxes.begin(), boxes.end(), [](const Box& a, const Box& b){ return a.area > b.area; });

    struct Cluster { float cx, cy; std::vector<cv::Point2f> pts; };
    std::vector<Cluster> clusters;

    for (const auto& b : boxes) {
        bool placed = false;
        for (auto& cl : clusters) {
            if (std::hypot(b.cx - cl.cx, b.cy - cl.cy) < 0.04 * std::max(h, w)) {
                cl.pts.push_back(cv::Point2f(b.cx, b.cy));
                float sx = 0, sy = 0;
                for (auto p : cl.pts) { sx += p.x; sy += p.y; }
                cl.cx = sx / cl.pts.size();
                cl.cy = sy / cl.pts.size();
                placed = true;
                break;
            }
        }
        if (!placed) {
            clusters.push_back({b.cx, b.cy, {cv::Point2f(b.cx, b.cy)}});
        }
    }

    if (clusters.size() < 3) return std::nullopt;

    std::vector<cv::Point2f> res;
    for (const auto& cl : clusters) res.push_back(cv::Point2f(cl.cx, cl.cy));
    return res;
}

std::tuple<int, std::string> decide_rotation(const std::vector<cv::Point2f>& pts, int h, int w) {
    std::vector<float> xs, ys;
    for (const auto& p : pts) { xs.push_back(p.x); ys.push_back(p.y); }
    
    auto get_mean_std = [](const std::vector<float>& v) {
        float sum = 0, sq_sum = 0;
        for (float x : v) { sum += x; sq_sum += x*x; }
        float mean = sum / v.size();
        float variance = sq_sum / v.size() - mean * mean;
        return std::make_pair(mean, std::sqrt(std::max(0.0f, variance)));
    };

    auto [mean_x, std_x] = get_mean_std(xs);
    auto [mean_y, std_y] = get_mean_std(ys);
    bool horizontal = std_x > std_y;

    if (horizontal) {
        if (mean_y < h / 2.0f) return {0, "squares horizontal, top half -> already correct"};
        else return {180, "squares horizontal, bottom half -> rotate 180"};
    } else {
        if (mean_x < w / 2.0f) return {-90, "squares vertical, left side -> rotate 90 CW"};
        else return {90, "squares vertical, right side -> rotate 90 CCW"};
    }
}

cv::Mat rotate_image(const cv::Mat& img, int angle) {
    cv::Mat res;
    if (angle == 0) return img.clone();
    if (angle == 180) cv::rotate(img, res, cv::ROTATE_180);
    else if (angle == 90) cv::rotate(img, res, cv::ROTATE_90_COUNTERCLOCKWISE);
    else if (angle == -90) cv::rotate(img, res, cv::ROTATE_90_CLOCKWISE);
    else throw std::invalid_argument("Unsupported rotation angle");
    return res;
}

std::tuple<std::optional<cv::Mat>, std::optional<cv::Mat>, std::optional<int>> stage3_orient(
    const std::optional<cv::Mat>& id_warped,
    const std::optional<cv::Mat>& mcq_warped
) {
    std::optional<int> angle = std::nullopt;

    if (mcq_warped && id_warped) {
        int mh = mcq_warped->rows, mw = mcq_warped->cols;
        if (mw < mh) {
            auto pts = find_id_squares(*id_warped);
            if (pts) {
                float sx = 0, sy = 0, sx2 = 0, sy2 = 0;
                for (auto p : *pts) { sx += p.x; sy += p.y; sx2 += p.x*p.x; sy2 += p.y*p.y; }
                float n = pts->size();
                float std_x = std::sqrt(std::max(0.0f, sx2/n - (sx/n)*(sx/n)));
                float std_y = std::sqrt(std::max(0.0f, sy2/n - (sy/n)*(sy/n)));
                if (std_x <= std_y) {
                    auto res = decide_rotation(*pts, id_warped->rows, id_warped->cols);
                    angle = std::get<0>(res);
                } else {
                    angle = -90;
                }
            } else {
                angle = -90;
            }
        } else {
            auto pts = find_id_squares(*id_warped);
            if (pts) {
                auto res = decide_rotation(*pts, id_warped->rows, id_warped->cols);
                angle = std::get<0>(res);
            } else {
                angle = 0;
            }
        }
    } else if (id_warped) {
        auto pts = find_id_squares(*id_warped);
        if (pts) {
            auto res = decide_rotation(*pts, id_warped->rows, id_warped->cols);
            angle = std::get<0>(res);
        }
    }

    if (!angle) return {id_warped, mcq_warped, std::nullopt};

    std::optional<cv::Mat> id_oriented = id_warped ? std::make_optional(rotate_image(*id_warped, *angle)) : std::nullopt;
    std::optional<cv::Mat> mcq_oriented = mcq_warped ? std::make_optional(rotate_image(*mcq_warped, *angle)) : std::nullopt;
    return {id_oriented, mcq_oriented, angle};
}

cv::Mat remove_shadow(const cv::Mat& gray, float alpha) {
    int h = gray.rows, w = gray.cols;
    int k = std::max(35, (std::min(h, w) / 6) | 1);
    cv::Mat kernel = cv::getStructuringElement(cv::MORPH_ELLIPSE, cv::Size(k, k));
    cv::Mat bg;
    cv::morphologyEx(gray, bg, cv::MORPH_CLOSE, kernel);
    cv::GaussianBlur(bg, bg, cv::Size(0, 0), k / 3.0);
    cv::Mat diff;
    cv::subtract(bg, gray, diff);
    cv::Mat boosted;
    cv::convertScaleAbs(diff, boosted, alpha, 0);
    cv::Mat res;
    cv::bitwise_not(boosted, res);
    return res;
}

cv::Mat to_yolo_grayscale(const cv::Mat& gray, float alpha = 2.5f) {
    cv::Mat flat = remove_shadow(gray, alpha);
    cv::Mat denoised;
    cv::bilateralFilter(flat, denoised, 7, 45, 45);
    auto clahe = cv::createCLAHE(1.5, cv::Size(16, 16));
    cv::Mat res;
    clahe->apply(denoised, res);
    return res;
}

cv::Mat fit_on_canvas(const cv::Mat& gray, int target_w, int target_h, int margin = 10) {
    int h = gray.rows, w = gray.cols;
    int avail_w = target_w - 2 * margin;
    int avail_h = target_h - 2 * margin;
    double scale = std::min((double)avail_w / w, (double)avail_h / h);
    int new_w = std::max(1, (int)(w * scale));
    int new_h = std::max(1, (int)(h * scale));
    int interp = scale > 1 ? cv::INTER_CUBIC : cv::INTER_AREA;
    cv::Mat resized;
    cv::resize(gray, resized, cv::Size(new_w, new_h), 0, 0, interp);

    cv::Mat canvas(target_h, target_w, CV_8UC1, cv::Scalar(128));
    int x0 = (target_w - new_w) / 2;
    int y0 = (target_h - new_h) / 2;
    resized.copyTo(canvas(cv::Rect(x0, y0, new_w, new_h)));
    return canvas;
}

cv::Mat stage4_to_bgr(const cv::Mat& img, const std::string& panel_type, float alpha = 2.5f) {
    cv::Mat gray;
    cv::cvtColor(img, gray, cv::COLOR_BGR2GRAY);
    int h = gray.rows, w = gray.cols;
    int trim = std::max(2, (int)(0.015 * std::min(h, w)));
    if (h > 4 * trim && w > 4 * trim) {
        gray = gray(cv::Rect(trim, trim, w - 2*trim, h - 2*trim));
    }
    
    int tw = panel_type == "id" ? 700 : 1400;
    int th = 900;
    
    cv::Mat canvas_gray = fit_on_canvas(gray, tw, th);
    cv::Mat final_gray = to_yolo_grayscale(canvas_gray, alpha);
    cv::Mat final_bgr;
    cv::cvtColor(final_gray, final_bgr, cv::COLOR_GRAY2BGR);
    return final_bgr;
}

} // namespace

namespace preprocessing {

cv::Mat prepare_image(const std::string& image_path) {
    cv::Mat img = cv::imread(image_path);
    if (img.empty()) throw std::runtime_error("Could not load image: " + image_path);
    return img;
}

std::tuple<std::optional<cv::Mat>, std::optional<cv::Mat>> prepare_from_raw(const std::string& raw_image_path) {
    cv::Mat img = cv::imread(raw_image_path);
    if (img.empty()) throw std::runtime_error("Could not load raw image: " + raw_image_path);

    auto quads = stage1_detect_quads(img);
    auto id_quad = std::get<0>(quads);
    auto mcq_quad = std::get<1>(quads);

    std::optional<cv::Mat> id_warped = id_quad ? std::make_optional(stage2_warp(img, *id_quad)) : std::nullopt;
    std::optional<cv::Mat> mcq_warped = mcq_quad ? std::make_optional(stage2_warp(img, *mcq_quad)) : std::nullopt;

    auto orient_res = stage3_orient(id_warped, mcq_warped);
    auto id_oriented = std::get<0>(orient_res);
    auto mcq_oriented = std::get<1>(orient_res);
    auto angle = std::get<2>(orient_res);

    std::optional<cv::Mat> id_final = id_oriented ? std::make_optional(stage4_to_bgr(*id_oriented, "id")) : std::nullopt;
    std::optional<cv::Mat> mcq_final = mcq_oriented ? std::make_optional(stage4_to_bgr(*mcq_oriented, "mcq")) : std::nullopt;

    return {id_final, mcq_final};
}

} // namespace preprocessing
