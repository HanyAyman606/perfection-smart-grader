// common.hpp -- small shared helpers used by every stage (directory
// listing/creation, mirrors of Python's glob.glob(*.jpg) etc).
#pragma once

#include <algorithm>
#include <filesystem>
#include <string>
#include <vector>

namespace fs = std::filesystem;

namespace common {

inline void ensure_dir(const fs::path& p) {
    std::error_code ec;
    fs::create_directories(p, ec);
}

// Returns sorted list of files directly under `dir` whose extension
// (case-insensitive) matches one of `exts` (each given with leading dot,
// e.g. {".jpg", ".jpeg", ".png"}).
inline std::vector<fs::path> glob_ext(const fs::path& dir, const std::vector<std::string>& exts) {
    std::vector<fs::path> out;
    if (!fs::exists(dir)) return out;
    for (const auto& entry : fs::directory_iterator(dir)) {
        if (!entry.is_regular_file()) continue;
        std::string ext = entry.path().extension().string();
        std::transform(ext.begin(), ext.end(), ext.begin(), ::tolower);
        if (std::find(exts.begin(), exts.end(), ext) != exts.end()) {
            out.push_back(entry.path());
        }
    }
    std::sort(out.begin(), out.end());
    return out;
}

// Returns sorted list of files directly under `dir` whose filename ends
// with `suffix` (mirrors glob.glob(dir/"*suffix")).
inline std::vector<fs::path> glob_suffix(const fs::path& dir, const std::string& suffix) {
    std::vector<fs::path> out;
    if (!fs::exists(dir)) return out;
    for (const auto& entry : fs::directory_iterator(dir)) {
        if (!entry.is_regular_file()) continue;
        std::string name = entry.path().filename().string();
        if (name.size() >= suffix.size() &&
            name.compare(name.size() - suffix.size(), suffix.size(), suffix) == 0) {
            out.push_back(entry.path());
        }
    }
    std::sort(out.begin(), out.end());
    return out;
}

inline std::string strip_suffix(const std::string& s, const std::string& suffix) {
    if (s.size() >= suffix.size() && s.compare(s.size() - suffix.size(), suffix.size(), suffix) == 0) {
        return s.substr(0, s.size() - suffix.size());
    }
    return s;
}

inline bool ends_with(const std::string& s, const std::string& suffix) {
    return s.size() >= suffix.size() && s.compare(s.size() - suffix.size(), suffix.size(), suffix) == 0;
}

inline bool contains(const std::string& s, const std::string& sub) {
    return s.find(sub) != std::string::npos;
}

inline std::string replace_all(std::string s, const std::string& from, const std::string& to) {
    if (from.empty()) return s;
    size_t pos = 0;
    while ((pos = s.find(from, pos)) != std::string::npos) {
        s.replace(pos, from.size(), to);
        pos += to.size();
    }
    return s;
}

}  // namespace common
