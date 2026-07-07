#!/usr/bin/env bash
# install_deps.sh — install system packages and Python dependencies
# Usage: bash scripts/install_deps.sh
# Run from the project root.
set -euo pipefail

echo "=== Voice Assistant — dependency installation ==="

# ── System packages ────────────────────────────────────────────────────────
echo "[1/3] Installing system packages …"
sudo apt install -y \
    libportaudio2 \
    portaudio19-dev \
    python3-dev \
    xdotool

# ── Python packages ────────────────────────────────────────────────────────
echo "[2/3] Installing Python packages …"
pip install --upgrade pip
pip install \
    "faster-whisper>=1.0.0" \
    "sounddevice>=0.4.6" \
    "numpy>=1.26.0" \
    "pyyaml>=6.0" \
    "torch>=2.0.0" \
    "torchaudio>=2.0.0" \
    "speechbrain>=1.0.0" \
    "PyQt6>=6.6.0" \
    "pyqtgraph>=0.13.3" \
    "pynput>=1.7.6" \
    "qasync>=0.27.0" \
    "pytest>=8.0.0" \
    "pytest-asyncio>=0.23.0"

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
    verbose=True,
)
print("Silero VAD cached ✓")
EOF

echo ""
echo "Installation complete."
echo "Whisper model files download to ~/.cache/huggingface/ on first transcription."
