#include "inference.h"
#include <opencv2/opencv.hpp>
#include <iostream>
#include <iomanip>

int main() {
    std::string img_path = "C:/Users/Yassin/Downloads/opencv without the model/New folder/input/shamel_1.jpeg_shamel_id.jpg";
    std::string model_path = "C:/Users/Yassin/Downloads/shamel.onnx";
    cv::Mat img = cv::imread(img_path);
    if (img.empty()) { std::cerr << "No img\n"; return 1; }

    Ort::Env env(ORT_LOGGING_LEVEL_WARNING, "test");
    Ort::SessionOptions session_options;
    Ort::Session sess(env, std::wstring(model_path.begin(), model_path.end()).c_str(), session_options);

    auto dets = inference::infer_single(sess, img, 0.25f);
    std::cout << "Detected " << dets.size() << " bubbles\n";
    for (const auto& d : dets) {
        float x1 = d.cx - d.w/2;
        float y1 = d.cy - d.h/2;
        float x2 = d.cx + d.w/2;
        float y2 = d.cy + d.h/2;
        std::cout << "C++: (" << x1 << ", " << y1 << ", " << x2 << ", " << y2 << ", " << d.class_id << ", " << d.confidence << ")\n";
    }
    return 0;
}
