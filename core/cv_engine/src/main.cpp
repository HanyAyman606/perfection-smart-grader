#include "ffi.h"
#include <nlohmann/json.hpp>
#include <iostream>
#include <fstream>
#include <sstream>
#include <chrono>

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

    // Run combined in-memory processing
    auto t_start = std::chrono::high_resolution_clock::now();
    const char* res_cstr = process_exam_in_memory(image_path.c_str(), config_json_str.c_str());
    auto t_end = std::chrono::high_resolution_clock::now();
    double time_ms = std::chrono::duration<double, std::milli>(t_end - t_start).count();

    if (!res_cstr) {
        std::cerr << "Error: process_exam_in_memory returned null" << std::endl;
        return 1;
    }

    std::string res = res_cstr;
    free_string(const_cast<char*>(res_cstr));

    std::cout << "--- Pipeline Success ---" << std::endl;
    std::cout << res << std::endl;

    std::cout << "\n=== TIMING REPORT ===" << std::endl;
    std::cout << "Total Time: " << time_ms << " ms" << std::endl;

    return 0;
}
