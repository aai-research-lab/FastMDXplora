"""A run is said as it ended, on every page that says it.

A run whose record still said "running" after it had taken every step it
planned (the Python API writes no end of its own) was offered `fastmdx
resume` beside a report of the whole run. A run whose record said it failed
(a failure the GUI found after the run's own end is recorded there) was
listed Completed in All studies and Recent beside an Overview saying
"Simulation failed", and its sidebar's **What would fix it** led to the top
of the Overview, the card it names hidden and empty. The record names its
checkpoint from the run's start, and as the run was asked to name it, so a
run stopped before production was offered a checkpoint it never wrote, and
a study run under one name and opened under another named a file in another
folder.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from tests.test_the_sidebar_says_the_study_and_where_its_run_stands import (  # noqa: F401
    _live, _sidebar_of, browser)
from tests.test_the_workspace_says_its_studies import _study


def _days_ago(days: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def test_a_run_that_took_every_step_is_not_told_to_resume(tmp_path):
    from fastmdxplora.remedies import remedies_of

    study = _live(_study(tmp_path / "study"), status="running", stage="production",
                  current_step=55000, total_planned_steps=55000,
                  last_update_timestamp=_days_ago(3))
    assert remedies_of(study) == []


def test_a_run_that_ended_unsaid_part_way_is(tmp_path):
    from fastmdxplora.remedies import remedies_of

    study = _live(_study(tmp_path / "study"), status="running", stage="production",
                  current_step=20000, total_planned_steps=55000,
                  last_update_timestamp=_days_ago(3))
    [remedy] = remedies_of(study)
    assert remedy.code == "simulation.run.interrupted"


def test_a_run_whose_record_says_it_failed_is_listed_failed(tmp_path):
    from fastmdxplora.gui.workspace import _state_of

    study = _live(_study(tmp_path / "study"), status="failed", stage="production",
                  latest_error="The run wrote no trajectory.")
    manifest = json.loads((study / "manifest.json").read_text())
    assert _state_of(study, None, manifest) == "failed"
    stopped = _live(_study(tmp_path / "stopped"), status="stopped", stage="npt")
    assert _state_of(stopped, None, json.loads((stopped / "manifest.json").read_text())) \
        == "stopped"


def test_a_checkpoint_is_named_only_where_the_study_holds_it(tmp_path):
    from fastmdxplora.gui.telemetry import read_study_status

    study = _live(_study(tmp_path / "trp-run"), status="stopped", stage="nvt",
                  current_checkpoint_path="trpcage/simulation/checkpoint.chk")
    assert read_study_status(study)["current_checkpoint_path"] is None
    (study / "simulation" / "checkpoint.chk").write_bytes(b"\0")
    assert read_study_status(study)["current_checkpoint_path"] == str(
        (study / "simulation" / "checkpoint.chk").resolve())
    outside = tmp_path / "trpcage" / "simulation"
    outside.mkdir(parents=True)
    (outside / "checkpoint.chk").write_bytes(b"\0")
    elsewhere = _live(_study(tmp_path / "other"), status="stopped", stage="nvt",
                      current_checkpoint_path=str(outside / "checkpoint.chk"))
    assert read_study_status(elsewhere)["current_checkpoint_path"] is None


def test_the_fix_link_is_shown_only_where_there_is_a_fix(browser, tmp_path):  # noqa: F811
    nothing = _live(_study(tmp_path / "failed"), status="failed", stage="production",
                    latest_error="The run wrote no trajectory.")
    with _sidebar_of(browser, nothing) as (facts, page):
        fixes = page.evaluate("() => document.documentElement.dataset.fixes")
    assert facts["errors"] == []
    assert facts["run"] == "failed" and fixes == "none"
    assert not facts["fix"]


@pytest.mark.parametrize("ended", ["stopped", "failed"])
def test_a_stopped_or_failed_study_s_fix_link_leads_to_its_fix(browser, tmp_path, ended):  # noqa: F811
    study = _study(tmp_path / "study", failed="simulation.run.stopped" if ended == "stopped"
                   else "simulation.unstable")
    _live(study, status=ended, stage="production", current_step=100,
          total_planned_steps=1000, latest_error="It stopped.")
    with _sidebar_of(browser, study) as (facts, page):
        assert facts["fix"], facts
        page.click('.nav-link[data-view-link="analysis"]')
        page.click("#sidebar-fix")
        page.wait_for_function("() => document.documentElement.dataset.page === 'overview'")
        page.wait_for_function("() => !document.getElementById('fixes-card').hidden")
        shown = page.evaluate("() => { const r = document.getElementById('fixes-card')"
                              ".getBoundingClientRect(); return r.top < innerHeight && "
                              "r.bottom > 0; }")
    assert shown


def test_the_fix_link_is_above_the_foot_however_the_sidebar_scrolls(browser, tmp_path):  # noqa: F811
    """In a window short enough for the sidebar to scroll, the card was in
    flow and the pinned foot covered its link: a click there opened the
    settings."""
    study = _study(tmp_path / "study", failed="simulation.unstable")
    _live(study, status="failed", stage="production", current_step=100,
          total_planned_steps=1000, latest_error="It blew up.")
    with _sidebar_of(browser, study, height=560) as (facts, page):
        assert facts["fix"], facts
        hits = page.evaluate("""() => {
          const side = document.querySelector('.sidebar'), hits = [];
          for (const top of [0, side.scrollHeight]) {
            side.scrollTop = top;
            const r = document.getElementById('sidebar-fix').getBoundingClientRect();
            const at = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
            hits.push(at && at.id);
          }
          return [side.scrollHeight > side.clientHeight, ...hits]; }""")
    assert hits == [True, "sidebar-fix", "sidebar-fix"]
