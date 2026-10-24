"""The Agent's provider settings are exercised in a real browser."""

from __future__ import annotations

import json

import pytest


def _stale_client_state(page) -> None:
    page.add_init_script("""
      localStorage.setItem("agent-auth", JSON.stringify({
        provider: "anthropic", auth_method: "anthropic_console", connected: true
      }));
      localStorage.setItem("agent-provider", "anthropic");
      sessionStorage.setItem("agent-account", JSON.stringify({
        provider: "openai", auth_method: "chatgpt_plan", signed_in: true
      }));
      history.replaceState({provider: "anthropic", auth_method: "anthropic_console",
                            connected: true}, "", location.href);
    """)


def _agent_page(page, url: str) -> None:
    page.goto(url + "#agent", wait_until="domcontentloaded")
    page.wait_for_function(
        "document.querySelectorAll('#agent-provider option').length === 2")


def test_agent_settings_offer_api_keys_without_auth_controls_or_restored_state(
        tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    settings = tmp_path / "settings"
    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", str(settings))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("FASTMDX_MODEL_API_KEY", raising=False)
    session = start_dashboard_session(output=str(tmp_path), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.set_default_timeout(30000)
            _stale_client_state(page)
            _agent_page(page, session.url)

            assert page.locator("#agent-provider option").all_text_contents() == [
                "OpenAI", "Other (any OpenAI-compatible server)",
            ]
            assert page.locator("#agent-provider").input_value() == "openai"
            page.click("#agent-settings-open")
            page.wait_for_selector("#agent-settings:not([hidden])")
            assert page.locator("#agent-settings select").count() == 3
            assert page.locator('[id^="agent-auth"], [id^="agent-cache"]').count() == 0
            settings_text = page.locator("#agent-settings").inner_text().lower()
            for phrase in ("authentication", "continue with", "test connection", "disconnect"):
                assert phrase not in settings_text
            assert "connected" not in settings_text and "signed in" not in settings_text
            assert "no ai model is set" in settings_text
            browser.close()
    finally:
        session.server.shutdown()


def test_saving_an_openai_key_is_shown_after_reload_without_echoing_it(
        tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.agent import models
    from fastmdxplora.gui.server import start_dashboard_session

    settings = tmp_path / "settings"
    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", str(settings))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("FASTMDX_MODEL_API_KEY", raising=False)
    monkeypatch.setattr(models, "list_model_options",
                        lambda _choice: [{"id": "gpt-5", "label": "gpt-5"}])
    session = start_dashboard_session(output=str(tmp_path), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.set_default_timeout(30000)
            _stale_client_state(page)
            model_responses = []
            model_payloads = []

            def record_response(response):
                if "/api/agent/model" in response.url:
                    model_responses.append(response.text())

            def record_request(request):
                if "/api/agent/model" in request.url and request.method == "POST":
                    model_payloads.append(json.loads(request.post_data or "{}"))

            page.on("response", record_response)
            page.on("request", record_request)
            _agent_page(page, session.url)
            page.click("#agent-settings-open")
            page.wait_for_selector("#agent-settings:not([hidden])")
            page.select_option("#agent-provider", "openai")
            page.select_option("#agent-model", "gpt-5")
            page.fill("#agent-key", "browser-test-key")
            page.click("#agent-save-model")
            page.wait_for_function(
                "document.querySelector('#agent-key-help').textContent.includes("
                "'An API key is set')")
            assert page.locator("#agent-key").input_value() == ""
            assert "browser-test-key" not in page.locator("body").inner_text()

            page.reload(wait_until="domcontentloaded")
            page.wait_for_function(
                "document.querySelector('#agent-key-help').textContent.includes("
                "'An API key is set')")
            assert page.locator("#agent-provider").input_value() == "openai"
            assert page.locator("#agent-key").input_value() == ""
            assert "browser-test-key" not in page.locator("body").inner_text()
            assert all("browser-test-key" not in body for body in model_responses)

            page.click("#agent-settings-open")
            page.wait_for_selector("#agent-settings:not([hidden])")
            page.select_option("#agent-model", "__other__")
            page.fill("#agent-model-other", "gpt-5.1")
            page.click("#agent-save-model")
            page.wait_for_function(
                "document.querySelector('#agent-key-help').textContent.includes("
                "'An API key is set')")
            assert page.locator("#agent-key").input_value() == ""
            assert "browser-test-key" not in page.locator("body").inner_text()

            page.reload(wait_until="domcontentloaded")
            page.wait_for_function(
                "document.querySelector('#agent-key-help').textContent.includes("
                "'An API key is set')")
            page.click("#agent-settings-open")
            page.wait_for_selector("#agent-settings:not([hidden])")
            assert page.locator("#agent-model-current").inner_text() == "gpt-5.1"

            page.select_option("#agent-provider", "compatible")
            page.fill("#agent-base-url", "http://localhost:11434/v1")
            page.select_option("#agent-provider", "openai")
            summary = page.locator("#agent-model-summary").inner_text()
            assert "http://localhost:11434/v1" not in summary
            assert "An API key is set" in page.locator("#agent-key-help").text_content()
            page.click("#agent-save-model")
            page.wait_for_function(
                "document.querySelector('#agent-key-help').textContent.includes("
                "'An API key is set')")
            assert "http://localhost:11434/v1" not in page.locator(
                "#agent-model-summary").inner_text()
            assert model_payloads[-1]["provider"] == "openai"
            assert model_payloads[-1]["base_url"] == ""

            page.reload(wait_until="domcontentloaded")
            page.wait_for_function(
                "document.querySelector('#agent-key-help').textContent.includes("
                "'An API key is set')")
            page.click("#agent-settings-open")
            page.wait_for_selector("#agent-settings:not([hidden])")
            assert "An API key is set" in page.locator("#agent-key-help").text_content()
            assert "browser-test-key" not in page.locator("body").inner_text()
            assert all("browser-test-key" not in body for body in model_responses)
            assert json.loads((settings / "model.json").read_text(encoding="utf-8"))["api_key"] == (
                "browser-test-key")
            browser.close()
    finally:
        session.server.shutdown()


def test_saved_model_stays_separate_and_failed_saves_keep_settings_open(
        tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.agent import models
    from fastmdxplora.gui.server import start_dashboard_session

    settings = tmp_path / "settings"
    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", str(settings))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(models, "list_model_options",
                        lambda _choice: [{"id": "gpt-5", "label": "gpt-5"}])
    models.save_choice(models.ModelChoice("openai", "gpt-5"),
                       key="browser-failure-test-key")
    session = start_dashboard_session(output=str(tmp_path), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.set_default_timeout(30000)
            _agent_page(page, session.url)
            page.click("#agent-settings-open")
            page.wait_for_selector("#agent-settings:not([hidden])")

            page.select_option("#agent-model", "__other__")
            page.fill("#agent-model-other", "gpt-5.1")
            summary = page.locator("#agent-model-summary").inner_text()
            assert page.locator("#agent-model-current").inner_text() == "gpt-5"
            assert "Current selection (unsaved): OpenAI · gpt-5.1" in summary

            refused = '<img data-provider-error src=x> provider settings refused'

            def refuse_save(route):
                if route.request.method == "POST":
                    route.fulfill(json={"ok": False, "error": refused})
                else:
                    route.continue_()

            page.route("**/api/agent/model", refuse_save)
            page.click("#agent-save-model")
            page.wait_for_function(
                "error => document.getElementById('agent-model-summary').textContent"
                ".includes(error)", arg=refused)
            assert refused in page.locator("#agent-model-summary").inner_text()
            assert page.locator("#agent-model-current").inner_text() == "gpt-5"
            assert page.locator("[data-provider-error]").count() == 0
            assert page.locator("#agent-settings").is_visible()
            assert page.locator("#agent-save-model").is_enabled()

            page.unroute("**/api/agent/model")

            def abort_save(route):
                if route.request.method == "POST":
                    route.abort()
                else:
                    route.continue_()

            page.route("**/api/agent/model", abort_save)
            page.click("#agent-save-model")
            page.wait_for_function(
                "document.getElementById('agent-model-summary').textContent"
                ".includes('The provider settings could not be saved.')")
            assert page.locator("#agent-model-current").inner_text() == "gpt-5"
            assert page.locator("#agent-settings").is_visible()
            assert page.locator("#agent-save-model").is_enabled()
            browser.close()
    finally:
        session.server.shutdown()


def test_a_stale_model_file_shows_no_model_or_signed_in_state_in_the_browser(
        tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    settings = tmp_path / "settings"
    settings.mkdir()
    (settings / "model.json").write_text(json.dumps({
        "provider": "anthropic",
        "model": "old-model",
        "cache_for": "one-hour",
        "auth_method": "chatgpt_plan",
        "oauth_access_token": "stale-browser-test-token",
        "account_id": "stale-account",
        "session": {"id": "stale-session"},
    }), encoding="utf-8")
    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", str(settings))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("FASTMDX_MODEL_API_KEY", raising=False)
    session = start_dashboard_session(output=str(tmp_path), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.set_default_timeout(30000)
            _stale_client_state(page)
            model_responses = []
            page.on("response", lambda response: model_responses.append(response.text())
                    if "/api/agent/model" in response.url else None)
            _agent_page(page, session.url)
            page.click("#agent-settings-open")
            page.wait_for_selector("#agent-settings:not([hidden])")

            assert page.locator("#agent-provider").input_value() == "openai"
            assert "no ai model is set" in page.locator("#agent-model-summary").inner_text().lower()
            settings_text = page.locator("#agent-settings").inner_text().lower()
            assert "connected" not in settings_text and "signed in" not in settings_text
            assert "stale-browser-test-token" not in page.locator("body").inner_text()
            assert all("stale-browser-test-token" not in body for body in model_responses)
            browser.close()
    finally:
        session.server.shutdown()
