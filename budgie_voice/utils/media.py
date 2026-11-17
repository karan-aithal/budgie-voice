"""Pause media players while dictating, via ``playerctl``.

A missing ``playerctl`` (or any error) is a silent no-op: dictation never depends on it.
Both functions block on subprocesses — call them from an executor.
"""
from __future__ import annotations

import subprocess


def _playerctl(*args: str) -> str:
    try:
        out = subprocess.run(["playerctl", *args], capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.TimeoutExpired):  # not installed, or hung
        return ""
    return out.stdout.strip()


def pause_playing() -> list[str]:
    """Pause every player that is playing; return their names for :func:`resume`."""
    playing = [p for p in _playerctl("-l").splitlines() if _playerctl("-p", p, "status") == "Playing"]
    for player in playing:
        _playerctl("-p", player, "pause")
    return playing


def resume(players: list[str]) -> None:
    """Resume exactly the players :func:`pause_playing` paused."""
    for player in players:
        _playerctl("-p", player, "play")
