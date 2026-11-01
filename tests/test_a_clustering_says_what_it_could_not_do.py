"""What clustering and the projections could not do is said where they are
seen, and the dendrogram's heights carry their unit.

Found by the clustering reviews after 2709 and the clust-est use review
(10-29): a study whose MDS was refused for memory showed its PCA card with no
word of it, while its record and its report said so; a clustering refused
for memory showed a card that said to re-run the analysis, which run again
as it was is refused again; and the dendrogram's CSV gave its heights with no
unit.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from fastmdxplora.gui.figure_data import figure_payload

STATIC = Path(__file__).parents[1] / "src" / "fastmdxplora" / "gui" / "static"

#: As dimred records MDS refused for memory beside the PCA it ran.
MDS_REFUSED = {
    "code": "analysis.memory.matrix_too_large",
    "said": ("MDS of 40,000 frames needs about 25.6 GB for their frame-by-frame RMSD "
             "matrix and its centred square, and this machine has 16.0 GB available."),
    "kept": ["pca"],
}


def _function(script: str, name: str) -> str:
    """The function ``name`` of a page's script, as its source."""
    text = (STATIC / script).read_text(encoding="utf-8")
    start = text.index(f"  function {name}(")
    return text[start:text.index("\n  }\n", start) + 4]


def _node(source: str) -> object:
    node = shutil.which("node")
    if node is None:
        pytest.skip("no node")
    done = subprocess.run([node, "-e", source], capture_output=True, text=True, check=True)
    return json.loads(done.stdout)


def _dimred_study(root: Path) -> Path:
    """A study whose dimred ran PCA and refused MDS for memory."""
    folder = root / "analysis" / "dimred"
    folder.mkdir(parents=True)
    (root / "analysis" / "analysis_manifest.json").write_text(json.dumps({
        "load_kwargs": {"stride": 1, "first": None, "saving_interval_ps": 10.0},
        "results": {"dimred": {"status": "ok",
                               "message": "dimred: ok (pca); MDS refused for memory, "
                                          "the rest run and saved"}}}), encoding="utf-8")
    rows = ["frame,component_1,component_2"] + [
        f"{i},{0.1 * (i % 7) - 0.3:.3f},{0.05 * (i % 5) - 0.1:.3f}" for i in range(40)]
    (folder / "dimred_pca.dat").write_text("\n".join(rows) + "\n", encoding="utf-8")
    (folder / "options.json").write_text(json.dumps({
        "options": {"methods": ["pca", "mds"]},
        "findings": {"mds_refused": MDS_REFUSED}}), encoding="utf-8")
    return root


class TestMDSRefusedIsSaidOnTheCard:
    def test_the_projection_carries_the_report_s_note(self, tmp_path):
        from fastmdxplora.report.document import _findings_notes

        root = _dimred_study(tmp_path / "study")
        payload = figure_payload(root, "analysis/dimred/dimred_pca.png")
        assert payload["ok"], payload
        # As the report says it: one sentence, from one place.
        said = [note for note in _findings_notes({"mds_refused": MDS_REFUSED})
                if note.startswith("MDS was not run")]
        assert (payload.get("note") or "") == said[0]

    def test_the_card_shows_it(self, tmp_path):
        pytest.importorskip("playwright.sync_api")
        from playwright.sync_api import sync_playwright

        from fastmdxplora.gui.server import start_dashboard_session
        from tests.test_an_analysis_is_drawn_from_its_numbers import _figure

        root = _dimred_study(tmp_path / "study")
        _figure(root / "analysis" / "dimred" / "dimred_pca.png")
        session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
                page = browser.new_page(viewport={"width": 1280, "height": 900})
                page.set_default_timeout(60000)
                errors: list[str] = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(session.url + "#analysis", wait_until="domcontentloaded")
                card = '.analysis-card[data-analysis="dimred"]'
                page.wait_for_selector(f"{card} .figure-chart svg")
                notes = page.eval_on_selector_all(
                    f"{card} .figure-note", "(all) => all.map((n) => n.textContent)")
                browser.close()
        finally:
            session.server.shutdown()
        assert not errors, errors
        assert any(note.startswith("MDS was not run: MDS of 40,000 frames") for note in notes), notes


def test_the_dendrogram_s_csv_names_its_unit(tmp_path):
    """Its heights are the linkage's distance, in nm."""
    import numpy as np
    from scipy.cluster.hierarchy import linkage

    folder = tmp_path / "analysis" / "cluster"
    folder.mkdir(parents=True)
    points = np.random.default_rng(0).normal(size=(12, 3))
    np.save(folder / "hierarchical_linkage.npy", linkage(points, method="average"))
    (folder / "options.json").write_text(json.dumps({"options": {"n_clusters": 3}}))
    payload = figure_payload(tmp_path, "analysis/cluster/cluster_hierarchical_dendrogram.png")
    assert payload["ok"], payload
    header = _node(_function("figure-chart.js", "csvOf") + "\n"
                   f"var data = {json.dumps(payload)};\n"
                   "console.log(JSON.stringify(csvOf(data, {low: -1e9, high: 1e9}, "
                   "{low: -1e9, high: 1e9})[0]));")
    assert header[5:9] == [f"height{k} (nm)" for k in range(1, 5)], header


def test_a_refused_clustering_s_card_says_what_to_set():
    """Its card said "Re-run the analysis with this revised version to
    generate PNG and SVG figures" of a clustering refused for memory."""
    message = ("cluster: Clustering every 3rd frame, 667 frames, fits in the memory, where "
               "clustering 2,000 frames does not. Set `stride: 3` under the analysis's "
               "`options: {cluster: ...}` for it; the other analyses keep every frame. The "
               "2,000 frames need about 152.4 MB to plot the map of their RMSD matrix.")
    card = json.dumps({"name": "cluster", "title": "Clustering", "status": "error",
                       "message": message, "artifacts": []})
    html = _node(
        "function escapeAttr(v) { return String(v); }\n"
        "function escapeHTML(v) { return String(v); }\n"
        "function humanise(v) { return String(v); }\n"
        + _function("dashboard.js", "analysisCardHtml") + "\n"
        f"console.log(JSON.stringify(analysisCardHtml({card}, null)));")
    assert "Re-run" not in html, html
    assert ("Set stride: 3 under the analysis's options: {cluster: ...} for it; the other "
            "analyses keep every frame.") in html, html
