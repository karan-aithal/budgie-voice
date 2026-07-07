"""xdotool text injection with spoken punctuation command substitution.

Injection command (never shell=True):
    xdotool type --clearmodifiers --delay <ms> -- <text>

Newlines are handled via ``xdotool key Return`` between segments so that
``\\n`` (new line) and ``\\n\\n`` (new paragraph) work in every X11 window.
"""

import logging
import re
import subprocess
from typing import Final

from assistant.utils.exceptions import InjectorError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Punctuation command map & substitution regex
# ---------------------------------------------------------------------------

# Ordered longest-first so multi-word keys are tried before short ones.
_COMMAND_MAP: Final[dict[str, str]] = {
    "new paragraph": "\n\n",
    "new line": "\n",
    "exclamation mark": "!",
    "question mark": "?",
    "open quote": "\u201c",   # "
    "close quote": "\u201d",  # "
    "comma": ",",
    "period": ".",
}


def _build_subst_re() -> re.Pattern[str]:
    """Compile the punctuation-command substitution pattern.

    A command word/phrase is substituted **only** at a phrase boundary:

    * end of string ($)
    * immediately before another command word
    * immediately before a capitalised / digit-starting word
      (Whisper capitalises the first word of a new sentence)

    This prevents ``"My period is late"`` → ``"My . is late"``.
    """
    # Longest-first so "new paragraph" matches before "new" (prefix safety)
    alts = "|".join(
        re.escape(k) for k in sorted(_COMMAND_MAP, key=len, reverse=True)
    )
    lookahead = rf"(?=\s+(?:{alts})\b|\s+[A-Z\d]|$)"
    # (?:^|\s+) — consume preceding whitespace, or anchor to string start
    # group(1) captures the matched command key
    return re.compile(rf"(?:^|\s+)({alts})\b{lookahead}", re.IGNORECASE)


_SUBST_RE: re.Pattern[str] = _build_subst_re()


def _apply_punctuation(text: str) -> str:
    """Replace spoken punctuation commands with their symbol equivalents.

    Substitution is word-boundary aware:
    ``"My period is late"`` → ``"My period is late"`` (unchanged)
    ``"Hello world period new line This is a test"``
        → ``"Hello world.\\nThis is a test"``

    Args:
        text: Raw transcribed text.

    Returns:
        Text with recognised dictation commands replaced by punctuation.
    """

    def _repl(m: re.Match[str]) -> str:
        return _COMMAND_MAP[m.group(1).lower()]

    return _SUBST_RE.sub(_repl, text)


# ---------------------------------------------------------------------------
# Injector
# ---------------------------------------------------------------------------


class XdotoolInjector:
    """Types text into the currently focused X11 window via xdotool.

    Args:
        delay_ms: Per-keystroke delay passed to ``xdotool type --delay``.
        clear_modifiers: Pass ``--clearmodifiers`` to release held modifier keys
            (Shift, Ctrl, etc.) before typing.

    Example::

        injector = XdotoolInjector(delay_ms=12)
        injector.inject("Hello world period new line This is a test")
        # Types: Hello world.
        #        This is a test
    """

    def __init__(
        self,
        delay_ms: int = 12,
        clear_modifiers: bool = True,
    ) -> None:
        self._delay_ms = delay_ms
        self._clear_modifiers = clear_modifiers

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _run(self, args: list[str]) -> None:
        """Run an xdotool sub-command; raise InjectorError on failure.

        Args:
            args: Full argument list including ``"xdotool"`` as first element.

        Raises:
            InjectorError: If xdotool is not installed or the command fails.
        """
        try:
            subprocess.run(
                args,
                check=True,
                capture_output=True,
            )
        except FileNotFoundError as exc:
            raise InjectorError(
                "xdotool not found — install it with: sudo apt install xdotool"
            ) from exc
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr.decode(errors="replace").strip()
            raise InjectorError(f"xdotool failed (exit {exc.returncode}): {stderr}") from exc

    def _type_segment(self, text: str) -> None:
        """Type a single line segment (no newlines) via xdotool.

        Args:
            text: Plain text with no newline characters.
        """
        cmd: list[str] = ["xdotool", "type"]
        if self._clear_modifiers:
            cmd.append("--clearmodifiers")
        cmd.extend(["--delay", str(self._delay_ms)])
        cmd.extend(["--", text])
        self._run(cmd)

    def _press_return(self) -> None:
        """Send a Return key press via xdotool key."""
        cmd: list[str] = ["xdotool", "key"]
        if self._clear_modifiers:
            cmd.append("--clearmodifiers")
        cmd.append("Return")
        self._run(cmd)

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def inject(self, text: str) -> None:
        """Substitute punctuation commands and type text into the active window.

        Newlines (``\\n``) are injected as xdotool ``key Return`` so they work
        in terminals, browsers, and GUI editors alike.

        Args:
            text: Raw transcribed text, possibly containing dictation commands.

        Raises:
            InjectorError: If xdotool is unavailable or a sub-command fails.
        """
        substituted = _apply_punctuation(text)
        logger.debug("inject pre:  %r", text)
        logger.debug("inject post: %r", substituted)

        if not substituted:
            return

        # Split on newlines; type each segment, pressing Return between them.
        # "a\n\nb" → ["a", "", "b"] → type "a", Return, Return, type "b"
        lines = substituted.split("\n")
        for idx, segment in enumerate(lines):
            if segment:
                self._type_segment(segment)
            if idx < len(lines) - 1:
                self._press_return()
