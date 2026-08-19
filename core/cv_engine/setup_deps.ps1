$ErrorActionPreference = "Stop"

$ORT_VERSION = "1.18.0"
$ORT_DIR = "third_party\onnxruntime-win-x64-$ORT_VERSION"

Set-Location $PSScriptRoot

if (-Not (Test-Path $ORT_DIR)) {
    Write-Host "Downloading ONNX Runtime $ORT_VERSION..."
    New-Item -ItemType Directory -Force -Path third_party | Out-Null
    $zipPath = "$env:TEMP\onnxruntime.zip"
    Invoke-WebRequest -Uri "https://github.com/microsoft/onnxruntime/releases/download/v${ORT_VERSION}/onnxruntime-win-x64-${ORT_VERSION}.zip" -OutFile $zipPath
    Write-Host "Extracting..."
    Expand-Archive -Path $zipPath -DestinationPath third_party -Force
    Remove-Item $zipPath
} else {
    Write-Host "ONNX Runtime already present at $ORT_DIR"
}

Write-Host ""
Write-Host "Dependencies ready. Note: You must install OpenCV and nlohmann-json yourself."
Write-Host "We recommend using vcpkg:"
Write-Host "  vcpkg install opencv:x64-windows nlohmann-json:x64-windows"
Write-Host ""
Write-Host "Then build with CMake:"
Write-Host "  cmake -B build -DCMAKE_BUILD_TYPE=Release -DCMAKE_TOOLCHAIN_FILE=[path-to-vcpkg]/scripts/buildsystems/vcpkg.cmake"
Write-Host "  cmake --build build --config Release"
