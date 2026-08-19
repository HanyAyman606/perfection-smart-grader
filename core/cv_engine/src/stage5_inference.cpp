// STAGE 5: Inference & Metadata Export (Standalone)
//
// Splits dense MCQ panels into columns before running the ONNX model.
// See stage5_inference.py's module docstring for the full rationale
// (divider detection on the clean Stage 3 image but cropping/inferring on
// the Stage 4 image, self-calibrated overlap margin, center-proximity
// dedup) -- this is a straight behavioural port of that logic.
//
// C++ port of stage5_inference.py, using the ONNX Runtime C++ API in
// place of onnxruntime-python.
#include <opencv2/opencv.hpp>

#include <algorithm>
#include <cmath>
#include <fstream>
#include <iostream>
#include <numeric>
#include <optional>
#include <string>
#include <vector>

#include <nlohmann/json.hpp>
#include <onnxruntime_cxx_api.h>

#include "common.hpp"
#include "exam_config.hpp"

using json = nlohmann::json;
namespace fs = std::filesystem;

namespace {

fs::path WORK_DIR, STAGE3_DIR, STAGE4_DIR, OUT_DIR, MODEL_PATH;

constexpr int TOTAL_QUESTIONS = 35;

// Only split MCQ panels into columns when there are enough questions that
// a single dense crop risks confusing the model. Below this, run the
// whole panel through as one image -- no split needed, nothing to gain.
constexpr int COLUMN_SPLIT_MIN_QUESTIONS = 18;

// Fallback margin/threshold used ONLY if we can't measure real bubble
// size for this image yet (e.g. the very first pass before any
// detections exist). Kept conservative and small on purpose.
constexpr int FALLBACK_CUT_MARGIN = 12;
constexpr int FALLBACK_CENTER_DIST_THRESH = 15;

// (x1, y1, x2, y2, cls_id, score)
struct Detection {
    double x1, y1, x2, y2;
    int cls_id;
    double score;
};

// =======================================================================
// PURE INFERENCE ENGINE
// =======================================================================
class OnnxDetector {
public:
#ifdef _WIN32
    static std::wstring to_wstring(const std::string& str) {
        return std::wstring(str.begin(), str.end());
    }
#endif

    explicit OnnxDetector(const std::string& model_path)
        : env_(ORT_LOGGING_LEVEL_WARNING, "exam_scanner"),
#ifdef _WIN32
          session_(env_, to_wstring(model_path).c_str(), Ort::SessionOptions{nullptr}) {
#else
          session_(env_, model_path.c_str(), Ort::SessionOptions{nullptr}) {
#endif
        Ort::AllocatorWithDefaultOptions allocator;
        auto name_alloc = session_.GetInputNameAllocated(0, allocator);
        input_name_ = name_alloc.get();

        Ort::TypeInfo type_info = session_.GetInputTypeInfo(0);
        auto tensor_info = type_info.GetTensorTypeAndShapeInfo();
        std::vector<int64_t> shape = tensor_info.GetShape();
        int h = (shape.size() > 2 && shape[2] > 0) ? static_cast<int>(shape[2]) : 640;
        int w = (shape.size() > 3 && shape[3] > 0) ? static_cast<int>(shape[3]) : 640;
        input_h_ = h;
        input_w_ = w;

        Ort::AllocatorWithDefaultOptions alloc2;
        auto out_name_alloc = session_.GetOutputNameAllocated(0, alloc2);
        output_name_ = out_name_alloc.get();
    }

    int input_h() const { return input_h_; }
    int input_w() const { return input_w_; }

    static cv::Mat letterbox(const cv::Mat& img, int new_h, int new_w, double& r, int& left, int& top,
                              cv::Scalar color = cv::Scalar(114, 114, 114)) {
        int h = img.rows, w = img.cols;
        r = std::min(static_cast<double>(new_h) / h, static_cast<double>(new_w) / w);
        int uw = static_cast<int>(std::round(w * r));
        int uh = static_cast<int>(std::round(h * r));
        cv::Mat resized;
        cv::resize(img, resized, cv::Size(uw, uh), 0, 0, cv::INTER_LINEAR);
        double dw = (new_w - uw) / 2.0, dh = (new_h - uh) / 2.0;
        int top_ = static_cast<int>(std::round(dh - 0.1));
        int bottom_ = static_cast<int>(std::round(dh + 0.1));
        int left_ = static_cast<int>(std::round(dw - 0.1));
        int right_ = static_cast<int>(std::round(dw + 0.1));
        cv::Mat padded;
        cv::copyMakeBorder(resized, padded, top_, bottom_, left_, right_, cv::BORDER_CONSTANT, color);
        left = left_;
        top = top_;
        return padded;
    }

    std::vector<Detection> infer(const cv::Mat& bgr_img, double conf_thres = 0.25, double iou_thres = 0.45,
                                  std::optional<int> num_classes = std::nullopt) {
        int h0 = bgr_img.rows, w0 = bgr_img.cols;
        double r;
        int padx, pady;
        cv::Mat img = letterbox(bgr_img, input_h_, input_w_, r, padx, pady);

        // BGR -> RGB, HWC -> CHW, normalize to [0,1], NCHW float32 blob.
        std::vector<float> blob(static_cast<size_t>(3) * input_h_ * input_w_);
        int plane = input_h_ * input_w_;
        for (int y = 0; y < input_h_; ++y) {
            const cv::Vec3b* row = img.ptr<cv::Vec3b>(y);
            for (int x = 0; x < input_w_; ++x) {
                cv::Vec3b px = row[x];  // BGR
                blob[0 * plane + y * input_w_ + x] = px[2] / 255.0f;  // R
                blob[1 * plane + y * input_w_ + x] = px[1] / 255.0f;  // G
                blob[2 * plane + y * input_w_ + x] = px[0] / 255.0f;  // B
            }
        }

        std::array<int64_t, 4> input_shape{1, 3, input_h_, input_w_};
        Ort::MemoryInfo mem_info = Ort::MemoryInfo::CreateCpu(OrtArenaAllocator, OrtMemTypeDefault);
        Ort::Value input_tensor = Ort::Value::CreateTensor<float>(mem_info, blob.data(), blob.size(),
                                                                    input_shape.data(), input_shape.size());

        const char* input_names[] = {input_name_.c_str()};
        const char* output_names[] = {output_name_.c_str()};
        auto outputs = session_.Run(Ort::RunOptions{nullptr}, input_names, &input_tensor, 1, output_names, 1);

        Ort::Value& out_tensor = outputs.front();
        auto out_shape = out_tensor.GetTensorTypeAndShapeInfo().GetShape();
        const float* out_data = out_tensor.GetTensorData<float>();
        size_t total = out_tensor.GetTensorTypeAndShapeInfo().GetElementCount();

        // np.squeeze then guarantee 2D: drop leading dims of size 1.
        std::vector<int64_t> dims;
        for (auto d : out_shape) if (d != 1) dims.push_back(d);
        if (dims.empty()) dims.push_back(1);

        int rows, cols;
        if (dims.size() == 1) {
            rows = 1;
            cols = static_cast<int>(dims[0]);
        } else if (dims.size() == 2) {
            rows = static_cast<int>(dims[0]);
            cols = static_cast<int>(dims[1]);
        } else {
            // Unexpected rank after squeeze; treat as flattened rows of
            // the last dimension (best-effort, mirrors numpy's leniency).
            cols = static_cast<int>(dims.back());
            rows = static_cast<int>(total / std::max(1, cols));
        }

        if (rows == 0) return {};

        std::vector<float> mat(out_data, out_data + total);

        if (cols == 6 || cols == 7) {
            if (rows < 1000) {
                return decode_end2end(mat, rows, cols, r, padx, pady, w0, h0, conf_thres);
            }
        }
        int nc = num_classes.value_or(cols - 5);
        return decode_raw_grid(mat, rows, cols, r, padx, pady, w0, h0, conf_thres, iou_thres, nc);
    }

private:
    std::vector<Detection> decode_end2end(const std::vector<float>& out, int rows, int cols, double r, int padx,
                                           int pady, int w0, int h0, double conf_thres) {
        std::vector<Detection> results;
        for (int i = 0; i < rows; ++i) {
            const float* row = &out[static_cast<size_t>(i) * cols];
            double x1, y1, x2, y2, score;
            int cls_id;
            if (cols == 7) {
                x1 = row[1]; y1 = row[2]; x2 = row[3]; y2 = row[4];
                cls_id = static_cast<int>(std::lround(row[5]));
                score = row[6];
            } else {
                x1 = row[0]; y1 = row[1]; x2 = row[2]; y2 = row[3];
                score = row[4];
                cls_id = static_cast<int>(std::lround(row[5]));
            }
            if (score < conf_thres) continue;
            double bx1 = (x1 - padx) / r, by1 = (y1 - pady) / r;
            double bx2 = (x2 - padx) / r, by2 = (y2 - pady) / r;
            bx1 = std::clamp(bx1, 0.0, static_cast<double>(w0 - 1));
            bx2 = std::clamp(bx2, 0.0, static_cast<double>(w0 - 1));
            by1 = std::clamp(by1, 0.0, static_cast<double>(h0 - 1));
            by2 = std::clamp(by2, 0.0, static_cast<double>(h0 - 1));
            if (bx2 - bx1 < 2 || by2 - by1 < 2) continue;
            results.push_back({bx1, by1, bx2, by2, cls_id, score});
        }
        return results;
    }

    std::vector<Detection> decode_raw_grid(const std::vector<float>& out, int rows, int cols, double r, int padx,
                                            int pady, int w0, int h0, double conf_thres, double iou_thres, int nc) {
        std::vector<Detection> results;
        std::vector<int> keep_idx;
        for (int i = 0; i < rows; ++i) {
            if (out[static_cast<size_t>(i) * cols + 4] > conf_thres) keep_idx.push_back(i);
        }
        if (keep_idx.empty()) return {};

        struct Cand { double cx, cy, w, h, score; int cls_id; };
        std::vector<Cand> cands;
        for (int i : keep_idx) {
            const float* row = &out[static_cast<size_t>(i) * cols];
            double obj = row[4];
            int best_cls = 0;
            double best_conf = -1;
            for (int c = 0; c < nc; ++c) {
                double v = row[5 + c];
                if (v > best_conf) { best_conf = v; best_cls = c; }
            }
            double score = obj * best_conf;
            if (score > conf_thres) {
                cands.push_back({row[0], row[1], row[2], row[3], score, best_cls});
            }
        }
        if (cands.empty()) return {};

        int max_wh = std::max(input_h_, input_w_);
        std::vector<cv::Rect2d> boxes_for_nms;
        std::vector<float> scores;
        for (auto& c : cands) {
            double x1 = c.cx - c.w / 2.0, y1 = c.cy - c.h / 2.0;
            double offset = static_cast<double>(c.cls_id) * max_wh;
            boxes_for_nms.emplace_back(x1 + offset, y1 + offset, c.w, c.h);
            scores.push_back(static_cast<float>(c.score));
        }
        std::vector<int> idxs;
        cv::dnn::NMSBoxes(boxes_for_nms, scores, static_cast<float>(conf_thres), static_cast<float>(iou_thres),
                           idxs);

        for (int i : idxs) {
            const Cand& c = cands[i];
            double x1 = c.cx - c.w / 2.0, y1 = c.cy - c.h / 2.0;
            double bx1 = (x1 - padx) / r, by1 = (y1 - pady) / r;
            double bx2 = (x1 + c.w - padx) / r, by2 = (y1 + c.h - pady) / r;
            bx1 = std::clamp(bx1, 0.0, static_cast<double>(w0 - 1));
            bx2 = std::clamp(bx2, 0.0, static_cast<double>(w0 - 1));
            by1 = std::clamp(by1, 0.0, static_cast<double>(h0 - 1));
            by2 = std::clamp(by2, 0.0, static_cast<double>(h0 - 1));
            if (bx2 - bx1 < 2 || by2 - by1 < 2) continue;
            results.push_back({bx1, by1, bx2, by2, c.cls_id, c.score});
        }
        return results;
    }

    Ort::Env env_;
    Ort::Session session_;
    std::string input_name_, output_name_;
    int input_h_ = 640, input_w_ = 640;
};

// =======================================================================
// PIPELINE LOGIC
// =======================================================================
std::array<int, 3> get_mcq_column_math(int total_q) {
    int col1 = static_cast<int>(std::ceil(total_q / 3.0));
    int remaining = total_q - col1;
    int col2 = static_cast<int>(std::ceil(remaining / 2.0));
    int col3 = remaining - col2;
    return {col1, col2, col3};
}

// Detects the two printed vertical divider lines on a CLEAN (Stage 3
// oriented, not Stage 4 processed) image. See module docstring.
// Returns (x1, x2) in the coordinate space of `clean_bgr`.
std::pair<int, int> find_vertical_delimiters(const cv::Mat& clean_bgr, const std::string& name) {
    cv::Mat gray;
    cv::cvtColor(clean_bgr, gray, cv::COLOR_BGR2GRAY);
    int h = gray.rows, w = gray.cols;

    int roi_top = static_cast<int>(h * 0.1), roi_bottom = static_cast<int>(h * 0.9);
    cv::Mat roi = gray(cv::Range(roi_top, roi_bottom), cv::Range::all());
    int roi_h = roi.rows;

    cv::Mat thresh;
    cv::threshold(roi, thresh, 150, 255, cv::THRESH_BINARY_INV);

    cv::Mat kernel = cv::getStructuringElement(cv::MORPH_RECT, cv::Size(1, std::max(1, static_cast<int>(roi_h * 0.06))));
    cv::Mat vert_only;
    cv::morphologyEx(thresh, vert_only, cv::MORPH_OPEN, kernel);

    // col_scores[x] = sum over rows of vert_only column x
    std::vector<double> col_scores(w, 0.0);
    for (int y = 0; y < vert_only.rows; ++y) {
        const uchar* row = vert_only.ptr<uchar>(y);
        for (int x = 0; x < w; ++x) col_scores[x] += row[x];
    }

    int search_band = static_cast<int>(0.08 * w);
    int z1_center = w / 3, z2_center = 2 * w / 3;

    auto best_in_zone = [&](int center) {
        int lo = std::max(0, center - search_band), hi = std::min(w, center + search_band);
        int best_i = lo;
        double best_v = -1;
        for (int x = lo; x < hi; ++x) {
            if (col_scores[x] > best_v) { best_v = col_scores[x]; best_i = x; }
        }
        return best_i;
    };

    int x1 = best_in_zone(z1_center);
    int x2 = best_in_zone(z2_center);

    // --- VISUAL DEBUGGER OUTPUT (drawn on the clean stage3 image) ---
    cv::Mat debug_vis = clean_bgr.clone();
    cv::Mat overlay = debug_vis.clone();
    cv::rectangle(overlay, cv::Point(z1_center - search_band, 0), cv::Point(z1_center + search_band, h),
                  cv::Scalar(255, 200, 200), cv::FILLED);
    cv::rectangle(overlay, cv::Point(z2_center - search_band, 0), cv::Point(z2_center + search_band, h),
                  cv::Scalar(255, 200, 200), cv::FILLED);
    cv::addWeighted(overlay, 0.4, debug_vis, 0.6, 0, debug_vis);
    cv::line(debug_vis, cv::Point(x1, 0), cv::Point(x1, h), cv::Scalar(0, 0, 255), 3);
    cv::line(debug_vis, cv::Point(x2, 0), cv::Point(x2, h), cv::Scalar(0, 0, 255), 3);
    cv::putText(debug_vis, "CUT 1: " + std::to_string(x1), cv::Point(x1 + 10, 50), cv::FONT_HERSHEY_SIMPLEX, 0.8,
                cv::Scalar(0, 0, 255), 2);
    cv::putText(debug_vis, "CUT 2: " + std::to_string(x2), cv::Point(x2 + 10, 50), cv::FONT_HERSHEY_SIMPLEX, 0.8,
                cv::Scalar(0, 0, 255), 2);

    std::string out_name = common::replace_all(common::replace_all(name, ".jpg", "_debug_cuts.jpg"), ".jpeg",
                                                 "_debug_cuts.jpg");
    cv::imwrite((OUT_DIR / out_name).string(), debug_vis);

    return {x1, x2};
}

// Measures the median bubble width/height from a set of raw detections on
// THIS image. Returns nullopt if there aren't enough detections to trust
// a median (mirrors estimate_bubble_size()).
std::optional<double> estimate_bubble_size(const std::vector<Detection>& detections) {
    if (detections.size() < 5) return std::nullopt;
    std::vector<double> widths, heights;
    for (auto& d : detections) {
        widths.push_back(d.x2 - d.x1);
        heights.push_back(d.y2 - d.y1);
    }
    auto median = [](std::vector<double> v) {
        std::sort(v.begin(), v.end());
        size_t n = v.size();
        if (n % 2 == 1) return v[n / 2];
        return (v[n / 2 - 1] + v[n / 2]) / 2.0;
    };
    double med_w = median(widths), med_h = median(heights);
    return (med_w + med_h) / 2.0;
}

// Removes overlapping/adjacent duplicate boxes from stitched crops by
// center-proximity (mirrors deduplicate_boxes()).
std::vector<Detection> deduplicate_boxes(std::vector<Detection> boxes, double center_dist_thresh) {
    if (boxes.empty()) return {};
    std::sort(boxes.begin(), boxes.end(), [](const Detection& a, const Detection& b) { return a.score > b.score; });
    std::vector<Detection> keep;
    for (const auto& b : boxes) {
        if (b.x2 - b.x1 < 15 || b.y2 - b.y1 < 15) continue;
        double cx = (b.x1 + b.x2) / 2.0, cy = (b.y1 + b.y2) / 2.0;
        bool is_dup = false;
        for (const auto& k : keep) {
            double kcx = (k.x1 + k.x2) / 2.0, kcy = (k.y1 + k.y2) / 2.0;
            if (std::abs(cx - kcx) < center_dist_thresh && std::abs(cy - kcy) < center_dist_thresh) {
                is_dup = true;
                break;
            }
        }
        if (!is_dup) keep.push_back(b);
    }
    return keep;
}

std::optional<json> process_inference(OnnxDetector& detector, const fs::path& stage4_path,
                                       const std::string& panel_type, int total_q) {
    std::string name = stage4_path.filename().string();
    cv::Mat img = cv::imread(stage4_path.string());  // Stage 4 processed image -- used for actual inference
    if (img.empty()) return std::nullopt;

    int h4 = img.rows, w4 = img.cols;
    bool do_split = panel_type == "mcq" && total_q >= COLUMN_SPLIT_MIN_QUESTIONS;

    std::vector<Detection> final_detections;

    if (do_split) {
        std::string clean_name = common::replace_all(name, "_mcq_final.jpg", "_mcq_oriented.jpg");
        fs::path clean_path = STAGE3_DIR / clean_name;
        cv::Mat clean_img;
        bool has_clean = fs::exists(clean_path) && !(clean_img = cv::imread(clean_path.string())).empty();

        int x1_4, x2_4;
        if (has_clean) {
            int wc = clean_img.cols;
            auto [x1_c, x2_c] = find_vertical_delimiters(clean_img, clean_name);
            double scale_x = static_cast<double>(w4) / wc;
            x1_4 = static_cast<int>(std::round(x1_c * scale_x));
            x2_4 = static_cast<int>(std::round(x2_c * scale_x));
        } else {
            std::tie(x1_4, x2_4) = find_vertical_delimiters(img, name);
        }

        if (x1_4 > x2_4) std::swap(x1_4, x2_4);
        x1_4 = std::clamp(x1_4, 0, w4);
        x2_4 = std::clamp(x2_4, 0, w4);

        // --- PASS 1: unmargined split, purely to measure bubble size ---
        std::vector<std::pair<int, int>> probe_bounds = {{0, x1_4}, {x1_4, x2_4}, {x2_4, w4}};
        std::vector<Detection> probe_detections;
        for (auto [x_start, x_end] : probe_bounds) {
            if (x_end <= x_start) continue;
            cv::Mat crop = img(cv::Range::all(), cv::Range(x_start, x_end));
            for (auto d : detector.infer(crop)) {
                d.x1 += x_start; d.x2 += x_start;
                probe_detections.push_back(d);
            }
        }

        int cut_margin, center_dist_thresh;
        auto bubble_size = estimate_bubble_size(probe_detections);
        if (bubble_size) {
            cut_margin = std::max(6, static_cast<int>(std::round(*bubble_size * 0.3)));
            center_dist_thresh = std::max(8, static_cast<int>(std::round(*bubble_size * 0.4)));
        } else {
            cut_margin = FALLBACK_CUT_MARGIN;
            center_dist_thresh = FALLBACK_CENTER_DIST_THRESH;
        }

        // --- PASS 2: real split, with self-calibrated overlap margin ---
        std::vector<std::pair<int, int>> col_bounds = {
            {0, std::min(w4, x1_4 + cut_margin)},
            {std::max(0, x1_4 - cut_margin), std::min(w4, x2_4 + cut_margin)},
            {std::max(0, x2_4 - cut_margin), w4},
        };
        std::vector<Detection> raw_detections;
        for (auto [x_start, x_end] : col_bounds) {
            if (x_end <= x_start) continue;
            cv::Mat crop = img(cv::Range::all(), cv::Range(x_start, x_end));
            for (auto d : detector.infer(crop)) {
                d.x1 += x_start; d.x2 += x_start;
                raw_detections.push_back(d);
            }
        }

        final_detections = deduplicate_boxes(raw_detections, center_dist_thresh);
    } else {
        std::vector<Detection> raw_detections = detector.infer(img);
        // Even without a column split there can be near-duplicate boxes
        // from the model itself; use the fallback threshold here since
        // we didn't do a probe pass.
        final_detections = deduplicate_boxes(raw_detections, FALLBACK_CENTER_DIST_THRESH);
    }

    cv::Mat annotated_img = img.clone();
    std::string txt_name = common::replace_all(common::replace_all(name, ".jpg", ".txt"), ".jpeg", ".txt");
    std::ofstream f(OUT_DIR / txt_name);
    for (const auto& d : final_detections) {
        char line[128];
        std::snprintf(line, sizeof(line), "%d %.2f %.2f %.2f %.2f %.4f\n", d.cls_id, d.x1, d.y1, d.x2, d.y2, d.score);
        f << line;
        cv::Scalar color = d.cls_id == 0 ? cv::Scalar(255, 229, 0) : cv::Scalar(113, 61, 255);
        cv::rectangle(annotated_img, cv::Point(static_cast<int>(d.x1), static_cast<int>(d.y1)),
                      cv::Point(static_cast<int>(d.x2), static_cast<int>(d.y2)), color, 2);
        char score_buf[16];
        std::snprintf(score_buf, sizeof(score_buf), "%.2f", d.score);
        cv::putText(annotated_img, score_buf, cv::Point(static_cast<int>(d.x1), static_cast<int>(d.y1) - 5),
                    cv::FONT_HERSHEY_SIMPLEX, 0.4, color, 1);
    }

    cv::imwrite((OUT_DIR / name).string(), annotated_img);

    json r;
    r["file"] = name;
    r["panel_type"] = panel_type;
    r["column_split"] = do_split;
    r["total_detections"] = static_cast<int>(final_detections.size());
    return r;
}

}  // namespace

int main(int argc, char** argv) {
    fs::path here = argc > 1 ? fs::path(argv[1]) : fs::current_path();
    WORK_DIR = fs::absolute(here);
    STAGE3_DIR = WORK_DIR / "stage3";
    STAGE4_DIR = WORK_DIR / "stage4";
    OUT_DIR = WORK_DIR / "stage5";
    common::ensure_dir(OUT_DIR);

    fs::path config_file = WORK_DIR / "exam_config.json";
    if (!fs::exists(config_file)) {
        std::cerr << "Config not found at " << config_file << "\n";
        return 1;
    }
    
    auto cfg = examcfg::load_exam_config_from_file(config_file.string());

    MODEL_PATH = fs::path(cfg.model_path);
    if (!MODEL_PATH.is_absolute()) {
        MODEL_PATH = WORK_DIR / MODEL_PATH;
    }

    if (!fs::exists(MODEL_PATH)) {
        std::cerr << "Model not found at " << MODEL_PATH << ".\n";
        return 1;
    }

    std::cout << "Loading ONNX Model...\n";
    OnnxDetector detector(MODEL_PATH.string());

    json results = json::array();
    for (const auto& f : common::glob_suffix(STAGE4_DIR, "_final.jpg")) {
        std::string fname = f.filename().string();
        std::string panel_type = common::contains(fname, "_id_") ? "id" : "mcq";
        auto r = process_inference(detector, f, panel_type, TOTAL_QUESTIONS);
        if (!r) continue;
        results.push_back(*r);
        std::cout << "Processed " << (*r)["file"].get<std::string>() << " | split=" << (*r)["column_split"].dump()
                  << " | Found " << (*r)["total_detections"].get<int>() << " bubbles\n";
    }

    auto cols = get_mcq_column_math(TOTAL_QUESTIONS);
    json metadata;
    metadata["mcq_layout"]["total_questions"] = TOTAL_QUESTIONS;
    metadata["mcq_layout"]["columns"] = {cols[0], cols[1], cols[2]};
    metadata["id_layout"]["columns"] = json::array({
        {{"type", "letters"}, {"count", 6}},
        {{"type", "digits"}, {"count", 10}},
        {{"type", "digits"}, {"count", 10}},
        {{"type", "digits"}, {"count", 10}},
    });
    metadata["column_split_min_questions"] = COLUMN_SPLIT_MIN_QUESTIONS;

    std::ofstream mf(OUT_DIR / "_layout_metadata.json");
    mf << metadata.dump(2);

    return 0;
}
