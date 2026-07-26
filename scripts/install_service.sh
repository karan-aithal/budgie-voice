#!/usr/bin/env bash
# Myna — install as a systemd user service
# Usage: bash scripts/install_service.sh   (from project root, venv created)
#
# Runs in place from this checkout via its own .venv/bin/python3.12 — no copy
# to a second install directory. A copy excluding .venv/ but a unit running
# system python3.12 is how this broke before: the service instantly hit
# ModuleNotFoundError (system python has none of the project's deps) and
# systemd auto-restart-looped it, exit code 1 every ~2s.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
VENV_PYTHON="${PROJECT_DIR}/.venv/bin/python3.12"
SERVICE_DIR="${HOME}/.config/systemd/user"
SERVICE_FILE="${SERVICE_DIR}/myna.service"

echo "=== Myna — Service Installation ==="
echo "Project: ${PROJECT_DIR}"

if [[ ! -x "${VENV_PYTHON}" ]]; then
    echo "error: ${VENV_PYTHON} not found — run scripts/install_deps.sh first" >&2
    exit 1
fi

# ── 1. Copy default config if absent ──────────────────────────────────────
mkdir -p "${HOME}/.config/myna"
if [[ ! -f "${HOME}/.config/myna/config.yaml" ]]; then
    cp "${PROJECT_DIR}/config.yaml.example" "${HOME}/.config/myna/config.yaml"
    echo "Default config written to ~/.config/myna/config.yaml"
fi

# ── 2. Write service unit ──────────────────────────────────────────────────
echo "[2/3] Writing ${SERVICE_FILE} …"
mkdir -p "${SERVICE_DIR}"
cat > "${SERVICE_FILE}" << UNIT
[Unit]
Description=Myna Voice Typing Assistant
After=graphical-session.target
PartOf=graphical-session.target

[Service]
Type=simple
ExecStart=${VENV_PYTHON} -m myna.main
WorkingDirectory=${PROJECT_DIR}
Restart=on-failure
RestartSec=5
Environment=DISPLAY=:0
Environment=DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/%U/bus
Environment=IBUS_ADDRESS_FILE=%h/.config/ibus/bus

[Install]
WantedBy=graphical-session.target
UNIT

# ── 3. Enable and start ────────────────────────────────────────────────────
echo "[3/3] Enabling and starting myna.service …"
systemctl --user daemon-reload
systemctl --user enable --now myna

echo ""
echo "Myna installed and running."
echo "  Status : systemctl --user status myna"
echo "  Logs   : journalctl --user -u myna -f"
echo "  Enroll : ${VENV_PYTHON} -m myna.speaker.enroller"
