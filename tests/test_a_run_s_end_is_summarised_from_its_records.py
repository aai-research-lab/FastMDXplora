"""When a run the Agent started ends, what it found, from its records; and the
message box's words follow the situation.

Decided 10-07: "A summary when a run ends", a suggestion only, written from
the study's records so it costs no tokens (`records_answer` already answers
what a study found, whether it ran long enough and what would strengthen
it). And (user, 10-07): "Describe a study, ask a question, or tell me what to
do." should be adaptive, depending on the situation.
"""

from __future__ import annotations

import json
import tempfile
import time
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

from fastmdxplora.gui import records_answer
from fastmdxplora.gui.agent_panel import run_summary_endpoint
from tests.test_the_agent_answers_from_the_records import SUPPORTED, _study


def _ended(root: Path, status: str = "completed") -> Path:
    _study(root)
    (root / "manifest.json").write_text(json.dumps({"phases": [
        {"name": "simulation", "started_at": "2026-10-07T10:00:00+00:00",
         "finished_at": "2026-10-07T12:07:00+00:00"}]}), encoding="utf-8")
    (root / "simulation").mkdir(exist_ok=True)
    (root / "simulation" / "live_status.json").write_text(json.dumps({"status": status}),
                                                          encoding="utf-8")
    return root


def test_an_ended_run_is_said_from_its_records(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(records_answer, "_supports", lambda base: SUPPORTED)
    root = _ended(tmp_path / "study")
    said = run_summary_endpoint({"study": str(root)}, SimpleNamespace())
    assert said["ok"] and said["ended"] and said["status"] == "completed"
    assert said["head"].startswith("The run ended: completed")
    assert "0.1123 ± 0.0021 nm" in said["found"]
    assert not said["found"].startswith("*From")
    assert said["long_enough"] and said["strengthen"]
    assert "AI model" not in json.dumps(said)


def test_a_failed_run_says_so(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(records_answer, "_supports", lambda base: SUPPORTED)
    root = _ended(tmp_path / "study", "failed")
    said = run_summary_endpoint({"study": str(root)}, SimpleNamespace())
    assert said["head"].startswith("The run failed")


def test_a_run_still_going_is_not_summarised(tmp_path) -> None:
    root = _ended(tmp_path / "study")
    going = SimpleNamespace(running_root=root, process=SimpleNamespace(poll=lambda: None))
    assert run_summary_endpoint({"study": str(root)}, going) == {"ok": True, "ended": False}


def test_a_run_its_record_says_is_going_is_not_summarised(tmp_path) -> None:
    """Found by the review (10-07): a run this server does not hold (after a
    restart, or on another machine) was summarised while its record said it
    was running."""
    root = _ended(tmp_path / "study", "running")
    assert run_summary_endpoint({"study": str(root)}, SimpleNamespace()) == {
        "ok": True, "ended": False}


def test_a_study_of_several_runs_is_said_once(tmp_path, monkeypatch) -> None:
    """Found by the review (10-07): its one paragraph was said under all
    three heads."""
    monkeypatch.setattr(records_answer, "_several_runs", lambda base: "Three runs.")
    root = _ended(tmp_path / "study")
    (root / "batch_manifest.json").write_text("{}", encoding="utf-8")
    said = run_summary_endpoint({"study": str(root)}, SimpleNamespace())
    assert said["found"] == "Three runs." and said["long_enough"] == said["strengthen"] == ""


def _sweep(root: Path, results: dict, live: dict | None = None,
           planned: tuple[str, ...] = ("r1", "r2")) -> Path:
    """A study of several runs as `explore` lays it out: its batch manifest
    and its runs, nothing of one study at its top. The manifest's `runs`
    is empty until the study ends (the explorer writes it at the start and
    at the end)."""
    (root / "runs").mkdir(parents=True)
    planned = [{"run_id": r} for r in planned]
    (root / "batch_manifest.json").write_text(json.dumps({
        "planned": planned,
        "runs": [{"run_id": r, "status": s} for r, s in results.items()]}), encoding="utf-8")
    for run, status in (live or {}).items():
        (root / "runs" / run / "simulation").mkdir(parents=True)
        (root / "runs" / run / "simulation" / "live_status.json").write_text(
            json.dumps(status), encoding="utf-8")
    return root


def test_a_study_of_several_runs_is_said_when_every_run_has_ended(tmp_path, monkeypatch):
    """Found by the second review (10-07): a study of several runs, as
    `explore` writes it, was answered "No study there." and never
    summarised; its end is that of its runs."""
    monkeypatch.setattr(records_answer, "_several_runs", lambda base: "Two runs.")
    root = _sweep(tmp_path / "sweep", {"r1": "ok", "r2": "failed"})
    said = run_summary_endpoint({"study": str(root)}, SimpleNamespace())
    assert said["ok"] and said["ended"]
    assert said["head"] == "The runs ended: 1 completed, 1 failed"
    assert said["found"] == "Two runs." and said["long_enough"] == said["strengthen"] == ""
    both = _sweep(tmp_path / "both", {"r1": "ok", "r2": "ok"})
    assert run_summary_endpoint({"study": str(both)}, SimpleNamespace())["status"] == "completed"


def _going(monkeypatch, going: bool) -> None:
    """Whether a process is known to run the folder (this server's, or one
    in the workspace's list of runs started there)."""
    from fastmdxplora.gui import agent_panel

    monkeypatch.setattr(agent_panel, "_going_here", lambda root, runtime: going)


def test_a_study_of_several_runs_with_its_process_going_is_not_summarised(
        tmp_path, monkeypatch) -> None:
    """And by the third and fourth reviews (10-07): after a restart, with
    its process still going, a sweep whose runs had all written
    `completed` was summarised before its comparison was written."""
    _going(monkeypatch, True)
    root = _sweep(tmp_path / "sweep", {}, {
        "r1": {"stage": "completed", "current_step": 50, "total_planned_steps": 50},
        "r2": {"stage": "completed", "current_step": 50, "total_planned_steps": 50}})
    assert run_summary_endpoint({"study": str(root)}, SimpleNamespace()) == {
        "ok": True, "ended": False}


def test_a_stopped_study_of_several_runs_is_said(tmp_path, monkeypatch) -> None:
    """Found by the third and fourth reviews (10-07): a sweep stopped, or
    ended by a failure with `continue_on_error` off, has runs the explorer
    marks `skipped`; it read as still going, for ever."""
    monkeypatch.setattr(records_answer, "_several_runs", lambda base: "Three runs.")
    _going(monkeypatch, False)
    root = _sweep(tmp_path / "sweep", {"r1": "ok", "r2": "error", "r3": "skipped"},
                  planned=("r1", "r2", "r3"))
    said = run_summary_endpoint({"study": str(root)}, SimpleNamespace())
    assert said["ended"] and said["status"] == "failed"
    assert said["head"] == "The runs ended: 1 completed, 1 failed, 1 did not finish"
    # Stopped by this server's Stop (fifth and sixth reviews, 10-07: told
    # by the runtime, not guessed from a return code).
    asked = SimpleNamespace(stopped_roots={root.resolve()})
    assert run_summary_endpoint({"study": str(root)}, asked)["status"] == "stopped"


def test_a_study_of_several_runs_whose_process_was_killed_is_said(tmp_path, monkeypatch):
    """Killed before it wrote its manifest's end: the plan, and a run left
    in production."""
    monkeypatch.setattr(records_answer, "_several_runs", lambda base: "Two runs.")
    root = _sweep(tmp_path / "sweep", {}, {
        "r1": {"stage": "completed", "current_step": 50, "total_planned_steps": 50},
        "r2": {"stage": "Production", "current_step": 10, "total_planned_steps": 50}})
    killed = SimpleNamespace(running_root=root, process=SimpleNamespace(poll=lambda: -9))
    said = run_summary_endpoint({"study": str(root)}, killed)
    assert said["ended"] and said["status"] == "interrupted"
    assert said["head"] == "The runs ended: 1 completed, 1 did not finish"


def test_a_study_just_begun_is_not_said_gone(tmp_path, monkeypatch) -> None:
    """Found by the second review (10-07): a folder holding only its
    config, in the first seconds of its run, was "No study there.", and
    the page then never asked again."""
    root = tmp_path / "begun"
    root.mkdir()
    (root / "exploration.yml").write_text("systems: []\n", encoding="utf-8")
    going = SimpleNamespace(running_root=root, process=SimpleNamespace(poll=lambda: None))
    assert run_summary_endpoint({"study": str(root)}, going) == {"ok": True, "ended": False}
    _going(monkeypatch, True)
    assert run_summary_endpoint({"study": str(root)}, SimpleNamespace()) == {
        "ok": True, "ended": False}


def test_a_run_that_wrote_nothing_before_it_ended_is_said(tmp_path, monkeypatch) -> None:
    """And by the third and fourth reviews (10-07): one whose process this
    server had forgotten (another folder opened, or a restart) was asked
    about for ever; one stopped in its first seconds was said as failed."""
    root = tmp_path / "begun"
    root.mkdir()
    (root / "exploration.yml").write_text("systems: []\n", encoding="utf-8")
    ended = SimpleNamespace(running_root=root, process=SimpleNamespace(poll=lambda: 1))
    said = run_summary_endpoint({"study": str(root)}, ended)
    assert said["ended"] and said["status"] == "failed"
    assert said["head"] == "The run failed" and said["found"]
    stopped = SimpleNamespace(running_root=root, process=SimpleNamespace(poll=lambda: 1),
                              stopped_roots={root.resolve()})
    said = run_summary_endpoint({"study": str(root)}, stopped)
    assert said["status"] == "stopped" and said["head"] == "The run was stopped"
    killed = SimpleNamespace(running_root=root, process=SimpleNamespace(poll=lambda: -9))
    assert run_summary_endpoint({"study": str(root)}, killed)["head"] == (
        "The run was interrupted")
    _going(monkeypatch, False)
    said = run_summary_endpoint({"study": str(root)}, SimpleNamespace())
    assert said["ended"] and said["head"] == "The run was interrupted"
    assert said["found"] == "It wrote no records to read."


def test_a_process_in_the_workspace_s_list_is_known_to_run_its_folder(tmp_path) -> None:
    """`_going_here` reads the list every Run writes (`runs_here`): a live
    process recorded for the folder runs it; once it has exited, nothing
    does."""
    import subprocess
    import sys

    from fastmdxplora.gui.agent_panel import _going_here
    from fastmdxplora.runs_here import record_start

    workspace = tmp_path / "workspace"
    folder = workspace / "begun"
    folder.mkdir(parents=True)
    argv = [sys.executable, "-c", "import time; time.sleep(30)", str(folder)]
    child = subprocess.Popen(argv)
    try:
        record_start(workspace, folder, child.pid, argv, by="the GUI")
        runtime = SimpleNamespace(_rule_folders=lambda: [workspace])
        assert _going_here(folder, runtime)
        assert not _going_here(workspace / "other", runtime)
    finally:
        child.kill()
        child.wait()
    from fastmdxplora.gui import agent_panel

    agent_panel._GOING_ASKED.clear()  # kept a few seconds; asked again after
    assert not _going_here(folder, runtime)
    said = run_summary_endpoint({"study": str(folder)}, runtime)
    assert said["ended"] and said["found"] == "It wrote no records to read."


def test_a_study_prepared_once_for_every_window_is_completed(tmp_path, monkeypatch):
    """Found by the fifth review (10-07): asked only to prepare, an
    umbrella study marks every window skipped, prepared once; it was said
    as stopped with none finished."""
    from fastmdxplora.batch.explorer import PREPARED_ONCE

    monkeypatch.setattr(records_answer, "_several_runs", lambda base: "Prepared.")
    root = _sweep(tmp_path / "sweep", {"r1": "skipped", "r2": "skipped"})
    manifest = json.loads((root / "batch_manifest.json").read_text(encoding="utf-8"))
    for run in manifest["runs"]:
        run["message"] = PREPARED_ONCE + ", in setup, for every window."
    (root / "batch_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    said = run_summary_endpoint({"study": str(root)}, SimpleNamespace())
    # Not "2 completed" above records saying none was run (tenth review,
    # 10-08): prepared, as asked.
    assert said["status"] == "completed"
    assert said["head"] == "The study was prepared once, for its 2 windows"


def test_a_run_stopped_inside_a_study_of_several_did_not_finish(tmp_path, monkeypatch):
    """Found by the sixth review (10-07): a run stopped mid-way is recorded
    `error` in the manifest, and was counted failed."""
    monkeypatch.setattr(records_answer, "_several_runs", lambda base: "Two runs.")
    root = _sweep(tmp_path / "sweep", {"r1": "ok", "r2": "error"},
                  {"r2": {"status": "stopped", "stage": "Production"}})
    said = run_summary_endpoint({"study": str(root)}, SimpleNamespace())
    assert said["head"] == "The runs ended: 1 completed, 1 did not finish"


def test_a_record_left_saying_running_by_this_server_s_ended_process(tmp_path, monkeypatch):
    """Found by the sixth review (10-07): this server's process had exited,
    and a record it left saying "running" (no process file to ask) held
    the summary back for a day."""
    monkeypatch.setattr(records_answer, "_supports", lambda base: SUPPORTED)
    monkeypatch.setattr(records_answer, "_several_runs", lambda base: "Two runs.")
    single = _ended(tmp_path / "single", "running")
    ended = SimpleNamespace(running_root=single, process=SimpleNamespace(poll=lambda: -9))
    said = run_summary_endpoint({"study": str(single)}, ended)
    assert said["ended"] and said["head"].startswith("The run was interrupted")
    sweep = _sweep(tmp_path / "sweep", {}, {"r1": {"status": "running"}})
    ended = SimpleNamespace(running_root=sweep, process=SimpleNamespace(poll=lambda: -9))
    assert run_summary_endpoint({"study": str(sweep)}, ended)["ended"]


def test_a_run_inside_a_study_of_several_going_elsewhere_is_waited_for(tmp_path):
    """No process here, and a run's own record says it is going (the study
    run on another machine): not ended."""
    root = _sweep(tmp_path / "sweep", {}, {"r1": {"status": "running"}})
    assert run_summary_endpoint({"study": str(root)}, SimpleNamespace()) == {
        "ok": True, "ended": False}


def test_a_run_s_own_record_names_its_live_process(tmp_path) -> None:
    """With no list to read (a runtime that keeps none), the run's own
    record at its top names a live process: going."""
    import subprocess
    import sys

    from fastmdxplora.gui.agent_panel import _going_here
    from fastmdxplora.orchestrator import RUN_PROCESS_FILE

    folder = tmp_path / "sweep"
    folder.mkdir()
    argv = [sys.executable, "-c", "import time; time.sleep(30)", str(folder)]
    child = subprocess.Popen(argv)
    try:
        (folder / RUN_PROCESS_FILE).write_text(json.dumps(
            {"pid": child.pid, "argv": argv[1:]}), encoding="utf-8")
        assert _going_here(folder, SimpleNamespace())
    finally:
        child.kill()
        child.wait()


def test_whether_a_folder_is_run_is_asked_once_in_a_while(tmp_path, monkeypatch) -> None:
    """Asked at every poll of the page while a run awaits its summary;
    reading a process's command line starts PowerShell on Windows and `ps`
    on macOS (fifth review, 10-07). Asked again after a few seconds."""
    from fastmdxplora import runs_here
    from fastmdxplora.gui import agent_panel
    from fastmdxplora.orchestrator import RUN_PROCESS_FILE

    asked = []
    monkeypatch.setattr(runs_here, "_going", lambda *a: asked.append(a) or True)
    folder = tmp_path / "begun"
    folder.mkdir()
    (folder / RUN_PROCESS_FILE).write_text(json.dumps({"pid": 4242}), encoding="utf-8")
    now = [1000.0]
    monkeypatch.setattr(agent_panel.time, "monotonic", lambda: now[0])
    assert agent_panel._going_here(folder, SimpleNamespace())
    assert agent_panel._going_here(folder, SimpleNamespace())
    assert len(asked) == 1
    now[0] += agent_panel.GOING_ASKED_FOR_S + 1
    assert agent_panel._going_here(folder, SimpleNamespace())
    assert len(asked) == 2


def test_no_study_no_summary(tmp_path) -> None:
    assert not run_summary_endpoint({"study": str(tmp_path / "nothing")}, None)["ok"]
    assert not run_summary_endpoint({}, None)["ok"]

    def outside(path):
        raise ValueError("outside the workspace")

    root = _ended(tmp_path / "study")
    assert not run_summary_endpoint({"study": str(root)}, None, path_for=outside)["ok"]


# ---- In a browser ----------------------------------------------------------

pytest.importorskip("playwright.sync_api")


def _page(pw, url):
    browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.set_default_timeout(30000)
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(url + "#agent", wait_until="domcontentloaded")
    return browser, page, errors


def _kept(url, entries) -> None:
    request = urllib.request.Request(
        url + "/api/agent/conversation", data=json.dumps({"entries": entries}).encode(),
        headers={"Content-Type": "application/json", "Origin": url}, method="POST")
    urllib.request.urlopen(request, timeout=10).read()


def test_a_run_that_ended_while_the_page_was_closed_is_summarised_once(tmp_path, monkeypatch):
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", tempfile.mkdtemp())
    monkeypatch.setattr(records_answer, "_supports", lambda base: SUPPORTED)
    ran = _ended(tmp_path / "workspace" / "ran")
    session = start_dashboard_session(output=str(tmp_path / "workspace" / "other"),
                                      host="127.0.0.1", port=0)
    _kept(session.url, [{"role": "user", "text": "run it"},
                        {"role": "agent", "kind": "action", "action": "run", "where": "",
                         "version": 1, "started": "2026-10-07T10:00:00Z", "output": str(ran)}])
    try:
        with sync_playwright() as pw:
            browser, page, errors = _page(pw, session.url)
            page.wait_for_selector("#agent-thread .agent-summary")
            head = page.text_content("#agent-thread .agent-summary-head")
            rows = page.locator("#agent-thread .agent-summary-rows dt").all_text_contents()
            found = page.text_content("#agent-thread .agent-summary-rows dd")
            page.reload(wait_until="domcontentloaded")
            page.wait_for_selector("#agent-thread .agent-summary")
            time.sleep(1.5)
            count = page.locator("#agent-thread .agent-summary").count()
            browser.close()
    finally:
        session.server.shutdown()
    assert head.startswith("The run ended: completed in 2h 7m")
    assert "from the study’s records" in head and "AI model" not in head
    assert rows == ["What it found", "Long enough?", "To strengthen it"]
    assert "0.1123 ± 0.0021 nm" in found
    assert count == 1
    assert errors == []


def test_the_box_s_words_follow_the_situation(tmp_path, monkeypatch):
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", tempfile.mkdtemp())
    session = start_dashboard_session(output=str(tmp_path / "workspace" / "new"),
                                      host="127.0.0.1", port=0)
    replies = [
        {"ok": True, "cycles": 1, "yaml": "x: 1\n",
         "config": {"systems": [{"system": "1UAO"}]}, "plan": [], "attempts": []},
        {"ok": False, "question": "Which one?\n\nCandidates: 2VB1; 2LZM.",
         "asked": "Which one?", "choices": ["2VB1", "2LZM"]},
        {"ok": False, "action": "run", "where": "", "confirm": True},
    ]
    try:
        with sync_playwright() as pw:
            browser, page, errors = _page(pw, session.url)
            page.route("**/api/agent/propose*", lambda route: route.fulfill(
                status=200, content_type="application/json", body=json.dumps(replies.pop(0))))
            page.wait_for_selector("#agent-request", state="visible")
            seen = [page.get_attribute("#agent-request", "placeholder")]
            for typed, waited in (("chignolin", ".agent-study:not([hidden])"),
                                  ("which lysozyme?", ".agent-choice"),
                                  ("run it", ".agent-confirm")):
                page.fill("#agent-request", typed)
                page.keyboard.press("Enter")
                page.wait_for_selector("#agent-thread " + waited)
                page.wait_for_timeout(200)
                seen.append(page.get_attribute("#agent-request", "placeholder"))
            browser.close()
    finally:
        session.server.shutdown()
    assert seen == ["Describe a study, or ask about one in this folder",
                    "Change something, or say run it",
                    "Pick one above, or type your answer",
                    "Answer above, or type yes or no"]
    assert errors == []


def test_a_run_that_ended_before_it_was_seen_running_is_summarised(tmp_path, monkeypatch):
    """Found by the review (10-07): the summary was asked only on a change
    seen from running, so a run that ended between two polls had none. And
    a study whose record says nothing of how it ended is not said as one
    that could not finish. And by the second review (10-07): such a run
    stayed "Running" with Stop, and the next "run it" was told a study was
    running."""
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", tempfile.mkdtemp())
    monkeypatch.setattr(records_answer, "_supports", lambda base: SUPPORTED)
    ran = _ended(tmp_path / "workspace" / "ran")
    (ran / "simulation" / "live_status.json").unlink()
    session = start_dashboard_session(output=str(tmp_path / "workspace" / "here"),
                                      host="127.0.0.1", port=0)
    replies = [{"ok": True, "cycles": 1, "yaml": "x: 1\n",
                "config": {"systems": [{"system": "1UAO"}]}, "plan": [], "attempts": []},
               {"ok": False, "action": "run", "where": "", "confirm": False}]
    try:
        with sync_playwright() as pw:
            browser, page, errors = _page(pw, session.url)
            page.route("**/api/agent/propose*", lambda route: route.fulfill(
                status=200, content_type="application/json", body=json.dumps(replies.pop(0))))
            page.route("**/api/agent/run", lambda route: route.fulfill(
                status=200, content_type="application/json",
                body=json.dumps({"ok": True, "output": str(ran)})))
            page.route("**/api/agent/conversation/attach", lambda route: route.fulfill(
                status=200, content_type="application/json", body=json.dumps({"ok": False})))
            page.wait_for_selector("#agent-request", state="visible")
            for typed, waited in (("chignolin", ".agent-study:not([hidden])"),
                                  ("run it", ".agent-running")):
                page.fill("#agent-request", typed)
                page.keyboard.press("Enter")
                page.wait_for_selector("#agent-thread " + waited)
            page.evaluate("() => document.getElementById('refresh-now').click()")
            page.wait_for_selector("#agent-thread .agent-summary")
            state = page.get_attribute("#agent-thread .agent-msg-agent >> nth=-1", "data-state")
            head = page.text_content("#agent-thread .agent-summary-head")
            line = page.text_content("#agent-thread .agent-running-said")
            stop = page.locator("#agent-thread .agent-running-stop").count()
            button = page.eval_on_selector(
                "#agent-thread .agent-study:not([hidden]) [data-role=run]",
                "b => [b.textContent, b.disabled]")
            browser.close()
    finally:
        session.server.shutdown()
    assert head.startswith("The run ended after 2h 7m")
    assert state == "done"
    assert line.startswith("Ran version 1") and stop == 0
    assert button == ["Run again", False]
    assert errors == []


def test_a_late_summary_of_one_run_does_not_end_the_next(tmp_path, monkeypatch):
    """Found by the third and fourth reviews (10-07): with run A's summary
    still being read, version 2 was run; A's answer then said B's line had
    run, took its Stop and freed its button while B ran. And by the fifth
    and sixth (10-07): A's own line then kept "Running" and a Stop that
    would have stopped B."""
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", tempfile.mkdtemp())
    session = start_dashboard_session(output=str(tmp_path / "workspace" / "here"),
                                      host="127.0.0.1", port=0)

    def config(ns):
        return {"ok": True, "cycles": 1, "yaml": "systems:\n- system: 1UAO\n",
                "config": {"systems": [{"system": "1UAO"}], "simulation": {"duration_ns": ns}},
                "plan": [], "attempts": []}

    replies, outputs, held = [config(2), config(4)], ["/A", "/B"], []

    def summary(route):
        if json.loads(route.request.post_data)["study"] == "/A":
            held.append(route)  # a slow read of A's records
        else:
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"ok": True, "ended": False}))

    try:
        with sync_playwright() as pw:
            browser, page, errors = _page(pw, session.url)
            page.route("**/api/agent/propose*", lambda route: route.fulfill(
                status=200, content_type="application/json", body=json.dumps(replies.pop(0))))
            page.route("**/api/agent/run", lambda route: route.fulfill(
                status=200, content_type="application/json",
                body=json.dumps({"ok": True, "output": outputs.pop(0)})))
            page.route("**/api/agent/run-summary", summary)
            page.wait_for_selector("#agent-request", state="visible")
            page.fill("#agent-request", "chignolin")
            page.keyboard.press("Enter")
            page.click("#agent-thread .agent-study:not([hidden]) [data-role=run]")
            page.wait_for_selector("#agent-thread .agent-running")
            page.evaluate("() => document.getElementById('refresh-now').click()")
            for _ in range(100):
                if held:
                    break
                page.wait_for_timeout(100)
            page.fill("#agent-request", "make it 4 ns")
            page.keyboard.press("Enter")
            page.wait_for_function(
                "() => document.querySelectorAll('#agent-thread .agent-study').length >= 2")
            page.click("#agent-thread .agent-study:not([hidden]) [data-role=run] >> nth=-1")
            page.wait_for_function(
                "() => document.querySelectorAll('#agent-thread .agent-running').length >= 2")
            held[0].fulfill(status=200, content_type="application/json", body=json.dumps(
                {"ok": True, "ended": True, "status": "completed",
                 "head": "The run ended: completed", "found": "x"}))
            page.wait_for_timeout(800)
            line = page.locator("#agent-thread .agent-running-said").last.text_content()
            first = page.locator("#agent-thread .agent-running-said").first.text_content()
            stop = page.locator("#agent-thread .agent-running").last.locator(
                ".agent-running-stop").count()
            stops = page.locator("#agent-thread .agent-running-stop").count()
            button = page.eval_on_selector_all(
                "#agent-thread [data-role=run]", "bs => [bs.at(-1).textContent, "
                "bs.at(-1).disabled]")
            browser.close()
    finally:
        session.server.shutdown()
    assert line.startswith("Running version 2") and stop == 1
    assert first.startswith("Ran version 1") and stops == 1
    assert button == ["Running", True]
    assert errors == []


def test_stop_marks_the_folder_it_stopped(tmp_path, monkeypatch) -> None:
    """How a run ended is said as stopped when Stop here ended it, not
    guessed from its return code (fifth and sixth reviews, 10-07)."""
    import subprocess
    import sys

    from fastmdxplora.gui.exploration import DashboardRuntime
    from fastmdxplora.simulation import runner

    monkeypatch.setattr(runner, "stop_grace_seconds", lambda: 1)
    root = tmp_path / "study"
    root.mkdir()
    runtime = DashboardRuntime(workspace_root=tmp_path, exploration_root=tmp_path)
    runtime.running_root = runtime.active_root = root
    runtime.process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    assert runtime.stop()["stopped"]
    assert root.resolve() in runtime.stopped_roots
    said = run_summary_endpoint({"study": str(root)}, runtime)
    assert said["ended"] and said["status"] == "stopped"


def test_a_run_started_again_where_one_was_stopped_is_not_said_stopped(tmp_path):
    """Stop's mark goes when a run starts again in that folder (seventh
    review, 10-08: untested)."""
    import sys

    from fastmdxplora.gui.exploration import DashboardRuntime

    root = tmp_path / "study"
    root.mkdir()
    runtime = DashboardRuntime(workspace_root=tmp_path, exploration_root=tmp_path)
    runtime.stopped_roots.add(root.resolve())
    runtime._spawn_now([sys.executable, "-c", "pass"], root, None)
    runtime.process.wait()
    assert root.resolve() not in runtime.stopped_roots


def test_a_run_carried_on_after_this_server_s_ended_is_going(tmp_path, monkeypatch):
    """Found by the eighth review (10-08): after a Stop, `fastmdx resume`
    carried the study on, and its summary was said from records the resume
    was writing."""
    _going(monkeypatch, True)
    root = _ended(tmp_path / "study")
    ended = SimpleNamespace(running_root=root, process=SimpleNamespace(poll=lambda: -15),
                            stopped_roots={root.resolve()})
    assert run_summary_endpoint({"study": str(root)}, ended) == {"ok": True, "ended": False}


def test_how_a_study_of_several_runs_ended_follows_its_runs(tmp_path, monkeypatch):
    """Found by the seventh review (10-08): Stop pressed after every run had
    completed (its comparison being written) said "stopped" beside "3
    completed"; one halted by a failure said "interrupted"."""
    monkeypatch.setattr(records_answer, "_several_runs", lambda base: "Runs.")
    done = _sweep(tmp_path / "done", {"r1": "ok", "r2": "ok"})
    asked = SimpleNamespace(stopped_roots={done.resolve()})
    assert run_summary_endpoint({"study": str(done)}, asked)["status"] == "completed"
    halted = _sweep(tmp_path / "halted", {"r1": "error", "r2": "skipped"})
    said = run_summary_endpoint({"study": str(halted)}, SimpleNamespace())
    assert said["status"] == "failed" and said["head"] == (
        "The runs ended: 1 failed, 1 did not finish")
