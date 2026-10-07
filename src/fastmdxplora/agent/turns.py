"""One turn of a conversation with an AI model, with tools, in each provider's shape.

:func:`fastmdxplora.agent.propose_config` began with a prompt in and text
out, and that is still what a test, a script or a lent AI model gives it.
This module adds the other way to talk to an AI model: a system prompt, the
conversation as turns, and the tools declared to the provider, so that a
reply is a structured call rather than text read by a pattern. Each
provider has its own wire shape for that; the differences are tabled here
and nowhere else, so the loop that uses a turn knows nothing about them.

What a turn gives back is the same whoever answered: the text the AI model
wrote, the tools it called with their arguments as data, and what the call
cost in tokens, cached and not.

Four things it does because a provider will, sooner or later:

**It retries what the provider says to retry.** A rate limit (429), an
overloaded service (529, 503) or a gateway that timed out (502, 504) is
asked again, after the provider's own ``Retry-After`` where it gives one,
else after 1, 2 and 4 seconds. Anything else is said at once.

**It says when tools are not taken.** A server that speaks the chat shape
without tool calling answers a request carrying tools with a 400 saying it
does not support them. That, in the words such servers use and no others,
is :class:`NoToolCalling`, which the loop reads on a conversation's first
turn as "use the text protocol with this server", never as a failure. A 400
that only mentions tools (a result missing, a schema it could not read) is
a fault, and is said as one.

**It keeps the key out of what it says.** The key is in the headers and
never in the request's words; a provider's error that quotes it back has it
replaced before the error is raised.

**It caches what does not change.** The tools and the system prompt are
the same for every message of every conversation; marked for caching
where the provider asks to be told (content blocks), and left first where
it caches a repeated beginning by itself (the chat shape).
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from fastmdxplora.refusals import CodedError, StudyError

__all__ = ["NoToolCalling", "ToolCall", "ToolSpec", "Turn", "Usage", "redacted", "take_turn"]

#: Statuses asked again, and how often in all.
RETRIED = (429, 502, 503, 504, 529)
MOST_TRIES = 4
#: The longest a provider's Retry-After is waited for, in seconds.
LONGEST_WAIT = 30.0

#: Replaced by the tests, so a retry is counted rather than waited for.
_sleep: Callable[[float], None] = time.sleep


@dataclass(frozen=True)
class ToolSpec:
    """A tool as it is declared to the AI model: its name, what it is for,
    and its arguments as a JSON schema."""

    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    """A tool the AI model called, with its arguments as data."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class Usage:
    """What the calls cost, in the provider's own counts."""

    calls: int = 0
    input_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0

    def add(self, other: Usage) -> None:
        self.calls += other.calls
        self.input_tokens += other.input_tokens
        self.cache_read_tokens += other.cache_read_tokens
        self.cache_write_tokens += other.cache_write_tokens
        self.output_tokens += other.output_tokens

    def as_record(self) -> dict[str, int]:
        return {"calls": self.calls, "input_tokens": self.input_tokens,
                "cache_read_tokens": self.cache_read_tokens,
                "cache_write_tokens": self.cache_write_tokens,
                "output_tokens": self.output_tokens}

    def said(self) -> str:
        """As a person reads it: '2 calls, 31,204 tokens in (28,770 cached), 412 out'."""
        sent = self.input_tokens + self.cache_read_tokens + self.cache_write_tokens
        cached = f" ({self.cache_read_tokens:,} cached)" if self.cache_read_tokens else ""
        return (f"{self.calls} call{'' if self.calls == 1 else 's'}, {sent:,} tokens in"
                f"{cached}, {self.output_tokens:,} out")


@dataclass(frozen=True)
class Turn:
    """One reply: its text, the tools called, and its cost."""

    text: str
    calls: tuple[ToolCall, ...] = ()
    usage: Usage = field(default_factory=Usage)
    stop: str = ""


class NoToolCalling(CodedError, Exception):
    """The server does not take tools. Not a failure: the text protocol
    is used with it instead, so it is never said to the person; its code
    is what it would be if it were."""

    default_code = "environment.service.unusable_response"


# ---------------------------------------------------------------------------
# The conversation, in one neutral shape
#
#   {"role": "user", "text": str}
#   {"role": "assistant", "text": str, "calls": [ToolCall, ...]}
#   {"role": "results", "results": [{"id", "name", "content", "is_error"}]}
# ---------------------------------------------------------------------------
def _blocks_body(model: str, system: str, messages: list[dict[str, Any]],
                 tools: list[ToolSpec], max_tokens: int) -> dict[str, Any]:
    """The content-block shape: system and tools cached, the conversation
    as blocks, each tool's result answering its call by id."""
    out: list[dict[str, Any]] = []
    for message in _alternating(messages):
        if message["role"] == "user":
            said = {"type": "text", "text": message["text"]}
            if out and out[-1]["role"] == "user":
                # After the tool results, in the same turn: two user turns
                # in a row are refused, and every result has to come in the
                # turn straight after its call.
                out[-1]["content"].append(said)
            else:
                out.append({"role": "user", "content": [said]})
        elif message["role"] == "assistant":
            content: list[dict[str, Any]] = []
            if str(message.get("text") or "").strip():
                content.append({"type": "text", "text": message["text"]})
            for call in message.get("calls") or ():
                content.append({"type": "tool_use", "id": call.id, "name": call.name,
                                "input": call.arguments})
            out.append({"role": "assistant",
                        "content": content or [{"type": "text", "text": "…"}]})
        else:
            out.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": r["id"], "content": str(r["content"]),
                 "is_error": bool(r.get("is_error"))} for r in message["results"]]})
    if out:
        # The conversation so far cached too, so a second look in one reply
        # reads what the first sent from the cache.
        last = out[-1]["content"][-1]
        last["cache_control"] = {"type": "ephemeral"}
    body: dict[str, Any] = {
        "model": model, "max_tokens": max_tokens,
        "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        "messages": out,
    }
    if tools:
        body["tools"] = [{"name": t.name, "description": t.description,
                          "input_schema": t.parameters} for t in tools]
    return body


def _chat_body(model: str, system: str, messages: list[dict[str, Any]],
               tools: list[ToolSpec], *, usage_in_stream: bool) -> dict[str, Any]:
    """The chat shape: the system prompt first, function tools, each result
    a message of its own answering its call by id."""
    out: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for message in _alternating(messages):
        if message["role"] == "user":
            out.append({"role": "user", "content": message["text"]})
        elif message["role"] == "assistant":
            calls = message.get("calls") or ()
            text = message.get("text") if str(message.get("text") or "").strip() else None
            # Empty content is taken only beside tool calls; an empty turn
            # on its own (a reply that said nothing) is sent as an ellipsis.
            said: dict[str, Any] = {"role": "assistant",
                                    "content": text if (text or calls) else "\u2026"}
            if calls:
                said["tool_calls"] = [
                    {"id": c.id, "type": "function",
                     "function": {"name": c.name, "arguments": json.dumps(c.arguments)}}
                    for c in calls]
            out.append(said)
        else:
            for r in message["results"]:
                out.append({"role": "tool", "tool_call_id": r["id"],
                            "content": str(r["content"])})
    body: dict[str, Any] = {"model": model, "messages": out}
    if tools:
        body["tools"] = [{"type": "function",
                          "function": {"name": t.name, "description": t.description,
                                       "parameters": t.parameters}} for t in tools]
    if usage_in_stream:
        body["stream_options"] = {"include_usage": True}
    return body


def _alternating(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Two user turns in a row joined into one (a history that ends with the
    person's last message, then this message), so every provider reads the
    conversation as it requires: the person and the AI model in turn."""
    joined: list[dict[str, Any]] = []
    for message in messages:
        if (joined and message["role"] == "user" and joined[-1]["role"] == "user"):
            joined[-1] = {"role": "user", "text": joined[-1]["text"] + "\n\n" + message["text"]}
        else:
            joined.append(dict(message))
    if joined and joined[0]["role"] != "user":
        joined.insert(0, {"role": "user", "text": "(the conversation before this)"})
    return joined


# ---------------------------------------------------------------------------
# Reading a reply
# ---------------------------------------------------------------------------
def _arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {"unreadable": str(raw)[:400]}
    return parsed if isinstance(parsed, dict) else {"value": parsed}


def _id_of(given: Any, number: int) -> str:
    """A call's id, or one made for it where a server gave none: each result
    answers its call by id, so two calls must never share one."""
    return str(given) if given else f"call_{number}"


def _blocks_usage(usage: dict[str, Any] | None) -> Usage:
    usage = usage or {}
    return Usage(calls=1, input_tokens=int(usage.get("input_tokens") or 0),
                 cache_read_tokens=int(usage.get("cache_read_input_tokens") or 0),
                 cache_write_tokens=int(usage.get("cache_creation_input_tokens") or 0),
                 output_tokens=int(usage.get("output_tokens") or 0))


def _chat_usage(usage: dict[str, Any] | None) -> Usage:
    usage = usage or {}
    cached = int(((usage.get("prompt_tokens_details") or {}).get("cached_tokens")) or 0)
    return Usage(calls=1, input_tokens=max(0, int(usage.get("prompt_tokens") or 0) - cached),
                 cache_read_tokens=cached,
                 output_tokens=int(usage.get("completion_tokens") or 0))


def _blocks_reply(answer: dict[str, Any]) -> Turn:
    text, calls = [], []
    for part in answer.get("content") or []:
        if part.get("type") == "text":
            text.append(str(part.get("text") or ""))
        elif part.get("type") == "tool_use":
            calls.append(ToolCall(_id_of(part.get("id"), len(calls)), str(part.get("name")),
                                  _arguments(part.get("input"))))
    return Turn("".join(text), tuple(calls), _blocks_usage(answer.get("usage")),
                str(answer.get("stop_reason") or ""))


def _chat_reply(answer: dict[str, Any], url: str) -> Turn:
    choices = answer.get("choices") or []
    if not choices:
        raise StudyError(f"{url} answered in a shape this does not recognise: no choices.",
                         code="environment.service.unusable_response", url=url)
    message = choices[0].get("message") or {}
    calls = tuple(ToolCall(_id_of(c.get("id"), i), str((c.get("function") or {}).get("name")),
                           _arguments((c.get("function") or {}).get("arguments")))
                  for i, c in enumerate(message.get("tool_calls") or []))
    return Turn(str(message.get("content") or ""), calls, _chat_usage(answer.get("usage")),
                str(choices[0].get("finish_reason") or ""))


def _events(response: Any):
    """Each server-sent event's data, as a dict, until the stream says done."""
    for raw in response:
        line = raw.decode("utf-8", "replace").strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            return
        try:
            event = json.loads(data)
        except ValueError:
            continue
        if isinstance(event, dict):
            yield event


def _stream_error(event: dict[str, Any]) -> str:
    error = event.get("error") if isinstance(event.get("error"), dict) else {}
    return redacted("The AI model stopped with an error: "
                    f"{str(error.get('message') or error)[:400]}")


def _blocks_streamed(response: Any, on_text: Callable[[str], None] | None,
                     on_call: Callable[[str], None] | None, url: str) -> Turn:
    text: list[str] = []
    blocks: dict[int, dict[str, Any]] = {}
    usage: dict[str, Any] = {}
    stop = ""
    for event in _events(response):
        kind = event.get("type")
        if kind == "error":
            raise StudyError(_stream_error(event),
                             code="environment.service.unusable_response", url=url)
        if kind == "message_start":
            usage.update((event.get("message") or {}).get("usage") or {})
        elif kind == "content_block_start":
            block = event.get("content_block") or {}
            blocks[int(event.get("index") or 0)] = {"type": block.get("type"),
                                                     "id": block.get("id"),
                                                     "name": block.get("name"), "json": ""}
            if block.get("type") == "tool_use" and on_call is not None:
                on_call(str(block.get("name")))
        elif kind == "content_block_delta":
            delta = event.get("delta") or {}
            if delta.get("type") == "input_json_delta":
                blocks.setdefault(int(event.get("index") or 0), {"json": ""})["json"] += str(
                    delta.get("partial_json") or "")
            elif delta.get("text"):
                piece = str(delta["text"])
                text.append(piece)
                if on_text is not None:
                    on_text(piece)
        elif kind == "message_delta":
            usage.update({k: v for k, v in (event.get("usage") or {}).items() if v is not None})
            stop = str((event.get("delta") or {}).get("stop_reason") or stop)
    used = [b for _, b in sorted(blocks.items()) if b.get("type") == "tool_use"]
    calls = tuple(ToolCall(_id_of(b.get("id"), i), str(b.get("name")), _arguments(b["json"]))
                  for i, b in enumerate(used))
    return Turn("".join(text), calls, _blocks_usage(usage), stop)


def _chat_streamed(response: Any, on_text: Callable[[str], None] | None,
                   on_call: Callable[[str], None] | None, url: str) -> Turn:
    text: list[str] = []
    calls: dict[int, dict[str, Any]] = {}
    usage: dict[str, Any] = {}
    stop = ""
    for event in _events(response):
        if event.get("type") == "error" or ("error" in event and "choices" not in event):
            raise StudyError(_stream_error(event),
                             code="environment.service.unusable_response", url=url)
        if event.get("usage"):
            usage = event["usage"]
        for choice in event.get("choices") or []:
            delta = choice.get("delta") or {}
            if delta.get("content"):
                piece = str(delta["content"])
                text.append(piece)
                if on_text is not None:
                    on_text(piece)
            for part in delta.get("tool_calls") or []:
                if part.get("index") is not None:
                    index = int(part["index"])
                elif not calls or (part.get("id") and calls[max(calls)]["id"]
                                   and part["id"] != calls[max(calls)]["id"]):
                    # Some servers send each call whole and without its
                    # index: a new id is a new call, not more of the last.
                    index = max(calls) + 1 if calls else 0
                else:
                    index = max(calls)
                held = calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
                if part.get("id"):
                    held["id"] = str(part["id"])
                function = part.get("function") or {}
                if function.get("name"):
                    if not held["name"] and on_call is not None:
                        on_call(str(function["name"]))
                    held["name"] += str(function["name"])
                if function.get("arguments"):
                    held["arguments"] += str(function["arguments"])
            if choice.get("finish_reason"):
                stop = str(choice["finish_reason"])
    found = tuple(ToolCall(_id_of(c["id"], n), c["name"], _arguments(c["arguments"]))
                  for n, (_, c) in enumerate(sorted(calls.items())))
    return Turn("".join(text), found, _chat_usage(usage), stop)


# ---------------------------------------------------------------------------
# Asking
# ---------------------------------------------------------------------------
def _wait_for(error: urllib.error.HTTPError, attempt: int) -> float:
    given = error.headers.get("retry-after") if error.headers is not None else None
    try:
        return max(0.0, min(float(given), LONGEST_WAIT))
    except (TypeError, ValueError):
        return float(2 ** attempt)


#: How servers without tool calling say so. Only these: a 400 that merely
#: mentions tools (a schema it could not read, a result missing, a context
#: too long "in the functions") is a fault to be said, not a server to be
#: written to in text from then on.
_NO_TOOLS_SAID = (
    "does not support tools", "doesn't support tools", "do not support tools",
    "tools are not supported", "tool use is not supported",
    "tool calling is not supported", "tool calls are not supported",
    "function calling is not supported", "functions are not supported",
    "no endpoints found that support tool", "does not support function calling",
    "unrecognized request argument supplied: tools",
)


def _no_tools(status: int, detail: str) -> bool:
    """A refusal of the tools themselves, as servers without tool calling say it."""
    said = " ".join(detail.lower().split())
    return status in (400, 404, 422, 501) and any(words in said for words in _NO_TOOLS_SAID)


_KEY_SHAPED = re.compile(r"\b(?:sk|key|api|pk)[-_][A-Za-z0-9_\-]{12,}")


def redacted(detail: str, secrets: Any = ()) -> str:
    """``detail`` with every secret in it replaced: a provider that answers a
    bad key by quoting it would otherwise put the key into the error, and
    from there into a log or a page."""
    for secret in secrets:
        secret = str(secret or "")
        if secret.lower().startswith("bearer "):
            secret = secret[7:]
        if len(secret) >= 8:
            detail = detail.replace(secret, "[key]")
    return _KEY_SHAPED.sub("[key]", detail)


def take_turn(*, url: str, headers: dict[str, str], shape: str, model: str,
              system: str, messages: list[dict[str, Any]], tools: list[ToolSpec],
              label: str, timeout: float = 120.0, max_tokens: int = 8192,
              on_text: Callable[[str], None] | None = None,
              on_call: Callable[[str], None] | None = None,
              usage_in_stream: bool = False) -> Turn:
    """Ask once, in ``shape`` (``"blocks"`` or ``"chat"``), retrying what the
    provider says to retry. The key is in ``headers`` and never in what is
    raised: an error message is the easiest place for a secret to escape
    into a log."""
    streamed = on_text is not None or on_call is not None
    if shape == "blocks":
        body = _blocks_body(model, system, messages, tools, max_tokens)
    else:
        body = _chat_body(model, system, messages, tools,
                          usage_in_stream=streamed and usage_in_stream)
    if streamed:
        body["stream"] = True
    data = json.dumps(body).encode("utf-8")
    sent = dict(headers, **{"content-type": "application/json"})
    secrets = [value for name, value in headers.items()
               if name.lower() in ("authorization", "x-api-key", "api-key")]
    for attempt in range(MOST_TRIES):
        request = urllib.request.Request(url, method="POST", data=data, headers=sent)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                if streamed:
                    reader = _blocks_streamed if shape == "blocks" else _chat_streamed
                    return reader(response, on_text, on_call, url)
                answer = json.loads(response.read())
                return _blocks_reply(answer) if shape == "blocks" else _chat_reply(answer, url)
        except urllib.error.HTTPError as exc:
            detail = redacted(exc.read().decode("utf-8", "replace")[:400], secrets)
            if exc.code in RETRIED and attempt < MOST_TRIES - 1:
                _sleep(_wait_for(exc, attempt))
                continue
            if tools and _no_tools(exc.code, detail):
                raise NoToolCalling(detail) from None
            raise StudyError(f"{label} refused the request ({exc.code}): {detail}",
                             code="environment.service.unusable_response", url=url) from None
        except urllib.error.URLError as exc:
            raise StudyError(f"Could not reach {url}: {exc.reason}",
                             code="environment.service.unreachable", url=url) from None
    raise StudyError(f"{label} was still busy after {MOST_TRIES} tries.",  # pragma: no cover
                     code="environment.service.unusable_response", url=url)
