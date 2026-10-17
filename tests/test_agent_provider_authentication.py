"""Provider authentication remains isolated from UI and ordinary settings."""

from __future__ import annotations

import hashlib
import os
import json
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from io import BytesIO
from pathlib import Path

import pytest


@pytest.fixture
def chatgpt_vault(monkeypatch, tmp_path):
    from fastmdxplora.agent import models
    from fastmdxplora.agent import openai_chatgpt as oauth

    values = {}

    class Vault:
        def get_password(self, service, username):
            return values.get((service, username))

        def set_password(self, service, username, value):
            values[(service, username)] = value

        def delete_password(self, service, username):
            if (service, username) not in values:
                raise ValueError("Password not found")
            del values[(service, username)]

    vault = Vault()
    monkeypatch.setattr(models, "_secure_keyring", lambda: vault)
    monkeypatch.setattr(oauth, "user_config_dir", lambda: tmp_path)
    monkeypatch.setattr(oauth, "_pending", {})
    monkeypatch.setattr(oauth, "_pending_note", "")
    return oauth, vault


def _account(*, expires_at, scopes=None):
    return {
        "client_id": "oaiapp_test",
        "subject": "subject-test",
        "issuer": "https://auth.openai.com",
        "email": "test@example.invalid",
        "ext_agent_host_id": "urn:uuid:test-host",
        "id_token": "identity-token-test",
        "access_token": "access-token-test",
        "refresh_token": "refresh-token-test",
        "token_type": "Bearer",
        "expires_at": expires_at,
        "scopes": scopes or ["openid", "offline_access", "resource.invoke",
                              "chatgpt.tokens.use.direct"],
    }


def _enable_account(oauth, account):
    oauth._write_store({"host_id": "urn:uuid:test-host",
                        "active_client_id": account["client_id"],
                        "accounts": [account]})


def test_settings_save_auth_mode_without_credentials_and_use_matching_endpoint(
        monkeypatch, tmp_path):
    from fastmdxplora.agent.models import ModelChoice, load_choice, save_choice
    from fastmdxplora.gui.agent_panel import model_endpoint

    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", str(tmp_path))
    choice = ModelChoice("openai", "gpt-6", auth_method="chatgpt_plan")
    assert choice.url == "https://api.openai.com/v1/responses"
    save_choice(choice)
    saved = load_choice()
    assert (saved.provider, saved.model, saved.auth_method) == (
        "openai", "gpt-6", "chatgpt_plan")
    record = json.loads(Path(tmp_path / "model.json").read_text())
    assert record == {"provider": "openai", "model": "gpt-6",
                      "auth_method": "chatgpt_plan"}

    providers = model_endpoint({})["providers"]
    openai = next(row for row in providers if row["id"] == "openai")
    siwc = next(row for row in openai["auth_methods"]
                if row["id"] == "chatgpt_plan")
    assert siwc["available"] is True
    assert siwc["requires_api_key"] is False


def test_console_login_retry_uses_pending_registration_without_assuming_saved_account(
        monkeypatch, chatgpt_vault):
    oauth, _vault = chatgpt_vault
    oauth._write_store({"host_id": "urn:uuid:retry-host", "active_client_id": "",
                        "pending_client_id": "oaiapp_pending", "accounts": []})
    monkeypatch.setattr(oauth, "_exchange", lambda _form: {
        "access_token": "access-token-retry", "refresh_token": "refresh-token-retry",
        "id_token": "signed-identity-token", "token_type": "Bearer", "expires_in": 3600,
        "scope": "openid profile email offline_access resource.invoke "
                 "chatgpt.tokens.use.direct"})
    monkeypatch.setattr(oauth, "_verify_id_token", lambda *_a, **_kw: {
        "iss": oauth.ISSUER, "sub": "retry-account", "email": "retry@example.invalid"})

    def open_authorize(url, **_kwargs):
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        assert query["client_id"] == ["oaiapp_pending"]
        assert query["prompt"] == ["consent"]
        assert "agent_name_hint" not in query
        assert "id_token_hint" not in query
        assert "login_hint" not in query
        callback = urllib.parse.urlsplit(query["redirect_uri"][0])
        callback_query = urllib.parse.urlencode({"code": "retry-code",
                                                 "state": query["state"][0],
                                                 "client_id": "oaiapp_pending"})
        with urllib.request.urlopen(urllib.parse.urlunsplit(
                (callback.scheme, callback.netloc, callback.path, callback_query, ""))) as response:
            assert response.status == 200
        return True

    monkeypatch.setattr(oauth.webbrowser, "open", open_authorize)
    assert oauth.connect()["ok"] is True
    saved = oauth._read_store()
    assert saved["pending_client_id"] == ""
    assert saved["active_client_id"] == "oaiapp_pending"
    assert saved["accounts"][0]["subject"] == "retry-account"


def test_chatgpt_registration_callback_uses_pkce_and_keeps_tokens_out_of_settings(
        monkeypatch, tmp_path, chatgpt_vault):
    oauth, _vault = chatgpt_vault
    exchanged = []
    verified = []

    def exchange(form):
        exchanged.append(form)
        return {"access_token": "access-token-test", "refresh_token": "refresh-token-test",
                "id_token": "signed-identity-token", "token_type": "Bearer",
                "expires_in": 3600,
                "scope": "openid profile email offline_access resource.invoke "
                         "chatgpt.tokens.use.direct"}

    def verify(token, *, client_id, nonce, timeout=15.0):
        verified.append((token, client_id, nonce))
        return {"iss": oauth.ISSUER, "sub": "account-subject", "email": "a@example.invalid"}

    monkeypatch.setattr(oauth, "_exchange", exchange)
    monkeypatch.setattr(oauth, "_verify_id_token", verify)
    seen = {}

    def open_authorize(url, **_kwargs):
        parsed = urllib.parse.urlsplit(url)
        query = urllib.parse.parse_qs(parsed.query)
        seen.update(query)
        assert query["client_id"] == ["dynamic_agent_client"]
        assert query["agent_name_hint"] == ["FastMDXplora"]
        assert query["redirect_uri"][0].startswith("http://127.0.0.1:")
        assert urllib.parse.urlsplit(query["redirect_uri"][0]).path == "/auth/callback"
        assert query["resource"] == [oauth.RESOURCE]
        assert query["code_challenge_method"] == ["S256"]
        assert "chatgpt.tokens.use.direct" in query["scope"][0]
        callback = urllib.parse.urlsplit(query["redirect_uri"][0])
        callback_query = urllib.parse.urlencode({"code": "one-time-code",
                                                 "state": query["state"][0],
                                                 "client_id": "oaiapp_test"})
        with urllib.request.urlopen(urllib.parse.urlunsplit(
                (callback.scheme, callback.netloc, callback.path, callback_query, ""))) as response:
            assert response.status == 200
        return True

    monkeypatch.setattr(oauth.webbrowser, "open", open_authorize)
    result = oauth.connect()
    assert result["ok"] is True
    assert "code_verifier" in exchanged[0]
    assert exchanged[0]["client_id"] == "oaiapp_test"
    assert exchanged[0]["resource"] == oauth.RESOURCE
    assert "client_secret" not in exchanged[0]
    assert verified[0][0] == "signed-identity-token"
    assert verified[0][1] == "oaiapp_test"
    record = oauth._read_store()
    account = record["accounts"][0]
    assert record["active_client_id"] == "oaiapp_test"
    assert account["subject"] == "account-subject"
    assert oauth.access_token() == "access-token-test"
    settings = tmp_path / "model.json"
    settings.write_text(json.dumps({"provider": "openai", "model": "gpt-6",
                                    "auth_method": "chatgpt_plan"}))
    assert "access-token-test" not in settings.read_text()
    assert "refresh-token-test" not in settings.read_text()


def test_chatgpt_callback_removes_the_one_time_code_from_the_browser_url(
        monkeypatch, chatgpt_vault):
    playwright = pytest.importorskip("playwright.sync_api")
    from fastmdxplora.agent import openai_chatgpt as oauth

    monkeypatch.setattr(oauth, "_exchange", lambda _form: {
        "access_token": "synthetic-access", "refresh_token": "synthetic-refresh",
        "id_token": "synthetic-identity", "expires_in": 3600,
        "scope": oauth.SCOPES,
    })
    monkeypatch.setattr(oauth, "_verify_id_token", lambda *_a, **_kw: {
        "sub": "synthetic-subject", "email": "synthetic@example.invalid",
    })
    opened = []
    monkeypatch.setattr(oauth.webbrowser, "open",
                        lambda url, **_kwargs: opened.append(url) or True)
    assert oauth.connect()["ok"]
    authorize = urllib.parse.urlsplit(opened[0])
    query = urllib.parse.parse_qs(authorize.query)
    redirect = query["redirect_uri"][0]
    callback = redirect + "?" + urllib.parse.urlencode({
        "code": "synthetic-one-time-code", "state": query["state"][0],
        "client_id": "oaiapp_synthetic",
    })

    with playwright.sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page()
            page.goto(callback, wait_until="load")
            assert page.url == redirect
            assert page.evaluate("location.search") == ""
            assert "synthetic-one-time-code" not in page.content()
        finally:
            browser.close()


def test_chatgpt_id_token_signature_issuer_audience_expiry_and_nonce_are_checked(
        monkeypatch):
    from fastmdxplora.agent import openai_chatgpt as oauth

    modulus = int(
        "9d0b9558969c6516a0614e724784cd45aab568f23cfc7f408f0f02fbbefee9da"
        "9d6ba0aa067f6d1ab0e28699f77810791b4840edb80614873b532c359f98b480"
        "6b9aac9b12a94150300b393fd4a872f6703d19e0fd09faa7fa1e528b52b241d"
        "ab4171ed17219a208fa0178652917c583708d344e248eb1c49233016c3ab66503", 16)
    private = int(
        "419616d71a7f525072c670a6956bc6f6b36673dfc6cc84b0878e736683cb9e0a"
        "a8f7cbef762b3d7b8a27f0a2d891120021feb8ef0e8dab8116ab93a9097012e"
        "16db7393e6f0acbb6479fded6ac595ae6a9704da188f9d8cafab4a5cd1c1620"
        "c65a968435aeeb1fceac4f249ef26aa7d59afeb999fff2b3102b0bbb9cb4f40941", 16)
    exponent = 65537
    nonce = "nonce-from-the-authorize-request"
    key_id = "test-rsa-signing-key"
    jwk = {"kid": key_id, "kty": "RSA", "use": "sig", "alg": "RS256",
           "n": oauth._b64url(modulus.to_bytes((modulus.bit_length() + 7) // 8, "big")),
           "e": oauth._b64url(exponent.to_bytes(3, "big"))}
    monkeypatch.setattr(oauth, "_fetch_json", lambda url, **_kwargs: (
        {"issuer": oauth.ISSUER, "jwks_uri": oauth.ISSUER + "/.well-known/jwks.json"}
        if url == oauth.DISCOVERY_URL else {"keys": [jwk]}))

    def signed_token(*, token_nonce=nonce, audience="oaiapp_test", expiry=None,
                     issuer=oauth.ISSUER):
        header = oauth._b64url(json.dumps({"alg": "RS256", "kid": key_id},
                                          separators=(",", ":")).encode())
        claims = oauth._b64url(json.dumps({
            "iss": issuer, "sub": "verified-subject", "aud": audience,
            "exp": expiry if expiry is not None else int(time.time()) + 3600,
            "nonce": token_nonce,
        }, separators=(",", ":")).encode())
        encoded = f"{header}.{claims}"
        digest = hashlib.sha256(encoded.encode("ascii")).digest()
        digest_info = bytes.fromhex("3031300d060960864801650304020105000420") + digest
        size = (modulus.bit_length() + 7) // 8
        padding = size - len(digest_info) - 3
        block = b"\x00\x01" + b"\xff" * padding + b"\x00" + digest_info
        signature = pow(int.from_bytes(block, "big"), private, modulus).to_bytes(size, "big")
        return encoded + "." + oauth._b64url(signature)

    token = signed_token()
    claims = oauth._verify_id_token(token, client_id="oaiapp_test", nonce=nonce)
    assert claims["sub"] == "verified-subject"
    encoded, signature = token.rsplit(".", 1)
    noncanonical = encoded + "." + oauth._b64url(b"\0" + oauth._unb64url(signature))
    for bad_token, client_id, expected_nonce in (
            (noncanonical, "oaiapp_test", nonce),
            (signed_token(token_nonce="wrong"), "oaiapp_test", nonce),
            (signed_token(audience="different-client"), "oaiapp_test", nonce),
            (signed_token(issuer="https://foreign.invalid"), "oaiapp_test", nonce),
            (signed_token(expiry=int(time.time()) - 1), "oaiapp_test", nonce),
            (signed_token(expiry="invalid-date"), "oaiapp_test", nonce),
            (token.rsplit(".", 1)[0] + ".invalid-signature", "oaiapp_test", nonce)):
        with pytest.raises(oauth.SignInError):
            oauth._verify_id_token(bad_token, client_id=client_id, nonce=expected_nonce)


def test_chatgpt_cancelled_browser_consent_does_not_exchange_or_replace_account(
        monkeypatch, chatgpt_vault):
    oauth, _vault = chatgpt_vault
    called = []
    connection_at_response = []
    finished = []
    finish = oauth._finish_attempt
    answer = oauth._CallbackHandler._answer

    def finish_once(state):
        finished.append(state)
        return finish(state)

    def answer_after_invalidation(handler, status_code, message):
        if status_code == 400 and "cancelled" in message.lower():
            connection_at_response.append(oauth.status()["connected"])
        answer(handler, status_code, message)

    monkeypatch.setattr(oauth._CallbackHandler, "_answer", answer_after_invalidation)
    monkeypatch.setattr(oauth, "_finish_attempt", finish_once)

    def open_authorize(url, **_kwargs):
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        callback = urllib.parse.urlsplit(query["redirect_uri"][0])
        callback_query = urllib.parse.urlencode({"error": "access_denied",
                                                 "state": query["state"][0]})
        with pytest.raises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(urllib.parse.urlunsplit(
                (callback.scheme, callback.netloc, callback.path, callback_query, "")))
        assert caught.value.code == 400
        return True

    monkeypatch.setattr(oauth, "_exchange", lambda _form: called.append(True))
    monkeypatch.setattr(oauth.webbrowser, "open", open_authorize)
    result = oauth.connect()
    assert result["ok"] is True
    assert called == []
    assert connection_at_response == [False]
    assert len(finished) == 1
    assert finish(finished[0]) is False
    assert not oauth._read_store()["accounts"]
    status = oauth.status()
    assert status["connected"] is False
    assert "cancelled" in status["message"].lower()


def test_invalid_identity_callback_keeps_only_the_unverified_registration_for_retry(
        monkeypatch, chatgpt_vault):
    oauth, _vault = chatgpt_vault

    monkeypatch.setattr(oauth, "_exchange", lambda _form: {
        "access_token": "access-token-test", "refresh_token": "refresh-token-test",
        "id_token": "bad-id-token", "token_type": "Bearer", "expires_in": 3600,
        "scope": "openid offline_access resource.invoke chatgpt.tokens.use.direct"})
    monkeypatch.setattr(oauth, "_verify_id_token",
                        lambda *_a, **_kw: (_ for _ in ()).throw(
                            oauth.SignInError("OpenAI identity did not validate.")))

    def open_authorize(url, **_kwargs):
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        callback = urllib.parse.urlsplit(query["redirect_uri"][0])
        callback_query = urllib.parse.urlencode({"code": "code", "state": query["state"][0],
                                                 "client_id": "oaiapp_pending"})
        with pytest.raises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(urllib.parse.urlunsplit(
                (callback.scheme, callback.netloc, callback.path, callback_query, "")))
        assert caught.value.code == 400
        return True

    monkeypatch.setattr(oauth.webbrowser, "open", open_authorize)
    oauth.connect()
    stored = oauth._read_store()
    assert stored["pending_client_id"] == "oaiapp_pending"
    assert stored["active_client_id"] == ""
    assert stored["accounts"] == []
    assert oauth.status()["connected"] is False


def test_expired_chatgpt_access_token_refresh_rotates_tokens_under_the_saved_client(
        monkeypatch, chatgpt_vault):
    oauth, _vault = chatgpt_vault
    _enable_account(oauth, _account(expires_at=0))
    asked = []

    def exchange(form):
        asked.append(form)
        return {"access_token": "access-token-rotated",
                "refresh_token": "refresh-token-rotated", "token_type": "Bearer",
                "expires_in": 3600,
                "scope": "openid offline_access resource.invoke chatgpt.tokens.use.direct"}

    monkeypatch.setattr(oauth, "_exchange", exchange)
    assert oauth.access_token() == "access-token-rotated"
    assert asked == [{"grant_type": "refresh_token", "client_id": "oaiapp_test",
                      "refresh_token": "refresh-token-test", "resource": oauth.RESOURCE}]
    saved = oauth._read_store()["accounts"][0]
    assert saved["refresh_token"] == "refresh-token-rotated"
    assert saved["access_token"] == "access-token-rotated"


def test_expired_revoked_refresh_grant_is_cleared_but_registration_remains(
        monkeypatch, chatgpt_vault):
    oauth, _vault = chatgpt_vault
    _enable_account(oauth, _account(expires_at=0))
    monkeypatch.setattr(oauth, "_exchange", lambda _form: (_ for _ in ()).throw(
        oauth.InvalidGrant("The refresh token expired.")))
    with pytest.raises(oauth.InvalidGrant):
        oauth.access_token()
    saved = oauth._read_store()
    assert saved["active_client_id"] == ""
    assert saved["accounts"][0]["client_id"] == "oaiapp_test"
    assert not any(name in saved["accounts"][0] for name in
                   ("access_token", "refresh_token", "id_token", "scopes"))


@pytest.mark.parametrize("error", ["refresh_token_expired", {"code": "invalid_refresh_token"}])
def test_terminal_token_endpoint_errors_are_safe_and_require_new_sign_in(monkeypatch, error):
    from fastmdxplora.agent import openai_chatgpt as oauth

    def rejected(_request, **_kwargs):
        raise urllib.error.HTTPError(
            oauth.TOKEN_URL,
            400,
            "bad grant",
            {},
            BytesIO(json.dumps({"error": error, "secret": "test-secret"}).encode()),
        )

    monkeypatch.setattr("fastmdxplora.agent.openai_chatgpt._open_https", rejected)
    with pytest.raises(oauth.InvalidGrant) as caught:
        oauth._exchange({"grant_type": "refresh_token"})
    assert "test-secret" not in str(caught.value)


def test_failed_account_switch_keeps_the_previous_chatgpt_account(monkeypatch, chatgpt_vault):
    oauth, _vault = chatgpt_vault
    _enable_account(oauth, _account(expires_at=9999999999))
    other = _account(expires_at=9999999999)
    other["client_id"] = "oaiapp_other"
    store = oauth._read_store()
    store["accounts"].append(other)
    oauth._write_store(store)
    monkeypatch.setattr(oauth, "access_token", lambda: (_ for _ in ()).throw(
        oauth.SignInError("Temporary provider failure.")))
    assert oauth.activate(client_id="oaiapp_other")["ok"] is False
    assert oauth._read_store()["active_client_id"] == "oaiapp_test"


def test_chatgpt_disconnect_revokes_and_clears_tokens_but_keeps_client_registration(
        monkeypatch, chatgpt_vault):
    oauth, _vault = chatgpt_vault
    _enable_account(oauth, _account(expires_at=9999999999))
    monkeypatch.setattr(oauth, "_fetch_json", lambda _url: {
        "revocation_endpoint": oauth.ISSUER + "/api/accounts/oauth/revoke"})
    sent = []

    class Reply(BytesIO):
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

    def urlopen(request, timeout=None, **_kwargs):
        sent.append(request)
        return Reply(b"")

    monkeypatch.setattr("fastmdxplora.agent.openai_chatgpt._open_https", urlopen)
    result = oauth.disconnect()
    assert result["remote_revoked"] is True
    body = urllib.parse.parse_qs(sent[0].data.decode("ascii"))
    assert body == {"token": ["refresh-token-test"],
                    "token_type_hint": ["refresh_token"],
                    "client_id": ["oaiapp_test"]}
    saved = oauth._read_store()
    assert saved["active_client_id"] == ""
    assert saved["accounts"][0]["client_id"] == "oaiapp_test"
    assert not any(name in saved["accounts"][0] for name in
                   ("access_token", "refresh_token", "id_token", "scopes"))


def test_chatgpt_disconnect_reports_unconfirmed_remote_revocation_without_secrets(
        monkeypatch, chatgpt_vault):
    oauth, _vault = chatgpt_vault
    _enable_account(oauth, _account(expires_at=9999999999))
    monkeypatch.setattr(oauth, "_fetch_json", lambda _url: {
        "revocation_endpoint": oauth.ISSUER + "/api/accounts/oauth/revoke"})
    monkeypatch.setattr(oauth.time, "sleep", lambda _seconds: None)
    requests = []

    def unavailable(request, **_kw):
        requests.append(request)
        raise urllib.error.HTTPError(request.full_url, 503, "Unavailable", {},
                                     BytesIO(b"refresh-token-test"))

    monkeypatch.setattr(oauth, "_open_https", unavailable)
    answer = oauth.disconnect()
    assert answer["ok"] and not answer["remote_revoked"]
    assert "remote revocation was not confirmed" in answer["message"]
    assert len(requests) == 3
    assert "refresh-token-test" not in json.dumps(answer)
    saved = oauth._read_store()
    assert saved["active_client_id"] == ""
    assert not saved["accounts"][0].get("refresh_token")
    # With no token left to revoke, a second click cannot prove remote success.
    assert oauth.disconnect()["remote_revoked"] is None
    assert len(requests) == 3


def test_disconnecting_a_pending_chatgpt_callback_cannot_reconnect_it(
        monkeypatch, chatgpt_vault):
    import threading

    oauth, _vault = chatgpt_vault
    urls = []
    started, release = threading.Event(), threading.Event()
    monkeypatch.setattr(oauth.webbrowser, "open", lambda url, **_kw: urls.append(url) or True)
    monkeypatch.setattr(oauth, "_verify_id_token", lambda *_a, **_kw: {
        "sub": "subject", "email": "test@example.invalid"})

    def exchange(_form):
        started.set()
        assert release.wait(5)
        return {"access_token": "test-access", "refresh_token": "test-refresh",
                "id_token": "test-identity", "token_type": "Bearer", "expires_in": 3600,
                "scope": oauth.SCOPES}

    monkeypatch.setattr(oauth, "_exchange", exchange)
    assert oauth.connect()["ok"]
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(urls[0]).query)
    callback = query["redirect_uri"][0] + "?" + urllib.parse.urlencode({
        "state": query["state"][0], "code": "test-code", "client_id": "oaiapp_pending"})
    results = []

    def finish_callback():
        try:
            urllib.request.urlopen(callback, timeout=5)
        except urllib.error.HTTPError as exc:
            results.append(exc.code)

    worker = threading.Thread(target=finish_callback)
    worker.start()
    try:
        assert started.wait(3)
        assert oauth.disconnect()["ok"]
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive()
    assert results == [400]
    assert oauth._read_store()["accounts"] == []
    assert oauth._pending == {}


def test_chatgpt_model_catalog_uses_public_list_and_only_visible_models(
        monkeypatch, chatgpt_vault):
    oauth, _vault = chatgpt_vault
    monkeypatch.setattr(oauth, "access_token", lambda: "route-test-token")

    class Reply(BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

    requests = []

    def urlopen(request, timeout=None, **_kwargs):
        requests.append(request)
        return Reply(json.dumps({"models": [
            {"slug": "gpt-visible", "display_name": "GPT Visible", "visibility": "list"},
            {"slug": "gpt-hidden", "display_name": "GPT Hidden", "visibility": "private"},
        ]}).encode())

    monkeypatch.setattr("fastmdxplora.agent.openai_chatgpt._open_https", urlopen)
    assert oauth.list_models() == [{"id": "gpt-visible", "label": "GPT Visible"}]
    assert requests[0].full_url == "https://api.openai.com/v1/models"
    assert requests[0].get_header("Authorization") == "Bearer route-test-token"


def test_responses_route_sends_full_context_and_reads_function_calls(
        monkeypatch):
    from fastmdxplora.agent import turns
    from fastmdxplora.agent.turns import ToolCall, ToolSpec

    events = [
        {"type": "response.output_text.delta", "delta": "Checking."},
        {"type": "response.output_item.added", "item": {
            "id": "item_1", "type": "function_call", "call_id": "call_1",
            "name": "read_current_view", "arguments": ""}},
        {"type": "response.function_call_arguments.delta", "item_id": "item_1",
         "delta": "{\"selected\":true}"},
        {"type": "response.output_item.done", "item": {
            "id": "item_1", "type": "function_call", "call_id": "call_1",
            "name": "read_current_view", "arguments": "{\"selected\":true}"}},
        {"type": "response.completed", "response": {
            "status": "completed", "usage": {"input_tokens": 18, "output_tokens": 4},
            "output": [{"id": "item_1", "type": "function_call",
                        "call_id": "call_1", "name": "read_current_view",
                        "arguments": "{\"selected\":true}"}]}},
    ]

    class Reply:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def __iter__(self):
            return iter([("data: " + json.dumps(event) + "\n\n").encode()
                         for event in events])

    sent = []

    def urlopen(request, timeout=None, **_kwargs):
        sent.append(request)
        return Reply()

    monkeypatch.setattr("fastmdxplora.agent.openai_chatgpt._open_https", urlopen)
    text = []
    called = []
    turn = turns.take_turn(
        url="https://api.openai.com/v1/responses",
        headers={"Authorization": "Bearer route-test-token"}, shape="responses",
        model="gpt-visible", system="Use verified study evidence.",
        messages=[{"role": "user", "text": "Inspect this study."},
                  {"role": "assistant", "text": "Reading context.",
                   "calls": [ToolCall("call_old", "read_current_view", {})]},
                  {"role": "results", "results": [{"id": "call_old",
                                                       "name": "read_current_view",
                                                       "content": "done"}]},
                  {"role": "user", "text": "Continue."}],
        tools=[ToolSpec("read_current_view", "Read current view", {
            "type": "object", "properties": {"selected": {"type": "boolean"}}})],
        label="OpenAI", on_text=text.append, on_call=called.append)
    body = json.loads(sent[0].data.decode("utf-8"))
    assert sent[0].full_url == "https://api.openai.com/v1/responses"
    assert body["instructions"] == "Use verified study evidence."
    assert body["input"][-2] == {"type": "function_call_output", "call_id": "call_old",
                                  "output": "done"}
    assert body["store"] is False and body["stream"] is True
    assert not ({"previous_response_id", "max_output_tokens", "tool_search"} & body.keys())
    assert body["input"][0] == {
        "type": "additional_tools", "role": "developer",
        "tools": [{"type": "function", "name": "read_current_view",
                   "description": "Read current view",
                   "parameters": {"type": "object", "properties": {
                       "selected": {"type": "boolean"}}}}],
    }
    assert turn.text == "Checking."
    assert text == ["Checking."]
    assert called == ["read_current_view"]
    assert turn.calls == (ToolCall("call_1", "read_current_view", {"selected": True}),)
    assert turn.usage.input_tokens == 18 and turn.usage.output_tokens == 4


def test_responses_stream_requires_completed_and_reports_incomplete_or_failed(monkeypatch):
    from fastmdxplora.agent import turns
    from fastmdxplora.refusals import StudyError

    class Reply:
        def __init__(self, events):
            self.events = events

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def __iter__(self):
            return iter([("data: " + json.dumps(event) + "\n\n").encode()
                         for event in self.events])

    for events in ([{"type": "response.output_text.delta", "delta": "partial"}],
                   [{"type": "response.incomplete", "response": {
                       "incomplete_details": {"reason": "max_output_tokens"}}}],
                   [{"type": "response.failed", "response": {
                       "error": {"code": "subscription_sharing_usage_limit_exceeded"}}}]):
        monkeypatch.setattr(
            "fastmdxplora.agent.openai_chatgpt._open_https",
            lambda *_a, events=events, **_kw: Reply(events)
        )
        with pytest.raises(StudyError):
            turns.take_turn(url="https://api.openai.com/v1/responses",
                            headers={"Authorization": "Bearer route-test-token"},
                            shape="responses", model="gpt-visible", system="",
                            messages=[{"role": "user", "text": "hi"}], tools=[],
                            label="OpenAI")


def test_anthropic_console_profile_never_inherits_or_reads_other_cli_credentials(
        monkeypatch, tmp_path):
    from fastmdxplora.agent import anthropic_cli

    monkeypatch.setattr(anthropic_cli, "config_dir", lambda: tmp_path / "anthropic-console")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-not-inherit")
    monkeypatch.setenv("ANTHROPIC_CONFIG_DIR", str(tmp_path / "some-other-profile"))
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://not-used.invalid")
    monkeypatch.setenv("DISPLAY", ":99")
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/run/user/test/bus")
    env = anthropic_cli._environment()
    assert env["ANTHROPIC_CONFIG_DIR"] == str(tmp_path / "anthropic-console")
    assert "ANTHROPIC_API_KEY" not in env
    assert "ANTHROPIC_BASE_URL" not in env
    assert env["DISPLAY"] == ":99"
    assert env["DBUS_SESSION_BUS_ADDRESS"] == "unix:path=/run/user/test/bus"
    assert str(tmp_path / "some-other-profile") not in env.values()


def test_anthropic_console_uses_documented_bearer_token_command_and_logout(monkeypatch):
    from subprocess import CompletedProcess

    from fastmdxplora.agent import anthropic_cli

    commands = []

    def run(args, **_kwargs):
        commands.append(args)
        return CompletedProcess(args, 0, stdout="console-oauth-token\n", stderr="")

    monkeypatch.setattr(anthropic_cli, "executable", lambda: "ant")
    monkeypatch.setattr(anthropic_cli, "_run", run)
    assert anthropic_cli.access_token() == "console-oauth-token"
    assert commands[-1] == ["auth", "print-credentials", "--access-token"]
    assert anthropic_cli.disconnect()["ok"] is True
    assert commands[-1] == ["auth", "logout", "--all"]


@pytest.mark.parametrize("exit_code,finished,pending,connected", [
    (1, None, True, None), (1, 1, False, False), (0, None, False, True),
])
def test_console_login_is_connected_only_after_api_access_succeeds(
        monkeypatch, exit_code, finished, pending, connected):
    from subprocess import CompletedProcess
    from types import SimpleNamespace

    from fastmdxplora.agent import anthropic_cli

    commands = []
    monkeypatch.setattr(anthropic_cli, "executable", lambda: "ant")
    monkeypatch.setattr(anthropic_cli, "_login_pending", True)
    monkeypatch.setattr(anthropic_cli, "_login_started", time.monotonic())
    monkeypatch.setattr(anthropic_cli, "_login_process", SimpleNamespace(poll=lambda: finished))
    monkeypatch.setattr(anthropic_cli, "_run", lambda args: (
        commands.append(args) or CompletedProcess(args, exit_code, stdout="", stderr="")))
    status = anthropic_cli.status()
    assert status["connected"] is connected
    assert bool(status.get("pending")) is pending
    assert commands == [["models", "list", "--format", "json"]]


def test_macos_console_login_sanitizes_the_existing_terminal_environment(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from fastmdxplora.agent import anthropic_cli

    launched = []
    monkeypatch.setattr(anthropic_cli, "os", SimpleNamespace(name="posix", environ={
        "PATH": "/usr/bin", "HOME": "/home/test", "ANTHROPIC_API_KEY": "never-inherit"}))
    monkeypatch.setattr(anthropic_cli, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(anthropic_cli, "config_dir", lambda: tmp_path / "app console")
    monkeypatch.setattr(anthropic_cli, "executable", lambda: "/usr/local/bin/ant")
    monkeypatch.setattr(anthropic_cli, "_login_pending", False)
    monkeypatch.setattr(anthropic_cli, "_login_started", None)
    monkeypatch.setattr(anthropic_cli, "_login_process", None)
    monkeypatch.setattr(
        anthropic_cli.subprocess, "Popen", lambda args, **_kw: launched.append(args)
    )
    assert anthropic_cli.connect()["ok"]
    script = launched[0][-1]
    assert "/usr/bin/env -i" in script
    assert "ANTHROPIC_CONFIG_DIR=" in script
    assert "never-inherit" not in script and "ANTHROPIC_API_KEY" not in script


def test_a_canceled_console_terminal_cannot_restore_the_app_session(monkeypatch, tmp_path):
    from subprocess import CompletedProcess
    from types import SimpleNamespace

    from fastmdxplora.agent import anthropic_cli

    monkeypatch.setattr(anthropic_cli, "user_config_dir", lambda: tmp_path)
    monkeypatch.setattr(anthropic_cli, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(anthropic_cli, "executable", lambda: "/usr/local/bin/ant")
    monkeypatch.setattr(anthropic_cli, "_login_pending", False)
    monkeypatch.setattr(anthropic_cli, "_login_process", None)
    monkeypatch.setattr(anthropic_cli, "_login_started", None)
    monkeypatch.setattr(anthropic_cli.subprocess, "Popen", lambda *_a, **_kw: None)
    credentials = {}

    def run(args, *, env, **_kwargs):
        profile = env["ANTHROPIC_CONFIG_DIR"]
        if args[1:] == ["auth", "logout", "--all"]:
            credentials.pop(profile, None)
            return CompletedProcess(args, 0, stdout="", stderr="")
        value = credentials.get(profile, "")
        return CompletedProcess(args, 0 if value else 1, stdout=value, stderr="")

    monkeypatch.setattr(anthropic_cli.subprocess, "run", run)
    assert anthropic_cli.connect()["ok"]
    pending_profile = str(anthropic_cli.config_dir())
    assert anthropic_cli.disconnect()["ok"]
    # The already-open terminal receives its OAuth result after logout.
    # The real CLI boundary writes into the profile it was launched with.
    credentials[pending_profile] = "synthetic-late-console-token"
    assert anthropic_cli.status()["connected"] is False
    with pytest.raises(anthropic_cli.ConsoleSignInError):
        anthropic_cli.access_token()
    assert str(anthropic_cli.config_dir()) != pending_profile


def test_console_token_refresh_cannot_return_a_session_disconnected_mid_request(
        monkeypatch, tmp_path):
    from subprocess import CompletedProcess

    from fastmdxplora.agent import anthropic_cli

    monkeypatch.setattr(anthropic_cli, "user_config_dir", lambda: tmp_path)
    monkeypatch.setattr(anthropic_cli, "executable", lambda: "ant")
    monkeypatch.setattr(anthropic_cli, "_login_pending", False)
    monkeypatch.setattr(anthropic_cli, "_login_process", None)

    def run(args, **_kwargs):
        if args[1:3] == ["auth", "print-credentials"]:
            assert anthropic_cli.disconnect()["ok"]
            return CompletedProcess(args, 0, stdout="synthetic-stale-token", stderr="")
        return CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(anthropic_cli.subprocess, "run", run)
    with pytest.raises(anthropic_cli.ConsoleSignInError, match="changed"):
        anthropic_cli.access_token()


def test_chatgpt_status_does_not_report_connected_after_concurrent_disconnect(
        monkeypatch, chatgpt_vault):
    oauth, _vault = chatgpt_vault
    _enable_account(oauth, _account(expires_at=9999999999))
    original = oauth.access_token
    monkeypatch.setattr(oauth, "_fetch_json", lambda *_a, **_kw: {})

    def token_then_disconnect():
        token = original()
        assert oauth.disconnect()["ok"]
        return token

    monkeypatch.setattr(oauth, "access_token", token_then_disconnect)
    status = oauth.status()
    assert status["active_client_id"] == ""
    assert status["connected"] is False


def test_disconnect_removes_the_saved_key_even_when_environment_auth_is_active(
    monkeypatch, tmp_path
):
    from fastmdxplora.agent import models
    from fastmdxplora.gui.agent_panel import auth_endpoint

    monkeypatch.setattr(models, "model_path", lambda: tmp_path / "model.json")
    choice = models.ModelChoice("openai", "gpt-5")
    models.save_choice(choice, key="saved-test-key")
    monkeypatch.setenv("OPENAI_API_KEY", "environment-test-key")
    result = auth_endpoint({"action": "disconnect"})
    assert result["ok"]
    assert result["status"]["connected"] is None
    assert "OPENAI_API_KEY is still active" in result["status"]["message"]
    assert models._stored_api_key(choice) == ""


def test_disconnect_reports_a_locked_vault_without_claiming_the_key_was_removed(
    monkeypatch, tmp_path
):
    from types import SimpleNamespace

    from fastmdxplora.agent import models
    from fastmdxplora.gui.agent_panel import auth_endpoint

    monkeypatch.setattr(models, "model_path", lambda: tmp_path / "model.json")
    models.save_choice(models.ModelChoice("openai", "gpt-5"))
    monkeypatch.setattr(models, "_secure_keyring", lambda: SimpleNamespace(
        get_password=lambda *_a: "test-key",
        delete_password=lambda *_a: (_ for _ in ()).throw(RuntimeError("test-key"))))
    result = auth_endpoint({"action": "disconnect"})
    assert result["ok"] is False
    assert "could not be removed" in result["status"]["message"]
    assert "test-key" not in result["status"]["message"]


def test_settings_save_failure_preserves_the_previous_choice_and_reports_the_error(
    monkeypatch, tmp_path
):
    from pathlib import Path

    from fastmdxplora.agent import models
    from fastmdxplora.gui.agent_panel import model_endpoint

    path = tmp_path / "model.json"
    monkeypatch.setattr(models, "model_path", lambda: path)
    models.save_choice(models.ModelChoice("openai", "gpt-5"), cache="5m")
    before = path.read_bytes()

    def denied(_source, _target):
        raise PermissionError("test denied")

    monkeypatch.setattr(Path, "replace", denied)
    result = model_endpoint({"provider": "anthropic", "model": "claude-sonnet-4-6"})
    assert result["ok"] is False
    assert "settings could not be saved" in result["error"]
    assert path.read_bytes() == before
    assert list(tmp_path.iterdir()) == [path]


def test_failed_console_disconnect_does_not_claim_to_be_disconnected(monkeypatch):
    from fastmdxplora.agent import anthropic_cli
    from fastmdxplora.gui.agent_panel import auth_endpoint

    monkeypatch.setattr(anthropic_cli, "disconnect", lambda: {
        "ok": False, "message": "Anthropic Console sign-out timed out. Try again."})
    result = auth_endpoint({"provider": "anthropic", "auth_method": "anthropic_console",
                            "action": "disconnect"})
    assert result["ok"] is False
    assert result["status"]["connected"] is None
    assert "timed out" in result["status"]["message"]


def test_unavailable_console_and_hosted_sign_in_cannot_save(
        monkeypatch, tmp_path):
    from fastmdxplora.agent import anthropic_cli
    from fastmdxplora.agent.models import load_choice
    from fastmdxplora.gui.agent_panel import model_endpoint

    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(anthropic_cli, "executable", lambda: None)
    response = model_endpoint({})
    claude = next(row for row in response["providers"] if row["id"] == "anthropic")
    console = next(row for row in claude["auth_methods"]
                   if row["id"] == "anthropic_console")
    assert console["available"] is False
    assert "Anthropic Console API OAuth" in console["reason"]
    assert console["requires_api_key"] is False
    assert {method["id"] for method in claude["auth_methods"]} == {
        "api_key", "anthropic_console"}

    rejected = model_endpoint({"provider": "openai", "model": "gpt-5",
                               "auth_method": "chatgpt_plan"},
                              local_auth_allowed=False)
    assert rejected["ok"] is False
    assert "only in the local dashboard" in rejected["error"]
    assert load_choice() is None


def test_dashboard_test_route_reports_missing_chatgpt_sign_in_without_network_access(
        monkeypatch, tmp_path):
    from fastmdxplora.gui.agent_panel import auth_endpoint

    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", str(tmp_path))
    response = auth_endpoint({"action": "test", "provider": "openai",
                              "auth_method": "chatgpt_plan", "model": "gpt-5"})
    assert response["ok"] is True
    assert response["status"]["connected"] is False
    assert "No active ChatGPT sign-in" in response["status"]["message"]


@pytest.mark.parametrize("terminal_available", [False, True])
@pytest.mark.parametrize("viewport", [{"width": 1164, "height": 668},
                                     {"width": 480, "height": 700}])
def test_live_agent_settings_switch_provider_method_and_persist_the_real_selection(
        monkeypatch, tmp_path, viewport, terminal_available):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.agent import models
    from fastmdxplora.agent import anthropic_cli
    from fastmdxplora.agent import openai_chatgpt as oauth
    from fastmdxplora.gui.server import start_dashboard_session
    from tests.test_the_drawing_scripts_run_in_a_browser import _write_study

    monkeypatch.setattr(models, "model_path", lambda: tmp_path / "model.json")
    monkeypatch.setattr(anthropic_cli, "executable", lambda: str(tmp_path / "synthetic-ant"))
    monkeypatch.setattr(anthropic_cli, "can_launch_login", lambda: terminal_available)
    waiting = {"pending": False}
    connection_requests = []
    original_status, original_disconnect = oauth.status, oauth.disconnect

    def pending_login(**kwargs):
        connection_requests.append(kwargs)
        waiting["pending"] = True
        return {"ok": True, "pending": True, "message": "Waiting for ChatGPT sign-in."}

    def login_status():
        return ({"available": True, "connected": None, "pending": True,
                 "message": "Waiting for ChatGPT sign-in.", "accounts": []}
                if waiting["pending"] else original_status())

    def cancel_login(**kwargs):
        waiting["pending"] = False
        return original_disconnect(**kwargs)

    monkeypatch.setattr(oauth, "connect", pending_login)
    monkeypatch.setattr(oauth, "status", login_status)
    monkeypatch.setattr(oauth, "disconnect", cancel_login)
    study = _write_study(tmp_path / "study")
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport=viewport)
            page.set_default_timeout(30000)
            page.goto(session.url + "#agent", wait_until="domcontentloaded")
            page.wait_for_function("() => document.body.classList.contains('state-ready')")
            page.wait_for_function(
                "() => document.getElementById('agent-provider').options.length >= 3"
            )
            page.click("#agent-settings-open")
            page.wait_for_selector("#agent-settings:not([hidden])")

            page.locator("#agent-provider").select_option("openai")
            page.wait_for_function("() => document.getElementById('agent-model').value === 'gpt-5'")
            assert page.locator("#agent-key").is_visible()
            page.locator("#agent-auth-method").select_option("chatgpt_plan")
            assert page.locator("#agent-key").is_hidden()
            assert page.locator("#agent-auth-connect").inner_text() == "Continue with ChatGPT"
            page.wait_for_function("() => document.getElementById('agent-auth-status')"
                                   ".textContent.includes('No ChatGPT account is connected')")
            assert (
                "No ChatGPT account is connected" in page.locator("#agent-auth-status").inner_text()
            )

            def check_auth_layout():
                page.locator("#agent-auth-test").scroll_into_view_if_needed()
                assert page.locator("#agent-auth-tools").evaluate("""tools => {
                  const status = tools.querySelector('.agent-auth-status').getBoundingClientRect();
                  const row = tools.querySelector('.agent-auth-buttons').getBoundingClientRect();
                  const bounds = tools.getBoundingClientRect();
                  const buttons = [...tools.querySelectorAll('button')].filter(b => !b.hidden);
                  return row.top >= status.bottom && buttons.every(button => {
                    const box = button.getBoundingClientRect();
                    return box.width >= 90 && box.height >= 36
                      && button.scrollWidth <= button.clientWidth + 1
                      && button.scrollHeight <= button.clientHeight + 1
                      && box.left >= bounds.left && box.right <= bounds.right;
                  });
                }""")
                assert page.locator("#agent-settings .agent-dialog-panel").evaluate("""panel => {
                  const box = panel.getBoundingClientRect();
                  return box.left >= 0 && box.right <= innerWidth
                    && box.top >= 0 && box.bottom <= innerHeight
                    && panel.scrollWidth <= panel.clientWidth;
                }""")

            check_auth_layout()

            # Hold an older signed-out Test response until Connect has
            # reported pending. Its late arrival must not hide Cancel.
            held = []

            def hold_old_test(route):
                if route.request.post_data_json.get("action") == "test" and not held:
                    held.append((route, route.fetch()))
                else:
                    route.continue_()

            page.route("**/api/agent/auth", hold_old_test)
            page.locator("#agent-auth-test").click()
            page.locator("#agent-auth-connect").click()
            cancel = page.get_by_role("button", name="Cancel sign-in", exact=True)
            cancel.wait_for(state="visible")
            assert len(held) == 1
            held[0][0].fulfill(response=held[0][1])
            page.wait_for_timeout(200)
            assert page.locator("#agent-auth-disconnect").inner_text() == "Cancel sign-in"
            assert connection_requests[-1].get("add_account") is False
            assert cancel.is_enabled()
            assert page.locator("#agent-auth-test").is_disabled()
            cancel.click()
            page.get_by_role("button", name="Disconnect", exact=True).wait_for(state="visible")
            assert waiting["pending"] is False

            # Exercise the dashboard's real auth test endpoint while signed out.
            page.locator("#agent-auth-test").click()
            page.wait_for_function("() => document.getElementById('agent-auth-status')"
                                   ".textContent.includes('No active ChatGPT sign-in')")
            page.locator("#agent-auth-disconnect").click()
            page.wait_for_function("() => document.getElementById('agent-auth-status')"
                                   ".textContent.includes('No saved ChatGPT sign-in')")

            page.click("#agent-save-model")
            page.locator("#agent-settings").wait_for(state="hidden")

            page.reload(wait_until="domcontentloaded")
            page.wait_for_function("() => document.body.classList.contains('state-ready')")
            page.click("#agent-settings-open")
            page.wait_for_function(
                "() => document.getElementById('agent-provider').value === 'openai'"
            )
            assert page.locator("#agent-auth-method").input_value() == "chatgpt_plan"
            assert "Saved for Agent requests: OpenAI · gpt-5 · ChatGPT plan sign-in" \
                in page.locator("#agent-model-current").inner_text()

            page.locator("#agent-provider").select_option("anthropic")
            page.wait_for_function(
                "() => document.getElementById('agent-model').value === 'claude-sonnet-4-6'")
            assert page.locator("#agent-key").is_visible()
            console_option = page.locator(
                "#agent-auth-method option[value='anthropic_console']")
            assert page.locator("#agent-auth-method option").evaluate_all(
                "options => options.map(option => option.value)"
            ) == ["api_key", "anthropic_console"]
            from fastmdxplora.agent.anthropic_cli import can_launch_login, executable

            console_available = bool(executable() and can_launch_login())
            assert console_option.evaluate("option => option.disabled") is (not console_available)
            if console_available:
                page.locator("#agent-auth-method").select_option("anthropic_console")
                assert page.locator("#agent-key").is_hidden()
                assert page.locator("#agent-auth-connect").is_visible()
            help_text = page.locator("#agent-auth-help").inner_text()
            if console_available:
                assert "Anthropic Console API OAuth" in help_text
            else:
                assert "Anthropic Console OAuth (official ant CLI) unavailable:" in help_text
                assert "API-key authentication uses the provider's documented API endpoint" in help_text
            check_auth_layout()
            summary = page.locator("#agent-model-current").inner_text()
            assert "Saved for Agent requests: OpenAI · gpt-5 · ChatGPT plan sign-in" in summary
            method = "Anthropic Console OAuth (official ant CLI)" if console_available else "API key"
            assert (
                f"Current selection (unsaved): Anthropic · claude-sonnet-4-6 · {method}" in summary
            )
            browser.close()
    finally:
        session.server.shutdown()


def test_concurrent_refresh_reads_are_serialized(chatgpt_vault):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    oauth, _ = chatgpt_vault
    entered = threading.Event()
    release = threading.Event()
    second_entered = threading.Event()

    def first():
        with oauth._process_lock():
            entered.set()
            assert release.wait(5)

    def second():
        with oauth._process_lock():
            second_entered.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_result = pool.submit(first)
        assert entered.wait(5)
        second_result = pool.submit(second)
        try:
            assert not second_entered.wait(0.1)
        finally:
            release.set()
        first_result.result(timeout=5)
        second_result.result(timeout=5)
    assert second_entered.is_set()


def test_settings_model_discovery_does_not_repeat_a_failed_request(monkeypatch):
    from fastmdxplora.agent import models
    from fastmdxplora.gui import agent_panel

    calls = []

    def unavailable(choice):
        calls.append(choice)
        raise RuntimeError("Provider unavailable")

    choice = models.ModelChoice("openai", "gpt-5", auth_method="chatgpt_plan")
    monkeypatch.setattr(models, "list_model_options", unavailable)
    monkeypatch.setattr(models, "load_choice", lambda: choice)
    monkeypatch.setattr(agent_panel, "_key_availability", lambda _: {
        "connected": False, "message": "Sign in to continue."})
    answer = agent_panel.model_endpoint({})
    provider = next(item for item in answer["providers"] if item["id"] == "openai")
    assert calls == [choice]
    assert provider["models"] == list(models.PROVIDERS["openai"]["models"])
    assert answer["auth_status"]["connected"] is False
    assert answer["current"] == choice.as_record()


@pytest.mark.parametrize("wrapped", [False, True])
def test_chatgpt_certificate_failure_is_actionable_without_exposing_request(monkeypatch, wrapped):
    from fastmdxplora.agent import openai_chatgpt as oauth

    def rejected(_request, **_kwargs):
        error = ssl.SSLCertVerificationError(1, "test-secret certificate has expired")
        raise urllib.error.URLError(error) if wrapped else error

    monkeypatch.setattr("fastmdxplora.agent.openai_chatgpt._open_https", rejected)
    with pytest.raises(oauth.SignInError, match="certificate could not be verified") as caught:
        oauth._exchange({"code": "test-secret"})
    assert "test-secret" not in str(caught.value)
    with pytest.raises(oauth.SignInError, match="certificate could not be verified"):
        oauth._fetch_json(oauth.DISCOVERY_URL)


def test_chatgpt_https_context_requires_certificate_and_hostname_verification():
    from fastmdxplora.agent import openai_chatgpt as oauth

    context = oauth._https_context()
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


def test_chatgpt_model_credentials_are_never_forwarded_by_a_redirect(monkeypatch):
    from email.message import Message
    from urllib.response import addinfourl

    from fastmdxplora.agent import openai_chatgpt as oauth

    foreign = []

    class SyntheticNetwork(urllib.request.BaseHandler):
        handler_order = 100

        def https_open(self, request):
            headers = Message()
            headers["Location"] = "http://foreign.invalid/receive"
            reply = addinfourl(BytesIO(b""), headers, request.full_url, 302)
            reply.msg = "Found"
            return reply

        def http_open(self, request):
            foreign.append(request.get_header("Authorization"))
            raise OSError("Synthetic foreign endpoint must not receive the request")

    build = urllib.request.build_opener
    contexts = []

    def isolated_opener(*handlers):
        for handler in handlers:
            if isinstance(handler, urllib.request.HTTPSHandler):
                contexts.append(handler._context)
                assert handler._context.verify_mode == ssl.CERT_REQUIRED
                assert handler._context.check_hostname is True
        return build(SyntheticNetwork(), *handlers)

    monkeypatch.setattr(urllib.request, "_opener", None)
    monkeypatch.setattr(urllib.request, "build_opener", isolated_opener)
    monkeypatch.setattr(oauth, "access_token", lambda: "synthetic-redirect-secret")
    with pytest.raises(oauth.SignInError):
        oauth.list_models()
    assert foreign == []
    assert len(contexts) == 1


def test_chatgpt_callback_rejects_a_different_host_before_exchanging(monkeypatch, chatgpt_vault):
    oauth, _vault = chatgpt_vault
    opened, exchanged = [], []
    monkeypatch.setattr(oauth.webbrowser, "open", lambda url, **_kw: opened.append(url) or True)
    monkeypatch.setattr(oauth, "_exchange", lambda form: exchanged.append(form) or {})
    assert oauth.connect()["ok"]
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(opened[0]).query)
    callback = query["redirect_uri"][0] + "?" + urllib.parse.urlencode({
        "state": query["state"][0], "code": "synthetic-code", "client_id": "oaiapp_test"})
    try:
        request = urllib.request.Request(callback, headers={"Host": "foreign.invalid"})
        with pytest.raises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=5)
        assert caught.value.code == 400
        assert exchanged == []
        assert query["state"][0] in oauth._pending
    finally:
        oauth._finish_attempt(query["state"][0])


def test_adding_a_chatgpt_account_does_not_reuse_an_unverified_registration(
        monkeypatch, chatgpt_vault):
    oauth, _vault = chatgpt_vault
    oauth._write_store({"pending_client_id": "oaiapp_old_attempt", "accounts": []})
    opened = []
    monkeypatch.setattr(oauth.webbrowser, "open", lambda url, **_kw: opened.append(url) or True)
    assert oauth.connect(add_account=True)["ok"]
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(opened[0]).query)
    try:
        assert query["client_id"] == ["dynamic_agent_client"]
        assert query["agent_name_hint"] == [oauth.CLIENT_NAME]
    finally:
        oauth._finish_attempt(query["state"][0])


def test_each_chatgpt_attempt_has_fresh_state_nonce_and_pkce(monkeypatch, chatgpt_vault):
    oauth, _vault = chatgpt_vault
    attempts = []

    def opened(url, **_kw):
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        attempt = oauth._pending[query["state"][0]]
        assert query["code_challenge"] == [oauth._b64url(
            hashlib.sha256(attempt["verifier"].encode("ascii")).digest())]
        assert query["nonce"] == [attempt["nonce"]]
        attempts.append(attempt.copy())
        return True

    monkeypatch.setattr(oauth.webbrowser, "open", opened)
    for _ in range(2):
        assert oauth.connect()["ok"]
        oauth._finish_attempt(attempts[-1]["state"])
    for name in ("state", "nonce", "verifier"):
        assert len(attempts[0][name]) >= 43
        assert attempts[0][name] != attempts[1][name]


@pytest.mark.parametrize("allow_control,headers,allowed", [
    (True, {}, True),
    (True, {"Host": "foreign.invalid"}, False),
    (True, {"Origin": "https://foreign.invalid"}, False),
    (True, {"Sec-Fetch-Site": "cross-site"}, False),
    (False, {}, False),
])
def test_authentication_route_is_local_and_refuses_foreign_pages(
        monkeypatch, tmp_path, allow_control, headers, allowed):
    from http.server import ThreadingHTTPServer

    from fastmdxplora.gui import agent_panel
    from fastmdxplora.gui.server import make_handler

    called = []
    monkeypatch.setattr(agent_panel, "auth_endpoint", lambda *args, **kw:
                        called.append((args, kw)) or {"ok": True})
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(
        tmp_path, template_html="<html></html>", allow_control=allow_control))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    request = urllib.request.Request(
        f"http://127.0.0.1:{server.server_address[1]}/api/agent/auth",
        data=b'{"action":"connect","provider":"openai","auth_method":"chatgpt_plan"}',
        headers={"Content-Type": "application/json", **headers})
    try:
        if allowed:
            with urllib.request.urlopen(request, timeout=5) as response:
                assert response.status == 200 and json.load(response)["ok"]
            assert len(called) == 1
        else:
            with pytest.raises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(request, timeout=5)
            assert caught.value.code == 403
            assert called == []
    finally:
        server.shutdown()
        server.server_close()
        worker.join(5)


@pytest.mark.parametrize("fail_at", [1, 3, "manifest"])
def test_a_failed_chunk_write_preserves_the_previous_complete_sign_in(
        monkeypatch, chatgpt_vault, fail_at):
    oauth, vault = chatgpt_vault
    original = {"host_id": "old-host", "accounts": [_account(expires_at=9999999999)]}
    oauth._write_store(original)
    saved = oauth._read_store()
    manifest = vault.get_password(oauth._SERVICE, oauth._vault_user())
    real_write = vault.set_password
    writes = []

    def limited_write(service, user, value):
        assert len(value.encode("utf-16-le")) <= 2560
        writes.append(service)
        if len(writes) == fail_at or (fail_at == "manifest" and service == oauth._SERVICE):
            raise OSError("synthetic vault write failure")
        real_write(service, user, value)

    monkeypatch.setattr(vault, "set_password", limited_write)
    large = dict(original, host_id="new-host", accounts=[dict(
        original["accounts"][0], access_token="synthetic" * 1000)])
    with pytest.raises(oauth.SignInError, match="could not save"):
        oauth._write_store(large)
    assert oauth._read_store() == saved
    assert vault.get_password(oauth._SERVICE, oauth._vault_user()) == manifest
    for service in writes:
        if service != oauth._SERVICE:
            assert vault.get_password(service, oauth._vault_user()) is None


def test_large_sign_in_records_fit_the_vault_and_old_parts_are_removed(chatgpt_vault):
    oauth, vault = chatgpt_vault
    user = oauth._vault_user()
    record = {"host_id": "host", "active_client_id": "oaiapp_test", "accounts": [dict(
        _account(expires_at=9999999999), access_token="synthetic" * 1000,
        id_token="identity" * 1000, email="unicode-\u03b1@example.invalid")]}
    # Read the app's existing unchunked format as well as the bounded format.
    vault.set_password(oauth._SERVICE, user, json.dumps(record))
    assert oauth._read_store()["accounts"] == record["accounts"]
    oauth._write_store(record)
    keys = oauth._chunk_keys(vault.get_password(oauth._SERVICE, user))
    assert len(keys) > 10
    assert all(len(vault.get_password(key, user).encode("utf-16-le")) <= 2560 for key in keys)
    assert oauth._read_store()["accounts"] == record["accounts"]
    oauth._write_store({"host_id": "host", "accounts": []})
    assert oauth._read_store()["accounts"] == []
    assert all(vault.get_password(key, user) is None for key in keys)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows native vault size regression")
def test_a_large_synthetic_sign_in_round_trips_through_the_windows_vault(monkeypatch, tmp_path):
    from keyring.backends.Windows import WinVaultKeyring

    from fastmdxplora.agent import openai_chatgpt as oauth

    vault = WinVaultKeyring()
    monkeypatch.setattr(oauth, "_vault", lambda: vault)
    monkeypatch.setattr(oauth, "_SERVICE", "FastMDXplora synthetic regression " + uuid.uuid4().hex)
    monkeypatch.setattr(oauth, "user_config_dir", lambda: tmp_path)
    record = {"host_id": "synthetic-host", "accounts": [dict(
        _account(expires_at=9999999999), access_token="synthetic" * 1000,
        id_token="synthetic-identity" * 1000)]}
    try:
        oauth._write_store(record)
        assert oauth._read_store()["accounts"] == record["accounts"]
        oauth._write_store({"host_id": "synthetic-host", "accounts": []})
        assert oauth._read_store()["accounts"] == []
    finally:
        user = oauth._vault_user()
        raw = vault.get_password(oauth._SERVICE, user)
        for key in oauth._chunk_keys(raw):
            vault.delete_password(key, user)
        if raw:
            vault.delete_password(oauth._SERVICE, user)


def test_vault_readers_wait_for_the_whole_sign_in_write(monkeypatch, chatgpt_vault):
    oauth, vault = chatgpt_vault
    oauth._write_store({"host_id": "old", "accounts": []})
    entered, release, read_started, read_done = (threading.Event() for _ in range(4))
    real_write = vault.set_password
    failures, records = [], []

    def delayed_write(service, user, value):
        if service != oauth._SERVICE and not entered.is_set():
            entered.set()
            if not release.wait(15):
                raise TimeoutError("test did not release the vault write")
        real_write(service, user, value)

    def write():
        try:
            oauth._write_store({"host_id": "new", "accounts": []})
        except Exception as exc:
            failures.append(type(exc).__name__)

    def read():
        read_started.set()
        records.append(oauth._read_store())
        read_done.set()

    monkeypatch.setattr(vault, "set_password", delayed_write)
    writer = threading.Thread(target=write)
    reader = threading.Thread(target=read)
    writer.start()
    try:
        assert entered.wait(10)
        reader.start()
        assert read_started.wait(10)
        assert not read_done.wait(0.1)
    finally:
        release.set()
        writer.join(15)
        if reader.ident is not None:
            reader.join(15)
    assert not failures
    assert read_done.is_set()
    assert records[0]["host_id"] == "new"


@pytest.mark.parametrize("failure", ["vault", "grant", "sign-in"])
def test_provider_refusals_keep_their_cause_at_the_completion_boundary(
        monkeypatch, chatgpt_vault, failure):
    from fastmdxplora.agent.models import ModelChoice, _key_for, probe_connection
    from fastmdxplora.refusals import StudyError, known

    oauth, _vault = chatgpt_vault
    error = {"vault": oauth.CredentialStoreError,
             "grant": oauth.InvalidGrant, "sign-in": oauth.SignInError}[failure](
                 "Synthetic provider refusal")

    def refuse(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(oauth, "access_token", refuse)
    choice = ModelChoice("openai", "synthetic-model", "", "chatgpt_plan")
    with pytest.raises(StudyError) as caught:
        _key_for(choice)
    assert known(caught.value.code)
    assert caught.value.code == error.code
    monkeypatch.setattr(oauth, "list_models", refuse)
    assert probe_connection(choice) == {
        "connected": False, "message": str(error), "code": error.code}


def test_a_failed_vault_disconnect_is_a_refusal_not_a_disconnected_account(
        monkeypatch, chatgpt_vault):
    from fastmdxplora.gui.agent_panel import auth_endpoint

    oauth, vault = chatgpt_vault
    _enable_account(oauth, _account(expires_at=9999999999))

    def deny_write(*_args):
        raise OSError("synthetic-sensitive-backend-detail")

    monkeypatch.setattr(vault, "set_password", deny_write)
    result = auth_endpoint({"provider": "openai", "auth_method": "chatgpt_plan",
                            "action": "disconnect"})
    assert result["ok"] is False
    assert result["code"] == "environment.credentials.secure_store_unavailable"
    assert result["status"]["connected"] is None
    assert "synthetic-sensitive-backend-detail" not in json.dumps(result)
    assert oauth._read_store()["accounts"][0]["refresh_token"]


def test_an_internal_provider_error_is_not_misreported_as_missing_credentials(monkeypatch):
    from fastmdxplora.agent import anthropic_cli
    from fastmdxplora.agent.models import ModelChoice, _key_for

    def broken():
        raise RuntimeError("synthetic programming defect")

    monkeypatch.setattr(anthropic_cli, "access_token", broken)
    with pytest.raises(RuntimeError, match="programming defect"):
        _key_for(ModelChoice("anthropic", "synthetic-model", "", "anthropic_console"))


@pytest.mark.parametrize("names", [("Profile", "profile"), ("Straße", "strasse")])
def test_credential_scopes_follow_path_case_rules_without_merging_distinct_profiles(
        monkeypatch, tmp_path, names):
    from fastmdxplora.agent import models
    from fastmdxplora.agent import openai_chatgpt as oauth

    paths = [tmp_path / name for name in names]
    choice = models.ModelChoice("openai", "synthetic-model")
    api_ids = [models._credential_identity(choice, path / "model.json") for path in paths]
    oauth_ids = []
    for path in paths:
        monkeypatch.setattr(oauth, "user_config_dir", lambda path=path: path)
        oauth_ids.append(oauth._vault_user())
    same_path = os.name == "nt" and names == ("Profile", "profile")
    assert (api_ids[0] == api_ids[1]) is same_path
    assert (oauth_ids[0] == oauth_ids[1]) is same_path
