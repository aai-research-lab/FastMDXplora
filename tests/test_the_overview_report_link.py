"""The Overview links to the Report only when that page has something to show."""

from __future__ import annotations

import json
import shutil
from pathlib import Path


def _single_run(root: Path, *, report: bool) -> Path:
    from tests.test_the_frame_holds_the_study import _a_finished_study

    shutil.copytree(_a_finished_study(), root)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    phase_names = ["setup", "simulation", "analysis"]
    if report:
        phase_names.append("report")
    manifest["phases"] = [
        {"name": name, "status": "ok"}
        for name in phase_names
    ]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    if not report:
        shutil.rmtree(root / "report")
    return root


def _pending_batch(root: Path) -> Path:
    from fastmdxplora.batch.explorer import BatchExplorer

    root.mkdir()
    (root / "runs").mkdir()
    batch = BatchExplorer(
        config_data={"systems": [{"system": "1UBQ"}],
                     "simulation": {"duration_ns": 1.0},
                     "sweep": {"simulation.random_seed": [1]}},
        output_dir=str(root),
    )
    batch._write_study_config()
    batch._write_batch_manifest()
    return root


def _open_study(page, workspace: Path, study: Path) -> None:
    page.evaluate("() => window.FastMDXDashboard.navigate('studies')")
    page.evaluate("path => window.FastMDXStudies.load(path)", str(workspace))
    page.wait_for_function(
        "path => [...document.querySelectorAll('[data-path]')].some(item => "
        "item.dataset.path === path && item.querySelector('.study-open'))",
        arg=str(study),
    )
    with page.expect_navigation(wait_until="domcontentloaded"):
        page.evaluate(
            "path => [...document.querySelectorAll('[data-path]')].find(item => "
            "item.dataset.path === path && item.querySelector('.study-open'))"
            ".querySelector('.study-open').click()",
            str(study),
        )
    page.wait_for_function(
        "path => window.FastMDXDashboard?.state?.appState?.active_run === path",
        arg=str(study),
    )


def test_the_overview_opens_reports_and_updates_when_studies_change(tmp_path, monkeypatch) -> None:
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.exploration import DashboardRuntime
    from fastmdxplora.simulation import resume
    from tests.test_a_public_dashboard_answers_only_what_it_lists import _serving
    from tests.test_a_run_between_rounds_is_said_waiting import _study as _rounds_study
    from tests.test_the_overview_of_a_study_of_runs import _a_study_of_runs

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    report_study = _single_run(workspace / "run-report", report=True)
    no_report_study = _single_run(workspace / "run-no-report", report=False)
    unwritten_report_study = _single_run(workspace / "run-report-unwritten", report=True)
    shutil.rmtree(unwritten_report_study / "report")
    completed_batch = _a_study_of_runs(workspace, "simulation.random_seed", [1])
    monkeypatch.setattr(resume, "production_done_ns", lambda run: 44.87)
    so_far_batch = _rounds_study(workspace / "batch-so-far")
    pending_batch = _pending_batch(workspace / "batch-pending")

    runtime = DashboardRuntime(workspace_root=workspace, exploration_root=workspace)
    with _serving(runtime, allow_control=True) as url, sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.set_default_timeout(60000)
        page.goto(url + "#overview", wait_until="domcontentloaded")
        page.wait_for_function(
            "() => window.FastMDXDashboard && "
            "Object.hasOwn(window.FastMDXDashboard.state.appState, 'active_run')"
        )
        assert page.locator("#overview-report-link").is_hidden()

        _open_study(page, workspace, report_study)
        page.wait_for_function(
            "() => window.FastMDXDashboard.state.results.phases?.some(phase => "
            "String(phase.name).toLowerCase() === 'report') && "
            "window.FastMDXDashboard.state.results.reports?.some(report => "
            "report.path === 'report/report.md') && "
            "!document.getElementById('overview-report-link').hidden"
        )
        page.click("#overview-report-link")
        page.wait_for_function(
            "() => document.documentElement.dataset.page === 'report' && "
            "!document.getElementById('report-document').hidden && "
            "document.getElementById('report-document').textContent.includes('A finished run.')"
        )

        _open_study(page, workspace, no_report_study)
        page.wait_for_function(
            "() => window.FastMDXDashboard.state.results.phases?.some(phase => "
            "String(phase.name).toLowerCase() === 'analysis') && "
            "document.getElementById('overview-report-link').hidden"
        )

        _open_study(page, workspace, unwritten_report_study)
        page.wait_for_function(
            "() => window.FastMDXDashboard.state.results.phases?.some(phase => "
            "String(phase.name).toLowerCase() === 'report') && "
            "!window.FastMDXDashboard.state.results.reports?.some(report => "
            "report.path === 'report/report.md') && "
            "document.getElementById('overview-report-link').hidden"
        )

        _open_study(page, workspace, completed_batch)
        page.wait_for_function(
            "() => window.FastMDXDashboard.state.appState.runs?.some(run => "
            "run.state === 'completed') && !document.getElementById('overview-report-link').hidden"
        )
        page.click("#overview-report-link")
        page.wait_for_function(
            "() => document.documentElement.dataset.page === 'report' && "
            "!document.getElementById('report-document').hidden && "
            "document.getElementById('report-document').textContent.trim()"
        )

        _open_study(page, workspace, so_far_batch)
        page.wait_for_function(
            "() => window.FastMDXDashboard.state.appState.runs?.some(run => run.so_far) && "
            "window.FastMDXDashboard.state.appState.runs.every(run => "
            "run.state !== 'completed') && "
            "!document.getElementById('overview-report-link').hidden"
        )

        _open_study(page, workspace, pending_batch)
        page.wait_for_function(
            "() => window.FastMDXDashboard.state.appState.runs?.length && "
            "window.FastMDXDashboard.state.appState.runs.every(run => run.state === 'waiting') && "
            "document.getElementById('overview-report-link').hidden"
        )
        browser.close()
