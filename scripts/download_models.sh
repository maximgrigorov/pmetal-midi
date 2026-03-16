#!/bin/bash
# Download UVR audio separation models to /data/models/
# These ONNX models can be used for stem separation if audio-separator is installed.
# Run via: make download-models

set -e

MODEL_DIR="${PMETAL_DATA_DIR:-/data}/models"
mkdir -p "$MODEL_DIR"

MODELS=(
    "UVR-MDX-NET-Inst_HQ_3.onnx|https://github.com/TRvlvr/model_repo/releases/download/all_public_uvr_models/UVR-MDX-NET-Inst_HQ_3.onnx"
    "UVR_MDXNET_KARA_2.onnx|https://github.com/TRvlvr/model_repo/releases/download/all_public_uvr_models/UVR_MDXNET_KARA_2.onnx"
)

echo "=== Downloading UVR audio separation models ==="
echo "Target: $MODEL_DIR"
echo ""

for entry in "${MODELS[@]}"; do
    name="${entry%%|*}"
    url="${entry##*|}"
    dest="$MODEL_DIR/$name"

    if [ -f "$dest" ]; then
        size=$(stat -c%s "$dest" 2>/dev/null || stat -f%z "$dest" 2>/dev/null)
        echo "  SKIP: $name (already exists, ${size} bytes)"
        continue
    fi

    echo "  Downloading $name ..."
    curl -L --fail --progress-bar -o "$dest" "$url"
    size=$(stat -c%s "$dest" 2>/dev/null || stat -f%z "$dest" 2>/dev/null)
    echo "  OK: $name (${size} bytes)"
done

echo ""
echo "=== Model download complete ==="
ls -lh "$MODEL_DIR/"
