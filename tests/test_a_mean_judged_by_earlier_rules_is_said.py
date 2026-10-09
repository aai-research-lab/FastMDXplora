"""A mean an earlier version judged is said as judged by its rules.

A study analysed before a mean still drifting was withheld keeps the
verdicts it was given: the Overview and the Analysis page read "Determined"
from its records, while the report written since (or before, by the drift
check it always had) says the same mean is still moving. Nothing on the
pages said the records came from older rules. Each mean is now recorded
with the rules that judged it, and a page reading a determined mean
without them says the study was analysed by an earlier version and that
Analyze again judges it by this one's.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from tests.test_the_analysis_page_reads_as_a_whole import _analysis, _ar1, _manifest


def _without_rules(root: Path, name: str, key: str = "mean") -> None:
    path = root / "analysis" / name / "options.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    record["findings"][key].pop("rules", None)
    path.write_text(json.dumps(record), encoding="utf-8")


def _resolved(seed: int) -> np.ndarray:
    return 1.6 + 0.01 * _ar1(4000, 0.5, seed)


def test_a_mean_is_recorded_with_the_rules_that_judged_it():
    from fastmdxplora.statistics import MEAN_RULES, mean_record, summarise

    record = mean_record(_resolved(1))
    assert record["rules"] == MEAN_RULES >= 2
    equilibrated, _ = summarise(_resolved(2))
    assert equilibrated.as_record()["rules"] == MEAN_RULES


def test_a_determined_mean_judged_before_these_rules_is_named(tmp_path):
    from fastmdxplora.gui.analysis_overview import overview_of
    from fastmdxplora.gui.overview_view import _tiles

    root = tmp_path / "study"
    _manifest(root, {"rg": {"status": "ok"}, "rmsd": {"status": "ok"}})
    _analysis(root, "rg", _resolved(1))
    _analysis(root, "rmsd", 0.3 + 0.002 * _ar1(4000, 0.5, 3))
    _without_rules(root, "rg")
    said = overview_of(root)
    rg = next(row for row in said["rows"] if row["analysis"] == "rg")
    assert rg["mean"]["determined"] and rg["mean"]["judged_earlier"]
    assert said["judged_earlier"] == [rg["title"]]
    assert _tiles(root)["judged_earlier"] == [rg["title"]]


def test_a_mean_judged_by_these_rules_or_withheld_is_not_named(tmp_path):
    from fastmdxplora.gui.analysis_overview import overview_of

    root = tmp_path / "study"
    _manifest(root, {"rg": {"status": "ok"}, "rmsd": {"status": "ok"}})
    _analysis(root, "rg", _resolved(1))
    # Too short against its correlation: withheld, and the rules since have
    # only withheld more, so an older record of it is no other verdict.
    _analysis(root, "rmsd", 1.6 + 0.01 * _ar1(300, 0.95, 2, transient=3.0))
    _without_rules(root, "rmsd")
    said = overview_of(root)
    rmsd = next(row for row in said["rows"] if row["analysis"] == "rmsd")
    assert not rmsd["mean"]["determined"]
    assert said["judged_earlier"] == []


def test_a_thermodynamic_mean_judged_before_these_rules_is_named(tmp_path):
    from fastmdxplora.gui.analysis_overview import overview_of
    from fastmdxplora.statistics import summarise

    root = tmp_path / "study"
    _manifest(root, {"thermodynamics": {"status": "ok"}})
    folder = root / "analysis" / "thermodynamics"
    folder.mkdir(parents=True)
    equilibrated, reason = summarise(300.0 + _ar1(4000, 0.5, 4))
    assert reason is None
    entry = {"units": "K", **equilibrated.as_record()}
    entry.pop("rules", None)
    (folder / "options.json").write_text(json.dumps({
        "analysis": "thermodynamics",
        "findings": {"thermodynamics": {"samples": 4000, "temperature": entry}}}),
        encoding="utf-8")
    said = overview_of(root)
    assert said["judged_earlier"] == [said["rows"][0]["title"]]


def test_the_pages_say_it(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session
    from tests.test_an_analysis_is_drawn_from_its_numbers import _figure
    from tests.test_the_drawing_scripts_run_in_a_browser import _write_study

    root = _write_study(tmp_path / "study")
    _manifest(root, {"rg": {"status": "ok"}, "rmsd": {"status": "ok"}})
    _analysis(root, "rg", _resolved(1))
    _analysis(root, "rmsd", 0.3 + 0.002 * _ar1(4000, 0.5, 3))
    _without_rules(root, "rg")
    for name in ("rg", "rmsd"):
        _figure(root / "analysis" / name / f"{name}.png")
    from fastmdxplora.gui.analysis_overview import overview_of

    titles = {row["analysis"]: row["title"] for row in overview_of(root)["rows"]}
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.set_default_timeout(60000)
            page.goto(session.url + "#analysis", wait_until="domcontentloaded")
            page.wait_for_function(
                "() => /determined/.test((document.getElementById("
                "'analysis-results-summary') || {}).textContent || '')")
            summary = page.text_content("#analysis-results-summary")
            page.goto(session.url + "#overview", wait_until="domcontentloaded")
            page.wait_for_selector("#overview-results-said .overview-judged-earlier",
                                   state="attached")
            overview = page.text_content("#overview-results-said")
            named = page.get_attribute("#overview-results-said .overview-judged-earlier",
                                       "data-analyses")
            browser.close()
    finally:
        session.server.shutdown()
    earlier = summary[summary.index("for its error.") + len("for its error."):]
    assert earlier.strip().startswith("The study was analysed by an earlier version"), summary
    assert "Analyze again to judge the means of " + titles["rg"] + " by this version's rules." \
        in earlier
    assert titles["rmsd"] not in earlier
    assert "Analysed by an earlier version (" + titles["rg"] + ")" in overview, overview
    assert named == titles["rg"]


def test_a_title_two_analyses_share_is_named_once(tmp_path):
    from fastmdxplora.gui.analysis_overview import overview_of

    root = tmp_path / "study"
    _manifest(root, {"pl_contacts": {"status": "ok"}, "contacts": {"status": "ok"}})
    _analysis(root, "pl_contacts", 12.0 + _ar1(4000, 0.5, 5), unit="")
    _analysis(root, "contacts", 14.0 + _ar1(4000, 0.5, 6), unit="")
    _without_rules(root, "pl_contacts")
    _without_rules(root, "contacts")
    said = overview_of(root)
    titles = {row["title"] for row in said["rows"]}
    assert len(titles) == 1
    assert said["judged_earlier"] == list(titles)


def test_the_agent_says_it_too(tmp_path):
    from fastmdxplora.gui.records_answer import _means

    root = tmp_path / "study"
    _manifest(root, {"rg": {"status": "ok"}, "rmsd": {"status": "ok"}})
    _analysis(root, "rg", _resolved(1))
    _analysis(root, "rmsd", 0.3 + 0.002 * _ar1(4000, 0.5, 3))
    _without_rules(root, "rg")
    lines = _means(root)
    earlier = [line for line in lines if "earlier version" in line]
    assert len(earlier) == 1 and "Analyze again" in earlier[0], lines
    assert earlier[0].startswith("Radius of gyration") or "gyration" in earlier[0].lower()
