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
