#include "shamel_phases.h"
#include <opencv2/opencv.hpp>
#include <algorithm>
#include <cmath>
#include <iostream>
#include <fstream>
#include <sstream>
#include <filesystem>
#include <map>

namespace fs = std::filesystem;

namespace shamel_phases {

// Helper: Extract base filename without extension
static std::string get_base_name(const std::string& path) {
    auto p = fs::path(path);
    auto filename = p.filename().string();
    size_t dot_pos = filename.find_last_of(".");
    if (dot_pos != std::string::npos) {
        return filename.substr(0, dot_pos);
    }
    return filename;
}

// ─────────────────────────────────────────────────────────────────────────────
// PHASE 1: Blur Detection
// ─────────────────────────────────────────────────────────────────────────────

Phase1Result phase1_blur_detection(const std::string& image_path, const std::string& output_dir) {
    Phase1Result result;
    
    std::cout << " -> [PHASE 1] Loading FULL RESOLUTION: " << get_base_name(image_path) << std::endl;
    
    cv::Mat img = cv::imread(image_path);
    if (img.empty()) {
        result.is_ok = false;
        result.blur_score = 0.0;
        return result;
    }
    
    // Compute blur score (Laplacian variance)
    cv::Mat gray;
    cv::cvtColor(img, gray, cv::COLOR_BGR2GRAY);
    
    // Scale to 1000 width if needed for consistency
    if (gray.cols > 0 && gray.cols != 1000) {
        double scale = 1000.0 / gray.cols;
        cv::Mat gray_scaled;
        cv::resize(gray, gray_scaled, cv::Size(), scale, scale, cv::INTER_AREA);
        gray = gray_scaled;
    }
    
    cv::Mat lap;
    cv::Laplacian(gray, lap, CV_64F);
    cv::Scalar mean, stddev;
    cv::meanStdDev(lap, mean, stddev);
    result.blur_score = stddev[0] * stddev[0];  // variance
    
    const double BLUR_VARIANCE_THRESHOLD = 15.0;
    result.is_ok = (result.blur_score >= BLUR_VARIANCE_THRESHOLD);
    
    // Save full resolution image
    std::string base_name = get_base_name(image_path);
    
    result.output_path = output_dir + "/" + base_name + "_phase1_fullres.jpg";
    cv::imwrite(result.output_path, img);
    
    return result;
}

// ─────────────────────────────────────────────────────────────────────────────
// PHASE 2: Frame Detection (Quad detection + validation)
// ─────────────────────────────────────────────────────────────────────────────

static std::vector<cv::Point2f> order_points(const std::vector<cv::Point2f>& pts) {
    std::vector<cv::Point2f> sorted = pts;
    std::sort(sorted.begin(), sorted.end(),
        [](const cv::Point2f& a, const cv::Point2f& b) { return (a.x + a.y) < (b.x + b.y); });
    
    cv::Point2f tl = sorted[0], br = sorted[3];
    cv::Point2f p1 = sorted[1], p2 = sorted[2];
    cv::Point2f tr = (p1.x > p2.x) ? p1 : p2;
    cv::Point2f bl = (p1.x > p2.x) ? p2 : p1;
    
    return {tl, tr, br, bl};
}

static std::vector<Quad> detect_quads(const cv::Mat& img) {
    int h = img.rows, w = img.cols;
    float img_area = (float)(h * w);
    
    cv::Mat gray;
    cv::cvtColor(img, gray, cv::COLOR_BGR2GRAY);
    cv::GaussianBlur(gray, gray, {5, 5}, 0);
    
    float mean_brightness = cv::mean(gray)[0];
    
    int block_size = (int)std::max(15.0, std::min(101.0, (double)((w / 20) | 1)));
    if (block_size % 2 == 0) block_size++;
    int const_C = (mean_brightness > 120) ? 11 : 15;
    
    cv::Mat thresh;
    cv::adaptiveThreshold(gray, thresh, 255,
        cv::ADAPTIVE_THRESH_GAUSSIAN_C, cv::THRESH_BINARY_INV, block_size, const_C);
    
    int k = std::max(3, std::min(55, w / 150));
    cv::Mat kernel = cv::getStructuringElement(cv::MORPH_RECT, {k, k});
    cv::morphologyEx(thresh, thresh, cv::MORPH_CLOSE, kernel);
    cv::dilate(thresh, thresh, kernel, cv::Point(-1, -1), 1);
    
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
            cv::Rect r = cv::boundingRect(hull);
            float ar = std::max((float)r.width / (r.height + 1e-6f), (float)r.height / (r.width + 1e-6f));
            if (ar < 6.f && r.area() >= min_area) {
                approx = {{r.x, r.y}, {r.x + r.width, r.y}, {r.x + r.width, r.y + r.height}, {r.x, r.y + r.height}};
            } else continue;
        }
        
        cv::Moments M = cv::moments(approx);
        if (M.m00 == 0) continue;
        
        std::vector<cv::Point2f> pts;
        for (const auto& p : approx) pts.push_back({(float)p.x, (float)p.y});
        
        quads.push_back({pts, (float)area, (float)(M.m10 / M.m00), (float)(M.m01 / M.m00)});
    }
    
    // Fallback: Canny edge detection if < 3 quads
    if (quads.size() < 3) {
        cv::Mat edges;
        cv::Canny(gray, edges, 50, 150);
        cv::dilate(edges, edges, cv::Mat(), cv::Point(-1, -1), 1);
        
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
                float ar = std::max((float)r.width / (r.height + 1e-6f), (float)r.height / (r.width + 1e-6f));
                if (ar < 6.f && r.area() >= min_area) {
                    approx = {{r.x, r.y}, {r.x + r.width, r.y}, {r.x + r.width, r.y + r.height}, {r.x, r.y + r.height}};
                } else continue;
            }
            
            cv::Moments M = cv::moments(approx);
            if (M.m00 == 0) continue;
            
            std::vector<cv::Point2f> pts;
            for (const auto& p : approx) pts.push_back({(float)p.x, (float)p.y});
            
            quads.push_back({pts, (float)area, (float)(M.m10 / M.m00), (float)(M.m01 / M.m00)});
        }
    }
    
    std::sort(quads.begin(), quads.end(),
        [](const Quad& a, const Quad& b) { return a.area > b.area; });
    
    return quads;
}

static bool is_contained(const Quad& inner, const Quad& outer, float threshold = 0.55f) {
    auto bbox = [](const Quad& q) {
        float xmin = 1e9, ymin = 1e9, xmax = -1e9, ymax = -1e9;
        for (const auto& p : q.pts) {
            xmin = std::min(xmin, p.x); ymin = std::min(ymin, p.y);
            xmax = std::max(xmax, p.x); ymax = std::max(ymax, p.y);
        }
        return cv::Rect2f(xmin, ymin, xmax - xmin, ymax - ymin);
    };
    
    auto ia = bbox(inner), oa = bbox(outer);
    float ix1 = std::max(ia.x, oa.x), iy1 = std::max(ia.y, oa.y);
    float ix2 = std::min(ia.x + ia.width, oa.x + oa.width);
    float iy2 = std::min(ia.y + ia.height, oa.y + oa.height);
    
    if (ix2 <= ix1 || iy2 <= iy1) return false;
    float inter = (ix2 - ix1) * (iy2 - iy1);
    float ia_area = std::max(1.f, ia.width * ia.height);
    return (inter / ia_area) >= threshold;
}

static int quad_geometry_score(const Quad& q) {
    auto rect = order_points(q.pts);
    cv::Point2f tl = rect[0], tr = rect[1], br = rect[2], bl = rect[3];
    auto dist = [](const cv::Point2f& a, const cv::Point2f& b) {
        return std::sqrt((a.x - b.x) * (a.x - b.x) + (a.y - b.y) * (a.y - b.y));
    };

    float top_w = dist(tl, tr);
    float bot_w = dist(bl, br);
    float left_h = dist(tl, bl);
    float right_h = dist(tr, br);
    float diag1 = dist(tl, br);
    float diag2 = dist(tr, bl);
    if (top_w <= 0 || left_h <= 0 || diag1 <= 0 || diag2 <= 0) return 0;

    int score = 100;
    float w_ratio = std::min(top_w, bot_w) / std::max(top_w, bot_w);
    float h_ratio = std::min(left_h, right_h) / std::max(left_h, right_h);
    float diag_ratio = std::min(diag1, diag2) / std::max(diag1, diag2);

    if (w_ratio < 0.60f) score -= (int)((0.60f - w_ratio) * 200.f);
    if (h_ratio < 0.60f) score -= (int)((0.60f - h_ratio) * 200.f);
    if (diag_ratio < 0.70f) score -= (int)((0.70f - diag_ratio) * 200.f);
    return std::max(0, score);
}

Phase2Result phase2_frame_detection(const std::string& image_path, const std::string& output_dir) {
    Phase2Result result;
    result.confidence_score = 0;
    result.status = "RETAKE";
    
    std::cout << " -> [PHASE 2] Extracting Frames with Dynamic Kernels..." << std::endl;
    
    cv::Mat img = cv::imread(image_path);
    if (img.empty()) {
        result.errors.push_back("CANNOT_READ_IMAGE");
        return result;
    }
    
    int h = img.rows, w = img.cols;
    
    // Check brightness and warn if image is too dark
    cv::Mat gray;
    cv::cvtColor(img, gray, cv::COLOR_BGR2GRAY);
    double mean_brightness = cv::mean(gray)[0];
    if (mean_brightness < 80.0) {
        result.errors.push_back("UNDEREXPOSED");
    }
    
    auto quads = detect_quads(img);
    
    if (quads.empty()) {
        result.errors.push_back("MISSING_MCQ");
        return result;
    }
    
    // Identify MCQ (largest in lower 60%)
    int border = (int)(std::min(h, w) * 0.02f);
    float upper_thresh = h * 0.40f;
    
    auto is_page_like = [&](const Quad& q) {
        float xmin = 1e9, ymin = 1e9, xmax = -1e9, ymax = -1e9;
        for (const auto& p : q.pts) {
            xmin = std::min(xmin, p.x); ymin = std::min(ymin, p.y);
            xmax = std::max(xmax, p.x); ymax = std::max(ymax, p.y);
        }
        bool touches = xmin <= border || ymin <= border || xmax >= (w - border) || ymax >= (h - border);
        return touches || q.area > (float)(h * w) * 0.85f;
    };
    
    Quad* mcq_quad = nullptr;
    for (auto& q : quads) {
        if (!is_page_like(q) && q.cy > upper_thresh) {
            mcq_quad = &q;
            break;
        }
    }
    if (!mcq_quad) {
        for (auto& q : quads) {
            if (!is_page_like(q)) {
                mcq_quad = &q;
                break;
            }
        }
    }
    
    if (mcq_quad) {
        result.mcq_quad = *mcq_quad;
        result.confidence_score = 85;
    } else {
        result.errors.push_back("MCQ_NOT_FOUND");
        result.confidence_score = 0;
    }
    
    // Identify ID and Version (strict reference rules)
    if (mcq_quad && quads.size() > 1) {
        float mcq_top = 1e9f;
        float mcq_left = 1e9f, mcq_right = -1e9f;
        for (const auto& p : mcq_quad->pts) {
            mcq_top = std::min(mcq_top, p.y);
            mcq_left = std::min(mcq_left, p.x);
            mcq_right = std::max(mcq_right, p.x);
        }
        float mcq_width = mcq_right - mcq_left;
        float mcq_cx = mcq_quad->cx;
        float tol = std::max(5.f, (float)h * 0.03f);

        std::vector<Quad*> header_candidates;
        const Quad* mcq_ptr = mcq_quad;
        for (auto& q : quads) {
            Quad* q_ptr = &q;
            if (q_ptr == mcq_ptr) continue;
            if (is_contained(q, *mcq_quad, 0.55f)) continue;
            float qbottom = -1e9f;
            for (const auto& p : q.pts) qbottom = std::max(qbottom, p.y);
            if (qbottom < mcq_top + tol) {
                header_candidates.push_back(q_ptr);
            }
        }

        std::vector<Quad*> scored;
        for (auto* q : header_candidates) {
            q->_geom_score = quad_geometry_score(*q);
            scored.push_back(q);
        }

        std::sort(scored.begin(), scored.end(),
            [](Quad* a, Quad* b) {
                if (a->_geom_score != b->_geom_score) return a->_geom_score > b->_geom_score;
                return a->area > b->area;
            });

        auto choose_best_side = [&](bool left_side) -> Quad* {
            std::vector<Quad*> side;
            for (auto* q : scored) {
                if ((left_side && q->cx < mcq_cx) || (!left_side && q->cx >= mcq_cx)) {
                    side.push_back(q);
                }
            }
            if (side.empty()) return nullptr;
            return side[0];
        };

        Quad* id_candidate = choose_best_side(true);
        Quad* version_candidate = choose_best_side(false);

        if (id_candidate && version_candidate) {
            float id_area = (id_candidate->cx * 0.f) + 1.f;
            float ver_area = (version_candidate->cx * 0.f) + 1.f;
            for (const auto& p : id_candidate->pts) {
                float x = p.x, y = p.y;
                id_area = std::max(id_area, x);
            }
            for (const auto& p : version_candidate->pts) {
                float x = p.x, y = p.y;
                ver_area = std::max(ver_area, x);
            }
            // actual area is computed below with bbox, so keep a safe placeholder here
            auto box_area = [](const Quad& q) {
                float xmin = 1e9f, ymin = 1e9f, xmax = -1e9f, ymax = -1e9f;
                for (const auto& p : q.pts) { xmin = std::min(xmin, p.x); ymin = std::min(ymin, p.y); xmax = std::max(xmax, p.x); ymax = std::max(ymax, p.y); }
                return (xmax - xmin) * (ymax - ymin);
            };

            float id_area_real = box_area(*id_candidate);
            float ver_area_real = box_area(*version_candidate);
            float area_ratio = std::min(id_area_real, ver_area_real) / std::max(id_area_real, ver_area_real);

            if (area_ratio < 0.40f) {
                std::vector<Quad*> all_above;
                for (auto& q : quads) {
                    if (&q == mcq_quad) continue;
                    if (is_contained(q, *mcq_quad, 0.55f)) continue;
                    float qbottom = -1e9f;
                    for (const auto& p : q.pts) qbottom = std::max(qbottom, p.y);
                    if (qbottom < mcq_top + tol) all_above.push_back(&q);
                }
                Quad* best_match = nullptr;
                float best_ratio = 0.f;
                for (auto* q : all_above) {
                    bool correct_side = (id_candidate->cx < mcq_cx) ? (q->cx >= mcq_cx) : (q->cx < mcq_cx);
                    if (!correct_side) continue;
                    float ratio = std::min(box_area(*q), std::max(id_area_real, ver_area_real)) /
                        std::max(box_area(*q), std::max(id_area_real, ver_area_real));
                    if (ratio > best_ratio) { best_ratio = ratio; best_match = q; }
                }
                if (best_match && best_ratio >= 0.40f) {
                    if (id_candidate->cx < mcq_cx) {
                        version_candidate = best_match;
                    } else {
                        id_candidate = best_match;
                    }
                }
            }

            float horiz_sep = std::abs(id_candidate->cx - version_candidate->cx);
            float min_sep = std::max(20.f, mcq_width * 0.15f);
            if (horiz_sep < min_sep) {
                id_candidate = nullptr;
                version_candidate = nullptr;
            } else {
                bool id_above = id_candidate->cy < mcq_top + tol;
                bool ver_above = version_candidate->cy < mcq_top + tol;
                if (!id_above || !ver_above) {
                    id_candidate = nullptr;
                    version_candidate = nullptr;
                } else if (is_contained(*id_candidate, *mcq_quad, 0.55f) || is_contained(*version_candidate, *mcq_quad, 0.55f)) {
                    id_candidate = nullptr;
                    version_candidate = nullptr;
                }
            }
        }

        if (id_candidate == nullptr || version_candidate == nullptr) {
            std::vector<Quad*> non_contained;
            for (auto& q : quads) {
                if (&q == mcq_quad) continue;
                if (!is_contained(q, *mcq_quad, 0.55f)) non_contained.push_back(&q);
            }
            std::sort(non_contained.begin(), non_contained.end(),
                [](Quad* a, Quad* b) { return quad_geometry_score(*a) > quad_geometry_score(*b); });
            if (non_contained.size() >= 2) {
                std::sort(non_contained.begin(), non_contained.end(),
                    [](Quad* a, Quad* b) { return a->cx < b->cx; });
                id_candidate = non_contained[0];
                version_candidate = non_contained[1];
            }
        }

        if (id_candidate && version_candidate) {
            if (id_candidate->cx > version_candidate->cx) std::swap(id_candidate, version_candidate);
            result.id_quad = *id_candidate;
            result.version_quad = *version_candidate;
        }
    }
    
    // Set final status
    if (result.confidence_score >= 75 && result.errors.empty()) {
        result.status = "PROCEED";
    } else {
        result.status = "RETAKE";
        if (result.errors.empty()) {
            result.errors.push_back("LOW_CONFIDENCE");
        }
    }
    
    // Convert errors to human-readable reasons
    for (const auto& err : result.errors) {
        if (err == "MISSING_MCQ") result.retake_reasons.push_back("Wider capture needed");
        else if (err == "MCQ_NOT_FOUND") result.retake_reasons.push_back("Straighten page");
        else if (err == "UNDEREXPOSED") result.retake_reasons.push_back("Increase lighting");
        else result.retake_reasons.push_back(err);
    }
    
    return result;
}

// ─────────────────────────────────────────────────────────────────────────────
// PHASE 3: Perspective Warping
// ─────────────────────────────────────────────────────────────────────────────

cv::Mat warp_quad(const cv::Mat& img, const std::vector<cv::Point2f>& pts4) {
    auto rect = order_points(pts4);
    auto tl = rect[0], tr = rect[1], br = rect[2], bl = rect[3];
    
    float wa = std::sqrt((br.x - bl.x) * (br.x - bl.x) + (br.y - bl.y) * (br.y - bl.y));
    float wb = std::sqrt((tr.x - tl.x) * (tr.x - tl.x) + (tr.y - tl.y) * (tr.y - tl.y));
    int maxW = std::max(2, (int)std::max(wa, wb));
    
    float ha = std::sqrt((tr.x - br.x) * (tr.x - br.x) + (tr.y - br.y) * (tr.y - br.y));
    float hb = std::sqrt((tl.x - bl.x) * (tl.x - bl.x) + (tl.y - bl.y) * (tl.y - bl.y));
    int maxH = std::max(2, (int)std::max(ha, hb));
    
    std::vector<cv::Point2f> src = rect;
    std::vector<cv::Point2f> dst = {
        {0.f, 0.f}, {(float)(maxW - 1), 0.f},
        {(float)(maxW - 1), (float)(maxH - 1)}, {0.f, (float)(maxH - 1)}
    };
    
    cv::Mat M = cv::getPerspectiveTransform(src, dst);
    cv::Mat warped;
    cv::warpPerspective(img, warped, M, {maxW, maxH}, cv::INTER_CUBIC);
    return warped;
}

Phase3Result phase3_perspective_warp(
    const cv::Mat& img,
    const Phase2Result& phase2,
    const std::string& image_path,
    const std::string& output_dir)
{
    Phase3Result result;
    
    std::cout << " -> [PHASE 3] Warping frames to a flat 2D perspective..." << std::endl;
    
    std::string base_name = get_base_name(image_path);
    
    if (phase2.id_quad) {
        result.id_mat = warp_quad(img, phase2.id_quad->pts);
        result.id_path = output_dir + "/" + base_name + "_phase3_id.jpg";
        cv::imwrite(result.id_path, *result.id_mat);
    }
    
    if (phase2.version_quad) {
        result.version_mat = warp_quad(img, phase2.version_quad->pts);
        result.version_path = output_dir + "/" + base_name + "_phase3_version.jpg";
        cv::imwrite(result.version_path, *result.version_mat);
    }
    
    if (phase2.mcq_quad) {
        result.mcq_mat = warp_quad(img, phase2.mcq_quad->pts);
        result.mcq_path = output_dir + "/" + base_name + "_phase3_mcq.jpg";
        cv::imwrite(result.mcq_path, *result.mcq_mat);
    }
    
    return result;
}

// ─────────────────────────────────────────────────────────────────────────────
// PHASE 4: YOLO Letterbox Preparation
// ─────────────────────────────────────────────────────────────────────────────

static cv::Mat letterbox_for_yolo(const cv::Mat& img, int target_size = 416) {
    cv::Mat gray;
    cv::cvtColor(img, gray, cv::COLOR_BGR2GRAY);
    cv::Mat img_bgr;
    cv::cvtColor(gray, img_bgr, cv::COLOR_GRAY2BGR);
    
    int h = img_bgr.rows, w = img_bgr.cols;
    double scale = (double)target_size / std::max(h, w);
    int new_w = (int)(w * scale);
    int new_h = (int)(h * scale);
    
    cv::Mat resized;
    cv::resize(img_bgr, resized, {new_w, new_h}, 0, 0, cv::INTER_AREA);
    
    cv::Mat canvas(target_size, target_size, CV_8UC3, cv::Scalar(128, 128, 128));
    int x_offset = (target_size - new_w) / 2;
    int y_offset = (target_size - new_h) / 2;
    resized.copyTo(canvas(cv::Rect(x_offset, y_offset, new_w, new_h)));
    
    return canvas;
}

Phase4Result phase4_yolo_letterbox(
    const Phase3Result& phase3,
    const std::string& image_path,
    const std::string& output_dir)
{
    Phase4Result result;
    
    std::cout << " -> [PHASE 4] Preparing clean grayscale frames for YOLO..." << std::endl;
    
    std::string base_name = get_base_name(image_path);
    
    if (phase3.id_mat) {
        result.id_yolo_mat = letterbox_for_yolo(*phase3.id_mat, 416);
        result.id_yolo_path = output_dir + "/" + base_name + "_phase4_id_YOLO.jpg";
        cv::imwrite(result.id_yolo_path, *result.id_yolo_mat);
    }
    
    if (phase3.version_mat) {
        result.version_yolo_mat = letterbox_for_yolo(*phase3.version_mat, 416);
        result.version_yolo_path = output_dir + "/" + base_name + "_phase4_version_YOLO.jpg";
        cv::imwrite(result.version_yolo_path, *result.version_yolo_mat);
    }
    
    if (phase3.mcq_mat) {
        result.mcq_yolo_mat = letterbox_for_yolo(*phase3.mcq_mat, 416);
        result.mcq_yolo_path = output_dir + "/" + base_name + "_phase4_mcq_YOLO.jpg";
        cv::imwrite(result.mcq_yolo_path, *result.mcq_yolo_mat);
    }
    
    return result;
}

// ─────────────────────────────────────────────────────────────────────────────
// MCQ Grid Splitting: Split MCQ into 6 sub-regions
// ─────────────────────────────────────────────────────────────────────────────

struct DividerLines { int x1, x2, y1; };

static DividerLines find_mcq_divider_lines(const cv::Mat& gray) {
    int h = gray.rows, w = gray.cols;
    
    // Check brightness: if too dark, apply CLAHE to enhance contrast for divider detection
    double mean_brightness = cv::mean(gray)[0];
    cv::Mat work_gray = gray.clone();
    
    if (mean_brightness < 100.0) {
        // Apply CLAHE (Contrast Limited Adaptive Histogram Equalization) for dark images
        cv::Ptr<cv::CLAHE> clahe = cv::createCLAHE(2.0, cv::Size(8, 8));
        clahe->apply(gray, work_gray);
    }
    
    // Use horizontal band (8%..95%) to detect vertical dividers
    cv::Mat region = work_gray(cv::Range((int)(h * 0.08), (int)(h * 0.95)), cv::Range::all());
    cv::Mat col_mean_f;
    cv::reduce(region, col_mean_f, 0, cv::REDUCE_AVG, CV_32F);
    cv::Mat col_dark = 255.f - col_mean_f;
    
    auto argmax_in_band = [&](int lo, int hi) -> int {
        lo = std::max(0, lo); hi = std::min(w - 1, hi);
        float maxVal = -1.f;
        int maxIdx = lo;
        for (int x = lo; x <= hi; ++x) {
            float v = col_dark.at<float>(0, x);
            if (v > maxVal) { maxVal = v; maxIdx = x; }
        }
        return maxIdx;
    };
    
    int x1 = argmax_in_band((int)(w * 0.25), (int)(w * 0.48));
    int x2 = argmax_in_band((int)(w * 0.55), (int)(w * 0.82));
    
    // Horizontal divider: find darkest row in middle vertical band
    cv::Mat hstrip = work_gray(cv::Range::all(), cv::Range((int)(w * 0.04), (int)(w * 0.96)));
    cv::Mat row_mean_f;
    cv::reduce(hstrip, row_mean_f, 1, cv::REDUCE_AVG, CV_32F);
    cv::Mat row_dark = 255.f - row_mean_f;
    
    int lo3 = (int)(h * 0.30), hi3 = (int)(h * 0.75);
    int y1 = lo3;
    float best = -1.f;
    for (int y = lo3; y <= hi3; ++y) {
        float v = row_dark.at<float>(y, 0);
        if (v > best) { best = v; y1 = y; }
    }
    
    return {x1, x2, y1};
}

MCQRegions split_mcq_into_regions(
    const cv::Mat& img,
    const Phase2Result& phase2,
    const std::string& image_path,
    const std::string& output_dir)
{
    MCQRegions result;
    
    if (!phase2.mcq_quad) return result;
    
    // Warp MCQ quad
    cv::Mat warped = warp_quad(img, phase2.mcq_quad->pts);
    if (warped.empty() || warped.rows < 10 || warped.cols < 10) return result;
    
    // Convert to grayscale and find dividers
    cv::Mat gray;
    cv::cvtColor(warped, gray, cv::COLOR_BGR2GRAY);
    auto [dx1, dx2, dy1] = find_mcq_divider_lines(gray);
    if (dx1 > dx2) std::swap(dx1, dx2);
    
    int wh = warped.rows, ww = warped.cols;
    
    // Define 6 regions in row-major, then reorder to column-major
    struct Cell { std::string name; int xa, xb, ya, yb; };
    std::vector<Cell> cells = {
        {"top_left",     0,   dx1, 0,   dy1},
        {"top_mid",      dx1, dx2, 0,   dy1},
        {"top_right",    dx2, ww,  0,   dy1},
        {"bottom_left",  0,   dx1, dy1, wh},
        {"bottom_mid",   dx1, dx2, dy1, wh},
        {"bottom_right", dx2, ww,  dy1, wh},
    };
    
    std::string base_name = get_base_name(image_path);
    
    std::string mcq_dir = output_dir + "/" + base_name + "_mcq_regions";
    fs::create_directories(mcq_dir);
    
    // Column-major fill order
    const std::vector<std::string> fill_order = {
        "top_left", "bottom_left",
        "top_mid", "bottom_mid",
        "top_right", "bottom_right"
    };
    
    std::map<std::string, cv::Mat> cell_mats;
    for (auto& cell : cells) {
        int ya = std::max(0, cell.ya), yb = std::min(wh, cell.yb);
        int xa = std::max(0, cell.xa), xb = std::min(ww, cell.xb);
        if (ya >= yb || xa >= xb) continue;
        cell_mats[cell.name] = warped(cv::Range(ya, yb), cv::Range(xa, xb)).clone();
    }
    
    int region_idx = 1;
    for (const auto& region_name : fill_order) {
        auto it = cell_mats.find(region_name);
        if (it == cell_mats.end() || it->second.empty()) continue;
        
        std::string rpath = mcq_dir + "/" + base_name + "_mcq_" + std::to_string(region_idx) + "_" + region_name + ".jpg";
        cv::imwrite(rpath, it->second);
        
        result.regions.push_back(it->second);
        result.region_names.push_back(region_name);
        result.region_paths.push_back(rpath);
        ++region_idx;
    }
    
    return result;
}

// ─────────────────────────────────────────────────────────────────────────────
// MCQ Region Validation: Check if split regions have correct question distribution
// ─────────────────────────────────────────────────────────────────────────────

MCQValidationResult validate_mcq_regions(const MCQRegions& regions, int expected_total_questions) {
    // Question-count comparison against expected_total_questions was removed in
    // favor of Phase 5's real model-based per-question check (see main.cpp) - this
    // function now only confirms the split geometry itself is sound.
    (void)expected_total_questions;

    MCQValidationResult result;
    result.is_valid = true;
    result.error_message = "";
    result.detected_questions_per_region.clear();

    if (regions.regions.empty()) {
        result.is_valid = false;
        result.error_message = "MCQ_SPLIT_GEOMETRY_FAILED: no MCQ regions were produced by the divider-line split";
        return result;
    }

    // ── Step 1: confirm the OpenCV perpendicular-divider split is structurally sound ──
    // Everything below this point (question-count comparisons) is only meaningful if
    // the divider lines actually carved the panel into a proper 3-column x 2-row grid
    // with reasonably proportioned cells. If a divider collapsed to an edge or grabbed
    // the wrong dark line, the resulting regions are garbage and any question-count
    // mismatch we'd compute from them is a symptom of the bad split - not a real
    // config-vs-detected mismatch. Reporting that as QUESTIONS_NUMBER_MISMATCH would be
    // misleading, so we check split geometry first and bail out early if it's broken.
    if (regions.regions.size() != 6) {
        result.is_valid = false;
        std::stringstream ss;
        ss << "MCQ_SPLIT_GEOMETRY_FAILED: divider lines produced " << regions.regions.size()
           << " of 6 expected regions (a divider likely collapsed to an edge); ";
        result.error_message = ss.str();
        return result;
    }

    std::map<std::string, cv::Size> sizes;
    for (size_t i = 0; i < regions.regions.size(); ++i) {
        sizes[regions.region_names[i]] = regions.regions[i].size();
    }

    auto get_w = [&](const std::string& a, const std::string& b) -> int {
        auto it = sizes.find(a);
        if (it != sizes.end()) return it->second.width;
        it = sizes.find(b);
        if (it != sizes.end()) return it->second.width;
        return 0;
    };
    auto get_h = [&](const std::string& a, const std::string& b) -> int {
        auto it = sizes.find(a);
        if (it != sizes.end()) return it->second.height;
        it = sizes.find(b);
        if (it != sizes.end()) return it->second.height;
        return 0;
    };

    int w_left  = get_w("top_left",  "bottom_left");
    int w_mid   = get_w("top_mid",   "bottom_mid");
    int w_right = get_w("top_right", "bottom_right");
    int total_w = w_left + w_mid + w_right;

    int h_top    = get_h("top_left",    "top_mid");
    int h_bottom = get_h("bottom_left", "bottom_mid");
    int total_h  = h_top + h_bottom;

    bool geometry_sound = total_w > 0 && total_h > 0;
    std::stringstream geom_issues;
    if (geometry_sound) {
        // A genuine 3-column layout is roughly ~33% per column, a 2-row layout
        // roughly ~50% per row. Generous slack is allowed, but a column/row that
        // collapsed to a sliver (or ballooned to nearly the whole panel) means the
        // divider-line detection grabbed the wrong pixel, not a real content issue.
        const double MIN_COL_FRAC = 0.12, MAX_COL_FRAC = 0.60;
        const double MIN_ROW_FRAC = 0.25, MAX_ROW_FRAC = 0.75;

        double f_left  = (double)w_left  / total_w;
        double f_mid   = (double)w_mid   / total_w;
        double f_right = (double)w_right / total_w;
        double f_top   = (double)h_top   / total_h;
        double f_bot   = (double)h_bottom / total_h;

        if (f_left < MIN_COL_FRAC || f_left > MAX_COL_FRAC) {
            geometry_sound = false;
            geom_issues << "left column is " << (int)(f_left * 100) << "% of panel width; ";
        }
        if (f_mid < MIN_COL_FRAC || f_mid > MAX_COL_FRAC) {
            geometry_sound = false;
            geom_issues << "middle column is " << (int)(f_mid * 100) << "% of panel width; ";
        }
        if (f_right < MIN_COL_FRAC || f_right > MAX_COL_FRAC) {
            geometry_sound = false;
            geom_issues << "right column is " << (int)(f_right * 100) << "% of panel width; ";
        }
        if (f_top < MIN_ROW_FRAC || f_top > MAX_ROW_FRAC) {
            geometry_sound = false;
            geom_issues << "top row is " << (int)(f_top * 100) << "% of panel height; ";
        }
        if (f_bot < MIN_ROW_FRAC || f_bot > MAX_ROW_FRAC) {
            geometry_sound = false;
            geom_issues << "bottom row is " << (int)(f_bot * 100) << "% of panel height; ";
        }
    }

    if (!geometry_sound) {
        result.is_valid = false;
        result.error_message = "MCQ_SPLIT_GEOMETRY_FAILED: divider lines produced an unbalanced grid (" +
                                geom_issues.str() + "); this is a split problem, not a question-count mismatch";
        return result;
    }

    // Split geometry is confirmed sound. Question-count validation used to run a
    // second heuristic pass here (HoughCircles per region, divided by 4 to guess
    // a count) - that duplicated, less reliably, what the trained bubble-detection
    // model already does in Phase 5 on these exact region crops. Phase 5's
    // per-cell results are authoritative for question-count mismatches (see
    // main.cpp, which checks for QUESTIONS_NUMBER_MISMATCH states in the real
    // phase5 output) - so this function's job ends at confirming the split itself
    // is structurally sound.
    return result;
}

// ─────────────────────────────────────────────────────────────────────────────
// Annotation Helpers for Debugging
// ─────────────────────────────────────────────────────────────────────────────

void draw_and_save_quads_annotated(
    const cv::Mat& img,
    const Phase2Result& phase2,
    const std::string& image_path,
    const std::string& output_dir)
{
    cv::Mat vis = img.clone();
    int h = img.rows, w = img.cols;
    int line_thickness = std::max(3, w / 300);
    
    // Draw ID quad (blue)
    if (phase2.id_quad) {
        cv::Scalar color(255, 0, 0);  // Blue
        std::vector<cv::Point> id_pts;
        for (const auto& p : phase2.id_quad->pts) {
            id_pts.push_back(cv::Point((int)p.x, (int)p.y));
        }
        if (id_pts.size() == 4) {
            cv::polylines(vis, std::vector<std::vector<cv::Point>>{id_pts}, true, color, line_thickness);
            cv::putText(vis, "ID", cv::Point((int)phase2.id_quad->cx - 30, (int)phase2.id_quad->cy),
                cv::FONT_HERSHEY_SIMPLEX, 1.0, color, line_thickness);
        }
    }
    
    // Draw Version quad (green)
    if (phase2.version_quad) {
        cv::Scalar color(0, 255, 0);  // Green
        std::vector<cv::Point> ver_pts;
        for (const auto& p : phase2.version_quad->pts) {
            ver_pts.push_back(cv::Point((int)p.x, (int)p.y));
        }
        if (ver_pts.size() == 4) {
            cv::polylines(vis, std::vector<std::vector<cv::Point>>{ver_pts}, true, color, line_thickness);
            cv::putText(vis, "VERSION", cv::Point((int)phase2.version_quad->cx - 50, (int)phase2.version_quad->cy),
                cv::FONT_HERSHEY_SIMPLEX, 1.0, color, line_thickness);
        }
    }
    
    // Draw MCQ quad (red)
    if (phase2.mcq_quad) {
        cv::Scalar color(0, 0, 255);  // Red
        std::vector<cv::Point> mcq_pts;
        for (const auto& p : phase2.mcq_quad->pts) {
            mcq_pts.push_back(cv::Point((int)p.x, (int)p.y));
        }
        if (mcq_pts.size() == 4) {
            cv::polylines(vis, std::vector<std::vector<cv::Point>>{mcq_pts}, true, color, line_thickness + 1);
            cv::putText(vis, "MCQ", cv::Point((int)phase2.mcq_quad->cx - 50, (int)phase2.mcq_quad->cy),
                cv::FONT_HERSHEY_SIMPLEX, 1.2, color, line_thickness + 1);
        }
    }
    
    // Add status text
    cv::Scalar status_color = (phase2.status == "PROCEED") ? cv::Scalar(0, 255, 0) : cv::Scalar(0, 0, 255);
    std::string status_text = phase2.status + " (Conf: " + std::to_string(phase2.confidence_score) + "%)";
    cv::putText(vis, status_text, cv::Point(20, h - 20),
        cv::FONT_HERSHEY_SIMPLEX, 0.8, status_color, 2);
    
    // Save annotated image
    std::string base_name = get_base_name(image_path);
    std::string ann_path = output_dir + "/" + base_name + "_phase2_annotated.jpg";
    cv::Mat vis_scaled = vis.clone();
    if (w > 2000) {
        cv::resize(vis_scaled, vis_scaled, cv::Size(vis_scaled.cols / 2, vis_scaled.rows / 2));
    }
    cv::imwrite(ann_path, vis_scaled);
    std::cout << "[+] Saved annotated Phase 2 output: " << ann_path << std::endl;
}

} // namespace shamel_phases