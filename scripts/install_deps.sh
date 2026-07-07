#!/usr/bin/env bash
# install_deps.sh — install system packages and Python dependencies
# Usage: bash scripts/install_deps.sh
# Run from the project root.
set -euo pipefail

echo "=== Voice Assistant — dependency installation ==="

# Ensure we are running inside the venv
if [[ -z "${VIRTUAL_ENV:-}" ]]; then
    echo "ERROR: Activate your virtual environment first:"
    echo "  source .venv/bin/activate"
    exit 1
fi

PYTHON="${VIRTUAL_ENV}/bin/python"

echo "Using Python:"
$PYTHON --version

# ── System packages ────────────────────────────────────────────────────────
echo "[1/3] Installing system packages …"
sudo apt install -y \
    build-essential \
    libportaudio2 \
    portaudio19-dev \
    python3-dev \
    xdotool

# ── Python packages ────────────────────────────────────────────────────────
echo "[2/3] Installing Python packages …"

$PYTHON -m pip install --upgrade pip setuptools wheel

# CPU-only PyTorch for AMD Ryzen 7 7800X3D (no NVIDIA GPU)
$PYTHON -m pip install \
    torch==2.4.0 \
    torchaudio==2.4.0 \
    --index-url https://download.pytorch.org/whl/cpu

$PYTHON -m pip install \
    "faster-whisper>=1.1.0" \
    "RealtimeSTT>=0.3.90" \
    "sounddevice>=0.5.0" \
    "numpy>=2.0.0" \
    "pyyaml>=6.0" \
    "speechbrain>=1.0.2" \
    "huggingface-hub>=0.24.0" \
    "PyQt6>=6.7.0" \
    "pyqtgraph>=0.13.7" \
    "pynput>=1.7.7" \
    "qasync>=0.27.0" \
    "pytest>=8.0.0" \
    "pytest-asyncio>=0.23.0" \
    "PyAudio>=0.2.14" \
    "typeguard>=4.0.0" \
    "torch>=2.0.0" \
    "torchaudio>=2.0.0" \


# ── Silero VAD pre-cache ───────────────────────────────────────────────────
# torch.hub downloads the model on first use; pre-cache it now so the
# first run of the assistant does not wait.
echo "[3/3] Pre-caching Silero VAD model …"
python - <<'EOF'
import torch
torch.hub.load(
    "snakers4/silero-vad",
    "silero_vad",
    force_reload=False,
    trust_repo=True,
    verbose=True,
)
print("Silero VAD cached ✓")
EOF

echo ""
echo "Installation complete."
echo "Whisper model files download to ~/.cache/huggingface/ on first transcription."
