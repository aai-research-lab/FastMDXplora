"""A study running until it knows is drawn as it runs.

`simulation.stop_when` judges a study after each piece. The Overview now
answers the question a person watching it has, the rule's own: is the
answer settling down? For each measure it draws the error after each round
against the error asked for, where the error would reach it at the rate it
has fallen, and the mean with each replica's own mean beside it; it says
what the piece now running adds and how long that takes here.

The fixtures are records the loop itself wrote (`run_until_known`), read
mid-run from inside its extension and at its end.
"""

from __future__ import annotations

import json
import shutil
import urllib.request
from pathlib import Path

import pytest

from fastmdxplora.gui.stopping_view import stopping_payload
from fastmdxplora.simulation.stopping import run_until_known, targets_of

RULE = {"measures": [{"analysis": "rmsd", "standard_error": 0.01},
                     {"analysis": "rg", "relative_error": 0.02}],
        "max_duration_ns": 20}


def _mean(run: Path, analysis: str, mean: float, error: float, unit: str) -> None:
    where = run / "analysis" / analysis
    where.mkdir(parents=True, exist_ok=True)
    (where / "options.json").write_text(json.dumps({"analysis": analysis, "findings": {"mean": {
        "mean": mean, "standard_error": error, "n_frames": 1000, "discard": 0,
        "unit": unit}}}), encoding="utf-8")


def _study(root: Path, monkeypatch, *, rmsd=(0.20, 0.201, 0.199), error=0.03) -> dict:
    """Three replicas whose errors fall a little more slowly than one over
    the root of their production, as correlated sampling does, so the
    loop needs more than one extension; returns the record as it stood
    while the second extension ran, and leaves the finished one in
    ``root``."""
    runs = [root / "runs" / f"seed{i}" for i in range(3)]
    production = {run: 2.0 for run in runs}

    def write(run):
        scale = (2.0 / production[run]) ** 0.45
        _mean(run, "rmsd", rmsd[runs.index(run)], error * scale, "nm")
        _mean(run, "rg", 1.0 + 0.002 * runs.index(run), 0.04 * scale, "nm")
    for run in runs:
        write(run)
    # The speed a round's time is estimated from: 1,000 steps a second.
    (runs[0] / "simulation").mkdir(parents=True)
    (runs[0] / "simulation" / "cost.json").write_text(json.dumps(
        {"steps": 100_000, "seconds": 100.0, "timestep_fs": 2.0, "platform": "CUDA"}),
        encoding="utf-8")
    mid = {}

    def extend_all(chosen, more_ns):
        now = json.loads((root / "stopping.json").read_text(encoding="utf-8"))
        if len(now["rounds"]) == 2:
            mid["record"] = now
        for run in chosen:
            production[run] += more_ns
            write(run)
        return [{"ok": True} for _ in chosen]

    monkeypatch.setattr("fastmdxplora.simulation.resume.production_done_ns",
                        lambda run: production[Path(run)])
    run_until_known(runs, targets_of(RULE), RULE, record_in=root, extend_all=extend_all,
                    say=lambda _: None, at_once=3)
    return mid


@pytest.fixture
def finished(tmp_path, monkeypatch):
    root = tmp_path / "study"
    mid = _study(root, monkeypatch)
    return root, mid["record"]


class TestWhatThePageIsGiven:
    def test_each_measure_round_by_round(self, finished):
        root, _ = finished
        page = stopping_payload(root)
        assert page["ok"] and page["outcome"] == "met"
        assert page["outcome_label"] == "Known as asked"
        assert page["runs"] == ["seed0", "seed1", "seed2"] and page["ceiling_ns"] == 20
        rmsd, rg = page["measures"]
        assert (rmsd["label"], rmsd["unit"], rmsd["asked"]) == ("RMSD", "nm", "±0.01 nm")
        assert (rg["label"], rg["asked"], rg["relative"]) == ("Radius of gyration", "±2%", True)
        assert rmsd["state"] == "met" and rmsd["projection"] is None
        assert [p["production_ns"] for p in rmsd["points"]] == [
            r["production_ns"] for r in page["rounds"]]
        first = rmsd["points"][0]
        assert first["measured"] and first["agree"] is True and not first["met"]
        assert [r["run"] for r in first["replicas"]] == ["seed0", "seed1", "seed2"]
        assert page["rounds"][0]["decision"] == "extend" and page["rounds"][-1]["decision"] == "met"
        assert page["next"] is None

    def test_while_it_runs(self, finished):
        root, mid = finished
        (root / "stopping.json").write_text(json.dumps(mid), encoding="utf-8")
        page = stopping_payload(root)
        assert page["outcome"] == "running" and page["outcome_label"] == "Still running"
        last = page["rounds"][-1]
        assert last["decision"] == "extend"
        # The piece now running, and its time at the study's own speed: the
        # three runs side by side, so one run's time.
        assert page["next"]["more_ns"] == last["more_ns"]
        assert page["next"]["to_ns"] == pytest.approx(last["production_ns"] + last["more_ns"])
        assert page["next"]["seconds"] == pytest.approx(last["more_ns"] * 1e6 / 2.0 / 1000.0)
        assert page["next"]["platform"] == "CUDA"

    def test_the_projection_is_the_arithmetic_the_next_piece_was_sized_by(self, finished):
        root, mid = finished
        (root / "stopping.json").write_text(json.dumps(mid), encoding="utf-8")
        rmsd = stopping_payload(root)["measures"][0]
        proj = rmsd["projection"]
        last = rmsd["points"][-1]
        assert proj["from_ns"] == last["production_ns"]
        assert proj["to_ns"] == pytest.approx(last["production_ns"] + last["more_ns"])
        # At the projected production the error falls to what was asked.
        at_end = proj["error"] * (proj["kept_ns"] / (proj["kept_ns"] + proj["to_ns"]
                                                     - proj["from_ns"])) ** 0.5
        assert at_end == pytest.approx(proj["allowed"])

    def test_one_replicas_folder_finds_the_studys_record(self, finished):
        root, _ = finished
        assert stopping_payload(root / "runs" / "seed1")["outcome"] == "met"

    def test_a_study_of_a_fixed_length(self, tmp_path):
        assert stopping_payload(tmp_path) == {"ok": False,
                                              "reason": "this study runs for a fixed length"}

    def test_a_run_name_is_never_a_path(self, finished):
        from fastmdxplora.gui.stopping_view import _run_folder

        root, _ = finished
        assert _run_folder(root, "seed0") == root / "runs" / "seed0"
        assert _run_folder(root, "../seed0") is None and _run_folder(root, "..") is None
        assert _run_folder(root, "study") == root

    def test_replicas_that_disagree(self, tmp_path, monkeypatch):
        root = tmp_path / "study"
        _study(root, monkeypatch, rmsd=(0.10, 0.30, 0.20), error=0.001)
        page = stopping_payload(root)
        rmsd = page["measures"][0]
        assert page["outcome"] == "ceiling" and rmsd["state"] == "disagree"
        assert all(p["agree"] is False for p in rmsd["points"]) and rmsd["projection"] is None

    def test_the_rule_before_any_round(self, tmp_path):
        from fastmdxplora.simulation.stopping import planned_record

        (tmp_path / "stopping.json").write_text(json.dumps(planned_record(
            targets_of(RULE), RULE, ["seed0", "seed1", "seed2"])), encoding="utf-8")
        page = stopping_payload(tmp_path)
        assert page["outcome"] == "running" and page["rounds"] == []
        assert [m["state"] for m in page["measures"]] == ["waiting", "waiting"]
        assert page["production_ns"] == 0.0 and page["next"] is None


def test_it_is_served_and_answered_beyond_loopback(finished):
    from fastmdxplora.gui.server import GETS_ANSWERED_BEYOND_LOOPBACK, start_dashboard_session

    root, _ = finished
    assert "/api/stopping" in GETS_ANSWERED_BEYOND_LOOPBACK
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
    try:
        page = json.loads(urllib.request.urlopen(session.url + "/api/stopping", timeout=10).read())
    finally:
        session.server.shutdown()
    assert page["ok"] and page["outcome"] == "met"


def test_the_overview_draws_it(finished, tmp_path) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    root, mid = finished
    running = tmp_path / "running"
    shutil.copytree(root, running)
    (running / "stopping.json").write_text(json.dumps(mid), encoding="utf-8")
    seen = {}
    for name, where in (("finished", root), ("running", running)):
        session = start_dashboard_session(output=str(where), host="127.0.0.1", port=0)
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
                page = browser.new_page(viewport={"width": 1400, "height": 1000})
                page.set_default_timeout(60000)
                errors: list[str] = []
                page.on("pageerror", lambda error, into=errors: into.append(str(error)))
                page.goto(session.url + "#overview", wait_until="domcontentloaded")
                page.wait_for_selector("#stopping-card:not([hidden]) .stopping-measure")
                seen[name] = {
                    "outcome": page.text_content("#stopping-outcome"),
                    "measures": page.locator(".stopping-measure").count(),
                    "states": [page.locator(".stopping-measure").nth(i).get_attribute("data-state")
                               for i in range(page.locator(".stopping-measure").count())],
                    "points": page.locator(
                        '.stopping-measure[data-analysis="rmsd"] .stopping-point').count(),
                    "met": page.locator(
                        '.stopping-measure[data-analysis="rmsd"] .stopping-point.is-met').count(),
                    "replicas": page.locator(
                        '.stopping-measure[data-analysis="rmsd"] .stopping-replica').count(),
                    "projection": page.locator(
                        '.stopping-measure[data-analysis="rmsd"] .stopping-projection').count(),
                    "target": page.locator(
                        '.stopping-measure[data-analysis="rmsd"] .stopping-target-label').first.text_content(),
                    "strip": page.locator("#stopping-strip dd").all_text_contents(),
                    "rows": page.locator("#stopping-rounds-table tbody tr").count(),
                    "labels": page.locator('.stopping-svg[aria-label]').count(),
                    "errors": errors,
                }
                browser.close()
        finally:
            session.server.shutdown()
    done, now = seen["finished"], seen["running"]
    rounds = len(json.loads((root / "stopping.json").read_text())["rounds"])
    assert done["outcome"] == "Known as asked" and done["states"] == ["met", "met"]
    assert done["points"] == rounds and done["met"] == 1 and done["replicas"] == 3 * rounds
    assert done["projection"] == 0 and done["target"] == "asked ±0.01 nm"
    assert done["strip"][0] == f"{json.loads((root / 'stopping.json').read_text())['rounds'][-1]['production_ns']:g} ns of 20 ns"
    assert done["strip"][1] == "3 replicas" and done["rows"] == rounds
    assert done["labels"] == 4
    assert now["outcome"] == "Still running" and now["projection"] >= 1
    assert now["strip"][3].startswith("+") and "about" in now["strip"][3]
    assert done["errors"] == [] and now["errors"] == []
