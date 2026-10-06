"""An analysis's other figures are plotted from their numbers, in the page's
colours.

The Analysis page plotted an analysis's own series from its numbers; every
other figure (secondary structure, the clusters, the hierarchy, the RMSD
between frames, a projection and its landscape, the dihedrals) was the
picture the analysis wrote, white in a dark page, with nothing to point at.
Each is now plotted from the numbers it was made from (`figure_data.py`,
`figure-chart.js`), with its value, time and frame under the pointer, and
its picture one click away.
"""

from __future__ import annotations

import json
import math
import urllib.request
from pathlib import Path

import numpy as np
import pytest

from fastmdxplora.gui.figure_data import MOST_CELLS, figure_payload

INTERVAL_PS = 10.0
FRAMES = 60


def _write(root: Path) -> Path:
    """A study's analysis folder as the analyses write it, 60 frames."""
    rng = np.random.default_rng(3)
    folder = root / "analysis"
    for name in ("ss", "cluster", "dimred", "dihedrals"):
        (folder / name).mkdir(parents=True, exist_ok=True)
    (folder / "analysis_manifest.json").write_text(json.dumps({
        "load_kwargs": {"stride": 2, "first": None, "saving_interval_ps": INTERVAL_PS},
        "results": {}}), encoding="utf-8")
    # Secondary structure in DSSP's full alphabet: G is a helix, B a strand,
    # T and S coil.
    codes = "HGIEBTS C"
    lines = ["frame," + ",".join(str(r) for r in range(1, 10))]
    for k in range(FRAMES):
        lines.append(f"{k}," + ",".join(codes))
    (folder / "ss" / "ss.dat").write_text("\n".join(lines) + "\n", encoding="utf-8")
    # Two clusters, the first half and the second.
    labels = [0] * (FRAMES // 2) + [1] * (FRAMES // 2)
    (folder / "cluster" / "cluster_kmeans.dat").write_text(
        "frame,cluster\n" + "".join(f"{k},{c}\n" for k, c in enumerate(labels)), encoding="utf-8")
    (folder / "cluster" / "cluster_kmeans_populations.csv").write_text(
        "cluster,frames,fraction,medoid_frame,medoid_time_ns\n0,30,0.5,12,0.25\n1,30,0.5,44,0.89\n",
        encoding="utf-8")
    (folder / "cluster" / "cluster_hierarchical.dat").write_text(
        "frame,cluster\n" + "".join(f"{k},{c}\n" for k, c in enumerate(labels)), encoding="utf-8")
    points = np.concatenate([rng.normal(0, 0.1, (30, 2)), rng.normal(3, 0.1, (30, 2))])
    from scipy.cluster.hierarchy import linkage

    np.save(folder / "cluster" / "hierarchical_linkage.npy", linkage(points, method="average"))
    (folder / "cluster" / "options.json").write_text(json.dumps(
        {"options": {"n_clusters": 2, "methods": ["kmeans", "hierarchical"]}}), encoding="utf-8")
    distances = np.abs(points[:, None, 0] - points[None, :, 0])
    np.savez_compressed(folder / "cluster" / "cluster_rmsd_matrix.npz",
                        rmsd_nm=distances.astype(np.float32), frames=np.arange(FRAMES) * 2,
                        time_ns=(np.arange(FRAMES) * 2 + 1) * INTERVAL_PS / 1000.0,
                        distance=np.array("rmsd"))
    (folder / "dimred" / "dimred_pca.dat").write_text(
        "frame,component_1,component_2\n"
        + "".join(f"{k},{x},{y}\n" for k, (x, y) in enumerate(points)), encoding="utf-8")
    np.savez(folder / "dimred" / "dimred_pca_modes.npz", ratio=np.array([0.395, 0.147]),
             variance=np.array([1.0, 0.4]))
    energy = np.full((4, 3), np.inf)
    energy[0, 0], energy[1, 2] = 0.0, 1.5
    np.savez(folder / "dimred" / "dimred_pca_landscape.npz", free_energy=energy,
             edges_pc1=np.linspace(-1, 1, 5), edges_pc2=np.linspace(-2, 2, 4),
             unit=np.array("kJ/mol"))
    rows = ["frame,residue,phi_deg,psi_deg,omega_deg"]
    for k in range(FRAMES):
        rows.append(f"{k},2,-60.0,-45.0,180.0")
        rows.append(f"{k},3,-120.0,130.0,180.0")
    (folder / "dihedrals" / "dihedrals.dat").write_text("\n".join(rows) + "\n", encoding="utf-8")
    return root


@pytest.fixture
def study(tmp_path: Path) -> Path:
    return _write(tmp_path / "study")


def _get(root, name):
    payload = figure_payload(root, f"analysis/{name}.png")
    assert payload["ok"], payload
    return payload


class TestEachFigureIsGivenInItsShape:

    def test_secondary_structure_in_the_analysis_s_three_classes(self, study) -> None:
        ss = _get(study, "ss/ss")
        assert ss["kind"] == "states" and ss["residues"] == [str(r) for r in range(1, 10)]
        assert set(ss["states"]) == {"HHHEECCCC"}
        # Frame k of those loaded at a stride of 2 is frame 2k, written at
        # (2k + 1) saving intervals.
        assert ss["frames"][:3] == [0, 2, 4]
        assert ss["x"][:2] == pytest.approx([0.01, 0.03])

    def test_each_frame_s_cluster_on_the_clock(self, study) -> None:
        labels = _get(study, "cluster/cluster_kmeans")
        assert labels["kind"] == "labels" and labels["clusters"] == [0, 1]
        assert labels["x"][-1] == pytest.approx((2 * (FRAMES - 1) + 1) * INTERVAL_PS / 1000.0)

    def test_populations_with_their_medoids(self, study) -> None:
        bars = _get(study, "cluster/cluster_kmeans_counts")
        # The table numbers a medoid among the frames analysed; loaded at a
        # stride of 2, frame 12 of those is the trajectory's frame 24.
        assert [(k["cluster"], k["frames"], k["medoid_frame"]) for k in bars["clusters"]] == [
            (0, 30, 24), (1, 30, 88)]

    def test_noise_is_a_bar_though_the_table_leaves_it_out(self, study) -> None:
        dat = study / "analysis" / "cluster" / "cluster_kmeans.dat"
        rows = dat.read_text().splitlines()
        rows[1] = "0,-1"
        dat.write_text("\n".join(rows) + "\n")
        bars = _get(study, "cluster/cluster_kmeans_counts")
        assert bars["clusters"][0]["cluster"] == -1 and bars["clusters"][0]["frames"] == 1

    def test_the_hierarchy_is_coloured_by_the_clusters_it_was_cut_into(self, study) -> None:
        tree = _get(study, "cluster/cluster_hierarchical_dendrogram")
        assert tree["kind"] == "dendrogram" and tree["frames"] == FRAMES
        assert len(tree["icoord"]) == len(tree["dcoord"]) == len(tree["groups"]) == FRAMES - 1
        # Two clusters: one merge above the cut, joining them, and every
        # other merge in the cluster its frames are in.
        assert tree["groups"].count(None) == 1
        assert set(tree["groups"]) - {None} == {0, 1}
        top = max(range(len(tree["dcoord"])), key=lambda i: tree["dcoord"][i][1])
        assert tree["groups"][top] is None
        # The cut lies between the last two merges, on neither.
        heights = sorted(d[1] for d in tree["dcoord"])
        assert heights[-2] < tree["cut"] < heights[-1]

    def test_tied_merges_keep_each_cluster_s_colour(self, tmp_path) -> None:
        """Frames written twice merge at height 0; matched by height, ties
        were laid out in another order and took another cluster's colour."""
        from scipy.cluster.hierarchy import linkage

        root = _write(tmp_path / "study")
        points = np.repeat(np.concatenate([np.zeros((10, 2)), np.full((10, 2), 5.0),
                                           np.full((10, 2), 9.0)]), 2, axis=0)
        folder = root / "analysis" / "cluster"
        np.save(folder / "hierarchical_linkage.npy", linkage(points, method="average"))
        labels = [0] * 20 + [1] * 20 + [2] * 20
        (folder / "cluster_hierarchical.dat").write_text(
            "frame,cluster\n" + "".join(f"{k},{c}\n" for k, c in enumerate(labels)))
        (folder / "options.json").write_text(json.dumps({"options": {"n_clusters": 3}}))
        tree = _get(root, "cluster/cluster_hierarchical_dendrogram")
        # Every link below the cut is in a cluster, and each leaf's link is
        # in the leaf's own: the leaves are laid out left to right in tens.
        leaf_links = [g for g, d in zip(tree["groups"], tree["dcoord"])
                      if d[0] == 0 and d[3] == 0 and d[1] < tree["cut"]]
        assert set(leaf_links) == {0, 1, 2}
        assert tree["groups"].count(None) == 2
        assert 0 < tree["cut"] < 9

    def test_the_rmsd_between_frames_is_averaged_in_blocks_past_its_size(self, tmp_path) -> None:
        root = _write(tmp_path / "study")
        n = MOST_CELLS + 51
        big = np.random.default_rng(1).random((n, n)).astype(np.float32)
        np.savez_compressed(root / "analysis" / "cluster" / "cluster_rmsd_matrix.npz",
                            rmsd_nm=big, frames=np.arange(n), time_ns=np.arange(n) * 0.01)
        matrix = _get(root, "cluster/cluster_rmsd_matrix")
        cells = math.ceil(n / 2)
        assert matrix["block"] == 2 and len(matrix["values"]) == cells
        assert matrix["values"][0][0] == pytest.approx(big[:2, :2].mean(), rel=1e-4)
        # The last cell is a block wide too: the axis ends where it does.
        assert matrix["extent"] == pytest.approx([-0.005, (cells * 2 - 0.5) * 0.01])
        assert matrix["values"][-1][-1] == pytest.approx(float(big[-1, -1]), rel=1e-4)

    def test_a_projection_names_its_components_by_their_share(self, study) -> None:
        pca = _get(study, "dimred/dimred_pca")
        assert pca["axes"] == ["PC 1 (39.5%)", "PC 2 (14.7%)"]
        assert len(pca["x"]) == len(pca["t"]) == FRAMES and pca["unit"] == "nm"

    def test_a_landscape_s_unvisited_bins_are_empty(self, study) -> None:
        land = _get(study, "dimred/dimred_pca_landscape")
        # Rows run along the second component, columns along the first.
        assert len(land["values"]) == 3 and len(land["values"][0]) == 4
        assert land["values"][0][0] == 0.0 and land["values"][2][1] == 1.5
        assert land["values"][1][1] is None

    def test_the_dihedrals_density_counts_every_pair(self, study) -> None:
        density = _get(study, "dihedrals/dihedrals")
        total = sum(math.expm1(v) for row in density["values"] for v in row)
        assert total == pytest.approx(2 * FRAMES, rel=1e-3)
        assert density["pairs"] == 2 * FRAMES and density["guides"] == [0.0]


class TestOnlyWhatItKnows:

    @pytest.mark.parametrize("figure", [
        "analysis/rmsd/rmsd.png", "analysis/../secret.png", "/etc/passwd",
        "analysis/ss/ss.svg", "analysis/cluster/cluster_kmeans.png/../../x.png", ""])
    def test_any_other_path_is_refused(self, study, figure) -> None:
        assert figure_payload(study, figure)["ok"] is False

    def test_a_figure_without_its_numbers_keeps_its_picture(self, tmp_path) -> None:
        said = figure_payload(tmp_path, "analysis/ss/ss.png")
        assert said["ok"] is False and said["reason"]

    def test_the_server_answers(self, study) -> None:
        from fastmdxplora.gui.server import GETS_ANSWERED_BEYOND_LOOPBACK, start_dashboard_session

        assert "/api/figure-data" in GETS_ANSWERED_BEYOND_LOOPBACK
        session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
        try:
            said = json.loads(urllib.request.urlopen(
                session.url + "/api/figure-data?figure=analysis/ss/ss.png", timeout=60).read())
        finally:
            session.server.shutdown()
        assert said["ok"] and said["kind"] == "states"


# --------------------------------------------------------------------------
# In a browser
# --------------------------------------------------------------------------

FIGURES = ("ss/ss", "cluster/cluster_kmeans", "cluster/cluster_kmeans_counts",
           "cluster/cluster_hierarchical_dendrogram", "cluster/cluster_rmsd_matrix",
           "dimred/dimred_pca", "dimred/dimred_pca_landscape", "dihedrals/dihedrals")


def test_each_is_plotted_and_follows_the_scheme(tmp_path) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session
    from tests.test_an_analysis_is_drawn_from_its_numbers import _figure

    root = _write(tmp_path / "study")
    results = {name.split("/")[0]: {"status": "ok"} for name in FIGURES}
    manifest = json.loads((root / "analysis" / "analysis_manifest.json").read_text())
    manifest["results"] = results
    (root / "analysis" / "analysis_manifest.json").write_text(json.dumps(manifest))
    for name in FIGURES:
        _figure(root / "analysis" / f"{name}.png")
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.set_default_timeout(60000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url + "#analysis", wait_until="domcontentloaded")
            page.wait_for_function(
                f"() => document.querySelectorAll('.figure-chart svg').length === {len(FIGURES)}")
            kinds = page.eval_on_selector_all(
                ".figure-chart", "(all) => all.map((c) => c.dataset.figureKind).sort()")
            # Pointing at a plot gives its value there.
            chart = page.locator('.figure-chart[data-figure-kind="labels"] svg')
            chart.scroll_into_view_if_needed()
            box = chart.bounding_box()
            page.mouse.move(box["x"] + box["width"] * 0.5, box["y"] + box["height"] * 0.5)
            tip = page.text_content('.figure-chart[data-figure-kind="labels"] .series-tip')
            # The picture is a click away, and back.
            card = page.locator('.analysis-card:has(.figure-chart[data-figure-kind="labels"])')
            card.locator("[data-figure-toggle]").click()
            shown = card.locator(".ac-frame img").is_visible()
            card.locator("[data-figure-toggle]").click()
            # Its text is in the scheme's colours, and changes with it.
            def ink():
                return page.eval_on_selector(
                    '.figure-chart[data-figure-kind="bars"] svg text', "(t) => t.getAttribute('fill')")
            before = ink()
            page.evaluate("""() => {
                document.documentElement.dataset.theme =
                    document.documentElement.dataset.theme === 'light' ? 'dark' : 'light';
                document.body.dataset.theme = document.documentElement.dataset.theme;
                document.dispatchEvent(new CustomEvent('fmx:theme'));
            }""")
            page.wait_for_function(
                "(was) => document.querySelector('.figure-chart[data-figure-kind=\"bars\"] svg text')"
                ".getAttribute('fill') !== was", arg=before)
            browser.close()
    finally:
        session.server.shutdown()
    assert not errors, errors
    assert kinds == sorted(["states", "labels", "bars", "dendrogram", "matrix", "projection",
                            "landscape", "density"])
    assert "cluster 0" in tip or "cluster 1" in tip
    assert shown
