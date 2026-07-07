#!/usr/bin/env bash
# uninstall_service.sh — stop, disable, and remove the systemd user service
#
# The install directory (~/.local/lib/voice-assistant/) is NOT removed
# automatically; remove manually if desired.
#
# Run from anywhere:
#   bash scripts/uninstall_service.sh
set -euo pipefail

SERVICE_FILE="${HOME}/.config/systemd/user/voice-assistant.service"
INSTALL_DIR="${HOME}/.local/lib/voice-assistant"

echo "=== Voice Assistant — Service Uninstall ==="

# ── Stop ──────────────────────────────────────────────────────────────────────
if systemctl --user is-active --quiet voice-assistant 2>/dev/null; then
    echo "Stopping service …"
    systemctl --user stop voice-assistant
else
    echo "Service not running — skipping stop."
fi

# ── Disable ───────────────────────────────────────────────────────────────────
if systemctl --user is-enabled --quiet voice-assistant 2>/dev/null; then
    echo "Disabling service …"
    systemctl --user disable voice-assistant
else
    echo "Service not enabled — skipping disable."
fi

# ── Remove unit file ──────────────────────────────────────────────────────────
if [ -f "${SERVICE_FILE}" ]; then
    rm -f "${SERVICE_FILE}"
    echo "Removed ${SERVICE_FILE}"
else
    echo "Unit file not found — skipping."
fi

systemctl --user daemon-reload

echo ""
echo "✓ Service removed."
echo ""
echo "Install directory preserved: ${INSTALL_DIR}"
echo "To remove it:  rm -rf ${INSTALL_DIR}"
echo ""
echo "Config + voiceprint preserved: ~/.config/voice-assistant/"
echo "To remove it:  rm -rf ~/.config/voice-assistant/"
