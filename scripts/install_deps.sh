#!/usr/bin/env bash
set -euo pipefail

echo "=== Budgie — dependency installation ==="

if [[ -z "${VIRTUAL_ENV:-}" ]]; then
    echo "ERROR: Activate the virtual environment first:"
    echo "  source .venv/bin/activate"
    exit 1
fi

PYTHON="${VIRTUAL_ENV}/bin/python"
echo "Python: $($PYTHON --version)"

# ────────────────────────────────────────────────────────────────
# 1. System packages
# ────────────────────────────────────────────────────────────────
echo "[1/5] Installing system packages..."

sudo apt update

sudo apt install -y \
    build-essential \
    pkg-config \
    cmake \
    meson \
    ninja-build \
    git \
    xdotool \
    ydotool \
    gir1.2-glib-2.0 \
    gobject-introspection \
    libgirepository1.0-dev \
    libglib2.0-dev \
    libcairo2-dev \
    libffi-dev \
    libdbus-1-dev \
    libportaudio2 \
    portaudio19-dev \
    libnotify-bin \
    python3-dev

# ────────────────────────────────────────────────────────────────
# 2. Python packages
# ────────────────────────────────────────────────────────────────
echo "[2/5] Installing Python packages..."

"$PYTHON" -m pip install --upgrade \
    pip \
    "setuptools<82" \
    wheel \
    meson \
    meson-python \
    ninja \
    pycairo \
    typeguard \
    jinja2 \
    pyyaml

# Build native bindings for Python 3.12
"$PYTHON" -m pip install \
    "PyGObject<3.50" \
    dbus-python

# Project requirements
"$PYTHON" -m pip install \
    -r "$(dirname "$0")/../requirements.txt"

# Verify imports
echo "[3/5] Verifying native bindings..."

"$PYTHON" - <<'EOF'
import gi
import dbus

print("✓ PyGObject:", gi.__version__)
print("✓ dbus-python:", dbus.__version__)
EOF

# ────────────────────────────────────────────────────────────────
# 4. Pre-download the configured speech model
# ────────────────────────────────────────────────────────────────
echo "[4/5] Pre-downloading the configured speech model..."

PYTHONPATH="$(dirname "$0")/.." "$PYTHON" - <<'EOF'
import asyncio

from budgie_voice.config.settings import load_settings
from budgie_voice.inference.backends.registry import get_backend, resolve

inf = load_settings().inference
name, model, problem = resolve(inf.backend, inf.model)
if problem:
    print("note:", problem)
asyncio.run(get_backend(name).load(model, inf.device, inf.compute_type))
print(f"✓ {name}/{model} cached")
EOF

# ────────────────────────────────────────────────────────────────
# 5. Cache Silero VAD
# ────────────────────────────────────────────────────────────────
echo "[5/5] Pre-caching Silero VAD..."

"$PYTHON" - <<'EOF'
import os
import torch

torch.hub.set_dir(os.path.expanduser("~/.local/share/budgie-voice/torch_hub"))
torch.hub.load(
    "snakers4/silero-vad",
    "silero_vad",
    force_reload=False,
    verbose=False,
    trust_repo=True,
)

print("✓ Silero VAD cached")
EOF

echo
echo "========================================="
echo "Installation complete."
echo
echo "Verify:"
echo "  $PYTHON -c 'import gi, dbus'"
echo
echo "ydotool (Wayland typing) needs /dev/uinput access. Not changed by this script;"
echo "to allow it, run once and log in again:"
echo "  sudo usermod -aG input \"\$USER\""
echo "  echo 'KERNEL==\"uinput\", GROUP=\"input\", MODE=\"0660\"' | sudo tee /etc/udev/rules.d/80-uinput.rules"
echo
echo "Then run:"
echo "  python -m budgie_voice.speaker.enroller"
echo "  python -m budgie_voice.main"
echo "========================================="