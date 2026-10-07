"""Every study in the workspace, as a card, and two of them compared.

The GUI showed one study at a time, and another was opened by walking to
its folder in a picker. The Studies page finds the studies under a folder
and says each in a card: its structure, the kind of study, where it stands,
when it began, the means it recorded with their errors, and a figure it
drew. Search narrows them; two chosen are compared, the settings in which
they differ as `fastmdx diff` says them, and the means both recorded, a
difference marked only past twice their combined error.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from fastmdxplora.gui.workspace import card_of, studies_compared, studies_in, thumbnail_of

#: The smallest PNG there is: one transparent pixel.
PIXEL = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000105fe0dd80000000049454e44ae426082")


def _study(where: Path, *, system="1UBQ", duration=10.0, started="2026-09-01T10:00:00+00:00",
           means=None, failed=None, forcefield="amber14") -> Path:
    where.mkdir(parents=True)
    (where / "resolved_config.yml").write_text(yaml.safe_dump({
        "systems": [{"system": system}], "setup": {"forcefield": forcefield},
        "simulation": {"duration_ns": duration}}), encoding="utf-8")
    phases = [{"name": "setup", "status": "ok", "started_at": started},
              {"name": "simulation", "status": "ok"}]
    if failed:
        phases[1] = {"name": "simulation", "status": "error",
                     "refusal": {"code": failed, "message": "It stopped."}}
    (where / "manifest.json").write_text(json.dumps({"phases": phases}), encoding="utf-8")
    for name, (mean, error) in (means or {}).items():
        folder = where / "analysis" / name
        folder.mkdir(parents=True)
        (folder / "options.json").write_text(json.dumps({"analysis": name, "findings": {
            "mean": {"mean": mean, "standard_error": error, "unit": "nm",
                     "effective_samples": 40}}}), encoding="utf-8")
        (folder / f"{name}.png").write_bytes(PIXEL)
    return where


def _replicas(where: Path) -> Path:
    where.mkdir(parents=True)
    planned = [{"run_id": f"seed{n}", "system": "1UAO", "sweep_values": {
        "simulation.random_seed": n}, "options": {}} for n in (1, 2, 3)]
    (where / "batch_manifest.json").write_text(json.dumps({
        "n_runs": 3, "planned": planned, "sweep": {"simulation.random_seed": [1, 2, 3]},
        "systems": [{"id": "s", "system": "1UAO"}],
        "runs": [{"run_id": p["run_id"], "status": "ok"} for p in planned]}), encoding="utf-8")
    for p in planned:
        (where / "runs" / p["run_id"]).mkdir(parents=True)
    (where / "resolved_config.yml").write_text(yaml.safe_dump({
        "systems": [{"system": "1UAO"}], "sweep": {"simulation.random_seed": [1, 2, 3]},
        "options": {"simulation": {"duration_ns": 5}}}), encoding="utf-8")
    # A campaign records no start time; its folder's is taken. Older than
    # the others here.
    import os
    from datetime import datetime, timezone

    then = datetime(2026, 8, 1, tzinfo=timezone.utc).timestamp()
    os.utime(where, (then, then))
    return where


@pytest.fixture
def workspace(tmp_path) -> Path:
    _study(tmp_path / "ubiquitin", means={"rmsd": (0.1234, 0.0056), "rg": (1.18, 0.002)},
           started="2026-09-02T10:00:00+00:00")
    _study(tmp_path / "project" / "ubiquitin_longer", duration=20,
           means={"rmsd": (0.1500, 0.0040), "rg": (1.181, 0.003)},
           started="2026-09-03T10:00:00+00:00")
    _study(tmp_path / "stopped", system="1L2Y", failed="simulation.run.stopped",
           started="2026-08-30T10:00:00+00:00")
    _replicas(tmp_path / "chignolin")
    (tmp_path / "notes").mkdir()
    return tmp_path


class TestTheCards:
    def test_each_study_once_and_the_newest_first(self, workspace):
        found = studies_in(workspace)
        names = [card["name"] for card in found["studies"]]
        assert found["ok"] and not found["more"]
        assert names[:3] == ["ubiquitin_longer", "ubiquitin", "stopped"]
        # A campaign is one card: its runs are not studies of their own.
        assert sorted(names) == ["chignolin", "stopped", "ubiquitin", "ubiquitin_longer"]

    def test_what_a_card_says(self, workspace):
        card = card_of(workspace / "ubiquitin")
        assert (card["system"], card["kind"], card["state"]) == ("1UBQ", "one run", "completed")
        assert card["production_ns"] == 10 and card["forcefield"] == "amber14"
        assert card["means"][0] == {"analysis": "rmsd", "label": "RMSD", "mean": 0.1234,
                                    "error": 0.0056, "unit": "nm", "withheld": None}
        assert card["thumbnail"]

    def test_where_a_study_stands(self, workspace):
        assert card_of(workspace / "stopped")["state"] == "stopped"
        replicas = card_of(workspace / "chignolin")
        assert (replicas["kind"], replicas["state"], replicas["system"]) == \
            ("3 replicas", "completed", "1UAO")
        assert replicas["production_ns"] == 5

    def test_a_figure_for_the_card(self, workspace):
        assert thumbnail_of(workspace / "ubiquitin").name == "rmsd.png"
        assert thumbnail_of(workspace / "stopped") is None

    def test_no_folder(self, tmp_path):
        assert not studies_in(tmp_path / "nowhere")["ok"]

    def test_the_count_is_bounded(self, workspace):
        found = studies_in(workspace, most=2)
        assert len(found["studies"]) == 2 and found["more"]


def _batch(where: Path, planned: list[dict], *, sweep=None, runs=None) -> Path:
    where.mkdir(parents=True)
    (where / "batch_manifest.json").write_text(json.dumps({
        "n_runs": len(planned), "planned": planned, "sweep": sweep or {},
        "runs": runs if runs is not None else []}), encoding="utf-8")
    return where


class TestEveryKindAndState:
    def test_a_sweep_and_several_systems(self, tmp_path):
        planned = [{"run_id": f"t{k}", "system": "1UBQ", "sweep_values": {
            "simulation.temperature_K": k}} for k in (300, 310)]
        sweep = _batch(tmp_path / "sweep", planned,
                       sweep={"simulation.temperature_K": [300, 310]})
        assert card_of(sweep)["kind"] == "2 runs across temperature_K"
        several = _batch(tmp_path / "several", [{"run_id": "a", "system": "1UBQ"},
                                                {"run_id": "b", "system": "1L2Y"}])
        (several / "resolved_config.yml").write_text(yaml.safe_dump({"systems": [
            {"system": "1UBQ"}, {"system": "1L2Y"}]}), encoding="utf-8")
        card = card_of(several)
        assert (card["kind"], card["system"]) == ("2 runs", "2 systems")
        assert card_of(_batch(tmp_path / "empty", []))["kind"] == "several runs"

    def test_umbrella_windows_and_their_free_energy(self, tmp_path):
        planned = [{"run_id": f"window_{i:02d}", "system": "181L",
                    "options": {"simulation": {"umbrella": {"centre": 0.3 + i / 10}}}}
                   for i in range(3)]
        study = _batch(tmp_path / "umbrella", planned)
        (study / "pmf.json").write_text(json.dumps({"refused": "Windows 1 and 2 do not "
                                                    "overlap. More windows are needed."}),
                                        encoding="utf-8")
        card = card_of(study)
        assert card["kind"] == "umbrella sampling, 3 windows"
        assert card["free_energy"] == {"refused": "Windows 1 and 2 do not overlap."}
        (study / "pmf.json").write_text(json.dumps({"refused": None}), encoding="utf-8")
        (study / "pmf").mkdir()
        (study / "pmf" / "pmf.png").write_bytes(PIXEL)
        assert card_of(study)["free_energy"] == {"recombined": True}
        assert thumbnail_of(study).name == "pmf.png"

    @pytest.mark.parametrize("config,kind", [
        ({"include_phase": ["analysis", "report"]}, "a trajectory analysed"),
        ({"include_phase": ["setup"]}, "prepared"),
        ({"simulation": {"metadynamics": {"collective_variable": "phi"}}}, "metadynamics"),
        ({"simulation": {"steered": {"collective_variable": "rg"}}}, "steered pulling"),
    ])
    def test_the_kinds_of_one_run(self, tmp_path, config, kind):
        study = tmp_path / "one"
        study.mkdir()
        (study / "resolved_config.yml").write_text(
            yaml.safe_dump({"systems": [{"system": "/data/protein.pdb"}], **config}),
            encoding="utf-8")
        card = card_of(study)
        assert card["kind"] == kind and card["system"] == "PROT"
        assert card["structure"] == "protein.pdb"

    def test_campaigns_that_did_not_finish(self, tmp_path):
        planned = [{"run_id": "a"}, {"run_id": "b"}]
        failed = _batch(tmp_path / "failed", planned,
                        runs=[{"run_id": "a", "status": "ok"}, {"run_id": "b", "status": "error"}])
        stopped = _batch(tmp_path / "stopped", planned,
                         runs=[{"run_id": "a", "status": "error", "error_type": "Stopped"},
                               {"run_id": "b", "status": "ok"}])
        waiting = _batch(tmp_path / "waiting", planned, runs=[{"run_id": "a", "status": "ok"}])
        assert [card_of(s)["state"] for s in (failed, stopped, waiting)] == \
            ["failed", "stopped", "incomplete"]

    def test_a_study_running_or_not_started(self, tmp_path, monkeypatch):
        from fastmdxplora.simulation import resume

        study = _study(tmp_path / "going")
        monkeypatch.setattr(resume, "_still_running", lambda root: True)
        assert card_of(study)["state"] == "running"
        monkeypatch.setattr(resume, "_still_running", lambda root: 1 / 0)
        assert card_of(study)["state"] == "completed"
        (study / "manifest.json").write_text("{}", encoding="utf-8")
        assert card_of(study)["state"] == "not started"
        (study / "manifest.json").write_text(json.dumps({"system": "/x/1abc.pdb"}),
                                             encoding="utf-8")
        (study / "resolved_config.yml").write_text("{: not yaml", encoding="utf-8")
        assert card_of(study)["system"] == "1ABC"

    def test_the_figures_a_card_falls_back_to(self, tmp_path):
        study = _study(tmp_path / "s")
        (study / "report").mkdir()
        (study / "report" / "analysis_summary.png").write_bytes(PIXEL)
        assert thumbnail_of(study).name == "analysis_summary.png"
        (study / "report" / "analysis_summary.png").unlink()
        (study / "analysis" / "contacts").mkdir(parents=True)
        (study / "analysis" / "contacts" / "map.png").write_bytes(PIXEL)
        assert thumbnail_of(study).name == "map.png"

    def test_a_folder_that_is_itself_a_study(self, workspace):
        found = studies_in(workspace / "ubiquitin")
        assert [card["name"] for card in found["studies"]] == ["ubiquitin"]

    def test_how_deep_it_looks(self, tmp_path):
        _study(tmp_path / "a" / "b" / "c" / "d")
        assert studies_in(tmp_path, deepest=3)["studies"] == []
        assert len(studies_in(tmp_path, deepest=4)["studies"]) == 1

    def test_a_mean_it_cannot_read(self, workspace, monkeypatch):
        from fastmdxplora.batch import aggregate

        monkeypatch.setattr(aggregate, "read_member_findings", lambda base: 1 / 0)
        assert card_of(workspace / "ubiquitin")["means"] == []


class TestTwoCompared:
    def test_settings_and_means(self, workspace):
        compared = studies_compared(workspace / "ubiquitin",
                                    workspace / "project" / "ubiquitin_longer")
        assert compared["ok"]
        assert [(d["setting"], d["first"], d["second"]) for d in compared["settings"]] == [
            ("simulation.duration_ns", 10.0, 20.0)]
        rows = {m["analysis"]: m for m in compared["measures"]}
        # 0.0266 apart against a combined error of 0.0069: resolved.
        assert rows["rmsd"]["versus"]["resolved"]
        assert rows["rmsd"]["versus"]["difference"] == pytest.approx(0.0266)
        # 0.001 apart against 0.0036: not.
        assert not rows["rg"]["versus"]["resolved"]

    def test_a_folder_that_is_not_a_study(self, workspace):
        assert not studies_compared(workspace / "notes", workspace / "ubiquitin")["ok"]


def test_the_routes(workspace) -> None:
    import urllib.request
    from urllib.parse import urlencode

    from fastmdxplora.gui.server import GETS_ANSWERED_BEYOND_LOOPBACK, start_dashboard_session

    # Folders are listed only where browsing is: never beyond loopback.
    assert not {"/api/studies", "/api/studies-compared",
                "/api/study-thumbnail"} & GETS_ANSWERED_BEYOND_LOOPBACK
    session = start_dashboard_session(output=str(workspace / "ubiquitin"), host="127.0.0.1",
                                      port=0)
    base = session.url.rstrip("/")
    try:
        listed = json.loads(urllib.request.urlopen(
            base + "/api/studies?" + urlencode({"path": str(workspace)}), timeout=30).read())
        compared = json.loads(urllib.request.urlopen(
            base + "/api/studies-compared?" + urlencode(
                {"a": str(workspace / "ubiquitin"), "b": str(workspace / "stopped")}),
            timeout=30).read())
        with urllib.request.urlopen(base + "/api/study-thumbnail?" + urlencode(
                {"path": str(workspace / "ubiquitin")}), timeout=30) as answer:
            figure = (answer.headers["Content-Type"], answer.read())
        try:
            urllib.request.urlopen(base + "/api/study-thumbnail?" + urlencode(
                {"path": str(workspace / "notes")}), timeout=30)
            missing = 200
        except urllib.error.HTTPError as exc:
            missing = exc.code
    finally:
        session.server.shutdown()
    assert len(listed["studies"]) == 4
    assert compared["ok"] and compared["settings"][0]["setting"] == "systems[0].system"
    assert figure == ("image/png", PIXEL)
    assert missing == 404


def test_the_page(workspace) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    session = start_dashboard_session(output=str(workspace / "ubiquitin"), host="127.0.0.1",
                                      port=0)
    switched: list[dict] = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1400, "height": 1000})
            page.set_default_timeout(60000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))

            def switch(route):
                switched.append(json.loads(route.request.post_data or "{}"))
                route.fulfill(json={"ok": False, "error": "Not in this test."})
            page.route("**/api/explore/switch", switch)
            page.goto(session.url + "#studies", wait_until="domcontentloaded")
            page.evaluate(f"() => window.FastMDXStudies.load({str(workspace)!r})")
            page.wait_for_selector(".study-card")
            cards = page.locator(".study-card").count()
            first = page.text_content(".study-card .study-name")
            means = page.text_content('.study-card[data-path$="ubiquitin"] .study-means')
            page.fill("#studies-search", "1l2y")
            searched = page.locator(".study-card").count()
            page.fill("#studies-search", "")
            page.check('.study-card[data-path$="/ubiquitin"] .study-pick input')
            waiting = page.text_content("#studies-chosen-said")
            page.check('.study-card[data-path$="ubiquitin_longer"] .study-pick input')
            page.click("#studies-compare")
            page.wait_for_selector(".studies-means")
            setting = page.text_content(".studies-settings tr:nth-child(2)")
            rmsd = page.text_content('.studies-means tr[data-analysis="rmsd"]')
            resolved = page.get_attribute('.studies-means tr[data-analysis="rmsd"]', "class")
            page.click('.study-card[data-path$="stopped"] .study-open')
            # Said beside its button, which is an icon (and named so for a moment).
            page.wait_for_selector('.study-card[data-path$="stopped"] .study-open-said:has-text("Not in this test.")')
            # Read out once, by the page's notice, not by the words beside it too.
            heard = page.evaluate("""() => [document.getElementById('dashboard-toast').textContent,
                document.querySelector('.study-card[data-path$="stopped"] .study-open-said')
                    .closest('[role="status"], [aria-live]') === null]""")
            # The page opened from the nav loads the default folder itself.
            page.goto(session.url + "#overview", wait_until="domcontentloaded")
            page.wait_for_selector("body.state-ready")
            page.click('[data-view-link="studies"]')
            page.wait_for_function(
                "() => document.getElementById('studies-where').textContent.includes(' in ')")
            browser.close()
    finally:
        session.server.shutdown()
    assert cards == 4 and first == "ubiquitin_longer"
    assert "RMSD0.1234 ± 0.0056 nm" in means
    assert searched == 1
    assert waiting == "Choose one more to compare."
    assert "simulation.duration_ns" in setting and "10" in setting and "20" in setting
    assert "0.0266 ± 0.0069 nm, resolved" in rmsd and "is-resolved" in (resolved or "")
    assert switched == [{"folder": str(workspace / "stopped")}]
    assert heard == ["Not in this test.", True]
    assert errors == []
