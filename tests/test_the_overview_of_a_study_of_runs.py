"""The Overview of a study of several runs says its runs, not only how many.

It said "A study of 2 runs" and nothing else. It now has the runs as a table
(each one's state, how far its production has got, its speed and its own
means), for replicas the mean of their means with the error their spread
gives it, what is not determined and in which run, and one measure's series
of every run overlaid. A run opens from its row; a run going is read again
as it goes.
"""

from __future__ import annotations

import json
import math
import shutil
from pathlib import Path

import pytest

DONE = {"stage": "completed", "current_step": 500, "total_planned_steps": 500}


def _a_study_of_runs(where: Path, axis: str, values: list) -> Path:
    """Runs of one finished study, swept over ``axis``, each with RMSD and
    Rg recorded about a mean of its own."""
    from fastmdxplora.batch.explorer import BatchExplorer
    from tests.test_a_study_of_runs_is_shown_as_one import _recorded
    from tests.test_the_frame_holds_the_study import _a_finished_study

    root = where / "study"
    study = BatchExplorer(config_data={"systems": [{"system": "1UBQ"}],
                                       "simulation": {"duration_ns": 1.0},
                                       "sweep": {axis: list(values)}},
                          output_dir=str(root))
    root.mkdir(parents=True)
    (root / "runs").mkdir()
    study._write_study_config()
    study._write_batch_manifest()
    finished = Path(_a_finished_study())
    manifest = json.loads((root / "batch_manifest.json").read_text(encoding="utf-8"))
    manifest["runs"] = []
    for i, spec in enumerate(study.run_specs):
        run = study._run_output_dir(spec)
        shutil.copytree(finished, run, dirs_exist_ok=True)
        (run / "simulation" / "live_status.json").write_text(json.dumps(DONE), encoding="utf-8")
        for name, mean in (("rmsd", 0.15 + 0.01 * i), ("rg", 1.2 + 0.002 * i)):
            (run / "analysis" / name / "options.json").write_text(json.dumps({
                "analysis": name, "options": {},
                "findings": {"mean": _recorded(mean)}}), encoding="utf-8")
        manifest["runs"].append({"run_id": spec.run_id, "status": "ok",
                                 "output_dir": str(run), "system": "1UBQ",
                                 "sweep_values": spec.sweep_values})
    (root / "batch_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


@pytest.fixture()
def replicas(tmp_path) -> Path:
    return _a_study_of_runs(tmp_path, "simulation.random_seed", [1, 2, 3])


@pytest.fixture()
def variants(tmp_path) -> Path:
    return _a_study_of_runs(tmp_path, "simulation.temperature_K", [300, 310])


def test_replicas_are_said_with_the_mean_of_their_means(replicas):
    from fastmdxplora.batch.aggregate import aggregate_members
    from fastmdxplora.gui.runs_compared import runs_compared

    said = runs_compared(replicas)
    rmsd = next(m for m in said["measures"] if m["analysis"] == "rmsd")
    entry = aggregate_members(replicas)["analyses"]["rmsd"]
    assert said["replicas"] is True
    assert rmsd["ensemble"]["runs"] == 3
    assert rmsd["ensemble"]["mean"] == pytest.approx(entry["mean_of_means"])
    assert rmsd["ensemble"]["error"] == pytest.approx(entry["spread_of_means"] / math.sqrt(3))
    assert [run["label"] for run in said["runs"]] == ["seed 1", "seed 2", "seed 3"]
    assert all("speed_ns_per_day" in run and "production_ns" in run for run in said["runs"])


def test_variants_are_compared_not_averaged(variants):
    from fastmdxplora.gui.runs_compared import runs_compared

    said = runs_compared(variants)
    assert said["replicas"] is False
    assert all("ensemble" not in m for m in said["measures"])
    # Each run still says how far it went and how fast.
    assert [run["label"] for run in said["runs"]] == ["temperature 300 K", "temperature 310 K"]
    assert all("speed_ns_per_day" in run and "production_ns" in run for run in said["runs"])


def _serve(root: Path, look):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.set_default_timeout(60000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            said = look(page, session.url)
            browser.close()
    finally:
        session.server.shutdown()
    assert errors == []
    return said


TABLE = """() => ({
    shown: !document.getElementById('overview-runs').hidden,
    absent: !document.getElementById('live-absent').hidden,
    said: document.getElementById('overview-runs-said').textContent,
    head: [...document.querySelectorAll('#overview-runs-table thead th')].map((th) => th.textContent),
    rows: [...document.querySelectorAll('#overview-runs-table tbody tr')].map((tr) =>
        [...tr.children].map((c) => c.textContent.trim())),
    together: [...document.querySelectorAll('#overview-runs-table tfoot td, #overview-runs-table tfoot th')]
        .map((c) => c.textContent.trim()),
    overlaid: Number(document.getElementById('overview-runs-chart').dataset.drawn || 0),
})"""


def test_the_overview_shows_the_runs_and_opens_one(replicas):
    def look(page, url):
        page.goto(url + "#overview", wait_until="domcontentloaded")
        page.wait_for_selector("#overview-runs:not([hidden]) #overview-runs-table tbody tr")
        page.wait_for_function("() => Number(document.getElementById('overview-runs-chart')"
                               ".dataset.drawn || 0) > 0")
        table = page.evaluate(TABLE)
        page.click("#overview-runs-table tbody tr:nth-child(2) .overview-runs-open")
        page.wait_for_function(
            "() => (document.querySelector('.page[data-page=overview] .place-run') || {}).textContent"
            " === 'seed 2'")
        return {"table": table, "page": page.evaluate("() => document.documentElement.dataset.page")}

    said = _serve(replicas, look)
    table = said["table"]
    assert table["shown"] and not table["absent"]
    assert table["said"].startswith("3 of 3 runs completed") and "replicas" in table["said"]
    assert table["head"][:4] == ["Run", "State", "Production", "ns/day"]
    assert "RMSD (nm)" in table["head"]
    assert [row[0] for row in table["rows"]] == ["seed 1", "seed 2", "seed 3"]
    assert all(row[1] == "completed" for row in table["rows"])
    assert table["together"][0] == "The runs together"
    assert any("±" in cell for cell in table["together"][1:])
    assert table["overlaid"] == 3
    assert said["page"] == "overview"


def test_a_run_going_is_said_as_it_goes(variants):
    run = sorted((variants / "runs").iterdir())[1]
    (run / "simulation" / "live_status.json").write_text(json.dumps(
        {"stage": "production", "status": "running", "current_step": 200,
         "total_planned_steps": 500}), encoding="utf-8")
    manifest = json.loads((variants / "batch_manifest.json").read_text(encoding="utf-8"))
    manifest["runs"] = manifest["runs"][:1]
    (variants / "batch_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    def look(page, url):
        page.goto(url + "#overview", wait_until="domcontentloaded")
        page.wait_for_selector("#overview-runs:not([hidden]) #overview-runs-table tbody tr")
        first = page.evaluate(TABLE)
        (run / "simulation" / "live_status.json").write_text(json.dumps(
            {"stage": "production", "status": "running", "current_step": 400,
             "total_planned_steps": 500}), encoding="utf-8")
        page.wait_for_function(
            "() => [...document.querySelectorAll('#overview-runs-table tbody tr')]"
            ".some((tr) => tr.textContent.includes('80%'))", timeout=20000)
        return {"first": first, "bar": page.evaluate(
            "() => !!document.querySelector('.overview-runs-fill')")}

    said = _serve(variants, look)
    states = {row[0]: row[1] for row in said["first"]["rows"]}
    assert states["temperature 300 K"] == "completed"
    assert states["temperature 310 K"].endswith("40%")
    assert "compared, not averaged" in said["first"]["said"]
    assert said["first"]["together"] == []
    assert said["bar"]


@pytest.mark.parametrize("public", [False, True], ids=["loopback", "public"])
def test_beyond_loopback_no_run_is_offered_to_compare(variants, public):
    """Bound beyond loopback, Compare is not answered (/api/compared), yet
    each run's row offered a box to choose it to compare; and each run's
    name was a button that switched study, a post the server refused. The
    loopback case is the guard that both are still offered there: it
    passes before the change as after it."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.exploration import DashboardRuntime
    from tests.test_a_public_dashboard_answers_only_what_it_lists import _serving

    runtime = DashboardRuntime(workspace_root=variants, exploration_root=variants.parent,
                               active_root=variants)
    with _serving(runtime, allow_control=not public) as url, sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.set_default_timeout(60000)
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(url + "#overview", wait_until="domcontentloaded")
        page.wait_for_selector("#overview-runs:not([hidden]) #overview-runs-table tbody tr")
        shown = page.evaluate("""() => ({
            bound: document.body.dataset.beyondLoopback,
            boxes: document.querySelectorAll('.overview-runs-pick').length,
            open: document.querySelectorAll('.overview-runs-open').length,
            named: [...document.querySelectorAll('.overview-runs-row th')].map(
                (n) => n.textContent.trim()),
        })""")
        if public:
            # What the runs list sends once drawn, a row clicked and its
            # keys pressed: nothing but reads.
            posted: list[str] = []
            page.on("request", lambda r: posted.append(f"{r.method} {r.url}")
                    if r.method != "GET" else None)
            for nth in (0, 1):
                page.click(f".overview-runs-row >> nth={nth} >> th")
                page.keyboard.press("Enter")
                page.keyboard.press("Space")
            page.wait_for_timeout(1500)
            shown["posted"] = posted
            shown["page"] = page.evaluate("() => location.hash")
        else:
            page.click('.overview-runs-pick >> nth=0')
            page.click('.overview-runs-pick >> nth=1')
            shown["compare"] = page.is_visible("#overview-runs-compare")
        browser.close()
    assert errors == []
    assert shown["bound"] == ("true" if public else "false")
    assert shown["named"] == ["temperature 300 K", "temperature 310 K"], shown
    if public:
        assert shown["boxes"] == 0 and shown["open"] == 0, shown
        assert shown["posted"] == [] and shown["page"] == "#overview", shown
    else:
        assert shown["open"] == 2
        assert shown["boxes"] == 2 and shown["compare"] is True
