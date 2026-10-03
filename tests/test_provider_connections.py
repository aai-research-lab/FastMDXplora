import json
import threading
import time
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import urlopen

import pytest

from fastmdxplora.agent.credential_vault import CredentialVault
from fastmdxplora.agent.openai_plan import ConnectionError
from fastmdxplora.gui.provider_connections import ProviderConnections


@pytest.fixture
def service(tmp_path):
    # Fixture-only reversible codec; production uses OS protection.
    vault = CredentialVault(tmp_path, protect=lambda raw: raw[::-1], unprotect=lambda raw: raw[::-1])
    def exchange(grant):
        return {"client_id": grant.client_id, "subject": "fixture-subject", "email": "fixture@example.test",
                "access_token": "fixture-access", "refresh_token": "fixture-refresh", "id_token": "fixture-id",
                "expires_at": time.time() + 3600, "scopes": ["chatgpt.tokens.use.direct"]}
    manager = ProviderConnections(vault=vault, exchange=exchange,
                                  catalog=lambda token: [{"id": "fixture-model", "label": "Fixture model"}])
    yield manager
    manager.cancel()


def finish_sign_in(manager):
    started = manager.begin()
    params = parse_qs(urlsplit(started["url"]).query)
    callback = params["redirect_uri"][0] + "?" + urlencode({"state": params["state"][0],
                                                             "code": "fixture-code", "client_id": "oaiapp_fixture"})
    with urlopen(callback, timeout=3) as response:
        assert b"Authorization received" in response.read()
    deadline = time.monotonic() + 3
    while manager.snapshot()["status"] == "verifying" and time.monotonic() < deadline:
        time.sleep(0.02)
    result = manager.snapshot()
    assert result["status"] == "connected", result
    return result["accounts"][0]["id"]


def test_local_callback_stores_verified_account_but_requires_explicit_selection(service):
    account_id = finish_sign_in(service)
    view = service.snapshot()
    assert view["active"] is None and view["selection"] == "api"
    assert service.completion() is None
    assert "fixture-access" not in json.dumps(view)
    assert "fixture-refresh" not in json.dumps(view)
    service.select(account_id, "fixture-model")
    assert service.snapshot()["selection"] == "subscription"
    assert service.completion().model_record == {"provider": "openai-chatgpt", "model": "fixture-model"}


def test_account_models_are_verified_and_arbitrary_selection_is_refused(service):
    account_id = finish_sign_in(service)
    assert service.model_list(account_id)["models"][0]["id"] == "fixture-model"
    with pytest.raises(ConnectionError, match="catalog"):
        service.select(account_id, "invented-model")
    assert service.snapshot()["active"] is None


def test_denied_callback_closes_attempt_but_wrong_state_preserves_it(service):
    started = service.begin()
    params = parse_qs(urlsplit(started["url"]).query)
    callback = params["redirect_uri"][0]
    with urlopen(callback + "?" + urlencode({"state": "wrong", "error": "access_denied"}), timeout=3):
        pass
    assert service.snapshot()["status"] == "pending"
    assert service.pending is not None
    with urlopen(callback + "?" + urlencode({"state": params["state"][0], "error": "access_denied"}), timeout=3):
        pass
    assert service.snapshot()["status"] == "error"
    assert service.pending is None and service.listener is None
    assert service.snapshot()["accounts"] == []


def test_selected_subscription_survives_new_dashboard_service(service):
    account_id = finish_sign_in(service)
    service.select(account_id, "fixture-model")
    fresh = ProviderConnections(vault=service.vault, catalog=service.catalog)
    assert fresh.snapshot()["selection"] == "subscription"
    assert fresh.completion().model_record["model"] == "fixture-model"


def test_disconnected_subscription_never_becomes_api_fallback(service, monkeypatch):
    account_id = finish_sign_in(service)
    service.select(account_id, "fixture-model")
    monkeypatch.setattr("fastmdxplora.agent.openai_plan.revoke", lambda account: None)
    service.disconnect(account_id)
    assert service.snapshot()["selection"] == "subscription"
    with pytest.raises(ConnectionError, match="Reconnect"):
        service.completion()("question")
    with service.vault.locked():
        record = service.vault.read()
    assert "access_token" not in record["accounts"][0]
    assert record["accounts"][0]["client_id"] == "oaiapp_fixture"


def test_cancelled_exchange_cannot_replace_account(service):
    entered, release = threading.Event(), threading.Event()
    exchange = service.exchange
    def slow_exchange(grant):
        entered.set()
        assert release.wait(3)
        return exchange(grant)
    service.exchange = slow_exchange
    started = service.begin()
    params = parse_qs(urlsplit(started["url"]).query)
    with urlopen(params["redirect_uri"][0] + "?" + urlencode({"state": params["state"][0], "code": "fixture", "client_id": "oaiapp_fixture"}), timeout=3):
        pass
    assert entered.wait(3)
    service.cancel()
    release.set()
    assert service.snapshot()["accounts"] == []


def test_account_switch_during_reply_invalidates_text(service, monkeypatch):
    account_id = finish_sign_in(service)
    service.select(account_id, "fixture-model")
    complete = service.completion()
    def provider(token, model, prompt, on_text):
        service.select_api()
        on_text("stale answer")
        return "stale answer"
    monkeypatch.setattr("fastmdxplora.agent.openai_plan.complete", provider)
    with pytest.raises(ConnectionError, match="changed"):
        complete("question")


def test_unconfirmed_revocation_is_visible(service, monkeypatch):
    account_id = finish_sign_in(service)
    def refused(account):
        raise ConnectionError("network unavailable")
    monkeypatch.setattr("fastmdxplora.agent.openai_plan.revoke", refused)
    result = service.disconnect(account_id)
    assert "not confirmed" in result["message"]
    assert not result["accounts"][0]["connected"]


def test_claude_official_login_selection_and_disconnect_are_separate_from_api(service, monkeypatch):
    monkeypatch.setattr("fastmdxplora.gui.provider_connections.claude_executable", lambda: "fixture-client")
    monkeypatch.setattr("fastmdxplora.gui.provider_connections.unmanaged_host", lambda: None)
    events = []
    class Client:
        def login(self, cancelled):
            return {"email": "claude@example.test", "subject": "claude@example.test"}
        def identity(self):
            return {"subject": "claude@example.test"}
        def complete(self, prompt, subject):
            events.append((prompt, subject))
            return "Fixture Claude explanation"
        def logout(self):
            events.append("logout")
    monkeypatch.setattr(service, "_claude", lambda account: Client())
    result = service.begin_claude()
    assert result["browser_managed"] and "url" not in result
    deadline = time.monotonic() + 3
    while service.snapshot()["status"] == "pending" and time.monotonic() < deadline:
        time.sleep(0.02)
    account = service.snapshot()["accounts"][0]
    assert account["provider"] == "claude" and account["connected"]
    assert service.snapshot()["selection"] == "api"
    service.select(account["id"], "provider-default")
    completion = service.completion()
    assert not completion.streams and completion.model_record["provider"] == "claude"
    assert completion("question") == "Fixture Claude explanation"
    service.disconnect(account["id"])
    assert events == [("question", "claude@example.test"), "logout"]
    assert service.snapshot()["selection"] == "subscription"
    with pytest.raises(ConnectionError, match="Reconnect"):
        service.completion()("question")


def test_cancelled_claude_login_cannot_create_selected_account(service, monkeypatch):
    monkeypatch.setattr("fastmdxplora.gui.provider_connections.claude_executable", lambda: "fixture-client")
    monkeypatch.setattr("fastmdxplora.gui.provider_connections.unmanaged_host", lambda: None)
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    class Client:
        def login(self, cancelled):
            entered.set()
            assert release.wait(3)
            finished.set()
            return {"email": "claude@example.test", "subject": "claude@example.test"}
    monkeypatch.setattr(service, "_claude", lambda account: Client())
    service.begin_claude()
    assert entered.wait(3)
    service.cancel()
    release.set()
    assert finished.wait(3)
    assert service.snapshot()["accounts"] == []


def test_kimi_device_login_model_selection_and_logout(service, monkeypatch):
    monkeypatch.setattr("fastmdxplora.gui.provider_connections.kimi_executable", lambda: ["fixture-client"])
    entered, release = threading.Event(), threading.Event()
    events = []
    class Client:
        def login(self, cancelled, authorize):
            authorize("https://www.kimi.com/device?user_code=synthetic-fixture")
            entered.set()
            assert release.wait(3)
            return {"email": "fixture Kimi", "subject": "global:fixture"}

        def identity(self):
            return {"subject": "global:fixture"}

        def models(self, subject):
            assert subject == "global:fixture"
            return [{"id": "kimi-fixture", "label": "Fixture Kimi model"}]

        def complete(self, prompt, subject, model):
            events.append((prompt, subject, model))
            return "Fixture Kimi explanation"

        def logout(self):
            events.append("logout")
    monkeypatch.setattr(service, "_kimi", lambda account: Client())
    service.begin_kimi()
    assert entered.wait(3)
    pending = service.snapshot()
    assert pending["authorization_url"].startswith("https://www.kimi.com/device")
    assert pending["selection"] == "api"
    release.set()
    deadline = time.monotonic() + 3
    while service.snapshot()["status"] == "pending" and time.monotonic() < deadline:
        time.sleep(0.02)
    account = service.snapshot()["accounts"][0]
    assert account["provider"] == "kimi" and account["connected"]
    assert service.snapshot()["authorization_url"] is None
    with pytest.raises(ConnectionError, match="catalog"):
        service.select(account["id"], "arbitrary")
    service.select(account["id"], "kimi-fixture")
    complete = service.completion()
    assert not complete.streams and complete.model_record == {"provider": "kimi", "model": "kimi-fixture"}
    assert complete("question") == "Fixture Kimi explanation"
    service.disconnect(account["id"])
    assert events == [("question", "global:fixture", "kimi-fixture"), "logout"]
    with pytest.raises(ConnectionError, match="Reconnect"):
        service.completion()("question")


def test_gemini_connection_requires_explicit_model_selection_and_forwards_cancel(service, monkeypatch):
    monkeypatch.setattr("fastmdxplora.gui.provider_connections.gemini_executable", lambda: ["fixture-client"])
    entered, release, cancel = threading.Event(), threading.Event(), threading.Event()
    observed = []
    class Client:
        def login(self, cancelled):
            entered.set()
            assert release.wait(3)
            return {"email": "google@example.test", "subject": "123456", "tier": "Fixture"}
        def identity(self):
            return {"subject": "123456"}
        def models(self, subject):
            assert subject == "123456"
            return [{"id": "gemini-fixture", "label": "Fixture model"}]
        def complete(self, prompt, subject, model, *, cancelled):
            observed.append((prompt, subject, model, cancelled))
            return "Fixture Gemini explanation"
        def logout(self):
            observed.append("logout")
    monkeypatch.setattr(service, "_gemini", lambda account: Client())
    service.begin_client("gemini")
    assert entered.wait(3)
    release.set()
    deadline = time.monotonic() + 3
    while service.snapshot()["status"] == "pending" and time.monotonic() < deadline:
        time.sleep(.02)
    account = service.snapshot()["accounts"][0]
    assert account["provider"] == "gemini"
    assert service.snapshot()["selection"] == "api"
    service.select(account["id"], "gemini-fixture")
    complete = service.completion(cancelled=cancel)
    assert not complete.streams
    assert complete("question") == "Fixture Gemini explanation"
    assert observed[0] == ("question", "123456", "gemini-fixture", cancel)
    cancel.set()
    with pytest.raises(ConnectionError, match="cancelled"):
        complete("cancelled question")
    service.disconnect(account["id"])
    assert observed[-1] == "logout"


def test_dashboard_browser_sign_in_selects_subscription_without_api_key(service, monkeypatch, tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    from fastmdxplora.gui.server import start_test_server
    from tests.test_the_drawing_scripts_run_in_a_browser import _write_study
    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", str(tmp_path / "settings"))
    def get_service(runtime):
        runtime._provider_connections = service
        return service
    monkeypatch.setattr("fastmdxplora.gui.provider_connections.service_for", get_service)
    server, url = start_test_server(_write_study(tmp_path / "study"))
    try:
        with playwright.sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context()
            def fixture_login(route):
                params = parse_qs(urlsplit(route.request.url).query)
                callback = params["redirect_uri"][0] + "?" + urlencode({"state": params["state"][0], "code": "fixture", "client_id": "oaiapp_fixture"})
                route.fulfill(content_type="text/html", body=f'<p>Fixture provider authorization</p><a href="{callback}">Complete fixture sign-in</a>')
            context.route("https://auth.openai.com/**", fixture_login)
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(url + "/#agent")
            page.evaluate("FastMDXAgent.openSettings()")
            page.locator("#agent-oauth-picker summary").click()
            assert page.locator("#agent-subscription-provider option").evaluate_all(
                "options => options.map(option => option.value)") == ["", "openai-chatgpt", "claude", "kimi", "gemini"]
            assert page.locator("#agent-subscription-connect").is_disabled()
            page.locator("#agent-subscription-provider").select_option("openai-chatgpt")
            with page.expect_popup() as new_window:
                page.locator("#agent-subscription-connect").click()
            popup = new_window.value
            popup.get_by_role("link", name="Complete fixture sign-in").click()
            popup.close()
            page.wait_for_function("document.querySelector('#agent-connected-account').options.length === 2")
            account_id = service.snapshot()["accounts"][0]["id"]
            page.locator("#agent-connected-account").select_option(account_id)
            page.wait_for_function("document.querySelector('#agent-connected-model').value === 'fixture-model'")
            page.locator("#agent-connected-use").click()
            page.wait_for_function("document.querySelector('#agent-model-current').textContent.includes('ChatGPT subscription')")
            assert service.snapshot()["selection"] == "subscription"
            assert page.locator("#agent-key").input_value() == ""
            assert not errors
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
