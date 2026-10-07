"""The Agent replies by tool calls, in each provider's shape, by the same rules.

The text protocol read a reply with patterns: `SAY:`, `ASK:`, `DO:`,
`USE:`, `SHOW:` and bare YAML. A sentence before the YAML cost an attempt,
an answer and a config could not share a reply, and a repair went out
without the request, the schema or the conversation. Tool calls deliver a
reply as data, so none of that arises, and what the text protocol could
not hold (a reason for each setting, a question's candidates, a sentence
beside the config) has a place.

Three groups of tests. The wire: a stand-in server that answers in each
provider's exact shape (content blocks; the chat shape), whole and
streamed, records what it was sent, and refuses, rate-limits or turns
tools away when told to. The loop: a scripted turn, so what is tested is
what the loop does with each kind of reply. And what the text protocol
gained: repairs that keep the conversation, a config read out of prose,
the remedy said.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import yaml

from fastmdxplora.agent import turns
from fastmdxplora.agent.conversation import propose_with_tools, reply_specs
from fastmdxplora.agent.propose import _parse, propose_config, repair_prompt_for
from fastmdxplora.agent.tools import MOST_LOOKS, Toolbox
from fastmdxplora.agent.turns import NoToolCalling, ToolCall, ToolSpec, Turn, Usage
from fastmdxplora.refusals import Refusal, StudyError

STUDY = {"systems": [{"system": "1L2Y", "id": "trpcage_1L2Y"}],
         "simulation": {"duration_ns": 10, "temperature_K": 310}}
LOOK = ToolSpec("inspect_structure", "What a structure holds.",
                {"type": "object", "properties": {"system": {"type": "string"}}})


# ---------------------------------------------------------------------------
# A stand-in server, in either provider's shape
# ---------------------------------------------------------------------------
class Provider:
    """Answers each POST with the next of ``replies``: a dict sent as JSON,
    a list of events sent as a stream, or ``(status, body, headers)``."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.seen: list[dict] = []
        provider = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["content-length"])))
                provider.seen.append({"body": body, "headers": {
                    k.lower(): v for k, v in self.headers.items()}})
                reply = provider.replies.pop(0)
                if isinstance(reply, tuple):
                    status, said, headers = reply
                    self.send_response(status)
                    for key, value in headers.items():
                        self.send_header(key, value)
                    self.end_headers()
                    self.wfile.write(json.dumps(said).encode())
                elif isinstance(reply, list):
                    self.send_response(200)
                    self.send_header("content-type", "text/event-stream")
                    self.end_headers()
                    for event in reply:
                        self.wfile.write(f"data: {json.dumps(event)}\n\n".encode())
                    self.wfile.write(b"data: [DONE]\n\n")
                else:
                    self.send_response(200)
                    self.send_header("content-type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps(reply).encode())

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def waits(monkeypatch):
    waited: list[float] = []
    monkeypatch.setattr(turns, "_sleep", waited.append)
    return waited


def ask(provider, shape, *, messages=None, tools=(LOOK,), **extra):
    return turns.take_turn(url=provider.url, headers={"x-api-key": "sk-test"}, shape=shape,
                           model="an-ai-model", system="THE SYSTEM PROMPT",
                           messages=messages or [{"role": "user", "text": "Simulate trp-cage"}],
                           tools=list(tools), label="The provider", **extra)


CONVERSATION = [
    {"role": "user", "text": "earlier"},
    {"role": "assistant", "text": "an earlier answer", "calls": ()},
    {"role": "user", "text": "Simulate trp-cage"},
    {"role": "assistant", "text": "", "calls": (ToolCall("call_1", "inspect_structure",
                                                          {"system": "1L2Y"}),)},
    {"role": "results", "results": [{"id": "call_1", "name": "inspect_structure",
                                     "content": "1L2Y: 304 atoms", "is_error": False}]},
]


def test_the_block_shape_caches_what_does_not_change_and_answers_each_call():
    provider = Provider({"content": [{"type": "text", "text": "Looking."},
                                     {"type": "tool_use", "id": "toolu_2",
                                      "name": "propose_config",
                                      "input": {"config": STUDY}}],
                         "stop_reason": "tool_use",
                         "usage": {"input_tokens": 120, "cache_read_input_tokens": 9000,
                                   "cache_creation_input_tokens": 300,
                                   "output_tokens": 80}})
    try:
        turn = ask(provider, "blocks", messages=CONVERSATION)
    finally:
        provider.close()
    sent = provider.seen[0]["body"]
    assert sent["system"] == [{"type": "text", "text": "THE SYSTEM PROMPT",
                               "cache_control": {"type": "ephemeral"}}]
    assert sent["tools"] == [{"name": "inspect_structure",
                              "description": "What a structure holds.",
                              "input_schema": LOOK.parameters}]
    roles = [m["role"] for m in sent["messages"]]
    assert roles == ["user", "assistant", "user", "assistant", "user"]
    assert sent["messages"][3]["content"][0] == {
        "type": "tool_use", "id": "call_1", "name": "inspect_structure",
        "input": {"system": "1L2Y"}}
    result = sent["messages"][4]["content"][0]
    assert (result["type"], result["tool_use_id"], result["content"]) == (
        "tool_result", "call_1", "1L2Y: 304 atoms")
    # The conversation so far cached too, for the next look in this reply.
    assert result["cache_control"] == {"type": "ephemeral"}
    assert provider.seen[0]["headers"]["x-api-key"] == "sk-test"
    assert turn.text == "Looking."
    assert turn.calls == (ToolCall("toolu_2", "propose_config", {"config": STUDY}),)
    assert turn.usage.as_record() == {"calls": 1, "input_tokens": 120,
                                      "cache_read_tokens": 9000,
                                      "cache_write_tokens": 300, "output_tokens": 80}


def test_the_block_shape_streams_text_and_a_call_in_pieces():
    provider = Provider([
        {"type": "message_start", "message": {"usage": {
            "input_tokens": 50, "cache_read_input_tokens": 8000, "output_tokens": 1}}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text"}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "text_delta", "text": "I will "}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "text_delta", "text": "write it."}},
        {"type": "content_block_start", "index": 1, "content_block": {
            "type": "tool_use", "id": "toolu_9", "name": "propose_config", "input": {}}},
        {"type": "content_block_delta", "index": 1,
         "delta": {"type": "input_json_delta", "partial_json": '{"config": {"systems": '}},
        {"type": "content_block_delta", "index": 1,
         "delta": {"type": "input_json_delta", "partial_json": '[{"system": "1L2Y"}]}}'}},
        {"type": "message_delta", "delta": {"stop_reason": "tool_use"},
         "usage": {"output_tokens": 64}},
        {"type": "message_stop"},
    ])
    pieces, called = [], []
    try:
        turn = ask(provider, "blocks", on_text=pieces.append, on_call=called.append)
    finally:
        provider.close()
    assert provider.seen[0]["body"]["stream"] is True
    assert pieces == ["I will ", "write it."]
    assert called == ["propose_config"]
    assert turn.calls == (ToolCall("toolu_9", "propose_config",
                                   {"config": {"systems": [{"system": "1L2Y"}]}}),)
    assert (turn.usage.cache_read_tokens, turn.usage.output_tokens) == (8000, 64)
    assert turn.stop == "tool_use"


def test_the_chat_shape_puts_the_system_first_and_each_result_on_its_own():
    provider = Provider({"choices": [{"message": {
        "content": None, "tool_calls": [{"id": "call_7", "type": "function", "function": {
            "name": "ask_person",
            "arguments": json.dumps({"question": "Which?", "choices": ["1L2Y"]})}}]},
        "finish_reason": "tool_calls"}],
        "usage": {"prompt_tokens": 9100, "completion_tokens": 20,
                  "prompt_tokens_details": {"cached_tokens": 9000}}})
    try:
        turn = ask(provider, "chat", messages=CONVERSATION)
    finally:
        provider.close()
    sent = provider.seen[0]["body"]
    assert sent["messages"][0] == {"role": "system", "content": "THE SYSTEM PROMPT"}
    assert sent["tools"][0] == {"type": "function", "function": {
        "name": "inspect_structure", "description": "What a structure holds.",
        "parameters": LOOK.parameters}}
    asked = sent["messages"][4]
    assert asked["tool_calls"][0]["function"] == {"name": "inspect_structure",
                                                  "arguments": '{"system": "1L2Y"}'}
    assert sent["messages"][5] == {"role": "tool", "tool_call_id": "call_1",
                                   "content": "1L2Y: 304 atoms"}
    assert turn.calls == (ToolCall("call_7", "ask_person",
                                   {"question": "Which?", "choices": ["1L2Y"]}),)
    assert (turn.usage.input_tokens, turn.usage.cache_read_tokens) == (100, 9000)


def test_the_chat_shape_streams_a_call_in_pieces_and_its_usage_last():
    provider = Provider([
        {"choices": [{"delta": {"content": "Asking."}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_3", "function": {
            "name": "act", "arguments": '{"act'}}]}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {
            "arguments": 'ion": "stop"}'}}]}, "finish_reason": "tool_calls"}]},
        {"choices": [], "usage": {"prompt_tokens": 40, "completion_tokens": 5}},
    ])
    pieces, called = [], []
    try:
        turn = ask(provider, "chat", on_text=pieces.append, on_call=called.append,
                   usage_in_stream=True)
    finally:
        provider.close()
    assert provider.seen[0]["body"]["stream_options"] == {"include_usage": True}
    assert pieces == ["Asking."] and called == ["act"]
    assert turn.calls == (ToolCall("call_3", "act", {"action": "stop"}),)
    assert turn.usage.output_tokens == 5


def test_a_rate_limit_is_asked_again_after_the_wait_the_provider_gives(waits):
    provider = Provider((429, {"error": "slow down"}, {"retry-after": "3"}),
                        (529, {"error": "overloaded"}, {}),
                        {"content": [{"type": "text", "text": "Here."}]})
    try:
        turn = ask(provider, "blocks")
    finally:
        provider.close()
    assert turn.text == "Here."
    assert waits == [3.0, 2.0]
    assert len(provider.seen) == 3


def test_a_refusal_is_said_at_once_with_the_provider_s_reason_and_never_the_key(waits):
    provider = Provider((401, {"error": "bad key"}, {}))
    try:
        with pytest.raises(StudyError) as caught:
            ask(provider, "blocks")
    finally:
        provider.close()
    assert waits == []
    assert "refused the request (401)" in str(caught.value)
    assert "sk-test" not in str(caught.value)


def test_a_server_that_turns_tools_away_says_so():
    provider = Provider((400, {"error": "tools are not supported by this model"}, {}))
    try:
        with pytest.raises(NoToolCalling):
            ask(provider, "chat")
    finally:
        provider.close()


def test_a_server_that_turns_tools_away_is_asked_in_text_from_then_on(tmp_path):
    from fastmdxplora.agent.models import (
        ModelChoice, completion_for, describe_choice, save_choice)

    provider = Provider((400, {"error": "this model does not support tools"}, {}),
                        {"choices": [{"message": {"content": "SAY: Hello."}}]})
    where = tmp_path / "model.json"
    save_choice(ModelChoice("compatible", "local", provider.url), key="sk-local", path=where)
    try:
        complete = completion_for(path=where)
        assert callable(complete.turn)
        proposal = propose_config("who are you?", complete)
    finally:
        provider.close()
    assert proposal.answer == "Hello." and proposal.protocol == "text"
    stored = json.loads(where.read_text())
    assert stored["tool_use"] is False and stored["api_key"] == "sk-local"
    assert not hasattr(completion_for(path=where), "turn")
    assert "does not take tool calls" in describe_choice(where)
    # A new choice starts again from asking with tools.
    save_choice(ModelChoice("compatible", "other", provider.url), path=where)
    assert hasattr(completion_for(path=where), "turn")


def test_the_text_completion_is_asked_again_on_a_rate_limit_too(tmp_path, waits):
    from fastmdxplora.agent.models import ModelChoice, completion_for, save_choice

    provider = Provider((503, {"error": "busy"}, {}),
                        {"choices": [{"message": {"content": "fine"}}]})
    where = tmp_path / "model.json"
    save_choice(ModelChoice("compatible", "local", provider.url), key="k", path=where)
    try:
        assert completion_for(path=where)("hello") == "fine"
    finally:
        provider.close()
    assert waits == [1.0]


@pytest.mark.parametrize("detail", [
    "messages.2: `tool_use` ids were found without `tool_result` blocks immediately after",
    "This model's maximum context length is 128000 tokens (800 in the functions).",
    "tools.3.custom.input_schema: JSON schema is invalid.",
    "Invalid value for 'tool_choice'",
    "An assistant message with 'tool_calls' must be followed by tool messages",
])
def test_a_fault_that_mentions_tools_is_not_a_server_without_them(detail):
    assert not turns._no_tools(400, detail)


@pytest.mark.parametrize("detail", [
    "this model does not support tools",
    "Tools are not supported by this model",
    "No endpoints found that support tool use.",
    "Unrecognized request argument supplied: tools",
])
def test_a_server_without_tools_is_known_by_what_it_says(detail):
    assert turns._no_tools(400, detail)


def test_a_turn_that_said_nothing_is_still_a_turn_in_either_shape():
    empty = [{"role": "user", "text": "hi"}, {"role": "assistant", "text": "  ", "calls": ()},
             {"role": "user", "text": "Reply to the person."}]
    chat = turns._chat_body("m", "s", empty, [], usage_in_stream=False)["messages"]
    assert chat[2] == {"role": "assistant", "content": "…"}
    blocks = turns._blocks_body("m", "s", empty, [], 10)["messages"]
    assert blocks[1]["content"] == [{"type": "text", "text": "…"}]
    beside = [{"role": "user", "text": "hi"},
              {"role": "assistant", "text": "\n\n", "calls": (ToolCall("t1", "x", {}),)},
              {"role": "results", "results": [{"id": "t1", "name": "x", "content": "ok"}]}]
    assert [c["type"] for c in turns._blocks_body("m", "s", beside, [], 10)["messages"][1][
        "content"]] == ["tool_use"]


def test_two_calls_are_two_calls_however_a_server_numbers_them():
    reply = turns._chat_reply({"choices": [{"message": {"tool_calls": [
        {"function": {"name": "inspect_structure", "arguments": "{}"}},
        {"function": {"name": "check_config", "arguments": "{}"}}]}}]}, "u")
    assert [c.id for c in reply.calls] == ["call_0", "call_1"]
    events = [{"choices": [{"delta": {"tool_calls": [{"id": "a", "function": {
        "name": "inspect_structure", "arguments": '{"system": "1L2Y"}'}}]}}]},
              {"choices": [{"delta": {"tool_calls": [{"id": "b", "function": {
                  "name": "check_selection", "arguments": '{"expression": "all"}'}}]}}]}]
    stream = [f"data: {json.dumps(e)}\n".encode() for e in events] + [b"data: [DONE]\n"]
    streamed = turns._chat_streamed(stream, None, None, "u")
    assert [(c.id, c.name, c.arguments) for c in streamed.calls] == [
        ("a", "inspect_structure", {"system": "1L2Y"}),
        ("b", "check_selection", {"expression": "all"})]


def test_a_provider_that_quotes_the_key_back_does_not_put_it_in_the_error(tmp_path, waits):
    from fastmdxplora.agent.models import ModelChoice, completion_for, save_choice

    key = "sk-proj-abcdefghijklmnopqrstuvwxyz0123"
    quoted = {"error": {"message": f"Incorrect API key provided: {key}."}}
    provider = Provider((401, quoted, {}), (401, quoted, {}))
    where = tmp_path / "model.json"
    save_choice(ModelChoice("compatible", "local", provider.url), key=key, path=where)
    complete = completion_for(path=where)
    try:
        for asked in (lambda: complete("hello"),
                      lambda: complete.turn("s", [{"role": "user", "text": "hi"}], [LOOK])):
            with pytest.raises(StudyError) as caught:
                asked()
            assert key not in str(caught.value) and "[key]" in str(caught.value)
    finally:
        provider.close()
    assert key not in turns.redacted(f"bad key {key[:-4]}…")


def test_tools_turned_away_are_noted_once_and_for_that_server_only(tmp_path):
    from fastmdxplora.agent.models import (
        ModelChoice, _takes_tools, completion_for, save_choice)

    provider = Provider((400, {"error": "this model does not support tools"}, {}))
    where = tmp_path / "model.json"
    save_choice(ModelChoice("compatible", "local", provider.url), key="k", path=where)
    try:
        with pytest.raises(NoToolCalling):
            completion_for(path=where).turn("s", [{"role": "user", "text": "hi"}], [LOOK])
    finally:
        provider.close()
    # A turn turned away is not noted by itself: only the loop knows it was
    # the conversation's first.
    assert "tool_use" not in json.loads(where.read_text())
    completion_for(path=where).turned_away()
    assert not _takes_tools(where)
    stored = json.loads(where.read_text())
    stored["model"] = "another"
    where.write_text(json.dumps(stored))
    assert _takes_tools(where)


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------
class Scripted:
    """A turn that replies with these, in order, every request kept."""

    def __init__(self, *replies: Turn):
        self.replies = list(replies)
        self.asked: list[dict] = []

    def __call__(self, system, messages, tools):
        self.asked.append({"system": system, "messages": [dict(m) for m in messages],
                           "tools": [t.name for t in tools]})
        return self.replies.pop(0)


def call(tool, n=1, **arguments):
    return ToolCall(f"call_{tool}_{n}", tool, arguments)


def said(*calls, text="", tokens=100):
    return Turn(text, tuple(calls), Usage(calls=1, input_tokens=tokens, output_tokens=10))


def propose(turn, request="Simulate trp-cage for 10 ns at body temperature", **kw):
    options = dict(phases=["setup", "simulation"], max_cycles=3, verbose_schema=True,
                   history=None, current_config=None, run_status=None, attachments=None,
                   tools=None)
    options.update(kw)
    return propose_with_tools(request, turn, **options)


def test_a_config_comes_with_a_reason_for_each_setting_written_as_a_decision():
    turn = Scripted(said(call("propose_config", config=STUDY, note="310 K, as asked.", reasons={
        "systems": {"why": "Trp-cage is 1L2Y, the NMR structure of the miniprotein."},
        "simulation.temperature_K": {"why": "Body temperature, as asked.", "asked": True},
        "simulation.duration_ns": {"why": "Ten nanoseconds, as asked.", "asked": True,
                                   "alternatives": [5, 20]}})))
    proposal = propose(turn)
    assert proposal.accepted and proposal.cycles == 1 and proposal.protocol == "tools"
    assert proposal.note == "310 K, as asked."
    decisions = proposal.config["decisions"]
    assert decisions["systems"]["source"] == "agent"
    # The request said 10 ns, so that is the person's; it said "body
    # temperature" and not 310 K, so the number is the Agent's reading of
    # it, whatever the AI model claims.
    assert decisions["simulation.duration_ns"] == {
        "why": "Ten nanoseconds, as asked.", "source": "person", "alternatives": [5, 20]}
    assert decisions["simulation.temperature_K"] == {"why": "Body temperature, as asked.",
                                                     "source": "agent"}
    assert proposal.usage["calls"] == 1


def test_a_refusal_comes_back_in_the_same_conversation_with_what_would_fix_it():
    wrong = dict(STUDY, simulation={"duration_ns": 10, "temprature_K": 310})
    turn = Scripted(said(call("propose_config", config=wrong)),
                    said(call("propose_config", 2, config=STUDY)))
    proposal = propose(turn, history=[{"role": "user", "text": "hello"},
                                      {"role": "agent", "text": "Hello."}])
    assert proposal.accepted and proposal.cycles == 2
    assert [a.refusal.code for a in proposal.attempts if a.refusal] == ["config.option.unknown"]
    second = turn.asked[1]["messages"]
    # The request, the history and the first reply are all still there.
    assert second[0] == {"role": "user", "text": "hello"}
    assert "## The study wanted\nSimulate trp-cage" in second[2]["text"]
    result = second[-1]["results"][0]
    assert result["is_error"] and result["id"] == "call_propose_config_1"
    assert "Refused (config.option.unknown)" in result["content"]
    assert "What would fix it: Rename `simulation.temprature_K` to `temperature_K`" in (
        result["content"])
    assert turn.asked[0]["system"] == turn.asked[1]["system"]
    assert proposal.usage["calls"] == 2 and proposal.usage["input_tokens"] == 200


def test_a_semantic_refusal_ends_the_loop(monkeypatch):
    from fastmdxplora.config import loader

    def undetermined(config, **kwargs):
        raise loader.ConfigError("LIG carries groups whose protonation depends on pH.",
                                 code="setup.chemistry.protonation_undetermined",
                                 resname="LIG", ph=7.4)

    monkeypatch.setattr(loader, "validate_config", undetermined)
    turn = Scripted(said(call("propose_config", config=STUDY)))
    proposal = propose(turn)
    assert not proposal.accepted and proposal.cycles == 1 and len(turn.asked) == 1
    assert proposal.refusal.code == "setup.chemistry.protonation_undetermined"


def test_the_cap_holds():
    never = dict(STUDY, setup={"not_a_setting": 1})
    turn = Scripted(*[said(call("propose_config", n, config=never)) for n in range(5)])
    proposal = propose(turn, max_cycles=3)
    assert not proposal.accepted and proposal.cycles == 3 and len(turn.asked) == 3
    assert proposal.refusal is not None


def test_looks_are_not_attempts_and_are_capped():
    from fastmdxplora.agent.tools import Look

    box = Toolbox()

    def use(name, asked):
        look = Look(name, asked, "304 atoms", True)
        box.looks.append(look)
        return look

    box.use = use  # type: ignore[method-assign]
    turn = Scripted(*[said(call("inspect_structure", n, system="1L2Y"))
                      for n in range(MOST_LOOKS + 1)],
                    said(call("propose_config", config=STUDY)))
    proposal = propose(turn, tools=box)
    assert proposal.accepted and proposal.cycles == 1
    assert len(proposal.looks) == MOST_LOOKS
    last = turn.asked[MOST_LOOKS + 1]["messages"][-1]["results"][0]
    assert last["is_error"] and "used up" in last["content"]
    assert "inspect_structure" in turn.asked[0]["tools"]


def test_a_question_comes_with_its_candidates():
    turn = Scripted(said(call("ask_person", question="Which lysozyme?",
                              choices=["1AKI (hen, 1.5 A)", "2LZM (T4)"])))
    proposal = propose(turn, request="Simulate lysozyme")
    assert proposal.question == "Which lysozyme?"
    assert proposal.choices == ("1AKI (hen, 1.5 A)", "2LZM (T4)")
    assert not proposal.accepted and len(turn.asked) == 1


def test_an_action_comes_back_named_and_with_what_it_takes():
    assert propose(Scripted(said(call("act", action="run")))).action == "run"
    again = propose(Scripted(said(call("act", action="rerun windows", windows=[5, 3, 3],
                                       force_constant=6000))))
    assert (again.action, again.arguments) == (
        "rerun windows", {"windows": [3, 5], "force_constant": 6000.0, "duration_ns": None})
    analysed = propose(Scripted(said(call("act", action="analyze again",
                                          analyses=["RMSD", "rg", "rmsd"]))))
    assert analysed.arguments == {"analyses": ["rmsd", "rg"]}


def test_two_actions_or_one_not_known_are_not_taken():
    two = Scripted(said(call("act", action="run"), call("act", 2, action="stop")),
                   said(text="I will wait for you to say which."))
    proposal = propose(two)
    assert proposal.action is None and proposal.answer
    assert all(r["is_error"] for r in two.asked[1]["messages"][-1]["results"])
    unknown = Scripted(said(call("act", action="delete everything")),
                       said(text="I cannot do that."))
    assert propose(unknown).action is None


def test_a_config_and_a_run_in_one_reply_give_the_config_only():
    turn = Scripted(said(call("propose_config", config=STUDY,
                              note="say run when you have read it"),
                         call("act", action="run")))
    proposal = propose(turn)
    assert proposal.accepted and proposal.action is None
    assert proposal.note == "say run when you have read it"


def test_an_answer_in_text_with_a_scene_beside_it():
    turn = Scripted(said(call("show_scene", frame=40, colour="result:rmsf", labels=True,
                              name="loop at 40"),
                         text="The loop moves most."))
    proposal = propose(turn, request="what moves most?")
    assert proposal.answer == "The loop moves most."
    assert proposal.scene == {"frame": 40, "colour": "result:rmsf", "labels": True,
                              "name": "loop at 40"}
    bad = Scripted(said(call("show_scene", frame=40, colour="rainbow"), text="Here."))
    assert propose(bad).scene is None and propose(
        Scripted(said(text="Plain."))).answer == "Plain."


def test_an_empty_reply_or_an_unknown_tool_is_asked_again():
    turn = Scripted(said(), said(call("delete_files")), said(text="Hello."))
    proposal = propose(turn, request="hi")
    assert proposal.answer == "Hello." and proposal.cycles == 2
    assert turn.asked[1]["messages"][-1] == {
        "role": "user", "text": "Reply to the person: answer in plain text, or call one "
                                "of the reply tools."}
    assert "no tool called 'delete_files'" in turn.asked[2]["messages"][-1]["results"][0][
        "content"]


def test_what_stays_the_same_comes_first_and_what_changes_last():
    first, second = Scripted(said(text="a")), Scripted(said(text="b"))
    propose(first, request="one", run_status="status: running")
    propose(second, request="two", run_status="status: done",
            current_config="systems: [{system: 1UBQ}]")
    assert first.asked[0]["system"] == second.asked[0]["system"]
    system = first.asked[0]["system"]
    for marker in ("SAY:", "ASK:", "DO:", "USE:", "SHOW:", "YAML only"):
        assert marker not in system
    assert "propose_config" in system and "## The study wanted" not in system
    last = second.asked[0]["messages"][-1]["text"]
    assert last.endswith("## The study wanted\ntwo\n")
    assert "## What the run is doing\nstatus: done" in last
    assert "## The current config" in last


def test_the_reply_tools_are_declared_with_their_arguments():
    names = {spec.name: spec for spec in reply_specs()}
    assert set(names) == {"propose_config", "ask_person", "act", "show_scene"}
    assert names["propose_config"].parameters["required"] == ["config"]
    assert "rerun windows" in names["act"].parameters["properties"]["action"]["enum"]
    looks = {spec.name for spec in Toolbox().specs()}
    assert {"inspect_structure", "preview_setup", "check_config", "check_selection",
            "read_study", "methods_of_study"} <= looks


def test_a_config_written_as_text_is_validated_and_never_taken_as_an_answer():
    wrong = yaml.safe_dump(dict(STUDY, simulation={"duraton_ns": 10}))
    turn = Scripted(said(text="Here it is:\n" + wrong),
                    said(text="```yaml\n" + yaml.safe_dump(STUDY) + "```"))
    proposal = propose(turn)
    assert proposal.accepted and proposal.cycles == 2 and proposal.answer is None
    told = turn.asked[1]["messages"][-1]["text"]
    assert told.startswith("That config was refused (config.option.unknown)")
    assert "Call `propose_config` with it corrected." in told


def test_a_reply_in_the_text_protocol_s_words_is_read_as_they_mean():
    assert propose(Scripted(said(text="SAY: It equilibrated."))).answer == "It equilibrated."
    asked = propose(Scripted(said(text="ASK: Which structure?")))
    assert asked.question == "Which structure?" and asked.answer is None


def test_a_completion_without_turns_is_asked_in_text():
    proposal = propose_config("x", lambda prompt: "SAY: plain.")
    assert proposal.answer == "plain." and proposal.protocol == "text"
    assert proposal.usage is None


def _a_box():
    from fastmdxplora.agent.tools import Look

    box = Toolbox()

    def use(name, asked):
        look = Look(name, asked, "1L2Y: 304 atoms", True)
        box.looks.append(look)
        return look

    box.use = use  # type: ignore[method-assign]
    return box


def test_text_beside_a_look_is_not_taken_as_the_reply():
    turn = Scripted(said(call("inspect_structure", system="1L2Y"),
                         text="Let me inspect the structure first."),
                    said(call("propose_config", config=STUDY)))
    proposal = propose(turn, tools=_a_box())
    assert proposal.accepted and proposal.answer is None and len(turn.asked) == 2
    result = turn.asked[1]["messages"][-1]["results"][0]
    assert (result["id"], result["content"]) == ("call_inspect_structure_1",
                                                 "1L2Y: 304 atoms")


def test_every_call_is_answered_in_the_next_turn_before_anything_more_is_said():
    wrong = yaml.safe_dump(dict(STUDY, simulation={"duraton_ns": 10}), sort_keys=False)
    turn = Scripted(said(call("inspect_structure", system="1L2Y"), call("show_scene", frame=1),
                         text=wrong),
                    said(call("propose_config", config=STUDY)))
    # A look beside a written config: the look's text is the reply's
    # preamble, so the config is not read; the next turn answers both calls.
    proposal = propose(turn, tools=_a_box())
    assert proposal.accepted
    sent = turn.asked[1]["messages"]
    assert [m["role"] for m in sent[-2:]] == ["assistant", "results"]
    assert {r["id"] for r in sent[-1]["results"]} == {"call_inspect_structure_1",
                                                      "call_show_scene_1"}

    # A written config refused beside a scene: the scene's result first,
    # then what was refused, and in the block shape both in one user turn.
    turn = Scripted(said(call("show_scene", frame=1), text=wrong),
                    said(call("propose_config", config=STUDY)))
    assert propose(turn).accepted
    sent = turn.asked[1]["messages"]
    assert [m["role"] for m in sent[-3:]] == ["assistant", "results", "user"]
    blocks = turns._blocks_body("m", "s", sent, [], 100)["messages"]
    assert [m["role"] for m in blocks[-2:]] == ["assistant", "user"]
    assert [c["type"] for c in blocks[-1]["content"]] == ["tool_result", "text"]
    chat = turns._chat_body("m", "s", sent, [], usage_in_stream=False)["messages"]
    assert [m["role"] for m in chat[-3:]] == ["assistant", "tool", "user"]


def test_a_server_that_refuses_tools_after_taking_them_has_failed():
    class Refuses(Scripted):
        def __call__(self, system, messages, tools):
            if self.asked:
                raise NoToolCalling("this model does not support tools")
            return super().__call__(system, messages, tools)

    turn = Refuses(said(call("propose_config", config={"systems": "not a list"})))
    with pytest.raises(StudyError) as caught:
        propose(turn)
    assert not isinstance(caught.value, NoToolCalling)
    assert caught.value.code == "environment.service.unusable_response"


def test_a_reason_is_kept_only_for_a_setting_and_never_put_in_the_person_s_mouth():
    from fastmdxplora.agent.conversation import _reasons_into

    # A reason about what is not a setting is set aside and said; the
    # config is not refused for it.
    turn = Scripted(said(call("propose_config", config=STUDY, reasons={
        "systems[0].system": {"why": "Trp-cage is 1L2Y."},
        "simulation.duration_ns": {"why": "As asked.", "asked": True}})))
    proposal = propose(turn)
    assert proposal.accepted and proposal.cycles == 1
    assert set(proposal.config["decisions"]) == {"simulation.duration_ns"}
    wrong = dict(STUDY, setup={"not_a_setting": 1})
    turn = Scripted(said(call("propose_config", config=wrong,
                              reasons={"systems[0]": "Trp-cage."})),
                    said(call("propose_config", 2, config=STUDY)))
    assert propose(turn).accepted
    told = turn.asked[1]["messages"][-1]["results"][0]["content"]
    assert "none was kept for: systems[0]." in told

    # The person's own decision stays theirs.
    mine = dict(STUDY, decisions={"simulation.temperature_K": {
        "why": "Our assay runs at 310 K.", "source": "person"}})
    kept, dropped = _reasons_into(mine, {"simulation.temperature_K": "A common default."},
                                  "at 310 K")
    assert kept["decisions"]["simulation.temperature_K"]["why"] == "Our assay runs at 310 K."
    assert dropped == []

    # Asked, says the AI model; the request decides.
    claimed = {"simulation.temperature_K": {"why": "As asked.", "asked": True}}
    said_so, _ = _reasons_into(STUDY, claimed, "run it at 310 K for 10 ns")
    not_said, _ = _reasons_into(STUDY, claimed, "run it warm for 10 ns")
    assert said_so["decisions"]["simulation.temperature_K"]["source"] == "person"
    assert not_said["decisions"]["simulation.temperature_K"]["source"] == "agent"
    named, _ = _reasons_into(STUDY, {"systems": {"why": "Named.", "asked": True}},
                             "simulate 1l2y")
    assert named["decisions"]["systems"]["source"] == "person"


@pytest.mark.parametrize("reply", [
    "simulaton:\n  duration_ns: 10\nsystems:\n  - system: 1L2Y\n",
    "Here it is:\n```yaml\nsystems:\n  - system: 1L2Y\nsimulation:\n  duraton_ns: 10\n```",
])
def test_a_written_config_with_a_misspelling_is_refused_by_name(reply):
    for beside in ((), (call("show_scene", frame=1),)):
        turn = Scripted(*[said(*beside, text=reply)] * 3)
        proposal = propose(turn)
        assert proposal.answer is None and proposal.cycles == 3
        assert {a.refusal.code for a in proposal.attempts} == {"config.option.unknown"}


def test_an_answer_that_shows_a_snippet_of_yaml_stays_an_answer():
    answer = ("Set the pH in the setup block, like this:\n\n```yaml\nsetup:\n  ph: 6.5\n```"
              "\n\nEverything else can stay as it is.")
    turn = Scripted(said(text=answer))
    proposal = propose(turn, request="how do I set the pH?")
    assert proposal.answer == answer and len(turn.asked) == 1


def test_an_action_written_as_the_page_records_one_is_read_as_one():
    # The page keeps an action in the thread as "DO: run", and an AI model
    # that reads it may reply the same way.
    history = [{"role": "user", "text": "run it"}, {"role": "agent", "text": "DO: run"}]
    assert propose(Scripted(said(text="DO: stop")), request="stop it",
                   history=history).action == "stop"
    again = propose(Scripted(said(text="DO: rerun windows 3 5 at 6000")))
    assert (again.action, again.arguments["windows"]) == ("rerun windows", [3, 5])
    assert propose(Scripted(said(text="DO: write the report again"))).action == (
        "write the report again")
    assert propose(Scripted(said(text="SAY: Here.\nSHOW: frame 3"))).scene == {"frame": 3}


def test_the_harnesses_ask_as_they_were_registered(monkeypatch):
    import inspect

    from fastmdxplora.agent import evaluate
    from fastmdxplora.validation import agent_looks

    prompts: list[str] = []
    replies = iter([GOOD.replace("duration_ns", "duraton_ns"), GOOD])

    def complete(prompt):
        prompts.append(prompt)
        return next(replies)

    def turn(*args, **kwargs):  # pragma: no cover - never asked
        raise AssertionError("asked by tool calls")

    complete.turn = turn
    proposal = propose_config("Simulate trp-cage for 10 ns", complete, as_registered=True)
    assert proposal.accepted and proposal.protocol == "text"
    assert "What would fix it" not in prompts[1]
    assert "## The study wanted" not in prompts[1]
    assert inspect.signature(evaluate.measure).parameters["as_registered"].default is True

    seen = {}

    def proposed(request, complete, **kwargs):
        seen.update(kwargs)
        raise StudyError("stop here")

    import fastmdxplora.agent as agent_module

    monkeypatch.setattr(agent_module, "propose_config", proposed)
    agent_looks.ask(agent_looks.QUESTIONS[0], 1, complete, with_tools=False)
    assert seen["as_registered"] is True


def test_the_registered_repair_is_the_one_measured():
    refusal = Refusal(code="config.option.unknown", message="Unknown key 'simulaton'.",
                      details={"option": "simulaton", "suggestion": "simulation"})
    registered = repair_prompt_for("simulaton: {}", refusal, give_remedy=False)
    assert registered == "\n".join([
        "That config was refused.", "", "Reason: Unknown key 'simulaton'.", "",
        "The setting at fault is `simulaton`.",
        "The nearest permitted name is `simulation`.",
        "", "Here is what you sent:", "", "simulaton: {}", "", "Send the corrected YAML only."])


# ---------------------------------------------------------------------------
# What the text protocol gained
# ---------------------------------------------------------------------------
GOOD = "systems:\n  - system: 1L2Y\nsimulation:\n  duration_ns: 10\n"


@pytest.mark.parametrize("reply", [
    "Here is the config:\n" + GOOD,
    "Sure, here it is.\n```yaml\n" + GOOD + "```",
    "```yaml\n" + GOOD + "```\nI kept every other setting at its default.",
    GOOD + "\nI kept every other setting at its default.",
])
def test_a_config_among_prose_is_accepted_first_time(reply):
    proposal = propose_config("Simulate trp-cage for 10 ns", lambda prompt: reply)
    assert proposal.accepted and proposal.cycles == 1


def test_a_misspelled_key_is_kept_to_be_refused_by_name():
    assert "system" in _parse("Here is the config:\nsystem:\n  - system: 1L2Y\n"
                              "setup:\n  ph: 7\n")
    assert "Setup" in _parse("Setup:\n  ph: 7\n" + GOOD)


def test_a_repair_keeps_the_request_the_schema_and_the_conversation():
    prompts: list[str] = []
    replies = iter([GOOD.replace("duration_ns", "duraton_ns"), GOOD])

    def complete(prompt):
        prompts.append(prompt)
        return next(replies)

    proposal = propose_config("Simulate trp-cage for 10 ns", complete,
                              history=[{"role": "user", "text": "earlier words"}])
    assert proposal.cycles == 2
    repair = prompts[1]
    assert repair.startswith("That config was refused.")
    assert "## The study wanted\nSimulate trp-cage for 10 ns" in repair
    assert "A FastMDXplora study is a YAML mapping" in repair
    assert "Person: earlier words" in repair
    assert "What would fix it: Rename `simulation.duraton_ns` to `duration_ns`" in repair


def test_a_repair_says_the_remedy_the_registry_allows_and_no_more():
    structural = repair_prompt_for("setup: {box_shape: dodecahedran}", Refusal(
        code="config.option.not_permitted", message="box_shape does not accept it.",
        details={"option": "box_shape", "permitted": ["cube", "dodecahedron"],
                 "suggestion": "dodecahedron"}))
    assert "What would fix it: Set `box_shape` to one of: cube, dodecahedron." in structural
    semantic = repair_prompt_for("setup: {ligand: lig.sdf}", Refusal(
        code="setup.chemistry.protonation_undetermined",
        message="LIG carries groups whose protonation depends on pH.",
        details={"resname": "LIG", "permitted": ["protonated", "deprotonated"]}))
    assert "deprotonated" not in semantic
    assert "suggests nothing; the choice is yours" in semantic


@pytest.mark.parametrize(("reply", "kept"), [
    ("setup.ph: 6.5\n" + GOOD, "setup.ph"),
    (GOOD + "simulation.temperature_K: 340\n", "simulation.temperature_K"),
    ("Here it is:\n" + GOOD + "Setup:\n  ph: 6.5\n", "Setup"),
    ("```yaml\n" + GOOD + "```\nand for the pH:\n```yaml\nsetup:\n  ph: 6.5\n```", "setup"),
    ("setup.ph: 6.5\n```yaml\n" + GOOD + "```", "setup.ph"),
])
def test_a_setting_outside_the_block_is_kept_to_be_refused_by_name(reply, kept):
    assert kept in _parse(reply)


def test_a_label_over_the_block_is_not_a_setting():
    assert _parse("Here:\n```yaml\n" + GOOD + "```\nThat is all.") == yaml.safe_load(GOOD)


# ---------------------------------------------------------------------------
# The callers
# ---------------------------------------------------------------------------
def test_the_config_the_run_used_is_the_one_written_or_what_was_decided(tmp_path):
    from fastmdxplora.gui.agent_panel import _config_the_run_used

    resolved = {"systems": [{"id": "s1", "system": "1L2Y"}], "output": str(tmp_path),
                "setup": {"ph": 7.4, "mutations": None, "box_shape": "cube"},
                "simulation": {"duration_ns": 0.5, "temperature_K": 300.0,
                               "resume_from": None},
                "analysis": {"include": ["rmsd"], "stride": None}}
    (tmp_path / "resolved_config.yml").write_text(yaml.safe_dump(resolved))
    said = _config_the_run_used(tmp_path)
    assert "null" not in said
    assert "ph:" not in said and "temperature_K" not in said  # at their defaults
    assert "box_shape: cube" in said and "include:" in said
    (tmp_path / "exploration.yml").write_text("# a header\nsystems:\n- system: 1L2Y\n"
                                              "setup:\n  ph: 7.4\n")
    said = _config_the_run_used(tmp_path)
    assert said == "systems:\n- system: 1L2Y\nsetup:\n  ph: 7.4"


def test_the_page_is_given_the_reply_the_reasons_and_what_it_cost(monkeypatch, tmp_path):
    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", str(tmp_path))
    import fastmdxplora.agent as agent_module
    from fastmdxplora.gui import agent_panel

    turn = Scripted(said(call("propose_config", config=STUDY, note="As asked.", reasons={
        "simulation.temperature_K": {"why": "Body temperature.", "asked": True}})))

    def complete(prompt):  # pragma: no cover - the tool path is taken
        raise AssertionError("asked in text")

    complete.turn = lambda system, messages, tools, **kw: turn(system, messages, tools)
    monkeypatch.setattr(agent_module, "completion_for", lambda *a, **k: complete)
    events: list[dict] = []
    answer = agent_panel.propose_endpoint({"request": "trp-cage, 10 ns, 310 K"}, None,
                                          emit=events.append)
    assert answer["ok"] and answer["protocol"] == "tools"
    assert answer["note"] == "As asked."
    assert answer["config"]["decisions"]["simulation.temperature_K"]["source"] == "person"
    assert answer["usage"]["calls"] == 1
    assert events[0] == {"type": "begin"}


def test_the_page_is_told_a_config_is_being_written():
    from fastmdxplora.gui.agent_panel import _written_as_it_goes

    events: list[dict] = []

    def complete(prompt):  # pragma: no cover
        return ""

    def turn(system, messages, tools, *, on_text=None, on_call=None):
        on_text("I will write it.")
        on_call("propose_config")
        return said(text="I will write it.")

    complete.turn = turn
    _written_as_it_goes(complete, events.append).turn("s", [], [])
    assert events == [{"type": "begin"}, {"type": "text", "text": "I will write it."},
                      {"type": "text", "text": "Writing the config…"}]


def test_the_command_line_says_an_answer_and_what_it_cost(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", str(tmp_path))
    import argparse

    import fastmdxplora.agent as agent_module
    from fastmdxplora.cli.main import _run_agent

    def complete(prompt):  # pragma: no cover
        raise AssertionError("asked in text")

    complete.turn = lambda system, messages, tools, **kw: said(
        text="A 2 fs step is the usual one with bonds to hydrogen constrained.", tokens=900)
    monkeypatch.setattr(agent_module, "completion_for", lambda *a, **k: complete)
    args = argparse.Namespace(request="what timestep?", request_file=None,
                              agent_mode="assisted", phases="setup,simulation",
                              attempts=None, agent_output=None, budget_hours=None)
    assert _run_agent(args) == 0
    out = capsys.readouterr().out
    assert "AI model: 1 call, 900 tokens in, 10 out" in out
    assert "A 2 fs step is the usual one" in out


def test_a_question_s_candidates_reach_the_page_in_its_words(monkeypatch, tmp_path):
    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", str(tmp_path))
    import fastmdxplora.agent as agent_module
    from fastmdxplora.gui import agent_panel

    def complete(prompt):  # pragma: no cover - the tool path is taken
        raise AssertionError("asked in text")

    complete.turn = lambda system, messages, tools, **kw: said(call(
        "ask_person", question="Which lysozyme?", choices=["1AKI (hen)", "2LZM (T4)"]))
    monkeypatch.setattr(agent_module, "completion_for", lambda *a, **k: complete)
    answer = agent_panel.propose_endpoint({"request": "simulate lysozyme"}, None)
    assert answer["question"] == "Which lysozyme?\n\nCandidates: 1AKI (hen); 2LZM (T4)."
    assert answer["choices"] == ["1AKI (hen)", "2LZM (T4)"]


def test_the_wrappers_pass_on_that_tools_were_turned_away():
    from fastmdxplora.gui.agent_panel import _written_as_it_goes

    noted: list[bool] = []

    def complete(prompt):
        return "SAY: in text."

    def turn(system, messages, tools, *, on_text=None, on_call=None):
        raise NoToolCalling("this model does not support tools")

    complete.turn, complete.turned_away = turn, lambda: noted.append(True)
    proposal = propose_config("hi", _written_as_it_goes(complete, lambda event: None))
    assert proposal.answer == "in text." and noted == [True]


def test_an_ai_app_is_given_the_candidates_the_note_and_the_scene(tmp_path, monkeypatch):
    from fastmdxplora.mcp import App, Workspace
    from tests._mcp_wire import Wire
    from tests.test_an_ai_app_reads_and_checks_studies import _structure

    monkeypatch.setenv("FASTMDXPLORA_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", str(tmp_path / "settings"))
    root = tmp_path / "work"
    root.mkdir()
    _structure(root / "ghg.pdb")
    study = {"systems": [{"system": "ghg.pdb"}], "simulation": {"duration_ns": 5}}
    replies = [said(call("ask_person", question="Which form?", choices=["open", "closed"])),
               said(call("propose_config", config=study, note="Five, as asked.")),
               said(call("show_scene", frame=3), text="It folds by frame 3.")]

    def complete(prompt):  # pragma: no cover - the tool path is taken
        raise AssertionError("asked in text")

    complete.turn = lambda system, messages, tools, **kw: replies.pop(0)
    wire = Wire(App(Workspace.at(root), complete_for=lambda: complete).server())

    def ask_it(request):
        return wire.request("tools/call", {"name": "ask_agent", "arguments": {
            "request": request, "save": False}})["result"]["content"][0]["text"]

    try:
        assert "The Agent asks: Which form?\nCandidates: open; closed." in ask_it("simulate")
        assert "The Agent says: Five, as asked." in ask_it("five ns of ghg.pdb")
        assert ('It proposes a scene: {"frame": 3}. write_scene writes it, once the '
                "person agrees.") in ask_it("when does it fold?")
    finally:
        wire.close()
