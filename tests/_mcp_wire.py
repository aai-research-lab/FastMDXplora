"""A client at the other end of an MCP server in this process, for the tests.

It writes requests as an AI app would and reads what comes back, keeping
the notifications and the questions the server asks on the way, and can
answer those questions.
"""

from __future__ import annotations

import itertools
import json
import os
import select
import threading
from typing import Any, Callable

MODERN = "2026-07-28"
KEY = "io.modelcontextprotocol/"


def meta(capabilities: dict[str, Any] | None = None, **more: Any) -> dict[str, Any]:
    return {f"{KEY}protocolVersion": MODERN,
            f"{KEY}clientCapabilities": capabilities or {},
            f"{KEY}clientInfo": {"name": "test", "version": "1"}, **more}


class Wire:
    def __init__(self, server: Any) -> None:
        r_in, w_in = os.pipe()
        r_out, w_out = os.pipe()
        self._to = os.fdopen(w_in, "wb", buffering=0)
        # Read by hand, not through a buffered file: a buffer holding two
        # lines would leave select() saying nothing is waiting.
        self._from = r_out
        self._buffer = b""
        reader, writer = os.fdopen(r_in, "rb"), os.fdopen(w_out, "wb", buffering=0)

        def run() -> None:
            try:
                server.serve(reader, writer)
            finally:
                writer.close()
                reader.close()

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()
        self.seen: list[dict[str, Any]] = []
        self._ids = itertools.count(1)

    def send(self, message: dict[str, Any] | bytes) -> None:
        raw = message if isinstance(message, bytes) else json.dumps(message).encode()
        self._to.write(raw.rstrip(b"\n") + b"\n")

    def read(self, timeout: float = 30.0) -> dict[str, Any] | None:
        while b"\n" not in self._buffer:
            ready, _, _ = select.select([self._from], [], [], timeout)
            if not ready:
                return None
            more = os.read(self._from, 65536)
            if not more:
                return None
            self._buffer += more
        line, self._buffer = self._buffer.split(b"\n", 1)
        return json.loads(line)

    def request(self, method: str, params: dict[str, Any] | None = None, *,
                modern: bool = True, capabilities: dict[str, Any] | None = None,
                request_id: Any = None, answer: Callable[[dict], dict] | None = None,
                timeout: float = 30.0) -> dict[str, Any]:
        """Send a request and return its response, answering questions with
        ``answer`` (a server request in, a result out) on the way."""
        params = dict(params or {})
        if modern:
            params["_meta"] = {**meta(capabilities), **params.get("_meta", {})}
        request_id = next(self._ids) if request_id is None else request_id
        self.send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        while True:
            message = self.read(timeout)
            assert message is not None, f"no answer to {method} within {timeout} s"
            if message.get("id") == request_id and "method" not in message:
                return message
            self.seen.append(message)
            if "method" in message and "id" in message and answer is not None:
                self.send({"jsonrpc": "2.0", "id": message["id"], "result": answer(message)})

    def initialize(self, version: str = "2025-11-25",
                   capabilities: dict[str, Any] | None = None) -> dict[str, Any]:
        answer = self.request("initialize", {
            "protocolVersion": version, "capabilities": capabilities or {},
            "clientInfo": {"name": "test", "version": "1"}}, modern=False)
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return answer

    def close(self) -> None:
        self._to.close()
        self.thread.join(timeout=10)
        os.close(self._from)


def text_of(response: dict[str, Any]) -> str:
    return response["result"]["content"][0]["text"]
