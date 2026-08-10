#include "ffi.h"
#include <nlohmann/json.hpp>
#include <iostream>
#include <fstream>
#include <sstream>

int main(int argc, char** argv) {
    if (argc < 3) {
        std::cerr << "Usage: " << argv[0] << " <image_path> <config_json>" << std::endl;
        return 1;
    }

    std::string image_path = argv[1];
    std::string config_json_path = argv[2];

    std::ifstream t(config_json_path);
    std::stringstream buffer;
    buffer << t.rdbuf();
    std::string config_json_str = buffer.str();

    // Step 1: Extract panels
    const char* step1_res_cstr = step1_extract_panels(image_path.c_str(), config_json_str.c_str());
    if (!step1_res_cstr) {
        std::cerr << "Error: step1_extract_panels returned null" << std::endl;
        return 1;
    }

    std::string step1_res = step1_res_cstr;
    free_string(const_cast<char*>(step1_res_cstr));

    nlohmann::json s1_json = nlohmann::json::parse(step1_res);
    if (s1_json["status"] != "SUCCESS") {
        std::cerr << "Step 1 Failed: " << step1_res << std::endl;
        return 1;
    }

    std::string id_path = s1_json.contains("id_panel_path") ? s1_json["id_panel_path"].get<std::string>() : "";
    std::string mcq_path = s1_json.contains("mcq_panel_path") ? s1_json["mcq_panel_path"].get<std::string>() : "";

    std::cout << "--- Step 1 Success ---" << std::endl;
    std::cout << step1_res << std::endl;

    // Step 2: Infer and score
    const char* step2_res_cstr = step2_infer_and_score(id_path.c_str(), mcq_path.c_str(), config_json_str.c_str());
    if (!step2_res_cstr) {
        std::cerr << "Error: step2_infer_and_score returned null" << std::endl;
        return 1;
    }

    std::string step2_res = step2_res_cstr;
    free_string(const_cast<char*>(step2_res_cstr));

    std::cout << "--- Step 2 Success ---" << std::endl;
    std::cout << step2_res << std::endl;

    return 0;
}
