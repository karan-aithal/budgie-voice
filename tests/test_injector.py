"""Tests for injector/xdotool.py — Phase 2."""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from assistant.injector.xdotool import XdotoolInjector, _apply_punctuation
from assistant.utils.exceptions import InjectorError


# ---------------------------------------------------------------------------
# _apply_punctuation unit tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        # Basic commands at end of string
        ("Hello world period", "Hello world."),
        ("Hello world comma", "Hello world,"),
        ("Hello world question mark", "Hello world?"),
        ("Hello world exclamation mark", "Hello world!"),
        ("Hello world new line", "Hello world\n"),
        ("Hello world new paragraph", "Hello world\n\n"),
        ("Hello world open quote", "Hello world\u201c"),
        ("Hello world close quote", "Hello world\u201d"),
        # Acceptance test: mixed commands before capitalized continuation
        (
            "Hello world period new line This is a test",
            "Hello world.\nThis is a test",
        ),
        # Chain: command before another command
        ("Hello comma world period", "Hello, world."),
        # Anti-example: command NOT at phrase boundary → unchanged
        ("My period is late", "My period is late"),
        # No commands → unchanged
        ("Nothing to replace here", "Nothing to replace here"),
        # Command at very start of string (edge case)
        ("period", "."),
    ],
)
def test_apply_punctuation(raw: str, expected: str) -> None:
    assert _apply_punctuation(raw) == expected


# ---------------------------------------------------------------------------
# XdotoolInjector unit tests
# ---------------------------------------------------------------------------


def test_inject_plain_text() -> None:
    """inject() with no newlines calls xdotool type once."""
    injector = XdotoolInjector(delay_ms=12, clear_modifiers=True)
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        injector.inject("Hello world")

    assert mock_run.call_count == 1
    args = mock_run.call_args[0][0]
    assert args[0] == "xdotool"
    assert args[1] == "type"
    assert "--clearmodifiers" in args
    assert "Hello world" in args


def test_inject_with_newline_calls_key_return() -> None:
    """inject() with \\n sends one 'type' call and one 'key Return' call."""
    injector = XdotoolInjector(delay_ms=12)
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        # "period new line" → ".\n"
        injector.inject("Hello period new line World")

    # Expect: type "Hello.", key Return, type "World"
    assert mock_run.call_count == 3
    all_args = [call[0][0] for call in mock_run.call_args_list]
    type_calls = [a for a in all_args if a[1] == "type"]
    key_calls = [a for a in all_args if a[1] == "key"]
    assert len(type_calls) == 2
    assert len(key_calls) == 1
    assert "Return" in key_calls[0]


def test_inject_xdotool_not_found_raises() -> None:
    """FileNotFoundError from subprocess → InjectorError with helpful message."""
    injector = XdotoolInjector()
    with patch("subprocess.run", side_effect=FileNotFoundError):
        with pytest.raises(InjectorError, match="xdotool not found"):
            injector.inject("test")


def test_inject_xdotool_nonzero_exit_raises() -> None:
    """Non-zero exit from xdotool → InjectorError."""
    injector = XdotoolInjector()
    with patch(
        "subprocess.run",
        side_effect=subprocess.CalledProcessError(1, "xdotool", stderr=b"error"),
    ):
        with pytest.raises(InjectorError, match="xdotool failed"):
            injector.inject("test")


def test_inject_no_shell_true() -> None:
    """subprocess.run must never be called with shell=True."""
    injector = XdotoolInjector()
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        injector.inject("test")

    for call in mock_run.call_args_list:
        kwargs = call[1]
        assert kwargs.get("shell") is not True


def test_inject_empty_text_is_noop() -> None:
    """Empty substituted text must not call subprocess at all."""
    injector = XdotoolInjector()
    with patch("subprocess.run") as mock_run:
        injector.inject("")
    mock_run.assert_not_called()

