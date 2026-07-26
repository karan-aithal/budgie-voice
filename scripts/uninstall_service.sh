#!/usr/bin/env bash
# Myna — stop, disable, and remove the systemd user service
# Runs in place from the checkout (see install_service.sh) — nothing under the
# project directory is touched. Config (~/.config/myna/) and logs
# (~/.local/share/myna/) are also preserved.
set -euo pipefail

SERVICE="myna"
SERVICE_FILE="${HOME}/.config/systemd/user/${SERVICE}.service"

echo "=== Myna — Uninstall ==="

systemctl --user stop    "${SERVICE}" 2>/dev/null || true
systemctl --user disable "${SERVICE}" 2>/dev/null || true

if [[ -f "${SERVICE_FILE}" ]]; then
    rm -f "${SERVICE_FILE}"
    echo "Removed ${SERVICE_FILE}"
fi

systemctl --user daemon-reload

echo ""
echo "Myna service uninstalled."
echo "  Config preserved : ~/.config/myna/"
echo "  Logs preserved   : ~/.local/share/myna/logs/"
echo "  Project checkout and .venv left untouched."
