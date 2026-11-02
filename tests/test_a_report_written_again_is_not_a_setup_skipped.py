"""A report written again is not recorded as a setup skipped.

A run given `setup_from` records its setup as skipped, taken from the
prepared system it simulates, so its Manifest names the system that was
simulated. That record was added whatever the run's plan: a report or an
analysis written again, which simulates nothing, recorded a setup skipped
at its own time, and the study's timeline placed a setup there.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.mark.parametrize("phases", [["report"], ["analysis", "report"]])
def test_a_run_that_simulates_nothing_records_no_setup(tmp_path: Path, monkeypatch,
                                                        phases) -> None:
    from fastmdxplora import FastMDXplora, orchestrator

    prepared = tmp_path / "prepared"
    prepared.mkdir()
    for name in ("system.xml", "state.xml", "topology.pdb"):
        (prepared / name).write_text("stood in for\n", encoding="utf-8")
    pdb = tmp_path / "tri.pdb"
    pdb.write_text("END\n", encoding="utf-8")

    def ran(self, phase, *_args, **_kwargs):
        return orchestrator.PhaseResult(name=phase, status="ok",
                                        output_dir=self.output_dir / phase)

    monkeypatch.setattr(orchestrator.FastMDXplora, "_run_phase", ran)
    study = tmp_path / "study"
    results = FastMDXplora(config_data={
        "systems": [{"id": "tri", "system": str(pdb)}],
        "simulation": {"setup_from": str(prepared), "platform": "CPU"},
        "include_phase": phases,
    }, output_dir=str(study)).explore()
    recorded = [(phase.name, phase.status) for result in results for phase in result.phases]
    assert ("setup", "skipped") not in recorded
    manifest = json.loads((study / "manifest.json").read_text(encoding="utf-8"))
    assert [p["name"] for p in manifest["phases"]] == phases
