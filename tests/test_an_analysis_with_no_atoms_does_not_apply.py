"""An analysis with no atoms to read does not apply; it has not failed.

A duplex of DNA alone, 1BNA, ran its analysis phase with eight of its
analyses refused, each "Atom selection 'protein' matched zero atoms": the
scope a study's analyses read where they have no selection of their own is
`protein` without a ligand, and backbone dihedrals are a protein's. A study
of DNA read as one whose analyses broke. They are left out of the plan, as an
analysis with no water, no bilayer or too few atoms to align already is.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import mdtraj as md
import numpy as np


def _dna(n_residues: int = 6) -> md.Trajectory:
    top = md.Topology()
    chain = top.add_chain()
    for name in ("DA", "DT", "DG", "DC") * (n_residues // 4 + 1):
        residue = top.add_residue(name, chain)
        for atom, element in (("P", md.element.phosphorus),
                              ("C4'", md.element.carbon),
                              ("N9", md.element.nitrogen)):
            top.add_atom(atom, element, residue)
    xyz = np.random.default_rng(0).random((3, top.n_atoms, 3)).astype(np.float32)
    return md.Trajectory(xyz, top)


def _protein(n_residues: int = 12) -> md.Trajectory:
    top = md.Topology()
    chain = top.add_chain()
    for _ in range(n_residues):
        residue = top.add_residue("ALA", chain)
        for atom, element in (("N", md.element.nitrogen), ("CA", md.element.carbon),
                              ("C", md.element.carbon), ("O", md.element.oxygen)):
            top.add_atom(atom, element, residue)
    xyz = np.random.default_rng(0).random((3, top.n_atoms, 3)).astype(np.float32)
    return md.Trajectory(xyz, top)


def _plan(traj: md.Trajectory, *, options=None, exclude=None) -> list[str]:
    from fastmdxplora.analysis.orchestrator import AnalysisOrchestrator

    orchestrator = AnalysisOrchestrator.__new__(AnalysisOrchestrator)
    orchestrator.traj = traj
    orchestrator.ligand_resname = None
    orchestrator.default_selection = None
    orchestrator.scope_selection = "protein"
    orchestrator.output_dir = Path(tempfile.mkdtemp())
    return orchestrator._build_plan(None, exclude, options)


def test_the_protein_analyses_are_left_out_of_a_dna_study() -> None:
    plan = _plan(_dna())
    for name in ("rg", "sasa", "end_to_end", "moments_of_inertia", "dihedrals"):
        assert name not in plan, name


def test_the_check_against_its_images_still_runs_on_dna() -> None:
    """The image check finds the macromolecules itself, DNA among them; the
    scope only adds what else is solute. It ran on 1BNA before the gate and
    must still."""
    traj = _dna()
    traj.unitcell_vectors = np.repeat(np.eye(3, dtype=np.float32)[None] * 5.0,
                                      traj.n_frames, axis=0)
    assert "image_check" in _plan(traj)


def test_and_out_of_a_plan_written_as_what_to_leave_out() -> None:
    plan = _plan(_dna(), exclude=["rmsd"])
    assert "rg" not in plan and "dihedrals" not in plan


def test_a_selection_of_its_own_brings_it_back() -> None:
    """The selection the analysis will be given is the one judged: a study
    that names the nucleic acid for the radius of gyration gets one."""
    plan = _plan(_dna(), options={"rg": {"selection": "all"}})
    assert "rg" in plan


def test_a_protein_keeps_every_one() -> None:
    plan = _plan(_protein())
    for name in ("rg", "sasa", "end_to_end", "dihedrals"):
        assert name in plan, name


def test_a_selection_that_cannot_be_parsed_is_left_for_the_analysis_to_refuse() -> None:
    plan = _plan(_dna(), options={"rg": {"selection": "resname ((("}})
    assert "rg" in plan


def test_the_protein_gate_is_declared_with_the_others() -> None:
    """Read by the plan from every analysis, it was declared on one."""
    from fastmdxplora.analysis.base import Analysis
    from fastmdxplora.analysis.dihedrals import Dihedrals

    assert Analysis.__dict__["requires_protein"] is False
    assert Dihedrals.requires_protein is True


def test_a_study_none_applies_to_says_so(tmp_path) -> None:
    """A study of DNA alone planned nothing, and its analysis was said
    "complete" with no word on why."""
    import json

    from fastmdxplora.analysis.orchestrator import NOTHING_APPLIES, AnalysisOrchestrator

    traj = _dna()
    traj[0].save_pdb(str(tmp_path / "top.pdb"))
    traj.save_dcd(str(tmp_path / "traj.dcd"))
    orchestrator = AnalysisOrchestrator(trajectory=str(tmp_path / "traj.dcd"),
                                        topology=str(tmp_path / "top.pdb"),
                                        output_dir=tmp_path / "analysis")
    assert orchestrator.run() == {}
    manifest = json.loads((tmp_path / "analysis" / "analysis_manifest.json").read_text())
    assert manifest["plan"] == [] and manifest["nothing_applies"] == NOTHING_APPLIES
    assert "--scope all" in NOTHING_APPLIES and "--analyses" in NOTHING_APPLIES

    from fastmdxplora.orchestrator import PhaseResult, _how_it_ended

    ended = _how_it_ended(PhaseResult(name="analysis", status="ok",
                                      output_dir=tmp_path / "analysis"))
    assert ended["status"] == "warning" and "ran nothing" in ended["message"]

    from fastmdxplora.report.document import _results_section

    assert "No analysis of the molecule applies" in _results_section(tmp_path)


def test_a_plan_asked_for_is_not_said_to_be_empty_for_want_of_atoms() -> None:
    from fastmdxplora.analysis.orchestrator import _REGISTRY, _nothing_applies

    assert _nothing_applies(_plan(_protein()), None, None) is None
    assert _nothing_applies([], ["rg"], None) is None
    assert _nothing_applies([], None, list(_REGISTRY)) is None
    assert _nothing_applies(_plan(_dna()), None, None)


def test_leaving_out_every_analysis_but_the_image_check_is_asked_for() -> None:
    """Named one by one, every analysis of the molecule but the image check
    was still said to be left out for what the system does not have."""
    from fastmdxplora.analysis.orchestrator import _REGISTRY, _nothing_applies

    named = [name for name in _REGISTRY if name != "image_check"]
    assert _nothing_applies(["image_check"], None, named) is None
    assert _nothing_applies(["image_check"], None, named[1:])


def test_an_image_check_that_ran_is_not_said_as_nothing(tmp_path) -> None:
    import json

    from fastmdxplora.orchestrator import PhaseResult, _how_it_ended

    (tmp_path / "analysis_manifest.json").write_text(json.dumps(
        {"plan": ["image_check"], "nothing_applies": "None applies."}))
    ended = _how_it_ended(PhaseResult(name="analysis", status="ok", output_dir=tmp_path))
    assert ended["status"] == "warning"
    assert "ran nothing" not in ended["message"]
    assert "no analysis applies to this study but the image check" in ended["message"]


def test_the_command_line_says_it_once(tmp_path, capfd) -> None:
    """`fastmdx analyze` on DNA said it twice: a log line and a plain one."""
    from fastmdxplora.analysis.orchestrator import NOTHING_APPLIES
    from fastmdxplora.cli.main import main

    traj = _dna()
    traj[0].save_pdb(str(tmp_path / "top.pdb"))
    traj.save_dcd(str(tmp_path / "traj.dcd"))
    main(["analyze", "-s", str(tmp_path / "top.pdb"), "--trajectory", str(tmp_path / "traj.dcd"),
          "--topology", str(tmp_path / "top.pdb"), "--output", str(tmp_path / "out")])
    printed = capfd.readouterr()
    assert (printed.out + printed.err).count(NOTHING_APPLIES) == 1
