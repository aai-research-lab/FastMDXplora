"""The Agent's reply is shown as it is written, and can be stopped.

A reply came back whole: the thread said "Thinking" for as long as the
model took, a config or a long answer included, and nothing stopped it but
closing the page. The model is now asked for a stream, in content blocks or the
OpenAI chat shape, and the page is sent each piece as it arrives, each look the
Agent takes as it takes it, and the answer it would have had at the end.
The send button stops it while it is written, and the request to the model
is closed with it.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from fastmdxplora.agent.models import PROVIDERS, ModelChoice, completion_for
from fastmdxplora.refusals import StudyError

#: The provider that answers in content blocks, and where its key is read.
BLOCKS = next(name for name, spec in PROVIDERS.items() if spec["auth"] == "x-api-key")
BLOCKS_KEY = str(PROVIDERS[BLOCKS]["env"])


def _provider(events: list[str], *, pause: float = 0.0):
    """A model service that answers every request with these server-sent
    events, and records what it was asked."""
    asked: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802 - the standard library's name
            length = int(self.headers.get("Content-Length") or 0)
            asked.append(json.loads(self.rfile.read(length) or b"{}"))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            try:
                for event in events:
                    self.wfile.write(event.encode("utf-8"))
                    self.wfile.flush()
                    time.sleep(pause)
            except (BrokenPipeError, ConnectionResetError):
                asked[-1]["stopped"] = True

        def log_message(self, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, asked


def _blocks(*pieces: str) -> list[str]:
    events = ['event: message_start\ndata: {"type": "message_start"}\n\n']
    events += [f'event: content_block_delta\ndata: {json.dumps({"type": "content_block_delta", "delta": {"type": "text_delta", "text": p}})}\n\n'
               for p in pieces]
    return events + ['event: message_stop\ndata: {"type": "message_stop"}\n\n']


def _openai(*pieces: str) -> list[str]:
    events = [f'data: {json.dumps({"choices": [{"delta": {"content": p}}]})}\n\n' for p in pieces]
    return events + ["data: [DONE]\n\n"]


class TestTheModelIsReadAsItWrites:
    def test_a_stream_of_content_blocks(self, monkeypatch):
        monkeypatch.setenv(BLOCKS_KEY, "k")
        server, asked = _provider(_blocks("SAY: The box ", "is 6.9 nm", " wide."))
        try:
            complete = completion_for(ModelChoice(
                BLOCKS, "m", base_url=f"http://127.0.0.1:{server.server_port}"))
            pieces: list[str] = []
            said = complete("How wide?", on_text=pieces.append)
        finally:
            server.shutdown()
        assert said == "SAY: The box is 6.9 nm wide."
        assert pieces == ["SAY: The box ", "is 6.9 nm", " wide."]
        assert asked[0]["stream"] is True and complete.streams

    def test_the_openai_shape(self, monkeypatch):
        monkeypatch.setenv("FASTMDX_MODEL_API_KEY", "k")
        server, asked = _provider(_openai("systems:\n", "  - {system: 1UBQ}\n"))
        try:
            complete = completion_for(ModelChoice(
                "compatible", "m", base_url=f"http://127.0.0.1:{server.server_port}"))
            pieces: list[str] = []
            said = complete("ubiquitin", on_text=pieces.append)
        finally:
            server.shutdown()
        assert said == "systems:\n  - {system: 1UBQ}\n" and len(pieces) == 2

    def test_an_error_in_the_stream_is_said(self, monkeypatch):
        monkeypatch.setenv(BLOCKS_KEY, "k")
        server, _ = _provider(['data: {"type": "error", "error": {"message": "overloaded"}}\n\n'])
        try:
            complete = completion_for(ModelChoice(
                BLOCKS, "m", base_url=f"http://127.0.0.1:{server.server_port}"))
            with pytest.raises(StudyError, match="overloaded"):
                complete("x", on_text=lambda piece: None)
        finally:
            server.shutdown()

    def test_without_on_text_it_is_asked_for_whole(self, monkeypatch):
        monkeypatch.setenv(BLOCKS_KEY, "k")
        asked: list[dict] = []

        class Whole(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                asked.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                body = json.dumps({"content": [{"type": "text", "text": "SAY: whole"}]}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Whole)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            complete = completion_for(ModelChoice(
                BLOCKS, "m", base_url=f"http://127.0.0.1:{server.server_port}"))
            assert complete("x") == "SAY: whole"
        finally:
            server.shutdown()
        assert "stream" not in asked[0]


class TestThePanelSendsItOn:
    def test_each_piece_each_look_and_the_answer(self, monkeypatch):
        import fastmdxplora.agent as agent_mod
        from fastmdxplora.agent import tools
        from fastmdxplora.gui.agent_panel import propose_endpoint

        patched = {name: (arguments, what, lambda box, asked: "1UBQ: 76 protein residues")
                   for name, (arguments, what, _) in tools._TOOLS.items()}
        monkeypatch.setattr(tools, "_TOOLS", patched)
        replies = iter(["USE: inspect_structure\nsystem: 1UBQ", "SAY: It has 76 residues."])

        def complete(prompt, on_text=None):
            text = next(replies)
            for piece in (text[:5], text[5:]):
                on_text(piece)
            return text
        complete.streams = True
        monkeypatch.setattr(agent_mod, "completion_for", lambda *a, **k: complete)
        events: list[dict] = []
        answer = propose_endpoint({"request": "How many residues in 1UBQ?"}, None,
                                  emit=events.append)
        kinds = [e["type"] for e in events]
        assert kinds == ["begin", "text", "text", "look", "begin", "text", "text"]
        assert events[3]["look"]["tool"] == "inspect_structure"
        assert "".join(e["text"] for e in events[5:]) == "SAY: It has 76 residues."
        assert answer["answer"] == "It has 76 residues."

    def test_a_completion_that_cannot_stream_is_sent_whole(self, monkeypatch):
        import fastmdxplora.agent as agent_mod
        from fastmdxplora.gui.agent_panel import propose_endpoint

        monkeypatch.setattr(agent_mod, "completion_for",
                            lambda *a, **k: (lambda prompt: "SAY: Whole."))
        events: list[dict] = []
        propose_endpoint({"request": "hello"}, None, emit=events.append)
        assert events == [{"type": "begin"}, {"type": "text", "text": "SAY: Whole."}]


def _post_stream(url: str, body: dict) -> list[dict]:
    import urllib.request

    request = urllib.request.Request(
        url + "/api/agent/propose-stream", data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json", "Origin": url})
    with urllib.request.urlopen(request, timeout=30) as answer:
        assert answer.headers["Content-Type"].startswith("application/x-ndjson")
        return [json.loads(line) for line in answer.read().decode().splitlines() if line]


def test_the_route_streams_and_ends_with_the_answer(tmp_path, monkeypatch) -> None:
    import fastmdxplora.agent as agent_mod
    from fastmdxplora.gui.server import start_dashboard_session

    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", str(tmp_path / "settings"))
    monkeypatch.setattr(agent_mod, "completion_for",
                        lambda *a, **k: (lambda prompt: "SAY: Hello."))
    session = start_dashboard_session(output=str(tmp_path), host="127.0.0.1", port=0)
    try:
        events = _post_stream(session.url.rstrip("/"), {"request": "hi"})
        monkeypatch.setattr(agent_mod, "completion_for", lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("no model")))
        failed = _post_stream(session.url.rstrip("/"), {"request": "hi"})
    finally:
        session.server.shutdown()
    assert [e["type"] for e in events] == ["begin", "text", "done"]
    assert events[-1]["answer"]["answer"] == "Hello."
    assert failed[-1]["type"] == "done" and failed[-1]["answer"]["ok"] is False


def test_the_page_shows_it_and_stops_it(tmp_path, monkeypatch) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    import fastmdxplora.agent as agent_mod
    from fastmdxplora.gui.server import start_dashboard_session

    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", str(tmp_path / "settings"))
    monkeypatch.setenv(BLOCKS_KEY, "k")
    words = [f"word{n} " for n in range(40)]
    server, asked = _provider(_blocks("SAY: ", *words), pause=0.15)
    model = completion_for(ModelChoice(BLOCKS, "m",
                                       base_url=f"http://127.0.0.1:{server.server_port}"))
    monkeypatch.setattr(agent_mod, "completion_for", lambda *a, **k: model)
    session = start_dashboard_session(output=str(tmp_path), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.set_default_timeout(60000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url + "#agent", wait_until="domcontentloaded")
            page.fill("#agent-request", "Tell me a long story.")
            page.keyboard.press("Enter")
            # Written into the thread as it arrives, without its marker.
            page.wait_for_function(
                "() => (document.querySelector('#agent-thread .agent-writing') || {})"
                ".textContent?.includes('word3')")
            partly = page.text_content("#agent-thread .agent-writing")
            button = page.get_attribute("#agent-propose", "aria-label")
            page.click("#agent-propose")
            page.wait_for_selector("#agent-thread .agent-attempt:has-text('Stopped before it finished.')")
            after = page.get_attribute("#agent-propose", "aria-label")
            browser.close()
    finally:
        session.server.shutdown()
        server.shutdown()
    assert partly.startswith("word0 word1") and "SAY:" not in partly
    assert "word39" not in partly
    assert button == "Stop" and after == "Send"
    # The model's request was closed with it, before it had written all.
    deadline = time.time() + 10
    while not asked[0].get("stopped") and time.time() < deadline:
        time.sleep(0.2)
    assert asked[0].get("stopped")
    assert errors == []
