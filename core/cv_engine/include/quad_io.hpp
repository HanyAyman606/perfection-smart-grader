// quad_io.hpp -- internal replacement for numpy's .npy quad files.
//
// stage1_boxes.py saves each detected panel's 4 corners with
// np.save(...).npy; that format is numpy-specific and has no reason to
// survive the port. Here a quad is just 4 (x,y) float64 pairs, always in
// TL, TR, BR, BL order (same order order_pts() produces), written raw to
// disk and read back the same way.
#pragma once

#include <array>
#include <fstream>
#include <stdexcept>
#include <vector>

#include <opencv2/opencv.hpp>

struct Quad {
    cv::Point2f tl, tr, br, bl;

    std::vector<cv::Point2f> as_points_f() const { return {tl, tr, br, bl}; }
    std::vector<cv::Point> as_points_i() const {
        return {cv::Point(cvRound(tl.x), cvRound(tl.y)), cv::Point(cvRound(tr.x), cvRound(tr.y)),
                cv::Point(cvRound(br.x), cvRound(br.y)), cv::Point(cvRound(bl.x), cvRound(bl.y))};
    }
};

inline void write_quad(const std::filesystem::path& path, const Quad& q) {
    std::ofstream f(path, std::ios::binary);
    if (!f) throw std::runtime_error("could not write quad file: " + path.string());
    double buf[8] = {q.tl.x, q.tl.y, q.tr.x, q.tr.y, q.br.x, q.br.y, q.bl.x, q.bl.y};
    f.write(reinterpret_cast<const char*>(buf), sizeof(buf));
}

inline bool read_quad(const std::filesystem::path& path, Quad& out) {
    std::ifstream f(path, std::ios::binary);
    if (!f) return false;
    double buf[8];
    f.read(reinterpret_cast<char*>(buf), sizeof(buf));
    if (!f) return false;
    out.tl = {static_cast<float>(buf[0]), static_cast<float>(buf[1])};
    out.tr = {static_cast<float>(buf[2]), static_cast<float>(buf[3])};
    out.br = {static_cast<float>(buf[4]), static_cast<float>(buf[5])};
    out.bl = {static_cast<float>(buf[6]), static_cast<float>(buf[7])};
    return true;
}
