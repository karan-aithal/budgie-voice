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

# Commands that are almost never used as ordinary English words → always
# replace at a simple word boundary.
_LOOSE_COMMANDS: Final[frozenset[str]] = frozenset(_COMMAND_MAP) - {"period"}

# "period" is a common English noun ("my period is late") → only replace at
# a phrase boundary: end-of-string, before another command, or before a
# capitalised word (Whisper capitalises the first word of a new sentence).
_STRICT_COMMANDS: Final[frozenset[str]] = {"period"}


def _make_loose_re() -> re.Pattern[str]:
    """``new line``, ``comma``, quotes, etc. — replace wherever they appear."""
    alts = "|".join(
        re.escape(k) for k in sorted(_LOOSE_COMMANDS, key=len, reverse=True)
    )
    return re.compile(rf"(?:^|\s+)({alts})\b", re.IGNORECASE)


def _make_strict_re() -> re.Pattern[str]:
    """``period`` — replace only at a phrase boundary.

    ``(?-i:[A-Z])`` disables IGNORECASE inside that group so only a genuine
    capital letter (start of a new Whisper sentence) triggers the match.
    """
    all_alts = "|".join(
        re.escape(k) for k in sorted(_COMMAND_MAP, key=len, reverse=True)
    )
    strict_alts = "|".join(
        re.escape(k) for k in sorted(_STRICT_COMMANDS, key=len, reverse=True)
    )
    lookahead = rf"(?=\s+(?:{all_alts})\b|(?-i:\s+[A-Z])|$)"
    return re.compile(rf"(?:^|\s+)({strict_alts})\b{lookahead}", re.IGNORECASE)


_LOOSE_RE: re.Pattern[str] = _make_loose_re()
_STRICT_RE: re.Pattern[str] = _make_strict_re()


def _apply_punctuation(text: str) -> str:
    """Replace spoken punctuation commands with their symbol equivalents.

    Two-pass strategy:

    1. **Loose pass** — replace ``new line``, ``comma``, ``open quote``, etc.
       anywhere they appear as whole words (they are never ordinary nouns).
    2. **Strict pass** — replace ``period`` only when it sits at a phrase
       boundary, preventing ``"My period is late"`` → ``"My . is late"``.

    Post-processing removes stray whitespace artefacts:
    - spaces after ``\\n`` (from inline newline injection)
    - space between ``"`` (open-quote) and the first quoted word

    Args:
        text: Raw transcribed text.

    Returns:
        Text with recognised dictation commands replaced by punctuation.
    """

    def _repl(m: re.Match[str]) -> str:
        return _COMMAND_MAP[m.group(1).lower()]

    result = _LOOSE_RE.sub(_repl, text)   # pass 1
    result = _STRICT_RE.sub(_repl, result)  # pass 2

    # Clean artefacts
    result = re.sub(r"\n[ \t]+", "\n", result)      # space after \n
    result = re.sub(r"\u201c\s+", "\u201c", result)  # space after open-quote
    return result


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

    def get_focused_window(self) -> str | None:
        """Return the active X11 window ID string, or ``None`` on failure.

        Call this *before* any blocking operation (e.g. Whisper transcription)
        to capture which window the user was typing into.  Pass the result to
        :meth:`inject` so text always lands in the correct window even if
        focus shifts during transcription.

        Returns:
            Decimal window-ID string from ``xdotool getactivewindow``,
            or ``None`` if xdotool is unavailable or returns an error.
        """
        try:
            result = subprocess.run(
                ["xdotool", "getactivewindow"],
                capture_output=True,
                text=True,
                check=True,
            )
            return result.stdout.strip() or None
        except (FileNotFoundError, subprocess.CalledProcessError):
            return None

    def _type_segment(self, text: str, window_id: str | None = None) -> None:
        """Type a single line segment (no newlines) via xdotool.

        Args:
            text: Plain text with no newline characters.
            window_id: X11 window ID from :meth:`get_focused_window`.
                ``None`` → type into currently focused window.
        """
        cmd: list[str] = ["xdotool", "type"]
        if window_id:
            cmd.extend(["--window", window_id])
        if self._clear_modifiers:
            cmd.append("--clearmodifiers")
        cmd.extend(["--delay", str(self._delay_ms)])
        cmd.extend(["--", text])
        self._run(cmd)

    def _press_return(self, window_id: str | None = None) -> None:
        """Send a Return key press via xdotool key.

        Args:
            window_id: X11 window ID.  ``None`` → currently focused window.
        """
        cmd: list[str] = ["xdotool", "key"]
        if window_id:
            cmd.extend(["--window", window_id])
        if self._clear_modifiers:
            cmd.append("--clearmodifiers")
        cmd.append("Return")
        self._run(cmd)

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def inject(self, text: str, window_id: str | None = None) -> None:
        """Substitute punctuation commands and type text into *window_id*.

        Capture ``window_id`` with :meth:`get_focused_window` *before*
        calling Whisper so that text always lands in the window the user was
        typing into, even if focus shifts during the 1-3 s of transcription.

        Newlines (``\\n``) are injected as xdotool ``key Return``.

        Args:
            text: Raw transcribed text, possibly containing dictation commands.
            window_id: X11 window ID from :meth:`get_focused_window`.
                ``None`` → type into currently focused window.

        Raises:
            InjectorError: If xdotool is unavailable or a sub-command fails.
        """
        substituted = _apply_punctuation(text)
        logger.debug("inject pre:  %r", text)
        logger.debug("inject post: %r  window=%s", substituted, window_id)

        if not substituted:
            return

        lines = substituted.split("\n")
        for idx, segment in enumerate(lines):
            if segment:
                self._type_segment(segment, window_id)
            if idx < len(lines) - 1:
                self._press_return(window_id)
