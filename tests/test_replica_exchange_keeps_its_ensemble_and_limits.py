"""Replica exchange must keep its ensemble and must not invent kinetics."""

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from fastmdxplora.config.loader import ConfigError, _check_replica_exchange
from fastmdxplora.simulation.replica_exchange import plan_from_block


@pytest.mark.parametrize("block", [
    {"replicas": 2.5}, {"replicas": True}, {"exchange_interval_steps": 20.1},
    {"equilibration_iterations": False}, {"max_temperature_K": float("nan")},
    {"max_temperature_K": float("inf")}, {"max_temperature_K": "hot"},
    {"solute": ["protein"]}, {"solute": ""}, [],
])
def test_invalid_settings_are_config_errors(block):
    with pytest.raises(ConfigError):
        plan_from_block(block)


@pytest.mark.parametrize("other", [
    {"metadynamics": {}}, {"umbrella": {}}, {"steered": {}},
    {"integrator": "variable_langevin"}, {"restrain_production": True},
    {"resume_from": "checkpoint.chk"}, {"stop_when": {"observable": "rg"}},
    {"temperature_K": 500},
])
def test_settings_the_sampler_cannot_honour_are_refused(other):
    with pytest.raises(ConfigError):
        _check_replica_exchange({"replica_exchange": {}, **other})


def test_an_empty_block_still_requests_replica_exchange():
    from fastmdxplora.simulation.resume import segmentability

    assert not segmentability({"simulation": {"replica_exchange": {}}}).allowed


def test_the_cost_counts_every_replica_and_its_equilibration():
    from fastmdxplora.cost import total_steps

    settings = {"production_steps": 100, "nvt_steps": 30, "npt_steps": 40,
                "replica_exchange": {"replicas": 3, "exchange_interval_steps": 20,
                                     "equilibration_iterations": 2}}
    assert total_steps(settings) == 70 + 3 * (100 + 40)
    assert total_steps({**settings, "production_steps": 0}) == 70


@pytest.mark.parametrize("ensemble, pressure", [("nvt", None), ("npt", 1.2)])
def test_the_pipeline_passes_the_resolved_ensemble(monkeypatch, tmp_path, ensemble, pressure):
    from fastmdxplora.simulation import pipeline, replica_exchange, runner

    params = pipeline._resolve_params({"replica_exchange": {"exchange_interval_steps": 20},
                                      "production_steps": 100, "nvt_steps": 30, "npt_steps": 40})
    arguments = {k: params[k] for k in ("duration_ns", "timestep_fs", "nvt_steps", "npt_steps",
                                      "production_steps", "nvt_duration_ns", "npt_duration_ns")}
    settled = SimpleNamespace(final_state=tmp_path / "settled.xml", pressure_bar_used=1.2,
                              minimized_state=None, notes=[],
                              resolved={"ensemble": ensemble, "nvt_steps": 30, "npt_steps": 40})
    monkeypatch.setattr(runner, "run_simulation", lambda **kw: settled)
    called = {}

    def exchange(**kw):
        called.update(kw)
        return {"trajectory": tmp_path / "production.dcd", "topology": tmp_path / "topology.pdb",
                "final_state": tmp_path / "state_final.xml", "frames": 5, "duration_ns": .0002,
                "record": {"platform": "CPU"}}

    monkeypatch.setattr(replica_exchange, "run_replica_exchange", exchange)
    result = pipeline._run_replica_exchange(params, run_arguments=arguments,
        system_xml=tmp_path / "system.xml", topology=tmp_path / "topology.pdb",
        output_dir=tmp_path, presenter=None)
    assert called["pressure_bar"] == pressure
    assert called["state_xml"] == settled.final_state
    assert result.final_state == tmp_path / "state_final.xml"
    assert result.resolved["nvt_steps"] == 30 and result.resolved["npt_steps"] == 40
    assert result.resolved["trajectory_interval_steps"] == 20


def test_replica_frames_keep_structural_values_but_withhold_kinetics(tmp_path):
    md = pytest.importorskip("mdtraj")
    from fastmdxplora.analysis import AnalysisOrchestrator

    topology = md.Topology()
    chain = topology.add_chain()
    for i in range(4):
        topology.add_atom("CA", md.element.carbon, topology.add_residue("ALA", chain, resSeq=i + 1))
    xyz = np.random.default_rng(1).normal(size=(40, 4, 3)).astype(np.float32)
    trajectory = md.Trajectory(xyz, topology, time=np.arange(40))
    simulation = tmp_path / "simulation"
    simulation.mkdir()
    (simulation / "replica_exchange.json").write_text("{}")
    trajectory.save_hdf5(str(simulation / "production.h5"))
    ao = AnalysisOrchestrator(simulation / "production.h5", output_dir=tmp_path / "analysis")
    results = ao.run(include=["rg", "diffusion", "markov_model", "water_sites"])
    assert results["rg"].status == "ok", results["rg"].message
    for name in ("diffusion", "markov_model", "water_sites"):
        assert results[name].status == "skipped"
        assert "continuous trajectory" in results[name].message
    record = json.loads(Path(results["rg"].options_path).read_text())["findings"]["mean"]
    assert np.isfinite(record["mean"])
    assert "Replica-exchange" in record["not_a_measurement"]
    assert "statistical_inefficiency" not in record


@pytest.mark.slow
def test_a_whole_replica_exchange_study_reports_what_it_ran(tmp_path):
    pytest.importorskip("openmmtools")
    pytest.importorskip("pdbfixer")
    from fastmdxplora import FastMDXplora
    from tests.test_a_real_study_runs_end_to_end import TRI_ALANINE

    pdb = tmp_path / "tri.pdb"
    pdb.write_text(TRI_ALANINE)
    out = tmp_path / "out"
    config = {"systems": [{"id": "tri", "system": str(pdb)}],
              "setup": {"ph": 7, "solvent_padding_nm": 1, "nonbonded_cutoff_nm": .9},
              "simulation": {"platform": "CPU", "random_seed": 17, "nvt_steps": 50,
                             "npt_steps": 50, "production_steps": 100,
                             "replica_exchange": {"replicas": 2, "max_temperature_K": 330,
                                                  "exchange_interval_steps": 20,
                                                  "equilibration_iterations": 1}},
              "analysis": {"include": ["rg", "replica_mixing", "diffusion", "markov_model"]}}
    results = FastMDXplora(config_data=config, output_dir=str(out)).explore()
    assert [r.status for r in results] == ["ok"]
    manifest = json.loads((out / "manifest.json").read_text())
    assert all(p["status"] == "ok" for p in manifest["phases"])
    record = json.loads((out / "simulation" / "simulation_parameters.json").read_text())
    assert record["resolved"]["ensemble"] == "npt"
    assert record["resolved"]["production_steps"] == 100
    assert record["resolved"]["npt_steps"] == 50
    assert "replica_exchange.json" in record["artifacts_written"]
    assert (out / "report" / "report.md").is_file()
    assert (out / "analysis" / "replica_mixing" / "options.json").is_file()
