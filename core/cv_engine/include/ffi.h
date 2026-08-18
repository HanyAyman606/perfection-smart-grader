#pragma once

extern "C" {
    // Step 1: Extract panels from the raw camera image.
    // Returns a JSON string. Fields depend on mode (quiz/shamel) - see API_DOCUMENTATION.md.
    const char* step1_extract_panels(const char* image_path, const char* config_json);

    // Step 2: Run AI inference and score.
    // step1_result_json: the full JSON string returned by step1_extract_panels.
    // config_json:       same config string passed to step 1.
    const char* step2_infer_and_score(const char* step1_result_json, const char* config_json);

    // Single-call wrapper for Flutter: runs step1 then step2 internally with
    // the same config and returns step2's final JSON directly. step1/step2
    // remain exported on their own (e.g. for tooling/tests/debugging) but
    // the app should call this one function instead of chaining the two.
    const char* process_exam_in_memory(const char* image_path, const char* config_json);

    void free_string(char* str);
}