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
             "matrix and its centred square, and this machine has 16.0 GB available. "
             "PCA, t-SNE and UMAP make no such matrix (`methods: [pca]`)."),
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
    def test_the_projection_carries_a_short_note(self, tmp_path):
        """A record of MDS refused alone, written before each method was
        recorded: one short line, its settings plain on the page."""
        root = _dimred_study(tmp_path / "study")
        payload = figure_payload(root, "analysis/dimred/dimred_pca.png")
        assert payload["ok"], payload
        assert payload.get("figure_note") == (
            "MDS was not run: its frame-by-frame RMSD matrix did not fit in the memory "
            "this process can take."), payload.get("figure_note")

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
                # Kept when the card shows the figure in place of the plot:
                # it was inside the plot and left with it.
                page.click(f"{card} [data-figure-toggle]")
                # Until the image loads it has no size, and Playwright
                # counts it hidden: waited for, not read at the click.
                figure_shown = page.wait_for_selector(
                    f"{card} img", state="visible", timeout=30000) is not None
                shown = page.is_visible(f"{card} .figure-note")
                browser.close()
        finally:
            session.server.shutdown()
        assert not errors, errors
        assert any(note.startswith("MDS was not run: its frame-by-frame") for note in notes), notes
        assert figure_shown and shown, (figure_shown, shown)

    def test_the_figure_says_it(self, tmp_path, monkeypatch):
        """The PNG and SVG of the projection run beside it said nothing of
        it: one opened from the card, or alone, gave no word."""
        from fastmdxplora.analysis import cluster as clustering
        from fastmdxplora.analysis import dimred as module
        from tests.test_a_clustering_fits_the_machine import _chain

        texts: dict[str, list[str]] = {}
        real = module.save_figure

        def save(fig, path, *args, **kwargs):
            texts[Path(path).name] = [text.get_text() for text in fig.texts]
            return real(fig, path, *args, **kwargs)

        monkeypatch.setattr(module, "save_figure", save)
        monkeypatch.setattr(clustering, "memory_available",
                            lambda: (module.mds_memory_needed(300) // 3, "mocked"))
        result = module.DimRed(output_dir=tmp_path, methods=["pca", "mds"]).run(_chain(300))
        assert result.status == "ok", result.message
        said = " ".join(texts["dimred_pca.png"])
        assert said.startswith("MDS was not run"), texts
        assert "analysis: {stride: 2}" in said and "`" not in said, said


#: Each refusal's whole text, as the record keeps it: the room, where it
#: was read and the control group's path.
_READ_AS = (" Read as RLIMIT_AS less what this process holds (VmSize), the least of that "
            "and MemAvailable in /proc/meminfo and the cgroup v1 limit in /sys/fs/cgroup/"
            "memory/process_api/x/memory.limit_in_bytes less its usage, its reclaimable "
            "page cache, 626.2 MB of inactive_file in memory.stat, not counted as used.")


class TestTheProjectionsNoteIsShort:
    """The card's note under PCA was each refusal's whole text, 1,650
    characters with the cgroup's path twice, 1,021 px tall at 390; a t-SNE
    that ran out as it ran was said "t-SNE was not run"; and MDS's need was
    said as its matrix's where the figures planned beside it set it."""

    @staticmethod
    def _study(root: Path) -> Path:
        root = _dimred_study(root)
        options = root / "analysis" / "dimred" / "options.json"
        record = json.loads(options.read_text())
        record["findings"] = {
            "methods_refused": {
                "mds": {"said": "MDS of 500 frames takes about 52.4 MB of address space "
                                "for the 4 figures plotted once every method has run."
                                + _READ_AS,
                        "before_it_ran": True, "stride_that_fits": None,
                        "need_from": "figures", "kept": ["pca"]},
                "tsne": {"said": "t-SNE of 10,000 frames ran out of memory as it ran "
                                 "(std::bad_alloc)." + _READ_AS,
                         "before_it_ran": False, "stride_that_fits": None, "kept": ["pca"]}},
            "mds_refused": {"said": "MDS of 500 frames takes about 52.4 MB." + _READ_AS,
                            "kept": ["pca"]}}
        options.write_text(json.dumps(record), encoding="utf-8")
        return root

    def test_one_short_line_for_each_method_refused(self, tmp_path):
        root = self._study(tmp_path / "study")
        note = figure_payload(root, "analysis/dimred/dimred_pca.png").get("figure_note") or ""
        lines = note.split("\n")
        assert len(lines) == 2, note
        assert "/sys/fs/cgroup" not in note and "Read as" not in note, note
        assert all(len(line) < 120 for line in lines), lines

    def test_mds_s_need_is_said_as_the_figures_where_they_set_it(self, tmp_path):
        root = self._study(tmp_path / "study")
        note = figure_payload(root, "analysis/dimred/dimred_pca.png").get("figure_note") or ""
        assert note.split("\n")[0].startswith(
            "MDS was not run: the figures planned beside it did not fit"), note

    def test_a_t_sne_that_ran_out_is_said_to_have_run(self, tmp_path):
        root = self._study(tmp_path / "study")
        note = figure_payload(root, "analysis/dimred/dimred_pca.png").get("figure_note") or ""
        assert "t-SNE ran out of memory as it ran" in note, note
        assert "t-SNE was not run" not in note, note

    def test_the_refusal_names_the_figures_alone_where_they_set_the_need(self, monkeypatch):
        """"MDS of 500 frames takes about 54.5 MB ... for its frame-by-frame
        RMSD matrix, or for the 4 figures": the matrix was 4 MB of it."""
        from fastmdxplora.analysis import cluster as clustering
        from fastmdxplora.analysis import dimred as module
        from tests.test_a_clustering_fits_the_machine import _chain

        room = module.address_space_needed("pca", ["pca"], 120)
        monkeypatch.setattr(clustering, "memory_available", lambda: (
            room, "RLIMIT_AS less what this process holds (VmSize)"))
        analysis = module.DimRed(methods=["pca", "mds"])
        analysis._projected(_chain(120))
        said = analysis.findings["methods_refused"]["mds"]["said"]
        assert "for the 3 figures plotted once every method has run" in said, said
        assert "RMSD matrix" not in said.split(".")[0], said

    def test_a_projection_that_ran_out_is_not_blamed_on_the_memory_falling(
            self, tmp_path, monkeypatch):
        from fastmdxplora.analysis import dimred as module
        from tests.test_a_clustering_fits_the_machine import _chain

        def out(*args, **kwargs):
            raise MemoryError("std::bad_alloc")

        monkeypatch.setattr(module, "_classical_mds", out)
        result = module.DimRed(output_dir=tmp_path, methods=["pca", "mds"]).run(_chain(120))
        found = json.loads((result.output_dir / "options.json").read_text())["findings"]
        said = found["methods_refused"]["mds"]["said"]
        assert "ran out of memory as it ran" in said and "fell" not in said, said


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
        + _function("dashboard.js", "whatToSet") + "\n"
        + _function("dashboard.js", "analysisCardHtml") + "\n"
        f"console.log(JSON.stringify(analysisCardHtml({card}, null)));")
    assert "Re-run" not in html, html
    assert ("Set stride: 3 under the analysis's options: {cluster: ...} for it; the other "
            "analyses keep every frame.") in html, html


#: A clustering refused for memory, as the analysis manifest keeps it.
CLUSTER_REFUSED = (
    "cluster: Clustering every 3rd frame, 667 frames, fits in the memory, where "
    "clustering 2,000 frames does not. Set `stride: 3` under the analysis's "
    "`options: {cluster: ...}` for it; the other analyses keep every frame. The "
    "2,000 frames need about 152.4 MB to plot the map of their RMSD matrix, once the "
    "matrix is freed, and the container allows 116.0 MB, of which clustering plans "
    "on 75%, 87.0 MB.")


@pytest.mark.parametrize("width", [1280, 390])
def test_a_refused_clustering_is_said_in_its_section_on_the_page(tmp_path, width):
    """A study's page shows sections, and the grid that held the refused
    clustering's card is hidden beside them: what to set showed nowhere a
    person sees, and the results table's "Clustering" link went to no card."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session
    from tests.test_an_analysis_is_drawn_from_its_numbers import _figure

    root = tmp_path / "study"
    (root / "analysis" / "rmsd").mkdir(parents=True)
    _figure(root / "analysis" / "rmsd" / "rmsd.png")
    (root / "analysis" / "analysis_manifest.json").write_text(json.dumps({
        "load_kwargs": {"stride": 1, "first": None, "saving_interval_ps": 10.0},
        "results": {"rmsd": {"status": "ok", "message": "rmsd: ok"},
                    "cluster": {"status": "error", "message": CLUSTER_REFUSED}}}),
        encoding="utf-8")
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": width, "height": 900})
            page.set_default_timeout(60000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            # As the results table's link opens it.
            page.goto(session.url + "#analysis/cluster", wait_until="domcontentloaded")
            card = '#analysis-sections .analysis-card[data-analysis="cluster"]'
            page.wait_for_selector(card, state="visible")
            grid_hidden = page.eval_on_selector("#analysis-grid", "(grid) => grid.hidden")
            said = page.inner_text(card)
            theme = page.eval_on_selector(
                card, "(c) => c.closest('.analysis-theme').dataset.theme")
            page.wait_for_function(
                "() => document.querySelector('.page[data-page=\"analysis\"]')"
                ".dataset.cited === 'cluster'")
            sideways = page.evaluate(
                "() => document.documentElement.scrollWidth > "
                "document.documentElement.clientWidth")
            browser.close()
    finally:
        session.server.shutdown()
    assert not errors, errors
    assert grid_hidden, "the page has sections, so the grid is hidden"
    assert theme == "Conformations", theme
    assert ("Set stride: 3 under the analysis's options: {cluster: ...} for it; the other "
            "analyses keep every frame.") in said, said
    assert "152.4 MB" in said and "`" not in said and "Re-run" not in said, said
    assert not sideways


def _refusals_of_this_branch(monkeypatch) -> dict[str, str]:
    """What clustering, the projections and a superposition say when they
    refuse, made by the code that says it."""
    import mdtraj as md
    import numpy as np

    from fastmdxplora.analysis import cluster as clustering
    from fastmdxplora.analysis.dimred import DimRed
    from fastmdxplora.refusals import StudyError

    def chain(n, atoms=4):
        rng = np.random.default_rng(0)
        top = md.Topology()
        c = top.add_chain()
        for i in range(atoms):
            top.add_atom("CA", md.element.carbon, top.add_residue("ALA", c, resSeq=i + 1))
        xyz = rng.normal(0, 0.4, (atoms, 3))[None] + np.cumsum(
            rng.normal(0, 0.01, (n, atoms, 3)), axis=0)
        return md.Trajectory(xyz.astype(np.float32), top)

    said = {}
    rlimit = "RLIMIT_AS less what this process holds (VmSize)"
    cases = [
        ("cluster at any stride, resident", "cluster", 10 * 10**6, "the cgroup v2 limit, mocked",
         lambda: clustering.Cluster().compute(chain(500))),
        ("cluster at any stride, address", "cluster", 30 * 2**20, rlimit,
         lambda: clustering.Cluster().compute(chain(500))),
        ("cluster with a stride", "cluster", 150 * 2**20, rlimit,
         lambda: clustering.Cluster().compute(chain(2000))),
        ("dimred, PCA the last", "dimred", 20 * 2**20, rlimit,
         lambda: DimRed(methods=["pca"]).compute(chain(200))),
        ("dimred, MDS alone", "dimred", 4 * 10**6, "mocked",
         lambda: DimRed(methods=["mds"]).compute(chain(600))),
    ]
    for label, name, room, read_from, run in cases:
        monkeypatch.setattr(clustering, "memory_available",
                            lambda room=room, read_from=read_from: (room, read_from))
        with pytest.raises(StudyError) as raised:
            run()
        said[label] = (name, str(raised.value))
    monkeypatch.undo()
    one = chain(5, atoms=1)
    for name in ("cluster", "dimred"):
        analysis = clustering.Cluster() if name == "cluster" else DimRed()
        with pytest.raises(StudyError) as raised:
            analysis.compute(one)
        said[f"{name} on one atom"] = (name, str(raised.value))
    return said


def test_an_analysis_named_and_refused_is_a_failure_of_the_run(tmp_path, monkeypatch):
    """The CLI leaves out of its failures an analysis the run was too short
    for, read by its words (a frame and "needs", "only" or "at least"): the
    any-stride refusal ("Clustering 500 frames ... needs at least") and the
    three-atom one ("superposes the frames ... needs 3") read so, and a
    study that named the analysis ended with exit 0 and no failure said."""
    from types import SimpleNamespace

    from fastmdxplora.cli.main import _analyses_asked_for_that_failed

    for label, (name, said) in _refusals_of_this_branch(monkeypatch).items():
        folder = tmp_path / label.replace(" ", "_").replace(",", "")
        (folder / "analysis").mkdir(parents=True)
        (folder / "analysis" / "analysis_manifest.json").write_text(json.dumps({
            "results": {name: {"status": "error", "message": f"{name}: {said}"}}}),
            encoding="utf-8")
        failed = _analyses_asked_for_that_failed(
            {"analysis": {"include": [name]}}, [SimpleNamespace(output_dir=folder)])
        assert failed == [f"{name} ({said})"], (label, said)


def _card(analysis: dict) -> str:
    return _node(
        "function escapeAttr(v) { return String(v); }\n"
        "function escapeHTML(v) { return String(v); }\n"
        "function humanise(v) { return String(v); }\n"
        + _function("dashboard.js", "whatToSet") + "\n"
        + _function("dashboard.js", "unfinishedCardHtml") + "\n"
        f"console.log(JSON.stringify(unfinishedCardHtml({json.dumps(analysis)}, true)));")


def test_the_card_says_what_to_set_once():
    """The stride refusal's card said "Set stride: 2 ..." in its frame and
    again in its body: the body held the whole message where the "Set"
    sentence was not its first."""
    html = _card({"name": "cluster", "section": "Clustering", "status": "error",
                  "message": CLUSTER_REFUSED, "refused_before_it_ran": True})
    assert html.count("Set stride: 3") == 1, html
    assert "152.4 MB" in html, html


def test_the_card_says_whether_it_ran():
    """"The analysis did not finish" was said of one refused before it
    started; one that stopped part-way still says so."""
    for before, said in ((True, "Not plotted: the analysis was refused before it ran."),
                         (False, "Not plotted: the analysis did not finish.")):
        html = _card({"name": "cluster", "section": "Clustering", "status": "error",
                      "message": CLUSTER_REFUSED, "refused_before_it_ran": before})
        assert said in html, html


def _clustering_short_of_figures(root: Path) -> Path:
    """A study whose clustering ran, its map not plotted for memory."""
    from tests.test_an_analysis_is_drawn_from_its_numbers import _figure

    folder = root / "analysis" / "cluster"
    folder.mkdir(parents=True)
    for name in ("cluster_kmeans.png", "cluster_kmeans_counts.png"):
        _figure(folder / name)
    (root / "analysis" / "analysis_manifest.json").write_text(json.dumps({
        "load_kwargs": {"stride": 1, "first": None, "saving_interval_ps": 10.0},
        "results": {"cluster": {
            "status": "ok",
            "message": "cluster: ok (kmeans); not plotted, the memory ran out: "
                       "cluster_rmsd_matrix.png"}}}), encoding="utf-8")
    (folder / "options.json").write_text(json.dumps({
        "options": {"methods": ["kmeans"]},
        "findings": {"figures_not_plotted": {"cluster_rmsd_matrix.png": (
            "cluster_rmsd_matrix.png was not plotted: the memory ran out plotting it "
            "(std::bad_alloc). Its data is saved. Set `stride: 2` under the analysis's "
            "`options: {cluster: ...}` to plot half as many frames.")}}}),
        encoding="utf-8")
    return root


def test_the_server_gives_each_analysis_what_was_not_plotted(tmp_path):
    from fastmdxplora.gui.server import _results_payload

    root = _clustering_short_of_figures(tmp_path / "study")
    record = next(each for each in _results_payload(root)["analyses"]
                  if each["name"] == "cluster")
    assert [each.get("file") for each in record.get("not_plotted", [])] == [
        "cluster_rmsd_matrix.png"], record
    assert record.get("refused_before_it_ran") is False, record


@pytest.mark.parametrize("width", [1280, 390])
def test_a_figure_not_plotted_for_memory_is_said_in_its_section(tmp_path, width):
    """The Clustering section said "3 figures" and showed them, and nothing
    visible on the page said that the map was not plotted, or what to set:
    the words were in the record and the CLI's warning alone."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    root = _clustering_short_of_figures(tmp_path / "study")
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": width, "height": 900})
            page.set_default_timeout(60000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url + "#analysis", wait_until="domcontentloaded")
            note = "#analysis-sections [data-not-plotted]"
            page.wait_for_selector(note, state="visible")
            said = page.inner_text(note)
            count = page.eval_on_selector(
                note, "(n) => n.closest('.analysis-section')"
                      ".querySelector('.analysis-section-count').innerText")
            sideways = page.evaluate(
                "() => document.documentElement.scrollWidth > "
                "document.documentElement.clientWidth")
            browser.close()
    finally:
        session.server.shutdown()
    assert not errors, errors
    # Named by its card's title, not its file.
    assert "Cluster RMSD Matrix" in said and "cluster_rmsd_matrix.png" not in said, said
    assert ("Set stride: 2 under the analysis's options: {cluster: ...} to plot half "
            "as many frames.") in said and "`" not in said, said
    assert count.endswith("1 not plotted"), count
    assert not sideways


def test_the_figures_not_plotted_are_named_by_their_cards_titles(tmp_path):
    """"Not plotted for memory: cluster_hierarchical_dendrogram.png,
    cluster_dbscan.png", where the cards are titled "Hierarchical
    dendrogram" and "DBSCAN trajectory scatter"."""
    from fastmdxplora.gui.server import _results_payload

    root = _clustering_short_of_figures(tmp_path / "study")
    options = root / "analysis" / "cluster" / "options.json"
    record = json.loads(options.read_text())
    record["findings"]["figures_not_plotted"] = {
        name: f"{name} was not plotted." for name in (
            "cluster_hierarchical_dendrogram.png", "cluster_dbscan.png")}
    options.write_text(json.dumps(record), encoding="utf-8")
    entry = next(each for each in _results_payload(root)["analyses"]
                 if each["name"] == "cluster")
    assert [each.get("title") for each in entry["not_plotted"]] == [
        "Hierarchical dendrogram", "DBSCAN trajectory scatter"], entry["not_plotted"]


def test_the_line_says_the_room_a_figure_needs():
    """The line under the heading said the stride its record gave; for a
    figure whose canvas no stride makes smaller it says the room."""
    said = ("cluster_hierarchical_dendrogram.png was not plotted: the memory ran out "
            "plotting it (std::bad_alloc). Its data is saved. Its canvas is the same at "
            "any stride, so a stride does not help: run it where `ulimit -v` allows "
            "about 40.0 MB more.")
    picked = _node(_function("dashboard.js", "whatToSet") + "\n"
                   f"console.log(JSON.stringify(whatToSet({json.dumps(said)})));")
    assert picked == ("Its canvas is the same at any stride, so a stride does not help: "
                      "run it where `ulimit -v` allows about 40.0 MB more."), picked


def test_a_section_whose_analysis_was_refused_before_it_ran_counts_it_so(tmp_path):
    """The card said "the analysis was refused before it ran" and the count
    beside the section's heading "not finished"."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session
    from tests.test_an_analysis_is_drawn_from_its_numbers import _figure

    root = tmp_path / "study"
    (root / "analysis" / "rmsd").mkdir(parents=True)
    (root / "analysis" / "cluster").mkdir(parents=True)
    _figure(root / "analysis" / "rmsd" / "rmsd.png")
    (root / "analysis" / "analysis_manifest.json").write_text(json.dumps({
        "load_kwargs": {"stride": 1, "first": None, "saving_interval_ps": 10.0},
        "results": {"rmsd": {"status": "ok", "message": "rmsd: ok"},
                    "cluster": {"status": "error", "message": CLUSTER_REFUSED}}}),
        encoding="utf-8")
    (root / "analysis" / "cluster" / "options.json").write_text(json.dumps({
        "options": {}, "findings": {"refused": {
            "code": "analysis.memory.matrix_too_large", "said": CLUSTER_REFUSED,
            "before_it_ran": True}}}), encoding="utf-8")
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.set_default_timeout(60000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url + "#analysis/cluster", wait_until="domcontentloaded")
            card = '#analysis-sections .analysis-card[data-analysis="cluster"]'
            page.wait_for_selector(card, state="visible")
            said = page.inner_text(card)
            count = page.eval_on_selector(
                card, "(c) => c.closest('.analysis-section')"
                      ".querySelector('.analysis-section-count').innerText")
            browser.close()
    finally:
        session.server.shutdown()
    assert not errors, errors
    assert "refused before it ran" in said, said
    assert count == "refused", count
