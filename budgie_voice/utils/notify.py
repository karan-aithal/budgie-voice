"""Desktop notifications via the configured CLI tool (notify-send)."""
from __future__ import annotations

import asyncio
import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from budgie_voice.config.settings import Settings


async def notify(settings: Settings, message: str) -> None:
    """Show *message*; never raises (a missing tool must not break dictation)."""
    try:
        await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: subprocess.run(
                [settings.notifications.tool, "Budgie", message], capture_output=True, timeout=3
            ),
        )
    except Exception:  # noqa: BLE001
        pass
