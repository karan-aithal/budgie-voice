#!/usr/bin/env bash
# Myna — install system packages + Python deps + pre-cache models
# Usage: bash scripts/install_deps.sh
# Run from project root with venv active.
set -euo pipefail

echo "=== Myna — dependency installation ==="

if [[ -z "${VIRTUAL_ENV:-}" ]]; then
    echo "ERROR: Activate venv first:  source .venv/bin/activate"
    exit 1
fi

PYTHON="${VIRTUAL_ENV}/bin/python"
echo "Python: $($PYTHON --version)"

# ── System packages ────────────────────────────────────────────────────────
echo "[1/4] System packages …"
sudo apt install -y \
    xdotool ibus python3-ibus libportaudio2 portaudio19-dev \
    libnotify-bin python3-pyqt6 python3-dbus python3-gi \
    build-essential python3-dev

# ── Python packages ────────────────────────────────────────────────────────
echo "[2/4] Python packages …"
$PYTHON -m pip install --upgrade pip setuptools wheel
$PYTHON -m pip install -r "$(dirname "$0")/../requirements.txt"

# ── Pre-download Whisper model ─────────────────────────────────────────────
echo "[3/4] Pre-downloading Whisper 'small' model …"
$PYTHON - << 'EOF'
import os
from faster_whisper import WhisperModel
WhisperModel(
    "small", device="cpu", compute_type="int8",
    download_root=os.path.expanduser("~/.cache/myna/models"),
)
print("Whisper 'small' cached ✓")
EOF

# ── Pre-cache Silero VAD ───────────────────────────────────────────────────
echo "[4/4] Pre-caching Silero VAD …"
$PYTHON - << 'EOF'
import torch
torch.hub.load("snakers4/silero-vad", "silero_vad",
               force_reload=False, verbose=False, trust_repo=True)
print("Silero VAD cached ✓")
EOF

echo ""
echo "=== Installation complete ==="
echo "Next: python -m myna.speaker.enroller   # enroll your voice"
echo "      python -m myna.main               # run Myna"
