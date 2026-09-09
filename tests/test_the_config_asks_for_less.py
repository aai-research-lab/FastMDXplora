"""Four places a config asked the user for something it already had.

Each is the same shape: a value the framework holds, or a word it already
understands, demanded from the user in a particular spelling or a particular
form. None of them changed what a study does -- they changed how much the
person writing it had to know.
"""

from __future__ import annotations

import numpy as np
import pytest


def _topology():
    """A protein and a six-carbon ligand, the same shape test_steered uses."""
    import mdtraj as md

    top = md.Topology()
    chain = top.add_chain()
    for index in range(30):
        residue = top.add_residue("ALA", chain, resSeq=index + 1)
        for name in ("N", "CA", "C", "O"):
            top.add_atom(name, md.element.carbon, residue)
    ligand_chain = top.add_chain()
    ligand = top.add_residue("BNZ", ligand_chain, resSeq=900)
    for index in range(6):
        top.add_atom(f"C{index}", md.element.carbon, ligand)
    return top


def _structure(separation_nm: float):
    """One frame with the ligand a known distance from the site."""
    import mdtraj as md

    top = _topology()
    xyz = np.zeros((1, top.n_atoms, 3), dtype=np.float32)
    ligand = top.select("resname BNZ")
    xyz[0, ligand, 0] = separation_nm
    return md.Trajectory(xyz, top)


# ---------------------------------------------------------------------------
# One ligand, either spelling
# ---------------------------------------------------------------------------
def test_a_variable_block_accepts_the_setup_blocks_word():
    """`ligand_name` is what setup calls it, and it works here too.

    A user who wrote `ligand_name: BEN` once should not discover that the
    umbrella block wants the same string under a different key.
    """
    from fastmdxplora.simulation.metadynamics import plan_from_config

    plan = plan_from_config(
        {"collective_variable": "ligand_distance",
         "ligand_name": "BNZ",
         "site_selection": "resid 1 to 3 and name CA",
         "sigma": 0.05, "unbounded": True},
        _topology())

    assert sorted(plan.atoms) == ["ligand", "site"]
    assert len(plan.atoms["ligand"]) == 6


def test_the_setup_block_accepts_the_variable_blocks_word():
    """And `ligand_resname` works where setup wanted `ligand_name`."""
    from fastmdxplora.setup.pipeline import _explicit_ligand_resnames

    assert _explicit_ligand_resnames({"ligand_resname": "BEN"}) == ("BEN",)
    assert _explicit_ligand_resnames({"ligand_name": "BEN"}) == ("BEN",)


# ---------------------------------------------------------------------------
# Naming a study rather than its interior
# ---------------------------------------------------------------------------
def _finished_study(root, interior):
    directory = root / interior
    directory.mkdir(parents=True)
    for name in ("system.xml", "state.xml", "topology.pdb"):
        (directory / name).write_text("x", encoding="utf-8")
    return directory


class _Orchestrator:
    def __init__(self, output_dir):
        self.output_dir = output_dir


def test_naming_the_study_finds_the_system_a_single_run_wrote(tmp_path):
    """`runs/reference`, not `runs/reference/setup`.

    Where the prepared system sits inside a study is this package's layout,
    not something a user chose or would remember.
    """
    from fastmdxplora.simulation.pipeline import _where_the_system_was_prepared

    inside = _finished_study(tmp_path, "study/setup")

    found, named = _where_the_system_was_prepared(
        _Orchestrator(tmp_path), str(tmp_path / "study"))

    assert found == inside
    assert named is True


def test_naming_the_study_finds_the_system_a_set_of_windows_shared(tmp_path):
    """Umbrella studies keep theirs somewhere else again.

    A single run writes `setup/`; a set of windows writes one
    `shared_setup/setup/` for all of them. Requiring the exact interior
    path meant knowing which shape the earlier study had.
    """
    from fastmdxplora.simulation.pipeline import _where_the_system_was_prepared

    inside = _finished_study(tmp_path, "study/shared_setup/setup")

    found, _ = _where_the_system_was_prepared(
        _Orchestrator(tmp_path), str(tmp_path / "study"))

    assert found == inside


def test_the_exact_directory_still_works(tmp_path):
    """Naming the setup directory itself is not broken by finding it."""
    from fastmdxplora.simulation.pipeline import _where_the_system_was_prepared

    inside = _finished_study(tmp_path, "study/shared_setup/setup")

    found, _ = _where_the_system_was_prepared(
        _Orchestrator(tmp_path), str(inside))

    assert found == inside


def test_a_half_written_setup_is_not_mistaken_for_a_finished_one(tmp_path):
    """Two of the three files is a setup that stopped, not one to start from."""
    from fastmdxplora.simulation.pipeline import _where_the_system_was_prepared

    partial = tmp_path / "study" / "setup"
    partial.mkdir(parents=True)
    (partial / "system.xml").write_text("x", encoding="utf-8")
    (partial / "state.xml").write_text("x", encoding="utf-8")

    found, _ = _where_the_system_was_prepared(
        _Orchestrator(tmp_path), str(tmp_path / "study"))

    # Hands back what was named, so the error names the user's own path.
    assert found == tmp_path / "study"


# ---------------------------------------------------------------------------
# Where the pull starts
# ---------------------------------------------------------------------------
def test_a_pull_starts_where_the_system_already_is():
    """`from` is read from the structure rather than demanded.

    The old message told the user to measure the variable in the structure
    being simulated and type it back in. That is a number this is holding.
    """
    from fastmdxplora.simulation.steered import plan_steered

    plan = plan_steered(
        {"collective_variable": "ligand_distance",
         "ligand_resname": "BNZ",
         "site_selection": "resid 1 to 3 and name CA",
         "to": 3.0, "steps": 1000},
        _topology(), structure=_structure(1.25))

    assert plan.from_value == pytest.approx(1.25, abs=1e-4)
    assert plan.to_value == 3.0


def test_what_the_user_writes_still_wins():
    """A measured default must not overrule a stated value."""
    from fastmdxplora.simulation.steered import plan_steered

    plan = plan_steered(
        {"collective_variable": "ligand_distance",
         "ligand_resname": "BNZ",
         "site_selection": "resid 1 to 3 and name CA",
         "from": 0.4, "to": 3.0, "steps": 1000},
        _topology(), structure=_structure(1.25))

    assert plan.from_value == pytest.approx(0.4)


def test_without_a_structure_it_still_refuses_rather_than_guessing():
    """Zero is a real position, and it is not a default.

    Anchoring a pull at zero drags the system towards it before the pull
    begins. With nothing to measure, saying so is the only honest answer.
    """
    from fastmdxplora.simulation.steered import plan_steered

    with pytest.raises(ValueError, match="where the run starts"):
        plan_steered(
            {"collective_variable": "ligand_distance",
             "ligand_resname": "BNZ",
             "site_selection": "resid 1 to 3 and name CA",
             "to": 3.0}, _topology())


def test_a_variable_that_cannot_be_read_from_coordinates_says_so():
    """Not every variable is measurable from one frame, and that is stated."""
    from fastmdxplora.simulation.steered import measure_in, plan_steered

    plan = plan_steered(
        {"collective_variable": "ligand_distance",
         "ligand_resname": "BNZ",
         "site_selection": "resid 1 to 3 and name CA",
         "from": 0.4, "to": 3.0}, _topology())

    unreadable = type(plan.cv)(**{**plan.cv.__dict__,
                                  "collective_variable": "q"})
    assert measure_in(unreadable, _structure(1.25)) is None


def test_the_centre_is_mass_weighted():
    """PLUMED's COM is mass-weighted, and a centroid is not the same point."""
    import mdtraj as md

    from fastmdxplora.simulation.steered import measure_in, plan_steered

    top = _topology()
    # One heavy atom in the ligand pulls its centre towards itself.
    for atom in top.atoms:
        if atom.residue.name == "BNZ" and atom.name == "C0":
            atom.element = md.element.iodine

    xyz = np.zeros((1, top.n_atoms, 3), dtype=np.float32)
    ligand = top.select("resname BNZ")
    xyz[0, ligand, 0] = 1.0
    xyz[0, ligand[0], 0] = 2.0          # the heavy one, further out
    frame = md.Trajectory(xyz, top)

    plan = plan_steered(
        {"collective_variable": "ligand_distance",
         "ligand_resname": "BNZ",
         "site_selection": "resid 1 to 3 and name CA",
         "from": 0.4, "to": 3.0}, top)

    measured = measure_in(plan.cv, frame)
    centroid = (5 * 1.0 + 2.0) / 6

    assert measured > centroid + 0.1
