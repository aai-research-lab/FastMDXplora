"""All studies opens on its list, each study a row that shows its molecule.

The page opened on two cards a row, each a plot of the study's first
measure, and its Table said a study's name, system, state, dates and its
first three means. A workspace of forty studies is read as rows, and a
study is known by its molecule before its RMSD (user, 10-18: "why not just
show the molecule?"). So the list comes first: views as tabs (All,
Running, Needs more sampling, Not started, and the person's own, kept in
this browser), chips for state, system and tags, rows grouped (replicas
folded under their study, folders where nothing ran gathered at the foot),
and in each row the molecule, where the study stands (a run going, how far
and how long it has left), the ns simulated and every mean, a filled dot
where it is determined and a ring where it is not, with why. A study of
replicas gives the mean of its replicas' means, its error their spread
over the square root of their number, as the Compare page does; and where
some replicas could not determine their own mean, the spread, not an
error. A trajectory analysed with no run of its own says "analysed", not
"not started". The cards are a gallery of the same molecules. Keys: / to
search, j and k to move, x to choose, m for every mean.
"""

from __future__ import annotations

import json
import math

import pytest

from tests.test_a_study_without_a_figure_shows_its_backbone import _helix, _pdb
from tests.test_the_workspace_says_its_studies import _replicas, _study


def _live(folder, *, status="running", current=6000, elapsed=381.555):
    from datetime import datetime, timedelta, timezone

    (folder / "simulation").mkdir(parents=True, exist_ok=True)
    (folder / "simulation" / "live_status.json").write_text(json.dumps({
        "stage": "production", "status": status, "current_step": current,
        "total_planned_steps": 12000, "timestep_fs": 2.0, "elapsed_wall_time_s": elapsed,
        "nvt_steps_planned": 1000, "npt_steps_planned": 1000, "production_steps_planned": 10000,
        "stage_states": {"production": "running" if status == "running" else "completed"},
    }), encoding="utf-8")
    # Production's own samples: 4,000 steps in 200 s, 20 steps a second.
    # The run's whole time (381.555 s for 6,000 steps) has setup in it.
    start = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
    lines = ["timestamp,stage,step,simulation_time_ns,temperature,speed"]
    for k in range(5):
        lines.append(f"{(start + timedelta(seconds=50 * k)).isoformat()},Production,"
                     f"{2000 + 1000 * k},{(2000 + 1000 * k) * 2e-6},300,1.0")
    (folder / "simulation" / "live_metrics.csv").write_text("\n".join(lines) + "\n")
    return folder


def _members(folder, means, *, short=(), unit="nm"):
    """Each replica's own record of its RMSD, as its analysis writes it: a
    run in ``short`` too short against its correlation time (no error of
    its own, its mean not a measurement). No `members.json`: a study need
    not keep one, and the runs' table reads the runs."""
    for n, m in enumerate(means, start=1):
        run = folder / "runs" / f"seed{n}" / "analysis" / "rmsd"
        run.mkdir(parents=True, exist_ok=True)
        too_short = f"seed{n}" in short
        (run / "options.json").write_text(json.dumps({"analysis": "rmsd", "findings": {"mean": {
            "mean": m, "standard_error": None if too_short else 0.01, "unit": unit,
            "effective_samples": 3 if too_short else 40,
            "not_a_measurement": "Too short against its correlation time." if too_short
            else None}}}), encoding="utf-8")


def _paced(pace):
    """A study's progress and time left as the page works them out, by the
    Overview's and the status bar's own functions (run-progress.js)."""
    from pathlib import Path

    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    import fastmdxplora.gui as gui

    script = Path(gui.__file__).parent / "static" / "run-progress.js"
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.add_script_tag(path=str(script))
        said = page.evaluate("(pace) => window.FastMDXRunProgress.ofPace(pace)", pace)
        browser.close()
    return said


def test_a_run_going_says_how_far_and_how_long_it_has_left(tmp_path):
    from fastmdxplora.gui.workspace import card_of

    card = card_of(_live(_study(tmp_path / "going")))
    # 6,000 steps taken, 2,000 of them equilibration: 4,000 of production's
    # 10,000 at 2 fs, 8 ps of 20.
    assert card["state"] == "running"
    assert card["simulated_ns"] == pytest.approx(0.008)
    assert card["planned_ns"] == pytest.approx(0.02)
    # As the status bar says it: 6,000 of 12,000 steps (it said 40%, of the
    # production alone, under a status bar's 50%), and 6,000 steps more at
    # the rate of its newest samples, 20 a second, as the Overview's time
    # left reads it; not at the whole time's 381.555 s a 6,000, setup in it.
    said = _paced(card["pace"])
    assert said["percent"] == pytest.approx(50.0) and said["seconds"] == pytest.approx(300)
    ended = card_of(_live(_study(tmp_path / "ended"), status="completed", current=12000))
    assert ended["simulated_ns"] == pytest.approx(0.02) and "pace" not in ended
    # Stopped once its quantities were determined, short of the ceiling:
    # what it ran, as its Overview says, not the 20 ps it might have.
    early = _live(_study(tmp_path / "early"), status="completed", current=8000)
    (early / "simulation" / "simulation_parameters.json").write_text(
        json.dumps({"duration_ns_actual": 0.012}), encoding="utf-8")
    early = card_of(early)
    assert early["simulated_ns"] == pytest.approx(0.012)
    assert early["planned_ns"] == pytest.approx(0.02)


def test_a_study_of_runs_counts_every_run_as_it_runs_them(tmp_path):
    """Three replicas of 20 ps, one ended, one going at 8 ps, one not
    begun: it said 28 ps of 40 ps, 70%, five minutes left, the run not begun
    left out of what was planned and of the time; three going side by side
    were said the sum of their times left."""
    import yaml

    from fastmdxplora.gui.workspace import _how_far

    study = _replicas(tmp_path / "reps")
    batch = json.loads((study / "batch_manifest.json").read_text())
    _live(study / "runs" / "seed1", status="completed", current=12000)
    _live(study / "runs" / "seed2")
    one_after_another = _how_far(study, batch, {"state": "running"},
                                 config={"execution": {"mode": "sequential"}})
    assert one_after_another["simulated_ns"] == pytest.approx(0.028)
    assert one_after_another["planned_ns"] == pytest.approx(0.06)
    assert one_after_another["lengths"] == [pytest.approx(0.02), pytest.approx(0.008)]
    said = _paced(one_after_another["pace"])
    # 18,000 of 36,000 steps; 300 s for the run going, 600 for the next.
    assert said["percent"] == pytest.approx(50.0) and said["seconds"] == pytest.approx(900)
    _live(study / "runs" / "seed1")
    _live(study / "runs" / "seed3")
    side_by_side = _how_far(study, batch, {"state": "running"},
                            config={"execution": {"workers": 3}})
    assert side_by_side["pace"]["mode"] == "parallel"
    assert _paced(side_by_side["pace"])["seconds"] == pytest.approx(300)
    # Side by side on as many workers as nobody said: not known.
    unknown = _how_far(study, batch, {"state": "running"},
                       config={"execution": {"mode": "parallel"}})
    assert _paced(unknown["pace"])["seconds"] is None
    # The workers its runner resolved and recorded, where the Config set
    # only the mode: what it ran on, read first.
    recorded = _how_far(study, {**batch, "execution": {"mode": "parallel", "workers": 3}},
                        {"state": "running"}, config={"execution": {"mode": "parallel"}})
    assert recorded["pace"]["workers"] == 3
    assert _paced(recorded["pace"])["seconds"] == pytest.approx(300)
    # A run not begun where a length is swept: what was planned not known.
    (study / "runs" / "seed3" / "simulation" / "live_status.json").unlink()
    (study / "runs" / "seed3" / "simulation" / "live_metrics.csv").unlink()
    (study / "runs" / "seed3" / "simulation").rmdir()
    batch["sweep"] = {"simulation.duration_ns": [0.02, 0.04, 0.06]}
    swept = _how_far(study, batch, {"state": "running"}, config=yaml.safe_load("execution: {}"))
    assert "planned_ns" not in swept and _paced(swept["pace"])["seconds"] is None


def test_a_trajectory_analysed_is_analysed_and_every_mean_is_sent(tmp_path):
    from fastmdxplora.gui.workspace import card_of

    analysed = tmp_path / "helical"
    (analysis := analysed / "analysis").mkdir(parents=True)
    (analysis / "analysis_manifest.json").write_text("{}", encoding="utf-8")
    card = card_of(analysed)
    assert (card["state"], card["kind"]) == ("analysed", "a trajectory analysed")
    (tmp_path / "empty").mkdir()
    assert card_of(tmp_path / "empty")["state"] == "not started"
    many = _study(tmp_path / "many",
                  means={n: (1.0, 0.01) for n in ("rmsd", "rg", "sasa", "hbonds")})
    assert len(card_of(many)["means"]) == 4


def test_a_study_of_replicas_gives_its_mean_of_means(tmp_path):
    from fastmdxplora.gui.workspace import card_of

    study = _replicas(tmp_path / "chignolin")
    _members(study, [0.10, 0.12, 0.14])
    card = card_of(study)
    rmsd = card["means"][0]
    assert card["runs"] == 3
    assert rmsd["mean"] == pytest.approx(0.12) and rmsd["spread"] == pytest.approx(0.02)
    # The error of the mean of three: their spread over the root of three.
    assert rmsd["error"] == pytest.approx(0.02 / math.sqrt(3)) and rmsd["withheld"] is None
    _members(study, [0.10, 0.12, 0.14], short=("seed2",))
    rmsd = card_of(study)["means"][0]
    assert rmsd["error"] is None and rmsd["spread"] == pytest.approx(0.02)
    assert rmsd["withheld"] == (
        "1 of the 3 replicas did not determine its own mean: it said its mean is not "
        "determined, so their spread is not yet an error: begun from one structure, runs "
        "too short to resolve their correlation time are not yet independent samples.")
    # None of them determined its own: not an error either (it was said
    # determined, the qualification written only where one run gave one).
    _members(study, [0.10, 0.12, 0.14], short=("seed1", "seed2", "seed3"))
    rmsd = card_of(study)["means"][0]
    assert rmsd["error"] is None and rmsd["withheld"].startswith(
        "None of the 3 replicas determined its own mean")


def test_the_replicas_mean_and_error_are_the_runs_tables(tmp_path, monkeypatch):
    """One derivation: All studies gives the replicas' mean, its error, or
    why it has none, as the runs' table (`runs_compared`) and the written
    comparison (`batch.compare`) give them, from the runs' records and
    with no `members.json`. The row said "no mean recorded" for a study
    that kept none, and withheld an error the runs' table gave."""
    from fastmdxplora.batch.compare import _across_the_replicas
    from fastmdxplora.gui import runs_compared
    from fastmdxplora.gui.workspace import card_of

    study = _replicas(tmp_path / "chignolin")
    for short in ((), ("seed2",), ("seed1", "seed2", "seed3")):
        _members(study, [0.10, 0.12, 0.14], short=short)
        table = {m["analysis"]: m for m in runs_compared.runs_compared(study)["measures"]}
        rmsd = card_of(study)["means"][0]
        together = table["rmsd"]["ensemble"]
        assert (rmsd["mean"], rmsd["error"], rmsd["spread"], rmsd["withheld"]) == (
            together["mean"], together["error"], together["spread"], together["withheld"]), short
        written = "\n".join(_across_the_replicas(study))
        if short:
            assert together["withheld"] in written and together["withheld"] in table["rmsd"]["said"]
        else:
            assert "is not yet an error" not in written
    monkeypatch.setattr(runs_compared, "mean_of_replicas",
                        lambda found, of=None: {**together, "error": 0.5})
    from fastmdxplora.gui import workspace

    workspace._CARDS_KEPT.clear()
    assert card_of(study)["means"][0]["error"] == 0.5


def test_the_molecule_is_its_backbone_in_its_own_axes(tmp_path):
    from fastmdxplora.gui import backbone_picture
    from fastmdxplora.gui.backbone_picture import LIST_POINTS, backbone_points

    study = _study(tmp_path / "helix")
    assert backbone_points(study) is None
    _pdb(study / "setup" / "prepared.pdb", _helix(20, "A", 1))
    drawn = backbone_points(study)
    (piece,) = drawn["pieces"]
    assert drawn["from"] == "structure" and drawn["residues"] == 20 and len(piece) == 20
    # The N terminus on the left, coloured from 0 to 1 along the chain,
    # the height filled, the width its own; depth from 0 to 1.
    assert piece[0][0] < piece[-1][0]
    assert [p[3] for p in piece] == sorted(p[3] for p in piece) and piece[-1][3] == 1
    assert min(p[1] for p in piece) == 0 and max(p[1] for p in piece) == 1
    assert max(p[0] for p in piece) == pytest.approx(drawn["aspect"], abs=1e-3)
    assert drawn["aspect"] >= 1
    assert {min(p[2] for p in piece), max(p[2] for p in piece)} == {0, 1}
    # Kept until the file changes.
    assert backbone_points(study) == drawn
    assert any(key[0].endswith("prepared.pdb") for key in backbone_picture._POINTS_KEPT)
    # The latest frame a run wrote is drawn before the structure it began from.
    _pdb(study / "simulation" / "live_frame.pdb", _helix(500, "A", 1))
    latest = backbone_points(study)
    assert latest["from"] == "latest frame" and latest["residues"] == 500
    assert sum(len(p) for p in latest["pieces"]) <= LIST_POINTS


LOOK = """() => ({
    rows: [...document.querySelectorAll('#studies-table tbody tr.studies-row')]
        .map((r) => r.querySelector('.studies-name-text').textContent),
    paths: [...document.querySelectorAll('#studies-table tbody tr.studies-row')]
        .map((r) => r.dataset.path),
    children: document.querySelectorAll('#studies-table tr.studies-child').length,
    groups: [...document.querySelectorAll('#studies-table tr.studies-group b')]
        .map((b) => b.textContent),
    tabs: [...document.querySelectorAll('#studies-views .studies-view-tab')]
        .map((t) => [...t.childNodes].map((n) => n.textContent.trim()).filter(Boolean).join(' ')),
    selected: (document.querySelector('#studies-views [aria-selected="true"]') || {}).textContent,
    grid: !document.getElementById('studies-grid').hidden,
    focused: (document.activeElement.closest('tr') || {}).dataset?.path || '',
    pop: (document.querySelector('.studies-means-pop') || {}).textContent || '',
    wide: document.documentElement.scrollWidth > innerWidth,
})"""


def test_the_list_first_with_views_molecules_means_and_keys(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    helix = _study(tmp_path / "helix", system="1L2Y", started="2026-09-03T10:00:00+00:00",
                   means={"rmsd": (0.05, 0.01), "rg": (0.7, 0.01), "sasa": (12.0, 0.1)})
    _pdb(helix / "setup" / "prepared.pdb", _helix(20, "A", 1))
    _live(_study(tmp_path / "going", system="1UAO", started="2026-09-02T10:00:00+00:00"))
    replicas = _replicas(tmp_path / "chignolin")
    _members(replicas, [0.10, 0.12, 0.14], short=("seed2",))
    for n in (1, 2, 3):
        run = replicas / "runs" / f"seed{n}"
        (run / "resolved_config.yml").write_text("systems: [{system: 1UAO}]\n", encoding="utf-8")
        (run / "manifest.json").write_text(json.dumps(
            {"phases": [{"name": "simulation", "status": "ok"}]}), encoding="utf-8")
    (tmp_path / "begun").mkdir()
    (tmp_path / "begun" / "resolved_config.yml").write_text("systems: [{system: 1ABC}]\n")
    session = start_dashboard_session(output=str(helix), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.set_default_timeout(60000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url + "#studies", wait_until="domcontentloaded")
            page.wait_for_selector("body.state-ready")
            page.evaluate(f"() => window.FastMDXStudies.load({str(tmp_path)!r})")
            page.wait_for_selector("#studies-table tbody tr.studies-row")
            first = page.evaluate(LOOK)
            row = '#studies-table tr.studies-row[data-path$="helix"]'
            molecule = page.eval_on_selector(row, "(r) => [r.querySelector('.studies-mol-frame')"
                                                  ".dataset.picture, r.querySelectorAll("
                                                  "'svg.study-molecule line').length]")
            going = page.text_content(
                '#studies-table tr.studies-row[data-path$="going"] .studies-state')
            bar = page.get_attribute('#studies-table tr[data-path$="going"] .studies-progress',
                                     "aria-valuenow")
            means = page.eval_on_selector(
                '#studies-table tr.studies-row[data-path$="chignolin"]',
                "(r) => [...r.querySelectorAll('.studies-mean-item')].map((m) => "
                "[m.dataset.determined, m.querySelector('.studies-mean-v').textContent])")
            # The replicas unfolded: read when asked.
            page.click('#studies-table tr[data-path$="chignolin"] .studies-fold')
            page.wait_for_selector("#studies-table tr.studies-child")
            unfolded = page.evaluate(LOOK)
            # A view: Running alone.
            page.click('#studies-views .studies-view-tab:has-text("Running")')
            running = page.evaluate(LOOK)
            page.click('#studies-views .studies-view-tab:has-text("All")')
            # The person's own view: a search kept under a name, and kept.
            page.fill("#studies-search", "1L2Y")
            page.click("#studies-views .studies-view-save")
            page.fill("#studies-views .studies-view-name", "Trp-cage")
            page.keyboard.press("Enter")
            page.wait_for_selector('#studies-views .studies-view-tab.is-saved:has-text("Trp-cage")')
            page.reload(wait_until="domcontentloaded")
            # Settled: the list read again as the page opens is drawn before
            # the keys go to a row.
            page.wait_for_selector("body.state-ready")
            # And its first page known, which reads the list again.
            page.wait_for_function("() => window.FastMDXDashboard"
                                   " && window.FastMDXDashboard.state.initialRouteResolved")
            page.evaluate(f"() => window.FastMDXStudies.load({str(tmp_path)!r})")
            page.wait_for_selector("#studies-table tbody tr.studies-row")
            page.click('#studies-views .studies-view-tab:has-text("Trp-cage")')
            saved = page.evaluate(LOOK)
            page.click('#studies-views .studies-view-tab:has-text("All")')
            # The keys: j moves down the rows, x chooses, m every mean.
            page.click("#studies-views .studies-view-tab[aria-selected='true']")
            page.keyboard.press("j")
            page.keyboard.press("j")
            page.keyboard.press("j")
            moved = page.evaluate(LOOK)
            page.keyboard.press("x")
            chosen = page.eval_on_selector_all("#studies-table .studies-pick input:checked",
                                               "(all) => all.map((b) => b.dataset.path)")
            page.keyboard.press("m")
            page.wait_for_selector(".studies-means-pop")
            popped = page.evaluate(LOOK)
            page.keyboard.press("Escape")
            page.keyboard.press("/")
            searching = page.evaluate("() => document.activeElement.id")
            page.keyboard.press("Escape")
            # The cards: a gallery of the same molecules.
            page.click('[data-studies-view="cards"]')
            page.wait_for_selector(".study-card")
            gallery = page.eval_on_selector(
                '.study-card[data-path$="helix"] .study-thumb',
                "(t) => [t.dataset.picture, Math.round(t.getBoundingClientRect().height)]")
            page.click('[data-studies-view="table"]')
            page.set_viewport_size({"width": 390, "height": 844})
            page.wait_for_timeout(300)
            phone = page.evaluate(LOOK)
            phone_row = page.eval_on_selector(row, "(r) => getComputedStyle(r).display")
            browser.close()
    finally:
        session.server.shutdown()
    assert errors == []
    assert not first["grid"]
    # Most recently active first; the folder where nothing ran at the foot.
    assert first["rows"][-1] == "begun" and first["groups"] == ["Not started"]
    assert first["tabs"][:4] == ["All 4", "Running 1", "Needs more sampling 1", "Not started 1"]
    assert molecule == ["molecule", 19]
    assert going.strip().startswith("running, 50%") and "8\u00a0ps of 20\u00a0ps · 5m 0s left" in going
    assert bar == "50"
    assert means == [["false", "0.120 nm, spread 0.020"]]
    assert unfolded["children"] == 3
    assert running["rows"] == ["going"] and running["selected"].startswith("Running")
    assert saved["rows"] == ["helix"] and saved["selected"].startswith("Trp-cage")
    # The replicas, the oldest, last: the third row.
    assert moved["focused"] == moved["paths"][2]
    assert len(chosen) == 1 and chosen[0] == moved["focused"]
    assert "every mean recorded" in popped["pop"]
    assert searching == "studies-search"
    assert gallery == ["molecule", 130]
    assert phone_row == "grid" and not phone["wide"]


def test_why_a_mean_is_not_determined_is_reached_from_the_keyboard(tmp_path):
    """A row of one or two means, one not determined, said why only on
    hover over its ring: no button to reach it from the keyboard, as +N is
    where there are more means than a row shows."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    replicas = _replicas(tmp_path / "chignolin")
    _members(replicas, [0.10, 0.12, 0.14], short=("seed2",))
    _study(tmp_path / "helix", means={"rmsd": (0.05, 0.01)})
    short = _study(tmp_path / "short", means={"rmsd": (0.05, 0.01)})
    options = short / "analysis" / "rmsd" / "options.json"
    record = json.loads(options.read_text())
    record["findings"]["mean"]["not_a_measurement"] = (
        "Too short against its correlation time. It needs 40 ps more. Run it on.")
    options.write_text(json.dumps(record))
    session = start_dashboard_session(output=str(tmp_path / "helix"), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.set_default_timeout(60000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url + "#studies", wait_until="domcontentloaded")
            page.evaluate(f"() => window.FastMDXStudies.load({str(tmp_path)!r})")
            page.wait_for_selector("#studies-table tbody tr.studies-row")
            # The page settled: drawn again, a list closes what is open on it.
            page.wait_for_selector("body.state-ready")
            page.evaluate(f"() => window.FastMDXStudies.load({str(tmp_path)!r})")
            row = '#studies-table tr.studies-row[data-path$="/chignolin"]'
            offered = page.locator(f"{row} .studies-means-more").count()
            determined = page.locator(
                '#studies-table tr.studies-row[data-path$="/helix"] .studies-means-more').count()
            page.focus(f"{row} .studies-means-more")
            page.keyboard.press("Enter")
            page.wait_for_selector(".studies-means-pop")
            said = page.inner_text(".studies-means-pop")
            page.keyboard.press("Escape")
            # A reason of more than one sentence: the keyboard in it, on its
            # first sentence; Enter opens the rest; Tab stays in it; Escape
            # gives it back to "why".
            page.focus('#studies-table tr.studies-row[data-path$="/short"] .studies-means-more')
            page.keyboard.press("Enter")
            page.wait_for_selector(".studies-means-pop")
            inside = page.evaluate("() => document.activeElement.tagName")
            page.keyboard.press("Enter")
            rest = page.evaluate("() => document.querySelector('.studies-means-pop details').open")
            page.keyboard.press("Tab")
            kept = page.evaluate("() => !!document.activeElement.closest('.studies-means-pop')")
            page.keyboard.press("Escape")
            back = page.evaluate("() => document.activeElement.className")
            browser.close()
    finally:
        session.server.shutdown()
    assert errors == []
    assert offered == 1 and determined == 0
    assert inside == "SUMMARY" and rest is True and kept is True
    assert back == "studies-means-more"
    assert "did not determine its own mean" in said


@pytest.mark.parametrize("width, beside, panel, rows", [
    (1920, False, None, "table-row"), (1440, False, "open", "table-row"),
    (1440, False, "folded", "table-row"), (1250, True, None, "table-row"),
    (900, False, None, "grid"), (390, False, None, "grid")])
def test_the_list_fits_its_column_and_keeps_what_it_says(tmp_path, width, beside, panel, rows):
    """The table ran past its column, its means and buttons under a
    sideways scroll; then it was lines at every width a laptop has, which
    left out the sort, the head's choose-all, Active, a run's time left and
    the row's Open, Tag and Files. Columns now give way in turn (Active and
    the molecule first, said under the name), the table kept at 1440 with
    the side panel open or folded and at about 1250 with the Agent beside,
    lines only in a window under about 1000, and lines keep all of it. A
    long "Not read" list made every width lines and was cut; it wraps, and
    its whole opens from it by keyboard or tap. The count of studies, cut
    beside the title at 1250, is kept whole. A study with tags lost its
    name to them at 1440 and wider, the tags running into State; they wrap
    under it. On a phone the running study's name was cut to a few letters
    by how far it got and its time left, which have a line of their own."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    _study(tmp_path / "helix", system="1L2Y", started="2026-09-03T10:00:00+00:00",
           means={"rmsd": (0.0074123, 0.00071), "rg": (0.313961, 0.00096), "sasa": (12.0, 0.1)})
    _live(_study(tmp_path / "going_batch_live", system="1UAO", started="2026-09-02T10:00:00+00:00"))
    _members(_replicas(tmp_path / "chignolin"), [0.10, 0.12, 0.14], short=("seed2",))
    tagged = _study(tmp_path / "c_study_tagged", system="1UBQ", started="2026-09-01T10:00:00+00:00",
                    means={"rmsd": (0.1, 0.01)})
    (tagged / "study_tags.json").write_text(json.dumps({"tags": [
        "wild type", "JCIM Fig. 4", "reference run", "needs more"]}), encoding="utf-8")
    linked = _study(tmp_path / "a_study_with_a_long_name_linked_out", started="2026-09-04T10:00:00+00:00",
                    means={n: (1.0, 0.01) for n in ("rmsd", "rg", "sasa", "hbonds", "end_to_end")})
    outside = tmp_path.parent / (tmp_path.name + "_outside")
    for path in sorted(p for p in linked.rglob("*") if p.is_file()):
        target = outside / path.relative_to(linked)
        target.parent.mkdir(parents=True, exist_ok=True)
        path.rename(target)
        path.symlink_to(target)
    session = start_dashboard_session(output=str(tmp_path / "helix"), host="127.0.0.1", port=0)
    look = """() => {
        const wrap = document.getElementById('studies-table-wrap');
        const edge = wrap.getBoundingClientRect().right;
        const past = [...wrap.querySelectorAll('tbody td *')].filter((n) => {
            const r = n.getBoundingClientRect();
            return r.width > 0 && r.right > edge + 1;
        }).map((n) => n.className);
        const shown = (n) => !!n && n.getClientRects().length > 0 && getComputedStyle(n).visibility !== 'hidden';
        const going = document.querySelector('#studies-table tr.studies-row[data-path$="/going_batch_live"]');
        const helix = document.querySelector('#studies-table tr.studies-row[data-path$="/helix"]');
        const count = document.querySelector('.studies-where-n');
        const tagged = document.querySelector('#studies-table tr.studies-row[data-path$="/c_study_tagged"]');
        const cellOf = (row) => row.querySelector('td.studies-name').getBoundingClientRect();
        const nameOf = (row) => row.querySelector('.studies-name-text').getBoundingClientRect();
        return {over: wrap.scrollWidth - wrap.clientWidth, past: past.slice(0, 4),
                rows: getComputedStyle(wrap.querySelector('tr.studies-row')).display,
                page: document.documentElement.scrollWidth - innerWidth,
                sort: shown(document.querySelector('#studies-table thead .studies-sort'))
                    || shown(document.querySelector('.studies-sort-select')),
                all: shown(document.querySelector('#studies-table thead .studies-pick-all'))
                    || shown(document.querySelector('.studies-pick-all-lines')),
                left: shown(going.querySelector('.studies-state-more'))
                    ? going.querySelector('.studies-state-more').textContent : '',
                active: shown(helix.querySelector('td.studies-active'))
                    || shown(helix.querySelector('.studies-active-said')),
                count: [count.textContent, count.scrollWidth <= count.clientWidth + 1,
                        count.getBoundingClientRect().width > 0],
                tagged: {name: Math.round(nameOf(tagged).width),
                         tags: [...tagged.querySelectorAll('.studies-row-tag')].filter((t) =>
                             t.getBoundingClientRect().right > cellOf(tagged).right + 1
                             || t.getBoundingClientRect().top < nameOf(tagged).bottom - 1).length},
                going: Math.round(nameOf(going).width) >= Math.min(
                    going.querySelector('.studies-name-text').scrollWidth, 100)};
    }"""
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": width, "height": 900})
            page.set_default_timeout(60000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url + "#studies", wait_until="domcontentloaded")
            page.wait_for_selector("body.state-ready")
            page.evaluate(f"() => window.FastMDXStudies.load({str(tmp_path)!r})")
            page.wait_for_selector("#studies-table tbody tr.studies-row")
            if beside:
                page.click("#agent-beside-open")
                page.locator("#agent-drawer").wait_for()
            elif panel == "open" and page.is_visible("#side-expand"):
                page.click("#side-expand")
            elif panel == "folded" and page.is_visible("#side-collapse"):
                page.click("#side-collapse")
            page.wait_for_timeout(500)
            seen = page.evaluate(look)
            # The records not read, every one with why, from the keyboard.
            said = page.locator('tr.studies-row[data-path$="_linked_out"] .studies-not-read')
            said.focus()
            page.keyboard.press("Enter")
            page.wait_for_selector(".studies-means-pop")
            opened = page.evaluate("""() => [document.querySelector('.studies-means-pop').innerText,
                !!document.activeElement.closest('.studies-means-pop')]""")
            page.keyboard.press("Escape")
            back = page.evaluate("() => document.activeElement.classList.contains('studies-not-read')")
            browser.close()
    finally:
        session.server.shutdown()
    assert errors == []
    assert seen["over"] <= 0 and seen["past"] == [] and seen["page"] <= 0, seen
    assert seen["rows"] == rows, seen
    assert seen["sort"] and seen["all"] and seen["active"], seen
    assert "8\u00a0ps of 20\u00a0ps" in seen["left"] and "left" in seen["left"], seen
    # On a phone a page's heading is its name alone (every page's heading
    # is one line), the count with it.
    assert seen["count"] == ["5 studies", True, width > 800], seen
    # The tagged study's name kept (about 8 characters or all of it), its
    # tags under it, within its cell; the running study's name kept whole.
    assert seen["tagged"]["name"] >= 60 and seen["tagged"]["tags"] == 0, seen
    assert seen["going"], seen
    assert "analysis/sasa/options.json" in opened[0] and opened[1]
    assert "a link to a file outside the study" in opened[0]
    assert back


def test_a_replicas_mean_of_fewer_runs_says_how_many(tmp_path):
    """Replicas one of whose runs is linked out of the study (not read):
    the other two's mean and error were given as the study's, a filled dot,
    "Determined, with its standard error", nothing on it saying it was two
    of three. The runs' table, by the same function, the same."""
    import shutil

    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.runs_compared import runs_compared
    from fastmdxplora.gui.server import start_dashboard_session
    from fastmdxplora.gui.workspace import card_of

    reps = _replicas(tmp_path / "ws" / "reps")
    _members(reps, [0.10, 0.12, 0.14])
    outside = tmp_path / "outside" / "seed2"
    shutil.move(str(reps / "runs" / "seed2"), str(outside))
    (reps / "runs" / "seed2").symlink_to(outside)
    (mean,) = card_of(reps)["means"]
    assert (mean["replicas"], mean["of"]) == (2, 3)
    # seed1 and seed3: their spread, 0.0283, over the square root of the two.
    assert mean["error"] == pytest.approx(0.02)
    # The runs' table: a run that gave no mean, by the same function.
    whole = _replicas(tmp_path / "ws2" / "whole")
    _members(whole, [0.10, 0.12, 0.14])
    (together,) = [m["ensemble"] for m in runs_compared(whole)["measures"]]
    assert together["runs"] == 3 and "of" not in together
    (whole / "runs" / "seed3" / "analysis" / "rmsd" / "options.json").unlink()
    (together,) = [m["ensemble"] for m in runs_compared(whole)["measures"]]
    assert (together["runs"], together["of"]) == (2, 3)
    session = start_dashboard_session(output=str(reps), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.set_default_timeout(60000)
            page.goto(session.url + "#studies", wait_until="domcontentloaded")
            page.wait_for_selector("body.state-ready")
            page.evaluate(f"() => window.FastMDXStudies.load({str(tmp_path / 'ws')!r})")
            item = '#studies-table tr.studies-row[data-path$="/reps"] .studies-mean-item'
            page.wait_for_selector(item)
            said = page.inner_text(item)
            title = page.get_attribute(item, "title")
            browser.close()
    finally:
        session.server.shutdown()
    assert "from 2 of 3 replicas" in said
    assert title == ("Determined: the mean of the means from 2 of the study's 3 replicas, its "
                     "error their spread over √2")


def test_a_study_of_runs_says_how_many_began_in_one_unit_and_when_its_run_was_active(tmp_path):
    """Three runs, one begun: "1 × 18.5 ps". Two begun, one at its start:
    "2 runs, 0 ns to 80 ps". A study whose run is going said "active 1 h
    ago" beside its run's "Just now": its own records are not the run's."""
    import os
    import time

    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session
    from fastmdxplora.gui.workspace import card_of

    space = tmp_path / "ws"
    one = _replicas(space / "one_begun")
    _live(one / "runs" / "seed1")
    two = _replicas(space / "two_begun")
    _live(two / "runs" / "seed1", status="completed", current=2000)
    _live(two / "runs" / "seed2", status="completed", current=12000)
    # All three began; one failed early, its record not giving its length.
    failed = _replicas(space / "one_failed")
    for seed in ("seed1", "seed2"):
        _live(failed / "runs" / seed, status="completed", current=12000)
    (failed / "runs" / "seed3" / "simulation").mkdir(parents=True)
    (failed / "runs" / "seed3" / "simulation" / "live_status.json").write_text(json.dumps({
        "stage": "production", "status": "failed", "current_step": 3000}), encoding="utf-8")
    hour_ago = time.time() - 3600
    for study in (one, two):
        for name in ("batch_manifest.json", "resolved_config.yml"):
            os.utime(study / name, (hour_ago, hour_ago))
    card = card_of(one)
    assert card["last_active"] == card_of(one / "runs" / "seed1")["last_active"]
    assert (card_of(failed)["begun"], len(card_of(failed)["lengths"])) == (3, 2)
    from datetime import datetime

    assert time.time() - datetime.fromisoformat(card["last_active"]).timestamp() < 600
    session = start_dashboard_session(output=str(one), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1920, "height": 900})
            page.set_default_timeout(60000)
            page.goto(session.url + "#studies", wait_until="domcontentloaded")
            page.wait_for_selector("body.state-ready")
            page.evaluate(f"() => window.FastMDXStudies.load({str(space)!r})")
            page.wait_for_selector("#studies-table tbody tr.studies-row")
            each = {name: page.inner_text(f'#studies-table tr.studies-row[data-path$="/{name}"] '
                                          '.studies-length-each')
                    for name in ("one_begun", "two_begun", "one_failed")}
            browser.close()
    finally:
        session.server.shutdown()
    # A number kept with its unit (a no-break space) where the line wraps.
    assert each == {"one_begun": "1 of 3 runs begun, 8\u00a0ps",
                    "two_begun": "2 of 3 runs begun, 0\u00a0ps to 20\u00a0ps",
                    "one_failed": "2 of 3 lengths known, 20\u00a0ps each"}


def _shown_at(space, width, look, *, unfold=None):
    """What ``look`` finds on All studies of ``space`` at ``width``, a
    study's runs unfolded first where ``unfold`` names it."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    session = start_dashboard_session(output=str(next(space.iterdir())), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": width, "height": 900},
                                    is_mobile=width < 500, has_touch=width < 500)
            page.set_default_timeout(60000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url + "#studies", wait_until="domcontentloaded")
            page.wait_for_selector("body.state-ready")
            page.evaluate(f"() => window.FastMDXStudies.load({str(space)!r})")
            page.wait_for_selector("#studies-table tbody tr.studies-row")
            if unfold:
                page.click(f'#studies-table tr.studies-row[data-path$="/{unfold}"] .studies-fold')
                page.wait_for_selector("#studies-table tr.studies-child")
            said = page.evaluate(look)
            browser.close()
    finally:
        session.server.shutdown()
    assert errors == []
    return said


@pytest.mark.parametrize("width", [390, 900, 1920])
def test_an_unfolded_run_s_connector_stays_in_its_row(tmp_path, width):
    """Under 1000 px, unfolding a study's runs drew each run's connector
    from a box above the list: a line down through the filters and rows,
    its hook beside the parent."""
    space = tmp_path / "ws"
    _members(_replicas(space / "reps"), [0.10, 0.12, 0.14])
    tall = _shown_at(space, width, """() => [...document.querySelectorAll('#studies-table tr.studies-child')]
        .map((row) => {
            const line = getComputedStyle(row.querySelector('td.studies-name'), '::before');
            return line.content === 'none' ? 0 : parseFloat(line.height) - row.getBoundingClientRect().height;
        })""", unfold="reps")
    assert len(tall) == 3 and max(tall) <= 0, tall


def test_a_cut_name_is_said_whole_and_a_refused_archive_says_why_once(tmp_path):
    """A name cut by a narrow column had nothing that gave the rest; a
    refused Archive said its reason with two full stops."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    space = tmp_path / "ws"
    long_name = "R-batch-live-replicas-of-chignolin-at-300K-seed-7"
    _study(space / long_name, started="2026-09-02T10:00:00+00:00")
    out = _study(space / "tags_out", started="2026-09-03T10:00:00+00:00")
    (tmp_path / "tags.json").write_text(json.dumps({"tags": ["elsewhere"]}), encoding="utf-8")
    (out / "study_tags.json").symlink_to(tmp_path / "tags.json")
    session = start_dashboard_session(output=str(out), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1250, "height": 900})
            page.set_default_timeout(60000)
            page.goto(session.url + "#studies", wait_until="domcontentloaded")
            page.wait_for_selector("body.state-ready")
            page.evaluate(f"() => window.FastMDXStudies.load({str(space)!r})")
            page.wait_for_selector("#studies-table tbody tr.studies-row")
            title = page.get_attribute(f'tr.studies-row[data-path$="/{long_name}"] .studies-name-text',
                                       "title")
            page.click('#studies-table tr.studies-row[data-path$="/tags_out"] .studies-pick input')
            page.click("#studies-archive")
            page.wait_for_function("() => document.querySelector('.studies-bar-said')"
                                   ".textContent.includes('not changed')")
            said = page.inner_text(".studies-bar-said")
            browser.close()
    finally:
        session.server.shutdown()
    assert title.startswith(long_name + ": open ")
    assert said.strip().endswith("outside the study.") and ".." not in said, said


def test_a_study_that_ran_is_not_said_not_started(tmp_path):
    """A study with no Manifest that simulated and analysed was said "not
    started", grouped under "nothing ran", beside its 40 ps and its RMSD;
    one that completed without recording its timestep said its length was
    "none", "Nothing simulated yet"."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session
    from fastmdxplora.gui.workspace import card_of

    ran = _study(tmp_path / "no_manifest", means={"rmsd": (0.01, 0.001)})
    (ran / "manifest.json").unlink()
    _live(ran, status="completed", current=12000)
    (ran / "analysis" / "analysis_manifest.json").write_text("{}", encoding="utf-8")
    simulated = _study(tmp_path / "simulated_only")
    (simulated / "manifest.json").unlink()
    _live(simulated, status="completed", current=12000)
    untimed = _live(_study(tmp_path / "untimed"), status="completed", current=12000)
    record = json.loads((untimed / "simulation" / "live_status.json").read_text())
    del record["timestep_fs"]
    (untimed / "simulation" / "live_status.json").write_text(json.dumps(record))
    cards = {name: card_of(tmp_path / name) for name in ("no_manifest", "simulated_only")}
    assert (cards["no_manifest"]["state"], cards["no_manifest"]["kind"]) == ("analysed", "one run")
    assert cards["simulated_only"]["state"] == "not known"
    session = start_dashboard_session(output=str(untimed), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1920, "height": 900})
            page.set_default_timeout(60000)
            page.goto(session.url + "#studies", wait_until="domcontentloaded")
            page.wait_for_selector("body.state-ready")
            page.evaluate(f"() => window.FastMDXStudies.load({str(tmp_path)!r})")
            page.wait_for_selector("#studies-table tbody tr.studies-row")
            length = page.inner_text('#studies-table tr.studies-row[data-path$="/untimed"] .studies-length')
            groups = page.eval_on_selector_all("#studies-table .studies-group b",
                                               "g => g.map((x) => x.textContent)")
            browser.close()
    finally:
        session.server.shutdown()
    assert length == "not known"
    assert "Not started" not in groups


def test_a_card_is_read_once_while_its_records_stand(tmp_path, monkeypatch):
    """All studies took two to four times as long as before its cards were
    held to their studies, and the sidebar's Recent asks for the list again
    and again. A card is read again only where a record of its study
    changed (its place, size or time), and a study running every time."""
    import os

    from fastmdxplora.gui import workspace

    study = _study(tmp_path / "helix", means={"rmsd": (0.05, 0.01)})
    reads: list[str] = []
    real = workspace._card_of
    monkeypatch.setattr(workspace, "_card_of", lambda base: reads.append(base.name) or real(base))
    first = workspace.card_of(study)
    again = workspace.card_of(study)
    assert reads == ["helix"] and again == first
    # A record rewritten: read again, with what it says now.
    options = study / "analysis" / "rmsd" / "options.json"
    record = json.loads(options.read_text())
    record["findings"]["mean"]["mean"] = 0.07
    options.write_text(json.dumps(record))
    later = os.stat(options).st_mtime_ns + 10_000_000
    os.utime(options, ns=(later, later))
    assert workspace.card_of(study)["means"][0]["mean"] == 0.07 and reads == ["helix"] * 2
    # A record put in place of a link out of the study: read again, not read.
    outside = tmp_path / "outside.json"
    options.rename(outside)
    options.symlink_to(outside)
    assert workspace.card_of(study)["means"] == [] and len(reads) == 3
    # A study running: read each time.
    going = _live(_study(tmp_path / "going"))
    workspace.card_of(going)
    workspace.card_of(going)
    assert reads[3:] == ["going", "going"]


def test_a_card_kept_sees_its_start_structure_replaced(tmp_path):
    """A study whose Config names its start in a folder of its own
    (``inputs/start.pdb``): its molecule is drawn from it, and a card kept
    by its stamp went on drawing the start it had once that file was
    replaced in place, until the GUI restarted."""
    import os

    from fastmdxplora.gui import workspace

    study = tmp_path / "prepared"
    _pdb(study / "inputs" / "start.pdb", _helix(10, "A", 1))
    (study / "exploration.yml").write_text("systems:\n  - system: inputs/start.pdb\n",
                                           encoding="utf-8")
    first = workspace.card_of(study)["molecule"]
    assert (first["from"], first["residues"]) == ("start", 10)
    start = study / "inputs" / "start.pdb"
    folder = os.stat(start.parent).st_mtime_ns
    _pdb(start, _helix(40, "A", 1))
    later = os.stat(start).st_mtime_ns + 10_000_000
    os.utime(start, ns=(later, later))
    os.utime(start.parent, ns=(folder, folder))
    assert workspace.card_of(study)["molecule"]["residues"] == 40
    assert "_drawn_from" not in workspace.card_of(study)
