"""The protocol an assistant speaks to `fastmdx mcp`, both eras of it.

The 2026-07-28 revision carries the version and the client's capabilities
on every request and has no session; the revisions before it open with
`initialize`. Clients of both are in use, so both are answered: a request
with modern `_meta` is served as modern, and `initialize` selects the older
revision for the requests without it. The application's methods here are
stand-ins; what is tested is the protocol.
"""

from __future__ import annotations

import threading

import pytest

from fastmdxplora.mcp.protocol import Call, Method, ProtocolError, Server
from tests._mcp_wire import KEY, MODERN, Wire, meta

INFO = {"name": "fastmdxplora", "version": "0"}
FORM = {"type": "object", "properties": {"go": {"type": "boolean"}}, "required": ["go"]}


@pytest.fixture
def wire():
    released = threading.Event()
    seen: dict[str, object] = {}

    def call_tool(call: Call) -> dict:
        name = call.params.get("name")
        if name == "wait":
            released.wait(10)
            return {"content": [], "isError": False}
        if name == "boom":
            raise RuntimeError("it broke")
        if name == "refuse":
            raise ProtocolError(-32602, "Unknown tool: refuse")
        if name == "progress":
            call.progress("looked at the structure")
            call.progress("checked the config")
            return {"content": [], "isError": False}
        if name == "confirm":
            answer = call.confirm("go", "Start it?", FORM,
                                  bound_to=str(call.params.get("arguments")))
            seen["answer"] = answer
            return {"content": [{"type": "text", "text": repr(answer)}], "isError": False}
        return {"content": [], "isError": False}

    server = Server({"tools/list": Method(lambda call: {"tools": []}, ttl_ms=60_000),
                     "tools/call": Method(call_tool)},
                    info=INFO, instructions="Use it well.", capabilities={"tools": {}})
    wire = Wire(server)
    wire.released, wire.answers = released, seen
    yield wire
    released.set()
    wire.close()


class TestModern:
    def test_discovery_says_versions_capabilities_and_who(self, wire):
        result = wire.request("server/discover")["result"]
        assert result["resultType"] == "complete"
        assert result["supportedVersions"] == [MODERN]
        assert result["capabilities"] == {"tools": {}}
        assert result["instructions"] == "Use it well."
        assert result["_meta"][f"{KEY}serverInfo"] == INFO
        assert result["ttlMs"] > 0 and result["cacheScope"] == "public"

    def test_a_list_carries_its_caching_hints_and_a_call_does_not(self, wire):
        listed = wire.request("tools/list")["result"]
        assert (listed["ttlMs"], listed["cacheScope"]) == (60_000, "public")
        called = wire.request("tools/call", {"name": "plain"})["result"]
        assert called["resultType"] == "complete" and "ttlMs" not in called

    def test_a_request_without_its_version_or_capabilities_is_refused(self, wire):
        bare = wire.request("tools/list", modern=False)["error"]
        assert bare["code"] == -32602 and "initialize" in bare["message"]
        no_caps = wire.request("tools/list", {"_meta": {f"{KEY}protocolVersion": MODERN}},
                               modern=False)["error"]
        assert no_caps["code"] == -32602 and "clientCapabilities" in no_caps["message"]
        discover = wire.request("server/discover", modern=False)["error"]
        assert discover["code"] == -32602

    def test_an_unsupported_version_names_the_supported_ones(self, wire):
        error = wire.request("tools/list", {"_meta": {
            f"{KEY}protocolVersion": "1900-01-01", f"{KEY}clientCapabilities": {}}},
            modern=False)["error"]
        assert error == {"code": -32022, "message": "Unsupported protocol version",
                         "data": {"supported": [MODERN], "requested": "1900-01-01"}}

    def test_unknown_methods_and_ping_are_not_found(self, wire):
        assert wire.request("prompts/list")["error"]["code"] == -32601
        assert wire.request("ping")["error"]["code"] == -32601

    def test_a_handler_error_is_said_and_the_server_goes_on(self, wire):
        assert wire.request("tools/call", {"name": "refuse"})["error"]["code"] == -32602
        error = wire.request("tools/call", {"name": "boom"})["error"]
        assert error["code"] == -32603 and "it broke" in error["message"]
        assert "result" in wire.request("tools/list")


class TestLegacy:
    def test_initialize_agrees_a_version_and_says_who(self, wire):
        result = wire.initialize("2025-06-18")["result"]
        assert result["protocolVersion"] == "2025-06-18"
        assert result["serverInfo"] == INFO and result["instructions"] == "Use it well."
        listed = wire.request("tools/list", modern=False)["result"]
        assert listed == {"tools": []}
        assert wire.request("ping", modern=False)["result"] == {}

    def test_an_unknown_version_is_answered_with_the_newest_known(self, wire):
        assert wire.initialize("2099-01-01")["result"]["protocolVersion"] == "2025-11-25"

    def test_a_modern_request_after_initialize_is_still_modern(self, wire):
        wire.initialize()
        assert wire.request("tools/list")["result"]["resultType"] == "complete"


class TestTheWire:
    def test_what_is_not_a_request_is_said_so(self, wire):
        wire.send(b"{not json")
        assert wire.read()["error"]["code"] == -32700
        wire.send(b'[{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}]')
        assert wire.read()["error"]["code"] == -32600
        wire.send({"jsonrpc": "2.0", "id": True, "method": "tools/list"})
        assert wire.read()["error"]["code"] == -32600
        wire.send({"jsonrpc": "2.0", "id": 7, "method": 3})
        assert wire.read() == {"jsonrpc": "2.0", "id": 7, "error": {
            "code": -32600, "message": "The method must be a string."}}
        wire.send({"jsonrpc": "2.0", "id": 8, "method": "tools/list", "params": [1]})
        assert wire.read()["error"]["code"] == -32602
        wire.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        assert wire.read(timeout=0.3) is None

    def test_a_cancelled_call_is_never_answered(self, wire):
        wire.send({"jsonrpc": "2.0", "id": "slow", "method": "tools/call",
                   "params": {"_meta": meta(), "name": "wait"}})
        wire.send({"jsonrpc": "2.0", "id": "slow", "method": "tools/call",
                   "params": {"_meta": meta(), "name": "plain"}})
        assert wire.read()["error"]["message"] == "Request 'slow' is already being served."
        wire.send({"jsonrpc": "2.0", "method": "notifications/cancelled",
                   "params": {"requestId": "slow", "reason": "changed my mind"}})
        # Answered in order, so the cancellation has been read once this is.
        wire.request("tools/list")
        wire.released.set()
        wire.request("tools/call", {"name": "plain"})
        assert all(m.get("id") != "slow" for m in wire.seen)
        assert wire.read(timeout=0.5) is None

    def test_progress_is_sent_only_when_asked_for(self, wire):
        wire.request("tools/call", {"name": "progress"})
        assert wire.seen == []
        wire.request("tools/call", {"name": "progress", "_meta": {"progressToken": "p1"}})
        assert [(m["method"], m["params"]["progress"], m["params"]["message"])
                for m in wire.seen] == [
            ("notifications/progress", 1, "looked at the structure"),
            ("notifications/progress", 2, "checked the config")]
        assert all(m["params"]["progressToken"] == "p1" for m in wire.seen)


class TestAskingThePerson:
    def test_modern_asks_by_answering_input_required_and_takes_the_retry(self, wire):
        caps = {"elicitation": {"form": {}}}
        params = {"name": "confirm", "arguments": {"study": "a"}}
        first = wire.request("tools/call", params, capabilities=caps)["result"]
        assert first["resultType"] == "input_required"
        asked = first["inputRequests"]["go"]
        assert asked == {"method": "elicitation/create", "params": {
            "mode": "form", "message": "Start it?", "requestedSchema": FORM}}
        retry = {**params, "inputResponses": {"go": {"action": "accept", "content": {"go": True}}},
                 "requestState": first["requestState"]}
        done = wire.request("tools/call", retry, capabilities=caps)["result"]
        assert done["resultType"] == "complete"
        assert wire.answers["answer"] == {"action": "accept", "content": {"go": True}}

    def test_a_state_is_good_once_for_its_own_call_and_untouched(self, wire):
        caps = {"elicitation": {}}
        params = {"name": "confirm", "arguments": {"study": "a"}}
        state = wire.request("tools/call", params, capabilities=caps)["result"]["requestState"]
        yes = {"go": {"action": "accept", "content": {"go": True}}}
        other = {"name": "confirm", "arguments": {"study": "b"}, "inputResponses": yes,
                 "requestState": state}
        assert wire.request("tools/call", other,
                            capabilities=caps)["result"]["resultType"] == "input_required"
        body, mac = state.split(".")
        forged = {**params, "inputResponses": yes, "requestState": body[:-2] + "AA." + mac}
        assert wire.request("tools/call", forged,
                            capabilities=caps)["result"]["resultType"] == "input_required"
        good = {**params, "inputResponses": yes, "requestState": state}
        assert wire.request("tools/call", good, capabilities=caps)["result"]["resultType"] \
            == "complete"
        assert wire.request("tools/call", good,
                            capabilities=caps)["result"]["resultType"] == "input_required"

    def test_a_client_that_cannot_ask_gets_no_question(self, wire):
        done = wire.request("tools/call", {"name": "confirm"},
                            capabilities={"elicitation": {"url": {}}})["result"]
        assert done["resultType"] == "complete" and wire.answers["answer"] is None

    def test_legacy_sends_the_question_and_waits_for_the_answer(self, wire):
        wire.initialize("2025-06-18", {"elicitation": {}})
        response = wire.request("tools/call", {"name": "confirm"}, modern=False,
                                answer=lambda asked: {"action": "decline"})
        assert "result" in response
        question = wire.seen[0]
        assert question["method"] == "elicitation/create"
        assert question["params"] == {"message": "Start it?", "requestedSchema": FORM}
        assert wire.answers["answer"] == {"action": "decline"}

    def test_legacy_newest_names_the_form_and_an_error_reads_as_cancel(self, wire):
        wire.initialize("2025-11-25", {"elicitation": {"form": {}}})
        wire.send({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                   "params": {"name": "confirm"}})
        question = wire.read()
        assert question["params"]["mode"] == "form"
        wire.send({"jsonrpc": "2.0", "id": question["id"],
                   "error": {"code": -32601, "message": "no forms here"}})
        assert wire.read()["id"] == 9
        assert wire.answers["answer"] == {"action": "cancel"}
