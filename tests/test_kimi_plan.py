import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from fastmdxplora.agent.kimi_plan import (
    KimiPlan, _LocalAPI, authorization_url, executable, explanation_output,
)
from fastmdxplora.agent.openai_plan import ConnectionError


@pytest.mark.parametrize("url", ["http://www.kimi.com/login", "https://evil.test/login",
                                  "https://www.kimi.com:444/login", "https://u@www.kimi.com/login",
                                  "https://www.kimi.com/login#token", None])
def test_authorization_url_rejects_untrusted_destinations(url):
    with pytest.raises(ConnectionError):
        authorization_url(url)


@pytest.mark.parametrize("origin", ["https://127.0.0.1:9", "http://evil.test:9",
                                     "http://127.0.0.1:9/path", "http://u@127.0.0.1:9"])
def test_transport_never_forwards_token_outside_owned_loopback(origin):
    with pytest.raises(ConnectionError):
        _LocalAPI(origin, "synthetic-fixture-token")


def test_environment_has_no_inherited_keys_and_refuses_hooks(tmp_path, monkeypatch):
    monkeypatch.setenv("KIMI_API_KEY", "fixture-private")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fixture-private")
    monkeypatch.setenv("NODE_OPTIONS", "--require unsafe")
    client = KimiPlan(tmp_path)
    env = client.environment()
    assert not {"KIMI_API_KEY", "ANTHROPIC_API_KEY", "NODE_OPTIONS"} & env.keys()
    assert env["KIMI_CODE_HOME"] == str(tmp_path / "config")
    (tmp_path / "config/hooks.json").write_text("{}")
    with pytest.raises(ConnectionError, match="hooks"):
        client.environment()


@pytest.mark.parametrize("rows,code", [
    ([{"role": "assistant", "content": "partial"}], 0),
    ([{"role": "assistant", "content": "partial"}, {"role": "meta", "type": "session.resume_hint"}], 1),
    ([{"role": "tool"}, {"role": "meta", "type": "session.resume_hint"}], 0),
    ([{"role": "assistant", "tool_calls": [{"name": "run"}]}, {"role": "meta", "type": "session.resume_hint"}], 0),
])
def test_partial_failed_and_tool_outputs_are_refused(rows, code):
    with pytest.raises(ConnectionError):
        explanation_output(code, "\n".join(json.dumps(row) for row in rows))


def test_installed_kimi_profile_sends_no_tools_to_local_fixture_model(tmp_path):
    """Exercise the real client's agent binding, rather than mirror its flags."""
    try:
        command = executable()
    except ConnectionError:
        pytest.skip("Official Kimi client is not installed")
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(request)
            event = {"id": "fixture", "object": "chat.completion.chunk", "created": 1, "model": "fixture",
                     "choices": [{"index": 0, "delta": {"role": "assistant", "content": "Fixture explanation."}, "finish_reason": None}]}
            end = {"id": "fixture", "object": "chat.completion.chunk", "created": 1, "model": "fixture",
                   "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
            body = ("data: " + json.dumps(event) + "\n\ndata: " + json.dumps(end) + "\n\ndata: [DONE]\n\n").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        client = KimiPlan(tmp_path, client=command)
        client.environment()
        (tmp_path / "config/config.toml").write_text(
            f'default_model = "fixture"\n[providers.fixture]\ntype = "openai"\n'
            f'base_url = "http://127.0.0.1:{server.server_port}/v1"\napi_key = "synthetic-test-key"\n'
            '[models.fixture]\nprovider = "fixture"\nmodel = "fixture"\nmax_context_size = 32000\n'
            'capabilities = ["thinking", "always_thinking"]\nsupport_efforts = ["low", "high"]\n', encoding="utf-8")
        assert client._explain("Explain RMSF; literal ${unknown}; never run MD.", "fixture") == "Fixture explanation."
        assert requests and all(not row.get("tools") for row in requests)
        assert "unknown" in json.dumps(requests)
        assert client._explain("Explain only.", "fixture", reasoning="high") == "Fixture explanation."
        assert requests[-1].get("reasoning_effort") == "high"
        assert not requests[-1].get("tools")
        assert not list((tmp_path / "work").glob("explanation-*"))
    finally:
        server.shutdown()
        server.server_close()
