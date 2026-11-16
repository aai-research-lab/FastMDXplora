"""The GUI's Analysis page analyzes the study open again, after asking.

The GUI ran a study's phases only into a new, empty folder: a finished
study's analyses could be run again only from the command line. **Analyze
again** offers the analyses the study ran last, ticked, and every other one
this release has; says what is written again and what is kept aside; and
runs the phase command with `--rerun` on the study through the GUI's own
start rule, once its own button is pressed.
"""

from __future__ import annotations

import json
import shutil
import sys
import urllib.request
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_a_phase_command_is_explore_with_one_phase import _analysed, _campaign, _made  # noqa: E402

from fastmdxplora.gui.exploration import DashboardRuntime  # noqa: E402


def _runtime(root: Path) -> DashboardRuntime:
    runtime = DashboardRuntime(workspace_root=root.parent, exploration_root=root.parent)
    runtime.active_root = root
    return runtime


#: What the analysis of the four-residue test study runs by default.
_TINY_PROTEIN_DEFAULTS = [
    "rmsd", "rmsf", "rg", "hbonds", "ss", "sasa", "dihedrals",
    "end_to_end", "moments_of_inertia", "cluster", "dimred",
]


def _forget_analysis_selection(root: Path) -> None:
    """A study with no analyses on record: none asked for, no manifest."""
    import yaml

    record = root / "resolved_config.yml"
    if record.is_file():
        config = yaml.safe_load(record.read_text(encoding="utf-8")) or {}
        if isinstance(config.get("analysis"), dict):
            config["analysis"].pop("include", None)
        record.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    (root / "analysis" / "analysis_manifest.json").unlink(missing_ok=True)


def _again(root: Path, query: str = "") -> dict:
    from fastmdxplora.gui.server import start_dashboard_session

    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
    try:
        return json.loads(urllib.request.urlopen(
            session.url.rstrip("/") + "/api/again" + query, timeout=30).read())
    finally:
        session.server.shutdown()


def test_the_analyses_chosen_are_run_again_in_the_study_open(tmp_path):
    root = _analysed(tmp_path / "study")
    with mock.patch.object(DashboardRuntime, "_spawn",
                           return_value={"launched": True}) as spawn:
        answer = _runtime(root).run_again(["analysis"], ["rmsd", "rg"])
    command, where, _ = spawn.call_args.args
    assert answer["ok"] and "The report is written again too" in answer["said"]
    assert command == [sys.executable, "-m", "fastmdxplora", "explore",
                       "--output", str(root.resolve()), "--include-phase", "analysis", "report",
                       "--rerun", "--analyze-analyses", "rmsd", "rg"]
    assert where == root


def test_a_refusal_is_said_at_once_and_nothing_starts(tmp_path):
    root = _analysed(tmp_path / "study")
    with mock.patch.object(DashboardRuntime, "_spawn") as spawn:
        unknown = _runtime(root).run_again(["analysis"], ["rmsdd"])
        in_place = _runtime(root).run_again(["simulation"])
    spawn.assert_not_called()
    assert unknown["code"] == "analysis.unknown" and "did you mean 'rmsd'" in unknown["error"]
    assert in_place["code"] == "config.option.not_permitted"


@pytest.mark.parametrize("query", ["", "?defaults=1"])
def test_the_page_is_told_what_can_be_run_again(tmp_path, query):
    root = _analysed(tmp_path / "study", analyses=("rmsd", "rg"))
    offered = _again(root, query)
    assert offered["available"] and offered["analysis"]["can"]
    assert offered["recorded"] == ["rmsd", "rg"]
    # Those on record are ticked: nothing is planned in their place.
    assert "defaults" not in offered["analysis"]
    titles = {item["name"]: item["title"] for item in offered["catalogue"]}
    assert titles["rmsd"] == "Root-mean-square deviation" and "sasa" in titles
    assert offered["previous"] == "previous"


def test_a_study_with_none_on_record_is_offered_what_its_analysis_would_run(tmp_path):
    root = _analysed(tmp_path / "study", analyses=(), report=False)
    _forget_analysis_selection(root)
    shutil.rmtree(root / "analysis")
    offered = _again(root, "?defaults=1")
    assert offered["recorded"] == [] and offered["analysis"]["can"]
    assert offered["analysis"]["defaults"] == _TINY_PROTEIN_DEFAULTS
    assert not offered["analysis"].get("defaults_why")
    # Planned without writing: no analysis folder is made.
    assert not (root / "analysis").exists()


@pytest.mark.parametrize("query", ["", "?defaults=0"])
def test_what_is_ticked_is_planned_only_when_the_dialog_asks(tmp_path, query):
    from fastmdxplora.gui import again_view

    root = _analysed(tmp_path / "study", analyses=(), report=False)
    _forget_analysis_selection(root)
    with mock.patch.object(again_view, "automatic_analyses",
                           side_effect=AssertionError("not asked for")) as plan:
        offered = _again(root, query)
    plan.assert_not_called()
    assert offered["analysis"]["can"] and offered["recorded"] == []
    assert "defaults" not in offered["analysis"]
    assert "defaults_why" not in offered["analysis"]


@pytest.mark.parametrize("own_config", [False, True])
def test_a_study_of_several_runs_is_offered_what_its_first_run_would_run(tmp_path, own_config):
    root = _campaign(tmp_path)
    for run in (root / "runs").iterdir():
        _forget_analysis_selection(run)
    if not own_config:
        (root / "resolved_config.yml").unlink()
    for study in (root / "runs" / "a", root):
        offered = _again(study, "?defaults=1")
        assert offered["recorded"] == [] and offered["analysis"]["can"]
        assert offered["analysis"]["defaults"] == _TINY_PROTEIN_DEFAULTS
        assert not offered["analysis"].get("defaults_why")


def test_what_is_ticked_is_planned_on_one_frame_as_the_whole_would_be(tmp_path, monkeypatch):
    from fastmdxplora.analysis import AnalysisOrchestrator, loading, orchestrator
    from fastmdxplora.gui.again_view import automatic_analyses

    root = _analysed(tmp_path / "study", analyses=(), report=False, stride=2, first=3, last=9)
    _forget_analysis_selection(root)
    whole = AnalysisOrchestrator(
        trajectory=root / "simulation" / "production.dcd",
        topology=root / "simulation" / "topology.pdb", output_dir=root / "analysis",
        stride=2, first=3, last=9)
    assert whole.traj.n_frames == 6
    expected = list(whole.planned_analyses())
    load = orchestrator.load_trajectory
    frames_read = []

    def read(*args, **kwargs):
        trajectory = load(*args, **kwargs)
        frames_read.append(trajectory.n_frames)
        return trajectory

    monkeypatch.setattr(orchestrator, "load_trajectory", read)
    with mock.patch.object(loading.md, "load", side_effect=AssertionError("all frames read")):
        assert automatic_analyses(root) == expected
    assert frames_read == [1]


def test_a_trajectory_that_cannot_be_planned_from_has_nothing_ticked(tmp_path):
    root = _analysed(tmp_path / "study", analyses=(), report=False)
    _forget_analysis_selection(root)
    (root / "simulation" / "production.dcd").write_bytes(b"not a trajectory")
    offered = _again(root, "?defaults=1")
    assert offered["recorded"] == [] and offered["analysis"]["can"]
    assert offered["analysis"]["defaults"] is None
    assert "MDTraj failed to load trajectory" in offered["analysis"]["defaults_why"]


def test_a_study_of_several_plans_from_its_first_run_with_frames(tmp_path):
    from fastmdxplora import again

    root = _campaign(tmp_path)
    for run in (root / "runs").iterdir():
        _forget_analysis_selection(run)
    # Its first run has no trajectory: the analysis leaves it out and
    # analyses run b, so what is ticked is planned from b.
    (root / "runs" / "a" / "simulation" / "production.dcd").unlink()
    offered = _again(root, "?defaults=1")
    assert offered["analysis"]["can"]
    assert offered["analysis"]["defaults"] == _TINY_PROTEIN_DEFAULTS
    assert not offered["analysis"].get("defaults_why")
    # What the study ran last is still read from its first run.
    assert again.first_run(root) == root / "runs" / "a"


def test_the_planning_failure_names_the_file_from_the_study(tmp_path):
    root = _analysed(tmp_path / "study", analyses=(), report=False)
    _forget_analysis_selection(root)
    (root / "simulation" / "production.dcd").write_bytes(b"not a trajectory")
    why = _again(root, "?defaults=1")["analysis"]["defaults_why"]
    assert "Could not open file: simulation/production.dcd" in why, why
    assert str(root.resolve()) not in why and str(root) not in why


def test_a_damaged_analysis_manifest_is_said_and_nothing_is_ticked(tmp_path):
    root = _analysed(tmp_path / "study", analyses=(), report=False)
    _forget_analysis_selection(root)
    (root / "analysis" / "analysis_manifest.json").write_text("{not json", encoding="utf-8")
    offered = _again(root, "?defaults=1")
    assert offered["recorded"] == [] and offered["analysis"]["can"]
    assert offered["recorded_said"] == (
        "The analysis manifest could not be read (it is not a valid manifest), and the "
        "study's Config names no analyses, so none is ticked.")
    assert "defaults" not in offered["analysis"]


@pytest.mark.parametrize("route,method,body", [
    ("/api/again", "run_again", {"phases": ["analysis"], "analyses": ["rmsd"]}),
    ("/api/report/write", "write_the_report_again", {}),
])
def test_starting_a_run_again_plans_nothing_to_tick(tmp_path, route, method, body):
    from fastmdxplora.gui import again_view
    from fastmdxplora.gui.server import start_dashboard_session

    root = _analysed(tmp_path / "study", analyses=(), report=False)
    _forget_analysis_selection(root)
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
    base = session.url.rstrip("/")
    try:
        with mock.patch.object(again_view, "automatic_analyses",
                               side_effect=AssertionError("only the dialog asks")) as plan, \
                mock.patch.object(DashboardRuntime, method, return_value={"ok": True}) as run:
            request = urllib.request.Request(
                base + route, data=json.dumps(body).encode(), method="POST",
                headers={"Content-Type": "application/json", "Origin": base})
            answer = json.loads(urllib.request.urlopen(request, timeout=30).read())
    finally:
        session.server.shutdown()
    assert answer["ok"]
    run.assert_called_once()
    plan.assert_not_called()


@pytest.mark.parametrize("relative", ["simulation/production.dcd", "simulation/topology.pdb"])
def test_a_study_with_a_link_out_plans_nothing_to_tick(tmp_path, relative):
    from fastmdxplora.gui import again_view
    from tests.test_the_study_pages_read_only_the_study import _link

    root = _analysed(tmp_path / "study", analyses=(), report=False)
    _forget_analysis_selection(root)
    outside = tmp_path / Path(relative).name
    (root / relative).rename(outside)
    _link(root / relative, outside)
    with mock.patch.object(again_view, "automatic_analyses",
                           side_effect=AssertionError("the study has a link out")) as plan:
        offered = _again(root, "?defaults=1")
    plan.assert_not_called()
    assert offered["refused"] and relative in offered["links_out"]
    assert not offered["analysis"]["can"]


def test_the_dialog_ticks_what_the_analysis_would_run_and_takes_what_is_added(
    tmp_path,
) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    root = _analysed(tmp_path / "study", analyses=(), report=False)
    _forget_analysis_selection(root)
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
    submissions: list[dict[str, object]] = []
    errors: list[str] = []

    def submit(route):
        if route.request.method == "POST":
            submissions.append(json.loads(route.request.post_data or "{}"))
            route.abort()
        else:
            route.continue_()

    checked = "boxes => boxes.map(b => b.value)"
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1400, "height": 1000})
            page.set_default_timeout(60000)
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("**/api/again", submit)
            page.goto(session.url + "#analysis", wait_until="domcontentloaded")
            page.wait_for_selector("#analysis-again:not([hidden])")
            page.click("#analysis-again")
            page.wait_for_selector("#analysis-again-ask .again-choices")
            ticked = page.eval_on_selector_all("#analysis-again-ask input:checked", checked)
            page.check("#analysis-again-ask input[value='pmf']")
            added = page.eval_on_selector_all("#analysis-again-ask input:checked", checked)
            boxes = page.locator("#analysis-again-ask input:checked")
            while boxes.count():
                boxes.first.uncheck()
            confirm = page.locator("#analysis-again-ask .fix-confirm")
            none_ticked = confirm.is_disabled()
            page.check("#analysis-again-ask input[value='pmf']")
            page.click("#analysis-again-ask .fix-confirm")
            page.wait_for_selector(
                "#analysis-again-ask:has-text('Could not reach the server to start it.')")
            browser.close()
    finally:
        session.server.shutdown()
    assert ticked == _TINY_PROTEIN_DEFAULTS
    assert set(added) == {*_TINY_PROTEIN_DEFAULTS, "pmf"}
    assert none_ticked
    assert submissions == [{"phases": ["analysis"], "analyses": ["pmf"]}]
    assert not (root / "analysis" / "analysis_manifest.json").exists()
    assert errors == []


def test_the_dialog_ticks_nothing_where_nothing_could_be_planned(tmp_path) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    root = _analysed(tmp_path / "study", analyses=(), report=False)
    _forget_analysis_selection(root)
    (root / "simulation" / "production.dcd").write_bytes(b"not a trajectory")
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
    errors: list[str] = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1400, "height": 1000})
            page.set_default_timeout(60000)
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url + "#analysis", wait_until="domcontentloaded")
            page.wait_for_selector("#analysis-again:not([hidden])")
            page.click("#analysis-again")
            page.wait_for_selector("#analysis-again-ask .again-choices")
            ticked = page.eval_on_selector_all(
                "#analysis-again-ask input:checked", "boxes => boxes.map(b => b.value)")
            said = page.text_content("#analysis-again-ask .again-none")
            disabled = page.locator("#analysis-again-ask .fix-confirm").is_disabled()
            browser.close()
    finally:
        session.server.shutdown()
    assert ticked == [] and disabled
    assert said.startswith("The analyses that apply to this study could not be planned")
    assert "MDTraj failed to load trajectory" in said
    assert errors == []


def test_the_analysis_page_analyses_again_after_asking(tmp_path) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    root = _analysed(tmp_path / "study")
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
    asked: list[str] = []
    errors: list[str] = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1400, "height": 1000})
            page.set_default_timeout(60000)
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("request", lambda r: asked.append(r.method) if r.url.endswith("/api/again")
                    else None)
            page.goto(session.url + "#analysis", wait_until="domcontentloaded")
            page.wait_for_selector("#analysis-again:not([hidden])")
            page.click("#analysis-again")
            page.wait_for_selector("#analysis-again-ask .again-choices")
            ticked = page.eval_on_selector_all(
                "#analysis-again-ask input:checked", "boxes => boxes.map(b => b.value)")
            page.click("#analysis-again-ask .again-more summary")
            page.check("#analysis-again-ask input[value='rg']")
            said = page.text_content("#analysis-again-ask .again-said")
            assert "POST" not in asked          # nothing yet: asked first
            page.click("#analysis-again-ask .fix-confirm")
            page.wait_for_selector("#analysis-again-ask:has-text('Analyzed again.')",
                                   timeout=180000)
            browser.close()
    finally:
        session.server.shutdown()
    assert ticked == ["rmsd"]
    assert "with the 2 analyses ticked" in said and "Nothing is simulated." in said
    assert "saved in previous/" in said and "The report is written again too" in said
    assert asked.count("POST") == 1
    assert _made(root) == ["rmsd", "rg"] and _made(root / "previous") == ["rmsd"]
    assert errors == []


def test_the_panel_says_an_unfinished_analysis_is_removed(tmp_path) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora import again
    from fastmdxplora.gui.server import start_dashboard_session

    root = _analysed(tmp_path / "study")
    # Stopped part-way: its analyses written, not its manifest.
    (root / "analysis" / "analysis_manifest.json").unlink()
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1400, "height": 1000})
            page.set_default_timeout(60000)
            page.goto(session.url + "#analysis", wait_until="domcontentloaded")
            page.wait_for_selector("#analysis-again:not([hidden])")
            page.click("#analysis-again")
            page.wait_for_selector("#analysis-again-ask .again-choices")
            said = page.text_content("#analysis-again-ask .again-said")
            browser.close()
    finally:
        session.server.shutdown()
    # It said "The analysis/ and report/ there now are kept in previous/, in
    # place of what was kept there before": the unfinished analysis put over
    # the one saved there, where it is removed and the saved one stays.
    assert "kept" not in said and "did not finish is removed" in said, said
    # One wording with what `fastmdx mcp` and the Agent are told.
    rule = again.saved_said(("analysis", "report"))
    assert rule in said and rule in again.plan(root, ["analysis"]).said()


def test_a_state_sent_before_the_run_began_is_not_its_end(tmp_path) -> None:
    """The page polls the server's state. One answered before the run began
    says nothing runs, which was read as the run having ended: the page said
    "Analyzed again." with nothing analyzed. Only the end of the run started
    here is said, and a failed end as a failure."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    root = _analysed(tmp_path / "study")
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
    began = "2026-10-06T13:15:02+00:00"
    errors: list[str] = []

    def state(**more):
        return {"active_run": str(root), "process_running": False, **more}

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1400, "height": 1000})
            page.set_default_timeout(60000)
            page.on("pageerror", lambda error: errors.append(str(error)))
            # Started, as far as the page is told; nothing is run.
            page.route("**/api/again", lambda route: route.fulfill(
                json={"ok": True, "said": "", "state": {"started_at": began}})
                if route.request.method == "POST" else route.continue_())
            page.goto(session.url + "#analysis", wait_until="domcontentloaded")
            page.wait_for_selector("#analysis-again:not([hidden])")
            page.click("#analysis-again")
            page.click("#analysis-again-ask .fix-confirm")
            page.wait_for_selector("#analysis-again-ask:has-text('Analyzing again.')")

            def sent(detail):
                page.evaluate("d => window.dispatchEvent(new CustomEvent("
                              "'dashboard:app-state', {detail: d}))", detail)
                return page.text_content("#analysis-again-ask .again-said")

            before = sent(state(started_at=None, returncode=None))
            earlier = sent(state(started_at="2026-10-06T12:00:00+00:00", returncode=0))
            going = sent(state(started_at=began, returncode=None, process_running=True))
            ended = sent(state(started_at=began, returncode=0))
            browser.close()
    finally:
        session.server.shutdown()
    assert before.startswith("Analyzing again.") and earlier.startswith("Analyzing again.")
    assert going.startswith("Analyzing again.")
    assert ended.startswith("Analyzed again.")
    assert errors == []


def test_a_run_again_that_fails_is_said_to_have_failed(tmp_path) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    root = _analysed(tmp_path / "study")
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
    began = "2026-10-06T13:15:02+00:00"
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1400, "height": 1000})
            page.set_default_timeout(60000)
            page.route("**/api/again", lambda route: route.fulfill(
                json={"ok": True, "said": "", "state": {"started_at": began}})
                if route.request.method == "POST" else route.continue_())
            page.goto(session.url + "#analysis", wait_until="domcontentloaded")
            page.wait_for_selector("#analysis-again:not([hidden])")
            page.click("#analysis-again")
            page.click("#analysis-again-ask .fix-confirm")
            page.wait_for_selector("#analysis-again-ask:has-text('Analyzing again.')")
            page.evaluate("d => window.dispatchEvent(new CustomEvent('dashboard:app-state', "
                          "{detail: d}))", {"active_run": str(root), "process_running": False,
                                            "started_at": began, "returncode": 1,
                                            "error": "The workflow exited with code 1."})
            said = page.text_content("#analysis-again-ask .again-said")
            browser.close()
    finally:
        session.server.shutdown()
    assert said == "The workflow exited with code 1."
