"""Umbrella windows the person names run again from the GUI, at their values.

1142 gave the command line `--rerun-force-constant`; running a window again
held harder, or for longer, was still typed. Told "rerun window 2 at 6000",
the Agent replies `DO: rerun windows 2 at 6000`. The windows and the numbers
are read from that one line by a strict pattern, checked against the study,
and the command is built from the study's own record, never from the text:
the person sees what will run, with the spring in its unit and the price at
the study's own speed, and says yes. A window the study does not have, or a
spring that is not a positive number, is said, and nothing runs.
"""

from __future__ import annotations

import json
import sys
import tempfile
from unittest import mock

import pytest

from fastmdxplora.agent.propose import _windows_in
from fastmdxplora.batch import explorer as explorer_module
from fastmdxplora.batch.explorer import BatchExplorer
from fastmdxplora.gui.exploration import DashboardRuntime
from fastmdxplora.refusals import StudyError
from fastmdxplora.remedies import windows_again
from tests.test_a_window_runs_again_in_place import _config, _stand_in, _window


@pytest.fixture
def ran() -> list[int]:
    return []


@pytest.fixture
def study(tmp_path, monkeypatch, ran):
    monkeypatch.setattr(explorer_module, "_execute_run", _stand_in(ran))
    monkeypatch.setattr(BatchExplorer, "_maybe_build_comparison", lambda self: None)
    out = tmp_path / "out"
    BatchExplorer(config=_config(tmp_path, [2000] * 5), output_dir=str(out)).run()
    ran.clear()
    return out


def _runtime(root) -> DashboardRuntime:
    runtime = DashboardRuntime(workspace_root=root.parent, exploration_root=root.parent)
    runtime.active_root = root
    return runtime


class TestTheLine:
    @pytest.mark.parametrize("line,read", [
        ("DO: rerun windows 3 at 6000", ([3], 6000.0, None)),
        ("DO: rerun window 3", ([3], None, None)),
        ("DO: rerun windows 2, 5 and 7 for 4 ns at 2000 kJ/mol/nm^2", ([2, 5, 7], 2000.0, 4.0)),
        ("DO: Rerun windows 5 2 for 4 ns.", ([2, 5], None, 4.0)),
        ("DO: rerun windows 1 at 1e4 kJ/mol/rad²", ([1], 10000.0, None)),
    ])
    def test_what_is_read(self, line, read):
        found = _windows_in(line)
        assert (found["windows"], found["force_constant"], found["duration_ns"]) == read

    @pytest.mark.parametrize("line", [
        "DO: rerun windows 3 at 6000 now",
        "DO: rerun windows 3 at 6000\nand then stop",
        "DO: rerun windows at 6000",
        "DO: rerun windows 3 at 6000; rm -rf ~",
        "rerun windows 3 at 6000",
    ])
    def test_nothing_else_is(self, line):
        assert _windows_in(line) is None

    def test_the_loop_returns_it_as_an_action(self):
        from fastmdxplora.agent.propose import propose_config

        proposal = propose_config("rerun window 2 at 6000",
                                  lambda prompt: "DO: rerun windows 2 at 6000")
        assert proposal.action == "rerun windows"
        assert proposal.arguments == {"windows": [2], "force_constant": 6000.0,
                                      "duration_ns": None}


class TestWhatRuns:
    def test_the_windows_the_values_and_the_price(self, study):
        remedy = windows_again(study, [3, 1], force_constant="6000", duration_ns=2)
        assert remedy.fix == ("Run windows 1 and 3 again, held at 6000 kJ/mol/nm^2 with "
                              "2 ns of production each, keeping every other window, and "
                              "recombine the free energy.")
        assert remedy.argv[-5:] == ("--rerun-window", "1", "3", "--rerun-force-constant",
                                    "6000")
        assert remedy.argv[:3] == ("explore", "-c", str(study.resolve() / "resolved_config.yml"))
        assert "--simulate-duration-ns" in remedy.argv
        assert remedy.price.production_ns == 4.0 and remedy.price.runs == 2
        assert remedy.covers == ("window-01", "window-03")

    def test_as_the_windows_planned_when_no_length_is_named(self, study):
        remedy = windows_again(study, [0], force_constant=3000)
        assert "--simulate-duration-ns" not in remedy.argv
        assert remedy.fix.startswith("Run window 0 again, held at 3000 kJ/mol/nm^2, keeping")

    @pytest.mark.parametrize("windows,k,said", [
        ([9], None, "no window 9"),
        ([1], 0, "positive number"),
        ([1], "stiff", "positive number"),
        ([], None, "Name the windows"),
        (["a"], None, "by their numbers"),
    ])
    def test_what_is_refused(self, study, windows, k, said):
        with pytest.raises(StudyError, match=said):
            windows_again(study, windows, force_constant=k)

    @pytest.mark.parametrize("simulation,planned", [
        ({}, (2.0, 1.5)),
        ({"duration_ns": 5, "timestep_fs": 4}, (5.0, 1.5)),
        ({"production_steps": 1000, "nvt_duration_ns": 0.1, "npt_steps": 0}, (0.002, 0.1)),
    ])
    def test_a_windows_length_is_the_runners(self, tmp_path, simulation, planned):
        """Priced as the runner runs it: a run naming no length runs the
        default 2 ns (it was priced as none), and equilibration is 1.5 ns
        at any timestep."""
        import yaml

        from fastmdxplora.remedies import _planned

        (tmp_path / "resolved_config.yml").write_text(
            yaml.safe_dump({"simulation": simulation}), encoding="utf-8")
        assert tuple(round(v, 6) for v in _planned(tmp_path)) == planned

    def test_a_study_without_windows(self, tmp_path):
        with pytest.raises(StudyError, match="has none"):
            windows_again(tmp_path, [0])

    def test_the_command_does_it(self, study, ran):
        """The arguments run, and window 1 is held at the spring named."""
        from fastmdxplora.cli.main import main

        remedy = windows_again(study, [1], force_constant=6000)
        assert main(list(remedy.argv)) == 0
        assert ran == [1]
        record = json.loads((_window(study, 1) / "simulation" / "umbrella_window.json")
                            .read_text(encoding="utf-8"))
        assert record["force_constant"] == 6000


class TestTheUnitsOfASpring:
    @pytest.mark.parametrize("variable,unit", [
        ("ligand_distance", "kJ/mol/nm^2"), ("torsion", "kJ/mol/rad^2"),
        ("coordination", "kJ/mol"), ("something_new", "kJ/mol per unit of the variable squared"),
    ])
    def test_each(self, variable, unit):
        from fastmdxplora.simulation.umbrella import spring_unit

        assert spring_unit(variable) == unit

    def test_a_window_run_otherwise_is_said_in_its_units(self, tmp_path):
        from fastmdxplora.simulation.umbrella import UmbrellaPlan, Window, windows_off_the_plan

        where = tmp_path / "w0" / "simulation"
        where.mkdir(parents=True)
        (where / "umbrella_window.json").write_text(
            json.dumps({"centre": 1.0, "force_constant": 200}), encoding="utf-8")
        plan = UmbrellaPlan(windows=(Window(0, 1.0, 400), Window(1, 1.5, 400)),
                            collective_variable="torsion")
        ((_, said),) = windows_off_the_plan({0: tmp_path / "w0"}, plan)
        assert said == ("window 0 ran at 1 rad with 200 kJ/mol/rad^2, and the config "
                        "gives it 1 rad with 400")


class TestTheAgentAndTheRuntime:
    def test_the_agent_is_answered_with_what_would_run(self, study, monkeypatch):
        import fastmdxplora.agent as agent_mod
        from fastmdxplora.gui.agent_panel import propose_endpoint

        monkeypatch.setattr(agent_mod, "completion_for", lambda *a, **k: (
            lambda prompt: "DO: rerun windows 2 at 6000"))
        answer = propose_endpoint({"request": "rerun window 2 at 6000"}, _runtime(study))
        assert answer["action"] == "rerun windows" and answer["confirm"] is True
        assert answer["fix"]["request"] == {"windows": [2], "force_constant": 6000.0,
                                            "duration_ns": None}
        # The stand-in windows name no length, so they run the runner's default.
        assert answer["fix"]["price_said"].startswith(
            "2 ns of production and 1.5 ns of equilibration")

    def test_a_window_it_does_not_have_is_said(self, study, monkeypatch):
        import fastmdxplora.agent as agent_mod
        from fastmdxplora.gui.agent_panel import propose_endpoint

        monkeypatch.setattr(agent_mod, "completion_for", lambda *a, **k: (
            lambda prompt: "DO: rerun windows 12 at 6000"))
        answer = propose_endpoint({"request": "window 12 again"}, _runtime(study))
        assert answer["fix"] is None and "no window 12" in answer["refused"]

    def test_the_runtime_runs_the_study_record_command(self, study):
        runtime = _runtime(study)
        with mock.patch.object(DashboardRuntime, "_spawn",
                               return_value={"launched": True}) as spawn:
            answer = runtime.run_windows_again([2], force_constant="6000")
        assert answer["ok"]
        command, where, _ = spawn.call_args.args
        assert command[:3] == [sys.executable, "-m", "fastmdxplora"]
        assert command[-4:] == ["--rerun-window", "2", "--rerun-force-constant", "6000"]
        assert where == study

    def test_and_refuses_before_spawning(self, study):
        runtime = _runtime(study)
        with mock.patch.object(DashboardRuntime, "_spawn") as spawn:
            assert "no window 9" in runtime.run_windows_again([9])["error"]
            assert runtime.run_windows_again([1], force_constant=-1)["code"] == \
                "config.option.wrong_type"
            runtime.process = mock.Mock(poll=mock.Mock(return_value=None))
            with mock.patch.object(DashboardRuntime, "_refresh_process"):
                assert runtime.run_windows_again([1])["error"] == \
                    "A FastMDXplora workflow is already running."
        spawn.assert_not_called()


def test_the_route_runs_it(study) -> None:
    import urllib.request

    from fastmdxplora.gui.server import start_dashboard_session

    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    base = session.url.rstrip("/")
    try:
        with mock.patch.object(DashboardRuntime, "_spawn",
                               return_value={"launched": True}) as spawn:
            request = urllib.request.Request(
                base + "/api/fix", method="POST",
                data=json.dumps({"windows": [3], "duration_ns": 2}).encode(),
                headers={"Content-Type": "application/json", "Origin": base})
            ran = json.loads(urllib.request.urlopen(request, timeout=30).read())
    finally:
        session.server.shutdown()
    assert ran["ok"] and spawn.call_args.args[0][-2:] == ["--rerun-window", "3"]
    assert "--simulate-duration-ns" in spawn.call_args.args[0]


def test_the_agent_asks_then_runs_them(study, monkeypatch) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    import fastmdxplora.agent as agent_mod
    from fastmdxplora.gui.server import start_dashboard_session

    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", tempfile.mkdtemp())
    monkeypatch.setattr(agent_mod, "completion_for", lambda *a, **k: (
        lambda prompt: "DO: rerun windows 2 at 6000"))
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    ran: list[dict] = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.set_default_timeout(60000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))

            def answer(route):
                ran.append(json.loads(route.request.post_data or "{}"))
                route.fulfill(json={"ok": True})
            page.route("**/api/fix", answer)
            page.goto(session.url + "#agent", wait_until="domcontentloaded")
            page.fill("#agent-request", "rerun window 2 at 6000")
            page.keyboard.press("Enter")
            page.wait_for_selector("#agent-thread .agent-attempt:has-text('Say yes')")
            asked = page.locator("#agent-thread .agent-attempt").last.text_content()
            assert ran == []
            page.fill("#agent-request", "yes")
            page.keyboard.press("Enter")
            page.wait_for_selector("#agent-thread .agent-attempt:has-text('Started')",
                                   state="attached")
            browser.close()
    finally:
        session.server.shutdown()
    assert asked.startswith("Run window 2 again, held at 6000 kJ/mol/nm^2, keeping every "
                            "other window, and recombine the free energy? It costs 2 ns of "
                            "production and 1.5 ns of equilibration")
    assert asked.endswith("Say yes.")
    assert ran == [{"windows": [2], "force_constant": 6000.0, "duration_ns": None}]
    assert errors == []
