#pragma once

#include <opencv2/opencv.hpp>
#include <string>
#include <vector>
#include <optional>
#include <nlohmann/json.hpp>

using json = nlohmann::json;

namespace shamel_phases {

// ─── PHASE 1: Blur Detection ─────────────────────────────────────────────────
struct Phase1Result {
    bool is_ok;
    double blur_score;
    std::string output_path;
};

Phase1Result phase1_blur_detection(const std::string& image_path, const std::string& output_dir);

// ─── PHASE 2: Frame Detection (Quad Detection + Validation) ───────────────────
struct Quad {
    std::vector<cv::Point2f> pts;  // 4 corners
    float area;
    float cx, cy;  // centroid
    int _geom_score = 0;
};

struct Phase2Result {
    std::string status;  // "PROCEED" or "RETAKE"
    int confidence_score;
    std::vector<std::string> errors;
    std::vector<std::string> retake_reasons;
    
    // The 3 detected quads (may be null)
    std::optional<Quad> id_quad;
    std::optional<Quad> version_quad;
    std::optional<Quad> mcq_quad;
    
    // Visualization output
    std::string phase2_frames_image_path;
};

Phase2Result phase2_frame_detection(const std::string& image_path, const std::string& output_dir);

// ─── PHASE 3: Perspective Warping ────────────────────────────────────────────
struct Phase3Result {
    std::optional<cv::Mat> id_mat;
    std::optional<cv::Mat> version_mat;
    std::optional<cv::Mat> mcq_mat;
    
    std::string id_path;
    std::string version_path;
    std::string mcq_path;
};

Phase3Result phase3_perspective_warp(
    const cv::Mat& img,
    const Phase2Result& phase2,
    const std::string& image_path,
    const std::string& output_dir);

// ─── PHASE 4: YOLO Letterbox Preparation ────────────────────────────────────
struct Phase4Result {
    std::optional<cv::Mat> id_yolo_mat;
    std::optional<cv::Mat> version_yolo_mat;
    std::optional<cv::Mat> mcq_yolo_mat;
    std::vector<cv::Mat> mcq_region_yolo_mats;
    
    std::string id_yolo_path;
    std::string version_yolo_path;
    std::string mcq_yolo_path;
    std::vector<std::string> mcq_region_yolo_paths;
};

Phase4Result phase4_yolo_letterbox(
    const Phase3Result& phase3,
    const std::string& image_path,
    const std::string& output_dir);

// ─── MCQ Grid Splitting (split MCQ into 6 sub-regions) ──────────────────────
struct MCQRegions {
    std::vector<cv::Mat> regions;
    std::vector<std::string> region_names;
    std::vector<std::string> region_paths;
};

struct MCQValidationResult {
    bool is_valid;
    std::string error_message;
    std::vector<int> detected_questions_per_region;
};

MCQRegions split_mcq_into_regions(
    const cv::Mat& img,
    const Phase2Result& phase2,
    const std::string& image_path,
    const std::string& output_dir);

MCQValidationResult validate_mcq_regions(const MCQRegions& regions, int expected_total_questions);

// ─── Phase 5: ONNX Inference (handled elsewhere, but we need the region setup) ─

// ─── Helper: Warp a quad to an upright rectangle ──────────────────────────────
cv::Mat warp_quad(const cv::Mat& img, const std::vector<cv::Point2f>& pts4);

// ─── Annotation helpers ───────────────────────────────────────────────────────
void draw_and_save_quads_annotated(
    const cv::Mat& img,
    const Phase2Result& phase2,
    const std::string& image_path,
    const std::string& output_dir);

} // namespace shamel_phases
