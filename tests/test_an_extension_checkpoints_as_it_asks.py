"""An extension writes its checkpoints as often as its config asks.

A JCC rerun extended on a machine with a wall time asks for checkpoints
often enough to lose little at the stop: `checkpoint_interval_steps: 1000`
beside `extra_ns` in the extension's config. The piece ran at the study's
own interval whatever the extension said (`segment-001.yml` recorded
10000), so a piece of 6,000 steps wrote no checkpoint, and a stop lost all
of it. Asked from the command line or from Python, the piece runs at the
interval asked, and `fastmdx resume` carries a stopped piece on at the
interval it was asked to run at.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tests.test_a_study_is_extended_in_place import _study


class _Failed:
    """The piece's run, recorded and not run: it ends in an error, so the
    extension stops after writing the piece's config."""

    def __init__(self, *, config_data, output_dir):
        self.config = config_data

    def explore(self, force=False):
        from types import SimpleNamespace

        return [SimpleNamespace(status="error", message="not run here", phases=[])]


def _with_the_study_s_interval(study: Path) -> Path:
    resolved = study / "resolved_config.yml"
    config = yaml.safe_load(resolved.read_text(encoding="utf-8"))
    config["simulation"]["checkpoint_interval_steps"] = 10000
    resolved.write_text(yaml.safe_dump(config), encoding="utf-8")
    return study


def _asked(study: Path) -> dict:
    return yaml.safe_load((study / "segment-001.yml").read_text(encoding="utf-8"))["simulation"]


@pytest.fixture
def unrun(monkeypatch):
    import fastmdxplora

    monkeypatch.setattr(fastmdxplora, "FastMDXplora", _Failed)


def test_the_command_line_runs_the_piece_at_the_interval_asked(tmp_path, unrun) -> None:
    from fastmdxplora.cli.main import main

    study = _with_the_study_s_interval(_study(duration=0.5))
    extension = tmp_path / "ext.yml"
    extension.write_text(yaml.safe_dump({"simulation": {
        "resume_from": str(study), "extra_ns": 0.012,
        "checkpoint_interval_steps": 1000}}), encoding="utf-8")
    assert main(["explore", "-c", str(extension)]) == 1
    assert _asked(study)["checkpoint_interval_steps"] == 1000


def test_python_runs_the_piece_at_the_interval_asked(unrun) -> None:
    from fastmdxplora.orchestrator import FastMDXplora

    study = _with_the_study_s_interval(_study(duration=0.5))
    FastMDXplora(config_data={"simulation": {
        "resume_from": str(study), "extra_ns": 0.012,
        "checkpoint_interval_steps": 1000}}).explore()
    assert _asked(study)["checkpoint_interval_steps"] == 1000


def test_without_an_ask_the_piece_runs_at_the_study_s(unrun) -> None:
    from fastmdxplora.simulation.resume import extend_study

    study = _with_the_study_s_interval(_study(duration=0.5))
    extend_study(study, more_ns=0.012)
    assert _asked(study)["checkpoint_interval_steps"] == 10000


def test_a_resume_carries_a_stopped_piece_on_at_the_interval_it_asked(monkeypatch) -> None:
    from fastmdxplora.simulation import resume

    # Stopped 0.1 ns into its 0.5 ns, its last extension's piece set aside.
    study = _with_the_study_s_interval(_study(done_steps=50_000, finished=False))
    (study / "segment-001.yml").write_text(yaml.safe_dump({"simulation": {
        "duration_ns": 0.012, "checkpoint_interval_steps": 1000}}), encoding="utf-8")
    asked = {}

    def extend(root, **kwargs):
        asked.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(resume, "extend_study", extend)
    monkeypatch.setattr(resume, "_set_aside_a_segment_never_checkpointed", lambda root: None)
    assert resume.resume_study(study)["did"] == "continued"
    assert asked.get("checkpoint_interval_steps") == 1000
