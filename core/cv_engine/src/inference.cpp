#include "inference.h"
#include "grouping.h"
#include <onnxruntime/onnxruntime_cxx_api.h>
#include <unordered_map>
#include <map>
#include <cmath>
#include <algorithm>
#include <set>

namespace inference {

namespace {

constexpr int MODEL_W = 416;
constexpr int MODEL_H = 416;
constexpr int TILE_MAX_W = 550;

int internal_class(int model_cls) {
    // Class names order from model config: ["filled", "empty", "canceled"]
    // Model output 0 = filled, 1 = empty, 2 = canceled
    if (model_cls == 0) return CLASS_FILLED;    // model 0 = filled dark bubble
    if (model_cls == 1) return CLASS_EMPTY;     // model 1 = empty open circle
    if (model_cls == 2) return CLASS_CANCELED;  // model 2 = canceled (crossed)
    return CLASS_EMPTY;
}

std::unordered_map<std::string, Ort::Session*> session_map;
Ort::Env env(ORT_LOGGING_LEVEL_WARNING, "ai_corrector");
Ort::SessionOptions session_options;

Ort::Session& get_session(const std::string& model_path) {
    auto it = session_map.find(model_path);
    if (it == session_map.end()) {
#ifdef _WIN32
        std::wstring wpath(model_path.begin(), model_path.end());
        Ort::Session* sess = new Ort::Session(env, wpath.c_str(), session_options);
#else
        Ort::Session* sess = new Ort::Session(env, model_path.c_str(), session_options);
#endif
        session_map[model_path] = sess;
        return *sess;
    }
    return *(it->second);
}

float iou(const Detection& a, const Detection& b) {
    float ax1 = a.cx - a.w / 2.0f, ay1 = a.cy - a.h / 2.0f;
    float ax2 = a.cx + a.w / 2.0f, ay2 = a.cy + a.h / 2.0f;
    float bx1 = b.cx - b.w / 2.0f, by1 = b.cy - b.h / 2.0f;
    float bx2 = b.cx + b.w / 2.0f, by2 = b.cy + b.h / 2.0f;

    float ix1 = std::max(ax1, bx1), iy1 = std::max(ay1, by1);
    float ix2 = std::min(ax2, bx2), iy2 = std::min(ay2, by2);

    float inter_w = std::max(0.0f, ix2 - ix1);
    float inter_h = std::max(0.0f, iy2 - iy1);
    float inter = inter_w * inter_h;

    float area_a = a.w * a.h;
    float area_b = b.w * b.h;
    float union_area = area_a + area_b - inter;

    return inter / std::max(union_area, 1e-9f);
}

// True if a and b are almost certainly the same physical bubble.
//
// Plain IoU alone misses a specific case: infer_tiled() runs each tile
// through an independent resize/pad before the model, so a bubble sitting
// in the overlap band between two tiles gets detected twice with boxes
// that are shifted by a few pixels from each other (different per-tile
// rounding), not from any real difference in the underlying object. That
// small shift is often enough to drop IoU below the usual threshold, so
// both survive dedup as if they were two distinct bubbles — which then
// inflates row/column counts downstream (e.g. an ID column ending up with
// 18 detections instead of 10, one per tile for bubbles near the seam).
//
// Catch that case directly: if the two boxes are near-identical in size
// and their centers sit close together relative to that size, treat them
// as duplicates regardless of IoU.
bool is_duplicate_detection(const Detection& a, const Detection& b, float iou_threshold) {
    if (iou(a, b) > iou_threshold) return true;

    float dx = a.cx - b.cx;
    float dy = a.cy - b.cy;
    float center_dist = std::sqrt(dx * dx + dy * dy);
    float avg_size = (std::min(a.w, a.h) + std::min(b.w, b.h)) / 2.0f;

    return center_dist < avg_size * 0.5f;
}

std::vector<Detection> dedup(std::vector<Detection> detections, float iou_threshold) {
    if (detections.empty()) return {};

    std::sort(detections.begin(), detections.end(), [](const Detection& a, const Detection& b) {
        return a.confidence > b.confidence;
    });

    std::vector<Detection> kept;
    std::vector<bool> removed(detections.size(), false);

    for (size_t i = 0; i < detections.size(); ++i) {
        if (removed[i]) continue;
        kept.push_back(detections[i]);
        for (size_t j = i + 1; j < detections.size(); ++j) {
            if (removed[j]) continue;
            if (is_duplicate_detection(detections[i], detections[j], iou_threshold)) {
                removed[j] = true;
            }
        }
    }
    return kept;
}

std::vector<Detection> infer_single(Ort::Session& sess, const cv::Mat& image, float conf_threshold, float x_offset = 0.0f, float y_offset = 0.0f) {
    int h = image.rows, w = image.cols;
    
    cv::Mat padded, rgb;
    float r = std::min((float)MODEL_H / h, (float)MODEL_W / w);
    int uw = (int)std::round(w * r);
    int uh = (int)std::round(h * r);
    cv::Mat resized;
    cv::resize(image, resized, cv::Size(uw, uh), 0, 0, cv::INTER_LINEAR);
    float pad_w = (MODEL_W - uw) / 2.0f;
    float pad_h = (MODEL_H - uh) / 2.0f;
    int top = (int)std::round(pad_h - 0.1f);
    int bottom = (int)std::round(pad_h + 0.1f);
    int left = (int)std::round(pad_w - 0.1f);
    int right = (int)std::round(pad_w + 0.1f);
    cv::copyMakeBorder(resized, padded, top, bottom, left, right, cv::BORDER_CONSTANT, cv::Scalar(114, 114, 114));

    cv::cvtColor(padded, rgb, cv::COLOR_BGR2RGB);
    
    cv::Mat blob;
    rgb.convertTo(blob, CV_32FC3, 1.0 / 255.0);

    std::vector<float> input_tensor_values(1 * 3 * MODEL_H * MODEL_W);
    std::vector<cv::Mat> channels;
    for (int i = 0; i < 3; ++i) {
        channels.push_back(cv::Mat(MODEL_H, MODEL_W, CV_32FC1, input_tensor_values.data() + i * MODEL_H * MODEL_W));
    }
    cv::split(blob, channels);

    Ort::MemoryInfo memory_info = Ort::MemoryInfo::CreateCpu(OrtArenaAllocator, OrtMemTypeDefault);
    std::vector<int64_t> input_shape = {1, 3, MODEL_H, MODEL_W};
    Ort::Value input_tensor = Ort::Value::CreateTensor<float>(memory_info, input_tensor_values.data(), input_tensor_values.size(), input_shape.data(), input_shape.size());

    const char* input_names[] = {"images"};
    const char* output_names[] = {"output"};

    auto output_tensors = sess.Run(Ort::RunOptions{nullptr}, input_names, &input_tensor, 1, output_names, 1);
    auto& output_tensor = output_tensors.front();
    auto type_info = output_tensor.GetTensorTypeAndShapeInfo();
    auto shape = type_info.GetShape(); 

    if (shape.size() < 2 || shape[0] == 0) return {};

    const float* out_data = output_tensor.GetTensorData<float>();
    size_t num_detections = shape[0];

    std::vector<Detection> detections;
    for (size_t i = 0; i < num_detections; ++i) {
        const float* row = out_data + i * 7;
        float conf = row[6];
        if (conf < conf_threshold) continue;
        
        float x1 = (row[1] - pad_w) / r + x_offset;
        float y1 = (row[2] - pad_h) / r + y_offset;
        float x2 = (row[3] - pad_w) / r + x_offset;
        float y2 = (row[4] - pad_h) / r + y_offset;

        x1 = std::max(0.0f + x_offset, std::min((float)w - 1.0f + x_offset, x1));
        x2 = std::max(0.0f + x_offset, std::min((float)w - 1.0f + x_offset, x2));
        y1 = std::max(0.0f + y_offset, std::min((float)h - 1.0f + y_offset, y1));
        y2 = std::max(0.0f + y_offset, std::min((float)h - 1.0f + y_offset, y2));

        if (x2 - x1 < 2 || y2 - y1 < 2) continue;

        float cx = (x1 + x2) / 2.0f;
        float cy = (y1 + y2) / 2.0f;
        float bw = x2 - x1;
        float bh = y2 - y1;

        int cls = internal_class((int)row[5]);
        detections.push_back({cx, cy, bw, bh, conf, cls});
    }

    return detections;
}

std::vector<Detection> infer_tiled(Ort::Session& sess, const cv::Mat& image, float conf_threshold, float iou_threshold, int num_tiles) {
    int h = image.rows, w = image.cols;
    float tile_w = (float)w / num_tiles;
    int overlap_px = (int)(tile_w * 0.08f);

    std::vector<Detection> all_dets;
    for (int t = 0; t < num_tiles; ++t) {
        int x_start = std::max(0, (int)(t * tile_w) - overlap_px);
        int x_end = std::min(w, (int)((t + 1) * tile_w) + overlap_px);
        cv::Mat tile = image(cv::Rect(x_start, 0, x_end - x_start, h));
        auto tile_dets = infer_single(sess, tile, conf_threshold, (float)x_start, 0.0f);
        all_dets.insert(all_dets.end(), tile_dets.begin(), tile_dets.end());
    }

    return dedup(all_dets, iou_threshold);
}

std::vector<float> uniform_positions(int n, int span, float margin_frac = 0.10f) {
    float lo = span * margin_frac;
    float hi = span * (1.0f - margin_frac);
    if (n == 1) return {(lo + hi) / 2.0f};
    float step = (hi - lo) / (n - 1);
    std::vector<float> res(n);
    for (int i = 0; i < n; ++i) res[i] = lo + i * step;
    return res;
}

std::vector<float> merge_with_uniform(const std::vector<float>& detected, const std::vector<float>& uniform, int n) {
    std::vector<float> result = uniform;
    for (float dx : detected) {
        int closest = 0;
        float min_dist = std::abs(result[0] - dx);
        for (int i = 1; i < n; ++i) {
            float dist = std::abs(result[i] - dx);
            if (dist < min_dist) {
                min_dist = dist;
                closest = i;
            }
        }
        result[closest] = dx;
    }
    return result;
}

std::vector<float> estimate_col_xs(const std::vector<Detection>& dets, int num_cols, int img_w) {
    if (dets.empty()) return uniform_positions(num_cols, img_w, 0.12f);

    std::vector<float> xs;
    for (const auto& d : dets) xs.push_back(d.cx);
    std::sort(xs.begin(), xs.end());

    struct Gap { float dist; int idx; };
    std::vector<Gap> gaps;
    for (size_t i = 0; i < xs.size() - 1; ++i) {
        gaps.push_back({xs[i+1] - xs[i], (int)i});
    }
    std::sort(gaps.begin(), gaps.end(), [](const Gap& a, const Gap& b){ return a.dist > b.dist; });
    
    std::vector<int> split_after;
    for (int i = 0; i < std::min(num_cols - 1, (int)gaps.size()); ++i) {
        split_after.push_back(gaps[i].idx);
    }
    std::sort(split_after.begin(), split_after.end());

    std::vector<std::vector<float>> groups;
    int start = 0;
    for (int si : split_after) {
        groups.push_back(std::vector<float>(xs.begin() + start, xs.begin() + si + 1));
        start = si + 1;
    }
    groups.push_back(std::vector<float>(xs.begin() + start, xs.end()));

    std::vector<float> col_xs;
    for (const auto& g : groups) {
        if (!g.empty()) {
            std::vector<float> cg = g;
            std::sort(cg.begin(), cg.end());
            col_xs.push_back(cg[cg.size()/2]);
        }
    }

    if (col_xs.size() == (size_t)num_cols) {
        return col_xs;
    }

    if (col_xs.size() >= 2) {
        std::vector<float> cgaps;
        for (size_t i = 0; i < col_xs.size() - 1; ++i) cgaps.push_back(col_xs[i+1] - col_xs[i]);
        std::sort(cgaps.begin(), cgaps.end());
        float step = cgaps[cgaps.size()/2];

        if (step > img_w * 0.05f) {
            float best_start = 0;
            float min_error = 1e9;
            for (int offset = 0; offset < num_cols; ++offset) {
                float cand_start = col_xs[0] - offset * step;
                float error = 0;
                for (float dx : col_xs) {
                    float closest_dist = 1e9;
                    for (int i = 0; i < num_cols; ++i) {
                        float px = cand_start + i * step;
                        closest_dist = std::min(closest_dist, std::abs(px - dx));
                    }
                    error += closest_dist;
                }
                if (error < min_error) {
                    min_error = error;
                    best_start = cand_start;
                }
            }
            std::vector<float> res(num_cols);
            for (int i = 0; i < num_cols; ++i) res[i] = best_start + i * step;
            return res;
        }
    }

    auto uni = uniform_positions(num_cols, img_w, 0.12f);
    return merge_with_uniform(col_xs, uni, num_cols);
}

std::vector<float> estimate_row_ys(const std::vector<Detection>& col_dets, int n_rows, int img_h) {
    if (col_dets.empty()) return uniform_positions(n_rows, img_h, 0.08f);

    std::vector<float> ys;
    for (const auto& d : col_dets) ys.push_back(d.cy);
    std::sort(ys.begin(), ys.end());

    if (ys.size() == 1) {
        float cy0 = ys[0];
        float span = img_h * 0.78f;
        float step = span / std::max(n_rows - 1, 1);
        float start = cy0 - step * std::round((cy0 - img_h * 0.11f) / step);
        std::vector<float> res(n_rows);
        for (int i = 0; i < n_rows; ++i) res[i] = start + i * step;
        return res;
    }

    std::vector<float> spacings;
    for (size_t i = 0; i < ys.size() - 1; ++i) spacings.push_back(ys[i+1] - ys[i]);
    std::sort(spacings.begin(), spacings.end());
    float step = spacings[spacings.size()/2];
    if (step < 4.0f) step = (float)img_h / (n_rows + 1);

    float top_y = ys[0];
    std::vector<float> dense(n_rows);
    for (int i = 0; i < n_rows; ++i) dense[i] = top_y + i * step;

    float bottom_margin = img_h * 0.05f;
    float overshoot = dense.back() - (img_h - bottom_margin);
    if (overshoot > 0) {
        for (float& y : dense) y -= overshoot;
    }

    return dense;
}

std::vector<std::vector<std::pair<float, float>>> estimate_id_grid(const std::vector<Detection>& dets, const std::vector<int>& col_sizes, const cv::Size& img_shape) {
    int h = img_shape.height, w = img_shape.width;
    int num_cols = col_sizes.size();
    auto col_xs = estimate_col_xs(dets, num_cols, w);
    float tol_x = w / (num_cols * 2.0f);

    std::vector<std::vector<std::pair<float, float>>> grid;
    for (int col_i = 0; col_i < num_cols; ++col_i) {
        float col_x = col_xs[col_i];
        int n_rows = col_sizes[col_i];
        
        std::vector<Detection> col_dets;
        for (const auto& d : dets) {
            if (std::abs(d.cx - col_x) < tol_x) col_dets.push_back(d);
        }

        auto row_ys = estimate_row_ys(col_dets, n_rows, h);
        std::vector<std::pair<float, float>> col_cells;
        for (float ry : row_ys) col_cells.push_back({col_x, ry});
        grid.push_back(col_cells);
    }
    return grid;
}

std::vector<std::vector<std::pair<float, float>>> estimate_mcq_grid(
    const std::vector<Detection>& dets, int num_questions, int num_question_columns, int num_choices, const cv::Size& img_shape) {
    
    int h = img_shape.height, w = img_shape.width;
    int total_cols = num_question_columns * num_choices;
    int rows_per_col = std::ceil((float)num_questions / num_question_columns);

    float margin = 0.08f;
    float span = w * (1.0f - 2.0f * margin);
    float block_span = span / num_question_columns;
    float choice_span = block_span * 0.70f;
    float block_margin = (block_span - choice_span) / 2.0f;

    std::vector<float> col_xs;
    for (int qc = 0; qc < num_question_columns; ++qc) {
        float block_start = w * margin + qc * block_span + block_margin;
        if (num_choices == 1) {
            col_xs.push_back(block_start + choice_span / 2.0f);
        } else {
            float step = choice_span / (num_choices - 1);
            for (int c = 0; c < num_choices; ++c) {
                col_xs.push_back(block_start + c * step);
            }
        }
    }

    if (!dets.empty()) {
        std::vector<float> xs;
        for (const auto& d : dets) xs.push_back(d.cx);
        std::sort(xs.begin(), xs.end());
        
        for (float dx : xs) {
            int closest = 0;
            float min_dist = std::abs(col_xs[0] - dx);
            for (int i = 1; i < total_cols; ++i) {
                float dist = std::abs(col_xs[i] - dx);
                if (dist < min_dist) {
                    min_dist = dist;
                    closest = i;
                }
            }
            if (min_dist < w / (float)total_cols) {
                col_xs[closest] = col_xs[closest] * 0.8f + dx * 0.2f;
            }
        }
    }

    auto raw_mega_rows = grouping::group_by_rows(dets, 15);
    int expected_bubbles = num_choices * num_question_columns;
    int min_bubbles = std::max(num_question_columns > 1 ? 2 : 1, expected_bubbles / 3);
    
    std::vector<std::vector<Detection>> valid_mega_rows;
    for (const auto& r : raw_mega_rows) {
        if (r.size() >= (size_t)min_bubbles) valid_mega_rows.push_back(r);
    }

    std::vector<float> row_ys;
    if (valid_mega_rows.size() >= 2) {
        std::vector<float> detected_ys;
        for (const auto& mr : valid_mega_rows) {
            std::vector<float> y_vals;
            for (const auto& d : mr) y_vals.push_back(d.cy);
            std::sort(y_vals.begin(), y_vals.end());
            detected_ys.push_back(y_vals[y_vals.size()/2]);
        }
        std::sort(detected_ys.begin(), detected_ys.end());

        std::vector<float> gaps;
        for (size_t i = 0; i < detected_ys.size() - 1; ++i) gaps.push_back(detected_ys[i+1] - detected_ys[i]);
        std::sort(gaps.begin(), gaps.end());
        float step_y = gaps[gaps.size()/2];

        float first_y = detected_ys[0];
        row_ys.resize(rows_per_col);
        for (int i = 0; i < rows_per_col; ++i) row_ys[i] = first_y + i * step_y;

        float header_space = h * 0.15f;
        if (first_y - step_y > header_space && valid_mega_rows.size() < (size_t)rows_per_col) {
            for (int i = 0; i < rows_per_col; ++i) row_ys[i] = first_y - step_y + i * step_y;
        }

        for (float my : detected_ys) {
            int closest = 0;
            float min_dist = std::abs(row_ys[0] - my);
            for (int i = 1; i < rows_per_col; ++i) {
                float dist = std::abs(row_ys[i] - my);
                if (dist < min_dist) {
                    min_dist = dist;
                    closest = i;
                }
            }
            if (min_dist < step_y * 0.4f) {
                row_ys[closest] = row_ys[closest] * 0.5f + my * 0.5f;
            }
        }

    } else {
        float margin_top = 0.18f;
        float margin_bottom = 0.25f;
        float span_y = h * (1.0f - margin_top - margin_bottom);
        if (rows_per_col == 1) {
            row_ys.push_back(h / 2.0f);
        } else {
            float step_y = span_y / (rows_per_col - 1);
            row_ys.resize(rows_per_col);
            for (int i = 0; i < rows_per_col; ++i) row_ys[i] = h * margin_top + i * step_y;
        }
    }

    std::vector<std::vector<std::pair<float, float>>> grid;
    for (int c_idx = 0; c_idx < total_cols; ++c_idx) {
        float cx = col_xs[c_idx];
        std::vector<std::pair<float, float>> col_cells;
        int q_col = c_idx / num_choices;
        for (int r_idx = 0; r_idx < rows_per_col; ++r_idx) {
            float cy = row_ys[r_idx];
            int q_num = q_col * rows_per_col + r_idx + 1;
            if (q_num > num_questions) continue;
            col_cells.push_back({cx, cy});
        }
        grid.push_back(col_cells);
    }
    return grid;
}

// Mirrors estimate_id_grid but for the MCQ panel: each question-column is a
// block of `num_choices` choice-columns. Within each question-column the row
// count comes from the per_column_questions map (1-indexed keys).
std::vector<std::vector<std::pair<float,float>>> estimate_mcq_grid_per_column(
    const std::vector<Detection>& dets,
    const std::map<std::string, int>& per_column_questions, // 1-indexed
    int num_choices,
    const cv::Size& img_shape)
{
    int h = img_shape.height, w = img_shape.width;
    int num_q_cols = (int)per_column_questions.size();
    if (num_q_cols == 0) return {};

    // Build ordered per-column row counts (key "1" -> col 0, etc.)
    std::vector<int> cols_q(num_q_cols, 0);
    for (const auto& [k, v] : per_column_questions) {
        int idx = std::stoi(k) - 1; // convert 1-indexed to 0-indexed
        if (idx >= 0 && idx < num_q_cols) cols_q[idx] = v;
    }

    int total_q_cols = num_q_cols;
    int total_choice_cols = total_q_cols * num_choices;

    // Estimate x-centres for each question-column block.
    float margin = 0.08f;
    float span = w * (1.0f - 2.0f * margin);
    float block_span = span / total_q_cols;
    float choice_span = block_span * 0.70f;
    float block_margin = (block_span - choice_span) / 2.0f;

    std::vector<float> col_xs;
    for (int qc = 0; qc < total_q_cols; ++qc) {
        float block_start = w * margin + qc * block_span + block_margin;
        if (num_choices == 1) {
            col_xs.push_back(block_start + choice_span / 2.0f);
        } else {
            float step = choice_span / (num_choices - 1);
            for (int c = 0; c < num_choices; ++c)
                col_xs.push_back(block_start + c * step);
        }
    }

    // Refine col_xs with detected x positions (same snap logic as estimate_mcq_grid)
    if (!dets.empty()) {
        std::vector<float> xs;
        for (const auto& d : dets) xs.push_back(d.cx);
        std::sort(xs.begin(), xs.end());
        for (float dx : xs) {
            int closest = 0;
            float min_dist = std::abs(col_xs[0] - dx);
            for (int i = 1; i < total_choice_cols; ++i) {
                float dist = std::abs(col_xs[i] - dx);
                if (dist < min_dist) { min_dist = dist; closest = i; }
            }
            if (min_dist < w / (float)total_choice_cols)
                col_xs[closest] = col_xs[closest] * 0.8f + dx * 0.2f;
        }
    }

    // For each question-column, estimate per-column row y-centres using
    // detections near that block, then expand into one entry per choice-column.
    std::vector<std::vector<std::pair<float,float>>> grid;
    for (int qc = 0; qc < total_q_cols; ++qc) {
        int n_rows = cols_q[qc];
        if (n_rows <= 0) continue;

        // x range of this question-column's choice columns
        float x_lo = col_xs[qc * num_choices];
        float x_hi = col_xs[std::min(qc * num_choices + num_choices - 1,
                                     total_choice_cols - 1)];
        float x_mid = (x_lo + x_hi) / 2.0f;
        float tol_x = (x_hi - x_lo) / 2.0f + block_span * 0.15f;

        std::vector<Detection> col_dets;
        for (const auto& d : dets)
            if (std::abs(d.cx - x_mid) <= tol_x)
                col_dets.push_back(d);

        auto row_ys = estimate_row_ys(col_dets, n_rows, h);

        for (int c = 0; c < num_choices; ++c) {
            float cx = col_xs[qc * num_choices + c];
            std::vector<std::pair<float,float>> cells;
            for (float ry : row_ys) cells.push_back({cx, ry});
            grid.push_back(cells);
        }
    }
    return grid;
}

} // namespace

std::vector<Detection> run_inference(const cv::Mat& image, const std::string& model_path, float conf_threshold, float iou_threshold) {
    Ort::Session& sess = get_session(model_path);
    int num_tiles = std::max(1, (int)std::ceil((float)image.cols / TILE_MAX_W));
    if (num_tiles == 1) {
        return infer_single(sess, image, conf_threshold);
    }
    return infer_tiled(sess, image, conf_threshold, iou_threshold, num_tiles);
}

std::vector<Detection> run_inference_id_adaptive(const cv::Mat& image, const std::string& model_path, const std::vector<int>& col_sizes, float conf_threshold, float fill_conf, float iou_threshold) {
    Ort::Session& sess = get_session(model_path);
    auto initial_dets = infer_single(sess, image, conf_threshold);

    float max_bubble_size = 0.15f * std::min(image.rows, image.cols);
    float min_bubble_size = 0.02f * std::min(image.rows, image.cols);
    
    std::vector<float> ws, hs;
    for (const auto& d : initial_dets) {
        if (d.w >= min_bubble_size && d.w <= max_bubble_size &&
            d.h >= min_bubble_size && d.h <= max_bubble_size) {
            ws.push_back(d.w);
            hs.push_back(d.h);
        }
    }
    
    float avg_bw = 0.07f * std::min(image.rows, image.cols);
    float avg_bh = avg_bw;
    if (!ws.empty()) {
        std::sort(ws.begin(), ws.end()); std::sort(hs.begin(), hs.end());
        avg_bw = ws[ws.size()/2];
        avg_bh = hs[hs.size()/2];
    }
    float cover_r = std::max(avg_bw, avg_bh) * 0.55f;

    std::vector<Detection> valid_size_dets;
    for (const auto& d : initial_dets) {
        if (d.w <= avg_bw * 2.5f && d.h <= avg_bh * 2.5f &&
            d.w >= avg_bw * 0.4f && d.h >= avg_bh * 0.4f) {
            valid_size_dets.push_back(d);
        }
    }

    auto grid = estimate_id_grid(valid_size_dets, col_sizes, image.size());

    std::vector<Detection> valid_initial;
    for (const auto& d : initial_dets) {
        bool aligned = false;
        for (const auto& col_cells : grid) {
            for (const auto& p : col_cells) {
                float pred_cx = p.first, pred_cy = p.second;
                if ((d.cx - pred_cx)*(d.cx - pred_cx) + (d.cy - pred_cy)*(d.cy - pred_cy) < cover_r*cover_r) {
                    aligned = true; break;
                }
            }
            if (aligned) break;
        }
        if (aligned) valid_initial.push_back(d);
    }

    std::vector<Detection> recovered;
    for (const auto& col_cells : grid) {
        for (const auto& p : col_cells) {
            float pred_cx = p.first, pred_cy = p.second;
            bool covered = false;
            for (const auto& d : valid_initial) {
                if (std::abs(d.cx - pred_cx) < cover_r && std::abs(d.cy - pred_cy) < cover_r) {
                    covered = true; break;
                }
            }
            if (covered) continue;

            int roi_w = (int)(avg_bw * 3.5f);
            int roi_h = (int)(avg_bh * 3.5f);
            int x0 = std::max(0, (int)(pred_cx - roi_w / 2.0f));
            int y0 = std::max(0, (int)(pred_cy - roi_h / 2.0f));
            int x1 = std::min(image.cols, x0 + roi_w);
            int y1 = std::min(image.rows, y0 + roi_h);
            if (x1 - x0 < 8 || y1 - y0 < 8) continue;

            cv::Mat roi = image(cv::Rect(x0, y0, x1 - x0, y1 - y0));
            auto roi_dets = infer_single(sess, roi, fill_conf, (float)x0, (float)y0);
            if (roi_dets.empty()) continue;

            auto best = std::min_element(roi_dets.begin(), roi_dets.end(), [=](const Detection& a, const Detection& b){
                return (a.cx - pred_cx)*(a.cx - pred_cx) + (a.cy - pred_cy)*(a.cy - pred_cy) < 
                       (b.cx - pred_cx)*(b.cx - pred_cx) + (b.cy - pred_cy)*(b.cy - pred_cy);
            });
            float dist = std::sqrt((best->cx - pred_cx)*(best->cx - pred_cx) + (best->cy - pred_cy)*(best->cy - pred_cy));
            if (dist <= cover_r * 2.0f) recovered.push_back(*best);
        }
    }

    std::vector<Detection> all_dets = valid_initial;
    all_dets.insert(all_dets.end(), recovered.begin(), recovered.end());
    std::sort(all_dets.begin(), all_dets.end(), [](const Detection& a, const Detection& b){ 
        if (a.is_filled() != b.is_filled()) return a.is_filled() > b.is_filled();
        return a.confidence > b.confidence; 
    });

    std::vector<Detection> kept_dets;
    for (const auto& d : all_dets) {
        bool too_close = false;
        for (const auto& k : kept_dets) {
            if (std::sqrt((k.cx - d.cx)*(k.cx - d.cx) + (k.cy - d.cy)*(k.cy - d.cy)) < cover_r) {
                too_close = true; break;
            }
        }
        if (!too_close) kept_dets.push_back(d);
    }
    return dedup(kept_dets, iou_threshold);
}

std::vector<Detection> run_inference_mcq_adaptive(const cv::Mat& image, const std::string& model_path, int num_questions, int num_question_columns, int num_choices, float conf_threshold, float fill_conf, float iou_threshold) {
    Ort::Session& sess = get_session(model_path);
    int num_tiles = std::max(1, (int)std::ceil((float)image.cols / TILE_MAX_W));
    auto initial_dets = infer_tiled(sess, image, conf_threshold, iou_threshold, num_tiles);
    auto grid = estimate_mcq_grid(initial_dets, num_questions, num_question_columns, num_choices, image.size());

    float avg_bw = 0.03f * std::min(image.rows, image.cols);
    float avg_bh = avg_bw;
    if (!initial_dets.empty()) {
        std::vector<float> ws, hs;
        for (const auto& d : initial_dets) { ws.push_back(d.w); hs.push_back(d.h); }
        std::sort(ws.begin(), ws.end()); std::sort(hs.begin(), hs.end());
        avg_bw = ws[ws.size()/2];
        avg_bh = hs[hs.size()/2];
    }
    float cover_r = std::max(avg_bw, avg_bh) * 0.55f;

    std::vector<Detection> recovered;
    std::vector<Detection> valid_initial = initial_dets;

    for (const auto& col_cells : grid) {
        for (const auto& p : col_cells) {
            float pred_cx = p.first, pred_cy = p.second;
            bool covered = false;
            for (const auto& d : valid_initial) {
                if (std::abs(d.cx - pred_cx) < cover_r && std::abs(d.cy - pred_cy) < cover_r) {
                    covered = true; break;
                }
            }
            if (covered) continue;

            int roi_w = (int)(avg_bw * 3.5f);
            int roi_h = (int)(avg_bh * 3.5f);
            int x0 = std::max(0, (int)(pred_cx - roi_w / 2.0f));
            int y0 = std::max(0, (int)(pred_cy - roi_h / 2.0f));
            int x1 = std::min(image.cols, x0 + roi_w);
            int y1 = std::min(image.rows, y0 + roi_h);
            if (x1 - x0 < 8 || y1 - y0 < 8) continue;

            cv::Mat roi = image(cv::Rect(x0, y0, x1 - x0, y1 - y0));
            auto roi_dets = infer_single(sess, roi, fill_conf, (float)x0, (float)y0);
            if (roi_dets.empty()) continue;

            auto best = std::min_element(roi_dets.begin(), roi_dets.end(), [=](const Detection& a, const Detection& b){
                return (a.cx - pred_cx)*(a.cx - pred_cx) + (a.cy - pred_cy)*(a.cy - pred_cy) < 
                       (b.cx - pred_cx)*(b.cx - pred_cx) + (b.cy - pred_cy)*(b.cy - pred_cy);
            });
            float dist = std::sqrt((best->cx - pred_cx)*(best->cx - pred_cx) + (best->cy - pred_cy)*(best->cy - pred_cy));
            if (dist <= cover_r * 2.0f) recovered.push_back(*best);
        }
    }

    std::vector<Detection> all_dets = valid_initial;
    all_dets.insert(all_dets.end(), recovered.begin(), recovered.end());
    std::sort(all_dets.begin(), all_dets.end(), [](const Detection& a, const Detection& b){ 
        if (a.is_filled() != b.is_filled()) return a.is_filled() > b.is_filled();
        return a.confidence > b.confidence; 
    });

    std::vector<Detection> kept_dets;
    for (const auto& d : all_dets) {
        bool too_close = false;
        for (const auto& k : kept_dets) {
            if (std::sqrt((k.cx - d.cx)*(k.cx - d.cx) + (k.cy - d.cy)*(k.cy - d.cy)) < cover_r) {
                too_close = true; break;
            }
        }
        if (!too_close) kept_dets.push_back(d);
    }
    return dedup(kept_dets, iou_threshold);
}

std::vector<Detection> run_inference_mcq_adaptive_per_column(
    const cv::Mat& image,
    const std::string& model_path,
    const std::map<std::string, int>& per_column_questions,
    int num_choices,
    float conf_threshold,
    float fill_conf,
    float iou_threshold)
{
    Ort::Session& sess = get_session(model_path);
    int num_tiles = std::max(1, (int)std::ceil((float)image.cols / TILE_MAX_W));
    auto initial_dets = infer_tiled(sess, image, conf_threshold, iou_threshold, num_tiles);

    auto grid = estimate_mcq_grid_per_column(
        initial_dets, per_column_questions, num_choices, image.size());

    float avg_bw = 0.03f * std::min(image.rows, image.cols);
    float avg_bh = avg_bw;
    if (!initial_dets.empty()) {
        std::vector<float> ws, hs;
        for (const auto& d : initial_dets) { ws.push_back(d.w); hs.push_back(d.h); }
        std::sort(ws.begin(), ws.end()); std::sort(hs.begin(), hs.end());
        avg_bw = ws[ws.size()/2];
        avg_bh = hs[hs.size()/2];
    }
    float cover_r = std::max(avg_bw, avg_bh) * 0.55f;

    std::vector<Detection> recovered;
    std::vector<Detection> valid_initial = initial_dets;

    for (const auto& col_cells : grid) {
        for (const auto& p : col_cells) {
            float pred_cx = p.first, pred_cy = p.second;
            bool covered = false;
            for (const auto& d : valid_initial) {
                if (std::abs(d.cx - pred_cx) < cover_r && std::abs(d.cy - pred_cy) < cover_r) {
                    covered = true; break;
                }
            }
            if (covered) continue;

            int roi_w = (int)(avg_bw * 3.5f);
            int roi_h = (int)(avg_bh * 3.5f);
            int x0 = std::max(0, (int)(pred_cx - roi_w / 2.0f));
            int y0 = std::max(0, (int)(pred_cy - roi_h / 2.0f));
            int x1 = std::min(image.cols, x0 + roi_w);
            int y1 = std::min(image.rows, y0 + roi_h);
            if (x1 - x0 < 8 || y1 - y0 < 8) continue;

            cv::Mat roi = image(cv::Rect(x0, y0, x1 - x0, y1 - y0));
            auto roi_dets = infer_single(sess, roi, fill_conf, (float)x0, (float)y0);
            if (roi_dets.empty()) continue;

            auto best = std::min_element(roi_dets.begin(), roi_dets.end(),
                [=](const Detection& a, const Detection& b) {
                    return (a.cx - pred_cx)*(a.cx - pred_cx) + (a.cy - pred_cy)*(a.cy - pred_cy)
                         < (b.cx - pred_cx)*(b.cx - pred_cx) + (b.cy - pred_cy)*(b.cy - pred_cy);
                });
            float dist = std::sqrt((best->cx - pred_cx)*(best->cx - pred_cx)
                                 + (best->cy - pred_cy)*(best->cy - pred_cy));
            if (dist <= cover_r * 2.0f) recovered.push_back(*best);
        }
    }

    std::vector<Detection> all_dets = valid_initial;
    all_dets.insert(all_dets.end(), recovered.begin(), recovered.end());
    std::sort(all_dets.begin(), all_dets.end(),
        [](const Detection& a, const Detection& b){ 
            if (a.is_filled() != b.is_filled()) return a.is_filled() > b.is_filled();
            return a.confidence > b.confidence; 
        });

    std::vector<Detection> kept_dets;
    for (const auto& d : all_dets) {
        bool too_close = false;
        for (const auto& k : kept_dets) {
            if (std::sqrt((k.cx - d.cx)*(k.cx - d.cx) + (k.cy - d.cy)*(k.cy - d.cy)) < cover_r) {
                too_close = true; break;
            }
        }
        if (!too_close) kept_dets.push_back(d);
    }
    return dedup(kept_dets, iou_threshold);
}

} // namespace inference