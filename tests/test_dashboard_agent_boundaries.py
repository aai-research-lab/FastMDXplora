"""Human control, installed knowledge and evidence-based dashboard explanations."""
from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path

import pytest

from fastmdxplora.agent.knowledge import dashboard_knowledge, error_reference
from fastmdxplora.gui.agent_panel import context_endpoint, propose_endpoint, run_endpoint
from fastmdxplora.gui.research import context_for, residue_evidence
from fastmdxplora.refusals import CODES


@pytest.fixture
def model(monkeypatch):
    import fastmdxplora.agent as agent

    prompts = []
    replies = ["SAY: The selected evidence is incomplete."]
    def complete(prompt):
        prompts.append(prompt)
        return replies[0]
    monkeypatch.setattr(agent, "completion_for", lambda: complete)
    monkeypatch.setattr(agent, "load_choice", lambda: None)
    return prompts, replies


def test_installed_knowledge_includes_every_registered_error_and_disclosure():
    knowledge = dashboard_knowledge()
    for code in CODES:
        assert f"`{code.id}`" in knowledge
        assert f"disclosure={code.disclosure}" in knowledge
    bundled = files("fastmdxplora.agent").joinpath("knowledge/errors.md").read_text(encoding="utf-8")
    assert bundled == error_reference(), "Regenerate the packaged error reference after registry changes."
    assert "External/unclassified" in knowledge


@pytest.mark.parametrize("code", ["environment.provider.connection_failed", "environment.provider.usage_limit"])
def test_agent_preserves_subscription_failure_code_without_api_fallback(tmp_path, monkeypatch, code):
    from types import SimpleNamespace

    from fastmdxplora.agent.openai_plan import ConnectionError
    class Connections:
        def completion(self):
            raise ConnectionError("Sanitized fixture provider failure", code=code)
    monkeypatch.setattr("fastmdxplora.gui.provider_connections.service_for", lambda runtime: Connections())
    answer = propose_endpoint({"request": "Explain the study"}, SimpleNamespace())
    assert not answer["ok"] and answer["code"] == code


def test_explanation_receives_packaged_reference(model):
    prompts, _ = model
    result = propose_endpoint({"request": "Explain this warning"})
    assert result["answer"]
    assert "Knowledge contract version: 1" in prompts[0]
    assert "setup.chemistry.protonation_undetermined" in prompts[0]
    assert "Never launch/stop/resume" in prompts[0]


def test_context_opt_out_excludes_view_but_keeps_study_isolation(tmp_path, model):
    from types import SimpleNamespace

    prompts, _ = model
    runtime = SimpleNamespace(active_root=tmp_path)
    payload = {"request": "Explain the workflow", "include_view_context": False,
               "view_context": {"study": str(tmp_path), "page": "viewer",
                                "selection": {"chain": "A", "resseq": 999, "resname": "GLU"}}}
    preview = context_endpoint(payload, runtime)
    answer = propose_endpoint(payload, runtime)
    assert preview["ok"] and preview["view"] == {}
    assert answer["context_receipt"] == preview
    assert "Selected residue evidence:" not in prompts[0]
    assert '"resseq": 999' not in prompts[0]
    assert "Current view selection excluded" in prompts[0]
    payload["view_context"]["study"] = str(tmp_path / "other")
    assert not propose_endpoint(payload, runtime)["ok"]
    assert len(prompts) == 1


def test_context_inspection_uses_recorded_evidence_and_stream_receipt(tmp_path, model):
    from types import SimpleNamespace

    folder = tmp_path / "setup"
    folder.mkdir()
    (folder / "setup_parameters.json").write_text('{"parameters":{"ph":6.5}}')
    runtime = SimpleNamespace(active_root=tmp_path)
    payload = {"request": "Explain the selected pH", "include_view_context": True,
               "view_context": {"study": str(tmp_path), "page": "run", "field": "setup.ph", "field_value": "7"}}
    preview = context_endpoint(payload, runtime)
    assert "Recorded setting value: 6.5" in preview["view_evidence"]
    events = []
    result = propose_endpoint(payload, runtime, emit=events.append)
    assert events[0] == {"type": "context", "context": preview}
    assert result["context_receipt"]["fingerprint"] == preview["fingerprint"]
    (folder / "setup_parameters.json").write_text('{"parameters":{"ph":8}}')
    assert context_endpoint(payload, runtime)["fingerprint"] != preview["fingerprint"]
    assert not context_endpoint({**payload, "include_view_context": "false"}, runtime)["ok"]
    runtime.data_stale = True
    assert not context_endpoint(payload, runtime)["ok"]


def test_reply_from_a_changed_study_is_refused(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import fastmdxplora.agent as agent

    runtime = SimpleNamespace(active_root=tmp_path)
    def switched(prompt):
        runtime.active_root = tmp_path / "another-study"
        return "SAY: This is the old study."
    monkeypatch.setattr(agent, "completion_for", lambda: switched)
    answer = propose_endpoint({"request": "Explain this study", "view_context": {"study": str(tmp_path)}}, runtime)
    assert not answer["ok"] and "study changed" in answer["error"]
    assert "answer" not in answer


@pytest.mark.parametrize("theme", ["graphite", "ink", "paper"])
def test_agent_loading_and_provider_failure_layouts(tmp_path, monkeypatch, model, theme):
    import threading

    playwright = pytest.importorskip("playwright.sync_api")
    import fastmdxplora.agent as agent
    from fastmdxplora.agent.openai_plan import ConnectionError
    from fastmdxplora.gui.server import start_test_server
    from tests.test_the_drawing_scripts_run_in_a_browser import _write_study

    entered, released = threading.Event(), threading.Event()
    failure = "Controlled provider connection failed. " + "Retry through your selected connection. " * 12

    def delayed_failure(prompt):
        entered.set()
        assert released.wait(60), "The layout check did not release its controlled provider"
        raise ConnectionError(failure, code="environment.provider.connection_failed")

    monkeypatch.setattr(agent, "completion_for", lambda: delayed_failure)
    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", str(tmp_path / "isolated-settings"))
    root = _write_study(tmp_path / "study")
    before = {path.relative_to(root): path.read_bytes() for path in root.rglob("*")
              if path.is_file() and path.parts[-2] in {"setup", "simulation", "analysis"}}
    server, url = start_test_server(root)
    try:
        with playwright.sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.add_init_script("localStorage.setItem('fmx.theme', " + json.dumps(theme) + ")")
            page.goto(url + "/#agent")
            page.locator("#agent-request").fill("Explain the available evidence without running a simulation.")
            page.locator("#agent-propose").click()
            assert entered.wait(10)
            for state in ("loading", "failure"):
                if state == "failure":
                    released.set()
                    page.get_by_text(failure, exact=False).first.wait_for()
                    assert page.locator("#agent-propose").get_attribute("aria-label") == "Send"
                else:
                    assert page.locator("#agent-propose").get_attribute("aria-label") == "Stop"
                for zoom in (100, 200):
                    page.evaluate("size => document.documentElement.style.fontSize = size + '%'", zoom)
                    for width in (1440, 1280, 1024, 768, 390):
                        page.set_viewport_size({"width": width, "height": 1000})
                        page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
                        outside = page.locator('.page[data-page="agent"]').evaluate("""el =>
                            Array.from(el.querySelectorAll('button,input,select,textarea'))
                            .filter(node => node.checkVisibility())
                            .filter(node => {const r=node.getBoundingClientRect(); return r.x < -1 || r.right > innerWidth+1;})
                            .map(node => node.id)""")
                        assert not outside, (theme, state, zoom, width, outside)
                        assert page.locator("#agent-thread").evaluate("el => el.scrollWidth <= el.clientWidth + 1")
                        assert not page.locator('[data-role="run"]').is_visible()
            browser.close()
        for path, content in before.items():
            assert (root / path).read_bytes() == content
    finally:
        released.set()
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize("theme", ["graphite", "ink", "paper"])
def test_context_inspector_opt_out_and_toolbar_layout_in_browser(tmp_path, monkeypatch, model, theme):
    playwright = pytest.importorskip("playwright.sync_api")
    from fastmdxplora.gui.server import start_test_server
    from tests.test_the_drawing_scripts_run_in_a_browser import _write_study

    _write_study(tmp_path)
    server, url = start_test_server(tmp_path)
    try:
        with playwright.sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.add_init_script("localStorage.setItem('fmx.theme', " + json.dumps(theme) + ")")
            page.goto(url + "/#agent")
            page.locator("#agent-context-review summary").click()
            page.locator("#agent-use-context").uncheck()
            page.locator("#agent-inspect-context").click()
            page.wait_for_function("document.getElementById('agent-context-evidence').textContent.includes('Current view selection excluded')")
            sent = []
            page.on("request", lambda req: sent.append(req.post_data_json) if req.url.endswith("/api/agent/propose-stream") else None)
            page.locator("#agent-request").fill("Explain this workflow")
            page.locator("#agent-propose").click()
            page.wait_for_function("document.getElementById('agent-context-evidence').textContent.includes('Evidence used by the last message')")
            assert sent and sent[0]["include_view_context"] is False
            toolbar = page.locator(".research-tools").bounding_box()
            composer = page.locator(".agent-composer").bounding_box()
            assert toolbar["y"] + toolbar["height"] <= composer["y"]
            page.set_viewport_size({"width": 390, "height": 844})
            page.locator("#research-bookmarks-toggle").click()
            assert page.locator("#research-bookmarks").is_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            page.locator("#research-bookmarks-close").click()
            page.locator("#agent-context-review summary").click()
            box = page.locator("#agent-request").bounding_box()
            assert box["width"] > 0 and box["x"] + box["width"] <= 391
            page.locator("#agent-context-review summary").click()
            for zoom in (100, 200):
                page.add_style_tag(content=f"html {{font-size: {zoom}% !important;}}")
                for width in (1440, 1280, 1024, 768, 390):
                    page.set_viewport_size({"width": width, "height": 900})
                    outside = page.locator('.page[data-page="agent"]').evaluate("""panel =>
                        Array.from(panel.querySelectorAll('button,input,select,textarea,summary'))
                        .filter(el => el.checkVisibility({checkVisibilityCSS:true,checkOpacity:true}))
                        .filter(el => {const r=el.getBoundingClientRect(); return r.width > 0 && (r.x < -1 || r.right > innerWidth+1);})
                        .map(el => el.id || el.textContent.slice(0,50))""")
                    assert not outside, (theme, zoom, width, outside)
                    assert page.locator("#agent-context-evidence").is_visible()
                    assert "Evidence used by the last message" in page.locator("#agent-context-evidence").inner_text()
                    assert not page.locator("#agent-use-context").is_checked()
            browser.close()
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize("theme", ["graphite", "ink", "paper"])
def test_dashboard_shell_and_bookmarks_fit_each_theme(tmp_path, theme):
    playwright = pytest.importorskip("playwright.sync_api")
    from fastmdxplora.gui.server import start_test_server
    from tests.test_the_drawing_scripts_run_in_a_browser import _write_study

    _write_study(tmp_path)
    server, url = start_test_server(tmp_path)
    try:
        with playwright.sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            page.add_init_script("localStorage.setItem('fmx.theme', " + json.dumps(theme) + ")")
            for width in (1440, 1280, 1024, 768, 390):
                page.set_viewport_size({"width": width, "height": 900})
                for view in ("studies", "overview", "viewer", "analysis", "report", "files", "agent", "run", "settings", "cite"):
                    page.goto(url + "/#" + view)
                    page.wait_for_function("document.body.dataset.theme === " + json.dumps(theme))
                    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), (theme, width, view)
                page.locator("#research-bookmarks-toggle").click()
                panel = page.locator("#research-bookmarks")
                assert panel.is_visible()
                bounds = panel.bounding_box()
                assert bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= width + 1
                page.locator("#research-bookmarks-close").focus()
                page.keyboard.press("Enter")
                assert not panel.is_visible()
                assert page.locator("#research-bookmarks-toggle").get_attribute("aria-expanded") == "false"
                assert page.locator("#research-bookmarks-toggle").evaluate("el => el === document.activeElement")
                page.locator("#research-bookmarks-toggle").click()
                page.keyboard.press("Escape")
                assert not panel.is_visible()
                assert page.locator("#research-bookmarks-toggle").evaluate("el => el === document.activeElement")
            browser.close()
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize("connected", [True, False])
def test_settings_engine_tracks_selected_subscription_and_refreshes_on_open(tmp_path, monkeypatch, connected):
    playwright = pytest.importorskip("playwright.sync_api")
    from fastmdxplora.gui.server import start_test_server
    from tests.test_the_drawing_scripts_run_in_a_browser import _write_study

    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", str(tmp_path / "isolated-settings"))
    server, url = start_test_server(_write_study(tmp_path / "study"))
    state = {"ok": True, "selection": "subscription", "active": "fixture",
             "accounts": [{"id": "fixture", "provider": "openai-chatgpt",
                           "model": "gpt-6.1-sol", "reasoning": "high", "connected": connected}]}
    try:
        with playwright.sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            page.route("**/api/agent/connections", lambda route: route.fulfill(json=state))
            page.goto(url + "/#overview")
            page.locator("#settings-open").click()
            expected = "ChatGPT · gpt-6.1-sol · " + ("reasoning: high" if connected else "reconnect required")
            playwright.expect(page.locator("#settings-engine")).to_have_text(expected)
            page.locator("#settings-open").click()
            state["accounts"] = []
            page.locator("#settings-open").click()
            playwright.expect(page.locator("#settings-engine")).to_have_text("Select a subscription account")
            page.locator("#settings-open").click()
            state["ok"] = False
            page.locator("#settings-open").click()
            playwright.expect(page.locator("#settings-engine")).to_have_text("Connection status unavailable")
            page.locator("#settings-open").click()
            state.update(ok=True, selection="api")
            page.route("**/api/agent/model", lambda route: route.fulfill(json={
                "ok": True, "current": {"provider": "openai", "model": "fixture-api-model"}, "providers": []}))
            page.locator("#settings-open").click()
            playwright.expect(page.locator("#settings-engine")).to_have_text("openai · fixture-api-model")
            browser.close()
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize("theme", ["graphite", "ink", "paper"])
def test_column_and_file_splitters_resize_with_keyboard_and_keep_sources(tmp_path, monkeypatch, theme):
    import hashlib
    playwright = pytest.importorskip("playwright.sync_api")
    from fastmdxplora.gui.server import start_test_server
    from tests.test_the_drawing_scripts_run_in_a_browser import _write_study

    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", str(tmp_path / "isolated-settings"))
    root = _write_study(tmp_path / "study")
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in root.rglob("*") if path.is_file()}
    server, url = start_test_server(root)
    try:
        with playwright.sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.add_init_script("localStorage.setItem('fmx.theme', " + json.dumps(theme) + "); localStorage.setItem('fmx.panelCollapsed', '0')")
            page.goto(url + "/#files")
            sidebar = page.get_by_role("separator", name="Resize sidebar", exact=True)
            panel = page.get_by_role("separator", name="Resize side panel", exact=True)
            assert sidebar.get_attribute("tabindex") == "0"
            sidebar.press("End")
            assert page.locator(".sidebar").bounding_box()["width"] == pytest.approx(320)
            sidebar.press("Home")
            assert page.locator(".sidebar").bounding_box()["width"] == pytest.approx(180)
            sidebar.press("Shift+ArrowRight")
            assert sidebar.get_attribute("aria-valuenow") == "212"
            panel.press("End")
            assert page.locator("#side-panel").bounding_box()["width"] == pytest.approx(640)
            panel.press("Home")
            panel.press("ArrowLeft")
            assert page.locator("#side-panel").bounding_box()["width"] == pytest.approx(296)
            page.locator('.file-row').filter(has=page.locator('.file-title[title="simulation/energy.csv"]')).get_by_role("button", name="View", exact=True).click()
            seam = page.get_by_role("separator", name="Resize the file list", exact=True)
            playwright.expect(seam).to_be_visible()
            seam.press("End")
            assert seam.get_attribute("aria-valuenow") == "100"
            seam.press("ArrowUp")
            assert seam.get_attribute("aria-valuenow") == "95"
            seam.press("Home")
            assert seam.get_attribute("aria-valuenow") == "0"
            page.reload()
            playwright.expect(sidebar).to_have_attribute("aria-valuenow", "212")
            assert panel.get_attribute("aria-valuenow") == "296"
            assert page.locator("#side-seam").get_attribute("aria-valuenow") == "0"
            browser.close()
        assert before == {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in before}
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize("theme", ["graphite", "ink", "paper"])
def test_sidebar_brand_and_collapse_fit_resized_columns_and_large_text(tmp_path, theme):
    playwright = pytest.importorskip("playwright.sync_api")
    from fastmdxplora.gui.server import start_test_server
    from tests.test_the_drawing_scripts_run_in_a_browser import _write_study

    _write_study(tmp_path)
    server, url = start_test_server(tmp_path)
    try:
        with playwright.sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.add_init_script("localStorage.setItem('fmx.theme', " + json.dumps(theme) + ")")
            for width in (180, 232, 320):
                page.add_init_script(f"localStorage.setItem('fmx.sidebarWidth', '{width}')")
                for zoom in (100, 200):
                    page.goto(url + "/#overview")
                    page.wait_for_function("document.body.dataset.theme === " + json.dumps(theme))
                    page.add_style_tag(content=f"html {{font-size: {zoom}% !important;}}")
                    product = page.locator(".sidebar .brand-product")
                    assert product.inner_text() == "FastMDXplora"
                    assert product.evaluate("el => el.scrollWidth <= el.clientWidth + 1"), (theme, width, zoom)
                    if zoom == 100:
                        assert product.evaluate("el => el.clientHeight <= parseFloat(getComputedStyle(el).lineHeight) + 1"), (theme, width)
                    bounds = page.locator(".sidebar").bounding_box()
                    button = page.locator("#sidebar-collapse").bounding_box()
                    assert button and bounds and button["x"] >= bounds["x"]
                    assert button["x"] + button["width"] <= bounds["x"] + bounds["width"] + 1
                    page.locator("#sidebar-collapse").click()
                    assert page.locator(".sidebar-expand").is_visible()
                    page.locator(".sidebar-expand").click()
                    assert product.is_visible()
            browser.close()
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize("theme", ["graphite", "ink", "paper"])
def test_research_controls_follow_double_text_size_without_losing_actions(tmp_path, theme):
    playwright = pytest.importorskip("playwright.sync_api")
    from fastmdxplora.gui.server import start_test_server
    from tests.test_the_drawing_scripts_run_in_a_browser import _write_study

    _write_study(tmp_path)
    server, url = start_test_server(tmp_path)
    try:
        with playwright.sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 390, "height": 900})
            page.add_init_script("localStorage.setItem('fmx.theme', " + json.dumps(theme) + ")")
            page.goto(url + "/#agent")
            base = page.locator("#research-bookmarks-toggle").evaluate("el => parseFloat(getComputedStyle(el).fontSize)")
            page.add_style_tag(content="html {font-size: 200% !important;}")
            scaled = page.locator("#research-bookmarks-toggle").evaluate("el => parseFloat(getComputedStyle(el).fontSize)")
            assert scaled == pytest.approx(base * 2)
            page.locator("#research-bookmarks-toggle").click()
            page.locator("#research-title").fill("A long research title at double text size")
            page.locator("#research-bookmarks-close").click()
            assert page.locator("#research-bookmarks-toggle").evaluate("el => el === document.activeElement")
            for name in ("research-bookmarks-toggle", "research-agent-toggle", "agent-request", "agent-propose"):
                bounds = page.locator("#" + name).bounding_box()
                assert bounds and bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= 391, name
            for width in (1280, 390):
                page.set_viewport_size({"width": width, "height": 900})
                for view in ("studies", "overview", "viewer", "analysis", "report", "files", "agent", "run", "settings", "cite"):
                    page.goto(url + "/#" + view)
                    page.add_style_tag(content="html {font-size: 200% !important;}")
                    outside = page.evaluate("""() => Array.from(document.querySelectorAll('button,input:not([type=hidden]),select,textarea'))
                        .filter(el => el.checkVisibility({checkVisibilityCSS:true,checkOpacity:true}) && !el.closest('table,.table-wrap'))
                        .filter(el => {const r=el.getBoundingClientRect(); return r.width > 0 && (r.x < -1 || r.right > innerWidth+1);})
                        .map(el => el.id || el.textContent.slice(0,60))""")
                    assert not outside, (theme, width, view, outside)
            browser.close()
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize("theme", ["graphite", "ink", "paper"])
def test_agent_settings_native_dialog_keyboard_and_scaled_layout(tmp_path, theme):
    playwright = pytest.importorskip("playwright.sync_api")
    from fastmdxplora.gui.server import start_test_server
    from tests.test_the_drawing_scripts_run_in_a_browser import _write_study

    _write_study(tmp_path)
    server, url = start_test_server(tmp_path)
    try:
        with playwright.sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 390, "height": 900})
            page.add_init_script("localStorage.setItem('fmx.theme', " + json.dumps(theme) + ")")
            page.goto(url + "/#agent")
            page.add_style_tag(content="html {font-size: 200% !important;}")
            page.locator("#agent-settings-open").click()
            dialog = page.locator("#agent-settings")
            assert dialog.evaluate("el => el.matches(':modal')")
            assert page.locator("#agent-settings-close").evaluate("el => el === document.activeElement")
            page.keyboard.press("Shift+Tab")
            assert dialog.evaluate("el => el.contains(document.activeElement)")
            for _ in range(12):
                page.keyboard.press("Tab")
                assert dialog.evaluate("el => el.contains(document.activeElement)")
            outside = dialog.evaluate("""dialog => Array.from(dialog.querySelectorAll('button,input,select,textarea'))
                .filter(el => el.checkVisibility({checkVisibilityCSS:true,checkOpacity:true}))
                .filter(el => {const r=el.getBoundingClientRect(); return r.x < -1 || r.right > innerWidth+1;})
                .map(el => el.id)""")
            assert not outside, outside
            page.keyboard.press("Escape")
            assert dialog.is_hidden()
            assert page.locator("#agent-settings-open").evaluate("el => el === document.activeElement")
            browser.close()
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize("theme", ["graphite", "ink", "paper"])
def test_dashboard_text_palette_and_primary_actions_have_readable_contrast(tmp_path, theme):
    import re

    playwright = pytest.importorskip("playwright.sync_api")
    from fastmdxplora.gui.server import start_test_server
    from tests.test_the_drawing_scripts_run_in_a_browser import _write_study

    def rgb(value):
        if value.startswith("#"):
            return [int(value[index:index + 2], 16) / 255 for index in (1, 3, 5)]
        return [float(number) / 255 for number in re.findall(r"[\d.]+", value)[:3]]

    def contrast(one, two):
        def light(color):
            values = [value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4 for value in rgb(color)]
            return sum(value * weight for value, weight in zip(values, (0.2126, 0.7152, 0.0722)))
        first, second = sorted((light(one), light(two)))
        return (second + 0.05) / (first + 0.05)

    _write_study(tmp_path)
    server, url = start_test_server(tmp_path)
    try:
        with playwright.sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            page.add_init_script("localStorage.setItem('fmx.theme', " + json.dumps(theme) + ")")
            page.goto(url + "/#agent")
            page.wait_for_function("document.body.dataset.theme === " + json.dumps(theme))
            tokens = page.evaluate("""() => {const style=getComputedStyle(document.body); const names=['text-primary','text-secondary','text-muted','background-primary','background-secondary','background-elevated','accent-cyan','accent-blue','accent-orange','accent-red','accent-green','on-accent']; return Object.fromEntries(names.map(name=>[name,style.getPropertyValue('--'+name).trim()]));}""")
            for foreground in ("text-primary", "text-secondary", "text-muted", "accent-cyan", "accent-orange", "accent-red", "accent-green"):
                for background in ("background-primary", "background-secondary", "background-elevated"):
                    assert contrast(tokens[foreground], tokens[background]) >= 4.5, (theme, foreground, background)
            for accent in ("accent-cyan", "accent-blue"):
                assert contrast(tokens["on-accent"], tokens[accent]) >= 4.5, (theme, accent)
            page.locator("#agent-settings-open").click()
            for name in ("agent-provider", "agent-model", "agent-key", "agent-budget"):
                colors = page.locator("#" + name).evaluate("el => {const style=getComputedStyle(el); return {border:style.borderTopColor,background:style.backgroundColor,placeholder:getComputedStyle(el,'::placeholder').color};}")
                for background in ("background-primary", "background-secondary", "background-elevated"):
                    assert contrast(colors["border"], tokens[background]) >= 3, (theme, name, background)
            page.keyboard.press("Escape")
            browser.close()
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize("mode", ["autonomous", "unvalidated", "unexpected"])
def test_dashboard_cannot_select_unreviewed_modes(model, mode):
    prompts, _ = model
    result = propose_endpoint({"request": "Run it", "agent": mode})
    assert result["code"] == "config.option.not_permitted"
    assert not prompts


@pytest.mark.parametrize("action", ["run", "stop", "run the fix", "rerun windows"])
def test_model_execution_actions_become_explanations(model, action):
    _, replies = model
    replies[0] = "DO: " + ("rerun windows 2 at 6000" if action == "rerun windows" else action)
    result = propose_endpoint({"request": "Do it now"})
    assert result["answer"]
    assert "action" not in result


def test_legacy_agent_launch_route_never_reaches_the_runtime():
    class Runtime:
        def launch_from_config(self, *args, **kwargs):
            pytest.fail("An Agent request must not reach launch")
    for mode in ("assisted", "autonomous", "unvalidated"):
        result = run_endpoint({"config": {"agent": mode, "systems": [{"system": "1UAO"}]},
                               "budget_hours": 1}, Runtime())
        assert not result["ok"]
        assert result["code"] == "config.option.not_permitted"


def test_recorded_warning_frame_and_setting_are_evidence(tmp_path):
    sim = tmp_path / "simulation"
    sim.mkdir()
    (sim / "live_events.log").write_text("t0\twarning\tInsufficient sampling\nt1\terror\tapi_key=secret-value failed\n")
    (sim / "playback_index.json").write_text(json.dumps({"source_signature": "sample-v1", "source_kind": "dcd",
        "frame_indices": [0, 20, 40], "frame_times_ns": [0, 0.1, 0.2]}))
    setup = tmp_path / "setup"
    setup.mkdir()
    (setup / "setup_parameters.json").write_text('{"parameters":{"ph":6.5}}')
    result = context_for(tmp_path, {"warning": "t0", "frame": 1, "playback_signature": "sample-v1", "field": "setup.ph"})
    assert "Insufficient sampling" in result
    assert '"source_frame": 20' in result
    assert "Recorded setting value: 6.5" in result
    assert "could not be verified" in context_for(tmp_path, {"frame": 1, "playback_signature": "old"})
    assert "secret-value" not in context_for(tmp_path, {"page": "overview"})


def test_residue_comparison_refuses_ambiguous_analysis_mapping(tmp_path, monkeypatch):
    import fastmdxplora.gui.series as series
    setup = tmp_path / "setup"
    setup.mkdir()
    (setup / "topology.pdb").write_text("ATOM      1  CA  GLU A  57       0.000   0.000   0.000  1.00  0.00           C\n")
    folder = tmp_path / "analysis/rmsf"
    folder.mkdir(parents=True)
    (folder / "options.json").write_text('{"options":{"per_residue":true}}')

    monkeypatch.setattr(series, "series_payload", lambda *args: {"ok": True,
        "residues": [{"chain": "A", "resi": 57}, {"chain": "B", "resi": 57}],
        "y": [0.1, 0.8], "unit": "nm"})
    selected = {"chain": "A", "resseq": 57, "resname": "GLU"}
    result = residue_evidence(tmp_path, selected)
    assert result["rmsf"]["value"] == 0.1
    assert "mechanism" in result["notice"]
    assert "rmsf" not in residue_evidence(tmp_path, {**selected, "chain": "Z"})
    assert "rmsf" not in residue_evidence(tmp_path, {**selected, "icode": "A"})


def test_pinned_comparison_supplies_two_verified_residue_measurements(tmp_path, monkeypatch):
    import fastmdxplora.gui.series as series
    setup = tmp_path / "setup"
    setup.mkdir()
    (setup / "topology.pdb").write_text(
        "ATOM      1  CA  GLU A  57       0.000   0.000   0.000  1.00  0.00           C\n"
        "ATOM      2  CA  GLU B  57       1.000   0.000   0.000  1.00  0.00           C\n")
    folder = tmp_path / "analysis/rmsf"
    folder.mkdir(parents=True)
    (folder / "options.json").write_text('{"options":{"per_residue":true}}')
    monkeypatch.setattr(series, "series_payload", lambda *args: {"ok": True,
        "residues": [{"chain": "A", "resi": 57}, {"chain": "B", "resi": 57}],
        "y": [0.1, 0.8], "unit": "nm"})
    selection = {"chain": "A", "resseq": 57, "resname": "GLU"}
    text = context_for(tmp_path, {"page": "viewer", "selection": selection,
                                "comparison_selection": {**selection, "chain": "B"}})
    assert '"value": 0.1' in text and '"value": 0.8' in text
    assert "Pinned comparison residue evidence" in text
    assert "does not establish its chemical cause" in text
    missing = context_for(tmp_path, {"page": "viewer", "selection": selection,
                                   "comparison_selection": {**selection, "chain": "Z"}})
    assert '"value": 0.8' not in missing
    assert "not verified" in missing


@pytest.mark.parametrize("per_residue", [False, None, "true", 1])
def test_atom_rmsf_or_unknown_granularity_is_not_assigned_to_residue(tmp_path, per_residue):
    setup = tmp_path / "setup"
    setup.mkdir()
    (setup / "topology.pdb").write_text("ATOM      1  CA  GLU A  57       0.000   0.000   0.000  1.00  0.00           C\n")
    folder = tmp_path / "analysis/rmsf"
    folder.mkdir(parents=True)
    (folder / "options.json").write_text(json.dumps({"options": {"per_residue": per_residue}}))
    (folder / "rmsf.dat").write_text("57 0.8\n")
    result = residue_evidence(tmp_path, {"chain": "A", "resseq": 57, "resname": "GLU"})
    assert "rmsf" not in result
    assert "atom index" in result["reason"]


def test_chainless_residue_table_requires_globally_unique_identity_and_records_scope(tmp_path):
    setup = tmp_path / "setup"
    setup.mkdir()
    topology = setup / "topology.pdb"
    topology.write_text("ATOM      1  CA  GLU A  57       0.000   0.000   0.000  1.00  0.00           C\n")
    folder = tmp_path / "analysis/rmsf"
    folder.mkdir(parents=True)
    (folder / "options.json").write_text('{"selection":"name CA","options":{"per_residue":true,"ref":2}}')
    (folder / "rmsf.dat").write_text("57 0.8\n")
    (folder.parent / "analysis_manifest.json").write_text('{"n_frames":100,"load_kwargs":{"stride":2,"first":3}}')
    selected = {"chain": "A", "resseq": 57, "resname": "GLU"}
    result = residue_evidence(tmp_path, selected)
    assert result["rmsf"]["value"] == 0.8
    assert result["rmsf"]["scope"]["selection"] == "name CA"
    assert result["rmsf"]["scope"]["alignment_reference_frame"] == 2
    assert result["rmsf"]["scope"]["sampling"]["stride"] == 2
    assert result["rmsf"]["scope"]["n_frames"] == 100
    topology.write_text(topology.read_text() + "ATOM      2  CA  GLU B  57       1.000   0.000   0.000  1.00  0.00           C\n")
    assert "rmsf" not in residue_evidence(tmp_path, selected)


@pytest.mark.parametrize("theme", ["graphite", "ink", "paper"])
def test_a_suggestion_waits_for_add_to_draft_and_sidebar_can_be_disabled(tmp_path, monkeypatch, model, theme):
    playwright = pytest.importorskip("playwright.sync_api")
    from fastmdxplora.gui.server import start_test_server
    from tests.test_the_drawing_scripts_run_in_a_browser import _write_study

    _, replies = model
    replies[0] = "systems:\n  - {id: suggested, system: 1UAO}\nsetup:\n  ph: 6.5\n"
    root = _write_study(tmp_path / "study")
    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", str(tmp_path / "isolated-settings"))
    server, url = start_test_server(root)
    try:
        with playwright.sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.add_init_script("localStorage.setItem('fmx.theme', " + json.dumps(theme) + ")")
            page.goto(url + "/#agent")
            page.locator("#agent-request").fill("Suggest a study at pH 6.5")
            loads = []
            page.on("request", lambda req: loads.append(req.url) if req.url.endswith("/api/agent/review-draft") and req.post_data_json.get("action") == "accept" else None)
            page.locator("#agent-propose").click()
            page.locator("[data-role=load]").wait_for()
            assert not loads, "A reply must not overwrite the builder's draft"
            assert not page.locator("[data-role=run]").is_visible()
            page.locator("[data-role=load]").click()
            page.locator("#draft-review-dialog").wait_for(state="visible")
            assert not loads
            assert page.locator("#draft-review-accept").is_disabled()
            assert "setup.ph" in page.locator("#draft-review-rows").inner_text()
            # Review stays usable before human approval in every supported
            # layout; field differences may scroll locally inside their table.
            for zoom in (100, 200):
                page.evaluate("size => document.documentElement.style.fontSize = size + '%'", zoom)
                for width in (1440, 1280, 1024, 768, 390):
                    page.set_viewport_size({"width": width, "height": 1000})
                    page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
                    outside = page.locator("#draft-review-dialog").evaluate("""el =>
                        Array.from(el.querySelectorAll('button,input,select,textarea'))
                        .filter(node => node.checkVisibility() && !node.closest('table'))
                        .filter(node => {const r=node.getBoundingClientRect(); return r.x < -1 || r.right > innerWidth+1;})
                        .map(node => node.id)""")
                    assert not outside, (theme, zoom, width, outside)
                    assert page.locator("#draft-review-accept").is_disabled()
                    assert "setup.ph" in page.locator("#draft-review-rows").inner_text()
                    assert not loads
            page.evaluate("document.documentElement.style.fontSize = '100%'")
            page.set_viewport_size({"width": 1440, "height": 1000})
            page.locator("#draft-review-confirmed").check()
            page.locator("#draft-review-accept").click()
            page.wait_for_function("FastMDXDashboard.state.activePage === 'run'")
            assert loads
            assert page.evaluate("FastMDXRun.currentState().study.agent") == "assisted"
            runs = []
            def intercepted_run(route):
                runs.append(route.request.post_data_json)
                route.fulfill(json={"ok": False, "error": "Test intercepted the human run; no simulation starts."})
            page.route("**/api/run", intercepted_run)
            page.locator("#run-output").fill(str(tmp_path / "reviewed-output"))
            page.locator("#run-start-button").click()
            page.locator("#draft-review-dialog").wait_for(state="visible")
            assert not runs
            page.locator("#draft-review-cancel").click()
            assert not runs
            page.locator("#run-start-button").click()
            page.locator("#draft-review-confirmed").check()
            page.locator("#draft-review-accept").click()
            page.wait_for_function("document.getElementById('run-note').textContent.includes('Test intercepted')")
            from types import SimpleNamespace

            from fastmdxplora.gui.draft_review import verify_run_review
            assert runs and verify_run_review(runs[0], SimpleNamespace(active_root=root)) is None
            # Preference persists and closes the sidebar without navigation.
            page.evaluate("document.getElementById('research-agent-enabled').checked=false; document.getElementById('research-agent-enabled').dispatchEvent(new Event('change'))")
            page.reload()
            assert page.locator("#research-agent-toggle").is_hidden()
            assert page.evaluate("FastMDXResearch.enabled()") is False
            browser.close()
    finally:
        server.shutdown()
        server.server_close()


def test_protein_residue_clicks_highlight_and_prepare_a_question(tmp_path, monkeypatch):
    playwright = pytest.importorskip("playwright.sync_api")
    from fastmdxplora.gui.server import start_test_server
    from tests.test_the_drawing_scripts_run_in_a_browser import _write_study

    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", str(tmp_path / "isolated-settings"))
    root = _write_study(tmp_path / "study")
    server, url = start_test_server(root)
    try:
        with playwright.sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.goto(url + "/#viewer")
            page.wait_for_function("FastMDXMoleculeViewer.STATE.model !== null")
            page.evaluate("""() => {
                const atom = FastMDXMoleculeViewer.STATE.model.selectedAtoms({resn:'ALA',atom:'CA'})[0];
                if (!atom.clickable || typeof atom.callback !== 'function') throw new Error('Residue is not clickable');
                atom.callback(atom);
            }""")
            view = page.evaluate("FastMDXResearch.capture()")
            assert view["selection"]["resname"] == "ALA"
            assert view["selection"]["atom"] == ""
            assert page.evaluate("FastMDXMoleculeViewer.STATE.focusResidue.resi") == 1
            page.locator("#research-pin-residue").click()
            assert page.evaluate("FastMDXResearch.capture().comparison_selection.resname") == "ALA"
            assert page.locator("#research-compare-residues").is_disabled()
            page.evaluate("""() => {
                const atom = FastMDXMoleculeViewer.STATE.model.selectedAtoms({resn:'ALA',resi:2,atom:'CA'})[0];
                atom.callback(atom);
            }""")
            comparison = page.evaluate("FastMDXResearch.capture()")
            assert comparison["selection"]["resseq"] == 2
            assert comparison["comparison_selection"]["resseq"] == 1
            assert page.locator("#research-compare-residues").is_enabled()
            page.locator("#research-compare-residues").click()
            assert "pinned comparison residue" in page.locator("#agent-request").input_value()
            assert not page.locator("#agent-thread").inner_text()
            page.locator("#research-agent-close").click()
            page.locator("#research-clear-comparison").click()
            assert "comparison_selection" not in page.evaluate("FastMDXResearch.capture()")
            page.evaluate("view => FastMDXResearch.restore(view)", comparison)
            assert page.evaluate("FastMDXResearch.capture().comparison_selection.resseq") == 1
            assert page.locator("#research-compare-residues").is_enabled()
            page.locator("#research-explain-residue").click()
            assert page.locator("#research-agent-dock").is_visible()
            assert "selected residue" in page.locator("#agent-request").input_value()
            # Selecting/preparing a question has not sent an inference request.
            assert not page.locator("#agent-thread").inner_text()
            page.locator("#research-agent-close").click()
            page.locator("#viewer-click-mode").select_option("atom")
            page.evaluate("""() => {
                const atom = FastMDXMoleculeViewer.STATE.model.selectedAtoms({resn:'ALA',atom:'CA'})[0];
                atom.callback(atom);
            }""")
            assert page.evaluate("FastMDXResearch.capture().selection.atom") == "CA"
            browser.close()
    finally:
        server.shutdown()
        server.server_close()


def test_graph_residue_selection_can_be_pinned_and_compared_without_inference(tmp_path, monkeypatch):
    playwright = pytest.importorskip("playwright.sync_api")
    import hashlib

    from fastmdxplora.gui.server import start_test_server
    from tests.test_an_analysis_is_drawn_from_its_numbers import _analysis
    from tests.test_the_drawing_scripts_run_in_a_browser import _write_study

    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", str(tmp_path / "isolated-settings"))
    root = _write_study(tmp_path / "study")
    _analysis(root, "rmsf", "A 1 0.10\nA 2 0.25\n")
    files_before = {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in root.rglob("*") if path.is_file()}
    server, url = start_test_server(root)
    try:
        with playwright.sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            sent = []
            page.on("request", lambda req: sent.append(req.url) if "/api/agent/propose" in req.url else None)
            page.goto(url + "/#analysis")
            page.wait_for_function("window.FastMDXResearch && window.FastMDXMoleculeViewer")
            chart = page.locator('.series-chart[data-analysis="rmsf"] svg')
            chart.wait_for()
            chart.focus()
            chart.press("Enter")
            page.wait_for_function("FastMDXMoleculeViewer.STATE.researchSelection?.resseq === 1")
            assert page.evaluate("FastMDXResearch.capture().selection") == {
                "chain": "A", "resseq": 1, "resname": "ALA", "atom": "", "icode": "", "altloc": ""}
            page.locator("#research-pin-residue").click()
            page.evaluate("FastMDXDashboard.navigate('analysis')")
            chart.focus()
            chart.press("ArrowRight")
            chart.press("Enter")
            page.wait_for_function("FastMDXMoleculeViewer.STATE.researchSelection?.resseq === 2")
            assert page.locator("#research-compare-residues").is_enabled()
            page.locator("#research-compare-residues").click()
            assert "pinned comparison residue" in page.locator("#agent-request").input_value()
            assert sent == [], "Selecting/comparing must wait for the user to send the question"
            # An unmatched graph point clears the prior selection rather than
            # offering the old residue as the answer to a new graph selection.
            page.evaluate("window.dispatchEvent(new CustomEvent('dashboard:residue-focus', {detail:{chain:'Z',resi:999}}))")
            assert page.evaluate("FastMDXMoleculeViewer.STATE.researchSelection") is None
            assert page.locator("#research-compare-residues").is_disabled()
            browser.close()
        assert files_before == {path: hashlib.sha256(Path(path).read_bytes()).hexdigest()
                                for path in files_before}
        added = {str(path.relative_to(root)).replace('\\', '/') for path in root.rglob("*")
                 if path.is_file() and str(path) not in files_before}
        assert added <= {"simulation/playback.pdb", "simulation/playback_index.json"}, "Only existing display caches may be generated"
    finally:
        server.shutdown()
        server.server_close()
