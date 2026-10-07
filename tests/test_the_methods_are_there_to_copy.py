"""The methods paragraphs are on the Overview, to read and to copy.

They were written only into the report, which is made last and not made
again when a study is extended or its analyses run again; the paragraph a
person pastes into a manuscript was a file away and could be stale. The GUI
now writes them from the records as they stand and copies them without the
Markdown, since a manuscript is not Markdown.
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

import pytest

from fastmdxplora.gui.report_page import methods_payload


def _study(root: Path) -> Path:
    (root / "simulation").mkdir(parents=True)
    (root / "simulation" / "simulation_parameters.json").write_text(json.dumps({
        "parameters": {"production_steps": 500_000, "timestep_fs": 2.0,
                       "integrator": "langevin_middle", "temperature_K": 300.0}}),
        encoding="utf-8")
    (root / "manifest.json").write_text(json.dumps({"phases": []}), encoding="utf-8")
    where = root / "analysis" / "rmsd"
    where.mkdir(parents=True)
    (where / "options.json").write_text(json.dumps({"findings": {"mean": {
        "mean": 0.112, "standard_error": 0.002, "effective_samples": 40.0, "discard": 5,
        "n_frames": 200, "degrees_of_freedom": 12.0, "unit": "nm"}}}), encoding="utf-8")
    return root


def test_the_paragraphs_are_given_for_the_page_and_for_a_manuscript(tmp_path):
    said = methods_payload(_study(tmp_path))
    assert said["ok"]
    assert "<strong>Simulation protocol.</strong>" in said["html"]
    assert "<strong>Analysis.</strong>" in said["html"]
    assert "Production dynamics were run for 1 ns" in said["plain"]
    assert "Analysis. The per-frame quantity rmsd was averaged" in said["plain"]
    assert "**" not in said["plain"] and "`" not in said["plain"]


def test_nothing_run_and_a_study_of_runs_say_why_there_is_none(tmp_path):
    assert methods_payload(tmp_path) == {"ok": False, "reason": "nothing has run yet"}
    (tmp_path / "batch_manifest.json").write_text("{}", encoding="utf-8")
    assert "each run's report gives its own methods" in methods_payload(tmp_path)["reason"]


def test_it_is_served_and_answered_beyond_loopback(tmp_path):
    from fastmdxplora.gui.server import GETS_ANSWERED_BEYOND_LOOPBACK, start_dashboard_session

    assert "/api/methods" in GETS_ANSWERED_BEYOND_LOOPBACK
    session = start_dashboard_session(output=str(_study(tmp_path)), host="127.0.0.1", port=0)
    try:
        said = json.loads(urllib.request.urlopen(session.url + "/api/methods",
                                                 timeout=10).read())
    finally:
        session.server.shutdown()
    assert said["ok"] and "Analysis." in said["plain"]


def test_the_overview_shows_them_and_copies_them_plain(tmp_path):
    pytest.importorskip("playwright.sync_api")
    pytest.importorskip("mdtraj")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session
    from tests.test_an_analysis_is_drawn_from_its_numbers import _manifest
    from tests.test_the_drawing_scripts_run_in_a_browser import _write_study

    root = _write_study(tmp_path / "study")
    _manifest(root, saving_interval_ps=2.0)
    _study_records = _study(tmp_path / "records")
    for name in ("simulation/simulation_parameters.json", "manifest.json",
                 "analysis/rmsd/options.json"):
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text((_study_records / name).read_text(), encoding="utf-8")
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            context = browser.new_context(viewport={"width": 1400, "height": 1000})
            context.grant_permissions(["clipboard-read", "clipboard-write"],
                                      origin=session.url)
            page = context.new_page()
            page.set_default_timeout(60000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url + "#overview", wait_until="domcontentloaded")
            # Folded under the rest of the Overview; opened, they are there.
            page.wait_for_selector("#overview-methods-card:not([hidden]) strong", state="attached")
            page.click("#overview-methods-card summary .card-title")
            page.wait_for_selector("#overview-methods-card[open] strong")
            shown = page.text_content("#overview-methods-text")
            requests: list[str] = []
            page.on("request", lambda request: requests.append(request.url))
            page.click("#overview-methods-copy")
            # An icon: named so for a moment.
            page.wait_for_function(
                "document.getElementById('overview-methods-copy').getAttribute('aria-label') === 'Copied'")
            copied = page.evaluate("navigator.clipboard.readText()")
            # Results arriving again with nothing the methods rest on changed
            # do not ask for them again.
            with page.expect_request(lambda request: "/api/results" in request.url,
                                     timeout=20000):
                (root / "notes.txt").write_text("a note\n", encoding="utf-8")
            page.wait_for_timeout(1000)
            browser.close()
    finally:
        session.server.shutdown()
    assert "Analysis. The per-frame quantity rmsd was averaged" in shown
    assert copied.startswith("Simulation protocol.") or "Simulation protocol." in copied
    assert "**" not in copied and "`" not in copied
    assert "Production dynamics were run for 1 ns" in copied
    assert not [url for url in requests if url.endswith("/api/methods")]
    assert errors == []


def _prepared_elsewhere(tmp_path: Path) -> tuple[Path, Path]:
    """A study in a workspace whose system was prepared in a folder outside it."""
    import yaml

    outside = tmp_path / "outside" / "setup"
    outside.mkdir(parents=True)
    (outside / "setup_parameters.json").write_text(json.dumps({
        "parameters": {"forcefield": "amber14", "water_model": "tip3p"}}), encoding="utf-8")
    workspace = tmp_path / "work"
    study = _study(workspace / "study")
    (study / "resolved_config.yml").write_text(yaml.safe_dump({
        "simulation": {"setup_from": str(outside)}}), encoding="utf-8")
    return workspace, study


class TestTheAgentAndAnAIAppQuoteThem:
    """Asked for a methods section, an AI model wrote one from what is usual.
    It now looks, and quotes what the study recorded."""

    def test_the_agent_looks_them_up(self, tmp_path):
        from fastmdxplora.agent.tools import Toolbox

        look = Toolbox().use("methods_of_study", {"study": str(_study(tmp_path / "s"))})
        assert look.ok and "**Analysis.** The per-frame quantity `rmsd`" in look.said
        assert "quote them as they are" in look.said
        assert "methods_of_study" in Toolbox().describe()

    def test_the_agent_is_held_to_the_workspace(self, tmp_path):
        from fastmdxplora.agent.tools import Toolbox

        refused = Toolbox(path_for=lambda given: None).use(
            "methods_of_study", {"study": str(_study(tmp_path / "s"))})
        assert not refused.ok and "outside the workspace" in refused.said
        workspace, study = _prepared_elsewhere(tmp_path)

        def inside(given):
            path = Path(given).resolve()
            return str(path) if workspace.resolve() in (path, *path.parents) else None

        refused = Toolbox(path_for=inside).use("methods_of_study", {"study": str(study)})
        assert not refused.ok and "outside what may be read here" in refused.said
        assert "amber14" not in refused.said
        # Not held, as on a person's own machine, it is read.
        assert "amber14" in Toolbox().use("methods_of_study", {"study": str(study)}).said

    def test_an_ai_app_reads_them_within_its_workspace(self, tmp_path):
        from fastmdxplora.mcp.tools import TOOLS, Context, ToolError
        from fastmdxplora.mcp.workspace import Workspace

        workspace, study = _prepared_elsewhere(tmp_path)
        _study(workspace / "own")
        (workspace / "campaign").mkdir()
        (workspace / "campaign" / "batch_manifest.json").write_text("{}", encoding="utf-8")
        tool = next(t for t in TOOLS if t.name == "methods_of_study")
        assert tool.annotations["readOnlyHint"] is True and not tool.acts
        ctx = Context(Workspace.at(workspace))
        said = tool.run(ctx, {"study": "own"})
        assert said.startswith("The methods of own, as its report gives them:")
        assert "**Analysis.**" in said
        for given, why in (("study", "outside what may be read here"),
                           ("campaign", "Name one of its runs")):
            with pytest.raises(ToolError) as refused:
                tool.run(ctx, {"study": given})
            assert why in str(refused.value) and "amber14" not in str(refused.value)

    def test_a_hosted_page_is_held_too(self, tmp_path):
        workspace, study = _prepared_elsewhere(tmp_path)
        said = methods_payload(study, may_read=lambda path: workspace.resolve() in (
            Path(path).resolve(), *Path(path).resolve().parents))
        assert not said["ok"] and "outside what may be read here" in said["reason"]


def test_the_agent_is_told_what_it_named_wrongly(tmp_path):
    from fastmdxplora.agent.tools import Toolbox

    campaign = tmp_path / "campaign"
    (campaign / "simulation").mkdir(parents=True)
    (campaign / "batch_manifest.json").write_text("{}", encoding="utf-8")
    for asked, why in (({}, "Name the study"),
                       ({"study": str(tmp_path / "nothing")}, "is not a study folder"),
                       ({"study": str(campaign)}, "Name one of its runs")):
        look = Toolbox().use("methods_of_study", asked)
        assert not look.ok and why in look.said


def test_a_campaign_s_rule_outside_what_may_be_read_is_not_read(tmp_path):
    from fastmdxplora.report.document import _stopping_record_of

    (tmp_path / "stopping.json").write_text(json.dumps({"runs": ["r0"], "rounds": []}),
                                            encoding="utf-8")
    run = tmp_path / "runs" / "r0"
    run.mkdir(parents=True)
    assert _stopping_record_of(run)["runs"] == ["r0"]
    assert _stopping_record_of(run, lambda path: False) is None


def test_a_rule_that_does_not_read_says_nothing(tmp_path):
    from fastmdxplora.report.methods import _stopping_sentences

    assert _stopping_sentences({"targets": [{"no_such": 1}], "rounds": [{}]}, {}) == []
    assert _stopping_sentences({"targets": [], "rounds": [{}]}, {}) == []
