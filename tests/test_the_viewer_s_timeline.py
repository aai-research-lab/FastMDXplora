"""The Viewer's playback as a timeline, always under the molecule.

It was folded under the canvas, a slider and a time, with one series
beside it when opened. It is now always there while there are frames to
play: the transport, a ruler of the frames on production's clock with what
happened on them marked (each cluster's medoid, each judgement of a study run
until it is determined), the averaging's start shaded, and the study's series
as tracks moving with the frame; a click on the ruler, a mark or a track
shows that frame. The tool beside the molecule is a panel of its own, as
tall as the rail, not a fold forced open.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest


def _medoids(root: Path, rows: list[tuple[int, int, float]]) -> None:
    folder = root / "analysis" / "cluster"
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / "cluster_kmeans_populations.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["cluster", "frames", "fraction", "medoid_frame", "medoid_time_ns"])
        for cluster, frame, share in rows:
            writer.writerow([cluster, 3, share, frame, 0.002 * frame])


def test_the_marks_are_read_from_the_records(tmp_path):
    from fastmdxplora.gui.timeline_view import timeline_payload

    _medoids(tmp_path, [(0, 2, 0.5), (1, 4, 0.5)])
    (tmp_path / "stopping.json").write_text(json.dumps({
        "targets": [{"analysis": "rmsd", "standard_error": 0.01}],
        "rounds": [{"production_ns": 0.004, "decision": "extend", "more_ns": 0.004},
                   {"production_ns": 0.008, "decision": "met"}],
        "outcome": "met"}), encoding="utf-8")
    marks = timeline_payload(tmp_path)["marks"]
    medoids = [m for m in marks if m["kind"] == "medoid"]
    checks = [m for m in marks if m["kind"] == "check"]
    assert [m["frame"] for m in medoids] == [2, 4]
    assert medoids[0]["label"] == "The medoid of kmeans cluster 0, 50% of the frames"
    assert [m["time_ns"] for m in checks] == [0.004, 0.008]
    # As every page and the report say a round (`stopping.round_decided`).
    assert checks[0]["label"] == "Judged after round 1: the run extended by 4 ps"
    assert checks[1]["label"] == "Judged after round 2: determined as asked, so stopped"
    assert timeline_payload(tmp_path / "nothing") == {"ok": True, "marks": []}


@pytest.mark.parametrize("rounds, outcome, said, labels", [
    ([{"production_ns": 0.5, "decision": "extend", "more_ns": 0.25},
      {"production_ns": 0.75, "decision": "stopped", "stopped_by": "error"}], "stopped",
     "Stopped: extending study failed at simulation: CUDA_ERROR_OUT_OF_MEMORY",
     ["Judged after round 1: the run extended by 250 ps",
      "Judged after round 2: stopped by an error while the run was extended"]),
    ([{"production_ns": 0.5, "decision": "stopped"}], "stopped",
     "Stopped when asked, while extending study. Resuming the study judges the runs again "
     "and carries on from there.",
     ["Judged after round 1: stopped when asked (Ctrl+C or a signal) while the run was "
      "extended"]),
    ([{"production_ns": 0.5, "decision": "extend", "more_ns": 0.0995}], "running",
     "Extending by 99.5 ps, to 599.5 ps of the 5 ns allowed.",
     ["Judged after round 1: extending the run by 99.5 ps; the record does not say it "
      "finished"]),
], ids=["an error", "asked", "killed while extending"])
def test_a_round_is_marked_as_the_report_and_the_timeline_say_it(tmp_path, rounds, outcome,
                                                                  said, labels):
    """The Viewer worded rounds its own way: "extended by 0.0995 ns" of an
    extension the record does not say finished, and "stopped" with no
    cause, where the report and the Timeline said what stopped it."""
    from fastmdxplora.gui.timeline_view import timeline_payload
    from fastmdxplora.simulation.stopping import stopping_section

    (tmp_path / "stopping.json").write_text(json.dumps({
        "targets": [{"analysis": "rmsd", "standard_error": 0.01}], "runs": ["study"],
        "rounds": rounds, "outcome": outcome, "said": said}), encoding="utf-8")
    marks = [m["label"] for m in timeline_payload(tmp_path)["marks"] if m["kind"] == "check"]
    assert marks == labels
    table = "\n".join(stopping_section(tmp_path))
    for label in labels:
        assert f" | {label.split(': ', 1)[1]} |" in table


def _loaded(root: Path, frame_times_ps: list[float] | None = None, **load_kwargs) -> None:
    (root / "analysis").mkdir(parents=True, exist_ok=True)
    manifest = {"load_kwargs": load_kwargs}
    if frame_times_ps is not None:
        manifest["frame_times_ps"] = frame_times_ps
    (root / "analysis" / "analysis_manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8")


def test_a_medoid_is_marked_at_its_trajectory_frame_of_a_strided_analysis(tmp_path):
    """The populations table numbers a medoid among the frames analysed; the
    ruler numbers the trajectory's. An analysis that read every fifth frame
    from the tenth has its medoid 3 at trajectory frame (10 + 3) * 5."""
    from fastmdxplora.gui.figure_data import _clock
    from fastmdxplora.gui.timeline_view import timeline_payload

    _loaded(tmp_path, stride=5, first=10, saving_interval_ps=2.0)
    _medoids(tmp_path, [(0, 3, 0.5), (1, 7, 0.5)])
    medoids = timeline_payload(tmp_path)["marks"]
    assert [m["frame"] for m in medoids] == [65, 85] == _clock(tmp_path, [3, 7])[0]
    # Its time on production's clock, as the ruler's: frame 65 is the 66th, at 132 ps.
    assert [m["time_ns"] for m in medoids] == [0.132, 0.172]


def test_a_medoid_is_marked_by_its_recorded_time_where_frames_were_dropped(tmp_path):
    from fastmdxplora.gui.timeline_view import timeline_payload

    _loaded(tmp_path, saving_interval_ps=2.0, frame_times_ps=[2.0, 4.0, 10.0, 12.0])
    _medoids(tmp_path, [(0, 2, 1.0)])
    (mark,) = timeline_payload(tmp_path)["marks"]
    assert (mark["frame"], mark["time_ns"]) == (4, 0.01)


def test_a_one_frame_cluster_is_not_said_none_of_the_frames(tmp_path):
    from fastmdxplora.gui.timeline_view import timeline_payload

    _medoids(tmp_path, [(4, 9, 0.005)])
    (mark,) = timeline_payload(tmp_path)["marks"]
    assert mark["label"] == "The medoid of kmeans cluster 4, 0.5% of the frames"


def test_the_timeline_reads_only_the_study(tmp_path):
    """`/api/timeline` holds its reads to the study (`_confined`): a record
    linked out of it is said not read, not marked from."""
    from fastmdxplora.gui.server import _confined
    from fastmdxplora.gui.timeline_view import timeline_payload

    study = tmp_path / "study"
    study.mkdir()
    outside = tmp_path / "elsewhere.json"
    outside.write_text(json.dumps({"targets": [], "rounds": [
        {"production_ns": 0.004, "decision": "met"}]}), encoding="utf-8")
    (study / "stopping.json").symlink_to(outside)
    answer = _confined(study, timeline_payload)
    assert answer["marks"] == []
    assert answer["unavailable"] == ["stopping.json"]
    # And so the route answers.
    import urllib.request

    from fastmdxplora.gui.server import start_dashboard_session

    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    try:
        with urllib.request.urlopen(session.url.rstrip("/") + "/api/timeline", timeout=30) as reply:
            routed = json.loads(reply.read())
    finally:
        session.server.shutdown()
    assert routed["marks"] == [] and routed["unavailable"] == ["stopping.json"]


@pytest.fixture(scope="module")
def study(tmp_path_factory) -> Path:
    from tests.test_the_cartoon_is_dssp_of_each_frame import _helical_study

    root = _helical_study(tmp_path_factory.mktemp("timeline") / "study", analyse=False)
    from fastmdxplora.analysis import AnalysisOrchestrator

    AnalysisOrchestrator(str(root / "simulation" / "production.dcd"),
                         str(root / "simulation" / "trajectory_topology.pdb"),
                         output_dir=str(root / "analysis")).run(include=["rmsd", "rg"])
    _medoids(root, [(0, 3, 0.6), (1, 5, 0.4)])
    return root


def test_the_timeline_is_under_the_molecule_and_shows_a_frame_when_asked(study):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.set_default_timeout(60000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url + "#viewer", wait_until="domcontentloaded")
            if not page.evaluate("() => !!document.createElement('canvas').getContext('webgl')"):
                pytest.skip("this browser has no WebGL, so the viewer cannot render")
            # Shown without opening anything, and before any frame is shown.
            page.wait_for_selector("#timeline-ruler:not([hidden]) svg")
            page.wait_for_function(
                "() => document.querySelectorAll('#frame-series .frame-series-line').length === 2")
            first = page.evaluate("""() => ({
                fold: !!document.querySelector('details#viewer-under-fold, #viewer-timeline > summary'),
                tracks: [...document.querySelectorAll('.frame-track')].map((t) => t.dataset.analysis),
                marks: [...document.querySelectorAll('.timeline-mark')].map((m) =>
                    m.getAttribute('aria-label')),
            })""")
            # A medoid's mark shows its frame.
            page.locator('.timeline-mark[data-kind="medoid"]').nth(1).dispatch_event("pointerdown")
            page.wait_for_function("() => document.getElementById('timeline-ruler')"
                                   ".dataset.frame === '5'")
            on_mark = page.evaluate("() => [document.getElementById('traj-slider').value,"
                                    " [...document.querySelectorAll('.frame-track-value')]"
                                    ".map((v) => v.textContent)]")
            # The ruler's left end shows the first frame; a track's, too.
            page.locator("#timeline-ruler svg").click(position={"x": 49, "y": 14})
            page.wait_for_function("() => document.getElementById('traj-slider').value === '0'")
            track = page.locator(".frame-track").nth(1).locator("svg")
            width = track.evaluate("(svg) => svg.getBoundingClientRect().width")
            track.click(position={"x": width - 11, "y": 25})
            page.wait_for_function("() => Number(document.getElementById('traj-slider').value) >= 4")
            # A track is taken away and added back.
            page.click('.frame-track[data-analysis="rg"] .frame-track-drop')
            page.wait_for_function("() => document.querySelectorAll('.frame-track').length === 1")
            page.select_option("#frame-series-pick", "rg")
            page.wait_for_function(
                "() => document.querySelectorAll('#frame-series .frame-series-line').length === 2")
            # The frame's time in the ruler's unit, the badge too.
            times = page.evaluate("""() => [document.getElementById('traj-simtime').textContent,
                document.getElementById('overlay-simtime').textContent,
                [...document.querySelectorAll('#timeline-ruler text')].pop().textContent]""")
            # A 40 ps run of 0.2 ps frames, as both readouts say a frame's time.
            short = page.evaluate("""() => {
                const t = Array.from({length: 200}, (_, i) => (i + 1) * 0.0002);
                const said = window.FastMDXSeries.frameTimeSaid;
                return [said(t[0], t), said(t[1], t), said(1.5, [0.5, 1.0, 1.5])];
            }""")
            # A track that cannot be drawn says why under its name, not over it.
            boxes = page.evaluate("""() => {
                const track = document.querySelector('.frame-track');
                track.querySelector('.frame-track-chart').replaceChildren();
                const note = track.querySelector('.frame-track-note');
                note.textContent = 'RMSD was computed from another trajectory than the one played.';
                const a = track.querySelector('.frame-track-label').getBoundingClientRect();
                const words = document.createRange();
                words.selectNodeContents(note);
                const b = words.getBoundingClientRect();
                return [a.bottom, b.top];
            }""")
            # The tool beside the molecule is as tall as the rail.
            panel = page.evaluate("""() => {
                const chosen = document.querySelector('.viewer-side .side-section.is-chosen');
                const rail = document.getElementById('viewer-rail');
                const head = chosen.querySelector(':scope > summary');
                return [Math.round(chosen.getBoundingClientRect().height),
                        Math.round(rail.getBoundingClientRect().height), head.tabIndex];
            }""")
            browser.close()
    finally:
        session.server.shutdown()
    assert errors == []
    assert first["fold"] is False
    assert first["tracks"] == ["rmsd", "rg"]
    assert first["marks"] == ["The medoid of kmeans cluster 0, 60% of the frames",
                              "The medoid of kmeans cluster 1, 40% of the frames"]
    # The unit once, in the track's name, not again after its value.
    assert on_mark[0] == "5" and on_mark[1] and not any(v.endswith(" nm") for v in on_mark[1])
    assert panel[0] >= panel[1] - 2 and panel[2] == -1
    unit = times[2].split()[-1]
    if unit == "frame":
        # A run with no clock: its frames by number, and no time said.
        assert times[0] == "\u2014", times
    else:
        assert times[0].endswith(" " + unit) and times[1].endswith(" " + unit), times
    assert short == ["0.2 ps", "0.4 ps", "1.500 ns"]
    assert boxes[0] <= boxes[1], boxes


def test_the_overlay_says_a_time_under_1_ns_in_ps():
    """The Viewer's overlay said "LAST FRAME production step 800 0.002 ns"
    under a ruler in ps."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session
    from tests.test_the_frame_holds_the_study import _a_finished_study

    session = start_dashboard_session(output=_a_finished_study(), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.set_default_timeout(60000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url + "#viewer", wait_until="domcontentloaded")
            page.wait_for_selector("body.state-ready")
            said = page.evaluate("""() => [0.002, 0.0006, 2.5].map((ns) => {
                window.dispatchEvent(new CustomEvent('dashboard:status-updated', {detail: {
                    status: {status: 'completed', stage: 'production'},
                    times: {production_ns: ns}}}));
                return document.getElementById('overlay-simtime').textContent;
            })""")
            browser.close()
    finally:
        session.server.shutdown()
    assert errors == []
    assert said == ["production 2 ps", "production 0.6 ps", "production 2.500 ns"], said
