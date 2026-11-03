"""The study pages say what the study's records say (the weekly tester's
area 1, 10-13, items still open on `main` `43dfacaa`).

Each item is what a page said against what the records hold:
1. a stopped run's frames had no time where its record was never resolved;
4. Analyze again on a study never analysed ticked every analysis, those its
   Config leaves nothing for (a membrane's, a pull's) too;
7. at a phone's width a page opened at the last page's scroll;
8. "Coloured by RMSF" named every frame analysed, not the frames RMSF used;
12. the Viewer's States named another medoid than the analysis recorded;
14. Files said "100 frames over 0.01 ns" for 10 ps;
15. Files' times ignored the 24 or 12 hours Settings says;
17. a stopped run's step was the whole run's, said as production's;
18. a failed analysis's row read "instantiation failed:" with no reason;
19. the Viewer's XTC frames were each at their index in ps;
20. the Viewer's Information named a preset as the force field;
23. end-to-end distance was "end_to_end" over time.
"""

from __future__ import annotations

import json

import numpy as np
import pytest


# --------------------------------------------------------------------------
# Read from the records
# --------------------------------------------------------------------------


def test_a_series_not_listed_is_named_as_its_analysis_page_names_it(tmp_path):
    from fastmdxplora.gui.series import series_over_time

    folder = tmp_path / "analysis" / "end_to_end"
    folder.mkdir(parents=True)
    (folder / "end_to_end.dat").write_text("frame,end_to_end_nm\n0,1.0\n1,1.2\n",
                                           encoding="utf-8")
    (folder / "options.json").write_text(json.dumps(
        {"findings": {"mean": {"value": 1.1, "unit": "nm"}}}), encoding="utf-8")
    found = series_over_time(tmp_path)["series"]
    assert [(s["analysis"], s["label"]) for s in found] == [("end_to_end", "End-to-end distance")]


def test_files_says_a_trajectory_under_one_ns_in_ps(tmp_path):
    span, said = 0.01, "100 frames over 10 ps"
    from fastmdxplora.gui.files_page import _tiles_of

    (tmp_path / "simulation").mkdir()
    (tmp_path / "simulation" / "production.dcd").write_bytes(b"")
    (tmp_path / "simulation" / "simulation_parameters.json").write_text(json.dumps(
        {"n_production_frames": 100, "duration_ns_actual": span}), encoding="utf-8")
    by_path = {"simulation/production.dcd": {"path": "simulation/production.dcd",
                                             "opens_with": None}}
    words = json.dumps(_tiles_of(tmp_path, "", "", by_path))
    assert said in words, words
    assert "over 0.01 ns" not in words


def test_a_stopped_run_is_said_at_its_stage_s_own_step(tmp_path):
    from fastmdxplora.gui.study_timeline import timeline_of
    from tests.test_a_run_that_ended_unsaid_is_interrupted import _a_run_said_it_was_going

    root = tmp_path / "study"
    root.mkdir()
    _a_run_said_it_was_going(root, last="2026-10-18T07:40:00+00:00", step=16000)
    status = root / "simulation" / "live_status.json"
    record = json.loads(status.read_text(encoding="utf-8"))
    record.update({"nvt_steps_planned": 5000, "npt_steps_planned": 5000,
                   "production_steps_planned": 40000})
    status.write_text(json.dumps(record), encoding="utf-8")
    said = [e["said"] for e in timeline_of(root)["events"]]
    assert said == ["Stopped without finishing, in production, at step 6,000 of 40,000"], said


def test_rmsf_by_residue_names_the_frames_rmsf_used(tmp_path):
    from fastmdxplora.gui.by_residue import _rmsf

    (tmp_path / "rmsf.dat").write_text("residue,rmsf_nm\n1,0.1\n2,0.2\n3,0.15\n", encoding="utf-8")
    record = {"findings": {"frames": {"n_frames_analysed": 62, "n_frames_given": 100}}}
    said = _rmsf(tmp_path, record, 100)
    assert said and "over the 62 frames analysed" in said["about"], said


def test_a_stopped_run_s_frames_take_the_interval_it_ran_with(tmp_path):
    from fastmdxplora.gui.trajectory_frames import _interval_ns

    (tmp_path / "simulation").mkdir()
    (tmp_path / "simulation" / "simulation_parameters.json").write_text(json.dumps({
        "resolved": {}, "parameters": {"trajectory_interval_steps": 500, "timestep_fs": 2.0,
                                       "integrator": "langevin_middle"}}), encoding="utf-8")
    source = {"trajectory": tmp_path / "simulation" / "production.dcd"}
    assert _interval_ns(tmp_path, source) == pytest.approx(0.001)


def test_xtc_frames_carry_their_times(tmp_path):
    md = pytest.importorskip("mdtraj")
    from fastmdxplora.gui.trajectory_frames import _write_xtc

    top = md.Topology()
    chain = top.add_chain()
    residue = top.add_residue("GLY", chain)
    for name in ("N", "CA", "C"):
        top.add_atom(name, md.element.carbon, residue)
    frames = md.Trajectory(np.random.default_rng(1).random((4, 3, 3)).astype(np.float32), top)
    target = tmp_path / "frames.xtc"
    _write_xtc(frames, target, [0.002, 0.004, 0.006, 0.008])
    times = md.load_xtc(str(target), top=top).time
    assert np.allclose(times, [2.0, 4.0, 6.0, 8.0], atol=1e-3), times


def test_information_names_the_force_field_s_files_and_the_preset(tmp_path):
    from fastmdxplora.gui.server import _setup_details

    said = _setup_details({"parameters": {}, "resolved_forcefield": {
        "source": "named", "name": "amber-openff",
        "xmls": ["amber14-all.xml", "amber14/tip3p.xml"], "water_model": "tip3p"}})
    assert said["force_field"] == "amber14-all.xml, amber14/tip3p.xml (amber-openff)"


def test_analyze_again_offers_unticked_what_the_config_leaves_nothing_for(tmp_path):
    from fastmdxplora.gui.again_view import _not_applicable

    (tmp_path / "resolved_config.yml").write_text(
        "systems:\n  - {id: a, system: 1UBQ}\nsimulation:\n  save_selection: not water\n",
        encoding="utf-8")
    none = _not_applicable(tmp_path)
    for name in ("pmf", "steered_work", "area_per_lipid", "water_sites"):
        assert name in none, none
    assert "rmsd" not in none and "rg" not in none


# --------------------------------------------------------------------------
# The states a study visited: the analysis's own medoid
# --------------------------------------------------------------------------


def test_a_state_is_shown_by_the_medoid_the_analysis_recorded(tmp_path):
    pytest.importorskip("mdtraj")
    from fastmdxplora.gui.states import states_of
    from tests.test_the_states_a_study_visited import _two_states

    study = _two_states(tmp_path / "study")
    folder = study / "analysis" / "cluster"
    table = folder / "cluster_kmeans_populations.csv"
    import pandas as pd

    populations = pd.read_csv(table)
    # A medoid recorded at another frame of each state than the one found
    # again among the frames played: the recorded one is shown.
    before = {s["state"]: s["representative"] for s in states_of(study)["states"]}
    wanted = {}
    for i, row in populations.iterrows():
        label = int(row["cluster"])
        shown = before[label]
        half = shown < 15
        other = shown + 2 if (shown + 2 < 15) == half and shown + 2 < 30 else shown - 2
        wanted[label] = other
        populations.loc[i, "medoid_frame"] = other
    populations.to_csv(table, index=False)
    after = {s["state"]: s["representative"] for s in states_of(study)["states"]}
    assert after == wanted, (before, after)


# --------------------------------------------------------------------------
# In a browser
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def browser():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        launched = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
        yield launched
        launched.close()


@pytest.fixture
def session(tmp_path):
    from fastmdxplora.gui.server import start_dashboard_session
    from tests.test_the_workspace_says_its_studies import _study

    study = _study(tmp_path / "ubiquitin", system="1UBQ")
    started = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    yield started
    started.server.shutdown()


def _open(browser, url, where, width=1440, height=900):
    page = browser.new_context(viewport={"width": width, "height": height}).new_page()
    page.set_default_timeout(60000)
    page.errors = []
    page.on("pageerror", lambda error: page.errors.append(str(error)))
    page.goto(url + where, wait_until="domcontentloaded")
    page.wait_for_function("() => window.FastMDXDashboard")
    page.wait_for_selector("body:not(.state-loading)")
    return page


def test_at_a_phone_s_width_a_page_opens_at_its_top(browser, session):
    page = _open(browser, session.url, "#settings", width=390, height=700)
    # What scrolls at this width: the body, as the page lays it out.
    scroller = """() => [document.scrollingElement, document.body]
        .find((e) => e.scrollHeight > e.clientHeight + 5)"""
    page.evaluate(f"() => {{ const e = ({scroller})(); e.scrollTop = e.scrollHeight; }}")
    page.wait_for_timeout(200)
    scrolled = page.evaluate(f"() => ({scroller})().scrollTop")
    # To a page as long, so the scroll is not taken back by a shorter one.
    page.evaluate("() => window.FastMDXDashboard.navigate('run')")
    page.wait_for_timeout(600)
    after = page.evaluate("""() => Math.max(document.scrollingElement.scrollTop,
                                              document.body.scrollTop)""")
    errors = page.errors
    page.context.close()
    assert errors == []
    assert scrolled > 200 and after == 0, (scrolled, after)


def test_files_says_its_times_in_the_hours_settings_says(browser, session):
    page = _open(browser, session.url, "#files")
    page.wait_for_selector("#files-page time[data-when]", state="attached")
    page.evaluate("() => window.FastMDXDashboard.navigate('settings')")
    page.select_option("#setting-time-format", "12h")
    page.evaluate("() => window.FastMDXDashboard.navigate('files')")
    twelve = page.evaluate("""() => [...document.querySelectorAll('#files-page time[data-when]')]
        .map((t) => t.textContent)""")
    page.evaluate("() => window.FastMDXDashboard.navigate('settings')")
    page.select_option("#setting-time-format", "24h")
    twenty_four = page.evaluate("""() => [...document.querySelectorAll('#files-page time[data-when]')]
        .map((t) => t.textContent)""")
    errors = page.errors
    page.context.close()
    assert errors == [] and twelve
    assert all(" AM" in t or " PM" in t for t in twelve), twelve
    assert not any(" AM" in t or " PM" in t for t in twenty_four), twenty_four


def test_settings_marks_the_words_found(browser, session):
    page = _open(browser, session.url, "#settings")
    page.fill("#settings-find", "water")
    marked = page.evaluate("""() => {
        const found = CSS.highlights && CSS.highlights.get('settings-found');
        return found ? [...found].map((r) => r.toString()) : null; }""")
    page.fill("#settings-find", "")
    cleared = page.evaluate("() => CSS.highlights.has('settings-found')")
    errors = page.errors
    page.context.close()
    assert errors == []
    assert marked and all(word.lower() == "water" for word in marked), marked
    assert not cleared


def test_a_failed_analysis_row_says_its_reason(browser, session):
    page = browser.new_context(viewport={"width": 1440, "height": 900}).new_page()
    page.set_default_timeout(60000)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))

    def failed(route):
        answer = route.fetch()
        said = answer.json()
        said["rows"] = [{"analysis": "rmsd", "status": "failed", "quantities": [],
                         "message": "instantiation failed: the selection matched no atoms. "
                                    "Name another selection."}]
        route.fulfill(response=answer, json=said)

    page.route("**/api/analysis-overview*", failed)
    page.goto(session.url + "#analysis", wait_until="domcontentloaded")
    page.wait_for_selector('tr[data-analysis="rmsd"][data-status="failed"]')
    row = page.eval_on_selector('tr[data-analysis="rmsd"][data-status="failed"] .analysis-results-other',
                                "td => td.textContent.trim()")
    page.context.close()
    assert errors == []
    assert row == "instantiation failed: the selection matched no atoms.", row
