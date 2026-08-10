#include "preprocessing.h"
#include <vector>
#include <algorithm>
#include <cmath>
#include <stdexcept>

namespace {

// Laplacian variance is measured after resizing to this fixed reference
// width, so the score means the same thing regardless of the capturing
// phone's native camera resolution (raw variance scales with resolution,
// so an un-normalized threshold tuned against one phone's output would
// silently drift wrong on another phone's higher/lower-res photos).
constexpr int BLUR_REFERENCE_WIDTH = 1000;

// Default fallback for blur threshold
constexpr double DEFAULT_BLUR_VARIANCE_THRESHOLD = 15.0;

double compute_blur_score(const cv::Mat& img) {
    cv::Mat gray;
    if (img.channels() == 3) {
        cv::cvtColor(img, gray, cv::COLOR_BGR2GRAY);
    } else {
        gray = img;
    }

    // Normalize to a fixed reference width before measuring, so the score
    // is comparable across photos taken at different resolutions.
    if (gray.cols != BLUR_REFERENCE_WIDTH && gray.cols > 0) {
        double scale = (double)BLUR_REFERENCE_WIDTH / gray.cols;
        cv::resize(gray, gray, cv::Size(), scale, scale, cv::INTER_AREA);
    }

    cv::Mat lap;
    cv::Laplacian(gray, lap, CV_64F);
    cv::Scalar mean, stddev;
    cv::meanStdDev(lap, mean, stddev);
    return stddev[0] * stddev[0]; // variance
}

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

std::vector<QuadInfo> find_panel_quads(const cv::Mat& img, const ThresholdConfig& config) {
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
        if (a < config.min_panel_area_ratio * img_area || a > config.max_panel_area_ratio * img_area) continue;
        double peri = cv::arcLength(c, true);
        std::vector<cv::Point> approx;
        cv::approxPolyDP(c, approx, 0.02 * peri, true);
        if (approx.size() == 4 && cv::isContourConvex(approx)) {
            if (a < config.min_panel_area_ratio * img_area) {
                continue;
            }
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

std::tuple<std::optional<std::vector<cv::Point2f>>, std::optional<std::vector<cv::Point2f>>, bool> classify_quads(const std::vector<QuadInfo>& kept, int h, int w) {
    if (kept.empty()) return {std::nullopt, std::nullopt, false};
    std::vector<cv::Mat> masks;
    for (const auto& q : kept) masks.push_back(get_quad_mask(q.quad, h, w));

    int n = kept.size();
    std::vector<bool> is_container(n, false);
    bool container_found = false;
    for (int i = 0; i < n; ++i) {
        int contains = 0;
        for (int j = 0; j < n; ++j) {
            if (i != j) {
                cv::Mat inter;
                cv::bitwise_and(masks[i], masks[j], inter);
                int inter_cnt = cv::countNonZero(inter);
                int aj = cv::countNonZero(masks[j]);
                if ((double)inter_cnt / std::max(aj, 1) > 0.85) {
                    contains++;
                }
            }
        }
        
        double area_ratio = kept[i].area / (h * (double)w);
        if (contains >= 2 || (contains == 1 && area_ratio > 0.20)) {
            is_container[i] = true;
            container_found = true;
        }
    }

    std::vector<QuadInfo> leaves;
    for (int i = 0; i < n; ++i) {
        if (!is_container[i]) leaves.push_back(kept[i]);
    }
    std::sort(leaves.begin(), leaves.end(), [](const QuadInfo& a, const QuadInfo& b){ return a.area > b.area; });

    if (leaves.empty()) return {std::nullopt, std::nullopt, container_found};
    if (leaves.size() == 1) return {std::nullopt, leaves[0].quad, container_found};

    return {leaves[1].quad, leaves[0].quad, container_found};
}

std::tuple<std::optional<std::vector<cv::Point2f>>, std::optional<std::vector<cv::Point2f>>, bool> stage1_detect_quads(const cv::Mat& img, const ThresholdConfig& config) {
    auto kept = find_panel_quads(img, config);
    return classify_quads(kept, img.rows, img.cols);
}

// Pure read-only geometric sanity check on an already-found quad. Does
// NOT change what find_panel_quads/classify_quads detect or select --
// this only measures whether the 4 corner points that were already
// chosen look like a real, usable panel rectangle, so a garbage quad
// (extreme oblique angle, panel mostly cut off but still passing the
// area/convexity checks) can be caught as a warning rather than silently
// producing a warped crop full of the wrong content.
bool quad_geometry_is_sane(const std::vector<cv::Point2f>& quad, const ThresholdConfig& config) {
    if (quad.size() != 4) return false;

    cv::Point2f tl = quad[0], tr = quad[1], br = quad[2], bl = quad[3];
    double top = cv::norm(tr - tl);
    double bottom = cv::norm(br - bl);
    double left = cv::norm(bl - tl);
    double right = cv::norm(br - tr);

    if (top < 1.0 || bottom < 1.0 || left < 1.0 || right < 1.0) return false;

    // Opposite sides of a real rectangle-ish panel, even under real-world
    // perspective skew, shouldn't differ wildly in length. A cut-off or
    // extremely oblique capture tends to produce one very short side
    // (part of the panel outside the frame) or one very long side
    // (foreshortened by the angle) relative to its opposite.
    double horiz_ratio = std::max(top, bottom) / std::min(top, bottom);
    double vert_ratio = std::max(left, right) / std::min(left, right);
    if (horiz_ratio > config.max_quad_side_ratio || vert_ratio > config.max_quad_side_ratio) return false;

    // Interior angles of a real (even skewed) rectangle stay well clear
    // of 0/180 degrees. A near-degenerate quad (corners nearly collinear
    // -- a sign of a bad detection, not a real panel corner) produces an
    // angle close to a straight line.
    auto angle_deg = [](const cv::Point2f& a, const cv::Point2f& b, const cv::Point2f& c) {
        cv::Point2f v1 = a - b, v2 = c - b;
        double dot = v1.x * v2.x + v1.y * v2.y;
        double mag = cv::norm(v1) * cv::norm(v2);
        if (mag < 1e-6) return 0.0;
        double cosA = std::max(-1.0, std::min(1.0, dot / mag));
        return std::acos(cosA) * 180.0 / CV_PI;
    };
    double angles[4] = {
        angle_deg(bl, tl, tr),
        angle_deg(tl, tr, br),
        angle_deg(tr, br, bl),
        angle_deg(br, bl, tl),
    };
    for (double a : angles) {
        if (a < config.min_quad_angle_deg || a > config.max_quad_angle_deg) return false;
    }

    return true;
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

// Ported from the original Python stage3_orient.py, including the reason
// string and the explicit "don't guess, flag low confidence" branches that
// were previously dropped when this was first ported to C++. MCQ panel
// aspect ratio is the PRIMARY, blur-robust signal (portrait vs landscape);
// the ID-panel square landmark is used only to disambiguate direction.
struct OrientResult {
    std::optional<cv::Mat> id_oriented;
    std::optional<cv::Mat> mcq_oriented;
    std::optional<int> angle;
    std::string reason;
    bool low_confidence = false;
};

OrientResult stage3_orient(
    const std::optional<cv::Mat>& id_warped,
    const std::optional<cv::Mat>& mcq_warped
) {
    OrientResult result;
    std::optional<int> angle = std::nullopt;
    std::string reason;
    bool low_confidence = false;

    if (mcq_warped && id_warped) {
        int mh = mcq_warped->rows, mw = mcq_warped->cols;
        if (mw < mh) {
            // currently portrait -> must rotate 90 either way. Use the
            // ID-square landmark (if found) to pick WHICH 90; else default
            // to CW and flag low confidence.
            auto pts = find_id_squares(*id_warped);
            if (pts) {
                float sx = 0, sy = 0, sx2 = 0, sy2 = 0;
                for (auto p : *pts) { sx += p.x; sy += p.y; sx2 += p.x*p.x; sy2 += p.y*p.y; }
                float n = pts->size();
                float std_x = std::sqrt(std::max(0.0f, sx2/n - (sx/n)*(sx/n)));
                float std_y = std::sqrt(std::max(0.0f, sy2/n - (sy/n)*(sy/n)));
                if (std_x <= std_y) {
                    auto [a2, r2] = decide_rotation(*pts, id_warped->rows, id_warped->cols);
                    angle = a2;
                    reason = "mcq=portrait + " + r2;
                } else {
                    angle = -90;
                    reason = "mcq=portrait, squares ambiguous -> default 90 CW (LOW CONFIDENCE)";
                    low_confidence = true;
                }
            } else {
                angle = -90;
                reason = "mcq=portrait, squares not found -> default 90 CW (LOW CONFIDENCE, MANUAL CHECK)";
                low_confidence = true;
            }
        } else {
            // already landscape -> only need to check upside-down (0 vs 180)
            auto pts = find_id_squares(*id_warped);
            if (pts) {
                float sx = 0, sy = 0, sx2 = 0, sy2 = 0;
                for (auto p : *pts) { sx += p.x; sy += p.y; sx2 += p.x*p.x; sy2 += p.y*p.y; }
                float n = pts->size();
                float std_x = std::sqrt(std::max(0.0f, sx2/n - (sx/n)*(sx/n)));
                float std_y = std::sqrt(std::max(0.0f, sy2/n - (sy/n)*(sy/n)));
                if (std_x > std_y) {
                    auto [a2, r2] = decide_rotation(*pts, id_warped->rows, id_warped->cols);
                    angle = a2;
                    reason = "mcq=landscape + " + r2;
                } else {
                    // squares form a vertical line even though mcq looked
                    // landscape -- trust the squares, they're a direct
                    // landmark vs. a borderline aspect ratio.
                    auto [a2, r2] = decide_rotation(*pts, id_warped->rows, id_warped->cols);
                    angle = a2;
                    reason = "mcq=landscape(borderline) but squares vertical, trusting squares: " + r2;
                }
            } else {
                angle = 0;
                reason = "mcq=landscape, squares not found -> assume already correct (LOW CONFIDENCE, MANUAL CHECK)";
                low_confidence = true;
            }
        }
    } else if (id_warped) {
        auto pts = find_id_squares(*id_warped);
        if (pts) {
            auto [a2, r2] = decide_rotation(*pts, id_warped->rows, id_warped->cols);
            angle = a2;
            reason = r2;
        } else {
            reason = "no mcq panel, squares not found - MANUAL CHECK NEEDED";
            low_confidence = true;
        }
    } else {
        reason = "no id or mcq panel available for orientation";
        low_confidence = true;
    }

    result.angle = angle;
    result.reason = reason;
    result.low_confidence = low_confidence;

    if (!angle) {
        result.id_oriented = id_warped;
        result.mcq_oriented = mcq_warped;
        return result;
    }

    result.id_oriented = id_warped ? std::make_optional(rotate_image(*id_warped, *angle)) : std::nullopt;
    result.mcq_oriented = mcq_warped ? std::make_optional(rotate_image(*mcq_warped, *angle)) : std::nullopt;
    return result;
}

cv::Mat remove_shadow(const cv::Mat& gray, float alpha) {
    int h = gray.rows, w = gray.cols;
    // Cap the kernel size to 51 to prevent massive CPU overhead, but keep it large enough (> bubbles)
    int k = std::max(35, std::min(51, (std::min(h, w) / 15) | 1));
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
    // Replaced expensive bilateral filter with a simpler Gaussian blur to save CPU
    cv::GaussianBlur(flat, denoised, cv::Size(5, 5), 0);
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

std::tuple<std::optional<cv::Mat>, std::optional<cv::Mat>, QualityInfo> prepare_from_raw(const std::string& raw_image_path, const ExamConfig& config) {
    cv::setNumThreads(4);
    cv::Mat img = cv::imread(raw_image_path);
    if (img.empty()) throw std::runtime_error("Could not load raw image: " + raw_image_path);

    // Downscale early to save massive CPU cycles on 12MP+ camera photos
    constexpr int MAX_DIM = 1500;
    if (img.cols > MAX_DIM || img.rows > MAX_DIM) {
        double scale = (double)MAX_DIM / std::max(img.cols, img.rows);
        cv::resize(img, img, cv::Size(), scale, scale, cv::INTER_AREA);
    }

    QualityInfo quality;

    // Blur check on the raw input, before any warping softens edges further.
    quality.blur_score = compute_blur_score(img);
    if (quality.blur_score < config.tuning.blur_variance) {
        quality.warnings.push_back("BLUR");
        quality.is_ok = false;
    }

    auto kept = find_panel_quads(img, config.tuning);
    quality.num_quad_candidates = (int)kept.size();
    auto quads = classify_quads(kept, img.rows, img.cols);
    auto id_quad = std::get<0>(quads);
    auto mcq_quad = std::get<1>(quads);
    bool container_found = std::get<2>(quads);


    if (!id_quad) {
        quality.warnings.push_back("NO_ID_PANEL");
        quality.is_ok = false;
    } else if (!quad_geometry_is_sane(*id_quad, config.tuning)) {
        // A quad WAS found (passed find_panel_quads' area/convexity
        // checks) but its geometry doesn't look like a real panel corner
        // set -- e.g. the sheet is cut off in the frame or shot at an
        // extreme oblique angle so a garbage 4-point contour still
        // slipped through. Catch it here rather than silently warping
        // and cropping unusable content into Step 2.
        quality.warnings.push_back("ID_PANEL_GEOMETRY_INVALID");
        quality.is_ok = false;
    }
    if (!mcq_quad) {
        quality.warnings.push_back("NO_MCQ_PANEL");
        quality.is_ok = false;
    } else if (!quad_geometry_is_sane(*mcq_quad, config.tuning)) {
        quality.warnings.push_back("MCQ_PANEL_GEOMETRY_INVALID");
        quality.is_ok = false;
    }

    std::optional<cv::Mat> id_warped = id_quad ? std::make_optional(stage2_warp(img, *id_quad)) : std::nullopt;
    std::optional<cv::Mat> mcq_warped = mcq_quad ? std::make_optional(stage2_warp(img, *mcq_quad)) : std::nullopt;

    // Re-check blur on the cropped/warped panels themselves, not just the
    // raw photo. A photo can look sharp overall but end up soft in one
    // corner after perspective correction (extreme skew stretches that
    // area) -- the raw-photo check above can't see that, since it runs
    // before warping. Uses the same normalized threshold as the raw check.
    if (id_warped) {
        quality.id_panel_blur_score = compute_blur_score(*id_warped);
        if (quality.id_panel_blur_score < config.tuning.blur_variance) {
            quality.warnings.push_back("ID_PANEL_BLUR");
            quality.is_ok = false;
        }
    }
    if (mcq_warped) {
        quality.mcq_panel_blur_score = compute_blur_score(*mcq_warped);
        if (quality.mcq_panel_blur_score < config.tuning.blur_variance) {
            quality.warnings.push_back("MCQ_PANEL_BLUR");
            quality.is_ok = false;
        }
    }

    auto orient_res = stage3_orient(id_warped, mcq_warped);
    quality.orientation_reason = orient_res.reason;
    if (orient_res.low_confidence) {
        quality.warnings.push_back("ORIENTATION_LOW_CONFIDENCE");
        // Orientation being uncertain doesn't force a retake on its own
        // (this mirrors the original Python design: pass through unrotated
        // rather than mis-rotate) -- it's surfaced as a warning so the
        // proctor's preview screen can flag it, but is_ok/blur/panel-found
        // checks above are the hard retake triggers.
    }

    std::optional<cv::Mat> id_final = orient_res.id_oriented ? std::make_optional(stage4_to_bgr(*orient_res.id_oriented, "id")) : std::nullopt;
    std::optional<cv::Mat> mcq_final = orient_res.mcq_oriented ? std::make_optional(stage4_to_bgr(*orient_res.mcq_oriented, "mcq")) : std::nullopt;

    int total_pixels = 0;
    int dark_pixels = 0;
    if (id_final) {
        cv::Mat gray;
        cv::cvtColor(*id_final, gray, cv::COLOR_BGR2GRAY);
        dark_pixels += cv::countNonZero(gray < 100);
        total_pixels += gray.rows * gray.cols;
    }
    if (mcq_final) {
        cv::Mat gray;
        cv::cvtColor(*mcq_final, gray, cv::COLOR_BGR2GRAY);
        dark_pixels += cv::countNonZero(gray < 100);
        total_pixels += gray.rows * gray.cols;
    }
    if (total_pixels > 0) {
        double dark_ratio = (double)dark_pixels / total_pixels;
        // A perfectly well-exposed blank template with thin lines
        // often has very few actual ink pixels across the entire page (especially after CLAHE).
        // A ratio below the threshold indicates the page is genuinely washed out or blinded by flash.
        if (dark_ratio < config.tuning.exposure_dark_ratio) {
            quality.warnings.push_back("EXPOSURE_TOO_HIGH");
            quality.is_ok = false;
        }
    }

    return {id_final, mcq_final, quality};
}

} // namespace preprocessing