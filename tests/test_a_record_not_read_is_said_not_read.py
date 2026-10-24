"""A record the study pages did not read is said not read, not not determined.

The Overview and the Analysis page read only the study's own records: one
linked out of the study is refused (`record_access`). The mean whose record
was refused was badged "Not determined" and counted with the means not
determined ("0 of 1 mean determined"), though whether it was determined is
not known. And nothing on either page named a refused record: an analysis
manifest linked out quietly turned the analyses' times into frame numbers.

A refused mean is now Not read, counted apart, and each page names the
records it did not read, with what a clock not read does to the times.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_the_study_pages_read_only_the_study import (
    CLOCKS,
    OUTSIDE,
    _ask,
    _link,
    _study,
    _write,
)


def _linked(tmp_path: Path, relative: str) -> Path:
    """The study with ``relative`` moved outside it and linked back."""
    root = _study(tmp_path)
    if relative == "analysis/rmsd/options.json":
        content = OUTSIDE["determined"]
    else:
        content = next(content for path, content in CLOCKS.values() if path == relative)
        if (root / relative).exists():
            (root / relative).unlink()
    outside = _write(tmp_path, "elsewhere/" + Path(relative).name, content)
    _link(root / relative, outside)
    return root


@pytest.mark.parametrize("public", [False, True], ids=["loopback", "public"])
def test_the_status_title_is_not_read_from_a_manifest_outside_the_study(tmp_path, public):
    root = _study(tmp_path)
    outside = _write(tmp_path, "elsewhere/manifest.json", {"title": "Outside study title"})
    (root / "manifest.json").unlink()
    _link(root / "manifest.json", outside)

    status, body = _ask(root, tmp_path, "/api/status", public=public)

    assert status == 200
    answer = json.loads(body)
    assert answer["run_title"] == root.name
    assert "Outside study title" not in body.decode("utf-8")
    assert "manifest.json" in answer["unavailable"]


@pytest.mark.parametrize("public", [False, True], ids=["loopback", "public"])
def test_a_mean_not_read_is_counted_apart(tmp_path, public):
    root = _linked(tmp_path, "analysis/rmsd/options.json")

    overview = json.loads(_ask(root, tmp_path, "/api/overview", public=public)[1])
    analyses = json.loads(_ask(root, tmp_path, "/api/analysis-overview", public=public)[1])
    results = json.loads(_ask(root, tmp_path, "/api/results", public=public)[1])

    tiles = {tile["analysis"]: tile for tile in overview["tiles"]}
    assert tiles["rmsd"]["unavailable"] is True and tiles["rmsd"]["determined"] is None
    # Rg determined, of the one mean read; RMSD not read, not counted as a
    # mean not determined.
    assert (overview["means"], overview["determined"], overview["not_read"]) == (1, 1, 1)
    rows = {row["analysis"]: row for row in analyses["rows"]}
    assert rows["rmsd"]["quantities"][0]["unavailable"] is True
    metrics = {row["metric"]: row for row in results["metric_rows"]}
    assert metrics["RMSD"]["status"] == "Not read"
    assert metrics["Radius of gyration"]["status"] == "Determined"


# ---------------------------------------------------------------------------
# As the pages show it.
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def browser():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        launched = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
        yield launched
        launched.close()


def _pages(browser, root: Path) -> dict:
    """What the Overview and the Analysis page say of the study."""
    from fastmdxplora.gui.server import start_dashboard_session

    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.set_default_timeout(60000)
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        page.goto(session.url + "#overview", wait_until="domcontentloaded")
        page.wait_for_selector("#overview-tiles .overview-tile")
        page.wait_for_selector("#overview-not-read")
        said = page.evaluate("""() => ({
            badges: Object.fromEntries([...document.querySelectorAll('#overview-tiles .overview-tile')]
                .map((t) => [t.dataset.tileAnalysis, t.querySelector('.tile-badge').textContent])),
            why: Object.fromEntries([...document.querySelectorAll('#overview-tiles .overview-tile')]
                .map((t) => [t.dataset.tileAnalysis, (t.querySelector('.tile-why') || {}).textContent || ''])),
            values: Object.fromEntries([...document.querySelectorAll('#overview-tiles .overview-tile')]
                .map((t) => [t.dataset.tileAnalysis, t.querySelector('.tile-value').textContent])),
            all: [...document.querySelectorAll('#overview-not-read details li')]
                .map((item) => item.textContent),
            counted: document.getElementById('overview-results-said').textContent,
            line: document.getElementById('overview-not-read').firstChild.textContent,
        })""")
        page.goto(session.url + "#analysis", wait_until="domcontentloaded")
        page.wait_for_function(
            "() => (document.getElementById('analysis-results-summary') || {}).textContent")
        said.update(page.evaluate("""() => ({
            summary: document.getElementById('analysis-results-summary').textContent,
            unread: (document.getElementById('analysis-not-read') || {}).textContent || '',
            chips: Object.fromEntries([...document.querySelectorAll(
                '#analysis-results-table tr[data-analysis]')].map((row) => [
                row.dataset.analysis,
                (row.querySelector('.analysis-results-status') || {}).textContent || ''])),
        })"""))
    finally:
        page.close()
        session.stop()
    assert not errors, errors
    return said


def test_the_pages_say_a_mean_not_read(browser, tmp_path):
    said = _pages(browser, _linked(tmp_path, "analysis/rmsd/options.json"))

    assert said["badges"] == {"rmsd": "Not read", "rg": "Determined"}
    assert said["why"]["rmsd"].startswith("Not read. analysis/rmsd/options.json was not read")
    assert said["counted"].startswith("1 of 1 mean determined, 1 not read · ")
    # The badge says it; no "record unavailable" in the value's place.
    assert said["values"]["rmsd"] == ""
    assert said["line"] == ("Not read: analysis/rmsd/options.json is a link to a file outside "
                            "the study, so what it says is not shown.")
    assert said["chips"] == {"rmsd": "Not read", "rg": "Determined"}
    assert said["summary"].startswith("1 determined, 1 not read. ")
    assert said["unread"] == said["line"]
    assert said["all"] == []


@pytest.mark.parametrize("record", ["analysis/analysis_manifest.json",
                                    "simulation/simulation_parameters.json"])
def test_the_pages_say_a_clock_not_read(browser, tmp_path, record):
    said = _pages(browser, _linked(tmp_path, record))

    for text in (said["line"], said["unread"]):
        assert f"Not read: {record} is a link to a file outside the study, so " in text
        clock = "their frames are numbered, not timed" in text
        assert clock is (record == "analysis/analysis_manifest.json")
    # The mean itself was read.
    assert said["badges"] == {"rg": "Determined"} and said["chips"]["rg"] == "Determined"


def test_a_long_list_not_read_is_counted_and_listed_in_full(browser, tmp_path):
    """Every record of a study linked out: the line names the first few and
    counts the rest, each listed with why under it."""
    from tests.test_the_study_pages_read_only_the_study import (
        _a_study_carried_on,
        _every_record_linked_out,
    )

    root = _a_study_carried_on(tmp_path)
    _every_record_linked_out(root, tmp_path)
    for name in ("rmsd", "rg"):
        # Kept in the study, so the Overview has its tiles.
        target = (root / "analysis" / name / "options.json").resolve()
        (root / "analysis" / name / "options.json").unlink()
        target.rename(root / "analysis" / name / "options.json")

    said = _pages(browser, root)

    assert len(said["all"]) > 8, said
    assert said["line"].startswith("Not read: ")
    assert f" and {len(said['all']) - 8} more are links to files outside the study, " \
        "so what they say is not shown." in said["line"]
    assert all(item.endswith(" (a link to a file outside the study)") for item in said["all"])
    assert "simulation/simulation_parameters.json (a link to a file outside the study)" \
        in said["all"]


def _said_on_the_overview(browser, root: Path, ready: str, script: str) -> dict:
    """What the Overview says of the study, once ``ready`` shows."""
    from fastmdxplora.gui.server import start_dashboard_session

    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.set_default_timeout(60000)
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        page.goto(session.url + "#overview", wait_until="domcontentloaded")
        page.wait_for_function(ready)
        said = page.evaluate(script)
    finally:
        page.close()
        session.stop()
    assert not errors, errors
    return said


def test_the_overview_says_its_means_not_read_where_the_join_was_not(browser, tmp_path):
    """A carried-on study whose record of the join was refused: the
    Thermodynamics card says its means were not read, naming the record."""
    from tests.test_a_record_refused_is_not_read_as_missing import _joined, _linked_out

    root = _joined(tmp_path)
    _linked_out(root, tmp_path, "joined/joined.json")
    line = _said_on_the_overview(
        browser, root, "() => document.getElementById('overview-charts-not-read')",
        "() => document.getElementById('overview-charts-not-read').textContent")
    assert line == ("Production means not read. joined/joined.json is a link to a file "
                    "outside the study, so the means over the production are not shown.")


def test_the_run_card_says_its_live_record_not_read(browser, tmp_path):
    """The run's live record linked out: the run card says it was not read
    and why, and the run's word is Not read, not a reason it kept none."""
    from tests.test_every_study_page_reads_only_the_study import _a_run_finished

    root = _a_run_finished(_study(tmp_path))
    live = root / "simulation" / "live_status.json"
    target = tmp_path / "elsewhere" / "live_status.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    live.rename(target)
    _link(live, target)
    said = _said_on_the_overview(
        browser, root,
        "() => (document.getElementById('live-absent-title') || {}).textContent"
        " === 'Live record not read'",
        """() => ({body: document.getElementById('live-absent-body').textContent,
                   word: document.getElementById('topbar-status-text').textContent,
                   pill: document.getElementById('health-pill').textContent})""")
    assert said["body"] == ("simulation/live_status.json was not read: it is a link to a "
                            "file outside the study.")
    assert said["word"] == "Not read"
    assert said["pill"] == "not read"


def test_the_tiles_not_shown_are_counted_of_those_not_read(browser, tmp_path):
    """Twelve analyses not read, nine tiles shown: said as nine of twelve,
    not as twelve and three more."""
    from tests.test_the_study_pages_read_only_the_study import N

    root = _study(tmp_path)
    names = ["rmsd", "rg", "sasa", "hbonds", "contacts", "end_to_end", "dihedrals", "rmsf",
             "ss", "pca", "moments_of_inertia", "native_contacts"]
    _write(root, "analysis/analysis_manifest.json", {
        "phase": "analysis", "n_frames": N, "load_kwargs": {"saving_interval_ps": 10.0},
        "results": {name: {"status": "ok"} for name in names}})
    for name in names:
        outside = _write(tmp_path, f"elsewhere/{name}.json", OUTSIDE["determined"])
        record = root / "analysis" / name / "options.json"
        if record.exists():
            record.unlink()
        _link(record, outside)
    counted = _said_on_the_overview(
        browser, root, "() => document.querySelector('#overview-tiles .overview-tile')",
        "() => document.getElementById('overview-results-said').textContent")
    assert counted.startswith("12 analyses not read; 9 of the 12 shown here · ")


#: A figure as the analyses save it (one pixel will do for a card).
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d4944415478da63f8cf000003000101a5f645400000000049454e44ae426082")


@pytest.mark.parametrize("refused", [
    ("analysis/rmsd/options.json",), ("manifest.json",),
    ("manifest.json", "analysis/rmsd/options.json")], ids=["options", "manifest", "both"])
def test_a_figure_s_panel_says_its_record_not_read(browser, tmp_path, refused):
    """A figure's record linked out: its panel says the record was not read
    and why, gives no command where the options were not read, and does not
    call who made it a release that did not record itself."""
    from fastmdxplora.gui.server import start_dashboard_session
    from tests.test_a_record_refused_is_not_read_as_missing import _linked_out

    root = _study(tmp_path)
    _write(root, "analysis/analysis_manifest.json", {
        "phase": "analysis", "n_frames": 40,
        "resolved": {"trajectory": str(root / "simulation" / "production.dcd"),
                     "topology": str(root / "simulation" / "topology.pdb")},
        "results": {"rmsd": {"status": "ok"}, "rg": {"status": "ok"}}})
    _write(root, "analysis/rmsd/options.json", {
        "analysis": "rmsd", "selection": "protein and name CA",
        "options": {"ref": 0, "align": True}})
    _write(root, "manifest.json", {"phases": [{"name": "analysis", "status": "ok",
                                               "produced_by": {"version": "9.9.9"}}]})
    (root / "analysis" / "rmsd" / "rmsd.png").write_bytes(PNG)
    _linked_out(root, tmp_path, *refused)

    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.set_default_timeout(60000)
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    chip = '.analysis-card[data-analysis="rmsd"] .figure-chip'
    panel = '.analysis-card[data-analysis="rmsd"] .figure-provenance'
    try:
        page.goto(session.url + "#analysis", wait_until="domcontentloaded")
        page.wait_for_selector(chip)
        page.click(chip)
        page.wait_for_selector(f"{panel}:not([hidden])")
        said = page.evaluate(f"""() => ({{
            text: document.querySelector('{panel}').textContent,
            rows: Object.fromEntries([...document.querySelectorAll('{panel} dt')]
                .map((dt) => [dt.textContent, dt.nextElementSibling.textContent])),
            note: [...document.querySelectorAll('{panel} .figure-provenance-note')]
                .map((p) => p.textContent).join(' '),
            command: !!document.querySelector('{panel} .figure-provenance-command'),
        }})""")
    finally:
        page.close()
        session.stop()
    assert not errors, errors

    assert "did not record itself" not in said["text"]
    for name in refused:
        assert f"{name} was not read: it is a link to a file outside the study." in said["note"]
    if "manifest.json" in refused:
        assert said["rows"]["Made by"] == "Not read"
    else:
        assert said["rows"]["Made by"] == "FastMDXplora 9.9.9"
    if "analysis/rmsd/options.json" in refused:
        assert not said["command"]
        assert "Selection" not in said["rows"] and "Options" not in said["rows"]
        assert "command that makes the figure again is not written" in said["note"]
    else:
        assert said["command"]
