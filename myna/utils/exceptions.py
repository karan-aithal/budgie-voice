"""Custom exceptions for Myna.

Only types with a distinct ``except`` handler elsewhere get their own class;
everything else raises stdlib ``RuntimeError``/``ValueError``.
"""


class MynaInjectionError(Exception):
    """Text injection failure — caught in injection/selector.py to trigger fallback."""


class MynaSessionError(Exception):
    """Session state-machine violation — caught in orchestrator/dictation_service.py."""
