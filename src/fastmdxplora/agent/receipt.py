"""What the AI model was sent for one Agent reply, kept bounded.

Every time the AI model is asked for a reply, what it is given is recorded
as it goes: in the text protocol the prompt; in the tool-call protocol
(:mod:`fastmdxplora.agent.conversation`) the system prompt and the tools
declared (each kept once, as they are the same every turn) and the messages
added since the turn before, a look's result among them, as the loop gives
them to the provider's request (which frames them in its own shape). Each
text is bounded, and the record says when anything was cut.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "CONTEXT_RECEIPT_VERSION",
    "MAX_RECEIPT_SENT",
    "MAX_RECEIPT_TEXT",
    "ContextReceipt",
    "ReceiptBuilder",
    "digest_of",
]

CONTEXT_RECEIPT_VERSION = 3
#: The most times the AI model is recorded being asked for one reply.
MAX_RECEIPT_SENT = 32
#: The most of one text kept; the system prompt is about 46,000 characters.
MAX_RECEIPT_TEXT = 64_000
MAX_RECEIPT_ITEMS = 256


def _bounded_text(value: Any) -> str:
    text = str(value)
    if len(text) <= MAX_RECEIPT_TEXT:
        return text
    keep = (MAX_RECEIPT_TEXT - 80) // 2
    return (text[:keep] + "\n[receipt text bounded; middle omitted]\n" +
            text[-keep:])


def digest_of(record: dict[str, Any]) -> str:
    """The SHA-256 a receipt is named by: of its record without the digest,
    as canonical JSON (keys sorted, no spaces, ASCII)."""
    said = {key: value for key, value in record.items() if key != "sha256"}
    return hashlib.sha256(json.dumps(said, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True).encode("utf-8")).hexdigest()


def _bounded_value(value: Any, cut: list[bool], depth: int = 0) -> Any:
    """Arguments and results as JSON, each part bounded; ``cut`` is set when
    anything was left out, so the record says so."""
    if depth >= 16:
        cut.append(True)
        return _bounded_text(json.dumps(value, default=str, ensure_ascii=False))
    if isinstance(value, dict):
        if len(value) > MAX_RECEIPT_ITEMS:
            cut.append(True)
        return {str(key): _bounded_value(item, cut, depth + 1)
                for key, item in list(value.items())[:MAX_RECEIPT_ITEMS]}
    if isinstance(value, (list, tuple)):
        if len(value) > MAX_RECEIPT_ITEMS:
            cut.append(True)
        return [_bounded_value(item, cut, depth + 1) for item in value[:MAX_RECEIPT_ITEMS]]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    text = str(value)
    bounded = _bounded_text(text)
    if bounded != text:
        cut.append(True)
    return bounded


def _message(message: Any, cut: list[bool]) -> dict[str, Any]:
    """One message of the conversation as the AI model was given it: its
    role and words, the calls it made, the results it was answered with."""
    if not isinstance(message, dict):
        return {"role": "unknown", "text": _bounded_value(str(message), cut)}
    kept: dict[str, Any] = {"role": str(message.get("role") or "")}
    if message.get("text"):
        kept["text"] = _bounded_value(str(message["text"]), cut)
    calls = list(message.get("calls") or ())
    if calls:
        if len(calls) > MAX_RECEIPT_ITEMS:
            cut.append(True)
        kept["calls"] = [{"id": str(getattr(call, "id", "")),
                          "name": str(getattr(call, "name", "")),
                          "arguments": _bounded_value(getattr(call, "arguments", {}), cut)}
                         for call in calls[:MAX_RECEIPT_ITEMS]]
    results = list(message.get("results") or ())
    if results:
        kept["results"] = _bounded_value(results, cut)
    return kept


def _spec(spec: Any, cut: list[bool]) -> dict[str, Any]:
    """A tool as it was declared: its name, what it is said to do, and its
    arguments' schema."""
    return {"name": str(getattr(spec, "name", spec)),
            "description": _bounded_value(str(getattr(spec, "description", "")), cut),
            "parameters": _bounded_value(getattr(spec, "parameters", {}), cut)}


@dataclass(frozen=True)
class ContextReceipt:
    """What the AI model was sent for one reply."""

    sent: tuple[dict[str, Any], ...] = ()
    systems: tuple[str, ...] = ()
    toolsets: tuple[tuple[dict[str, Any], ...], ...] = ()
    truncated: bool = False

    def as_record(self) -> dict[str, Any]:
        record: dict[str, Any] = {
            "version": CONTEXT_RECEIPT_VERSION,
            "systems": list(self.systems),
            "toolsets": [[dict(tool) for tool in tools] for tools in self.toolsets],
            "sent": [dict(entry) for entry in self.sent],
            "truncated": self.truncated,
        }
        record["sha256"] = digest_of(record)
        return record


@dataclass
class ReceiptBuilder:
    """Records each time the AI model is asked, through :meth:`recording`."""

    sent: list[dict[str, Any]] = field(default_factory=list)
    systems: list[str] = field(default_factory=list)
    toolsets: list[tuple[dict[str, Any], ...]] = field(default_factory=list)
    truncated: bool = False
    _given: int = 0

    def _room(self) -> bool:
        if len(self.sent) >= MAX_RECEIPT_SENT:
            self.truncated = True
            return False
        return True

    def prompt(self, value: str) -> None:
        """A prompt of the text protocol, as it went."""
        if not self._room():
            return
        bounded = _bounded_text(value)
        self.truncated |= bounded != value
        self.sent.append({"prompt": bounded})

    def turn(self, system: str, messages: list[Any], tools: list[Any]) -> None:
        """A turn of the tool-call protocol: the system prompt and the tools
        declared, each kept once and named by its place, and the messages
        the AI model had not been given before."""
        if not self._room():
            return
        cut: list[bool] = []
        bounded = _bounded_value(str(system), cut)
        if bounded not in self.systems:
            self.systems.append(bounded)
        declared = tuple(_spec(spec, cut) for spec in tools)
        if declared not in self.toolsets:
            self.toolsets.append(declared)
        if len(messages) < self._given:
            self._given = 0
        new = [_message(message, cut) for message in messages[self._given:]]
        self._given = len(messages)
        self.truncated |= bool(cut)
        self.sent.append({"system": self.systems.index(bounded),
                          "tools": self.toolsets.index(declared),
                          "messages": new})

    def recording(self, complete: Any) -> Any:
        """``complete`` as it was, each prompt or turn recorded on its way
        to the AI model. What it streams, and that a server turned tools
        away, are passed on."""
        def sent(prompt: str, **more: Any) -> str:
            self.prompt(prompt)
            return complete(prompt, **more)

        sent.streams = getattr(complete, "streams", False)  # type: ignore[attr-defined]
        turn = getattr(complete, "turn", None)
        if callable(turn):
            def turned(system: str, messages: list[Any], tools: list[Any],
                       **more: Any) -> Any:
                self.turn(system, messages, tools)
                return turn(system, messages, tools, **more)
            sent.turn = turned  # type: ignore[attr-defined]
            sent.turned_away = getattr(complete, "turned_away", None)  # type: ignore[attr-defined]
        return sent

    def freeze(self) -> ContextReceipt:
        return ContextReceipt(tuple(self.sent), tuple(self.systems),
                              tuple(self.toolsets), self.truncated)
