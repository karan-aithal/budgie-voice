#!/usr/bin/env bash
# install_service.sh — install voice-assistant as a systemd user service
#
# What this script does:
#   1. Copies the project to ~/.local/lib/voice-assistant/
#   2. Writes ~/.config/systemd/user/voice-assistant.service
#   3. Runs: systemctl --user daemon-reload && enable --now
#
# Run from the project root:
#   bash scripts/install_service.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
INSTALL_DIR="${HOME}/.local/lib/voice-assistant"
SERVICE_DIR="${HOME}/.config/systemd/user"
SERVICE_FILE="${SERVICE_DIR}/voice-assistant.service"

echo "=== Voice Assistant — Service Installation ==="
echo "Source : ${PROJECT_DIR}"
echo "Install: ${INSTALL_DIR}"
echo ""

# ── 1. Copy project ───────────────────────────────────────────────────────────
echo "[1/4] Syncing project files to ${INSTALL_DIR} …"
mkdir -p "${INSTALL_DIR}"
rsync -a --delete \
    --exclude='.git/' \
    --exclude='__pycache__/' \
    --exclude='*.pyc' \
    --exclude='*.pyo' \
    --exclude='.venv/' \
    --exclude='venv/' \
    --exclude='models/' \
    --exclude='.pytest_cache/' \
    "${PROJECT_DIR}/" "${INSTALL_DIR}/"

# ── 2. Create service directory ───────────────────────────────────────────────
echo "[2/4] Creating systemd user service directory …"
mkdir -p "${SERVICE_DIR}"

# ── 3. Write service unit file ────────────────────────────────────────────────
echo "[3/4] Writing ${SERVICE_FILE} …"
cat > "${SERVICE_FILE}" << 'UNIT'
[Unit]
Description=Voice Typing Assistant
After=graphical-session.target
PartOf=graphical-session.target

[Service]
Type=simple
ExecStart=/usr/bin/python3.12 -m assistant.main
WorkingDirectory=%h/.local/lib/voice-assistant
Restart=on-failure
RestartSec=5
Environment=DISPLAY=:0
Environment=DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/%U/bus

[Install]
WantedBy=graphical-session.target
UNIT

# ── 4. Enable and start ───────────────────────────────────────────────────────
echo "[4/4] Enabling and starting service …"
systemctl --user daemon-reload
systemctl --user enable --now voice-assistant

echo ""
echo "✓ Service installed and running."
echo ""
echo "Useful commands:"
echo "  systemctl --user status  voice-assistant"
echo "  systemctl --user stop    voice-assistant"
echo "  systemctl --user restart voice-assistant"
echo "  journalctl --user -u voice-assistant -f"
echo ""
echo "First run: enroll your voice before the assistant will verify you:"
echo "  python3.12 -m assistant.speaker.enroller"
