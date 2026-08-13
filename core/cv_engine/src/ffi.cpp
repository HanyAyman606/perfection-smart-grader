#include "ffi.h"
#include "preprocessing.h"
#include "inference.h"
#include "questions.h"
#include "config.h"
#include "result.h"
#include <nlohmann/json.hpp>
#include <iostream>
#include <cstring>
#include <vector>
#include <chrono>
#include <opencv2/opencv.hpp>

static char* allocate_string(const std::string& str) {
    char* cstr = new char[str.length() + 1];
    std::strcpy(cstr, str.c_str());
    return cstr;
}

// Coarse wall-clock timer for the phase boundaries that already exist in
// process_exam_in_memory. Deliberately simple (no averaging, no
// percentiles) -- this is meant to answer ONE question fast: which phase
// of a single real scan is actually eating the time, since guessing that
// from code review alone (see the ffi.cpp/inference.cpp read-through
// this was built from) turned out to be unreliable -- e.g. sessions are
// already cached, so "session recreation" was ruled out by reading the
// code, not by timing it. Timed phases are reported back to Dart inside
// the same JSON payload the app already parses, under "timing_ms", so a
// real on-device run surfaces this via DebugLog/logcat with zero extra
// plumbing on the Flutter side.
struct PhaseTimer {
    std::chrono::steady_clock::time_point t0;
    nlohmann::json ms = nlohmann::json::object();

    void start() { t0 = std::chrono::steady_clock::now(); }

    // Records elapsed time since the last start() under `label`, then
    // restarts the clock for the next phase -- call once per phase in
    // sequence rather than juggling multiple time_points by hand.
    void lap(const std::string& label) {
        auto now = std::chrono::steady_clock::now();
        double elapsed = std::chrono::duration<double, std::milli>(now - t0).count();
        ms[label] = elapsed;
        t0 = now;
    }
};

extern "C" {

const char* process_exam_in_memory(const char* image_path, const char* config_json_str) {
    PhaseTimer timer;
    auto scan_start = std::chrono::steady_clock::now();
    try {
        nlohmann::json j = nlohmann::json::parse(config_json_str);
        ExamConfig config;
        from_json(j, config);

        // Step 1: In-memory panel extraction
        timer.start();
        auto [id_final, mcq_final, quality] = preprocessing::prepare_from_raw(image_path, config);
        timer.lap("stage1_preprocessing_total_ms");

        CorrectionResult final_result;
        
        // Pass step 1 signals through to the final payload
        nlohmann::json result = final_result.to_json_obj();
        result["status"] = "SUCCESS";
        result["confidence"] = quality.is_ok ? "OK" : "LOW";
        result["warnings"] = quality.warnings;
        result["orientation_reason"] = quality.orientation_reason;
        result["blur_score"] = quality.blur_score;
        result["id_panel_blur_score"] = quality.id_panel_blur_score;
        result["mcq_panel_blur_score"] = quality.mcq_panel_blur_score;
        result["num_quad_candidates"] = quality.num_quad_candidates;

        // Early exit: if the image quality is bad (e.g., BLUR or NO_ID_PANEL),
        // skip the heavy ONNX inference completely to save CPU!
        if (!quality.is_ok) {
            timer.ms["total_ms"] = std::chrono::duration<double, std::milli>(
                std::chrono::steady_clock::now() - scan_start).count();
            result["timing_ms"] = timer.ms;
            return allocate_string(result.dump());
        }

        // Step 2: Direct inference without writing images
        timer.start();
        if (id_final) {
            std::vector<int> col_sizes;
            for (int i = 0; i < config.id.num_letters; ++i) col_sizes.push_back(config.id.letters.size());
            for (int i = 0; i < config.id.num_digits; ++i) col_sizes.push_back(10);
            
            auto id_dets = inference::run_inference_id_adaptive(*id_final, config.model_path, col_sizes, 0.25f, 0.10f, 0.45f);
            timer.lap("stage2_id_inference_ms");

            auto [id_cols, id_str, id_letter, id_review] = questions::process_student_id(id_dets, config.id.num_digits, config.id.num_letters, config.id.letters, config.id.column_labels(), config.row_tolerance_px);
            timer.lap("stage3_id_classify_ms");

            final_result.id_columns = id_cols;
            final_result.student_id_string = id_str;
            final_result.student_id_letter = id_letter;
            final_result.id_needs_review = id_review;
        } else {
            timer.start(); // keep timer anchored even if this branch is skipped
        }

        if (mcq_final) {
            auto mcq_dets = inference::run_inference_mcq_adaptive(*mcq_final, config.model_path, config.num_questions, config.num_question_columns, config.num_choices, 0.25f, 0.05f, 0.45f, config.mcq_column_sizes);
            timer.lap("stage2_mcq_inference_ms");

            final_result.questions = questions::process_questions(mcq_dets, config.num_questions, config.num_question_columns, config.choice_labels(), config.row_tolerance_px, config.mcq_column_sizes);
            timer.lap("stage3_mcq_classify_ms");
        }

        for (const auto& q : final_result.questions) {
            if (q.state == "ERROR_MISSING") final_result.has_missing_rows = true;
        }
        for (const auto& c : final_result.id_columns) {
            if (c.state == "ERROR_MISSING") final_result.has_missing_rows = true;
        }

        // Merge inference results into the final payload
        nlohmann::json inference_json = final_result.to_json_obj();
        result.update(inference_json);

        timer.ms["total_ms"] = std::chrono::duration<double, std::milli>(
            std::chrono::steady_clock::now() - scan_start).count();
        result["timing_ms"] = timer.ms;

        return allocate_string(result.dump());
    } catch (const std::exception& e) {
        std::cerr << "Error in process_exam_in_memory: " << e.what() << std::endl;
        nlohmann::json err;
        err["status"] = "ERROR";
        err["message"] = e.what();
        err["confidence"] = "LOW";
        err["warnings"] = nlohmann::json::array({"PROCESSING_ERROR"});
        return allocate_string(err.dump());
    }
}

void free_string(char* str) {
    if (str) {
        delete[] str;
    }
}

} // extern "C"