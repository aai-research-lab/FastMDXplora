"""The Analysis page and the Overview say only what the study has.

"3 of 19 means determined, 11 more not shown" was said with ten hidden (the
RMSF tile counted as a mean). RMSF was offered a Convergence that said only
"rmsf recorded no mean over its frames", by its folder's name. A strand
fraction of 0 in every frame was "Not determined" from "1 independent
sample". Find an analysis suggested "residue 189", which finds nothing.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from tests.test_the_analysis_page_reads_as_a_whole import _analysis, _ar1, _manifest


def test_more_not_shown_counts_the_means_not_shown(tmp_path, monkeypatch):
    """"3 of 19 means determined, 11 more not shown" with 10 hidden: the
    RMSF tile, beyond those shown, was counted as a mean."""
    from fastmdxplora.gui import analysis_overview
    from fastmdxplora.gui.overview_view import MOST_TILES, _tiles

    rows = [{"analysis": f"m{k:02d}", "quantities": [
        {"key": "mean", "label": f"m{k:02d}", "value": 1.0, "determined": k % 2 == 0}]}
        for k in range(MOST_TILES + 3)]
    rows.append({"analysis": "rmsf", "quantities": []})
    monkeypatch.setattr(analysis_overview, "overview_of", lambda root: {"rows": rows})
    root = tmp_path / "study"
    _analysis(root, "rmsf", None, rows="1 0.05\n2 0.07\n3 0.06\n")
    said = _tiles(root)
    assert len(said["tiles"]) == MOST_TILES
    assert all(tile["kind"] == "mean" for tile in said["tiles"])
    assert said["means"] == MOST_TILES + 3
    assert said["more"] == 3


def test_convergence_is_said_by_the_page_s_name(tmp_path):
    from fastmdxplora.gui.analysis_overview import convergence_payload

    root = tmp_path / "study"
    _manifest(root, {"rmsf": {"status": "ok"}})
    _analysis(root, "rmsf", None, rows="1 0.05\n2 0.07\n3 0.06\n")
    said = convergence_payload(root, "rmsf")
    assert not said["ok"] and said["reason"].startswith("RMSF ")


def test_convergence_is_offered_only_where_there_is_a_mean():
    script = (Path(__file__).parents[1] / "src" / "fastmdxplora" / "gui" / "static"
              / "analysis-page.js").read_text(encoding="utf-8")
    assert "function hasAMean(name)" in script
    assert "if (!hasAMean(frame.getAttribute(\"data-series\"))) return;" in script
    assert "residue 189" not in script


def test_a_quantity_the_same_in_every_frame_is_said_so(tmp_path):
    from fastmdxplora.gui.analysis_overview import overview_of

    root = tmp_path / "study"
    _manifest(root, {"ss": {"status": "ok"}})
    _analysis(root, "ss", 0.4 + 0.01 * _ar1(400, 0.5, 1), unit="",
              others={"strand_fraction": np.zeros(400)})
    strand = next(q for row in overview_of(root)["rows"] for q in row["quantities"]
                  if q["key"] == "strand_fraction")
    assert strand["said"].startswith("0") and strand["said"].endswith(" in every frame")
    assert strand["samples"] is None and not strand["determined"]
    assert strand["from_frame"] is None and strand["from_ns"] is None
    assert "same in every frame" in strand["why"]


def test_a_power_is_raised_where_a_unit_is_said(tmp_path):
    """The Overview said "33.17 nm^3" beside "19.44 nm²"."""
    from fastmdxplora.gui.analysis_overview import overview_of
    from fastmdxplora.gui.report_dashboard import unit_as_written

    assert unit_as_written("nm^3") == "nm³"
    assert unit_as_written("kJ/mol/nm^2") == "kJ/mol/nm²"
    assert unit_as_written("ps^-1") == "ps⁻¹"
    root = tmp_path / "study"
    _manifest(root, {"rg": {"status": "ok"}})
    _analysis(root, "rg", 33.0 + 0.1 * _ar1(400, 0.5, 1), unit="nm^3")
    [quantity] = [q for row in overview_of(root)["rows"] for q in row["quantities"]]
    assert quantity["unit"] == "nm³" and quantity["said"].endswith(" nm³")


def test_where_the_mean_begins_is_said_as_the_overview_says_it(tmp_path):
    """The Analysis page said "from 0.0430 ns" where the Overview's tile
    said "from 43 ps"."""
    import shutil
    import subprocess

    node = shutil.which("node")
    if node is None:
        pytest.skip("no node")
    script = (Path(__file__).parents[1] / "src" / "fastmdxplora" / "gui" / "static"
              / "analysis-page.js").read_text(encoding="utf-8")
    start = script.index("  function sayLength(ns) {")
    end = script.index("\n  }\n", start) + 4
    probe = script[start:end] + (
        "\nconsole.log(JSON.stringify([sayLength(0.043), sayLength(0.0024), sayLength(2.5)]));")
    said = subprocess.run([node, "-e", probe], capture_output=True, text=True, check=True)
    assert json.loads(said.stdout) == ["43 ps", "2.4 ps", "2.5 ns"]
    tiny = subprocess.run([node, "-e", script[start:end] + "\nconsole.log(sayLength(1.3e-5));"],
                          capture_output=True, text=True, check=True)
    assert tiny.stdout.strip() == "0.013 ps"
    assert 'label: "\u03c4 " + sayLength(auto.tau_int_time)' in script
    assert '"from " + sayLength(quantity.from_ns)' in script


def test_convergence_gives_no_error_the_table_withholds(tmp_path):
    """The panel said "Recorded mean 0.0881 ± 0.00199 nm" under the
    table's "0.08811 nm, Not determined": the record held both an error and
    the reason it is not one."""
    from fastmdxplora.gui.analysis_overview import convergence_payload, overview_of

    root = tmp_path / "study"
    _manifest(root, {"rmsd": {"status": "ok"}})
    _analysis(root, "rmsd", 0.088 + 0.002 * _ar1(2000, 0.5, 3))
    options = root / "analysis" / "rmsd" / "options.json"
    record = json.loads(options.read_text(encoding="utf-8"))
    record["findings"]["mean"]["not_a_measurement"] = "The series is still drifting."
    options.write_text(json.dumps(record), encoding="utf-8")
    said = convergence_payload(root, "rmsd")["recorded"]
    [table] = [q for row in overview_of(root)["rows"] for q in row["quantities"]]
    assert said["standard_error"] is None and "±" not in said["said"]
    assert said["said"] == table["said"]
    script = (Path(__file__).parents[1] / "src" / "fastmdxplora" / "gui" / "static"
              / "analysis-page.js").read_text(encoding="utf-8")
    assert "sayLength(eq.start_time)" in script and "recorded.said" in script


def test_what_find_suggests_is_found(tmp_path):
    """The placeholder suggested "the ligand" on studies with none."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session
    from tests.test_the_drawing_scripts_run_in_a_browser import _write_study

    root = _write_study(tmp_path / "study")
    _manifest(root, {"rmsd": {"status": "ok"}, "rg": {"status": "ok"}, "rmsf": {"status": "ok"}})
    _analysis(root, "rmsd", 0.3 + 0.002 * _ar1(500, 0.5, 1))
    _analysis(root, "rg", 1.6 + 0.01 * _ar1(500, 0.5, 2))
    _analysis(root, "rmsf", None, rows="1 0.05\n2 0.07\n3 0.06\n")
    from tests.test_an_analysis_is_drawn_from_its_numbers import _figure

    for name in ("rmsd", "rg", "rmsf"):
        _figure(root / "analysis" / name / f"{name}.png")
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.set_default_timeout(60000)
            page.goto(session.url + "#analysis", wait_until="domcontentloaded")
            page.wait_for_selector("#analysis-index:not([hidden]) #analysis-filter")
            hint = page.get_attribute("#analysis-filter", "placeholder")
            found = {}
            for word in hint.rstrip("…").split(", "):
                page.fill("#analysis-filter", word)
                found[word] = page.text_content("#analysis-index-count")
            browser.close()
    finally:
        session.server.shutdown()
    assert hint and "ligand" not in hint.lower()
    assert found and all(not said.startswith("0 of") for said in found.values()), found
