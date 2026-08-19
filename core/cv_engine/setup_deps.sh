#!/usr/bin/env bash
# Fetches ONNX Runtime (C++ prebuilt) into third_party/, and installs
# OpenCV + nlohmann-json via apt if they're missing. Run once before
# building.
set -euo pipefail
cd "$(dirname "$0")"

ORT_VERSION="1.18.0"
ORT_DIR="third_party/onnxruntime-linux-x64-${ORT_VERSION}"

if [ ! -d "$ORT_DIR" ]; then
  echo "Downloading ONNX Runtime ${ORT_VERSION}..."
  mkdir -p third_party
  curl -sL -o /tmp/onnxruntime.tgz \
    "https://github.com/microsoft/onnxruntime/releases/download/v${ORT_VERSION}/onnxruntime-linux-x64-${ORT_VERSION}.tgz"
  tar xzf /tmp/onnxruntime.tgz -C third_party
  rm /tmp/onnxruntime.tgz
else
  echo "ONNX Runtime already present at $ORT_DIR"
fi

if ! dpkg -s libopencv-dev >/dev/null 2>&1; then
  echo "Installing libopencv-dev..."
  sudo apt-get update -qq
  sudo apt-get install -y libopencv-dev
fi

if ! dpkg -s nlohmann-json3-dev >/dev/null 2>&1; then
  echo "Installing nlohmann-json3-dev..."
  sudo apt-get install -y nlohmann-json3-dev
fi

echo "Dependencies ready. Now run:"
echo "  cmake -B build -DCMAKE_BUILD_TYPE=Release"
echo "  cmake --build build -j\$(nproc)"
