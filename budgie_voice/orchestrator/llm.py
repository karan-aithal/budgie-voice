"""Optional local-LLM text polish and rewrite over an OpenAI-compatible HTTP endpoint (stdlib only).

``llm.url`` is the OpenAI-style base URL including ``/v1``: LM Studio, or Ollama on :11434.

Only imported when ``llm.enabled`` is true. Policy, not audio: it sees text the
orchestrator already has. Every failure (server down, timeout, bad JSON, empty or
oversized answer) returns the caller's original text — dictation never depends on it.

Privacy: prompts and answers are transcript text, so they are logged at DEBUG only;
INFO lines carry the exception class, never its message.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import re
import socket
import urllib.request
from urllib.parse import urlsplit

from budgie_voice.config.settings import LlmSettings

_LOG = logging.getLogger(__name__)

_MAX_CHARS = 8000  # input and answer cap; bigger is refused, never truncated
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)
# Chatty wrappers around the answer: fences, "Here is ...", "Corrected text:", leftover think tags.
_WRAPPER = re.compile(
    r"```|<think|</think|(?:here(?:'s| is)|sure|certainly|of course)\b"
    r"|(?:cleaned|corrected|polished|rewritten)\b[^\n]{0,20}:|(?:output|result):",
    re.IGNORECASE,
)


def _wrapped(out: str, src: str) -> bool:
    """True if *out* looks like commentary around the text rather than the text itself.

    A wrapper the *source* also starts with ("Sure, we can…") is the user's own words.
    """
    if _WRAPPER.match(out) and not _WRAPPER.match(src):
        return True
    if "<think" in out.lower() or "</think" in out.lower() or out.startswith("```"):
        return True
    return out[0] in "\"“'" and out[-1] in "\"”'" and src[:1] not in "\"“'"  # quote-wrapped answer

_POLISH = (
    "You clean up dictated text. Remove filler words and false starts, apply spoken "
    "self-corrections (keep only the corrected version) and fix punctuation. Change no "
    "other wording. Reply with the cleaned text only."
)
_REWRITE = (
    "You rewrite text. The user gives SELECTED TEXT and a spoken INSTRUCTION. Apply the "
    "instruction to the selected text. Reply with the rewritten text only, no commentary."
)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect could send the text to another host; treat it as a failure."""

    def redirect_request(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        return None


def _check_url(url: str, allow_remote: bool) -> None:
    """Raise ValueError unless *url* is http(s) and every address its host resolves to is loopback.

    ponytail: a hostname is resolved here and again per request; pin the address if a
    hostile resolver is in scope. Literal IPs (the default) have no such gap.
    """
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError(f"llm.url {url!r} is not an http(s) URL")
    if allow_remote:
        _LOG.warning("llm.allow_remote is on — dictated text may leave this machine")
        return
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or 80, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ValueError(f"llm.url host {parts.hostname!r} does not resolve") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped  # ::ffff:127.0.0.1 is loopback, ::ffff:8.8.8.8 is not
        if not ip.is_loopback:
            raise ValueError(
                f"llm.url host {parts.hostname!r} is not loopback — "
                "set llm.allow_remote: true if sending dictated text off this machine is intended"
            )


class LlmClient:
    """Raises ValueError from the constructor when the settings cannot be used safely."""

    def __init__(self, cfg: LlmSettings) -> None:
        if not cfg.model:
            raise ValueError("llm.model is not set")
        _check_url(cfg.url, cfg.allow_remote)
        self._cfg = cfg
        # No env proxy (would route loopback text elsewhere), no redirects.
        self._open = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect).open

    def _chat(self, system: str, user: str) -> str:
        body = json.dumps({
            "model": self._cfg.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "stream": False,
            "temperature": 0,
            # Reasoning models (Qwen3.x) otherwise think for tens of seconds first; LM Studio
            # and recent Ollama honour this, other servers ignore the unknown field.
            "reasoning_effort": "none",
        }).encode()
        req = urllib.request.Request(
            self._cfg.url.rstrip("/") + "/chat/completions", body, {"Content-Type": "application/json"},
        )
        with self._open(req, timeout=self._cfg.timeout_s) as resp:
            # A cut-off read is invalid JSON, so an oversized answer lands in the fallback.
            data = json.loads(resp.read(_MAX_CHARS * 8))
        content = data["choices"][0]["message"]["content"]
        if not isinstance(content, str):  # null content (e.g. reasoning only) is not "None"
            raise TypeError("answer has no text content")
        # Reasoning models (Qwen3.x) may inline their thinking; it is not part of the answer.
        return _THINK.sub("", content).strip()

    async def _ask(self, system: str, user: str) -> str | None:
        try:
            out = await asyncio.get_running_loop().run_in_executor(None, self._chat, system, user)
        except Exception as exc:  # noqa: BLE001 — any failure means "no LLM this time"
            _LOG.info("LLM request failed (%s) — using the original text", type(exc).__name__)
            return None
        _LOG.debug("LLM answer: %r", out)
        return out or None

    async def polish(self, text: str) -> str:
        """Cleaned *text*, or *text* itself when short, on any failure, or if the answer drifts."""
        if len(text.split()) < self._cfg.min_words:
            return text
        out = await self._ask(_POLISH, text)
        if out is None:
            return text
        # A dictation is one paragraph: a multi-line answer is an explanation, not the text.
        if _wrapped(out, text) or ("\n" in out and "\n" not in text):
            _LOG.info("LLM polish rejected (commentary around the text) — using the original text")
            return text
        if abs(len(out) - len(text)) > self._cfg.max_len_ratio * len(text):
            _LOG.info("LLM polish rejected (length changed too much) — using the original text")
            return text
        return out

    async def rewrite(self, selection: str, instruction: str) -> str | None:
        """*selection* rewritten per the spoken *instruction*; None on any failure."""
        if len(selection) > _MAX_CHARS:
            _LOG.info("LLM rewrite skipped: selection over %d chars", _MAX_CHARS)
            return None
        out = await self._ask(_REWRITE, f"SELECTED TEXT:\n{selection}\n\nINSTRUCTION:\n{instruction}")
        if out is not None and (len(out) > _MAX_CHARS or _wrapped(out, selection)):
            _LOG.info("LLM rewrite rejected (oversized or wrapped in commentary)")
            return None
        return out
