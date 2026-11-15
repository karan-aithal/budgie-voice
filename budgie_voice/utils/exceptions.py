"""Custom exceptions for Budgie.

Only types with a distinct ``except`` handler elsewhere get their own class;
everything else raises stdlib ``RuntimeError``/``ValueError``.
"""


class BudgieInjectionError(Exception):
    """Text injection failure — caught in injection/selector.py to trigger fallback."""


class BudgieSessionError(Exception):
    """Session state-machine violation — caught in orchestrator/dictation_service.py."""
