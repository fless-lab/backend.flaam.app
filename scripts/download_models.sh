#!/bin/bash
set -euo pipefail

# Download ONNX models for photo moderation pipeline.
# Run once at deploy time or when activating PHOTO_MODERATION_MODE=onnx.

MODELS_DIR="$(cd "$(dirname "$0")/.." && pwd)/models"
mkdir -p "$MODELS_DIR"

echo "=== YuNet face detection (~230KB) ==="
wget -q --show-progress -O "$MODELS_DIR/yunet_face.onnx" \
  "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"

echo "=== ArcFace face embedding (~120MB) ==="
echo "Download manually from: https://github.com/deepinsight/insightface/releases"
echo "  1. Download buffalo_l.zip"
echo "  2. Extract w600k_r50.onnx"
echo "  3. Rename to $MODELS_DIR/arcface_r50.onnx"
echo ""
echo "Alternative (lighter, ~3MB): https://github.com/onnx/models/tree/main/validated/vision/body_analysis/arcface"

echo "=== NSFW detector (~24MB) ==="
echo "Download manually from: https://github.com/notAI-tech/NudeNet"
echo "  1. pip install nudenet"
echo "  2. Export to ONNX or use pre-converted model"
echo "  3. Place as $MODELS_DIR/nsfw_detector.onnx"
echo ""
echo "Alternative: https://github.com/GantMan/nsfw_model (Keras, convert with tf2onnx)"

echo ""
echo "Models directory: $MODELS_DIR"
ls -lh "$MODELS_DIR"
