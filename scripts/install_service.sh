#!/usr/bin/env bash
# Myna — install as a systemd user service
# Usage: bash scripts/install_service.sh   (from project root, venv active)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
INSTALL_DIR="${HOME}/.local/lib/myna"
SERVICE_DIR="${HOME}/.config/systemd/user"
SERVICE_FILE="${SERVICE_DIR}/myna.service"

echo "=== Myna — Service Installation ==="
echo "Source : ${PROJECT_DIR}"
echo "Install: ${INSTALL_DIR}"

# ── 1. Copy project ───────────────────────────────────────────────────────
echo "[1/4] Syncing files to ${INSTALL_DIR} …"
mkdir -p "${INSTALL_DIR}"
rsync -a --delete \
    --exclude='.git/' \
    --exclude='__pycache__/' \
    --exclude='*.pyc' \
    --exclude='.venv/' \
    --exclude='models/' \
    --exclude='.pytest_cache/' \
    "${PROJECT_DIR}/" "${INSTALL_DIR}/"

# ── 2. Copy default config if absent ─────────────────────────────────────
mkdir -p "${HOME}/.config/myna"
if [[ ! -f "${HOME}/.config/myna/config.yaml" ]]; then
    cp "${PROJECT_DIR}/config.yaml.example" "${HOME}/.config/myna/config.yaml"
    echo "Default config written to ~/.config/myna/config.yaml"
fi

# ── 3. Write service unit ─────────────────────────────────────────────────
echo "[3/4] Writing ${SERVICE_FILE} …"
mkdir -p "${SERVICE_DIR}"
cat > "${SERVICE_FILE}" << 'UNIT'
[Unit]
Description=Myna Voice Typing Assistant
After=graphical-session.target
PartOf=graphical-session.target

[Service]
Type=simple
ExecStart=/usr/bin/python3.12 -m myna.main
WorkingDirectory=%h/.local/lib/myna
Restart=on-failure
RestartSec=5
Environment=DISPLAY=:0
Environment=DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/%U/bus
Environment=IBUS_ADDRESS_FILE=%h/.config/ibus/bus

[Install]
WantedBy=graphical-session.target
UNIT

# ── 4. Enable and start ───────────────────────────────────────────────────
echo "[4/4] Enabling and starting myna.service …"
systemctl --user daemon-reload
systemctl --user enable --now myna

echo ""
echo "✓ Myna installed and running."
echo "  Status : systemctl --user status myna"
echo "  Logs   : journalctl --user -u myna -f"
echo "  Enroll : python3.12 -m myna.speaker.enroller"
