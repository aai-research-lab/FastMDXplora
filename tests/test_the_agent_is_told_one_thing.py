"""The Agent is given one meaning for each thing, whichever way it is asked.

Two settings meant different things depending on the way in:

- **Attempts.** The command line gave three in all and its help called them
  corrections, which would be four; the browser and Python gave four. One
  number now, three in all, the first included.
- **Continuing a study.** The prompt told the Agent to continue a study by
  naming it in `resume_from`, with `duration_ns` the total production it
  should end with. The browser's run status handed it the raw checkpoint
  form instead, with `duration_ns` the amount more, which ran as a separate
  study and joined nothing. The browser now offers the study form, and a
  config continuing a study is run inside that study and watched there;
  the GUI had watched a new, empty folder and marked the run failed.
"""

from __future__ import annotations

import json
from pathlib import Path



def _counting(monkeypatch, module):
    """A model that never writes a valid config, counting how often it is
    asked."""
    calls: list[str] = []

    def complete(prompt: str) -> str:
        calls.append(prompt)
        return "systems: [not a mapping"

    monkeypatch.setattr(module, "completion_for", lambda *a, **k: complete)
    return calls


class TestAttempts:

    def test_python_gives_three_in_all(self) -> None:
        from fastmdxplora.agent import propose_config

        calls: list[str] = []
        proposal = propose_config("simulate 1UBQ", lambda p: calls.append(p) or "x: [")
        assert not proposal.accepted
        assert len(calls) == 3

    def test_the_browser_gives_the_same(self, monkeypatch) -> None:
        import fastmdxplora.agent as agent
        from fastmdxplora.gui.agent_panel import propose_endpoint

        calls = _counting(monkeypatch, agent)
        propose_endpoint({"request": "simulate 1UBQ"})
        assert len(calls) == 3

    def test_the_command_line_gives_the_same(self, monkeypatch) -> None:
        import importlib

        import fastmdxplora.agent as agent

        cli = importlib.import_module("fastmdxplora.cli.main")
        calls = _counting(monkeypatch, agent)
        cli.main(["agent", "simulate 1UBQ"])
        assert len(calls) == 3
        parser = cli._build_parser()
        agent_parser = parser._subparsers._group_actions[0].choices["agent"]
        help_text = next(a.help for a in agent_parser._actions
                         if "--attempts" in a.option_strings)
        assert "in all, the first included" in help_text

    def test_asked_for_more_it_gives_more(self) -> None:
        from fastmdxplora.agent import propose_config

        calls: list[str] = []
        propose_config("simulate 1UBQ", lambda p: calls.append(p) or "x: [", max_cycles=5)
        assert len(calls) == 5


class TestContinuingAStudy:

    def test_the_prompt_and_the_offer_mean_the_same_thing(self) -> None:
        from fastmdxplora.agent.propose import prompt_for

        prompt = prompt_for("continue it")
        assert "the total production the study should end with" in prompt
        assert "`duration_ns` there is the TOTAL" in prompt
        assert "how much more\nproduction is wanted" not in prompt

    def _study(self, tmp_path: Path) -> Path:
        study = tmp_path / "runs" / "study"
        (study / "simulation").mkdir(parents=True)
        (study / "manifest.json").write_text(json.dumps({"phases": [
            {"name": "setup", "status": "ok"}, {"name": "simulation", "status": "ok"}]}),
            encoding="utf-8")
        (study / "exploration.yml").write_text("# how it was started\n", encoding="utf-8")
        return study

    def _runtime(self, tmp_path: Path, monkeypatch):
        from fastmdxplora.gui import exploration
        from fastmdxplora.gui.exploration import DashboardRuntime

        runtime = DashboardRuntime(workspace_root=tmp_path / "workspace",
                                   exploration_root=tmp_path / "runs")
        spawned: list[tuple[list[str], Path]] = []

        def spawn(self, command, output_dir, dashboard_url, **given):
            spawned.append((command, output_dir))
            self.active_root = output_dir
            return {"launched": True, "output": str(output_dir)}

        monkeypatch.setattr(exploration.DashboardRuntime, "_spawn", spawn)
        return runtime, spawned

    def test_it_runs_inside_the_study_and_is_watched_there(self, tmp_path, monkeypatch) -> None:
        study = self._study(tmp_path)
        runtime, spawned = self._runtime(tmp_path, monkeypatch)
        answer = runtime.launch_from_config(None, config={
            "simulation": {"resume_from": str(study), "duration_ns": 0.6}})
        assert answer["ok"], answer
        assert answer["continues"] == str(study.resolve())
        [(command, watched)] = spawned
        assert watched == study.resolve()
        config = Path(command[command.index("--config") + 1])
        assert config.parent.parent == runtime.workspace_root / "continuations"
        # The study's own record of how it was started is left alone.
        assert (study / "exploration.yml").read_text(encoding="utf-8") == "# how it was started\n"

    def test_a_checkpoint_is_still_an_ordinary_run(self, tmp_path, monkeypatch) -> None:
        study = self._study(tmp_path)
        checkpoint = study / "simulation" / "checkpoint.chk"
        checkpoint.write_bytes(b"x")
        runtime, spawned = self._runtime(tmp_path, monkeypatch)
        monkeypatch.setattr("fastmdxplora.gui.exploration.exploration_environment_error",
                            lambda *_: None)
        answer = runtime.launch_from_config(None, config={
            "output": "segment", "systems": [{"system": "1UBQ"}],
            "simulation": {"resume_from": str(checkpoint), "setup_from": str(study),
                           "duration_ns": 0.1}})
        assert "continues" not in answer
        [(_, watched)] = spawned
        assert watched != study.resolve()

    def test_a_folder_that_is_no_study_is_not_continued(self, tmp_path, monkeypatch) -> None:
        stranger = tmp_path / "runs" / "somewhere"
        stranger.mkdir(parents=True)
        runtime, _ = self._runtime(tmp_path, monkeypatch)
        assert runtime._study_being_continued(
            {"simulation": {"resume_from": str(stranger)}}) is None
