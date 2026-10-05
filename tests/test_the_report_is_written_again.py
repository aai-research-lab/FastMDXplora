"""The GUI's Report page writes a study's report again, after asking.

The Report page showed a study's report and could not write it again: a
report written before an analysis was added, or under a release that
writes it better, stayed as it was unless the command line was used.
**Write it again** runs `fastmdx report --output <study> --rerun` on the
study open, through the GUI's own start rule, once asked twice: from the
study's records and the report settings it recorded, the report before
kept in `previous/report`, nothing simulated or analysed.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from unittest import mock

import pytest

from fastmdxplora.cli.main import main
from fastmdxplora.gui.exploration import DashboardRuntime

PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
    b"\x00\x00\x00\x0cIDATx\x9cc```\x00\x00\x00\x04\x00\x01"
    b"\xf6\x178U\x00\x00\x00\x00IEND\xaeB`\x82"
)
RECORDED = {"title": "Recorded title", "slides": False, "pdf": False, "bundle": False}


def _study(root: Path, report: dict | None = RECORDED) -> Path:
    """An analysed study, as an analysis of an existing trajectory leaves one."""
    (root / "analysis" / "rmsd").mkdir(parents=True)
    (root / "analysis" / "rmsd" / "rmsd.png").write_bytes(PNG)
    (root / "analysis" / "analysis_manifest.json").write_text(json.dumps(
        {"plan": ["rmsd"], "n_frames": 4, "results": {"rmsd": {"status": "ok"}}}),
        encoding="utf-8")
    (root / "manifest.json").write_text(json.dumps(
        {"system": "1L2Y", "phases": [{"name": "analysis", "status": "ok"}]}),
        encoding="utf-8")
    config = {"systems": [{"system": "1L2Y"}], "include_phase": ["analysis", "report"]}
    if report is not None:
        config["report"] = report
    import yaml

    (root / "resolved_config.yml").write_text(yaml.safe_dump(config), encoding="utf-8")
    return root


def _title(root: Path) -> str:
    return (root / "report" / "report.md").read_text(encoding="utf-8").splitlines()[0]


def _runtime(root: Path) -> DashboardRuntime:
    runtime = DashboardRuntime(workspace_root=root.parent, exploration_root=root.parent)
    runtime.active_root = root
    return runtime


class TestTheGUI:
    def test_the_report_phase_alone_is_run_in_the_study_open(self, tmp_path):
        root = _study(tmp_path / "study")
        with mock.patch.object(DashboardRuntime, "_spawn",
                               return_value={"launched": True}) as spawn:
            answer = _runtime(root).write_the_report_again()
        command, where, _ = spawn.call_args.args
        assert answer["ok"]
        assert command == [sys.executable, "-m", "fastmdxplora", "report",
                           "--output", str(root.resolve()), "--rerun"]
        assert where == root

    def test_a_run_of_a_campaign_writes_its_own_and_a_campaign_each(self, tmp_path):
        campaign = tmp_path / "campaign"
        run = _study(campaign / "runs" / "seed1")
        (campaign / "batch_manifest.json").write_text("{}", encoding="utf-8")
        with mock.patch.object(DashboardRuntime, "_spawn",
                               return_value={"launched": True}) as spawn:
            assert _runtime(run).write_the_report_again()["ok"]
            assert spawn.call_args.args[1] == run
            each = _runtime(campaign).write_the_report_again()
        assert each["ok"] and "each of its" not in each["said"]
        assert spawn.call_args.args[0][-4:] == ["report", "--output", str(campaign.resolve()),
                                                "--rerun"]

    def test_nothing_open_or_something_running_is_refused(self, tmp_path):
        root = _study(tmp_path / "study")
        empty = tmp_path / "empty"
        empty.mkdir()
        busy = _runtime(root)
        busy.process = mock.Mock(poll=mock.Mock(return_value=None))
        with mock.patch.object(DashboardRuntime, "_refresh_process"), \
                mock.patch.object(DashboardRuntime, "_spawn") as spawn:
            assert busy.write_the_report_again()["error"].startswith("A FastMDXplora workflow")
        with mock.patch.object(DashboardRuntime, "_spawn") as spawn:
            assert _runtime(empty).write_the_report_again()["error"] == "No study is open."
            nobody = _runtime(root)
            nobody.active_root = None
            assert not nobody.write_the_report_again()["ok"]
        spawn.assert_not_called()

    def test_a_study_running_elsewhere_is_refused(self, tmp_path):
        root = _study(tmp_path / "study")
        said = {"ok": False, "error": "A study is running here", "code": "environment.workspace.run_going"}
        with mock.patch.object(DashboardRuntime, "_others_running", return_value=said), \
                mock.patch.object(DashboardRuntime, "_spawn") as spawn:
            assert _runtime(root).write_the_report_again() == said
        spawn.assert_not_called()

    def test_the_route_writes_it_as_recorded(self, tmp_path):
        import urllib.request

        from fastmdxplora.gui.server import start_dashboard_session

        root = _study(tmp_path / "study")
        session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
        base = session.url.rstrip("/")
        try:
            request = urllib.request.Request(
                base + "/api/report/write", data=b"{}", method="POST",
                headers={"Content-Type": "application/json", "Origin": base})
            answer = json.loads(urllib.request.urlopen(request, timeout=30).read())
            report = root / "report" / "report.md"
            deadline = time.time() + 120
            while time.time() < deadline and not (
                    report.is_file() and _title(root) == "# Recorded title"):
                time.sleep(0.5)
        finally:
            session.server.shutdown()
        assert answer["ok"], answer
        assert _title(root) == "# Recorded title"


def test_the_report_page_writes_it_again_after_asking(tmp_path) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    root = _study(tmp_path / "study")
    main(["report", "--output", str(root), "--title", "Before"])
    # Run in this process, the study's record of the run going names it
    # until it exits, and the page would hold Write it again for it.
    from fastmdxplora.orchestrator import RUN_PROCESS_FILE

    (root / RUN_PROCESS_FILE).unlink(missing_ok=True)
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
    asked: list[str] = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1400, "height": 1000})
            page.set_default_timeout(60000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("request", lambda r: asked.append(r.url) if "/api/report/write" in r.url else None)
            page.goto(session.url + "#report", wait_until="domcontentloaded")
            page.wait_for_selector("#report-document:not([hidden]) h1:has-text('Before')")
            page.wait_for_selector("#report-write:not([hidden])")
            page.click("#report-write")
            said = page.text_content("#report-write-ask")
            assert asked == []            # nothing yet: asked first
            page.click("#report-write-ask .fix-confirm")
            page.wait_for_selector("#report-write-ask:has-text('Written again.')", timeout=120000)
            # The title the study recorded last, given when it was written.
            page.wait_for_selector("#report-document h1:has-text('Before')")
            browser.close()
    finally:
        session.server.shutdown()
    assert (root / "previous" / "report" / "report.md").is_file()
    assert said.startswith("Write the report again from this study's records?")
    assert "Nothing is simulated or analysed." in said
    assert len(asked) == 1
    assert errors == []
