"""What a study's records and pages say is what happened.

Four places said something else:

- The methods said each heterogen decision was recorded in
  ``setup_parameters.json``; the decisions were logged and never written.
  They are written now, and the sentence appears only where they are.
- The report's settings list opened "Production MD was performed" for a
  study that ran none (``duration_ns: 0``).
- The GUI held every finished study to a prepared system in its own
  ``setup/`` and a finished simulation, so an analysis of a supplied
  trajectory, a setup-only study, a run given ``setup_from`` and a study of
  several runs were shown as failed; and a run it adopted was judged by a
  Manifest ``status`` that does not exist, so every one read as failed.
- A slide deck that could not be written was said at debug level and
  recorded nowhere, where a missing PDF is recorded in ``not_produced.json``.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from fastmdxplora.gui.exploration import DashboardRuntime, _AdoptedProcess
from fastmdxplora.report.methods import methods_paragraphs


def _setup_record(**extra):
    return {"parameters": {"heterogens": "auto", "ph": 7.0}, **extra}


class TestTheHeterogenDecisions:

    def test_they_are_written_to_the_setup_record(self, tmp_path: Path) -> None:
        from fastmdxplora.setup.heterogens import resolve
        from fastmdxplora.setup.pipeline import _write_manifest, decision_records
        from tests.test_heterogens import PROTEIN, _atom, _structure

        lines = PROTEIN + [
            _atom("HETATM", 10, "C1", " ", "EPE", "A", 301, 20, 20, 20),
            _atom("HETATM", 11, "O", " ", "HOH", "A", 401, 9, 9, 9, element="O"),
        ]
        params = {"heterogens": "auto",
                  "_heterogen_decisions": decision_records(resolve(_structure(lines)))}
        _write_manifest(tmp_path, SimpleNamespace(system="x.pdb"), "file", params, [], [])
        record = json.loads((tmp_path / "setup_parameters.json").read_text(encoding="utf-8"))
        decided = {d["component"]: d for d in record["heterogen_decisions"]}
        assert decided["EPE"]["action"] == "discard"
        assert decided["EPE"]["copies"] == ["EPE A301"]
        assert "crystallization additive" in decided["EPE"]["reason"]
        assert decided["HOH"]["action"] == "discard"

    def test_the_methods_say_so_where_they_are(self, tmp_path: Path) -> None:
        text = methods_paragraphs(tmp_path, _setup_record(heterogen_decisions=[
            {"component": "EPE", "action": "discard", "reason": "x", "copies": []}]), {})
        assert "recorded in `setup_parameters.json`" in text

    @pytest.mark.parametrize("policy", ["auto", "drop", "keep"])
    def test_and_not_where_they_are_not(self, tmp_path: Path, policy: str) -> None:
        setup = {"parameters": {"heterogens": policy, "ph": 7.0}}
        text = methods_paragraphs(tmp_path, setup, {})
        assert "recorded in" not in text
        assert "what went is recorded" not in text


class TestTheSettingsList:

    def _methods(self, tmp_path: Path, production_steps: int) -> str:
        from fastmdxplora.report.context import PhaseContext
        from fastmdxplora.report.document import _methods_section

        (tmp_path / "simulation").mkdir()
        (tmp_path / "simulation" / "simulation_parameters.json").write_text(json.dumps({
            "parameters": {"temperature_K": 300.0},
            "resolved": {"production_steps": production_steps}}), encoding="utf-8")
        return _methods_section(tmp_path, PhaseContext(simulation_present=True))

    def test_a_study_that_ran_no_production_does_not_claim_one(self, tmp_path: Path) -> None:
        text = self._methods(tmp_path, 0)
        assert "No production was run" in text
        assert "Production MD was performed" not in text

    def test_one_that_did_says_so(self, tmp_path: Path) -> None:
        assert "Production MD was performed" in self._methods(tmp_path, 1000)


def _finished(tmp_path: Path, **records) -> DashboardRuntime:
    runtime = DashboardRuntime(workspace_root=tmp_path / "workspace",
                               exploration_root=tmp_path / "runs")
    root = runtime.exploration_root / "study"
    root.mkdir(parents=True)
    for name, content in records.items():
        path = root / name.replace("__", "/")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content if isinstance(content, str) else json.dumps(content),
                        encoding="utf-8")
    runtime.active_root = root
    runtime.log_path = root / "exploration.log"
    runtime.command = ["python", "-m", "fastmdxplora.cli.main", "explore"]
    runtime.process = SimpleNamespace(poll=lambda: 0)
    return runtime


def _manifest(*phases):
    return {"phases": [{"name": name, "status": status, "message": message}
                       for name, status, message in phases]}


class TestAFinishedStudyIsNotShownAsFailed:

    def test_an_analysis_of_a_supplied_trajectory(self, tmp_path: Path) -> None:
        runtime = _finished(tmp_path, **{"manifest.json": _manifest(
            ("analysis", "ok", ""), ("report", "ok", ""))})
        state = runtime.snapshot()
        assert state["status"] == "completed", state["error"]

    def test_a_setup_only_study(self, tmp_path: Path) -> None:
        records = {"manifest.json": _manifest(("setup", "ok", ""))}
        records.update({f"setup__{n}": "x" for n in ("system.xml", "state.xml", "topology.pdb")})
        assert _finished(tmp_path, **records).snapshot()["status"] == "completed"

    def test_a_run_given_setup_from(self, tmp_path: Path) -> None:
        records = {
            "manifest.json": _manifest(("setup", "skipped", "Not run here"),
                                       ("simulation", "ok", "")),
            "simulation__state_final.xml": "<State/>",
            "simulation__simulation_parameters.json": {
                "platform_used": "CPU", "duration_ns_actual": 0.01},
        }
        assert _finished(tmp_path, **records).snapshot()["status"] == "completed"

    def test_a_study_of_several_runs(self, tmp_path: Path) -> None:
        records = {"batch_manifest.json": {"runs": [
            {"run_id": "s1", "status": "ok"}, {"run_id": "s2", "status": "ok"}]}}
        assert _finished(tmp_path, **records).snapshot()["status"] == "completed"


class TestAFailureIsStillOne:

    def test_a_failed_run_of_several_is_named(self, tmp_path: Path) -> None:
        records = {"batch_manifest.json": {"runs": [
            {"run_id": "s1", "status": "ok"}, {"run_id": "s2", "status": "error"}]}}
        state = _finished(tmp_path, **records).snapshot()
        assert state["status"] == "failed"
        assert "1 of the study's runs failed (s2)" in state["error"]

    def test_a_failed_phase_is_named_with_its_message(self, tmp_path: Path) -> None:
        records = {"manifest.json": _manifest(("setup", "ok", ""),
                                              ("simulation", "error", "NaN at step 40"))}
        records.update({f"setup__{n}": "x" for n in ("system.xml", "state.xml", "topology.pdb")})
        state = _finished(tmp_path, **records).snapshot()
        assert state["status"] == "failed"
        assert "The simulation phase failed: NaN at step 40" in state["error"]

    def test_a_simulation_that_says_ok_and_wrote_nothing(self, tmp_path: Path) -> None:
        records = {"manifest.json": _manifest(("setup", "ok", ""), ("simulation", "ok", ""))}
        records.update({f"setup__{n}": "x" for n in ("system.xml", "state.xml", "topology.pdb")})
        state = _finished(tmp_path, **records).snapshot()
        assert state["status"] == "failed"
        assert "without producing a completed molecular dynamics simulation" in state["error"]


class TestAnAdoptedRun:

    def _gone(self, tmp_path: Path, monkeypatch, manifest) -> _AdoptedProcess:
        (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        adopted = _AdoptedProcess(999_999, tmp_path)
        monkeypatch.setattr(adopted, "_alive", lambda: False)
        return adopted

    def test_one_that_finished_reads_as_finished(self, tmp_path: Path, monkeypatch) -> None:
        adopted = self._gone(tmp_path, monkeypatch, _manifest(
            ("setup", "ok", ""), ("simulation", "ok", ""), ("analysis", "ok", "")))
        assert adopted.poll() == 0

    def test_one_that_failed_reads_as_failed(self, tmp_path: Path, monkeypatch) -> None:
        adopted = self._gone(tmp_path, monkeypatch, _manifest(
            ("setup", "ok", ""), ("simulation", "error", "")))
        assert adopted.poll() == 1

    def test_one_that_recorded_nothing_reads_as_failed(self, tmp_path: Path, monkeypatch) -> None:
        assert self._gone(tmp_path, monkeypatch, {}).poll() == 1


def test_a_deck_that_could_not_be_written_is_recorded(tmp_path: Path, monkeypatch) -> None:
    from fastmdxplora.report import slides

    def unavailable(*_args, **_kwargs):
        raise ImportError("No module named 'pptx'")

    monkeypatch.setattr(slides, "_build_pptx", unavailable)
    monkeypatch.setattr(slides, "_outline_markdown", lambda *_: "# outline\n")
    missing: list[tuple[str, str]] = []
    written = slides.build_slides(orchestrator=None, output_dir=tmp_path, title="t",
                                  not_produced=missing)
    assert written == ["slides_outline.md"]
    assert [name for name, _ in missing] == ["slides.pptx"]
    assert "python-pptx" in missing[0][1]
