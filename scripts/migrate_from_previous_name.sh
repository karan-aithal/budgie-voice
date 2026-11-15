#!/usr/bin/env bash
# One-time move from the app's previous name to budgie-voice: stops and removes the old
# systemd user service, moves the config and data directories (config, voiceprint,
# vocab, corrections, models, caches, logs), and renames the log files.
# Run once, then: bash scripts/install_service.sh
#
# Deliberately the only file in the repo that still spells the old name.
set -euo pipefail

OLD="myna"
NEW="budgie-voice"
CONFIG="${HOME}/.config"
DATA="${HOME}/.local/share"

echo "=== ${NEW} — migrate from previous name ==="

# 1. Old service: stop, disable, remove the unit
systemctl --user stop "${OLD}" 2>/dev/null || true
systemctl --user disable "${OLD}" 2>/dev/null || true
if [[ -f "${CONFIG}/systemd/user/${OLD}.service" ]]; then
    rm -f "${CONFIG}/systemd/user/${OLD}.service"
    echo "Removed old systemd unit"
fi
systemctl --user daemon-reload

# 2. Directories: move, never overwrite
for base in "${CONFIG}" "${DATA}"; do
    if [[ -d "${base}/${OLD}" && ! -e "${base}/${NEW}" ]]; then
        mv "${base}/${OLD}" "${base}/${NEW}"
        echo "Moved ${base}/${OLD} -> ${base}/${NEW}"
    elif [[ -d "${base}/${OLD}" ]]; then
        echo "SKIPPED ${base}/${OLD}: ${base}/${NEW} already exists — merge by hand" >&2
    fi
done

# 3. Log files and config paths that spell the old name
shopt -s nullglob
for f in "${DATA}/${NEW}/logs/${OLD}.log"*; do
    mv "$f" "${DATA}/${NEW}/logs/${NEW}.log${f##*"${OLD}.log"}"
done
if [[ -f "${CONFIG}/${NEW}/config.yaml" ]]; then
    sed -i "s#\.config/${OLD}#.config/${NEW}#g; s#share/${OLD}#share/${NEW}#g" "${CONFIG}/${NEW}/config.yaml"
fi

echo
echo "Done. Next:"
echo "  1. bash scripts/install_service.sh"
echo "  2. Update your desktop shortcut command to:"
echo "     dbus-send --session --type=method_call --dest=io.gitlab.karanaithal841.BudgieVoice /io/gitlab/karanaithal841/BudgieVoice io.gitlab.karanaithal841.BudgieVoice.Toggle"
