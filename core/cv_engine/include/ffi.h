#pragma once

extern "C" {
    const char* step1_extract_panels(const char* image_path, const char* config_json);
    const char* step2_infer_and_score(const char* id_panel_path, const char* mcq_panel_path, const char* config_json);
    void free_string(char* str);
}
