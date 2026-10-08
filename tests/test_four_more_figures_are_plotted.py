"""Thermodynamics, the moments of inertia, and the ligand's contacts and
interactions are plotted from their numbers, in the page's colours.

Asked for (10-06): on the Analysis page these four were still the pictures
their analyses wrote. Each is plotted now as its analysis plots it: how
tightly each thermodynamic observable is known, a bar each; I1, I2 and I3
over time; the residues most often in contact with the ligand; and the
interactions that hold it, hollow where seen too few times, with their
errors. The value is under the pointer, and the picture a click away.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from fastmdxplora.gui.figure_data import MOST_BARS, figure_payload

INTERVAL_PS = 10.0
FRAMES = 40


def _write(root: Path) -> Path:
    """The four analyses' files as each writes them."""
    folder = root / "analysis"
    for name in ("thermodynamics", "moments_of_inertia", "pl_contacts", "pl_interactions"):
        (folder / name).mkdir(parents=True, exist_ok=True)
    (folder / "analysis_manifest.json").write_text(json.dumps({
        "load_kwargs": {"stride": 1, "first": None, "saving_interval_ps": INTERVAL_PS},
        "results": {}}), encoding="utf-8")
    # thermodynamics.dat, as save_data writes it, and its units recorded.
    (folder / "thermodynamics" / "thermodynamics.dat").write_text(
        "observable,mean,standard_error,effective_samples,standard_deviation\n"
        "potential_energy,-506500.0,25.3,30.0,140.0\n"
        "temperature,300.1,0.6,40.0,3.8\n", encoding="utf-8")
    (folder / "thermodynamics" / "options.json").write_text(json.dumps({"findings": {
        "thermodynamics": {
            "potential_energy": {"units": "kJ/mol", "mean": -506500.0},
            "temperature": {"units": "K", "mean": 300.1},
            "density": {"value": 1.02, "units": "g/mL", "not_a_measurement": "The box ..."},
            "kinetic_energy": {"units": "kJ/mol", "not_a_measurement": "Not long enough."},
        }}}), encoding="utf-8")
    # moments_of_inertia.dat: whitespace, its layout on a # line.
    moments = np.column_stack([np.linspace(100, 110, FRAMES), np.linspace(200, 190, FRAMES),
                               np.full(FRAMES, 300.0)])
    np.savetxt(folder / "moments_of_inertia" / "moments_of_inertia.dat", moments, fmt="%.8e",
               header="moments_of_inertia: whitespace-delimited, no column header. "
                      "Read with np.loadtxt(path).")
    # pl_contacts_per_residue.csv, the most often first, 25 residues.
    (folder / "pl_contacts" / "pl_contacts_per_residue.csv").write_text(
        "residue,contact_frequency\n" + "".join(
            f"RES{k},{1 - k / 30:.3f}\n" for k in range(25)), encoding="utf-8")
    # pl_interactions.dat, as the pair table is written.
    (folder / "pl_interactions" / "pl_interactions.dat").write_text(
        "kind,ligand_atom,protein_atom,residue,ligand_atom_name,protein_atom_name,"
        "frames_present,frames_total,occupancy,episodes,standard_error,well_sampled\n"
        "hydrophobic,3,40,TRP215,C4,CZ2,12,40,0.3,2,0.12,False\n"
        "hydrogen_bond,1,20,ASP189,N1,OD1,38,40,0.95,7,0.03,True\n"
        "salt_bridge,1,21,ASP189,N1,OD2,30,40,0.75,9,,True\n", encoding="utf-8")
    return root


@pytest.fixture
def study(tmp_path: Path) -> Path:
    return _write(tmp_path / "study")


def _get(root: Path, name: str) -> dict:
    payload = figure_payload(root, f"analysis/{name}/{name}.png")
    assert payload["ok"], payload
    return payload


def test_thermodynamics_is_how_tightly_each_is_known(study) -> None:
    got = _get(study, "thermodynamics")
    assert got["kind"] == "hbars" and got["x_label"] == "Standard error, per cent of the mean"
    bars = {b["label"]: b for b in got["bars"]}
    assert set(bars) == {"Potential energy", "Temperature"}
    assert bars["Temperature"]["value"] == pytest.approx(0.6 / 300.1 * 100)
    assert bars["Potential energy"]["said"][1] == "-506500 ± 25 kJ/mol"
    assert "30 independent samples" in bars["Potential energy"]["said"]
    # What was not measured is said, and why.
    assert got["note"] == ("Density: held constant by the run, not sampled; "
                           "Kinetic energy: not determined")


def test_the_moments_run_over_time(study) -> None:
    got = _get(study, "moments_of_inertia")
    assert got["kind"] == "lines" and [s["label"] for s in got["series"]] == ["I₁", "I₂", "I₃"]
    assert got["series"][0]["y"][0] == pytest.approx(100.0)
    assert got["series"][2]["y"] == [300.0] * FRAMES
    # Frame k analysed was written at (k + 1) saving intervals.
    assert got["frames"][:2] == [0, 1] and got["x"][1] == pytest.approx(0.02)
    assert got["x_label"] == "Time (ns)" and got["unit"] == "amu nm²"


def test_the_contacts_are_the_residues_most_often_touching(study) -> None:
    got = _get(study, "pl_contacts")
    assert got["kind"] == "hbars" and (got["x_low"], got["x_high"]) == (0.0, 1.0)
    assert len(got["bars"]) == MOST_BARS and got["bars"][0]["label"] == "RES0"
    assert got["bars"][1]["said"] == ["RES1", "in contact in 97% of frames"]
    assert got["note"] == f"The {MOST_BARS} most often in contact of 25 residues."


def test_the_interactions_are_atom_pairs_the_thin_ones_hollow(study) -> None:
    got = _get(study, "pl_interactions")
    labels = [b["label"] for b in got["bars"]]
    assert labels == ["hydrogen_bond  ASP189 OD1–N1", "salt_bridge  ASP189 OD2–N1",
                      "hydrophobic  TRP215 CZ2–C4"]
    first, second, thin = got["bars"]
    assert (first["value"], first["error"], first["hollow"]) == (0.95, 0.03, False)
    assert second["error"] is None and thin["hollow"]
    assert first["said"] == ["hydrogen_bond  ASP189 OD1–N1",
                             "present in 95% of frames (38 of 40)", "± 0.03",
                             "formed 7 times"]
    assert got["note"] == "1 hollow: fewer than five separate observations."


def test_a_figure_without_its_numbers_keeps_its_picture(tmp_path) -> None:
    (tmp_path / "analysis" / "pl_contacts").mkdir(parents=True)
    got = figure_payload(tmp_path, "analysis/pl_contacts/pl_contacts.png")
    assert not got["ok"] and got["reason"] == "the analysis wrote no numbers for it"


def _tip_over(page, analysis, across, up, down=None):
    """The tip of an analysis's figure with the pointer at a share of its
    width (`across`) and of its height (`up`), or `down` pixels from its
    top. A figure plotted again takes its tip with it, so the pointer is
    moved over the figure as it is now until its tip says something, for
    20 s in all; then what was last in the way is said."""
    import time

    from playwright.sync_api import Error

    card = f'.analysis-card[data-analysis="{analysis}"]'
    until = time.monotonic() + 20

    def left():
        return max(1, int((until - time.monotonic()) * 1000))

    last = "no tip shown"
    while time.monotonic() < until:
        try:
            # Plotted again while it is looked at, the figure whose box was
            # read is gone: looked at again.
            chart = page.locator(f"{card} .figure-chart svg")
            chart.scroll_into_view_if_needed(timeout=left())
            box = chart.bounding_box(timeout=left())
            if box:
                y = box["y"] + (down if down is not None else box["height"] * up)
                page.mouse.move(box["x"] + box["width"] * across + 1, y)
                page.mouse.move(box["x"] + box["width"] * across, y)
                tip = page.text_content(f"{card} .series-tip", timeout=left()) or ""
                if tip:
                    return tip
        except Error as error:
            last = str(error).splitlines()[0]
        page.wait_for_timeout(min(250, left()))
    raise AssertionError(f"{analysis}: {last}")


def test_each_is_plotted_on_the_analysis_page(tmp_path) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session
    from tests.test_an_analysis_is_drawn_from_its_numbers import _figure

    root = _write(tmp_path / "study")
    names = ("thermodynamics", "moments_of_inertia", "pl_contacts", "pl_interactions")
    manifest = json.loads((root / "analysis" / "analysis_manifest.json").read_text())
    manifest["results"] = {name: {"status": "ok"} for name in names}
    (root / "analysis" / "analysis_manifest.json").write_text(json.dumps(manifest))
    for name in names:
        _figure(root / "analysis" / name / f"{name}.png")
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.set_default_timeout(60000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url + "#analysis", wait_until="domcontentloaded")
            page.wait_for_function("() => document.querySelectorAll('.figure-chart svg').length === 4")
            kinds = page.eval_on_selector_all(
                ".figure-chart", "(all) => all.map((c) => c.dataset.figureKind).sort()")
            hollow = page.eval_on_selector_all(
                '.analysis-card[data-analysis="pl_interactions"] .figure-bar',
                "(all) => all.map((b) => b.getAttribute('fill') === 'none')")
            names_shown = page.eval_on_selector_all(
                '.analysis-card[data-analysis="pl_contacts"] .figure-chart svg > text',
                "(all) => all.map((t) => t.textContent)")
            # A figure is plotted again when its width changes (a scroll bar
            # coming, the window), and its tip goes with the old plot: CI run
            # #705 read it empty, the figure plotted again under the pointer.
            # The window is narrowed by more than the 4 px a figure is
            # plotted again for, so each figure is plotted again, perhaps as
            # it is first hovered; so can it be later (a scroll bar, its card
            # made again as results come). The tip is read from the figure as
            # it is, the pointer moved over it again until it shows.
            page.set_viewport_size({"width": 1400, "height": 900})
            tip = _tip_over(page, "moments_of_inertia", 0.5, 0.5)
            said = _tip_over(page, "pl_interactions", 0.5, None, down=20)
            note = page.text_content('.analysis-card[data-analysis="pl_interactions"] .figure-note')
            browser.close()
    finally:
        session.server.shutdown()
    assert not errors, errors
    assert kinds == ["hbars", "hbars", "hbars", "lines"]
    assert hollow == [False, False, True]
    assert "RES0" in names_shown and "RES19" in names_shown and "RES20" not in names_shown
    assert "I₁" in tip and "I₃ 300.0 amu nm²" in tip and "ns" in tip
    assert said.startswith("hydrogen_bond  ASP189 OD1–N1") and "95% of frames" in said
    assert note == "1 hollow: fewer than five separate observations."
