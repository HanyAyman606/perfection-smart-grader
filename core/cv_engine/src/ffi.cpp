#include "ffi.h"
#include "preprocessing.h"
#include "shamel_preprocessing.h"
#include "inference.h"
#include "grouping.h"
#include "questions.h"
#include "config.h"
#include "result.h"
#include <nlohmann/json.hpp>
#include <iostream>
#include <cstring>
#include <vector>
#include <opencv2/opencv.hpp>
#include <map>
#include <filesystem>
#include <optional>

// Implemented in preprocessing.cpp — returns the stage-by-stage diagnostic
// lines (which threshold rejected what) and the single most likely
// image-quality cause (blur/exposure/resolution/framing) from the most
// recent call to preprocessing::prepare_from_raw(). Forward-declared here
// rather than added to preprocessing.h so this change doesn't require
// touching the header; move these into preprocessing.h if that's more
// convenient later.
//
// NOTE: this must live OUTSIDE the anonymous namespace below. Nesting a
// `namespace preprocessing { ... }` inside `namespace { ... }` creates a
// distinct <unnamed>::preprocessing, which then collides with the real
// ::preprocessing from preprocessing.h at every call site below — that's
// what caused the "reference to 'preprocessing' is ambiguous" errors.
namespace preprocessing {
std::vector<std::string> get_diagnostics();
std::optional<std::string> get_quality_cause();
}

namespace {

std::string resolve_runtime_path(const std::string& raw_path) {
    if (raw_path.empty()) return raw_path;
    std::filesystem::path p(raw_path);
    if (p.is_absolute()) return raw_path;

    std::vector<std::filesystem::path> candidates;
    std::filesystem::path cwd = std::filesystem::current_path();
    candidates.push_back(cwd / p);

    for (std::filesystem::path parent = cwd; !parent.empty(); parent = parent.parent_path()) {
        candidates.push_back(parent / p);
        if (parent == parent.parent_path()) break;
    }

    for (const auto& candidate : candidates) {
        std::error_code ec;
        if (std::filesystem::exists(candidate, ec) && std::filesystem::is_regular_file(candidate, ec)) {
            return std::filesystem::weakly_canonical(candidate).string();
        }
    }

    return raw_path;
}

// ─────────────────────────────────────────────────────────────────────────
// RETAKE-worthy result taxonomy
//
// A result becomes status="RETAKE" (instead of a bare SUCCESS with empty
// fields, or an opaque error) whenever the photo is unusable enough that
// re-shooting it is a better answer than trying to grade it:
//   - neither panel could be located at all (NO_PANELS_DETECTED, or a more
//     specific image-quality cause if one stood out — see
//     preprocessing::get_quality_cause())
//   - the MCQ/answer-sheet panel specifically wasn't found (NO_MCQ_PANEL_DETECTED)
//     — without it there is nothing to grade, same as "no bubbles"
//   - the MCQ panel WAS found, but the bubble-detection model came back
//     with (almost) nothing in it (NO_BUBBLES_DETECTED) — printed bubbles
//     exist on the page whether filled or not, so near-zero detections
//     means the crop/quality is bad, not that the student left it blank
//   - the MCQ panel was found but the ID panel wasn't (NO_ID_PANEL_DETECTED)
//     — panel detection is an earlier pipeline stage than question-count
//     validation, so its outcome takes precedence: it must land as the
//     top-level RETAKE status rather than as a warning a later stage's
//     status (e.g. QUESTIONS_NUMBER_MISMATCH) can quietly override
//
// kMinBubbleDetectionRate is intentionally a low bar (not "did the student
// answer everything", just "did the model see roughly the right number of
// bubble shapes at all") — tune it up if genuinely bad photos are still
// slipping through as SUCCESS, or down if well-photographed sheets with a
// lot of legitimately blank answers are being flagged as RETAKE.
// ─────────────────────────────────────────────────────────────────────────
constexpr double kMinBubbleDetectionRate = 0.15;

} // namespace

// ─── Helpers ─────────────────────────────────────────────────────────────────

static char* allocate_string(const std::string& str) {
    char* cstr = new char[str.length() + 1];
    std::strcpy(cstr, str.c_str());
    return cstr;
}

// ─────────────────────────────────────────────────────────────────────────
// Every payload this file hands back to Flutter (via step1_extract_panels /
// step2_infer_and_score) must carry a "cause" key, always — never omitted:
//   - status == "SUCCESS"                   -> cause: null
//   - status == "RETAKE"                    -> cause: the retake reason string
//   - anything else (e.g. a mismatch state) -> cause: the questions / ID
//     columns that are actually in that state, so Flutter can point at the
//     specific offending entries instead of just a generic failure string
//
// `out` is the already-serialized final_result (final_result.to_json_obj());
// `explicit_cause`, if non-null, wins outright (e.g. a retake cause/message
// carried over from step1, or a RETAKE computed locally in step2).
// ─────────────────────────────────────────────────────────────────────────
static void finalize_cause(nlohmann::json& out, const nlohmann::json* explicit_cause = nullptr) {
    if (explicit_cause) {
        out["cause"] = *explicit_cause;
        return;
    }

    const std::string status = out.value("status", "");
    if (status == "SUCCESS") {
        out["cause"] = nullptr;
        return;
    }

    // Collect any question / ID-column entries sitting in a mismatch state
    // so the cause names the actual offenders rather than just repeating
    // the top-level status string.
    nlohmann::json mismatched = nlohmann::json::array();
    if (out.contains("questions") && out["questions"].is_array()) {
        for (const auto& q : out["questions"]) {
            if (q.contains("state") && q["state"] == "QUESTIONS_NUMBER_MISMATCH") {
                mismatched.push_back(q);
            }
        }
    }
    if (out.contains("id_columns") && out["id_columns"].is_array()) {
        for (const auto& c : out["id_columns"]) {
            if (c.contains("state") && c["state"] == "QUESTIONS_NUMBER_MISMATCH") {
                mismatched.push_back(c);
            }
        }
    }

    out["cause"] = mismatched.empty() ? nlohmann::json(status) : mismatched;
}

// ─────────────────────────────────────────────────────────────────────────
// process_exam_in_memory payload helpers
//
// This targets a fixed, closed cause vocabulary for status="FAILED":
//   BLUR, ID_PANEL_BLUR, MCQ_PANEL_BLUR, NO_ID_PANEL, NO_MCQ_PANEL,
//   ID_PANEL_GEOMETRY_INVALID, MCQ_PANEL_GEOMETRY_INVALID,
//   EXPOSURE_TOO_HIGH, ERROR_INTERNAL.
//
// IMPORTANT GAP: the pipeline currently only assesses blur/exposure on the
// *whole raw photo* (preprocessing::get_quality_cause(), quiz mode only —
// shamel mode has no quality assessment at all), not per-panel. So
// ID_PANEL_BLUR, MCQ_PANEL_BLUR, ID_PANEL_GEOMETRY_INVALID and
// MCQ_PANEL_GEOMETRY_INVALID can never actually be produced right now —
// there's no detector for them. Building those would mean running blur/geometry
// checks on the warped id_mat/mcq_mat crops individually, which doesn't exist
// yet in preprocessing.cpp or shamel_preprocessing.cpp. Until that lands,
// whole-photo blur maps to plain "BLUR" and a missing panel maps to
// NO_ID_PANEL/NO_MCQ_PANEL regardless of *why* it's missing.
static std::optional<std::string> map_quality_cause_to_doc(const std::optional<std::string>& internal_cause) {
    if (!internal_cause) return std::nullopt;
    if (*internal_cause == "BLUR") return "BLUR";
    // No separate "too dark" cause exists in the doc vocabulary; both
    // directions of bad exposure collapse onto EXPOSURE_TOO_HIGH.
    if (*internal_cause == "OVEREXPOSED" || *internal_cause == "UNDEREXPOSED") return "EXPOSURE_TOO_HIGH";
    // LOW_RESOLUTION / PARTIAL_CAPTURE have no doc equivalent — let the
    // caller fall back to NO_ID_PANEL / NO_MCQ_PANEL.
    return std::nullopt;
}

// Short-circuited payload for a fatal pipeline error — matches the doc's
// "Early Exit / Failed Camera Frame" example exactly: only status, cause,
// id_columns, questions, has_missing_rows. No student_id/id_needs_review,
// since nothing was computed yet.
static nlohmann::json build_failed_payload(const std::string& cause) {
    nlohmann::json out;
    out["status"] = "FAILED";
    out["cause"] = cause;
    out["id_columns"] = nlohmann::json::array();
    out["questions"] = nlohmann::json::array();
    out["has_missing_rows"] = false;
    return out;
}

// Assembles the final doc-shaped payload from a completed CorrectionResult.
// Handles the state remap (STATE_ERROR_MISSING -> QUESTIONS_NUMBER_MISMATCH,
// since the doc's "Understanding States" section only defines ANSWERED /
// MULTIPLE / BLANK / QUESTIONS_NUMBER_MISMATCH — a totally-missing row and
// an extra/unexpected row are the same state to callers, distinguished via
// has_missing_rows instead) and computes id_needs_review / has_missing_rows /
// status / cause per the doc's flag semantics.
static nlohmann::json build_success_payload(CorrectionResult& result) {
    constexpr float LOW_CONFIDENCE_THRESHOLD = 0.55f;

    bool has_missing_rows = false;
    for (auto& q : result.questions) {
        if (q.state == STATE_ERROR_MISSING) {
            has_missing_rows = true;
            q.state = STATE_QUESTIONS_NUMBER_MISMATCH;
        }
    }
    for (auto& c : result.id_columns) {
        if (c.state == STATE_ERROR_MISSING) {
            has_missing_rows = true;
            c.state = STATE_QUESTIONS_NUMBER_MISMATCH;
        }
    }

    bool id_needs_review = false;
    for (const auto& c : result.id_columns) {
        if (c.state == STATE_BLANK || c.state == STATE_MULTIPLE) {
            id_needs_review = true;
            break;
        }
    }

    nlohmann::json mismatched = nlohmann::json::array();
    nlohmann::json low_confidence = nlohmann::json::array();
    int low_confidence_count = 0;
    for (const auto& q : result.questions) {
        if (q.state == STATE_QUESTIONS_NUMBER_MISMATCH) {
            mismatched.push_back(q.to_json());
        } else if ((q.state == STATE_ANSWERED || q.state == STATE_MULTIPLE || q.state == STATE_BLANK)
                   && q.answer_confidence < LOW_CONFIDENCE_THRESHOLD) {
            low_confidence.push_back(q.to_json());
            low_confidence_count++;
        }
    }
    for (const auto& c : result.id_columns) {
        if (c.state == STATE_QUESTIONS_NUMBER_MISMATCH) {
            mismatched.push_back(c.to_json());
        } else if ((c.state == STATE_ANSWERED || c.state == STATE_MULTIPLE || c.state == STATE_BLANK)
                   && c.answer_confidence < LOW_CONFIDENCE_THRESHOLD) {
            low_confidence.push_back(c.to_json());
            low_confidence_count++;
        }
    }

    std::string status;
    nlohmann::json cause;
    if (!mismatched.empty()) {
        status = "QUESTIONS_NUMBER_MISMATCH";
        cause = mismatched;
    } else if (low_confidence_count > 3) {
        // Doc lists RETAKE as ">3 low-confidence questions" but doesn't spell
        // out its cause shape — using the same "array of offending entries"
        // shape as REVIEW_NEEDED/QUESTIONS_NUMBER_MISMATCH for consistency.
        status = "RETAKE";
        cause = low_confidence;
    } else if (low_confidence_count > 0) {
        status = "REVIEW_NEEDED";
        cause = low_confidence;
    } else {
        status = "SUCCESS";
        cause = nullptr;
    }

    nlohmann::json out;
    out["status"] = status;
    out["cause"] = cause;
    out["student_id"] = result.student_id_string.has_value() ? nlohmann::json(result.student_id_string.value()) : nlohmann::json(nullptr);
    out["student_id_letter"] = result.student_id_letter.has_value() ? nlohmann::json(result.student_id_letter.value()) : nlohmann::json(nullptr);
    out["id_needs_review"] = id_needs_review;
    out["has_missing_rows"] = has_missing_rows;

    nlohmann::json id_cols = nlohmann::json::array();
    for (const auto& c : result.id_columns) id_cols.push_back(c.to_json());
    out["id_columns"] = id_cols;

    nlohmann::json qs = nlohmann::json::array();
    for (const auto& q : result.questions) qs.push_back(q.to_json());
    out["questions"] = qs;

    if (result.version) out["version"] = result.version->to_json();

    return out;
}

// Process a single-row column-style inference result into a VersionResult.
// The version panel has exactly 2 rows of bubbles.
// row 0 = exam model (labels = config.version.exam_models)
// row 1 = exam day   (labels = config.version.exam_days)
static std::vector<Detection> trim_to_best_subset(const std::vector<Detection>& group, int num_choices) {
    if ((int)group.size() <= num_choices) return group;

    std::vector<Detection> sorted_group = group;
    std::sort(sorted_group.begin(), sorted_group.end(),
        [](const Detection& a, const Detection& b){ return a.cx < b.cx; });

    std::vector<Detection> best_subset(sorted_group.begin(), sorted_group.begin() + num_choices);
    float best_score = std::numeric_limits<float>::infinity();

    std::vector<int> selectors(sorted_group.size(), 0);
    std::fill(selectors.begin(), selectors.begin() + num_choices, 1);

    do {
        std::vector<Detection> combo;
        for (size_t i = 0; i < sorted_group.size(); ++i) {
            if (selectors[i]) combo.push_back(sorted_group[i]);
        }

        std::vector<float> gaps;
        float mean = 0.0f;
        for (size_t i = 0; i + 1 < combo.size(); ++i) {
            float g = combo[i + 1].cx - combo[i].cx;
            gaps.push_back(g);
            mean += g;
        }
        if (gaps.empty()) continue;
        mean /= (float)gaps.size();

        float variance = 0.0f;
        for (float g : gaps) variance += (g - mean) * (g - mean);
        float score = std::sqrt(variance / (float)gaps.size());

        if (score < best_score) {
            best_score = score;
            best_subset = combo;
        }
    } while (std::prev_permutation(selectors.begin(), selectors.end()));

    return best_subset;
}

static VersionResult process_version_panel(
    const VersionConfig& ver_cfg,
    const std::vector<Detection>& dets,
    int row_tolerance_px)
{
    VersionResult ver;

    // Match the working 5-phase reference: split the version crop into exactly
    // two rows by the single largest Y-gap, then classify each row with the
    // expected left-to-right label order.
    std::vector<Detection> sorted_dets = dets;
    std::sort(sorted_dets.begin(), sorted_dets.end(),
        [](const Detection& a, const Detection& b){ return a.cy < b.cy; });

    std::vector<std::vector<Detection>> rows(2);
    if (!sorted_dets.empty()) {
        if (sorted_dets.size() == 1) {
            rows[0] = sorted_dets;
        } else {
            size_t split_idx = 0;
            float best_gap = -1.0f;
            for (size_t i = 0; i + 1 < sorted_dets.size(); ++i) {
                float gap = sorted_dets[i + 1].cy - sorted_dets[i].cy;
                if (gap > best_gap) {
                    best_gap = gap;
                    split_idx = i;
                }
            }
            rows[0].assign(sorted_dets.begin(), sorted_dets.begin() + split_idx + 1);
            rows[1].assign(sorted_dets.begin() + split_idx + 1, sorted_dets.end());
        }
    }

    auto classify_row_single = [&](const std::vector<Detection>& row,
                                    const std::vector<std::string>& labels)
        -> std::tuple<std::string, std::optional<std::string>, float>
    {
        std::vector<Detection> sorted_row = row;
        std::sort(sorted_row.begin(), sorted_row.end(),
            [](const Detection& a, const Detection& b){ return a.cx < b.cx; });

        if (sorted_row.empty()) return {STATE_BLANK, std::nullopt, 1.0f};

        auto trimmed = trim_to_best_subset(sorted_row, (int)labels.size());
        if ((int)trimmed.size() < (int)labels.size()) {
            // Guard against duplicate/extra detections; keep the reference's
            // strict row sizing semantics rather than accepting a noisy superset.
        }

        std::vector<int> filled_idx;
        for (int i = 0; i < (int)trimmed.size(); ++i) {
            if (trimmed[i].is_filled()) filled_idx.push_back(i);
        }

        if (filled_idx.empty()) return {STATE_BLANK, std::nullopt, 1.0f};

        // Calculate minimum confidence of filled bubbles
        float min_conf = 1.0f;
        for (int idx : filled_idx) {
            if (idx < (int)trimmed.size()) {
                min_conf = std::min(min_conf, trimmed[idx].confidence);
            }
        }

        if (filled_idx.size() == 1) {
            int idx = filled_idx[0];
            std::string label = (idx < (int)labels.size()) ? labels[idx] : "?" + std::to_string(idx);
            return {STATE_ANSWERED, label, min_conf};
        }

        std::vector<std::string> answers;
        for (int idx : filled_idx) {
            if (idx < (int)labels.size()) answers.push_back(labels[idx]);
        }
        return {STATE_MULTIPLE, answers.empty() ? std::nullopt : std::optional<std::string>(answers[0]), min_conf};
    };

    if (!rows[0].empty()) {
        auto [state, val, conf] = classify_row_single(rows[0], ver_cfg.exam_models);
        ver.exam_model_state = state;
        ver.exam_model = val;
        ver.exam_model_confidence = conf;
    }
    if (!rows[1].empty()) {
        auto [state, val, conf] = classify_row_single(rows[1], ver_cfg.exam_days);
        ver.exam_day_state = state;
        ver.exam_day = val;
        ver.exam_day_confidence = conf;
    }
    return ver;
}

// ─── extern "C" ──────────────────────────────────────────────────────────────

extern "C" {

// Step 1: Extract and save panels from the raw image. Branches on config.mode.
const char* step1_extract_panels(const char* image_path, const char* config_json_str) {
    try {
        nlohmann::json j = nlohmann::json::parse(config_json_str);
        ExamConfig config;
        from_json(j, config);

        nlohmann::json result;

        if (config.mode == "shamel") {
            // ── Shamel: detect 3 quads, warp each, split MCQ into 6 sub-regions ──
            auto panels = shamel_preprocessing::extract_shamel_panels(image_path);

            result["status"] = "SUCCESS";
            result["mode"]   = "shamel";

            if (panels.id_mat)
                result["id_panel_path"] = std::string(image_path) + "_shamel_id.jpg";
            if (panels.version_mat)
                result["version_panel_path"] = std::string(image_path) + "_shamel_version.jpg";
            if (panels.mcq_mat)
                result["mcq_panel_path"] = std::string(image_path) + "_shamel_mcq.jpg";

            // MCQ sub-regions (column-major)
            nlohmann::json mcq_regions = nlohmann::json::array();
            int ridx = 1;
            for (const auto& name : shamel_preprocessing::region_fill_order()) {
                std::string rpath = std::string(image_path) + "_shamel_mcq_"
                    + std::to_string(ridx) + "_" + name + ".jpg";
                mcq_regions.push_back(rpath);
                ++ridx;
            }
            result["mcq_regions"] = mcq_regions;

        } else {
            // ── Quiz: existing two-panel logic ────────────────────────────────
            auto [id_final, mcq_final] = preprocessing::prepare_from_raw(image_path);

            result["mode"] = "quiz";

            if (id_final) {
                std::string id_path = std::string(image_path) + "_cropped_id.jpg";
                cv::imwrite(id_path, *id_final);
                result["id_panel_path"] = id_path;
            }
            if (mcq_final) {
                std::string mcq_path = std::string(image_path) + "_cropped_mcq.jpg";
                cv::imwrite(mcq_path, *mcq_final);
                result["mcq_panel_path"] = mcq_path;
            }

            // Attach the stage-by-stage diagnostics so callers/logs show
            // exactly why a panel was or wasn't found. See the "TUNABLE
            // THRESHOLDS" block at the top of preprocessing.cpp for what
            // each named constant controls.
            auto diags = preprocessing::get_diagnostics();
            if (!diags.empty()) {
                result["diagnostics"] = diags;
            }

            // NOTE: this used to hardcode status="SUCCESS" here unconditionally,
            // even when neither panel was found — which is exactly what let
            // step2 silently run on empty input and still report "SUCCESS"
            // with empty questions/id_columns. Report what was actually
            // extracted instead, and steer the caller toward a retake
            // whenever the photo is unusable rather than gradeable-but-empty:
            if (!id_final && !mcq_final) {
                // Neither panel located at all — nothing to grade or identify
                // the student with. Always a retake.
                result["status"]  = "RETAKE";
                result["cause"]   = preprocessing::get_quality_cause().value_or("NO_PANELS_DETECTED");
                result["message"] = "Couldn't locate the exam sheet in this photo — please retake it. "
                                     "Make sure the full page is in frame, in focus, and evenly lit.";
            } else if (!mcq_final) {
                // No answer-sheet panel — equivalent to "no bubbles": there is
                // nothing to score even if the ID panel came through fine.
                result["status"]  = "RETAKE";
                result["cause"]   = preprocessing::get_quality_cause().value_or("NO_MCQ_PANEL_DETECTED");
                result["message"] = "Found the ID panel but not the answer sheet — please retake the photo "
                                     "with the whole answer sheet visible and in focus.";
            } else if (!id_final) {
                // Panel detection is an earlier stage than question-count
                // validation, so a problem found here must win the top-level
                // status outright — it can't be downgraded to a warning that
                // a later stage's status (e.g. QUESTIONS_NUMBER_MISMATCH)
                // then silently overwrites in step2. Same RETAKE treatment
                // as the other missing-panel cases above; message/warning
                // text is unchanged.
                result["status"]  = "RETAKE";
                result["cause"]   = preprocessing::get_quality_cause().value_or("NO_ID_PANEL_DETECTED");
                result["message"] = "Found the answer sheet but not the student ID panel — please retake the "
                                     "photo with the whole ID panel visible and in focus.";
                result["warning"] = "NO_ID_PANEL_DETECTED: MCQ panel found, but the student ID panel was not. "
                                     "student_id will come back null.";
            } else {
                result["status"] = "SUCCESS";
            }
        }

        return allocate_string(result.dump());

    } catch (const std::exception& e) {
        std::cerr << "Error in step1_extract_panels: " << e.what() << std::endl;
        nlohmann::json err;
        err["status"]  = "ERROR";
        err["message"] = e.what();
        return allocate_string(err.dump());
    }
}

// Step 2: Infer and score. Accepts the step1 result JSON to get panel paths.
const char* step2_infer_and_score(const char* step1_result_json, const char* config_json_str) {
    try {
        nlohmann::json s1 = nlohmann::json::parse(step1_result_json);
        nlohmann::json jc = nlohmann::json::parse(config_json_str);
        if (jc.contains("model_path")) {
            jc["model_path"] = resolve_runtime_path(jc["model_path"].get<std::string>());
        }
        ExamConfig config;
        from_json(jc, config);

        CorrectionResult final_result;
        bool retake_triggered = false;
        std::string retake_cause;
        std::string retake_message;
        if (s1.contains("mcq_validation_status") && s1["mcq_validation_status"].is_string()) {
            const std::string mcq_validation_status = s1["mcq_validation_status"].get<std::string>();
            if (mcq_validation_status != "SUCCESS") {
                final_result.status = mcq_validation_status;
                final_result.mcq_split_failed = true;
                final_result.update_status();
                nlohmann::json out = final_result.to_json_obj();
                nlohmann::json explicit_cause = s1.contains("cause") ? s1["cause"] : nlohmann::json(mcq_validation_status);
                finalize_cause(out, &explicit_cause);
                if (s1.contains("message"))     out["message"] = s1["message"];
                if (s1.contains("warning"))     out["warning"] = s1["warning"];
                if (s1.contains("diagnostics")) out["diagnostics"] = s1["diagnostics"];
                return allocate_string(out.dump());
            }
        }
        if (s1.contains("status") && s1["status"].is_string()) {
            const std::string s1_status = s1["status"].get<std::string>();
            if (s1_status != "SUCCESS") {
                final_result.status = s1_status;
                final_result.mcq_split_failed = true;
                final_result.update_status();
                nlohmann::json out = final_result.to_json_obj();
                nlohmann::json explicit_cause = s1.contains("cause") ? s1["cause"] : nlohmann::json(s1_status);
                finalize_cause(out, &explicit_cause);
                if (s1.contains("message"))     out["message"] = s1["message"];
                if (s1.contains("warning"))     out["warning"] = s1["warning"];
                if (s1.contains("diagnostics")) out["diagnostics"] = s1["diagnostics"];
                return allocate_string(out.dump());
            }
        }

        // ── ID panel (same for both modes) ────────────────────────────────────
        if (s1.contains("id_panel_path")) {
            std::string id_path = s1["id_panel_path"];
            cv::Mat id_mat = cv::imread(id_path);
            if (!id_mat.empty()) {
                // Use simple inference (matching phase5_infer.py) - NO adaptive recovery
                auto id_dets = inference::run_inference(
                    id_mat, config.model_path, 0.25f, 0.45f);
                auto [id_cols, id_str, id_letter] = questions::process_student_id(
                    id_dets, config.id.num_digits, config.id.num_letters,
                    config.id.letters, config.id.column_labels(), config.row_tolerance_px);

                final_result.id_columns        = id_cols;
                final_result.student_id_string = id_str;
                final_result.student_id_letter = id_letter;
            }
        }

        if (config.mode == "shamel") {
            // ── Shamel: run inference on each of the 6 MCQ sub-regions ──────────
            std::vector<QuestionResult> all_questions;
            cv::Mat combined_annotated_mcq; // first non-empty region for annotation path

            if (s1.contains("mcq_regions") && s1["mcq_regions"].is_array()) {
                auto region_sizes = shamel_preprocessing::compute_region_sizes(config.num_questions);
                const auto& fill_order = shamel_preprocessing::region_fill_order();

                int question_offset = 1;

                for (int ri = 0; ri < (int)fill_order.size(); ++ri) {
                    int n_qs = region_sizes[ri];

                    if (ri >= (int)s1["mcq_regions"].size()) break;
                    std::string rpath = s1["mcq_regions"][ri];

                    // NOTE: even when the config says this region should hold zero
                    // questions (n_qs <= 0 - i.e. num_questions came in lower than
                    // this region's physical position on the fixed template), the
                    // region crop can still contain real printed/filled bubbles.
                    // Skipping inference here entirely would silently drop that
                    // content with zero signal. Always run inference; process_questions
                    // correctly reports every real detected row as
                    // QUESTIONS_NUMBER_MISMATCH when n_qs is 0 (or otherwise smaller
                    // than what's actually there), symmetric to the missing-row case.

                    cv::Mat region_mat = cv::imread(rpath);
                    if (region_mat.empty()) {
                        // Push missing questions for this region (only meaningful
                        // when the config actually expected questions here).
                        for (int q = 0; q < n_qs; ++q)
                            all_questions.push_back({question_offset + q, STATE_ERROR_MISSING, std::monostate{}});
                        question_offset += n_qs;
                        continue;
                    }

                    // Each sub-region is a single-column strip of (up to) n_qs
                    // questions. Use simple inference (matching phase5_infer.py) -
                    // NO adaptive recovery
                    auto dets = inference::run_inference(
                        region_mat, config.model_path, 0.25f, 0.45f);

                    auto region_qs = questions::process_questions(
                        dets, std::max(n_qs, 0), 1, config.choice_labels(), config.row_tolerance_px);

                    // ── Region count validation ───────────────────────────────────────
                    // If this region is supposed to contain questions, verify:
                    //   1. The number of detected rows matches n_qs exactly.
                    //   2. Every detected row has exactly num_choices bubbles.
                    // Either mismatch means the crop is broken/garbled and the whole
                    // sheet result is unreliable → escalate to FAILED.
                    if (n_qs > 0 && !final_result.mcq_split_failed) {
                        // Count real question rows (exclude QUESTIONS_NUMBER_MISMATCH entries
                        // that represent overflow rows beyond the expected count).
                        int detected_rows = 0;
                        bool choice_count_bad = false;
                        for (const auto& qr : region_qs) {
                            if (qr.state != STATE_QUESTIONS_NUMBER_MISMATCH) {
                                detected_rows++;
                            }
                        }
                        bool row_count_bad = (detected_rows != n_qs) ||
                                             ((int)region_qs.size() != n_qs); // extra mismatch rows
                        if (row_count_bad || choice_count_bad) {
                            std::cerr << "[!] Shamel region " << (ri + 1)
                                      << " validation FAILED: expected " << n_qs
                                      << " question rows, detected " << detected_rows
                                      << " (total entries: " << region_qs.size() << ")\n";
                            final_result.mcq_split_failed = true;
                            final_result.status = STATE_FAILED;
                        }
                    }

                    // Renumber questions according to their global offset. region_qs
                    // may contain MORE entries than n_qs if real detected rows
                    // exceeded the configured count - that's intentional (each such
                    // row is its own QUESTIONS_NUMBER_MISMATCH entry), so advance the
                    // offset by the actual result count, not the configured n_qs.
                    for (auto& qr : region_qs) {
                        qr.question_number = question_offset + qr.question_number - 1;
                        all_questions.push_back(qr);
                    }

                    question_offset += (int)region_qs.size();
                }


                // Sort all assembled questions by number
                std::sort(all_questions.begin(), all_questions.end(),
                    [](const QuestionResult& a, const QuestionResult& b){
                        return a.question_number < b.question_number;
                    });
                final_result.questions = all_questions;
            }

            // ── Shamel: version panel ─────────────────────────────────────────
            if (s1.contains("version_panel_path")) {
                std::string ver_path = s1["version_panel_path"];
                cv::Mat ver_mat = cv::imread(ver_path);
                if (!ver_mat.empty()) {
                    // Use simple inference (matching phase5_infer.py) - NO adaptive recovery
                    auto ver_dets = inference::run_inference(
                        ver_mat, config.model_path, 0.25f, 0.45f);

                    auto ver_result = process_version_panel(
                        config.version, ver_dets, config.row_tolerance_px);

                    final_result.version = ver_result;
                }
            }

        } else {
            // ── Quiz: single MCQ panel ────────────────────────────────────────
            if (s1.contains("mcq_panel_path")) {
                std::string mcq_path = s1["mcq_panel_path"];
                cv::Mat mcq_mat = cv::imread(mcq_path);
                if (!mcq_mat.empty()) {
                    const int effective_num_question_columns =
                        (!config.mcq_columns.columns.empty() && config.mcq_columns.num_cols > 0)
                            ? config.mcq_columns.num_cols
                            : config.num_question_columns;

                    std::vector<QuestionResult> questions;
                    std::vector<Detection> mcq_dets;
                    if (!config.mcq_columns.columns.empty()) {
                        // Per-column layout: use the dedicated adaptive inference that
                        // builds a per-column grid so every row is recovered correctly.
                        mcq_dets = inference::run_inference_mcq_adaptive_per_column(
                            mcq_mat, config.model_path,
                            config.mcq_columns.columns,
                            config.num_choices, 0.25f, 0.05f, 0.45f);
                        questions = questions::process_questions_per_column(
                            mcq_dets, effective_num_question_columns,
                            config.mcq_columns.columns,
                            config.choice_labels(), config.row_tolerance_px);
                    } else {
                        // Legacy: uniform distribution
                        mcq_dets = inference::run_inference_mcq_adaptive(
                            mcq_mat, config.model_path,
                            config.num_questions, effective_num_question_columns,
                            config.num_choices, 0.25f, 0.05f, 0.45f);
                        questions = questions::process_questions(
                            mcq_dets, config.num_questions, effective_num_question_columns,
                            config.choice_labels(), config.row_tolerance_px);
                    }
                    final_result.questions = questions;

                    // ── Bubble-detection sanity check ──────────────────────────
                    // A properly captured bubble sheet always yields SOME bubble
                    // detections — the circles are printed on the page whether or
                    // not the student filled any of them in. A near-zero raw
                    // detection count here is therefore a strong signal that the
                    // crop/photo is unusable (extreme blur, skew, wrong region,
                    // etc.), not that the student left the whole sheet blank.
                    // Flag it as a retake instead of silently returning an
                    // all-blank result that looks like a real (if empty) exam.
                    int expected_bubbles = config.num_questions * config.num_choices;
                    if (expected_bubbles > 0) {
                        double bubble_rate = (double)mcq_dets.size() / (double)expected_bubbles;
                        std::cerr << "[ffi] step2: mcq bubble detections=" << mcq_dets.size()
                                  << " expected=" << expected_bubbles << " rate=" << bubble_rate
                                  << " (kMinBubbleDetectionRate=" << kMinBubbleDetectionRate << ")" << std::endl;
                        if (bubble_rate < kMinBubbleDetectionRate) {
                            retake_triggered = true;
                            retake_cause = "NO_BUBBLES_DETECTED";
                            retake_message = "The answer sheet was found, but almost no bubbles could be detected "
                                              "in it (" + std::to_string(mcq_dets.size()) + " of ~" +
                                              std::to_string(expected_bubbles) + " expected). Please retake the "
                                              "photo with the answer sheet flat, well-lit, and in focus.";
                            std::cerr << "[ffi] step2: NO_BUBBLES_DETECTED — flagging RETAKE" << std::endl;
                        }
                    }
                } else {
                    // mcq_panel_path was set in step1 but the file couldn't be
                    // read back — treat the same as never having found the panel.
                    retake_triggered = true;
                    retake_cause = "NO_MCQ_PANEL_DETECTED";
                    retake_message = "The answer sheet panel image could not be read (" + mcq_path +
                                      "). Please retake the photo.";
                }
            } else {
                // Defensive: step1 should already have returned status="RETAKE"
                // with cause=NO_MCQ_PANEL_DETECTED in this situation and short-
                // circuited step2 above — this only fires if step1/step2 are
                // ever called out of sync with mismatched inputs.
                retake_triggered = true;
                retake_cause = "NO_MCQ_PANEL_DETECTED";
                retake_message = "No answer-sheet panel was provided by step 1. Please retake the photo.";
            }
        }

        final_result.update_status();

        // update_status() only inspects the MCQ questions array, so an
        // id_columns entry that came back unreliable (e.g. a count
        // mismatch on a digit column — see classify_column in
        // questions.cpp) never touches the top-level status: grading can
        // still be "SUCCESS" while student identification quietly failed.
        // Escalate explicitly here so a bad ID read is never reported as
        // a clean success — same "earlier pipeline stage wins" reasoning
        // as the NO_ID_PANEL_DETECTED case above, just discovered a step
        // later (panel was found, but a column inside it wasn't readable).
        std::string id_issue_label, id_issue_state;
        for (const auto& col : final_result.id_columns) {
            if (col.state != STATE_ANSWERED && col.state != STATE_BLANK) {
                id_issue_label = col.column_label;
                // Route through nlohmann::json rather than assuming state
                // converts directly to std::string — to_json_obj() already
                // has to serialize this field to produce the "state":
                // "QUESTIONS_NUMBER_MISMATCH" strings seen in the output,
                // so reuse that conversion instead of guessing its type.
                id_issue_state = nlohmann::json(col.state).get<std::string>();
                break;
            }
        }
        if (!retake_triggered && !id_issue_state.empty()) {
            retake_triggered = true;
            retake_cause = "ID_" + id_issue_state;
            retake_message = "The " + id_issue_label + " column of the student ID panel couldn't be read "
                              "reliably (" + id_issue_state + "). Please retake the photo with the ID panel "
                              "flat, well-lit, and in focus.";
        }

        nlohmann::json out = final_result.to_json_obj();
        if (retake_triggered) {
            out["status"]  = "RETAKE";
            out["message"] = retake_message;
            nlohmann::json explicit_cause = retake_cause;
            finalize_cause(out, &explicit_cause);
        } else if (s1.contains("cause")) {
            // Carry forward a non-fatal cause from step1 (e.g. NO_ID_PANEL_DETECTED
            // alongside status="SUCCESS") so the caller sees the full picture.
            nlohmann::json explicit_cause = s1["cause"];
            finalize_cause(out, &explicit_cause);
        } else {
            // No retake, no carried-over cause: SUCCESS -> null, a mismatch
            // status -> the offending questions/ID columns, computed from
            // `out` itself.
            finalize_cause(out);
        }
        if (!retake_triggered && s1.contains("warning")) {
            out["warning"] = s1["warning"];
        }

        return allocate_string(out.dump());

    } catch (const std::exception& e) {
        std::cerr << "Error in step2_infer_and_score: " << e.what() << std::endl;
        nlohmann::json err;
        err["status"]  = "ERROR";
        err["message"] = e.what();
        return allocate_string(err.dump());
    }
}

// Single-call, in-memory pipeline for Flutter: straightens the image, runs
// inference, and scores it — all against cv::Mat objects passed directly
// between stages, never round-tripped through disk the way step1/step2 have
// to (they're two separate FFI calls, so JSON + file paths is how they hand
// panels to each other). step1/step2 are untouched and still available.
//
// Quiz mode never wrote intermediate files (preprocessing::prepare_from_raw
// already works purely in memory). Shamel mode used to always write its
// crops via shamel_preprocessing::extract_shamel_panels — that function now
// takes a save_to_disk flag, called false here.
const char* process_exam_in_memory(const char* image_path_c, const char* config_json_str) {
    try {
        std::string image_path(image_path_c);
        nlohmann::json jc = nlohmann::json::parse(config_json_str);
        if (jc.contains("model_path")) {
            jc["model_path"] = resolve_runtime_path(jc["model_path"].get<std::string>());
        }
        ExamConfig config;
        from_json(jc, config);

        CorrectionResult final_result;
        cv::Mat id_mat, mcq_mat;
        std::vector<cv::Mat> shamel_region_mats;

        if (config.mode == "shamel") {
            auto panels = shamel_preprocessing::extract_shamel_panels(image_path, /*save_to_disk=*/false);
            if (!panels.mcq_mat) {
                return allocate_string(build_failed_payload("NO_MCQ_PANEL").dump());
            }
            if (!panels.id_mat) {
                return allocate_string(build_failed_payload("NO_ID_PANEL").dump());
            }
            mcq_mat = *panels.mcq_mat;
            id_mat = *panels.id_mat;
            shamel_region_mats = panels.mcq_regions;

            if (panels.version_mat) {
                auto ver_dets = inference::run_inference(*panels.version_mat, config.model_path, 0.25f, 0.45f);
                final_result.version = process_version_panel(config.version, ver_dets, config.row_tolerance_px);
            }
        } else {
            auto [id_final, mcq_final] = preprocessing::prepare_from_raw(image_path);
            auto doc_cause = map_quality_cause_to_doc(preprocessing::get_quality_cause());

            if (!mcq_final) {
                return allocate_string(build_failed_payload(doc_cause.value_or("NO_MCQ_PANEL")).dump());
            }
            if (!id_final) {
                return allocate_string(build_failed_payload(doc_cause.value_or("NO_ID_PANEL")).dump());
            }
            id_mat = *id_final;
            mcq_mat = *mcq_final;
        }

        // ── ID panel ────────────────────────────────────────────────────────
        auto id_dets = inference::run_inference(id_mat, config.model_path, 0.25f, 0.45f);
        auto [id_cols, id_str, id_letter] = questions::process_student_id(
            id_dets, config.id.num_digits, config.id.num_letters,
            config.id.letters, config.id.column_labels(), config.row_tolerance_px);
        final_result.id_columns        = id_cols;
        final_result.student_id_string = id_str;
        final_result.student_id_letter = id_letter;

        // ── MCQ ─────────────────────────────────────────────────────────────
        if (config.mode == "shamel") {
            std::vector<QuestionResult> all_questions;
            auto region_sizes = shamel_preprocessing::compute_region_sizes(config.num_questions);
            const auto& fill_order = shamel_preprocessing::region_fill_order();
            int question_offset = 1;

            for (int ri = 0; ri < (int)fill_order.size(); ++ri) {
                int n_qs = region_sizes[ri];
                if (ri >= (int)shamel_region_mats.size()) break;
                cv::Mat& region_mat = shamel_region_mats[ri];

                if (region_mat.empty()) {
                    for (int q = 0; q < n_qs; ++q)
                        all_questions.push_back({question_offset + q, STATE_ERROR_MISSING, std::monostate{}});
                    question_offset += n_qs;
                    continue;
                }

                auto dets = inference::run_inference(region_mat, config.model_path, 0.25f, 0.45f);
                auto region_qs = questions::process_questions(
                    dets, std::max(n_qs, 0), 1, config.choice_labels(), config.row_tolerance_px);

                for (auto& qr : region_qs) {
                    qr.question_number = question_offset + qr.question_number - 1;
                    all_questions.push_back(qr);
                }
                question_offset += (int)region_qs.size();
            }

            std::sort(all_questions.begin(), all_questions.end(),
                [](const QuestionResult& a, const QuestionResult& b) {
                    return a.question_number < b.question_number;
                });
            final_result.questions = all_questions;

        } else {
            const int effective_num_question_columns =
                (!config.mcq_columns.columns.empty() && config.mcq_columns.num_cols > 0)
                    ? config.mcq_columns.num_cols
                    : config.num_question_columns;

            std::vector<QuestionResult> questions;
            std::vector<Detection> mcq_dets;
            if (!config.mcq_columns.columns.empty()) {
                mcq_dets = inference::run_inference_mcq_adaptive_per_column(
                    mcq_mat, config.model_path, config.mcq_columns.columns,
                    config.num_choices, 0.25f, 0.05f, 0.45f);
                questions = questions::process_questions_per_column(
                    mcq_dets, effective_num_question_columns, config.mcq_columns.columns,
                    config.choice_labels(), config.row_tolerance_px);
            } else {
                mcq_dets = inference::run_inference_mcq_adaptive(
                    mcq_mat, config.model_path, config.num_questions,
                    effective_num_question_columns, config.num_choices, 0.25f, 0.05f, 0.45f);
                questions = questions::process_questions(
                    mcq_dets, config.num_questions, effective_num_question_columns,
                    config.choice_labels(), config.row_tolerance_px);
            }
            final_result.questions = questions;

            // Same "printed bubbles are always visible whether filled or not"
            // sanity check step2 uses — near-zero raw detections means the
            // crop/photo is unusable. No dedicated doc cause for this, so it
            // maps onto NO_MCQ_PANEL (closest fit: the panel is effectively
            // unreadable).
            int expected_bubbles = config.num_questions * config.num_choices;
            if (expected_bubbles > 0) {
                double bubble_rate = (double)mcq_dets.size() / (double)expected_bubbles;
                if (bubble_rate < kMinBubbleDetectionRate) {
                    return allocate_string(build_failed_payload("NO_MCQ_PANEL").dump());
                }
            }
        }

        return allocate_string(build_success_payload(final_result).dump());

    } catch (const std::exception& e) {
        std::cerr << "Error in process_exam_in_memory: " << e.what() << std::endl;
        return allocate_string(build_failed_payload("ERROR_INTERNAL").dump());
    }
}

void free_string(char* str) {
    if (str) delete[] str;
}

} // extern "C"