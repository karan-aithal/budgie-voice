#!/usr/bin/env bash
# Budgie — stop, disable, and remove the systemd user service
# Runs in place from the checkout (see install_service.sh) — nothing under the
# project directory is touched. Config (~/.config/budgie-voice/) and logs
# (~/.local/share/budgie-voice/) are also preserved.
set -euo pipefail

SERVICE="budgie-voice"
SERVICE_FILE="${HOME}/.config/systemd/user/${SERVICE}.service"

echo "=== Budgie — Uninstall ==="

systemctl --user stop    "${SERVICE}" 2>/dev/null || true
systemctl --user disable "${SERVICE}" 2>/dev/null || true

if [[ -f "${SERVICE_FILE}" ]]; then
    rm -f "${SERVICE_FILE}"
    echo "Removed ${SERVICE_FILE}"
fi

systemctl --user daemon-reload

echo ""
echo "Budgie service uninstalled."
echo "  Config preserved : ~/.config/budgie-voice/"
echo "  Logs preserved   : ~/.local/share/budgie-voice/logs/"
echo "  Project checkout and .venv left untouched."
