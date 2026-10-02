"""Human control, installed knowledge and evidence-based dashboard explanations."""
from __future__ import annotations

import json
from importlib.resources import files

import pytest

from fastmdxplora.agent.knowledge import dashboard_knowledge, error_reference
from fastmdxplora.gui.agent_panel import propose_endpoint, run_endpoint
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


def test_a_suggestion_waits_for_add_to_draft_and_sidebar_can_be_disabled(tmp_path, monkeypatch, model):
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
            page.goto(url + "/#agent")
            page.locator("#agent-request").fill("Suggest a study at pH 6.5")
            loads = []
            page.on("request", lambda req: loads.append(req.url) if req.url.endswith("/api/load-config") else None)
            page.locator("#agent-propose").click()
            page.locator("[data-role=load]").wait_for()
            assert not loads, "A reply must not overwrite the builder's draft"
            assert not page.locator("[data-role=run]").is_visible()
            page.locator("[data-role=load]").click()
            page.wait_for_function("FastMDXDashboard.state.activePage === 'run'")
            assert loads
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
