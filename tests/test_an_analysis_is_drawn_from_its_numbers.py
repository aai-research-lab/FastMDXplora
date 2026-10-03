"""An analysis is drawn from its numbers, and its points lead to their frames.

The Analysis page showed each analysis as the picture its figure is: a line
from which neither a value nor the frame behind it could be taken. The page
now draws each series from the data file the figure was drawn from, with the
frames the analysis left out as equilibration and the mean of the rest with
its error; pointing at the line gives the value, the time and the frame, and
choosing a point opens that frame in the viewer. A point of a per-residue
profile shows that residue in the structure.

The frame of each point is worked out as the analysis loaded the trajectory
(its stride and first frame), and its time as the loader set it: frame k of
the file was written at k + 1 saving intervals.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fastmdxplora.gui.series import MOST_POINTS, series_payload


def _analysis(root: Path, name: str, text: str, mean: dict | None = None) -> None:
    folder = root / "analysis" / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.dat").write_text(text, encoding="utf-8")
    _figure(folder / f"{name}.png")
    (folder / "options.json").write_text(json.dumps(
        {"analysis": name, "findings": {} if mean is None else {"mean": mean}}),
        encoding="utf-8")


def _figure(path: Path) -> None:
    """A figure as small as a picture can be."""
    import struct
    import zlib

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

    rows = b"".join(b"\x00" + b"\x80\x80\x80" * 65 for _ in range(42))
    path.write_bytes(b"\x89PNG\r\n\x1a\n"
                     + chunk(b"IHDR", struct.pack(">IIBBBBB", 65, 42, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def _manifest(root: Path, **load) -> None:
    (root / "analysis").mkdir(parents=True, exist_ok=True)
    (root / "analysis" / "analysis_manifest.json").write_text(json.dumps({
        "load_kwargs": {"stride": None, "first": None, "saving_interval_ps": None, **load},
        "resolved": {"trajectory": str(root / "simulation" / "production.dcd")},
    }), encoding="utf-8")


def _series(n: int) -> str:
    return "# rmsd\n" + "".join(f"{0.1 + 0.001 * i:.6f}\n" for i in range(n))


class TestTheSeries:

    def test_the_time_and_frame_of_each_point(self, tmp_path) -> None:
        _manifest(tmp_path, stride=5, first=2, saving_interval_ps=10.0)
        _analysis(tmp_path, "rmsd", _series(4), {
            "mean": 0.1025, "standard_error": 0.0004, "effective_samples": 12.0,
            "discard": 1, "n_frames": 4})
        data = series_payload(tmp_path, "rmsd")
        # Analysis frame i is frame (2 + i) of the strided file, which is
        # frame 5 * (2 + i) of the trajectory, written at (that + 1) * 10 ps.
        assert data["frames"] == [10, 15, 20, 25]
        assert data["x"] == [0.11, 0.16, 0.21, 0.26]
        assert (data["x_label"], data["unit"], data["label"]) == ("Time (ns)", "nm", "RMSD")
        assert data["mean"] == {"value": 0.1025, "error": 0.0004, "effective_samples": 12.0,
                                "discard": 1, "from_x": 0.16, "not_a_measurement": None}

    def test_without_a_clock_the_axis_is_the_frame(self, tmp_path) -> None:
        _manifest(tmp_path)
        _analysis(tmp_path, "rg", _series(3))
        data = series_payload(tmp_path, "rg")
        assert (data["x_label"], data["x"]) == ("Frame", [0.0, 1.0, 2.0])

    def test_a_table_with_a_header_and_a_frame_column(self, tmp_path) -> None:
        _manifest(tmp_path, saving_interval_ps=1.0)
        _analysis(tmp_path, "hbonds", "frame,n_hbonds\n0,4\n1,5\n2,3\n")
        assert series_payload(tmp_path, "hbonds")["y"] == [4.0, 5.0, 3.0]

    def test_a_mean_recorded_over_other_frames_is_not_drawn(self, tmp_path) -> None:
        """A series thinned or rewritten since its findings were recorded."""
        _manifest(tmp_path)
        _analysis(tmp_path, "rmsd", _series(5), {"mean": 0.1, "discard": 1, "n_frames": 9})
        assert series_payload(tmp_path, "rmsd")["mean"] is None

    def test_a_long_series_is_thinned_and_says_so(self, tmp_path) -> None:
        _manifest(tmp_path, saving_interval_ps=1.0)
        n = 3 * MOST_POINTS + 7
        _analysis(tmp_path, "rmsd", _series(n))
        data = series_payload(tmp_path, "rmsd")
        assert data["thinned"] == 4
        assert len(data["y"]) <= MOST_POINTS and data["frames"][1] == 4

    def test_a_profile_over_residues_of_several_chains(self, tmp_path) -> None:
        _analysis(tmp_path, "rmsf", "chain,residue,rmsf_nm\nA,12,0.10\nA,13,0.12\nB,12,0.30\n")
        data = series_payload(tmp_path, "rmsf")
        assert data["kind"] == "residue"
        assert data["labels"] == ["A:12", "A:13", "B:12"]
        assert data["residues"][2] == {"chain": "B", "resi": 12}
        assert data["y"] == [0.10, 0.12, 0.30]

    def test_a_profile_of_one_chain(self, tmp_path) -> None:
        _analysis(tmp_path, "rmsf", "1.0 0.2\n2.0 0.3\n")
        assert series_payload(tmp_path, "rmsf")["labels"] == ["1", "2"]

    def test_a_column_of_values_alone_is_numbered_from_one(self, tmp_path) -> None:
        _analysis(tmp_path, "rmsf", "# rmsf_nm\n0.2\n0.3\n")
        data = series_payload(tmp_path, "rmsf")
        assert data["labels"] == ["1", "2"]
        assert data["y"] == [0.2, 0.3]


class TestWhatIsLinked:

    def test_the_production_trajectory_is_what_the_viewer_plays(self, tmp_path) -> None:
        (tmp_path / "simulation").mkdir()
        (tmp_path / "simulation" / "production.dcd").write_bytes(b"x")
        _manifest(tmp_path, saving_interval_ps=1.0)
        _analysis(tmp_path, "rmsd", _series(3))
        assert series_payload(tmp_path, "rmsd")["linked"] is True

    def test_an_extended_study_plays_its_joined_trajectory(self, tmp_path) -> None:
        (tmp_path / "joined").mkdir()
        (tmp_path / "joined" / "joined.json").write_text("{}", encoding="utf-8")
        (tmp_path / "joined" / "production.dcd").write_bytes(b"x")
        _manifest(tmp_path, saving_interval_ps=1.0)
        _analysis(tmp_path, "rmsd", _series(3))
        assert series_payload(tmp_path, "rmsd")["linked"] is False

    def test_a_path_that_cannot_be_resolved_is_not(self, tmp_path, monkeypatch) -> None:
        (tmp_path / "analysis").mkdir()
        (tmp_path / "analysis" / "analysis_manifest.json").write_text(json.dumps(
            {"load_kwargs": {}, "resolved": {"trajectory": "md.dcd"}}), encoding="utf-8")
        _analysis(tmp_path, "rmsd", _series(3))

        def refuse(self, strict=False):
            raise OSError("too many levels of symbolic links")

        monkeypatch.setattr(Path, "resolve", refuse)
        assert series_payload(tmp_path, "rmsd")["linked"] is False

    def test_a_trajectory_from_elsewhere_is_not(self, tmp_path) -> None:
        (tmp_path / "analysis").mkdir()
        (tmp_path / "analysis" / "analysis_manifest.json").write_text(json.dumps(
            {"load_kwargs": {}, "resolved": {"trajectory": "/elsewhere/md.xtc"}}),
            encoding="utf-8")
        _analysis(tmp_path, "rmsd", _series(3))
        assert series_payload(tmp_path, "rmsd")["linked"] is False


class TestWhatIsRefused:

    @pytest.mark.parametrize("name", ["../simulation", "RMSD", "", "a" * 80, "rmsd/../x"])
    def test_a_name_that_is_not_an_analysis(self, tmp_path, name) -> None:
        assert series_payload(tmp_path, name) == {"ok": False, "reason": "not an analysis name"}

    def test_an_analysis_that_is_not_a_series(self, tmp_path) -> None:
        _analysis(tmp_path, "ss", "0 H H C\n")
        assert series_payload(tmp_path, "ss")["ok"] is False

    def test_one_that_wrote_nothing(self, tmp_path) -> None:
        assert "wrote no data file" in series_payload(tmp_path, "rmsd")["reason"]

    def test_one_whose_file_holds_no_numbers(self, tmp_path) -> None:
        _analysis(tmp_path, "rmsd", "# time rmsd\nframe value\n")
        assert "holds no numbers" in series_payload(tmp_path, "rmsd")["reason"]

    def test_a_file_that_cannot_be_read_gives_no_rows(self, tmp_path) -> None:
        from fastmdxplora.gui.series import _rows

        assert _rows(tmp_path) == []


def test_the_route_is_answered_to_a_viewer_beyond_this_machine() -> None:
    """Watching a run needs it, and it reads only what the run wrote."""
    from fastmdxplora.gui.server import GETS_ANSWERED_BEYOND_LOOPBACK

    assert "/api/series" in GETS_ANSWERED_BEYOND_LOOPBACK


# --------------------------------------------------------------------------
# In a browser
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def dashboard(tmp_path_factory):
    pytest.importorskip("playwright.sync_api")
    pytest.importorskip("mdtraj")
    from fastmdxplora.gui.server import start_dashboard_session
    from tests.test_the_drawing_scripts_run_in_a_browser import FRAMES, _write_study

    root = _write_study(tmp_path_factory.mktemp("series") / "study")
    _manifest(root, saving_interval_ps=2.0)
    _analysis(root, "rmsd", _series(FRAMES), {
        "mean": 0.112, "standard_error": 0.002, "effective_samples": 11.0,
        "discard": 5, "n_frames": FRAMES})
    _analysis(root, "rmsf", "".join(f"{i + 1} {0.05 + 0.01 * i:.4f}\n" for i in range(10)))
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
    yield session
    session.server.shutdown()


@pytest.fixture
def page(dashboard):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
        opened = browser.new_page(viewport={"width": 1440, "height": 900})
        errors: list[str] = []
        opened.on("pageerror", lambda error: errors.append(str(error)))
        opened.errors = errors
        opened.goto(dashboard.url + "#analysis", wait_until="domcontentloaded")
        opened.wait_for_function(
            "() => document.querySelectorAll('.series-chart svg').length >= 2", timeout=30000)
        # Past the first layout, when a chart may be drawn again at its width.
        opened.wait_for_timeout(500)
        yield opened
        browser.close()


def _point_at(page, analysis: str, share: float) -> None:
    box = page.locator(f'.series-chart[data-analysis="{analysis}"] svg').bounding_box()
    # Inside the plotting area, which starts after the y axis's labels.
    x = box["x"] + 64 + (box["width"] - 64 - 28) * share
    page.mouse.move(x, box["y"] + box["height"] / 2)


class TestTheChart:

    def test_it_draws_what_the_analysis_settled_on(self, page) -> None:
        chart = page.locator('.series-chart[data-analysis="rmsd"]')
        assert chart.locator(".series-line").count() == 1
        assert chart.locator(".series-excluded").count() == 1
        assert chart.locator(".series-mean").count() == 1
        assert chart.locator(".series-error").count() == 1
        label = chart.locator("svg").get_attribute("aria-label")
        assert label.startswith("RMSD, 20 points; mean 0.112 nm plus or minus 0.00200")
        assert page.errors == []

    def test_pointing_gives_the_value_time_and_frame(self, page) -> None:
        _point_at(page, "rmsd", 0.0)
        tip = page.locator('.series-chart[data-analysis="rmsd"] .series-tip')
        assert tip.is_visible()
        # Frame 0 was written after one 2 ps interval.
        assert tip.text_content().startswith("0.100 nm · 0.00200 ns · frame 0")
        assert "Click to open this frame" in tip.text_content()

    def test_the_keyboard_moves_along_it(self, page) -> None:
        svg = page.locator('.series-chart[data-analysis="rmsd"] svg')
        svg.focus()
        for _ in range(3):
            page.keyboard.press("ArrowRight")
        assert page.locator('.series-chart[data-analysis="rmsd"]').get_attribute("data-at") == "3"

    def test_the_figure_is_a_click_away(self, page) -> None:
        card = page.locator(".analysis-card", has=page.locator('[data-series="rmsd"]'))
        toggle = card.locator("[data-series-toggle]")
        assert toggle.text_content() == "Show the figure"
        toggle.click()
        # Waited for: the figure is loaded lazily, when it is first shown.
        card.locator(".ac-frame img").wait_for(state="visible", timeout=30000)
        assert card.locator(".series-chart").is_hidden()
        toggle.click()
        assert card.locator(".series-chart").is_visible()

    def test_choosing_a_point_opens_its_frame(self, page) -> None:
        _point_at(page, "rmsd", 1.0)
        page.mouse.down()
        page.mouse.up()
        page.wait_for_function(
            "() => document.documentElement.dataset.page === 'viewer'"
            " && window.FastMDXMoleculeViewer.STATE.playbackLoaded", timeout=30000)
        page.wait_for_function(
            "() => document.getElementById('traj-slider').value === '19'", timeout=30000)
        assert page.evaluate("() => window.FastMDXMoleculeViewer.STATE.engine.frame()") == 19

    def test_choosing_a_residue_shows_it(self, page) -> None:
        _point_at(page, "rmsf", 0.5)
        page.mouse.down()
        page.mouse.up()
        page.wait_for_function(
            "() => document.documentElement.dataset.page === 'viewer'"
            " && window.FastMDXMoleculeViewer.STATE.focusResidue", timeout=30000)
        page.wait_for_function(
            "() => { const S = window.FastMDXMoleculeViewer.STATE;"
            " return S.engine && (S.engine.rendered || []).includes('focus') && S.focusIndices.length"
            " && S.engine.atoms(S.focusIndices).every((atom) => atom.resi === 6); }",
            timeout=15000)
        focus = page.evaluate("() => window.FastMDXMoleculeViewer.STATE.focusResidue")
        assert focus == {"resi": 6, "chain": None}
