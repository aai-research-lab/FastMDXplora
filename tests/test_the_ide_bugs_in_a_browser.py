"""The first round of the Integrated MD Study Environment section, as seen.

A study of several runs said "Unknown" in the sidebar and on its Overview and
"completed" in All studies; its runs' dots were orange when completed and
their names cut to "random_see..."; its Analysis page said "no analyses yet"
above runs of fourteen each; the Overview's "Reset zoom" reset no zoom; a
chain numbered from 70 was drawn all red under Spectrum.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_a_study_of_runs_is_shown_as_one import _a_sweep_study  # noqa: E402

DONE = {"stage": "completed", "current_step": 500, "total_planned_steps": 500}


def _serve(root: Path, look, *, width: int = 1440, colour_scheme: str = "dark"):
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": width, "height": 900},
                                    color_scheme=colour_scheme)
            page.set_default_timeout(60000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            said = look(page, session.url)
            browser.close()
    finally:
        session.server.shutdown()
    assert errors == []
    return said


@pytest.fixture(scope="module")
def finished_sweep(tmp_path_factory) -> Path:
    root = _a_sweep_study(tmp_path_factory.mktemp("ide"))
    for folder in sorted((root / "runs").iterdir()):
        (folder / "simulation" / "live_status.json").write_text(json.dumps(DONE), encoding="utf-8")
    return root


@pytest.mark.parametrize("width,scheme", [(1440, "dark"), (900, "light")])
def test_a_finished_study_of_runs_is_completed_everywhere(finished_sweep, width, scheme):
    def look(page, url):
        page.goto(url + "#overview", wait_until="domcontentloaded")
        page.wait_for_selector(".page[data-page=overview] .place-run")
        page.wait_for_function(
            "() => document.getElementById('topbar-status-text').textContent !== 'Ready'")
        page.wait_for_timeout(500)
        said = {
            "word": page.text_content("#topbar-status-text"),
            "dot": page.get_attribute("#topbar-status-dot", "class"),
            "verdict": page.text_content("#verdict-word"),
        }
        # The runs, where they are listed now: the menu at the page's head.
        page.click(".page[data-page=overview] .place-run")
        page.wait_for_selector("#place-menu")
        said["runs"] = page.eval_on_selector_all(
            "#place-menu .place-menu-item:not(:first-child)",
            "rs => rs.map(r => [r.querySelector('.place-menu-label').textContent,"
            " r.title.split('\\n')[1] || '',"
            " r.querySelector('.status-dot').className,"
            " [r.querySelector('.place-menu-label').scrollWidth,"
            "  r.querySelector('.place-menu-label').clientWidth]])")
        page.keyboard.press("Escape")
        page.goto(url + "#studies", wait_until="domcontentloaded")
        page.wait_for_selector(".study-state, .studies-state")
        # The list first: the study's row says its state.
        page.wait_for_selector(".studies-row .studies-state-line")
        said["card"] = page.eval_on_selector_all(
            ".studies-row:not(.studies-child) .studies-state-line",
            "s => s.map(x => x.textContent.trim())")
        return said

    said = _serve(finished_sweep, look, width=width, colour_scheme=scheme)
    assert said["word"] == "Completed" and said["verdict"] == "Completed"
    assert "status-dot-completed" in said["dot"]
    assert said["card"] == ["completed"]
    names = [row[0] for row in said["runs"]]
    assert names == ["temperature 300 K", "temperature 310 K"]
    # The folder's name on hover; each dot green; each name whole.
    assert all(row[1].startswith("s") and "temperature" in row[1] for row in said["runs"])
    assert all("status-dot-completed" in row[2] for row in said["runs"])
    assert all(row[3][0] <= row[3][1] for row in said["runs"]), said["runs"]


def test_the_analysis_of_a_study_of_runs_says_its_runs(finished_sweep):
    def look(page, url):
        page.goto(url + "#analysis", wait_until="domcontentloaded")
        page.wait_for_selector("#runs-compared:not([hidden]) #runs-compared-table tr")
        page.wait_for_function("""root => {
            const state = window.FastMDXDashboard.state;
            return state.appState.active_run === root && state.results.output_dir === root;
        }""", arg=str(finished_sweep))
        # The table is the runs' own script's, and can be in before the page
        # has read the study's results and said its analyses.
        page.wait_for_function("() => document.getElementById('analysis-meta').textContent")
        return page.text_content("#analysis-meta")

    assert _serve(finished_sweep, look) == "the analyses of 2 runs, compared below"


def test_the_analysis_of_a_study_of_runs_says_no_count_before_its_results(finished_sweep):
    """The page said "no analyses yet" above a study of runs until its
    results were read: the template's own words, said before anything was
    known."""
    def look(page, url):
        # Every request to the server held, never answered.
        page.route("**/api/**", lambda route: None)
        page.goto(url + "#analysis", wait_until="domcontentloaded")
        page.wait_for_selector("#analysis-meta", state="attached")
        page.wait_for_timeout(1500)
        return page.text_content("#analysis-meta")

    assert _serve(finished_sweep, look) == ""


def test_the_runs_table_keeps_its_runs_in_view_and_short_runs_in_ps(finished_sweep):
    def look(page, url):
        page.goto(url + "#analysis", wait_until="domcontentloaded")
        page.wait_for_selector("#runs-compared:not([hidden]) #runs-compared-table tr")
        page.wait_for_timeout(500)
        sticky = page.eval_on_selector(
            "#runs-compared-table tbody tr > :first-child", "c => getComputedStyle(c).position")
        page.set_viewport_size({"width": 700, "height": 900})
        page.wait_for_timeout(400)
        more = page.eval_on_selector("#runs-compared .runs-compared-scroll",
                                     "s => [s.scrollWidth > s.clientWidth, s.dataset.more || '']")
        return {"sticky": sticky, "more": more,
                "ps": page.evaluate(
                    "() => window.FastMDXSeries.timeScale([0, 0.005, 0.02], 'Time (ns)')"),
                "ns": page.evaluate(
                    "() => window.FastMDXSeries.timeScale([0, 1.5, 20], 'Time (ns)')")}

    said = _serve(finished_sweep, look)
    assert said["sticky"] == "sticky"
    assert said["more"] == [True, "right"]
    assert said["ps"] == {"factor": 1000, "label": "Time (ps)", "unit": "ps"}
    assert said["ns"]["factor"] == 1 and said["ns"]["label"] == "Time (ns)"


def test_the_charts_button_is_named_for_what_it_does(finished_sweep):
    def look(page, url):
        page.goto(url + "#overview", wait_until="domcontentloaded")
        page.wait_for_selector("#chart-reset", state="attached")
        return page.eval_on_selector(
            "#chart-reset", "b => [b.title, b.getAttribute('aria-label')]")

    assert _serve(finished_sweep, look) == ["Show every chart", "Show every chart"]


@pytest.fixture(scope="module")
def numbered_from_seventy(tmp_path_factory) -> Path:
    import mdtraj as md

    from tests.test_the_cartoon_is_dssp_of_each_frame import _helical_study

    root = _helical_study(tmp_path_factory.mktemp("spectrum") / "study", analyse=False)
    sim = root / "simulation"
    frames = md.load_dcd(str(sim / "production.dcd"), top=str(sim / "trajectory_topology.pdb"))
    chain = [a.index for a in frames.topology.atoms if a.residue.chain.index == 0]
    frames = frames.atom_slice(chain)
    for residue in frames.topology.residues:
        residue.resSeq += 69
    frames[0].save_pdb(str(sim / "trajectory_topology.pdb"))
    frames[0].save_pdb(str(root / "setup" / "topology.pdb"))
    frames.save_dcd(str(sim / "production.dcd"))
    return root


def test_spectrum_runs_blue_to_red_whatever_the_numbering(numbered_from_seventy):
    def look(page, url):
        page.goto(url + "#viewer", wait_until="domcontentloaded")
        page.wait_for_function(
            "() => window.FastMDXMoleculeViewer && window.FastMDXMoleculeViewer.STATE.engine"
            " && window.FastMDXMoleculeViewer.STATE.engine.structure()")
        page.wait_for_timeout(1000)
        return page.evaluate("""() => {
            const e = window.FastMDXMoleculeViewer.STATE.engine;
            const SP = e.lib.structure.StructureProperties;
            const reg = e.plugin.representation.structure.themes.colorThemeRegistry;
            const theme = reg.get('fastmdx-spectrum').factory({}, {});
            const ca = [];
            e.forEachAtom((loc) => {
                if (SP.atom.label_atom_id(loc) === 'CA') {
                    ca.push([SP.residue.auth_seq_id(loc), theme.color(loc)]);
                }
            });
            const used = [];
            for (const cell of e.plugin.state.data.cells.values()) {
                const name = cell.params && cell.params.values && cell.params.values.colorTheme
                    && cell.params.values.colorTheme.name;
                if (name) used.push(name);
            }
            return {first: ca[0], last: ca[ca.length - 1], mid: ca[Math.floor(ca.length / 2)],
                    mode: window.FastMDXMoleculeViewer.STATE.colorMode, used,
                    description: theme.description};
        }""")

    said = _serve(numbered_from_seventy, look)
    rgb = lambda c: ((c >> 16) & 255, (c >> 8) & 255, c & 255)  # noqa: E731
    first, last = rgb(said["first"][1]), rgb(said["last"][1])
    assert said["first"][0] == 70 and said["mode"] == "spectrum"
    assert "fastmdx-spectrum" in said["used"]
    # Blue at the chain's first residue, red at its last, not red throughout.
    assert first[2] > first[0] and last[0] > last[2]
    assert len({said["first"][1], said["mid"][1], said["last"][1]}) == 3
    # Said as it is drawn: the turbo scale's dark ends, not "blue" and "red".
    assert max(first) < 80 and last[0] < 160
    assert said["description"].startswith("Each chain from dark violet")
    assert "dark red at its last" in said["description"]
