"""Bounded records of the prompts and tool output used by an Agent reply."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "CONTEXT_RECEIPT_VERSION",
    "MAX_RECEIPT_PROMPTS",
    "MAX_RECEIPT_TEXT",
    "ContextReceipt",
    "ReceiptBuilder",
]

CONTEXT_RECEIPT_VERSION = 1
MAX_RECEIPT_PROMPTS = 32
MAX_RECEIPT_TEXT = 64_000
MAX_RECEIPT_ITEMS = 32


def _bounded_text(value: Any) -> str:
    text = str(value)
    if len(text) <= MAX_RECEIPT_TEXT:
        return text
    keep = (MAX_RECEIPT_TEXT - 80) // 2
    return (text[:keep] + "\n[receipt text bounded; middle omitted]\n" +
            text[-keep:])


def _canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("utf-8")


def _bounded_value(value: Any, depth: int = 0) -> Any:
    """Bound tool arguments without changing ordinary small arguments."""
    if depth >= 4:
        return _bounded_text(value)
    if isinstance(value, dict):
        items = list(value.items())[:MAX_RECEIPT_ITEMS]
        return {str(key): _bounded_value(item, depth + 1) for key, item in items}
    if isinstance(value, list):
        return [_bounded_value(item, depth + 1) for item in value[:MAX_RECEIPT_ITEMS]]
    if isinstance(value, tuple):
        return [_bounded_value(item, depth + 1) for item in value[:MAX_RECEIPT_ITEMS]]
    if isinstance(value, str):
        return _bounded_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _bounded_text(value)


@dataclass(frozen=True)
class ContextReceipt:
    """The bounded evidence sent through one Agent completion loop."""

    prompts: tuple[str, ...] = ()
    tool_outputs: tuple[dict[str, Any], ...] = ()
    truncated: bool = False

    def as_record(self) -> dict[str, Any]:
        record: dict[str, Any] = {
            "version": CONTEXT_RECEIPT_VERSION,
            "prompts": list(self.prompts),
            "tool_outputs": [dict(output) for output in self.tool_outputs],
            "truncated": self.truncated,
        }
        record["sha256"] = hashlib.sha256(_canonical(record)).hexdigest()
        return record


@dataclass
class ReceiptBuilder:
    """Collect exact call inputs while keeping the returned record bounded."""

    prompts: list[str] = field(default_factory=list)
    tool_outputs: list[dict[str, Any]] = field(default_factory=list)
    truncated: bool = False

    def prompt(self, value: str) -> None:
        if len(self.prompts) >= MAX_RECEIPT_PROMPTS:
            self.truncated = True
            return
        bounded = _bounded_text(value)
        self.truncated |= bounded != value
        self.prompts.append(bounded)

    def tool(self, look: Any) -> None:
        if len(self.tool_outputs) >= MAX_RECEIPT_PROMPTS:
            self.truncated = True
            return
        asked = getattr(look, "asked", {})
        said = getattr(look, "said", "")
        record = {
            "tool": str(getattr(look, "tool", "")),
            "asked": _bounded_value(asked),
            "said": _bounded_text(said),
            "ok": bool(getattr(look, "ok", False)),
        }
        self.truncated |= record["said"] != said
        self.tool_outputs.append(record)

    def freeze(self) -> ContextReceipt:
        return ContextReceipt(tuple(self.prompts), tuple(self.tool_outputs),
                              self.truncated)
