#!/usr/bin/env bash
# Myna — stop, disable, and remove the systemd user service
# Config (~/.config/myna/) and logs (~/.local/share/myna/) are preserved.
set -euo pipefail

SERVICE="myna"
SERVICE_FILE="${HOME}/.config/systemd/user/${SERVICE}.service"
INSTALL_DIR="${HOME}/.local/lib/myna"

echo "=== Myna — Uninstall ==="

systemctl --user stop    "${SERVICE}" 2>/dev/null || true
systemctl --user disable "${SERVICE}" 2>/dev/null || true

if [[ -f "${SERVICE_FILE}" ]]; then
    rm -f "${SERVICE_FILE}"
    echo "Removed ${SERVICE_FILE}"
fi

systemctl --user daemon-reload

rm -rf "${INSTALL_DIR}"
echo "Removed ${INSTALL_DIR}"

echo ""
echo "✓ Myna uninstalled."
echo "  Config preserved : ~/.config/myna/"
echo "  Logs preserved   : ~/.local/share/myna/logs/"
echo "  Remove manually if desired."

echo ""
echo "✓ Service removed."
echo ""
echo "Install directory preserved: ${INSTALL_DIR}"
echo "To remove it:  rm -rf ${INSTALL_DIR}"
echo ""
echo "Config + voiceprint preserved: ~/.config/voice-assistant/"
echo "To remove it:  rm -rf ~/.config/voice-assistant/"
