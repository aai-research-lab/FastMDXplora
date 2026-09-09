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


# ---------------------------------------------------------------------------
# The config a person would actually write
# ---------------------------------------------------------------------------
STUDY = """
output: runs/pull-then-hold
systems:
  - id: complex
    system: ./3PTB.pdb
setup:
  ph: 7.4
  ligand: ./BEN_ideal.sdf
  ligand_name: BEN
simulation:
  setup_from: runs/earlier
  duration_ns: 10
  timestep_fs: 2.0
  steered:
    collective_variable: ligand_distance
    ligand_name: BEN
    site_selection: "resid 189 to 195 and name CA"
    to: 2.0
    steps: 5000000
    force_constant: 5000.0
  umbrella:
    collective_variable: ligand_distance
    ligand_name: BEN
    site_selection: "resid 189 to 195 and name CA"
    force_constant: 3000.0
    from: 0.4
    to: 2.0
    n_windows: 30
    equilibration_fraction: 0.2
execution:
  mode: sequential
"""


def test_the_whole_study_validates(tmp_path):
    """Pull-then-hold, written the way the documentation says to write it.

    Everything below was accepted piecemeal and refused as a config: the
    reader took `ligand_name` in a variable block while the validator did
    not, and `seed_from` was documented in the schema before it was on the
    accepted list. A reader that understands what a validator refuses is
    the same defect as a validator that refuses what the user meant -- and
    it is the one the user meets first, before anything runs.

    Nothing here was caught by a unit test because every unit passed. This
    is the config, whole.
    """
    from fastmdxplora.config import load_config_file, validate_config

    path = tmp_path / "study.yml"
    path.write_text(STUDY, encoding="utf-8")

    data = load_config_file(path)
    validate_config(data)

    assert len(data["systems"]) == 30
    for entry in data["systems"]:
        assert "steered" not in entry["simulation"]
        assert entry["simulation"]["umbrella"]["force_constant"] == 3000.0
    assert "steered" in data["simulation"]


def test_a_study_reusing_a_pull_validates(tmp_path):
    """The other way to ask: name a finished pull instead of running one."""
    from fastmdxplora.config import load_config_file, validate_config

    text = STUDY.replace("""  steered:
    collective_variable: ligand_distance
    ligand_name: BEN
    site_selection: "resid 189 to 195 and name CA"
    to: 2.0
    steps: 5000000
    force_constant: 5000.0
""", "")
    text = text.replace("    equilibration_fraction: 0.2",
                        "    equilibration_fraction: 0.2\n"
                        "    seed_from: runs/earlier-pull")

    path = tmp_path / "reuse.yml"
    path.write_text(text, encoding="utf-8")

    data = load_config_file(path)
    validate_config(data)

    assert len(data["systems"]) == 30
    assert data["systems"][0]["simulation"]["umbrella"]["seed_from"] == \
        "runs/earlier-pull"


def test_a_typo_in_a_window_setting_is_still_refused(tmp_path):
    """Widening the list must not have opened it.

    The block refuses unknown keys because every guard it offers can be
    switched off by a misspelling -- `minimum_ovelap` was accepted once,
    ignored, and the study stitched at the default while its author
    believed otherwise.
    """
    from fastmdxplora.config import load_config_file
    from fastmdxplora.config.loader import ConfigError

    path = tmp_path / "typo.yml"
    path.write_text(STUDY.replace("minimum_overlap", "minimum_ovelap")
                    .replace("    equilibration_fraction: 0.2",
                             "    minimum_ovelap: 0.15"), encoding="utf-8")

    with pytest.raises(ConfigError, match="minimum_ovelap"):
        load_config_file(path)


# ---------------------------------------------------------------------------
# The general word for a selection
# ---------------------------------------------------------------------------
def test_select_atoms_names_the_one_selection_a_variable_takes():
    """Every tool has a word for a selection expression; this one had six.

    `select_atoms` is what MDAnalysis calls it and what a user arrives
    knowing. Where a variable takes exactly one selection there is nothing
    to disambiguate, so the general word names it.
    """
    from fastmdxplora.simulation.metadynamics import plan_from_config

    plan = plan_from_config(
        {"collective_variable": "ligand_distance",
         "ligand_name": "BNZ",
         "select_atoms": "resid 1 to 3 and name CA",
         "sigma": 0.05, "unbounded": True},
        _topology())

    assert sorted(plan.atoms) == ["ligand", "site"]
    assert len(plan.atoms["site"]) == 3


def test_the_role_name_still_wins_over_the_general_one():
    from fastmdxplora.simulation.metadynamics import (
        with_general_selection_names)

    resolved = with_general_selection_names(
        {"site_selection": "resid 1", "select_atoms": "resid 2"},
        "ligand_distance")

    assert resolved["site_selection"] == "resid 1"


def test_two_groups_cannot_be_named_by_one_word():
    """A distance is between two things, and `select_atoms` names one.

    Picking a group and hoping would be the failure this package exists to
    avoid: a run that completes, produces a number, and measured something
    other than what was asked for.
    """
    from fastmdxplora.simulation.metadynamics import (
        with_general_selection_names)

    with pytest.raises(ValueError, match="does not say which is which"):
        with_general_selection_names(
            {"select_atoms": "resname BNZ"}, "distance")


def test_the_suffixed_form_names_them_both():
    from fastmdxplora.simulation.metadynamics import plan_from_config

    plan = plan_from_config(
        {"collective_variable": "distance",
         "select_atoms_a": "resname BNZ",
         "select_atoms_b": "resid 1 to 3 and name CA",
         "sigma": 0.05, "unbounded": True},
        _topology())

    assert len(plan.atoms["selection_a"]) == 6
    assert len(plan.atoms["selection_b"]) == 3


def test_a_variable_taking_no_selection_says_so():
    from fastmdxplora.simulation.metadynamics import (
        with_general_selection_names)

    with pytest.raises(ValueError, match="nothing to name here"):
        with_general_selection_names(
            {"select_atoms": "resname BNZ"}, "ligand_rmsd")


def test_a_study_written_with_the_general_word_validates(tmp_path):
    """End to end, because a reader that accepts it is only half the fix."""
    from fastmdxplora.config import load_config_file, validate_config

    path = tmp_path / "general.yml"
    path.write_text(
        STUDY.replace("site_selection:", "select_atoms:"), encoding="utf-8")

    data = load_config_file(path)
    validate_config(data)

    assert len(data["systems"]) == 30
    assert data["systems"][0]["simulation"]["umbrella"]["select_atoms"] == \
        "resid 189 to 195 and name CA"


def test_the_analysis_phase_takes_the_general_word_too(tmp_path):
    """One word for a selection, whichever phase is asking.

    The variable blocks take `select_atoms`; an analysis taking a different
    word for the same string puts the general name back where it started,
    which is a thing to look up.
    """
    from fastmdxplora.config import load_config_file, validate_config

    path = tmp_path / "analysis.yml"
    path.write_text(
        "output: runs/x\n"
        "systems:\n  - id: one\n    system: ./a.pdb\n"
        "analysis:\n"
        "  select_atoms: \"name CA\"\n"
        "  include: [rmsd]\n", encoding="utf-8")

    data = load_config_file(path)
    validate_config(data)

    assert data["analysis"]["select_atoms"] == "name CA"


def test_the_earlier_analysis_word_still_works(tmp_path):
    from fastmdxplora.config import load_config_file, validate_config

    path = tmp_path / "analysis.yml"
    path.write_text(
        "output: runs/x\n"
        "systems:\n  - id: one\n    system: ./a.pdb\n"
        "analysis:\n"
        "  selection: \"name CA\"\n"
        "  include: [rmsd]\n", encoding="utf-8")

    data = load_config_file(path)
    validate_config(data)

    assert data["analysis"]["selection"] == "name CA"
