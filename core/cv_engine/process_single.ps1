param(
    [Parameter(Mandatory=$true)]
    [string]$ImagePath,

    [Parameter(Mandatory=$true)]
    [string]$ConfigPath,

    [Parameter(Mandatory=$false)]
    [string]$ModelPath = "shamel.onnx",

    [Parameter(Mandatory=$false)]
    [string]$OutputDir = "results"
)

$ErrorActionPreference = "Stop"

# Create a unique temporary working directory
$tempDir = Join-Path $env:TEMP "exam_pipeline_$(New-Guid)"
New-Item -ItemType Directory -Force -Path "$tempDir\input" | Out-Null

Write-Host "Setting up temporary workspace at $tempDir..."

# Copy the specific image, config, and model to the temporary workspace
Copy-Item $ImagePath -Destination "$tempDir\input\"
Copy-Item $ConfigPath -Destination "$tempDir\exam_config.json"

if (Test-Path $ModelPath) {
    Copy-Item $ModelPath -Destination "$tempDir\shamel.onnx"
} else {
    Write-Host "WARNING: Could not find model at '$ModelPath'. The pipeline will likely fail at Stage 5." -ForegroundColor Yellow
}

# Run the pipeline against this temporary workspace
$exePath = Resolve-Path ".\build\run_pipeline.exe" -ErrorAction SilentlyContinue
if (-not $exePath) {
    # Try Release folder if using MSVC/vcpkg
    $exePath = Resolve-Path ".\build\Release\run_pipeline.exe" -ErrorAction SilentlyContinue
}

Write-Host "Running pipeline..."
& $exePath $tempDir

# Copy the results to the requested output directory
if (Test-Path "$tempDir\stage6") {
    New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
    
    $imageName = [System.IO.Path]::GetFileNameWithoutExtension($ImagePath)
    $finalOutput = "$OutputDir\${imageName}_results"
    
    Write-Host "Saving results to $finalOutput..."
    New-Item -ItemType Directory -Force -Path $finalOutput | Out-Null
    
    # Copy all stage outputs so you can see the intermediate steps
    Copy-Item "$tempDir\*" -Destination $finalOutput -Recurse
    
    Write-Host "Done! Final grades are in $finalOutput\stage6\final_grades.json" -ForegroundColor Green
} else {
    Write-Host "Pipeline failed to produce final output." -ForegroundColor Red
}

# Clean up
Remove-Item -Recurse -Force $tempDir
