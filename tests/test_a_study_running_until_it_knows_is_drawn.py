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


def _its_own_process(root: Path) -> None:
    """The study's process record, naming this process: a study known to
    be running."""
    import os

    from tests.test_a_run_that_ended_unsaid_is_interrupted import _its_process

    _its_process(root, os.getpid())


class TestWhatThePageIsGiven:
    def test_each_measure_round_by_round(self, finished):
        root, _ = finished
        page = stopping_payload(root)
        assert page["ok"] and page["outcome"] == "met"
        assert page["outcome_label"] == "Determined as asked"
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
        _its_own_process(root)
        page = stopping_payload(root)
        assert page["outcome"] == "running" and page["outcome_label"] == "Running"
        assert page["live"] is True
        last = page["rounds"][-1]
        assert last["decision"] == "extend"
        # The piece now running, and its time at the study's own speed: the
        # three runs side by side, so one run's time.
        assert page["next"]["more_ns"] == last["more_ns"]
        assert page["next"]["to_ns"] == pytest.approx(last["production_ns"] + last["more_ns"])
        assert page["next"]["seconds"] == pytest.approx(last["more_ns"] * 1e6 / 2.0 / 1000.0)
        assert page["next"]["platform"] == "CUDA"

    def test_a_record_still_running_is_not_said_running_unless_its_process_is(self, finished):
        """The card said "Still running" of any record left saying it, which
        a study killed where its process cannot be asked leaves too."""
        root, mid = finished
        (root / "stopping.json").write_text(json.dumps(mid), encoding="utf-8")
        page = stopping_payload(root)
        assert page["outcome"] == "running" and page["live"] is False
        assert page["outcome_label"] == "Not finished when this was written"
        assert page["next"] is not None

    @pytest.mark.skipif(not Path("/proc/self/stat").is_file(), reason="reads /proc")
    def test_a_process_given_its_number_since_is_not_its_run(self, finished):
        """A process named `fastmdx` begun after the record was written was
        taken for the run of a record with no arguments: "Running"."""
        import subprocess
        import sys
        from datetime import datetime, timedelta, timezone

        from tests.test_a_run_that_ended_unsaid_is_interrupted import _its_process

        root, mid = finished
        (root / "stopping.json").write_text(json.dumps(mid), encoding="utf-8")
        written = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        stand_in = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)",
                                     "fastmdx"])
        try:
            _its_process(root, stand_in.pid, started=written)
            page = stopping_payload(root)
            assert page["live"] is False and page["outcome_label"] != "Running"
        finally:
            stand_in.kill()
            stand_in.wait()
        assert page["outcome_label"] == "Not finished when this was written"
        assert page["next"] is not None

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


def _killed_in_its_round(root: Path, mid: dict, where: Path) -> Path:
    """The study as it stood mid-round, one of its runs killed while it was
    extended: its process gone, no end written."""
    from tests.test_a_run_that_ended_unsaid_is_interrupted import (
        _a_gone_process, _a_run_said_it_was_going, _its_process)

    shutil.copytree(root, where)
    (where / "stopping.json").write_text(json.dumps(mid), encoding="utf-8")
    run = where / "runs" / "seed1"
    _a_run_said_it_was_going(run)
    _its_process(run, _a_gone_process())
    return where


@pytest.mark.skipif(not Path("/proc/self/stat").is_file(), reason="reads /proc")
def test_a_study_killed_in_its_round_is_not_said_still_running(finished, tmp_path):
    """Its header and Timeline said it stopped without finishing; the card
    said "Still running" and "Now running" from the record it wrote as it
    ran."""
    from fastmdxplora.simulation.sampling_ask import length_said

    root, mid = finished
    killed = _killed_in_its_round(root, mid, tmp_path / "killed")
    page = stopping_payload(killed)
    assert page["outcome"] == "interrupted" and page["recorded_outcome"] == "running"
    assert page["outcome_label"] == "Stopped without finishing" and page["next"] is None
    last = page["rounds"][-1]
    assert page["said"] == (
        f"Stopped without finishing, in seed1, in round {last['round']}: extending each run by "
        f"{length_said(last['more_ns'])}; the record does not say it finished; its process is "
        "gone, and it wrote no end."), page["said"]
    assert last["decided"].startswith("extending each run by ")
    assert "running" not in page["outcome_label"].lower()
    # A study still going is said so: its run wrote a moment ago, and no
    # process of its is known to be gone.
    (killed / "runs" / "seed1" / ".fastmdxplora_run.json").unlink()
    assert stopping_payload(killed)["outcome"] == "running"


@pytest.mark.skipif(not Path("/proc/self/stat").is_file(), reason="reads /proc")
def test_a_study_killed_in_its_first_piece_is_not_said_running(tmp_path):
    """Killed before any round was judged, the card said "Stopped without
    finishing: running its first piece"."""
    from fastmdxplora.simulation.stopping import planned_record
    from tests.test_a_run_that_ended_unsaid_is_interrupted import (
        _a_gone_process, _a_run_said_it_was_going, _its_process)

    root = tmp_path / "study"
    root.mkdir()
    rule = {**RULE, "independent_starts": "not_required"}
    (root / "stopping.json").write_text(json.dumps(planned_record(
        targets_of(rule), rule, ["study"])), encoding="utf-8")
    _a_run_said_it_was_going(root)
    _its_process(root, _a_gone_process())
    page = stopping_payload(root)
    assert page["outcome"] == "interrupted" and page["rounds"] == []
    assert page["said"] == ("Stopped without finishing in its first piece; its process is "
                            "gone, and it wrote no end."), page["said"]
    assert "running" not in page["said"].lower() and "running" not in page["outcome_label"].lower()


@pytest.mark.skipif(not Path("/proc/self/stat").is_file(), reason="reads /proc")
def test_every_run_killed_is_named_as_every_page_names_it(tmp_path):
    """The card named a killed run by its folder, and only the first of
    them: "in s1__random-seed-1" where the runs list and the Timeline say
    "seed 1", with seed 2 killed too."""
    from fastmdxplora.simulation.stopping import planned_record
    from tests.test_a_run_that_ended_unsaid_is_interrupted import (
        _a_gone_process, _a_run_said_it_was_going, _its_process)
    from tests.test_the_overview_of_a_study_of_runs import _a_study_of_runs

    root = _a_study_of_runs(tmp_path, "simulation.random_seed", [1, 2])
    runs = sorted((root / "runs").iterdir())
    (root / "stopping.json").write_text(json.dumps(planned_record(
        targets_of(RULE), RULE, [run.name for run in runs])), encoding="utf-8")
    for run in runs:
        _a_run_said_it_was_going(run)
        _its_process(run, _a_gone_process())
    page = stopping_payload(root)
    assert page["said"] == ("Stopped without finishing in its first piece, in seed 1 and seed 2; "
                            "its process is gone, and it wrote no end."), page["said"]
    assert stopping_payload(runs[1])["said"] == page["said"]


@pytest.mark.skipif(not Path("/proc/self/stat").is_file(), reason="reads /proc")
def test_every_run_killed_is_said_as_it_ended(tmp_path):
    """The card gave how the first run killed ended for all of them: "its
    process is gone" of a run that fell silent with no process to ask."""
    from datetime import datetime, timedelta, timezone

    from tests.test_a_run_that_ended_unsaid_is_interrupted import (
        _a_gone_process, _a_run_said_it_was_going, _its_process)

    root = _a_campaign_killed_in_its_first_piece(tmp_path)
    runs = sorted((root / "runs").iterdir())
    (runs[1] / ".fastmdxplora_run.json").unlink()
    _a_run_said_it_was_going(runs[1], last=(
        datetime.now(timezone.utc) - timedelta(days=2)).isoformat())
    page = stopping_payload(root)
    assert page["said"] == (
        "Stopped without finishing in its first piece, in seed 1 and seed 2; in seed 1, its "
        "process is gone, and it wrote no end; in seed 2, nothing written for a day, and no "
        "end written."), page["said"]
    # Ended alike, said once.
    _its_process(runs[1], _a_gone_process())
    assert page["said"] != stopping_payload(root)["said"]
    assert stopping_payload(root)["said"].endswith(
        "in seed 1 and seed 2; its process is gone, and it wrote no end.")


def test_its_lengths_are_said_in_ps_under_1_ns(finished, tmp_path):
    root, mid = finished
    small = tmp_path / "small"
    shutil.copytree(root, small)
    record = json.loads((root / "stopping.json").read_text(encoding="utf-8"))
    record["max_duration_ns"] = 0.0012
    for entry in record["rounds"]:
        entry["production_ns"] = 0.0006
    (small / "stopping.json").write_text(json.dumps(record), encoding="utf-8")
    page = stopping_payload(small)
    assert page["ceiling_said"] == "1.2 ps" and page["production_said"] == "0.6 ps"
    assert {r["production_said"] for r in page["rounds"]} == {"0.6 ps"}


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


def _a_campaign_killed_in_its_first_piece(tmp_path: Path) -> Path:
    from fastmdxplora.simulation.stopping import planned_record
    from tests.test_a_run_that_ended_unsaid_is_interrupted import (
        _a_gone_process, _a_run_said_it_was_going, _its_process)
    from tests.test_the_overview_of_a_study_of_runs import _a_study_of_runs

    root = _a_study_of_runs(tmp_path, "simulation.random_seed", [1, 2])
    runs = sorted((root / "runs").iterdir())
    (root / "stopping.json").write_text(json.dumps(planned_record(
        targets_of(RULE), RULE, [run.name for run in runs])), encoding="utf-8")
    for run in runs:
        _a_run_said_it_was_going(run)
        _its_process(run, _a_gone_process())
    return root


def _linked_out(root: Path, name: str, outside: Path, change) -> None:
    """The study's ``name`` moved outside it, changed, and linked back."""
    record = json.loads((root / name).read_text(encoding="utf-8"))
    change(record)
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_text(json.dumps(record), encoding="utf-8")
    (root / name).unlink()
    try:
        (root / name).symlink_to(outside)
    except OSError:
        pytest.skip("this system cannot create symbolic links")


@pytest.mark.skipif(not Path("/proc/self/stat").is_file(), reason="reads /proc")
@pytest.mark.parametrize("public", [False, True], ids=["loopback", "public"])
def test_the_card_reads_only_the_study(tmp_path, public):
    """`/api/stopping` alone of the study's routes was not held to the
    study: its record and its batch manifest linked out of it were read and
    shown, beyond loopback too, a run named by the outside manifest's
    values."""
    from tests.test_the_study_pages_read_only_the_study import _ask

    root = _a_campaign_killed_in_its_first_piece(tmp_path / "a")
    run = sorted((root / "runs").iterdir())[1]
    # Nothing linked out: served as it reads.
    for served in (root, run):
        code, body = _ask(served, tmp_path, "/api/stopping", public=public)
        page = json.loads(body)
        assert code == 200 and "unavailable" not in page
        assert page["said"] == stopping_payload(served)["said"]
    assert "in seed 1 and seed 2" in page["said"], page["said"]

    def outside_values(manifest):
        for entry in manifest.get("planned") or manifest["runs"]:
            entry["sweep_values"] = {"simulation.random_seed": "OUTSIDE-" + entry["run_id"][-1]}
    _linked_out(root, "batch_manifest.json", tmp_path / "elsewhere" / "batch.json",
                outside_values)
    assert "OUTSIDE-1" in stopping_payload(root)["said"]
    for served, named in ((root, "batch_manifest.json"), (run, "../../batch_manifest.json")):
        code, body = _ask(served, tmp_path, "/api/stopping", public=public)
        page = json.loads(body)
        assert code == 200 and "OUTSIDE" not in body.decode(), page["said"]
        assert page["ok"] and page["said"].startswith("Stopped without finishing in its first piece")
        assert page["unavailable_why"] == {named: "outside"}, page.get("unavailable_why")
        assert f"{named} was not read" in page["not_read"]

    other = _a_campaign_killed_in_its_first_piece(tmp_path / "b")
    run = sorted((other / "runs").iterdir())[1]
    _linked_out(other, "stopping.json", tmp_path / "elsewhere" / "stopping.json",
                lambda record: record.update({"said": "SECRET OUTSIDE SENTENCE",
                                              "outcome": "stopped"}))
    for served, named in ((other, "stopping.json"), (run, "../../stopping.json")):
        code, body = _ask(served, tmp_path, "/api/stopping", public=public)
        page = json.loads(body)
        assert code == 200 and "SECRET" not in body.decode()
        assert page["ok"] is False and page["reason"] == "its record of its rounds was not read"
        assert page["unavailable_why"] == {named: "outside"}, page.get("unavailable_why")
        assert f"{named} was not read" in page["not_read"]


def test_the_overview_draws_it(finished, tmp_path) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    root, mid = finished
    running = tmp_path / "running"
    shutil.copytree(root, running)
    (running / "stopping.json").write_text(json.dumps(mid), encoding="utf-8")
    left = tmp_path / "left"
    shutil.copytree(running, left)
    _its_own_process(running)
    killed = _killed_in_its_round(root, mid, tmp_path / "killed")
    seen = {}
    for name, where in (("finished", root), ("running", running), ("left", left),
                        ("killed", killed)):
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
                    "title": page.text_content("#stopping-card .card-title"),
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
                    "card": page.inner_text("#stopping-card"),
                    "errors": errors,
                }
                browser.close()
        finally:
            session.server.shutdown()
    done, now = seen["finished"], seen["running"]
    rounds = len(json.loads((root / "stopping.json").read_text())["rounds"])
    assert done["outcome"] == "Determined as asked" and done["states"] == ["met", "met"]
    assert done["points"] == rounds and done["met"] == 1 and done["replicas"] == 3 * rounds
    assert done["projection"] == 0 and done["target"] == "asked ±0.01 nm"
    assert done["strip"][0] == f"{json.loads((root / 'stopping.json').read_text())['rounds'][-1]['production_ns']:g} ns of 20 ns"
    assert done["strip"][1] == "3 replicas" and done["rows"] == rounds
    assert done["labels"] == 4
    assert now["outcome"] == "Running" and now["projection"] >= 1
    assert now["title"] == "Running until it is determined"
    assert now["strip"][3].startswith("+") and "about" in now["strip"][3]
    assert "now running" in now["card"].lower()
    assert done["errors"] == [] and now["errors"] == []
    # Not known to be running: as the record stood when written, and its
    # title, as a stopped study's, the rule's name.
    left = seen["left"]
    assert left["outcome"] == "Not finished when this was written" and left["errors"] == []
    assert left["title"] == "Run until it is determined" == done["title"]
    assert "running" not in left["card"].lower(), left["card"]
    # Killed in its round: as its header and Timeline say it, never running.
    gone = seen["killed"]
    assert gone["outcome"] == "Stopped without finishing" and gone["errors"] == []
    assert "running" not in gone["card"].lower(), gone["card"]
    assert gone["projection"] >= 1 and len(gone["strip"]) == 3


def test_the_card_says_a_record_it_did_not_read(tmp_path) -> None:
    """With its record linked out of the study, the card was hidden, and the
    Overview said nowhere which record was not read; with its batch manifest
    linked out, the card named the runs by folder with no word of why."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    record_out = _a_campaign_killed_in_its_first_piece(tmp_path / "a")
    _linked_out(record_out, "stopping.json", tmp_path / "elsewhere" / "stopping.json",
                lambda record: record.update({"said": "SECRET OUTSIDE SENTENCE"}))
    manifest_out = _a_campaign_killed_in_its_first_piece(tmp_path / "b")
    _linked_out(manifest_out, "batch_manifest.json", tmp_path / "elsewhere" / "batch.json",
                lambda manifest: None)
    seen = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
        try:
            for name, where in (("record", record_out), ("manifest", manifest_out)):
                session = start_dashboard_session(output=str(where), host="127.0.0.1", port=0)
                try:
                    page = browser.new_page(viewport={"width": 1400, "height": 1000})
                    page.set_default_timeout(60000)
                    errors: list[str] = []
                    page.on("pageerror", lambda error, into=errors: into.append(str(error)))
                    page.goto(session.url + "#overview", wait_until="domcontentloaded")
                    page.wait_for_selector("#stopping-card:not([hidden])")
                    seen[name] = {
                        "outcome": page.text_content("#stopping-outcome"),
                        "title": page.text_content("#stopping-card .card-title"),
                        "said": page.text_content("#stopping-said"),
                        "note": page.evaluate(
                            "() => { const n = document.getElementById('stopping-not-read');"
                            " return n && !n.hidden ? n.textContent : null; }"),
                        "measures": page.locator(".stopping-measure").count(),
                        "card": page.inner_text("#stopping-card"),
                        "errors": errors,
                    }
                    page.close()
                finally:
                    session.server.shutdown()
        finally:
            browser.close()
    unread = seen["record"]
    assert (unread["outcome"], unread["title"]) == ("Not read", "Run until it is determined")
    assert unread["said"].startswith("stopping.json was not read"), unread["said"]
    assert unread["measures"] == 0 and unread["note"] is None and unread["errors"] == []
    assert "SECRET" not in unread["card"]
    read = seen["manifest"]
    assert read["outcome"] == "Stopped without finishing" and read["errors"] == []
    assert read["said"].startswith("Stopped without finishing in its first piece")
    assert read["note"] and read["note"].startswith("batch_manifest.json was not read"), read
