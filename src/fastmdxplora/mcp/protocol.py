"""The Model Context Protocol, over a pair of byte streams.

An AI app (an AI chat app, an AI coding tool, an AI agent a lab builds)
reaches FastMDXplora by starting ``fastmdx mcp`` and writing JSON-RPC to it,
one message per line. This module is the protocol only: framing, versions,
errors, asking the person a question in the middle of a call, and progress.
What is offered lives in :mod:`fastmdxplora.mcp.app`.

No SDK is used. The GUI's server is the standard library's, the conda-forge
package is the canonical install, and the protocol a stdio server needs is
smaller than the dependency tree an SDK would bring.

Two eras of the protocol are spoken, as the 2026-07-28 revision allows:

- **modern** (2026-07-28): every request carries its protocol version and
  the client's capabilities in ``_meta``, and there is no session. A server
  that needs the person mid-call answers ``input_required`` and the client
  asks the call again with the answer (multi round-trip requests).
- **legacy** (2025-11-25 and earlier): the client opens with ``initialize``
  and the version agreed there holds for the process. A question for the
  person is a request the server sends, answered on the same streams.

A request with modern ``_meta`` is served as modern whatever came before
it; ``initialize`` selects legacy for the requests without it.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import itertools
import json
import logging
import os
import queue
import threading
import time
from concurrent.futures import CancelledError, Future
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from typing import Any, BinaryIO, Callable

from fastmdxplora.refusals import CodedError

__all__ = [
    "MODERN_VERSIONS", "LEGACY_VERSIONS", "ProtocolError", "InputRequired", "Cancelled",
    "NotLent", "Call", "Method", "Server",
    "PARSE_ERROR", "INVALID_REQUEST", "METHOD_NOT_FOUND", "INVALID_PARAMS",
    "INTERNAL_ERROR", "UNSUPPORTED_VERSION",
]

logger = logging.getLogger("fastmdx.mcp")

#: Versions served per request, newest first.
MODERN_VERSIONS = ("2026-07-28",)
#: Versions served after ``initialize``, newest first.
LEGACY_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")

_KEY = "io.modelcontextprotocol/"
_VERSION = _KEY + "protocolVersion"
_CAPABILITIES = _KEY + "clientCapabilities"
_CLIENT_INFO = _KEY + "clientInfo"
_SERVER_INFO = _KEY + "serverInfo"

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
UNSUPPORTED_VERSION = -32022

#: How long the person has to answer a question asked mid-call (legacy),
#: and how long a modern answer's state stays good. Long enough to read a
#: plan; a call left open longer is treated as declined.
ANSWER_WITHIN_S = 900.0

#: Requests that can take a while: handled beside the reader, so the
#: reader is never held up behind an AI model, a download or a long walk.
_SLOW = frozenset({"tools/call", "prompts/get", "resources/read", "resources/list"})

#: How long calls still being served are given to answer once the client
#: has closed its side: enough for a quick one, not long enough to hold
#: an exit the client is waiting on.
DRAIN_S = 5.0


class ProtocolError(Exception):
    """A JSON-RPC error: the request itself could not be served."""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code, self.message, self.data = code, message, data

    def as_error(self) -> dict[str, Any]:
        error: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.data is not None:
            error["data"] = self.data
        return error


class Cancelled(Exception):
    """The client cancelled the call: whatever it was doing stops, and
    nothing more is sent for it."""


class NotLent(CodedError, Exception):
    """The AI app did not lend its model: the person declined, or it
    answered with something that is not a reply."""

    default_code = "mcp.ai_model.not_lent"


class InputRequired(Exception):
    """Raised by :meth:`Call.confirm` on a modern call: the answer is the
    person's to give, and the client is to ask the call again with it."""

    def __init__(self, requests: dict[str, Any], state: str) -> None:
        super().__init__("input required")
        self.requests, self.state = requests, state


@dataclass(frozen=True)
class Method:
    """One method the application serves, and how long its answer keeps.

    ``ttl_ms`` and ``scope`` are the caching hints the protocol requires on
    discovery, the lists and reads: ``public`` where the answer is the same
    for anyone, ``private`` where it holds the person's own files.
    """

    serve: Callable[[Call], dict[str, Any]]
    ttl_ms: int | None = None
    scope: str = "public"


@dataclass
class Call:
    """One request, as the application sees it."""

    id: Any
    method: str
    params: dict[str, Any]
    era: str
    version: str
    capabilities: dict[str, Any]
    _server: Server = field(repr=False)
    _steps: int = 0
    #: Replies the client's AI model gave this call in earlier rounds (modern),
    #: each with the prompt it answered, and how many have been used again;
    #: and what the call worked out once and keeps the same every round.
    _replies: list[list[str]] | None = field(default=None, repr=False)
    _replayed: int = 0
    _kept: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def client_name(self) -> str | None:
        """The client's own name for itself, where it gave one."""
        if self.era == "modern":
            meta = self.params.get("_meta")
            info = meta.get(_CLIENT_INFO) if isinstance(meta, dict) else None
        else:
            info = self._server.legacy_client
        name = info.get("name") if isinstance(info, dict) else None
        if not isinstance(name, str) or not name.strip():
            return None
        return name.strip()

    @property
    def progress_token(self) -> Any:
        meta = self.params.get("_meta")
        return meta.get("progressToken") if isinstance(meta, dict) else None

    @property
    def cancelled(self) -> bool:
        return self._server.cancelled(self.id)

    def progress(self, message: str) -> None:
        """Tell the client where a long call has got to, if it asked; and
        stop the call here if it has been cancelled."""
        if self.cancelled:
            raise Cancelled(self.method)
        token = self.progress_token
        if token is None:
            return
        self._steps += 1
        self._server.notify("notifications/progress", {
            "progressToken": token, "progress": self._steps, "message": message})

    def can_ask(self) -> bool:
        """Whether the client can put a form in front of the person."""
        asked = self.capabilities.get("elicitation")
        if not isinstance(asked, dict):
            return False
        # An empty object is form mode, for clients written before modes.
        return not asked or isinstance(asked.get("form"), dict)

    def answers_a_question(self, about: str = "") -> bool:
        """Whether this call carries an answer to a question this server put
        for this method, about what starts with ``about`` (a tool's own
        questions): a state it signed, unexpired and not yet used. Read
        without using the state up."""
        if self.era != "modern":
            return False
        state = self.params.get("requestState")
        answers = self.params.get("inputResponses")
        return (isinstance(state, str) and isinstance(answers, dict)
                and self._server.gave_out(state, self.method, about))

    def can_sample(self) -> bool:
        """Whether the AI app lends its own AI model (sampling)."""
        return isinstance(self.capabilities.get("sampling"), dict)

    def sample(self, prompt: str, *, bound_to: str, max_tokens: int) -> tuple[str, str]:
        """The client's own AI model's reply to ``prompt``, and the AI model's name.

        Legacy: asked now, and waited for. Modern: there is no asking
        mid-call, so the call is answered ``input_required`` and asked
        again with the reply; the replies given so far travel in the
        signed state, and the work is done again up to the next prompt
        with each one given back. A prompt that comes out differently on
        the way back is not given another prompt's reply.
        """
        params = {"messages": [{"role": "user", "content": {"type": "text", "text": prompt}}],
                  "maxTokens": max_tokens,
                  "modelPreferences": {"intelligencePriority": 0.9, "speedPriority": 0.2}}
        if self.era != "modern":
            answered = self._server.request(self, "sampling/createMessage", params)
            if self.cancelled:
                raise Cancelled(self.method)
            reply = _sampled(answered)
            if reply is None:
                raise NotLent("The AI app did not lend its model for this.")
            return reply
        self._rounds(bound_to)
        replies = self._replies if self._replies is not None else []
        heard = _digest(prompt)
        if self._replayed < len(replies):
            asked, text, model = replies[self._replayed]
            if asked != heard:
                raise NotLent("The work came out differently when done again with the "
                              "replies given so far, so they no longer answer it.")
            self._replayed += 1
            return text, model
        key = f"fastmdx-sample-{len(replies)}"
        carried = {"replies": replies, "asked": heard, "key": key, "kept": self._kept}
        raise InputRequired({key: {"method": "sampling/createMessage", "params": params}},
                            self._server.state_for(self.method, bound_to, carried))

    def kept(self, key: str, make: Callable[[], Any], *, bound_to: str) -> Any:
        """What ``make`` gives, worked out once for a call answered in
        rounds (modern sampling) and given back the same every later round,
        so the prompts are made again from what the first round saw: a
        running study's record, or a look, does not move under them. For
        a call in one piece, just what ``make`` gives. JSON, as it travels
        in the signed state."""
        if self.era != "modern":
            return make()
        self._rounds(bound_to)
        if key not in self._kept:
            self._kept[key] = make()
        return self._kept[key]

    def _rounds(self, bound_to: str) -> None:
        """The earlier rounds' replies and kept values, read once."""
        if self._replies is None:
            self._replies, self._kept = self._given_back(bound_to)

    def _given_back(self, bound_to: str) -> tuple[list[list[str]], dict[str, Any]]:
        """The replies of earlier rounds and the one this round carries, and
        what the call kept."""
        state = self.params.get("requestState")
        answers = self.params.get("inputResponses")
        if not isinstance(state, str):
            if answers is not None:
                raise NotLent("A reply came without the state it answers.")
            return [], {}
        carried = self._server.opened(state, self.method, bound_to)
        if not isinstance(carried, dict) or not isinstance(carried.get("replies"), list):
            raise NotLent("The state given back is not this server's for this call, has "
                          "expired, or was used before.")
        reply = _sampled(answers.get(carried.get("key")) if isinstance(answers, dict) else None)
        if reply is None:
            raise NotLent("The AI app did not lend its model for this.")
        kept = carried.get("kept")
        return ([*carried["replies"], [str(carried.get("asked")), *reply]],
                dict(kept) if isinstance(kept, dict) else {})

    def confirm(self, key: str, message: str, schema: dict[str, Any], *,
                bound_to: str) -> dict[str, Any] | None:
        """The person's answer to a form, or None where nobody can be asked.

        ``bound_to`` names what the answer is about (the tool and its
        arguments), so an answer given to one call is never taken for
        another. Modern: the first time raises :class:`InputRequired`; the
        call asked again with the answer gets it back. Legacy: the question
        is sent now and the answer waited for. An answer never given reads
        as ``cancel``.

        A modern call that carries an answer is read as answering, whatever
        capabilities it declares this time: a retry that left elicitation
        out never turns a "no" into a call with nobody asked.
        """
        if self.era == "modern":
            answers = self.params.get("inputResponses")
            state = self.params.get("requestState")
            answering = answers is not None or state is not None
            if (isinstance(answers, dict) and isinstance(answers.get(key), dict)
                    and isinstance(state, str)
                    and self._server.redeem(state, self.method, bound_to)):
                return answers[key]
            if not self.can_ask():
                return {"action": "cancel"} if answering else None
            # Asked, or asked again: an answer missing, altered, expired,
            # used before or given to another call is not an answer.
            asked = {"method": "elicitation/create",
                     "params": {"mode": "form", "message": message,
                                "requestedSchema": schema}}
            raise InputRequired({key: asked}, self._server.state_for(self.method, bound_to))
        if not self.can_ask():
            return None
        params: dict[str, Any] = {"message": message, "requestedSchema": schema}
        if self.version >= "2025-11-25":
            params["mode"] = "form"
        return self._server.ask(self, "elicitation/create", params)


class Server:
    """Reads requests, serves them, writes answers.

    ``methods`` maps a method name to the :class:`Method` serving it;
    ``info`` is the server's name, title and version; ``instructions`` are
    said to the client's AI model once, at discovery or ``initialize``.
    """

    def __init__(self, methods: dict[str, Method], *, info: dict[str, str],
                 instructions: str, capabilities: dict[str, Any],
                 workers: int = 8, discover_ttl_ms: int = 3_600_000) -> None:
        self.methods = dict(methods)
        self.info = dict(info)
        self.instructions = instructions
        self.capabilities = dict(capabilities)
        self.discover_ttl_ms = discover_ttl_ms
        self._workers = max(1, int(workers))
        self._out: BinaryIO | None = None
        self._write_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._legacy: tuple[str, dict[str, Any]] | None = None
        self.legacy_client: dict[str, Any] | None = None
        self._in_flight: set[Any] = set()
        self._cancelled: set[Any] = set()
        self._asks: dict[str, tuple[Future[dict[str, Any]], Any]] = {}
        self._ask_ids = itertools.count(1)
        self._secret = os.urandom(32)
        self._redeemed: set[str] = set()
        self._jobs: queue.Queue[tuple[Any, str, dict[str, Any]] | None] = queue.Queue()

    # ---- the streams ----
    def serve(self, reader: BinaryIO, writer: BinaryIO) -> None:
        """Serve until the reader ends, then give the calls still being
        served :data:`DRAIN_S` to answer. Workers are daemons: a call
        running longer than that is not waited for."""
        self._out = writer
        workers = [threading.Thread(target=self._work, name=f"fastmdx-mcp-{number}",
                                    daemon=True) for number in range(self._workers)]
        for worker in workers:
            worker.start()
        try:
            for raw in iter(reader.readline, b""):
                if raw.strip():
                    self._on_line(raw)
        finally:
            # Nobody is left to answer a question, so none is waited on.
            with self._state_lock:
                asks = list(self._asks.values())
            for future, _ in asks:
                future.cancel()
            for _ in workers:
                self._jobs.put(None)
            deadline = time.monotonic() + DRAIN_S
            for worker in workers:
                worker.join(max(0.0, deadline - time.monotonic()))

    def _send(self, message: dict[str, Any]) -> None:
        line = json.dumps(message, ensure_ascii=False, separators=(",", ":"),
                          default=str).encode("utf-8") + b"\n"
        with self._write_lock:
            if self._out is None:
                return
            try:
                self._out.write(line)
                self._out.flush()
            except (OSError, ValueError):
                # The client has gone; there is nobody left to tell.
                self._out = None

    def notify(self, method: str, params: dict[str, Any]) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _answer(self, request_id: Any, *, result: dict[str, Any] | None = None,
                error: ProtocolError | None = None) -> None:
        with self._state_lock:
            self._in_flight.discard(request_id)
            if request_id in self._cancelled:
                # Cancelled: nothing more is sent for it.
                self._cancelled.discard(request_id)
                return
        message: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id}
        if error is not None:
            message["error"] = error.as_error()
        else:
            message["result"] = result if result is not None else {}
        self._send(message)

    # ---- reading ----
    def _on_line(self, raw: bytes) -> None:
        try:
            message = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            self._send({"jsonrpc": "2.0", "id": None,
                        "error": {"code": PARSE_ERROR, "message": "Not JSON."}})
            return
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            self._send({"jsonrpc": "2.0", "id": None, "error": {
                "code": INVALID_REQUEST,
                "message": "One JSON-RPC 2.0 object per line; batches are not part of MCP."}})
            return
        if "method" not in message:
            self._answered(message)
            return
        method, params = message.get("method"), message.get("params")
        has_id = "id" in message
        request_id = message.get("id")
        if not isinstance(method, str):
            if has_id:
                self._send({"jsonrpc": "2.0", "id": request_id, "error": {
                    "code": INVALID_REQUEST, "message": "The method must be a string."}})
            return
        if not has_id:
            self._notified(method, params if isinstance(params, dict) else {})
            return
        if isinstance(request_id, bool) or not isinstance(request_id, (str, int)):
            self._send({"jsonrpc": "2.0", "id": None, "error": {
                "code": INVALID_REQUEST, "message": "A request id is a string or an integer."}})
            return
        if params is not None and not isinstance(params, dict):
            self._send({"jsonrpc": "2.0", "id": request_id, "error": {
                "code": INVALID_PARAMS, "message": "Params, where given, are an object."}})
            return
        with self._state_lock:
            if request_id in self._in_flight:
                duplicate = True
            else:
                duplicate = False
                self._in_flight.add(request_id)
        if duplicate:
            self._send({"jsonrpc": "2.0", "id": request_id, "error": {
                "code": INVALID_REQUEST,
                "message": f"Request {request_id!r} is already being served."}})
            return
        if method in _SLOW:
            self._jobs.put((request_id, method, params or {}))
        else:
            self._serve(request_id, method, params or {})

    def _work(self) -> None:
        while True:
            job = self._jobs.get()
            if job is None:
                return
            self._serve(*job)

    def _notified(self, method: str, params: dict[str, Any]) -> None:
        if method == "notifications/cancelled":
            request_id = params.get("requestId")
            with self._state_lock:
                if request_id in self._in_flight:
                    self._cancelled.add(request_id)
                asks = [f for f, owner in self._asks.values() if owner == request_id]
            for future in asks:
                future.cancel()
        # notifications/initialized and anything else: nothing to do.

    def _answered(self, message: dict[str, Any]) -> None:
        """A client's answer to a question this server asked (legacy)."""
        with self._state_lock:
            entry = self._asks.get(str(message.get("id")))
        if entry is not None and not entry[0].done():
            entry[0].set_result(message)

    # ---- serving ----
    def _serve(self, request_id: Any, method: str, params: dict[str, Any]) -> None:
        call: Call | None = None
        if self.cancelled(request_id):
            # Cancelled while it waited: never started, never answered.
            self._answer(request_id)
            return
        try:
            call = self._call_for(request_id, method, params)
            result = self._dispatch(call)
            self._answer(request_id, result=self._finished(call, result))
        except InputRequired as needed:
            self._answer(request_id, result={
                "resultType": "input_required", "inputRequests": needed.requests,
                "requestState": needed.state, "_meta": {_SERVER_INFO: self.info}})
        except ProtocolError as exc:
            self._answer(request_id, error=exc)
        except Cancelled:
            self._answer(request_id)  # dropped: the call was cancelled
        except Exception as exc:  # noqa: BLE001 - answered, never a dead server
            logger.exception("MCP %s failed", method)
            self._answer(request_id, error=ProtocolError(
                INTERNAL_ERROR, f"The server failed serving {method}: {exc}"))

    def _call_for(self, request_id: Any, method: str, params: dict[str, Any]) -> Call:
        """Which era a request is in, and its version and capabilities."""
        meta = params.get("_meta")
        meta = meta if isinstance(meta, dict) else {}
        if method == "initialize":
            return Call(request_id, method, params, "legacy", "", {}, self)
        if _VERSION in meta or method == "server/discover":
            version = meta.get(_VERSION)
            if not isinstance(version, str) or not version:
                raise ProtocolError(INVALID_PARAMS, f"_meta['{_VERSION}'] is required.")
            if version not in MODERN_VERSIONS:
                raise ProtocolError(UNSUPPORTED_VERSION, "Unsupported protocol version", {
                    "supported": list(MODERN_VERSIONS), "requested": version})
            capabilities = meta.get(_CAPABILITIES)
            if not isinstance(capabilities, dict):
                raise ProtocolError(INVALID_PARAMS, f"_meta['{_CAPABILITIES}'] is required.")
            return Call(request_id, method, params, "modern", version, capabilities, self)
        legacy = self._legacy
        if legacy is not None or method == "ping":
            version, capabilities = legacy or (LEGACY_VERSIONS[0], {})
            return Call(request_id, method, params, "legacy", version, capabilities, self)
        raise ProtocolError(INVALID_PARAMS, (
            f"No protocol version: give _meta['{_VERSION}'] (this server speaks "
            f"{', '.join(MODERN_VERSIONS)}), or open with initialize "
            f"({', '.join(LEGACY_VERSIONS)})."))

    def _dispatch(self, call: Call) -> dict[str, Any]:
        if call.method == "initialize":
            return self._initialize(call.params)
        if call.method == "server/discover":
            return {"supportedVersions": list(MODERN_VERSIONS),
                    "capabilities": self.capabilities,
                    "instructions": self.instructions}
        if call.method == "ping" and call.era == "legacy":
            return {}
        method = self.methods.get(call.method)
        if method is None:
            raise ProtocolError(METHOD_NOT_FOUND, f"Method not found: {call.method}")
        return method.serve(call)

    def _initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        asked = params.get("protocolVersion")
        version = asked if asked in LEGACY_VERSIONS else LEGACY_VERSIONS[0]
        capabilities = params.get("capabilities")
        self._legacy = (version, capabilities if isinstance(capabilities, dict) else {})
        client = params.get("clientInfo")
        self.legacy_client = client if isinstance(client, dict) else None
        return {"protocolVersion": version, "capabilities": self.capabilities,
                "serverInfo": self.info, "instructions": self.instructions}

    def _finished(self, call: Call, result: dict[str, Any]) -> dict[str, Any]:
        """A result as the call's era writes it."""
        out = dict(result)
        if call.era != "modern":
            for key in ("resultType", "ttlMs", "cacheScope"):
                out.pop(key, None)
            return out
        out.setdefault("resultType", "complete")
        meta = dict(out.get("_meta") or {})
        meta[_SERVER_INFO] = self.info
        out["_meta"] = meta
        if call.method == "server/discover":
            # Private: the instructions name this person's workspace.
            out["ttlMs"], out["cacheScope"] = self.discover_ttl_ms, "private"
        else:
            # A result may say its own, where one method's answers differ.
            served = self.methods.get(call.method)
            if served is not None and served.ttl_ms is not None:
                out.setdefault("ttlMs", served.ttl_ms)
                out.setdefault("cacheScope", served.scope)
        return out

    # ---- asking the person ----
    def ask(self, call: Call, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Send a legacy client a question and wait for its answer. Any
        answer that is not a result (an error, a timeout, a cancelled
        call) is ``cancel``: nothing goes ahead on silence."""
        return self.request(call, method, params) or {"action": "cancel"}

    def request(self, call: Call, method: str, params: dict[str, Any]) -> dict[str, Any] | None:
        """Send a legacy client a request for ``call`` and wait for its
        result; None for an error, no answer in time, or a cancelled call."""
        ask_id = f"fastmdx-ask-{next(self._ask_ids)}"
        future: Future[dict[str, Any]] = Future()
        with self._state_lock:
            if call.id in self._cancelled:
                return None  # nobody is waiting for the answer
            self._asks[ask_id] = (future, call.id)
        try:
            self._send({"jsonrpc": "2.0", "id": ask_id, "method": method, "params": params})
            answered = future.result(timeout=ANSWER_WITHIN_S)
        except (FutureTimeout, CancelledError):
            return None
        finally:
            with self._state_lock:
                self._asks.pop(ask_id, None)
        result = answered.get("result")
        return result if isinstance(result, dict) else None

    def cancelled(self, request_id: Any) -> bool:
        with self._state_lock:
            return request_id in self._cancelled

    def state_for(self, method: str, bound_to: str, carried: Any = None) -> str:
        """A modern call's state while the client is asked: what it is
        about, until when, and what it carries to the next round, signed
        with this process's key."""
        said: dict[str, Any] = {"m": method, "b": bound_to, "e": time.time() + ANSWER_WITHIN_S,
                                "n": base64.urlsafe_b64encode(os.urandom(12)).decode()}
        if carried is not None:
            said["c"] = carried
        body = json.dumps(said, separators=(",", ":"), ensure_ascii=False,
                          default=str).encode()
        mac = hmac.new(self._secret, body, hashlib.sha256).digest()
        return (base64.urlsafe_b64encode(body).decode() + "."
                + base64.urlsafe_b64encode(mac).decode())

    def redeem(self, state: str, method: str, bound_to: str) -> bool:
        """Whether a state is this server's, unaltered, unexpired, about
        this call, and not used before. Used once only."""
        return self._opened(state, method, bound_to) is not None

    def opened(self, state: str, method: str, bound_to: str) -> Any:
        """What a state carries, where :meth:`redeem` would take it; None
        otherwise. Used once only."""
        said = self._opened(state, method, bound_to)
        return None if said is None else said.get("c")

    def gave_out(self, state: str, method: str, about: str = "") -> bool:
        """Whether a state is one this server gave out for ``method``, about
        what starts with ``about``, unaltered, unexpired and not used yet;
        it is not used up here."""
        said = self._read_state(state)
        if (said is None or said.get("m") != method
                or not str(said.get("b") or "").startswith(about)):
            return False
        with self._state_lock:
            return str(said.get("n")) not in self._redeemed

    def _read_state(self, state: str) -> dict[str, Any] | None:
        """A state's contents where its signature holds and it has not
        expired; None otherwise."""
        try:
            body_text, mac_text = state.split(".", 1)
            body = base64.b64decode(body_text.encode(), altchars=b"-_", validate=True)
            mac = base64.b64decode(mac_text.encode(), altchars=b"-_", validate=True)
            if not hmac.compare_digest(mac, hmac.new(self._secret, body, hashlib.sha256).digest()):
                return None
            said = json.loads(body)
        except (ValueError, TypeError):
            return None
        if not isinstance(said, dict):
            return None
        if not isinstance(said.get("e"), (int, float)) or said["e"] < time.time():
            return None
        return said

    def _opened(self, state: str, method: str, bound_to: str) -> dict[str, Any] | None:
        try:
            body_text, mac_text = state.split(".", 1)
            # Strictly: a state is taken only exactly as it was given out.
            body = base64.b64decode(body_text.encode(), altchars=b"-_", validate=True)
            mac = base64.b64decode(mac_text.encode(), altchars=b"-_", validate=True)
            if not hmac.compare_digest(mac, hmac.new(self._secret, body, hashlib.sha256).digest()):
                return None
            said = json.loads(body)
        except (ValueError, TypeError):
            return None
        if not isinstance(said, dict) or said.get("m") != method or said.get("b") != bound_to:
            return None
        if not isinstance(said.get("e"), (int, float)) or said["e"] < time.time():
            return None
        nonce = str(said.get("n"))
        with self._state_lock:
            if nonce in self._redeemed:
                return None
            self._redeemed.add(nonce)
        return said


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def _sampled(result: Any) -> tuple[str, str] | None:
    """The text and the AI model's name from a sampling result, or None where
    it is not one (a refusal, an error, nothing)."""
    if not isinstance(result, dict):
        return None
    content = result.get("content")
    blocks = content if isinstance(content, list) else [content]
    texts = [b.get("text") for b in blocks
             if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)]
    if not texts:
        return None
    model = result.get("model")
    return "".join(texts), (model.strip() if isinstance(model, str) and model.strip()
                            else "an AI model the AI app did not name")
