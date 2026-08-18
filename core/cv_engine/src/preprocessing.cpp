#include "preprocessing.h"
#include <vector>
#include <algorithm>
#include <cmath>
#include <stdexcept>
#include <iostream>
#include <string>
#include <optional>

namespace {

// ─────────────────────────────────────────────────────────────────────────
// TUNABLE THRESHOLDS
//
// If panel/quad detection is failing on a real photo, these are the knobs
// to adjust. Every rejection below is logged via log_diag(), and each log
// line names the specific constant that caused the rejection — so when
// debugging a bad image, read the [preprocessing] lines on stderr (or the
// "diagnostics" array step1_extract_panels attaches to its JSON result)
// first, then tune the constant it points at.
// ─────────────────────────────────────────────────────────────────────────

// Stage 1 — panel quad (ID card / MCQ sheet) detection ----------------------
constexpr double kPanelMinAreaFrac   = 0.03;  // reject candidate quads smaller than this fraction of the full image area. Lower this if a real panel is small in-frame and being missed.
constexpr double kPanelMaxAreaFrac   = 0.45;  // reject candidate quads larger than this fraction. Raise this if a legitimately large/close-up panel is being rejected.
constexpr int    kCannyLow           = 30;
constexpr int    kCannyHigh          = 100;   // Canny hysteresis thresholds. Raise both if noisy backgrounds are producing spurious edges; lower both if real panel edges aren't showing up in the edge map at all.
constexpr int    kDilateKernelSize   = 5;
constexpr int    kDilateIterations   = 1;     // raise if panel edges come out broken/disconnected after Canny, which stops findContours from closing the quad
constexpr double kApproxEpsilonFrac  = 0.02;  // fraction of contour perimeter used by approxPolyDP. Raise if real quads are being approximated with 5+ noisy corners and rejected for not being a clean 4-gon; lower if corners are being over-simplified.
constexpr double kQuadDupIoUThresh   = 0.75;  // two candidate quads whose intersection/union exceeds this are treated as duplicates (the smaller is dropped)
constexpr double kContainerIoThresh  = 0.85;  // a quad that is contained inside another by at least this fraction is treated as a "container" (e.g. the outer sheet border) and excluded from panel candidates. Lower if a real panel is being swallowed as a "container" of another quad.

// Stage 1b — ID-square marker detection (the 3 small corner squares used to orient the ID panel) --
constexpr double kIdSquareMinAreaFrac = 0.004;
constexpr double kIdSquareMaxAreaFrac = 0.06;  // area range (as a fraction of the ID panel) that a corner square must fall in. Adjust to match how large the printed squares are relative to the panel.
constexpr double kIdSquareAspectMin   = 0.6;
constexpr double kIdSquareAspectMax   = 1.6;   // bounding-box aspect ratio range for a valid square. Widen if squares are being rejected for looking slightly non-square (e.g. due to residual warp skew).
constexpr double kIdClusterDistFrac   = 0.04;  // fraction of max(h,w) — candidate boxes within this distance of each other are merged into one cluster. Raise if one physical square is being split into multiple boxes.
constexpr int    kMinIdSquareBoxes    = 3;     // minimum number of raw candidate boxes required before clustering is even attempted
constexpr int    kMinIdSquareClusters = 3;     // minimum number of distinct clusters required to proceed with orientation detection

// Stage 0 — overall image-quality checks, run once per prepare_from_raw()
// call, BEFORE any panel detection. These exist to explain *why* stage1 /
// stage1b are likely to fail (or did fail) in terms a phone-camera user can
// actually act on — "retake with better light" instead of a bare
// "NO_PANELS_DETECTED". Each one is logged regardless of pass/fail so the
// numbers are visible for tuning even on a successful run.
constexpr double kBlurVarianceThreshold = 60.0;   // Laplacian-variance-of-grayscale below this = blurry. Very camera/resolution dependent — raise if genuinely sharp photos get flagged, lower if obviously blurry ones don't.
constexpr double kMinMeanBrightness     = 40.0;   // 0-255 scale. Below this the WHOLE image is likely too dark (bad lighting / underexposed).
constexpr double kMaxMeanBrightness     = 235.0;  // above this the whole image is likely blown out / overexposed.
constexpr double kOverexposedIntensity  = 250.0;  // a pixel at/above this (0-255) is considered "blown out"
constexpr double kOverexposedPixelFrac  = 0.25;   // flag overexposure if at least this fraction of pixels are blown out — catches localized glare/flash reflection even when the overall mean brightness looks fine
constexpr double kUnderexposedIntensity = 8.0;    // a pixel at/below this is considered "crushed black"
constexpr double kUnderexposedPixelFrac = 0.35;   // flag underexposure if at least this fraction of pixels are crushed black
constexpr int    kMinShortSidePx        = 480;    // min(width, height) in px. Below this there's usually too little detail for reliable quad/bubble detection regardless of everything else.
constexpr int    kBorderTouchMarginPx   = 6;      // an otherwise-plausible-sized contour whose bounding box comes within this many px of the image edge is treated as "likely cut off" (paper probably isn't fully in frame)

// Collects human-readable diagnostic lines for the most recent
// preprocessing::prepare_from_raw() call. Cleared at the start of every
// call so results never leak across images. Exposed via
// preprocessing::get_diagnostics() (forward-declared directly in ffi.cpp,
// no header change needed) so the FFI layer can surface *why* a panel
// wasn't found instead of just reporting a bare failure.
std::vector<std::string>& diagnostics() {
    static thread_local std::vector<std::string> diags;
    return diags;
}

void log_diag(const std::string& msg) {
    diagnostics().push_back(msg);
    std::cerr << "[preprocessing] " << msg << std::endl;
}

// Stage 0 result: the overall quality read on the raw input image, used to
// pick a specific, actionable "cause" (BLUR / OVEREXPOSED / UNDEREXPOSED /
// LOW_RESOLUTION / PARTIAL_CAPTURE) instead of a generic detection failure.
struct QualityAssessment {
    bool   blur = false;
    double blur_score = 0.0;
    bool   overexposed = false;
    bool   underexposed = false;
    double mean_brightness = 0.0;
    double overexposed_frac = 0.0;
    double underexposed_frac = 0.0;
    bool   low_resolution = false;
    bool   partial_capture = false;  // best area-filtered contour touches the image border (paper likely cropped/out of frame)
};

QualityAssessment& quality() {
    static thread_local QualityAssessment q;
    return q;
}

void assess_image_quality(const cv::Mat& img) {
    QualityAssessment& q = quality();
    q = QualityAssessment{};

    int h = img.rows, w = img.cols;
    cv::Mat gray;
    cv::cvtColor(img, gray, cv::COLOR_BGR2GRAY);

    // Blur: variance of the Laplacian. Sharp images have a lot of
    // high-frequency edge content -> high variance; blurry ones are smooth
    // -> low variance.
    cv::Mat lap;
    cv::Laplacian(gray, lap, CV_64F);
    cv::Scalar lap_mean, lap_stddev;
    cv::meanStdDev(lap, lap_mean, lap_stddev);
    q.blur_score = lap_stddev[0] * lap_stddev[0];
    q.blur = q.blur_score < kBlurVarianceThreshold;
    log_diag("stage0 (quality): blur_score=" + std::to_string(q.blur_score) + " (Laplacian variance, threshold kBlurVarianceThreshold="
             + std::to_string(kBlurVarianceThreshold) + ") -> " + (q.blur ? "BLURRY" : "OK"));

    // Exposure: overall mean brightness plus clipped-highlight/shadow pixel
    // fractions, so both "the whole photo is dark/bright" and "there's a
    // blown-out glare patch on part of the sheet" get caught.
    cv::Scalar brightness_mean = cv::mean(gray);
    q.mean_brightness = brightness_mean[0];

    long total_px = (long)h * (long)w;
    int over_px = total_px > 0 ? cv::countNonZero(gray >= kOverexposedIntensity) : 0;
    int under_px = total_px > 0 ? cv::countNonZero(gray <= kUnderexposedIntensity) : 0;
    q.overexposed_frac  = total_px > 0 ? (double)over_px / (double)total_px : 0.0;
    q.underexposed_frac = total_px > 0 ? (double)under_px / (double)total_px : 0.0;

    q.overexposed  = (q.mean_brightness > kMaxMeanBrightness) || (q.overexposed_frac > kOverexposedPixelFrac);
    q.underexposed = (q.mean_brightness < kMinMeanBrightness) || (q.underexposed_frac > kUnderexposedPixelFrac);

    log_diag("stage0 (quality): mean_brightness=" + std::to_string(q.mean_brightness) + " (acceptable range [kMinMeanBrightness="
             + std::to_string(kMinMeanBrightness) + ", kMaxMeanBrightness=" + std::to_string(kMaxMeanBrightness) + "]), overexposed_frac="
             + std::to_string(q.overexposed_frac) + " (threshold kOverexposedPixelFrac=" + std::to_string(kOverexposedPixelFrac)
             + "), underexposed_frac=" + std::to_string(q.underexposed_frac) + " (threshold kUnderexposedPixelFrac="
             + std::to_string(kUnderexposedPixelFrac) + ") -> "
             + (q.overexposed ? "OVEREXPOSED (possible glare/flash reflection or too bright)"
                : (q.underexposed ? "UNDEREXPOSED (too dark / poor lighting)" : "OK")));

    // Resolution
    int short_side = std::min(h, w);
    q.low_resolution = short_side < kMinShortSidePx;
    log_diag("stage0 (quality): short_side=" + std::to_string(short_side) + "px (min kMinShortSidePx="
             + std::to_string(kMinShortSidePx) + ") -> " + (q.low_resolution ? "TOO LOW RESOLUTION" : "OK"));
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

std::vector<QuadInfo> find_panel_quads(const cv::Mat& img) {
    int h = img.rows, w = img.cols;
    double img_area = h * w;
    cv::Mat gray, edges;
    cv::cvtColor(img, gray, cv::COLOR_BGR2GRAY);
    cv::medianBlur(gray, gray, 5);
    cv::Canny(gray, edges, kCannyLow, kCannyHigh);
    cv::Mat kernel = cv::Mat::ones(kDilateKernelSize, kDilateKernelSize, CV_8UC1);
    cv::dilate(edges, edges, kernel, cv::Point(-1, -1), kDilateIterations);

    std::vector<std::vector<cv::Point>> cnts;
    cv::findContours(edges, cnts, cv::RETR_LIST, cv::CHAIN_APPROX_SIMPLE);

    log_diag("stage1 (" + std::to_string(w) + "x" + std::to_string(h) + " image): Canny+dilate produced "
             + std::to_string(cnts.size()) + " raw contours");

    int rejected_area = 0, rejected_shape = 0;
    bool any_border_touch = false;
    std::vector<QuadInfo> quads;
    for (const auto& c : cnts) {
        double a = cv::contourArea(c);
        if (a < kPanelMinAreaFrac * img_area || a > kPanelMaxAreaFrac * img_area) {
            ++rejected_area;
            continue;
        }
        cv::Rect br = cv::boundingRect(c);
        if (br.x <= kBorderTouchMarginPx || br.y <= kBorderTouchMarginPx ||
            br.x + br.width  >= w - kBorderTouchMarginPx ||
            br.y + br.height >= h - kBorderTouchMarginPx) {
            any_border_touch = true;
        }
        double peri = cv::arcLength(c, true);
        std::vector<cv::Point> approx;
        cv::approxPolyDP(c, approx, kApproxEpsilonFrac * peri, true);
        if (approx.size() == 4 && cv::isContourConvex(approx)) {
            quads.push_back({a, order_pts(approx)});
        } else {
            ++rejected_shape;
        }
    }

    log_diag("stage1: " + std::to_string(rejected_area) + " contours rejected by area filter (kPanelMinAreaFrac="
             + std::to_string(kPanelMinAreaFrac) + ", kPanelMaxAreaFrac=" + std::to_string(kPanelMaxAreaFrac)
             + " of image_area=" + std::to_string((long)img_area) + "), " + std::to_string(rejected_shape)
             + " rejected for not being a convex 4-corner shape (kApproxEpsilonFrac="
             + std::to_string(kApproxEpsilonFrac) + ") -> " + std::to_string(quads.size()) + " candidate quads before dedup");

    if (quads.empty()) {
        quality().partial_capture = any_border_touch;
        log_diag("stage1: NO_PANEL_QUADS_FOUND — 0 contours survived the area + 4-corner-convex filter. "
                 "If a panel/sheet border IS visible in the source photo, try lowering kPanelMinAreaFrac, "
                 "widening [kCannyLow, kCannyHigh], or raising kApproxEpsilonFrac (see TUNABLE THRESHOLDS block)."
                 + std::string(any_border_touch
                     ? " NOTE: at least one otherwise plausibly-sized contour touched the image border — the "
                       "paper/panel may be cut off or out of frame; try stepping back so the whole sheet is visible."
                     : ""));
        return {};
    }

    std::sort(quads.begin(), quads.end(), [](const QuadInfo& a, const QuadInfo& b){ return a.area > b.area; });

    std::vector<QuadInfo> kept;
    std::vector<cv::Mat> kept_masks;
    int dropped_dupes = 0;
    for (const auto& q : quads) {
        cv::Mat m1 = get_quad_mask(q.quad, h, w);
        bool dup = false;
        for (const auto& m2 : kept_masks) {
            cv::Mat inter, uni;
            cv::bitwise_and(m1, m2, inter);
            cv::bitwise_or(m1, m2, uni);
            int inter_cnt = cv::countNonZero(inter);
            int uni_cnt = cv::countNonZero(uni);
            if ((double)inter_cnt / std::max(uni_cnt, 1) > kQuadDupIoUThresh) {
                dup = true;
                break;
            }
        }
        if (!dup) {
            kept.push_back(q);
            kept_masks.push_back(m1);
        } else {
            ++dropped_dupes;
        }
    }

    log_diag("stage1: dropped " + std::to_string(dropped_dupes) + " duplicate quads (IoU > kQuadDupIoUThresh="
             + std::to_string(kQuadDupIoUThresh) + ") -> " + std::to_string(kept.size()) + " unique candidate quads kept");

    return kept;
}

std::tuple<std::optional<std::vector<cv::Point2f>>, std::optional<std::vector<cv::Point2f>>> classify_quads(const std::vector<QuadInfo>& kept, int h, int w) {
    if (kept.empty()) {
        log_diag("stage1-classify: called with 0 candidate quads (find_panel_quads found nothing) -> id=NOT FOUND, mcq=NOT FOUND");
        return {std::nullopt, std::nullopt};
    }
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
                if ((double)inter_cnt / std::max(aj, 1) > kContainerIoThresh) {
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

    log_diag("stage1-classify: " + std::to_string(n) + " candidate quads -> "
             + std::to_string(n - (int)leaves.size()) + " classified as containers (>=kContainerIoThresh="
             + std::to_string(kContainerIoThresh) + " contained in another quad), " + std::to_string(leaves.size())
             + " leaf quad(s) remain");

    if (leaves.empty()) {
        log_diag("stage1-classify: NO_LEAF_QUADS — every candidate quad was classified as a container of another "
                 "(nested boxes, e.g. the outer sheet border swallowing everything). Try lowering kContainerIoThresh "
                 "if a real panel is being misclassified this way.");
        return {std::nullopt, std::nullopt};
    }
    if (leaves.size() == 1) {
        log_diag("stage1-classify: only 1 leaf quad found -> treated as the MCQ panel; NO_ID_PANEL_FOUND "
                 "(the ID card quad was either never detected in stage1 or was classified as a container)");
        return {std::nullopt, leaves[0].quad};
    }

    log_diag("stage1-classify: " + std::to_string(leaves.size()) + " leaf quads -> largest = mcq panel, "
             "2nd-largest = id panel (any leaves beyond the top 2 are discarded)");
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

    int rejected_area = 0, rejected_shape = 0, rejected_aspect = 0;
    for (const auto& c : cnts) {
        double a = cv::contourArea(c);
        if (a < kIdSquareMinAreaFrac * h * w || a > kIdSquareMaxAreaFrac * h * w) { ++rejected_area; continue; }
        double peri = cv::arcLength(c, true);
        std::vector<cv::Point> approx;
        cv::approxPolyDP(c, approx, 0.03 * peri, true);
        if (approx.size() != 4 || !cv::isContourConvex(approx)) { ++rejected_shape; continue; }
        cv::Rect r = cv::boundingRect(approx);
        float ar = (float)r.width / r.height;
        if (ar > kIdSquareAspectMin && ar < kIdSquareAspectMax) {
            boxes.push_back({r.x + r.width / 2.0f, r.y + r.height / 2.0f, (float)(r.width * r.height)});
        } else {
            ++rejected_aspect;
        }
    }

    log_diag("stage1b (id-squares, " + std::to_string(w) + "x" + std::to_string(h) + " panel): "
             + std::to_string(cnts.size()) + " contours -> " + std::to_string(rejected_area) + " rejected by area "
             "(kIdSquareMinAreaFrac=" + std::to_string(kIdSquareMinAreaFrac) + ", kIdSquareMaxAreaFrac="
             + std::to_string(kIdSquareMaxAreaFrac) + "), " + std::to_string(rejected_shape)
             + " rejected for not being a convex 4-corner shape, " + std::to_string(rejected_aspect)
             + " rejected by aspect ratio (kIdSquareAspectMin=" + std::to_string(kIdSquareAspectMin)
             + ", kIdSquareAspectMax=" + std::to_string(kIdSquareAspectMax) + ") -> " + std::to_string(boxes.size())
             + " candidate boxes");

    if (boxes.size() < kMinIdSquareBoxes) {
        log_diag("stage1b: NOT_ENOUGH_ID_SQUARE_BOXES — found " + std::to_string(boxes.size()) + ", need >= "
                 + std::to_string(kMinIdSquareBoxes) + " (kMinIdSquareBoxes). Orientation cannot be determined "
                 "from ID-panel corner squares; widen the area/aspect thresholds above if the squares are visibly "
                 "present in the warped ID panel image.");
        return std::nullopt;
    }

    std::sort(boxes.begin(), boxes.end(), [](const Box& a, const Box& b){ return a.area > b.area; });

    struct Cluster { float cx, cy; std::vector<cv::Point2f> pts; };
    std::vector<Cluster> clusters;

    for (const auto& b : boxes) {
        bool placed = false;
        for (auto& cl : clusters) {
            if (std::hypot(b.cx - cl.cx, b.cy - cl.cy) < kIdClusterDistFrac * std::max(h, w)) {
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

    log_diag("stage1b: " + std::to_string(boxes.size()) + " candidate boxes clustered (kIdClusterDistFrac="
             + std::to_string(kIdClusterDistFrac) + ") into " + std::to_string(clusters.size()) + " distinct squares");

    if (clusters.size() < kMinIdSquareClusters) {
        log_diag("stage1b: NOT_ENOUGH_ID_SQUARE_CLUSTERS — got " + std::to_string(clusters.size()) + ", need >= "
                 + std::to_string(kMinIdSquareClusters) + " (kMinIdSquareClusters). Either fewer than 3 corner "
                 "squares are visible, or kIdClusterDistFrac is merging distinct squares together — try lowering it.");
        return std::nullopt;
    }

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

// Returns the stage-by-stage diagnostic lines collected during the most
// recent prepare_from_raw() call — e.g. why the panel-quad or id-square
// detection rejected everything, and which TUNABLE THRESHOLDS constant to
// adjust. Forward-declare this in ffi.cpp (or add it to preprocessing.h)
// to surface it in step1_extract_panels' JSON result instead of just a
// bare status string.
std::vector<std::string> get_diagnostics() {
    return diagnostics();
}

// Returns the single most likely image-quality issue found while
// processing the most recent prepare_from_raw() call, in priority order:
// root causes that would ALSO explain a downstream panel-detection failure
// (BLUR, exposure problems, low resolution) are surfaced ahead of the more
// generic PARTIAL_CAPTURE signal. Returns std::nullopt when nothing
// specific stood out — callers should fall back to a generic cause (e.g.
// "NO_PANELS_DETECTED") in that case rather than treat nullopt as success.
std::optional<std::string> get_quality_cause() {
    const auto& q = quality();
    if (q.blur)            return "BLUR";
    if (q.overexposed)     return "OVEREXPOSED";
    if (q.underexposed)    return "UNDEREXPOSED";
    if (q.low_resolution)  return "LOW_RESOLUTION";
    if (q.partial_capture) return "PARTIAL_CAPTURE";
    return std::nullopt;
}

cv::Mat prepare_image(const std::string& image_path) {
    cv::Mat img = cv::imread(image_path);
    if (img.empty()) throw std::runtime_error("Could not load image: " + image_path);
    return img;
}

std::tuple<std::optional<cv::Mat>, std::optional<cv::Mat>> prepare_from_raw(const std::string& raw_image_path) {
    diagnostics().clear();
    quality() = QualityAssessment{};

    cv::Mat img = cv::imread(raw_image_path);
    if (img.empty()) throw std::runtime_error("Could not load raw image: " + raw_image_path);

    assess_image_quality(img);

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

    if (!id_final && !mcq_final) {
        auto cause = get_quality_cause();
        log_diag("prepare_from_raw: FAILED — neither the ID panel nor the MCQ panel could be extracted from '"
                 + raw_image_path + "'."
                 + (cause ? (" Most likely cause: " + *cause + " (see stage0 quality lines above).")
                          : " No specific image-quality issue stood out — likely a framing/angle/occlusion problem "
                            "rather than blur/exposure/resolution.")
                 + " See stage1/stage1b lines above for the detection-side details.");
    } else {
        if (!id_final)  log_diag("prepare_from_raw: WARNING — ID panel not found (MCQ panel found OK). student_id will be empty downstream.");
        if (!mcq_final) log_diag("prepare_from_raw: WARNING — MCQ panel not found (ID panel found OK). questions will be empty downstream.");
    }

    return {id_final, mcq_final};
}

} // namespace preprocessing