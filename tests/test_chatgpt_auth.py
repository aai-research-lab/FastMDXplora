from __future__ import annotations

import json
import os
import threading
from http.client import HTTPConnection
from urllib.parse import parse_qs, urlparse

import pytest


@pytest.fixture()
def auth_path(tmp_path, monkeypatch):
    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", str(tmp_path / "config"))
    return tmp_path / "config" / "chatgpt_tokens.json"


def test_chatgpt_uses_the_published_codex_client_without_registration():
    from fastmdxplora.agent.chatgpt import (
        CHATGPT_CLIENT_ID,
        CHATGPT_REDIRECT_PORTS,
        authorization_request,
    )

    request = authorization_request(state="state", verifier="verifier", port=1455)
    query = parse_qs(urlparse(request.url).query)

    assert CHATGPT_CLIENT_ID == "app_EMoamEEZ73f0CkXaXp7hrann"
    assert CHATGPT_REDIRECT_PORTS == (1455, 1457)
    assert query["client_id"] == [CHATGPT_CLIENT_ID]
    assert query["redirect_uri"] == ["http://127.0.0.1:1455/auth/callback"]
    assert query["code_challenge_method"] == ["S256"]
    assert "dynamic" not in request.url.lower()


def test_chatgpt_authorization_code_exchange_uses_form_encoding(monkeypatch):
    from fastmdxplora.agent.chatgpt import _exchange_code

    calls = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps({
                "access_token": "access",
                "refresh_token": "refresh",
                "expires_in": 3600,
            }).encode()

    def fake_urlopen(request, timeout):
        calls.append((request, timeout))
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    _exchange_code("code", "verifier", 1455)

    request, _timeout = calls[0]
    assert request.get_header("Content-type") == "application/x-www-form-urlencoded"
    assert parse_qs(request.data.decode()) == {
        "grant_type": ["authorization_code"],
        "client_id": ["app_EMoamEEZ73f0CkXaXp7hrann"],
        "code": ["code"],
        "redirect_uri": ["http://127.0.0.1:1455/auth/callback"],
        "code_verifier": ["verifier"],
    }


def test_chatgpt_tokens_are_owner_only_and_never_include_the_client_secret(auth_path):
    from fastmdxplora.agent.chatgpt import ChatGPTTokens, load_tokens, save_tokens

    tokens = ChatGPTTokens("access", "refresh", 123.0)
    save_tokens(tokens, auth_path)

    assert load_tokens(auth_path) == tokens
    assert json.loads(auth_path.read_text()) == {
        "access_token": "access",
        "refresh_token": "refresh",
        "expires_at": 123.0,
    }
    assert os.stat(auth_path).st_mode & 0o077 == 0


def test_chatgpt_model_metadata_preserves_provider_reasoning_levels():
    from fastmdxplora.agent.chatgpt import model_metadata_from_response

    models = model_metadata_from_response({
        "models": [{
            "slug": "gpt-5-codex",
            "display_name": "GPT-5 Codex",
            "default_reasoning_level": "high",
            "supported_reasoning_levels": [
                {"effort": "low"}, {"effort": "high"},
            ],
        }],
    })

    assert models == [{
        "id": "gpt-5-codex",
        "label": "GPT-5 Codex",
        "default_reasoning_level": "high",
        "reasoning_levels": ["low", "high"],
    }]


def test_chatgpt_completion_uses_the_official_codex_responses_endpoint(monkeypatch, auth_path):
    from fastmdxplora.agent.chatgpt import ChatGPTTokens, complete
    from fastmdxplora.agent.models import ModelChoice

    monkeypatch.setattr(
        "fastmdxplora.agent.chatgpt.load_tokens",
        lambda path=None: ChatGPTTokens("access", "refresh", 9999999999.0),
    )
    calls = []

    def fake_json(url, *, method="GET", body=None, headers=None, timeout=0):
        calls.append((url, method, body, headers))
        return {"output": [{"content": [{"type": "output_text", "text": "done"}]}]}

    monkeypatch.setattr("fastmdxplora.agent.chatgpt.request_json", fake_json)
    answer = complete(ModelChoice("openai-chatgpt", "gpt-5-codex"), "hello", path=auth_path)

    assert answer == "done"
    assert calls[0][0] == "https://chatgpt.com/backend-api/codex/responses"
    assert calls[0][1] == "POST"
    assert calls[0][2]["model"] == "gpt-5-codex"
    assert calls[0][2]["input"] == "hello"
    assert calls[0][3]["Authorization"] == "Bearer access"
    assert calls[0][3]["originator"] == "fastmdxplora"


def test_chatgpt_sign_in_rejects_a_mismatched_callback_state():
    from fastmdxplora.agent.chatgpt import validate_callback

    with pytest.raises(ValueError, match="state"):
        validate_callback({"code": "code", "state": "wrong"}, expected_state="right")


def test_hosted_gui_refuses_chatgpt_session_routes(tmp_path):
    from fastmdxplora.gui.hosting import Hosting
    from fastmdxplora.gui.server import HOSTED_AUTH_ROUTES, make_handler

    assert {"/api/agent/sign-in", "/api/agent/session", "/auth/callback"} <= HOSTED_AUTH_ROUTES
    hosting = Hosting(tmp_path, frozenset({"app.test"}), "x" * 32)
    server = __import__("http.server", fromlist=["ThreadingHTTPServer"]).ThreadingHTTPServer(
        ("127.0.0.1", 0), make_handler(tmp_path, hosting=hosting))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
        connection.request(
            "POST", "/api/agent/session", "{}",
            {"Host": "app.test", "X-FastMDX-Proxy-Secret": "x" * 32,
             "Content-Type": "application/json"},
        )
        response = connection.getresponse()
        assert response.status == 403
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
