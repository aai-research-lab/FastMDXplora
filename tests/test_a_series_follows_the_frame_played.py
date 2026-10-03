"""A series is plotted under the Viewer's transport and follows the frame.

The trajectory was played with a slider that gave a frame number and a time,
and where the RMSD rose had to be found on the Analysis page and carried
back. One of the study's series over time is now plotted under the
transport: a line marks the frame shown and moves as the frames play, its
value is given beside it, and a click along the series shows that frame.
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

import pytest

from fastmdxplora.gui.series import series_over_time

md = pytest.importorskip("mdtraj")


def _as_said(value: float) -> str:
    """A number as the page writes it (frame-series.js `format`)."""
    magnitude = abs(value)
    if magnitude == 0:
        return "0"
    if magnitude >= 1e5 or magnitude < 1e-3:
        return f"{value:.2e}"
    if magnitude >= 100:
        return f"{value:.1f}"
    if magnitude >= 1:
        return f"{value:.3f}"
    return f"{value:.3g}"


def _analysis(root: Path, name: str, values: list[float], mean: dict | None) -> None:
    folder = root / "analysis" / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.dat").write_text(
        f"# {name}\n" + "".join(f"{v:.6f}\n" for v in values), encoding="utf-8")
    (folder / "options.json").write_text(json.dumps(
        {"analysis": name, "findings": {} if mean is None else {"mean": mean}}),
        encoding="utf-8")


def test_the_series_over_time_are_listed_in_order(tmp_path) -> None:
    _analysis(tmp_path, "rg", [1.0, 1.1], None)
    _analysis(tmp_path, "rmsd", [0.1, 0.2], None)
    _analysis(tmp_path, "rmsf", [0.1, 0.2], None)                # over residues
    _analysis(tmp_path, "end_to_end", [3.0, 3.1], {"mean": 3.05, "n_frames": 2})
    _analysis(tmp_path, "contacts", [3.0, 3.1], None)            # neither
    (tmp_path / "analysis" / "notes").mkdir()                    # wrote no numbers
    said = series_over_time(tmp_path)
    assert said["ok"]
    assert [s["analysis"] for s in said["series"]] == ["rmsd", "rg", "end_to_end"]
    assert said["series"][0] == {"analysis": "rmsd", "label": "RMSD", "unit": "nm"}
    assert series_over_time(tmp_path / "nothing") == {"ok": True, "series": []}


@pytest.fixture(scope="module")
def study(tmp_path_factory) -> Path:
    from tests.test_the_cartoon_is_dssp_of_each_frame import _helical_study

    root = _helical_study(tmp_path_factory.mktemp("series") / "study", analyse=False)
    from fastmdxplora.analysis import AnalysisOrchestrator

    AnalysisOrchestrator(str(root / "simulation" / "production.dcd"),
                         str(root / "simulation" / "trajectory_topology.pdb"),
                         output_dir=str(root / "analysis")).run(include=["rmsd", "rg"])
    return root


def test_the_series_is_plotted_under_the_frames_and_follows_them(study) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    base = session.url.rstrip("/")
    try:
        listed = json.loads(urllib.request.urlopen(base + "/api/series", timeout=30).read())
        rmsd = json.loads(urllib.request.urlopen(base + "/api/series?analysis=rmsd",
                                                 timeout=30).read())
        rg = json.loads(urllib.request.urlopen(base + "/api/series?analysis=rg",
                                               timeout=30).read())
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.set_default_timeout(60000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url + "#viewer", wait_until="domcontentloaded")
            if not page.evaluate("() => !!document.createElement('canvas').getContext('webgl')"):
                pytest.skip("this browser has no WebGL, so the viewer cannot render")
            page.wait_for_function("() => window.FastMDXMoleculeViewer"
                                   " && window.FastMDXMoleculeViewer.STATE.model")
            page.evaluate("async () => window.FastMDXMoleculeViewer.loadPlayback("
                          "await (await fetch('/api/frames-info')).json())")
            page.wait_for_selector("#frame-series:not([hidden]) .frame-series-line")
            options = page.eval_on_selector_all("#frame-series-pick option",
                                                "(all) => all.map((o) => o.textContent)")
            seen = {}
            for frame in (0, 3, 5):
                page.evaluate(f"() => window.dispatchEvent(new CustomEvent("
                              f"'dashboard:trajectory-seek', {{detail: {{frame: {frame}}}}}))")
                page.wait_for_function(
                    f"() => document.getElementById('frame-series-chart')"
                    f".getAttribute('data-frame') === '{frame}'")
                seen[frame] = {
                    "said": page.text_content("#frame-series-value"),
                    "x": float(page.get_attribute(".frame-series-here", "x1")),
                }
            # A click along the series, near its start, shows the first frame.
            page.locator(".frame-series-svg").click(position={"x": 50, "y": 38})
            page.wait_for_function("() => document.getElementById('traj-slider').value === '0'")
            # Another series is chosen and plotted.
            page.select_option("#frame-series-pick", "rg")
            page.wait_for_function("() => window.FastMDXFrameSeries.state.data"
                                   " && window.FastMDXFrameSeries.state.data.analysis === 'rg'")
            rg_said = page.text_content("#frame-series-value")
            browser.close()
    finally:
        session.server.shutdown()
    assert [s["analysis"] for s in listed["series"]] == ["rmsd", "rg"]
    assert options == ["RMSD (nm)", "Radius of gyration (nm)"]
    assert rmsd["linked"] and rmsd["frames"] == list(range(6))
    for frame, shown in seen.items():
        assert shown["said"] == f"{_as_said(rmsd['y'][frame])} nm"
    # The line moves along with the frames.
    assert seen[0]["x"] < seen[3]["x"] < seen[5]["x"]
    assert rg_said == f"{_as_said(rg['y'][0])} nm"
    assert errors == []
