"""A run's Overview leads with what it needs, says Not determined quietly,
and puts the means beside the run on a wide window.

The Overview of a run that ended opened on a grid of tiles, each with an
orange "Not determined" badge (17 of 19 on a short run), the reason only on
hover, and how much longer the run would need only in the fix card further
down; all of it in one column of 845 px between empty margins. It now leads
with one sentence: how many means are determined and, where some are not,
how much more production the analyses asked for and what that takes at this
run's own speed, said as not known where the records do not give it. A tile
says Not determined in the muted colour, with its reason in a line; and
where the page's column is 1180 px wide or more the means and the run sit
side by side (the column's width, not the window's: the side panel open
beside it leaves too little for two).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from tests.test_a_study_of_runs_is_shown_as_one import _recorded
from tests.test_the_frame_holds_the_study import _a_finished_study

SHORT = ("This run is not long against its own correlation time: 62 frames hold 3.2 "
         "independent samples by their own estimate. The remedy is a longer run, or better, "
         "replicas.")


def _withheld(more_ns: float | None, *, floor: bool = False) -> dict:
    mean = {"mean": 0.15, "standard_error": None, "effective_samples": 3.2, "n_frames": 62,
            "discard": 0, "not_a_measurement": SHORT}
    if more_ns is not None:
        mean["shortfall"] = {"more_ns": more_ns, "target_independent": 10, "more_frames": 100,
                             "independent": 3.2, "statistical_inefficiency": 19.0,
                             **({"lower_bound": True} if floor else {})}
    return mean


def _a_run(where: Path, rmsd: dict, rg: dict, *, cost: bool = True) -> Path:
    root = where / "run"
    shutil.copytree(_a_finished_study(), root)
    for name, mean in (("rmsd", rmsd), ("rg", rg)):
        (root / "analysis" / name / "options.json").write_text(json.dumps({
            "analysis": name, "options": {}, "findings": {"mean": mean}}), encoding="utf-8")
    (root / "simulation" / "live_status.json").write_text(json.dumps({
        "status": "completed", "stage": "production", "current_step": 500,
        "total_planned_steps": 500}), encoding="utf-8")
    if cost:
        (root / "simulation" / "cost.json").write_text(json.dumps({
            "steps": 10000, "seconds": 100.0, "timestep_fs": 2.0, "platform": "CUDA"}),
            encoding="utf-8")
    return root


def _lead(root: Path) -> dict:
    from fastmdxplora.gui.overview_view import overview_payload

    return overview_payload(root)["lead"]


def test_it_says_how_much_longer_and_what_that_takes_here(tmp_path):
    lead = _lead(_a_run(tmp_path, _withheld(2.43), _recorded(1.2)))
    assert lead["said"] == ("1 of 2 means is not determined: about 2.5 ns more production "
                            "should determine it, about 3 h 28 min at this run's speed on CUDA.")
    assert (lead["undetermined"], lead["means"], lead["more_ns"]) == (1, 2, 2.5)


def test_an_ask_written_in_single_precision_is_not_rounded_up_past_itself(tmp_path):
    """0.1 ns written as a float32 (0.10000002384185791) was said as 110 ps."""
    import numpy as np

    from fastmdxplora.simulation.sampling_ask import sampling_asked_for

    root = _a_run(tmp_path, _withheld(float(np.float32(0.1))), _recorded(1.2))
    assert sampling_asked_for(root).more_ns == 0.1
    assert _lead(root)["said"].startswith(
        "1 of 2 means is not determined: about 100 ps more production")


def test_a_few_seconds_are_said_as_under_a_minute(tmp_path):
    # 2 ps is 1,000 steps of 2 fs: 10 s at 100 s for 10,000 steps.
    lead = _lead(_a_run(tmp_path, _withheld(0.002), _recorded(1.2)))
    assert lead["said"] == ("1 of 2 means is not determined: about 2 ps more production "
                            "should determine it, under a minute at this run's speed on CUDA.")


def test_a_floor_is_said_as_one(tmp_path):
    lead = _lead(_a_run(tmp_path, _withheld(2.43, floor=True), _withheld(0.4)))
    assert lead["said"].startswith("Neither mean is determined: at least 2.5 ns more production "
                                   "should determine them")
    assert lead["said"].endswith("so run that much and estimate again.")


def test_what_the_records_do_not_give_is_not_guessed(tmp_path):
    lead = _lead(_a_run(tmp_path, _withheld(None), _recorded(1.2), cost=False))
    assert lead["said"] == ("1 of 2 means is not determined. How much longer would determine "
                            "it is not in the records: each says why below.")
    assert lead["more_ns"] is None


def test_every_mean_determined_is_said_so(tmp_path):
    assert _lead(_a_run(tmp_path, _recorded(0.15), _recorded(1.2)))["said"] == \
        "All 2 means are determined."


def test_a_reason_is_said_in_a_line():
    from fastmdxplora.gui.overview_view import _in_a_line

    assert _in_a_line(SHORT) == ("Short against its own correlation time: a longer run, "
                                 "or replicas.")
    assert _in_a_line("7.0 independent samples in 9 frames (one every 1). Below 10 a mean and "
                      "its error describe how this run went.") == \
        "Fewer than 10 independent samples: the run has to be longer."
    # Under one, as the reason says it, and a count with a thousands separator.
    for said in ("Fewer than one independent sample in 9 frames (one every 12). Below 10",
                 "fewer than one independent sample in 9 frames (one every 12). Below 10",
                 "1,234.5 independent samples in 98765 frames (one every 80). Below 2000"):
        assert _in_a_line(said + " a mean and its error describe how this run went.") == \
            f"Fewer than {said.split('Below ')[1]} independent samples: the run has to be longer."
    assert _in_a_line("The reweighting recovered no value.") == \
        "The reweighting recovered no value."


LOOK = """() => {
    const box = (id) => document.getElementById(id).getBoundingClientRect();
    const tile = document.querySelector('.overview-tile[data-determined="false"]');
    const badge = tile.querySelector('.tile-badge');
    const muted = getComputedStyle(document.documentElement).getPropertyValue('--text-muted');
    const probe = document.createElement('span');
    probe.style.color = muted;
    document.body.appendChild(probe);
    const mutedColour = getComputedStyle(probe).color;
    probe.remove();
    return {
        lead: document.getElementById('overview-lead').hidden ? ''
            : document.getElementById('overview-lead').textContent,
        reason: (tile.querySelector('.tile-reason') || {}).textContent || '',
        badge: [badge.textContent, getComputedStyle(badge).color === mutedColour],
        results: [Math.round(box('overview-results').left), Math.round(box('overview-results').right),
                  Math.round(box('overview-results').top)],
        run: [Math.round(box('overview-run').left), Math.round(box('overview-run').top)],
    };
}"""


def test_the_page_leads_with_it_and_sets_the_run_beside_the_means(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    root = _a_run(tmp_path, _withheld(2.43), _recorded(1.2))
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.set_default_timeout(60000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url + "#overview", wait_until="domcontentloaded")
            page.wait_for_selector("#overview-lead:not([hidden])")
            page.wait_for_selector('.overview-body[data-lead="results"]')
            page.evaluate("() => window.FastMDXFrame.setCollapsed(true)")
            page.wait_for_timeout(400)
            wide = page.evaluate(LOOK)
            page.evaluate("() => window.FastMDXFrame.setCollapsed(false)")
            page.wait_for_timeout(400)
            beside_the_panel = page.evaluate(LOOK)
            page.evaluate("() => window.FastMDXFrame.setCollapsed(true)")
            page.set_viewport_size({"width": 1200, "height": 900})
            page.wait_for_timeout(400)
            narrow = page.evaluate(LOOK)
            browser.close()
    finally:
        session.server.shutdown()
    assert errors == []
    assert wide["lead"].startswith("1 of 2 means is not determined: about 2.5 ns more")
    assert wide["reason"] == "Short against its own correlation time: a longer run, or replicas."
    assert wide["badge"] == ["Not determined", True]
    # Side by side on a wide window: the run to the right of the means, at
    # their height.
    assert wide["run"][0] >= wide["results"][1] and abs(wide["run"][1] - wide["results"][2]) < 40
    # One under the other on a narrower window, or beside the side panel.
    for stacked in (narrow, beside_the_panel):
        assert stacked["run"][1] > stacked["results"][2] + 100, stacked
        assert stacked["run"][0] < stacked["results"][1], stacked


# ---------------------------------------------------------------------------
# The lead is read held to the run (`/api/overview`, `server._confined`): a
# record linked out of it is not read, and said so.

PUBLIC = pytest.mark.parametrize("public", [False, True], ids=["loopback", "public"])


def _linked_out(record: Path, outside: Path) -> None:
    from tests.test_the_study_pages_read_only_the_study import _link

    outside.parent.mkdir(parents=True, exist_ok=True)
    record.rename(outside)
    _link(record, outside)


def _overview(root: Path, workspace: Path, outside: Path, *, public: bool) -> dict:
    from tests.test_the_study_pages_read_only_the_study import _ask, _OPENED, _watch, _WATCHED

    _watch(outside)
    try:
        status, body = _ask(root, workspace, "/api/overview", public=public)
        opened = sorted(set(_OPENED))
    finally:
        _WATCHED.clear()
    assert status == 200 and opened == []
    return json.loads(body)


@PUBLIC
def test_a_cost_linked_out_is_not_read_for_the_time_and_said_so(tmp_path, public):
    root = _a_run(tmp_path, _withheld(2.43), _recorded(1.2))
    outside = tmp_path / "elsewhere"
    _linked_out(root / "simulation" / "cost.json", outside / "cost.json")
    lead = _overview(root, tmp_path, outside, public=public)["lead"]
    assert "at this run's speed" not in lead["said"] and lead["seconds"] is None
    assert lead["said"].startswith("1 of 2 means is not determined: about 2.5 ns more")
    assert "simulation/cost.json was not read" in lead["said"]


@PUBLIC
def test_a_mean_s_record_linked_out_is_counted_apart(tmp_path, public):
    root = _a_run(tmp_path, _withheld(2.43), _withheld(0.4))
    outside = tmp_path / "elsewhere"
    _linked_out(root / "analysis" / "rmsd" / "options.json", outside / "options.json")
    lead = _overview(root, tmp_path, outside, public=public)["lead"]
    # The outside record's ask (2.43 ns) is not read: Rg's own is what is said.
    assert lead["said"].startswith("Its mean is not determined: about 400 ps more production")
    # Refused first by the tiles' read: still said, so the figure is not
    # taken for the run's whole need.
    assert ("analysis/rmsd/options.json was not read, so what it says is not counted here."
            in lead["said"])
    assert lead["said"].endswith("1 result was not read: it says why below.")
