#include "shamel_preprocessing.h"
#include <opencv2/opencv.hpp>
#include <algorithm>
#include <cmath>
#include <iostream>

namespace shamel_preprocessing {

// ─── Geometry helpers (ported from phase2_frames.py / phase3_warp.py) ──────

static cv::Point2f order_pts_tl(const std::vector<cv::Point2f>& pts4) {
    // Returns the top-left point: smallest sum of (x+y)
    return *std::min_element(pts4.begin(), pts4.end(),
        [](const cv::Point2f& a, const cv::Point2f& b){ return (a.x+a.y) < (b.x+b.y); });
}

// Re-order 4 points into [TL, TR, BR, BL]
static std::vector<cv::Point2f> order_points(const std::vector<cv::Point2f>& pts) {
    std::vector<cv::Point2f> sorted = pts;
    // sort by sum (TL has smallest, BR has largest)
    std::sort(sorted.begin(), sorted.end(),
        [](const cv::Point2f& a, const cv::Point2f& b){ return (a.x+a.y) < (b.x+b.y); });
    cv::Point2f tl = sorted[0], br = sorted[3];
    // among remaining two, the one with smaller x is BL, larger x is TR
    cv::Point2f p1 = sorted[1], p2 = sorted[2];
    cv::Point2f tr = (p1.x > p2.x) ? p1 : p2;
    cv::Point2f bl = (p1.x > p2.x) ? p2 : p1;
    return {tl, tr, br, bl};
}

// Perspective-warp a quad (given 4 points) into an upright rectangle.
static cv::Mat warp_quad(const cv::Mat& img, const std::vector<cv::Point2f>& pts4) {
    auto rect = order_points(pts4);
    auto tl = rect[0], tr = rect[1], br = rect[2], bl = rect[3];

    float wa = std::sqrt(std::pow(br.x-bl.x,2) + std::pow(br.y-bl.y,2));
    float wb = std::sqrt(std::pow(tr.x-tl.x,2) + std::pow(tr.y-tl.y,2));
    int maxW = std::max(2, (int)std::max(wa, wb));

    float ha = std::sqrt(std::pow(tr.x-br.x,2) + std::pow(tr.y-br.y,2));
    float hb = std::sqrt(std::pow(tl.x-bl.x,2) + std::pow(tl.y-bl.y,2));
    int maxH = std::max(2, (int)std::max(ha, hb));

    std::vector<cv::Point2f> src = rect;
    std::vector<cv::Point2f> dst = {
        {0.f, 0.f}, {(float)(maxW-1), 0.f},
        {(float)(maxW-1), (float)(maxH-1)}, {0.f, (float)(maxH-1)}
    };
    cv::Mat M = cv::getPerspectiveTransform(src, dst);
    cv::Mat warped;
    cv::warpPerspective(img, warped, M, {maxW, maxH}, cv::INTER_CUBIC);
    return warped;
}

// Extract 4 corner points from a contour approximation.
static std::vector<cv::Point2f> contour_to_quad(const std::vector<cv::Point>& approx4) {
    std::vector<cv::Point2f> pts;
    for (const auto& p : approx4) pts.push_back({(float)p.x, (float)p.y});
    return pts;
}

// ─── MCQ divider detection (ported from find_mcq_divider_lines) ─────────────

struct DividerLines { int x1, x2, y1; };

static DividerLines find_mcq_divider_lines(const cv::Mat& gray) {
    int h = gray.rows, w = gray.cols;

    // Use a horizontal band (8%..95%) to detect vertical dividers
    cv::Mat region = gray(cv::Range((int)(h * 0.08), (int)(h * 0.95)), cv::Range::all());
    cv::Mat col_mean_f;
    cv::reduce(region, col_mean_f, 0, cv::REDUCE_AVG, CV_32F);   // 1xW float
    // darkness = 255 - brightness
    cv::Mat col_dark = 255.f - col_mean_f;

    auto argmax_in_band = [&](int lo, int hi) -> int {
        lo = std::max(0, lo); hi = std::min(w-1, hi);
        float maxVal = -1.f; int maxIdx = lo;
        for (int x = lo; x <= hi; ++x) {
            float v = col_dark.at<float>(0, x);
            if (v > maxVal) { maxVal = v; maxIdx = x; }
        }
        return maxIdx;
    };

    int x1 = argmax_in_band((int)(w*0.25), (int)(w*0.48));
    int x2 = argmax_in_band((int)(w*0.55), (int)(w*0.82));

    // Horizontal divider: find darkest row in the middle vertical band
    cv::Mat hstrip = gray(cv::Range::all(), cv::Range((int)(w*0.04), (int)(w*0.96)));
    cv::Mat row_mean_f;
    cv::reduce(hstrip, row_mean_f, 1, cv::REDUCE_AVG, CV_32F);  // Hx1 float
    cv::Mat row_dark = 255.f - row_mean_f;

    int lo3 = (int)(h*0.30), hi3 = (int)(h*0.75);
    int y1 = lo3;
    float best = -1.f;
    for (int y = lo3; y <= hi3; ++y) {
        float v = row_dark.at<float>(y, 0);
        if (v > best) { best = v; y1 = y; }
    }

    return {x1, x2, y1};
}

// ─── Detect quads in image (ported from phase2_frames.py run()) ─────────────

struct Quad {
    std::vector<cv::Point2f> pts;
    float area;
    float cx, cy;
};

static std::vector<Quad> detect_quads(const cv::Mat& img) {
    int h = img.rows, w = img.cols;
    float img_area = (float)(h * w);

    cv::Mat gray;
    cv::cvtColor(img, gray, cv::COLOR_BGR2GRAY);
    cv::GaussianBlur(gray, gray, {5,5}, 0);

    float mean_brightness = cv::mean(gray)[0];

    int block_size = (int)std::max(15.0, std::min(101.0, (double)((w / 20) | 1)));
    if (block_size % 2 == 0) block_size++;
    int const_C = (mean_brightness > 120) ? 11 : 15;

    cv::Mat thresh;
    cv::adaptiveThreshold(gray, thresh, 255,
        cv::ADAPTIVE_THRESH_GAUSSIAN_C, cv::THRESH_BINARY_INV, block_size, const_C);

    int k = std::max(3, std::min(55, w / 150));
    cv::Mat kernel = cv::getStructuringElement(cv::MORPH_RECT, {k,k});
    cv::morphologyEx(thresh, thresh, cv::MORPH_CLOSE, kernel);
    cv::dilate(thresh, thresh, kernel, cv::Point(-1,-1), 1);

    std::vector<std::vector<cv::Point>> contours;
    cv::findContours(thresh, contours, cv::RETR_LIST, cv::CHAIN_APPROX_SIMPLE);

    float min_area = std::max(200.f, img_area * 0.00015f);
    float max_area = img_area * 0.9f;

    std::vector<Quad> quads;
    for (const auto& c : contours) {
        float area = (float)cv::contourArea(c);
        if (area < min_area || area > max_area) continue;

        std::vector<cv::Point> hull;
        cv::convexHull(c, hull);
        float peri = cv::arcLength(hull, true);

        std::vector<cv::Point> approx;
        for (double eps = 0.01; eps <= 0.06; eps += 0.006) {
            cv::approxPolyDP(hull, approx, eps * peri, true);
            if (approx.size() == 4) break;
        }
        if (approx.size() != 4) {
            // Fallback: axis-aligned bounding box
            cv::Rect r = cv::boundingRect(hull);
            float ar = std::max((float)r.width/(r.height+1e-6f), (float)r.height/(r.width+1e-6f));
            if (ar < 6.f && r.area() >= min_area) {
                approx = {{r.x,r.y},{r.x+r.width,r.y},{r.x+r.width,r.y+r.height},{r.x,r.y+r.height}};
            } else continue;
        }

        cv::Moments M = cv::moments(approx);
        if (M.m00 == 0) continue;
        float cx = (float)(M.m10 / M.m00);
        float cy = (float)(M.m01 / M.m00);
        quads.push_back({contour_to_quad(approx), area, cx, cy});
    }

    // Also try Canny fallback if fewer than 3 quads
    if (quads.size() < 3) {
        cv::Mat edges;
        cv::Canny(gray, edges, 50, 150);
        cv::dilate(edges, edges, cv::Mat(), cv::Point(-1,-1), 1);
        std::vector<std::vector<cv::Point>> e_contours;
        cv::findContours(edges, e_contours, cv::RETR_LIST, cv::CHAIN_APPROX_SIMPLE);
        for (const auto& c : e_contours) {
            float area = (float)cv::contourArea(c);
            if (area < min_area || area > max_area) continue;
            float peri = cv::arcLength(c, true);
            std::vector<cv::Point> approx;
            for (double eps = 0.01; eps <= 0.08; eps += 0.006) {
                cv::approxPolyDP(c, approx, eps * peri, true);
                if (approx.size() == 4) break;
            }
            if (approx.size() != 4) {
                cv::Rect r = cv::boundingRect(c);
                float ar = std::max((float)r.width/(r.height+1e-6f), (float)r.height/(r.width+1e-6f));
                if (ar < 6.f && r.area() >= min_area)
                    approx = {{r.x,r.y},{r.x+r.width,r.y},{r.x+r.width,r.y+r.height},{r.x,r.y+r.height}};
                else continue;
            }
            cv::Moments M = cv::moments(approx);
            if (M.m00 == 0) continue;
            quads.push_back({contour_to_quad(approx), (float)cv::contourArea(approx),
                (float)(M.m10/M.m00), (float)(M.m01/M.m00)});
        }
    }

    // Sort by area descending
    std::sort(quads.begin(), quads.end(), [](const Quad& a, const Quad& b){ return a.area > b.area; });
    return quads;
}

// Helper: get bbox (xmin,ymin,xmax,ymax) from quad
static std::tuple<float,float,float,float> get_bbox(const Quad& q) {
    float xmin=1e9,ymin=1e9,xmax=-1e9,ymax=-1e9;
    for (const auto& p : q.pts) {
        xmin=std::min(xmin,p.x); ymin=std::min(ymin,p.y);
        xmax=std::max(xmax,p.x); ymax=std::max(ymax,p.y);
    }
    return {xmin,ymin,xmax,ymax};
}

// Helper: intersection area of two bounding boxes (x1,y1,x2,y2)
static float intersection_area(float a_x1,float a_y1,float a_x2,float a_y2,
                               float b_x1,float b_y1,float b_x2,float b_y2) {
    float ix1=std::max(a_x1,b_x1), iy1=std::max(a_y1,b_y1);
    float ix2=std::min(a_x2,b_x2), iy2=std::min(a_y2,b_y2);
    if (ix2<=ix1||iy2<=iy1) return 0.f;
    return (ix2-ix1)*(iy2-iy1);
}

// Check whether inner's bbox is substantially inside outer's bbox (threshold of overlap).
static bool is_contained(const Quad& inner, const Quad& outer, float threshold = 0.55f) {
    auto [ix1,iy1,ix2,iy2] = get_bbox(inner);
    auto [ox1,oy1,ox2,oy2] = get_bbox(outer);
    float inter = intersection_area(ix1,iy1,ix2,iy2, ox1,oy1,ox2,oy2);
    float ia_area = std::max(1.f, (ix2-ix1)*(iy2-iy1));
    return (inter/ia_area) >= threshold;
}

// Geometry scoring: check width/height/diagonal ratios (from evaluate_quad_geometry)
struct GeometryScore {
    int score;  // 0-100
};

static GeometryScore evaluate_quad_geometry(const std::vector<cv::Point2f>& pts) {
    auto ordered = order_points(pts);
    auto tl=ordered[0], tr=ordered[1], br=ordered[2], bl=ordered[3];
    
    auto dist = [](const cv::Point2f& a, const cv::Point2f& b) {
        return std::sqrt((a.x-b.x)*(a.x-b.x) + (a.y-b.y)*(a.y-b.y));
    };
    
    float top_w = dist(tl, tr);
    float bot_w = dist(bl, br);
    float left_h = dist(tl, bl);
    float right_h = dist(tr, br);
    float diag1 = dist(tl, br);
    float diag2 = dist(tr, bl);
    
    if (top_w==0||left_h==0||diag1==0||diag2==0) return {0};
    
    int score = 100;
    float w_ratio = std::min(top_w, bot_w) / std::max(top_w, bot_w);
    float h_ratio = std::min(left_h, right_h) / std::max(left_h, right_h);
    float diag_ratio = std::min(diag1, diag2) / std::max(diag1, diag2);
    
    if (w_ratio < 0.60f) score -= (int)((0.60f - w_ratio) * 200.f);
    if (h_ratio < 0.60f) score -= (int)((0.60f - h_ratio) * 200.f);
    if (diag_ratio < 0.70f) score -= (int)((0.70f - diag_ratio) * 200.f);
    
    return {std::max(0, score)};
}

// ─── Public API ─────────────────────────────────────────────────────────────

ShamelPanels extract_shamel_panels(const std::string& image_path, bool save_to_disk) {
    ShamelPanels result;

    cv::Mat img = cv::imread(image_path);
    if (img.empty()) {
        std::cerr << "[shamel] Cannot read image: " << image_path << std::endl;
        return result;
    }
    int h = img.rows, w = img.cols;

    auto quads = detect_quads(img);
    if (quads.empty()) {
        std::cerr << "[shamel] No quads detected." << std::endl;
        return result;
    }

    // ── Identify MCQ (largest quad whose centroid is in the lower 60%) ───────
    int border = (int)(std::min(h,w) * 0.02f);
    float upper_thresh = h * 0.40f;

    auto is_page_like = [&](const Quad& q) {
        float xmin=1e9,ymin=1e9,xmax=-1e9,ymax=-1e9;
        for (auto& p : q.pts) {
            xmin=std::min(xmin,p.x); ymin=std::min(ymin,p.y);
            xmax=std::max(xmax,p.x); ymax=std::max(ymax,p.y);
        }
        bool touches = xmin<=border||ymin<=border||(xmax>=(w-border))||(ymax>=(h-border));
        return touches || q.area > (float)(h*w)*0.85f;
    };

    const Quad* mcq_quad = nullptr;
    for (const auto& q : quads) {
        if (!is_page_like(q) && q.cy > upper_thresh) { mcq_quad = &q; break; }
    }
    if (!mcq_quad) {
        for (const auto& q : quads) {
            if (!is_page_like(q)) { mcq_quad = &q; break; }
        }
    }
    if (!mcq_quad) {
        std::cerr << "[shamel] MCQ quad not found." << std::endl;
        return result;
    }

    // Warp MCQ
    result.mcq_mat = warp_quad(img, mcq_quad->pts);

    // Save full MCQ panel
    if (save_to_disk) {
        std::string mcq_path = image_path + "_shamel_mcq.jpg";
        cv::imwrite(mcq_path, *result.mcq_mat);
    }

    // ── Split MCQ into 6 sub-regions (column-major order) ────────────────────
    {
        cv::Mat& wm = *result.mcq_mat;
        int wh = wm.rows, ww = wm.cols;
        cv::Mat gray;
        cv::cvtColor(wm, gray, cv::COLOR_BGR2GRAY);
        auto [dx1, dx2, dy1] = find_mcq_divider_lines(gray);
        // ensure ordering
        if (dx1 > dx2) std::swap(dx1, dx2);

        // 6 cells in row-major layout as extracted:
        //  (xa,xb,ya,yb)
        struct Cell { std::string name; int xa,xb,ya,yb; };
        std::vector<Cell> row_major = {
            {"top_left",     0,   dx1, 0,   dy1},
            {"top_mid",      dx1, dx2, 0,   dy1},
            {"top_right",    dx2, ww,  0,   dy1},
            {"bottom_left",  0,   dx1, dy1, wh},
            {"bottom_mid",   dx1, dx2, dy1, wh},
            {"bottom_right", dx2, ww,  dy1, wh},
        };

        // Reorder to column-major fill order as required
        // fill order: top_left, bottom_left, top_mid, bottom_mid, top_right, bottom_right
        std::map<std::string, cv::Mat> cell_mats;
        for (auto& cell : row_major) {
            int ya = std::max(0, cell.ya), yb = std::min(wh, cell.yb);
            int xa = std::max(0, cell.xa), xb = std::min(ww, cell.xb);
            if (ya >= yb || xa >= xb) continue;
            cell_mats[cell.name] = wm(cv::Range(ya,yb), cv::Range(xa,xb)).clone();
        }

        int region_idx = 1;
        for (const auto& region_name : region_fill_order()) {
            auto it = cell_mats.find(region_name);
            if (it == cell_mats.end() || it->second.empty()) continue;
            if (save_to_disk) {
                std::string rpath = image_path + "_shamel_mcq_" + std::to_string(region_idx) + "_" + region_name + ".jpg";
                cv::imwrite(rpath, it->second);
            }
            result.mcq_regions.push_back(it->second);
            result.mcq_region_names.push_back(region_name);
            ++region_idx;
        }
    }

    // ── Identify ID (top-left) and Version (top-right) above MCQ ─────────────
    float mcq_top = 1e9f;
    for (auto& p : mcq_quad->pts) mcq_top = std::min(mcq_top, p.y);
    float mcq_bottom = -1e9f;
    for (auto& p : mcq_quad->pts) mcq_bottom = std::max(mcq_bottom, p.y);
    
    float mcq_left = 1e9f, mcq_right = -1e9f;
    for (auto& p : mcq_quad->pts) {
        mcq_left = std::min(mcq_left, p.x);
        mcq_right = std::max(mcq_right, p.x);
    }
    float mcq_width = mcq_right - mcq_left;
    float mcq_cx = mcq_quad->cx;
    
    float tol = std::max(5.f, h * 0.03f);

    // ---- GEOMETRIC RULE #1: no box inside box ----
    // Exclude candidates substantially contained in MCQ
    std::vector<const Quad*> header_candidates;
    const Quad* mcq_ptr = mcq_quad;  // Store MCQ pointer for comparison
    for (const auto& q : quads) {
        const Quad* q_ptr = &q;
        if (q_ptr == mcq_ptr) continue;  // Skip MCQ itself
        if (is_contained(q, *mcq_quad, 0.55f)) continue;  // NO BOX INSIDE BOX
        float qbottom = -1e9f;
        for (auto& p : q.pts) qbottom = std::max(qbottom, p.y);
        if (qbottom < mcq_top + tol) header_candidates.push_back(q_ptr);  // MUST BE ABOVE MCQ
    }

    // ---- Score all candidates by geometry quality ----
    for (auto& qptr : header_candidates) {
        auto* q = const_cast<Quad*>(qptr);
        auto geom_score = evaluate_quad_geometry(q->pts);
        // Store geometry score in a map (we can't modify Quad struct easily, so use indices)
    }
    
    // Sort by area descending, then prefer higher geometry scores
    std::vector<std::pair<int, const Quad*>> scored_candidates;  // (score, quad*)
    for (const auto& qptr : header_candidates) {
        auto score = evaluate_quad_geometry(qptr->pts).score;
        scored_candidates.push_back({score, qptr});
    }
    std::sort(scored_candidates.begin(), scored_candidates.end(),
        [](const auto& a, const auto& b){ 
            if (a.first != b.first) return a.first > b.first;  // higher score first
            return a.second->area > b.second->area;             // then larger area
        });

    const Quad* id_frame = nullptr;
    const Quad* version_frame = nullptr;
    std::string selection_error;

    if (scored_candidates.size() >= 2) {
        // ---- Split left/right relative to MCQ center ----
        std::vector<const Quad*> left_candidates, right_candidates;
        for (const auto& [_, qptr] : scored_candidates) {
            if (qptr->cx < mcq_cx)
                left_candidates.push_back(qptr);
            else
                right_candidates.push_back(qptr);
        }

        // Choose best-scoring left and right candidate
        if (!left_candidates.empty()) id_frame = left_candidates[0];
        if (!right_candidates.empty()) version_frame = right_candidates[0];

        // Fallback: if both on same side, pick two best and assign by x-position
        if (id_frame == nullptr || version_frame == nullptr) {
            std::vector<const Quad*> best_two;
            for (const auto& [_, qptr] : scored_candidates) {
                best_two.push_back(qptr);
                if (best_two.size() >= 2) break;
            }
            std::sort(best_two.begin(), best_two.end(),
                [](const Quad* a, const Quad* b){ return a->cx < b->cx; });
            id_frame = best_two[0];
            version_frame = best_two[1];
        }

        // ---- GEOMETRIC RULE #2: ID and VER must have similar areas (ratio >= 0.40) ----
        const float AREA_RATIO_THRESHOLD = 0.40f;
        if (id_frame != nullptr && version_frame != nullptr) {
            auto [id_x1,id_y1,id_x2,id_y2] = get_bbox(*id_frame);
            auto [ver_x1,ver_y1,ver_x2,ver_y2] = get_bbox(*version_frame);
            float id_area = (id_x2-id_x1)*(id_y2-id_y1);
            float ver_area = (ver_x2-ver_x1)*(ver_y2-ver_y1);
            float area_ratio = std::min(id_area, ver_area) / std::max(id_area, ver_area);

            if (area_ratio < AREA_RATIO_THRESHOLD) {
                // Determine trusted (larger) vs replacement
                const Quad* trusted = (id_area >= ver_area) ? id_frame : version_frame;
                std::string trusted_side = (id_area >= ver_area) ? "left" : "right";
                auto [trusted_x1,trusted_y1,trusted_x2,trusted_y2] = get_bbox(*trusted);
                float trusted_area = (trusted_x2-trusted_x1)*(trusted_y2-trusted_y1);

                // Re-pool: all above-MCQ, non-contained, excluding trusted
                std::vector<const Quad*> all_above;
                for (const auto& q : quads) {
                    if (&q == trusted || &q == mcq_quad) continue;
                    if (is_contained(q, *mcq_quad, 0.55f)) continue;
                    float qbottom = -1e9f;
                    for (auto& p : q.pts) qbottom = std::max(qbottom, p.y);
                    if (qbottom < mcq_top + tol) all_above.push_back(&q);
                }

                // Find best opposite-side candidate by area similarity
                const Quad* best_match = nullptr;
                float best_ratio = 0.f;
                for (const auto& q : all_above) {
                    bool is_opposite = (trusted_side == "left") ? (q->cx >= mcq_cx) : (q->cx < mcq_cx);
                    if (!is_opposite) continue;
                    auto [qx1,qy1,qx2,qy2] = get_bbox(*q);
                    float q_area = (qx2-qx1)*(qy2-qy1);
                    float ratio = std::min(q_area, trusted_area) / std::max(q_area, trusted_area);
                    if (ratio > best_ratio) {
                        best_ratio = ratio;
                        best_match = q;
                    }
                }

                if (best_match != nullptr && best_ratio >= AREA_RATIO_THRESHOLD) {
                    if (trusted_side == "left") {
                        id_frame = trusted;
                        version_frame = best_match;
                    } else {
                        id_frame = best_match;
                        version_frame = trusted;
                    }
                } else {
                    selection_error = "ID_VER_AREA_MISMATCH";
                    id_frame = nullptr;
                    version_frame = nullptr;
                }
            }
        }

        // ---- GEOMETRIC RULE #3: adjacency checks ----
        if (id_frame != nullptr && version_frame != nullptr) {
            float horiz_sep = std::abs(id_frame->cx - version_frame->cx);
            float min_sep = std::max(20.f, mcq_width * 0.15f);
            if (horiz_sep < min_sep) {
                selection_error = "ID_VER_NOT_SIDE_BY_SIDE";
                id_frame = nullptr;
                version_frame = nullptr;
            } else {
                // Both must be above MCQ top
                bool id_above = id_frame->cy < mcq_top + tol;
                bool ver_above = version_frame->cy < mcq_top + tol;
                if (!id_above || !ver_above) {
                    selection_error = "ID_VER_NOT_ABOVE_MCQ";
                    id_frame = nullptr;
                    version_frame = nullptr;
                } else if (is_contained(*id_frame, *mcq_quad, 0.55f) || 
                           is_contained(*version_frame, *mcq_quad, 0.55f)) {
                    selection_error = "ID_VER_INSIDE_MCQ";
                    id_frame = nullptr;
                    version_frame = nullptr;
                }
            }
        }

        // ---- Relaxed fallback ----
        if ((id_frame == nullptr || version_frame == nullptr) && scored_candidates.size() >= 2) {
            std::vector<const Quad*> non_contained;
            for (const auto& [_, qptr] : scored_candidates) {
                if (!is_contained(*qptr, *mcq_quad, 0.55f)) {
                    non_contained.push_back(qptr);
                }
            }
            if (non_contained.size() >= 2) {
                std::vector<const Quad*> fallback(non_contained.begin(), 
                    std::next(non_contained.begin(), std::min(size_t(2), non_contained.size())));
                std::sort(fallback.begin(), fallback.end(),
                    [](const Quad* a, const Quad* b){ return a->cx < b->cx; });
                id_frame = fallback[0];
                version_frame = fallback[1];
            }
        }
    } else if (scored_candidates.size() == 1) {
        // Only one header box found: assign based on position relative to MCQ center
        if (scored_candidates[0].second->cx < mcq_cx)
            id_frame = scored_candidates[0].second;
        else
            version_frame = scored_candidates[0].second;
    }

    // Save ID and Version panels
    if (id_frame != nullptr) {
        result.id_mat = warp_quad(img, id_frame->pts);
        if (save_to_disk) cv::imwrite(image_path + "_shamel_id.jpg", *result.id_mat);
    }
    if (version_frame != nullptr) {
        result.version_mat = warp_quad(img, version_frame->pts);
        if (save_to_disk) cv::imwrite(image_path + "_shamel_version.jpg", *result.version_mat);
    }

    // ── CREATE PHASE 2 VISUALIZATION ────────────────────────────────────────
    // Draw only the 3 final selected frames (ID, VERSION, MCQ) on original image
    // This shows the effect of geometric rule filtering. Debug-only — skipped
    // entirely (no disk write) when save_to_disk is false.
    if (save_to_disk) {
        cv::Mat vis_img = img.clone();
        int line_thickness = std::max(4, w / 400);
        double font_scale = std::max(1.5, (double)w / 800.0);

        // Draw ID frame (blue: BGR 255,0,0)
        if (id_frame != nullptr) {
            std::vector<cv::Point> id_pts;
            for (const auto& p : id_frame->pts) {
                id_pts.push_back(cv::Point((int)p.x, (int)p.y));
            }
            cv::polylines(vis_img, id_pts, true, cv::Scalar(255, 0, 0), line_thickness);
            cv::putText(vis_img, "ID", cv::Point((int)id_frame->cx - 50, (int)id_frame->cy),
                       cv::FONT_HERSHEY_SIMPLEX, font_scale, cv::Scalar(255, 0, 0), line_thickness);
        }

        // Draw VERSION frame (green: BGR 0,255,0)
        if (version_frame != nullptr) {
            std::vector<cv::Point> ver_pts;
            for (const auto& p : version_frame->pts) {
                ver_pts.push_back(cv::Point((int)p.x, (int)p.y));
            }
            cv::polylines(vis_img, ver_pts, true, cv::Scalar(0, 255, 0), line_thickness);
            cv::putText(vis_img, "VER", cv::Point((int)version_frame->cx - 50, (int)version_frame->cy),
                       cv::FONT_HERSHEY_SIMPLEX, font_scale, cv::Scalar(0, 255, 0), line_thickness);
        }

        // Draw MCQ frame (red: BGR 0,0,255)
        if (mcq_quad != nullptr) {
            std::vector<cv::Point> mcq_pts;
            for (const auto& p : mcq_quad->pts) {
                mcq_pts.push_back(cv::Point((int)p.x, (int)p.y));
            }
            cv::polylines(vis_img, mcq_pts, true, cv::Scalar(0, 0, 255), line_thickness);
            cv::putText(vis_img, "MCQ", cv::Point((int)mcq_quad->cx - 100, (int)mcq_quad->cy),
                       cv::FONT_HERSHEY_SIMPLEX, font_scale * 1.5, cv::Scalar(0, 0, 255), line_thickness + 2);
        }

        // Scale down to 40% like reference
        cv::Mat vis_small;
        cv::resize(vis_img, vis_small, cv::Size(), 0.4, 0.4);

        // Save phase 2 visualization
        std::string phase2_out = image_path + "_phase2_annotated.jpg";
        cv::imwrite(phase2_out, vis_small);
    }

    return result;
}

} // namespace shamel_preprocessing