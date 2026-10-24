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


def _analysis(root: Path, name: str, text: str, mean: dict | None = None,
              *, options: dict | None = None, selection: str | None = None) -> None:
    folder = root / "analysis" / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.dat").write_text(text, encoding="utf-8")
    _figure(folder / f"{name}.png")
    (folder / "options.json").write_text(json.dumps(
        {"analysis": name, "selection": selection, "options": options or {},
         "findings": {} if mean is None else {"mean": mean}}),
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
    topology = load.pop("topology", None)
    resolved = {"trajectory": str(root / "simulation" / "production.dcd")}
    if topology is not None:
        resolved["topology"] = str(topology)
    (root / "analysis" / "analysis_manifest.json").write_text(json.dumps({
        "load_kwargs": {"stride": None, "first": None, "saving_interval_ps": None, **load},
        "resolved": resolved,
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
                                "discard": 1, "from_x": 0.16, "not_a_measurement": None,
                                "noted": None}

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

    def test_atom_mode_is_not_mislabelled_as_a_residue_profile(self, tmp_path) -> None:
        _analysis(tmp_path, "rmsf", "7 0.2\n12 0.3\n",
                  options={"per_residue": False}, selection="name CA")

        data = series_payload(tmp_path, "rmsf")

        assert data["kind"] == "atom"
        assert data["x_label"] == "Atom"
        assert data["labels"] == ["Atom 7", "Atom 12"]
        assert data["entities"] == [None, None]

    def test_atom_rows_are_tied_to_the_analyzed_topology(self, tmp_path) -> None:
        pytest.importorskip("mdtraj")
        from fastmdxplora.utils.native_output import topology_quietly
        from tests.test_the_drawing_scripts_run_in_a_browser import _write_study

        root = _write_study(tmp_path / "study")
        topology_path = root / "simulation" / "trajectory_topology.pdb"
        topology = topology_quietly(topology_path)
        atoms = [atom for atom in topology.atoms if atom.name == "CA"][:3]
        _manifest(root, topology=topology_path)
        _analysis(root, "rmsf", "".join(
            f"{atom.serial} {0.1 + i * 0.01:.2f}\n" for i, atom in enumerate(atoms)),
            options={"per_residue": False}, selection="protein and name CA")

        entities = series_payload(root, "rmsf")["entities"]

        assert entities[0] == {"kind": "atom", "chain": "A", "resi": 1,
                               "icode": "", "resn": "ALA", "atom": "CA"}
        assert [entity["resi"] for entity in entities] == [1, 2, 3]

    @pytest.mark.parametrize("analysis", ["rmsf", "order_parameters"])
    def test_a_residue_number_shared_by_two_chains_is_not_guessed(self, tmp_path, analysis) -> None:
        pytest.importorskip("mdtraj")
        import mdtraj as md

        from fastmdxplora.gui.series import series_payload

        simulation = tmp_path / "simulation"
        simulation.mkdir()
        topology = md.Topology()
        for chain_name in ("A", "B"):
            chain = topology.add_chain(chain_name)
            residue = topology.add_residue("ALA", chain, resSeq=12)
            topology.add_atom("CA", md.element.carbon, residue)
        structure = md.Trajectory(
            xyz=[[[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]]], topology=topology)
        topology_path = simulation / "trajectory_topology.pdb"
        structure[0].save_pdb(str(topology_path))
        _manifest(tmp_path, topology=topology_path)
        _analysis(tmp_path, analysis, "12 0.2\n", options={"per_residue": True},
                  selection="name CA")

        profile = series_payload(tmp_path, analysis)

        assert profile["labels"] == ["12"]
        assert profile["entities"] == [None]

    @pytest.mark.parametrize("text,reference", [
        ("# order_parameters\n1 0.8\n2 0.9\n", [None, None]),
        ("# columns: residue S2_simulated S2_measured\n1 0.8 0.7\n2 0.9 nan\n", [0.7, None]),
        ("chain,residue,insertion,s2,measured\nA,1,,0.8,0.7\nA,2,,0.9,\n", [0.7, None]),
    ])
    def test_order_parameters_keep_simulated_and_measured_values_separate(
            self, tmp_path, text, reference) -> None:
        _analysis(tmp_path, "order_parameters", text)
        data = series_payload(tmp_path, "order_parameters")
        assert data["kind"] == "residue"
        assert data["y"] == [0.8, 0.9]
        assert data["reference"] == reference
        assert data["unit"] == ""
        assert data["y_bounds"] == [0.0, 1.05]
        assert data["entities"] == [None, None]

    def test_order_parameters_resolve_numeric_chains_and_insertion_codes(self, tmp_path) -> None:
        md = pytest.importorskip("mdtraj")
        topology = md.Topology()
        for name in ("1", "2"):
            chain = topology.add_chain(name)
            for _ in range(2):
                residue = topology.add_residue("ALA", chain, resSeq=12)
                topology.add_atom("CA", md.element.carbon, residue)
        simulation = tmp_path / "simulation"
        simulation.mkdir()
        topology_path = simulation / "trajectory_topology.pdb"
        md.Trajectory(xyz=[[[i, 0, 0] for i in range(4)]], topology=topology).save_pdb(
            str(topology_path))
        lines = topology_path.read_text().splitlines()
        # The second occurrence in each chain is residue 12A.
        lines = [line[:26] + "A" + line[27:] if line.startswith("ATOM")
                 and int(line[6:11]) in (2, 5) else line for line in lines]
        topology_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        _manifest(tmp_path, topology=topology_path)
        _analysis(tmp_path, "order_parameters",
                  "chain,residue,insertion,s2,measured\n1,12,A,0.8,0.7\n2,12,,0.9,\n")
        data = series_payload(tmp_path, "order_parameters")
        assert data["labels"] == ["1:12A", "2:12"]
        assert data["entities"] == [
            {"kind": "residue", "chain": "1", "resi": 12, "icode": "A", "resn": "ALA"},
            {"kind": "residue", "chain": "2", "resi": 12, "icode": "", "resn": "ALA"},
        ]

    def test_an_order_parameter_upper_bound_is_still_said(self, tmp_path) -> None:
        _analysis(tmp_path, "order_parameters", "1 0.8\n")
        path = tmp_path / "analysis" / "order_parameters" / "options.json"
        record = json.loads(path.read_text())
        record["findings"] = {"order_parameters": {"not_a_measurement": "Only an upper bound."}}
        path.write_text(json.dumps(record), encoding="utf-8")
        assert series_payload(tmp_path, "order_parameters")["notice"] == "Only an upper bound."


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
    _manifest(root, saving_interval_ps=2.0,
              topology=root / "simulation" / "trajectory_topology.pdb")
    _analysis(root, "rmsd", _series(FRAMES), {
        "mean": 0.112, "standard_error": 0.002, "effective_samples": 11.0,
        "discard": 5, "n_frames": FRAMES})
    _analysis(root, "rmsf", "".join(f"{i + 1} {0.05 + 0.01 * i:.4f}\n" for i in range(10)),
              options={"per_residue": True}, selection="protein and name CA")
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
    # Scrolled to first, as a reader would: the page opens with the results
    # table and the index above the figures.
    chart = page.locator(f'.series-chart[data-analysis="{analysis}"] svg')
    chart.scroll_into_view_if_needed()
    box = chart.bounding_box()
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
        assert label.startswith(
            "RMSD, 20 points; full-analysis mean 0.112 nm plus or minus 0.00200"
        )
        assert page.errors == []

    def test_pointing_gives_the_value_time_and_frame(self, page) -> None:
        _point_at(page, "rmsd", 0.0)
        tip = page.locator('.series-chart[data-analysis="rmsd"] .series-tip')
        assert tip.is_visible()
        # Frame 0 was written after one 2 ps interval, said in ps: a run
        # shorter than a nanosecond is plotted on a ps clock (8c2abdae).
        assert tip.text_content().startswith("0.100 nm · 2.000 ps · frame 0")
        assert "Click to open this frame" in tip.text_content()

    def test_the_keyboard_moves_along_it(self, page) -> None:
        svg = page.locator('.series-chart[data-analysis="rmsd"] svg')
        svg.focus()
        for _ in range(3):
            page.keyboard.press("ArrowRight")
        assert (
            page.locator('.series-chart[data-analysis="rmsd"] .series-plot').get_attribute(
                "data-at"
            )
            == "3"
        )

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
        # The molecular engine is mounted only on the Viewer page. Load the
        # trajectory there before returning to Analysis to exercise a profile
        # selection without changing the current frame.
        page.evaluate("() => window.FastMDXDashboard.navigate('viewer')")
        page.wait_for_function("() => document.documentElement.dataset.page === 'viewer'")
        assert page.evaluate("async () => window.FastMDXMoleculeViewer.movie.frames()")
        page.wait_for_function(
            "() => window.FastMDXMoleculeViewer.STATE.playbackLoaded", timeout=30000
        )
        page.evaluate("() => window.FastMDXMoleculeViewer.movie.showFrame(7)")
        page.wait_for_function("() => window.FastMDXMoleculeViewer.STATE.engine.frame() === 7",
                               timeout=15000)
        page.evaluate("() => window.FastMDXDashboard.navigate('analysis')")
        _point_at(page, "rmsf", 5 / 9)
        page.mouse.down()
        page.mouse.up()
        page.wait_for_function(
            "() => document.documentElement.dataset.page === 'viewer'"
            " && (window.FastMDXMoleculeViewer.STATE.focusResidue"
            " || window.FastMDXMoleculeViewer.STATE.selection?.residues?.length === 1)",
            timeout=30000)
        assert page.evaluate("() => window.FastMDXMoleculeViewer.STATE.selection") is not None
        page.wait_for_function(
            "() => { const S = window.FastMDXMoleculeViewer.STATE;"
            " return S.engine && S.selection.atoms.length"
            " && S.engine.atoms(S.selection.atoms).every((atom) => atom.resi === 6); }",
            timeout=15000)
        selection = page.evaluate("() => window.FastMDXMoleculeViewer.STATE.selection")
        assert selection["residues"][0]["resi"] == 6
        assert page.evaluate("() => window.FastMDXMoleculeViewer.STATE.engine.frame()") == 7
        assert page.evaluate("() => window.FastMDXMoleculeViewer.currentViewHints().selection") == [
            {"chain": "A", "resi": 6, "icode": "", "resn": "ALA"}]
        page.locator("#seq-clear").click()
        page.wait_for_function("() => window.FastMDXMoleculeViewer.STATE.selection === null")
        assert "selection" not in page.evaluate(
            "() => window.FastMDXMoleculeViewer.currentViewHints()"
        )


@pytest.mark.parametrize("delayed", ["frames-info", "structure-info"])
def test_rmsf_chart_does_not_wait_for_unrelated_viewer_metadata(tmp_path, delayed) -> None:
    """Results already received can be drawn while viewer metadata is pending."""
    pytest.importorskip("playwright.sync_api")
    pytest.importorskip("mdtraj")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session
    from tests.test_the_drawing_scripts_run_in_a_browser import _write_study

    root = _write_study(tmp_path / "rmsf-startup")
    _manifest(root, topology=root / "simulation" / "trajectory_topology.pdb")
    _analysis(root, "rmsf", "".join(f"{0.1 + i * 0.01:.2f}\n" for i in range(10)))
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            held = []

            def hold(route):
                held.append(route)

            page.route(f"**/api/{delayed}", hold)
            try:
                with page.expect_request(f"**/api/{delayed}"):
                    with page.expect_response("**/api/results") as results:
                        page.goto(session.url + "#analysis", wait_until="domcontentloaded")
                assert results.value.status == 200
                page.locator('.series-chart[data-analysis="rmsf"] svg').wait_for(
                    state="visible", timeout=5000)
                assert held, f"the delayed {delayed} request was not intercepted"
                # The chart really contains its ten residue measurements.
                assert page.locator(
                    '.series-chart[data-analysis="rmsf"] svg circle[r="2.2"]'
                ).count() == 10
            finally:
                for route in held:
                    route.abort()
                browser.close()
    finally:
        session.server.shutdown()


@pytest.mark.parametrize("delay_loading_check", [False, True])
def test_clicking_atom_rmsf_selects_the_named_atom_in_the_viewer(
        tmp_path, delay_loading_check) -> None:
    pytest.importorskip("playwright.sync_api")
    pytest.importorskip("mdtraj")
    from playwright.sync_api import TimeoutError as BrowserTimeout
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session
    from fastmdxplora.utils.native_output import topology_quietly
    from tests.test_the_drawing_scripts_run_in_a_browser import _write_study

    root = _write_study(tmp_path / "atom-rmsf")
    topology_path = root / "simulation" / "trajectory_topology.pdb"
    topology = topology_quietly(topology_path)
    atoms = [atom for atom in topology.atoms if atom.name == "CA"]
    _manifest(root, topology=topology_path)
    _analysis(root, "rmsf", "".join(
        f"{atom.serial} {0.1 + i * 0.01:.2f}\n" for i, atom in enumerate(atoms)),
        options={"per_residue": False}, selection="protein and name CA")
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            errors: list[str] = []
            series_responses = []
            held_loading_checks = []
            if delay_loading_check:
                page.route("**/api/metrics", lambda route: held_loading_checks.append(route))
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("response", lambda response: series_responses.append(response.status)
                    if "/api/series?" in response.url else None)
            page.goto(session.url + "#analysis", wait_until="domcontentloaded")
            if not page.evaluate("() => !!document.createElement('canvas').getContext('webgl')"):
                pytest.skip("this browser has no WebGL, so the viewer cannot render")
            chart = page.locator('.series-chart[data-analysis="rmsf"] svg')
            try:
                chart.wait_for(state="visible", timeout=30000)
            except BrowserTimeout as error:
                raise AssertionError({
                    "series_http_status": series_responses, "page_errors": errors,
                    "cards": page.locator('[data-series="rmsf"]').evaluate_all(
                        "nodes => nodes.map(n => ({ready: n.dataset.seriesReady,"
                        " hidden: n.hidden}))"),
                    "source": series_payload(root, "rmsf"),
                }) from error
            if delay_loading_check:
                # A drawn chart can still be covered by the startup checklist.
                assert held_loading_checks
                assert "state-loading" in page.locator("body").get_attribute("class")
                for route in held_loading_checks:
                    route.continue_()
                page.unroute("**/api/metrics")
            # Wait for the current SVG to receive the click after startup/redraw.
            chart.click(trial=True)
            height = chart.evaluate("el => el.clientHeight")
            # Inside the first point's interval, clear of the plot's boundary.
            chart.click(position={"x": 65, "y": height / 2})
            page.wait_for_function(
                "() => document.documentElement.dataset.page === 'viewer'"
                " && window.FastMDXMoleculeViewer.STATE.selection?.atoms?.length === 1",
                timeout=60000)
            selected = page.evaluate("""() => {
              const S = window.FastMDXMoleculeViewer.STATE;
              return {atom: S.engine.atoms(S.selection.atoms)[0],
                context: window.FastMDXMoleculeViewer.currentViewHints().selection};
            }""")
            assert (selected["atom"]["chain"], selected["atom"]["resi"],
                    selected["atom"]["resn"], selected["atom"]["atom"]) == (
                        "A", 1, "ALA", "CA")
            assert selected["context"] == [{"chain": "A", "resi": 1, "icode": "",
                                             "resn": "ALA", "atom": "CA"}]
            assert errors == []
            browser.close()
    finally:
        session.server.shutdown()


def test_backbone_order_parameter_points_open_the_named_residue(tmp_path) -> None:
    pytest.importorskip("playwright.sync_api")
    pytest.importorskip("mdtraj")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session
    from tests.test_the_drawing_scripts_run_in_a_browser import _write_study

    root = _write_study(tmp_path / "order-parameters")
    _manifest(root, topology=root / "simulation" / "trajectory_topology.pdb")
    text = "# residue S2_simulated S2_measured\n1 0.8 0.7\n2 0.9 nan\n3 0.6 0.5\n"
    _analysis(root, "order_parameters", text)
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.set_default_timeout(60000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url + "#analysis", wait_until="domcontentloaded")
            if not page.evaluate("() => !!document.createElement('canvas').getContext('webgl')"):
                pytest.skip("this browser has no WebGL, so the viewer cannot render")
            chart = page.locator('.series-chart[data-analysis="order_parameters"]')
            chart.locator("svg").wait_for(state="visible")
            assert chart.locator(".series-reference").count() == 2
            form = chart.locator(".series-range")
            form.locator('[data-range="from"]').fill("2")
            form.locator('[data-range="to"]').fill("3")
            form.get_by_role("button", name="Apply range").click()
            assert chart.locator(".series-reference").count() == 1
            # A real pointer click on residue 2, after limiting the range.
            chart.locator("svg circle").first.click()
            page.wait_for_function("""() => document.documentElement.dataset.page === 'viewer'
              && window.FastMDXMoleculeViewer.currentViewHints().selection?.[0]?.resi === 2""")
            assert page.evaluate(
                "() => window.FastMDXMoleculeViewer.currentViewHints().selection"
            ) == [{"chain": "A", "resi": 2, "icode": "", "resn": "ALA"}]
            page.get_by_role("link", name="Analysis", exact=True).click()
            chart.locator("svg").press("End")
            chart.locator("svg").press("Enter")
            page.wait_for_function("""() => document.documentElement.dataset.page === 'viewer'
              && window.FastMDXMoleculeViewer.currentViewHints().selection?.[0]?.resi === 3""")
            assert errors == []
            browser.close()
    finally:
        session.server.shutdown()
    assert (root / "analysis" / "order_parameters" / "order_parameters.dat").read_text() == text
