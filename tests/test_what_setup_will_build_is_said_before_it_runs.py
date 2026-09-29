"""What setup will build is said before it runs, and it is what setup builds.

The builder learned the box, the particle count and so the time from setup's
log, minutes into a run. `fastmdxplora.setup.estimate` works them out from
the structure and the settings. It is checked here against setup itself: the
same structures prepared by the setup phase, whose record is the answer.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from fastmdxplora.setup.estimate import (
    RESIDUE_ATOMS,
    VOLUME_PER_WIDTH_CUBED,
    WATER_PER_NM3,
    estimate_system,
)


def _file(root: Path, text: str, name: str = "structure.pdb") -> Path:
    path = root / name
    path.write_text(text, encoding="utf-8")
    return path


def _prepared(root: Path, text: str, **setup) -> dict:
    """The setup phase's own record of the system it built."""
    pytest.importorskip("openmm")
    pytest.importorskip("pdbfixer")
    from fastmdxplora import FastMDXplora

    structure = _file(root, text)
    FastMDXplora(config_data={
        "systems": [{"id": "p", "system": str(structure)}],
        "include_phase": ["setup"], "setup": dict(setup)},
        output_dir=str(root / "out")).explore(check=True)
    return json.loads((root / "out" / "setup" / "setup_parameters.json")
                      .read_text(encoding="utf-8"))


class TestAgainstSetup:
    """Setup's record is the answer, and the estimate is held to it."""

    def test_a_peptide_the_padding_boxes(self, tmp_path) -> None:
        record = _prepared(tmp_path, DECAPEPTIDE, heterogens="drop")
        guess = estimate_system(tmp_path / "structure.pdb", {"heterogens": "drop"})
        assert not guess.grows and "solvent_padding_nm" not in record["resolved"]
        assert guess.narrowest_nm == pytest.approx(record["box"]["smallest_width_nm"], abs=0.1)
        assert guess.particles == pytest.approx(record["n_atoms_solvated"], rel=0.04)

    def test_a_small_solute_whose_box_setup_grows(self, tmp_path) -> None:
        record = _prepared(tmp_path, TRIPEPTIDE, heterogens="drop")
        guess = estimate_system(tmp_path / "structure.pdb", {"heterogens": "drop"})
        assert guess.grows
        assert guess.padding_used_nm == pytest.approx(record["resolved"]["solvent_padding_nm"],
                                                      abs=1e-3)
        assert guess.narrowest_nm == pytest.approx(record["box"]["smallest_width_nm"], abs=1e-3)
        assert guess.particles == pytest.approx(record["n_atoms_solvated"], rel=0.10)


class TestTheWaterLostAtTheFaces:

    def test_it_is_what_openmm_loses_from_a_box_of_water(self) -> None:
        """A single ion in a dodecahedron of water, 3 and 6 nm across."""
        pytest.importorskip("openmm")
        import numpy as np
        from openmm import Vec3, app, unit

        from fastmdxplora.setup.estimate import WATERS_LOST_AT_THE_FACES

        forcefield = app.ForceField("amber14-all.xml", "amber14/tip3p.xml")
        for width in (3.0, 6.0):
            topology = app.Topology()
            residue = topology.addResidue("NA", topology.addChain())
            topology.addAtom("Na", app.element.sodium, residue)
            modeller = app.Modeller(topology, [Vec3(0, 0, 0)] * unit.nanometer)
            modeller.addSolvent(forcefield, padding=width / 2 * unit.nanometer,
                                boxShape="dodecahedron", neutralize=False)
            volume = abs(np.linalg.det(np.array(
                modeller.topology.getPeriodicBoxVectors().value_in_unit(unit.nanometer))))
            waters = sum(1 for r in modeller.topology.residues() if r.name == "HOH")
            lost = WATER_PER_NM3 * volume - waters
            assert lost == pytest.approx(WATERS_LOST_AT_THE_FACES * volume ** (2 / 3), rel=0.35)


class TestAgainstOpenMM:
    """The solute and the ions exactly, as OpenMM's own templates and its ion
    rule give them."""

    def test_the_solute_and_its_ions(self, tmp_path) -> None:
        pytest.importorskip("pdbfixer")
        from openmm import app, unit
        from pdbfixer import PDBFixer

        structure = _file(tmp_path, DECAPEPTIDE)
        fixer = PDBFixer(filename=str(structure))
        fixer.findMissingResidues()
        fixer.findMissingAtoms()
        fixer.addMissingAtoms()
        fixer.addMissingHydrogens(7.4)
        modeller = app.Modeller(fixer.topology, fixer.positions)
        solute = modeller.topology.getNumAtoms()
        forcefield = app.ForceField("amber14-all.xml", "amber14/tip3p.xml")
        modeller.addSolvent(forcefield, padding=1.0 * unit.nanometer,
                            boxShape="dodecahedron", ionicStrength=0.15 * unit.molar)
        names = [r.name for r in modeller.topology.residues()]

        guess = estimate_system(structure, {})
        assert guess.solute_atoms == solute
        assert (guess.ions_positive, guess.ions_negative) == (names.count("NA"), names.count("CL"))

    def test_the_water_density_is_openmm_s(self) -> None:
        pytest.importorskip("openmm")
        from openmm import app, unit

        box = app.PDBFile(str(Path(app.__file__).parent / "data" / "tip3p.pdb"))
        vectors = box.topology.getPeriodicBoxVectors().value_in_unit(unit.nanometer)
        volume = math.prod(vectors[i][i] for i in range(3))
        assert WATER_PER_NM3 == pytest.approx(box.topology.getNumResidues() / volume)

    @pytest.mark.parametrize("shape", sorted(VOLUME_PER_WIDTH_CUBED))
    def test_the_volume_of_each_shape(self, shape) -> None:
        pytest.importorskip("openmm")
        from openmm import app
        import numpy as np

        modeller = app.Modeller(app.Topology(), [])
        if not hasattr(modeller, "_computeBoxVectors"):
            pytest.skip("this OpenMM builds box vectors elsewhere")
        vectors = modeller._computeBoxVectors(2.0, shape)
        matrix = np.array([[float(c) for c in vector] for vector in vectors])
        assert abs(np.linalg.det(matrix)) == pytest.approx(VOLUME_PER_WIDTH_CUBED[shape] * 8.0)


ATOM = "ATOM  {:5d}  {:<3s} {:>3s} {}{:4d}    {:8.3f}{:8.3f}{:8.3f}  1.00  0.00          {:>2s}\n"


def _residue(serial: int, resname: str, chain: str, number: int, x: float,
             names=("N", "CA", "C", "O"), record: str = "ATOM  ") -> str:
    return "".join(
        ATOM.format(serial + k, name, resname, chain, number, x + 0.3 * k, 0.0, 0.0,
                    name[0]).replace("ATOM  ", record, 1)
        for k, name in enumerate(names))


class TestTheSolute:

    def test_each_residue_by_its_template_with_its_termini(self, tmp_path) -> None:
        text = (_residue(1, "LYS", "A", 1, 0.0) + _residue(5, "GLY", "A", 2, 4.0)
                + _residue(9, "ASP", "A", 3, 8.0) + "END\n")
        guess = estimate_system(_file(tmp_path, text), {"nonbonded_method": "NoCutoff"})
        assert guess.solute_atoms == RESIDUE_ATOMS["LYS"] + RESIDUE_ATOMS["GLY"] \
            + RESIDUE_ATOMS["ASP"] + 2 + 1
        assert guess.net_charge == 0

    def test_a_gap_is_built_and_a_missing_end_is_not(self, tmp_path) -> None:
        missing = ("REMARK 465   M RES C SSSEQI\n"
                   "REMARK 465     MET A     1\n"
                   "REMARK 465     ALA A     3\n")
        text = missing + _residue(1, "GLY", "A", 2, 0.0) + _residue(5, "GLY", "A", 4, 8.0) + "END\n"
        guess = estimate_system(_file(tmp_path, text), {"nonbonded_method": "NoCutoff"})
        assert guess.gaps_built == 1
        assert guess.solute_atoms == 2 * RESIDUE_ATOMS["GLY"] + RESIDUE_ATOMS["ALA"] + 3

    def test_bonded_cysteines_lose_a_hydrogen_each(self, tmp_path) -> None:
        names = ("N", "CA", "C", "O", "CB", "SG")
        text = (_residue(1, "CYS", "A", 1, 0.0, names) + _residue(7, "CYS", "B", 1, 0.2, names)
                + "END\n")
        guess = estimate_system(_file(tmp_path, text), {"nonbonded_method": "NoCutoff"})
        assert guess.solute_atoms == 2 * (RESIDUE_ATOMS["CYX"] + 3)

    def test_a_modified_residue_is_counted_as_what_replaces_it(self, tmp_path) -> None:
        text = _residue(1, "MSE", "A", 1, 0.0, record="HETATM") + "END\n"
        guess = estimate_system(_file(tmp_path, text), {"nonbonded_method": "NoCutoff"})
        assert guess.solute_atoms == RESIDUE_ATOMS["MET"] + 3

    def test_a_ligand_is_kept_or_dropped_as_heterogens_says(self, tmp_path) -> None:
        ligand = _residue(5, "BEN", "A", 301, 3.0, ("C1", "C2", "C3", "N1"), "HETATM")
        text = _residue(1, "GLY", "A", 1, 0.0) + ligand + "END\n"
        path = _file(tmp_path, text)
        kept = estimate_system(path, {"nonbonded_method": "NoCutoff"})
        dropped = estimate_system(path, {"nonbonded_method": "NoCutoff", "heterogens": "drop"})
        assert kept.ligands == ["BEN"] and dropped.ligands == []
        assert kept.solute_atoms - dropped.solute_atoms == 8, "four heavy atoms and theirs"

    def test_a_metal_brings_its_charge(self, tmp_path) -> None:
        text = (_residue(1, "GLY", "A", 1, 0.0)
                + _residue(5, "ZN", "A", 401, 3.0, ("ZN",), "HETATM") + "END\n")
        guess = estimate_system(_file(tmp_path, text), {"nonbonded_method": "NoCutoff"})
        assert guess.net_charge == 2 and guess.ions_negative >= 2

    def test_crystal_water_only_when_kept(self, tmp_path) -> None:
        text = (_residue(1, "GLY", "A", 1, 0.0)
                + _residue(5, "HOH", "A", 501, 3.0, ("O",), "HETATM") + "END\n")
        path = _file(tmp_path, text)
        assert estimate_system(path, {"keep_water": True}).solute_atoms == \
            estimate_system(path, {}).solute_atoms + 3


    def test_a_strand_of_dna_by_its_templates_and_ends(self, tmp_path) -> None:
        """A 5' end has no phosphate and a 3' end one more hydrogen: DA5,
        DC and DG3 in OpenMM's AMBER templates, 30 + 30 + 34 atoms."""
        names = ("P", "OP1", "OP2", "O5'", "C5'")
        text = (_residue(1, " DA", "A", 1, 0.0, names) + _residue(6, " DC", "A", 2, 6.0, names)
                + _residue(11, " DG", "A", 3, 12.0, names) + "END\n")
        guess = estimate_system(_file(tmp_path, text), {"nonbonded_method": "NoCutoff"})
        assert guess.solute_atoms == 30 + 30 + 34
        assert guess.net_charge == -2

    def test_the_templates_are_openmm_s(self) -> None:
        app = pytest.importorskip("openmm.app")
        forcefield = app.ForceField("amber14-all.xml", "amber14/tip3p.xml")
        for name, atoms in RESIDUE_ATOMS.items():
            template = forcefield._templates.get({"NMA": "NME"}.get(name, name))
            if template is not None:
                assert len(template.atoms) == atoms, name

    def test_a_stated_ligand_charge_is_counted(self, tmp_path) -> None:
        ligand = _residue(5, "LIG", "A", 301, 3.0, ("C1", "N1"), "HETATM")
        path = _file(tmp_path, _residue(1, "GLY", "A", 1, 0.0) + ligand + "END\n")
        assert estimate_system(path, {"ligand_net_charge": 1}).net_charge == 1
        assert estimate_system(path, {"keep_heterogens": True}).ligands == ["LIG"]

    def test_what_setup_discards_by_name_is_left_out(self, tmp_path) -> None:
        """Glycerol and the salt of the crystallization liquor are discarded
        under `auto`; a zinc in a site is kept, and `keep` keeps them all."""
        text = (_residue(1, "GLY", "A", 1, 0.0)
                + _residue(5, "GOL", "A", 501, 3.0, ("C1", "O1", "C2"), "HETATM")
                + _residue(8, "NA", "A", 502, 6.0, ("NA",), "HETATM")
                + _residue(9, "ZN", "A", 503, 9.0, ("ZN",), "HETATM") + "END\n")
        path = _file(tmp_path, text)
        auto = estimate_system(path, {"nonbonded_method": "NoCutoff"})
        kept = estimate_system(path, {"nonbonded_method": "NoCutoff", "heterogens": "keep"})
        assert auto.ligands == [] and auto.net_charge == 2
        assert kept.ligands == ["GOL"] and kept.net_charge == 3
        assert kept.solute_atoms - auto.solute_atoms == 6 + 1

    def test_a_metal_goes_with_the_heterogens_dropped(self, tmp_path) -> None:
        text = (_residue(1, "GLY", "A", 1, 0.0)
                + _residue(5, "ZN", "A", 401, 3.0, ("ZN",), "HETATM") + "END\n")
        assert estimate_system(_file(tmp_path, text), {"heterogens": "drop"}).net_charge == 0

    def test_a_line_that_is_not_a_record_is_passed_over(self, tmp_path) -> None:
        broken = "ATOM      9  CA  GLY A   x       0.000   0.000   0.000  1.00  0.00           C\n"
        text = _residue(1, "GLY", "A", 1, 0.0) + broken + "END\n"
        assert estimate_system(_file(tmp_path, text), {}).residues == 1

    def test_a_missing_residue_of_another_chain_or_kind_is_not_built(self, tmp_path) -> None:
        missing = ("REMARK 465     GLY B     2\n"
                   "REMARK 465     XYZ A     2\n")
        text = missing + _residue(1, "GLY", "A", 1, 0.0) + _residue(5, "GLY", "A", 3, 8.0) + "END\n"
        assert estimate_system(_file(tmp_path, text), {}).gaps_built == 0


class TestTheAssembly:

    OPERATORS = (
        "REMARK 350 BIOMOLECULE: 1\n"
        "REMARK 350 AUTHOR DETERMINED BIOLOGICAL UNIT: DIMERIC\n"
        "REMARK 350 APPLY THE FOLLOWING TO CHAINS: A\n"
        "REMARK 350   BIOMT1   1  1.000000  0.000000  0.000000        0.00000\n"
        "REMARK 350   BIOMT2   1  0.000000  1.000000  0.000000        0.00000\n"
        "REMARK 350   BIOMT3   1  0.000000  0.000000  1.000000        0.00000\n"
        "REMARK 350   BIOMT1   2  1.000000  0.000000  0.000000       40.00000\n"
        "REMARK 350   BIOMT2   2  0.000000  1.000000  0.000000        0.00000\n"
        "REMARK 350   BIOMT3   2  0.000000  0.000000  1.000000        0.00000\n"
    )

    def test_the_copies_its_operators_make_are_counted_and_boxed(self, tmp_path) -> None:
        text = self.OPERATORS + _residue(1, "GLY", "A", 1, 0.0) + "END\n"
        alone = estimate_system(_file(tmp_path, _residue(1, "GLY", "A", 1, 0.0) + "END\n",
                                      "one.pdb"), {"nonbonded_method": "NoCutoff"})
        both = estimate_system(_file(tmp_path, text), {"nonbonded_method": "NoCutoff"})
        assert both.solute_atoms == 2 * alone.solute_atoms
        assert both.narrowest_nm > alone.narrowest_nm + 2.0, "the copy sits 4 nm away"
        assert any("2 copies" in note for note in both.notes)

    def test_named_chains_are_what_is_kept(self, tmp_path) -> None:
        text = self.OPERATORS + _residue(1, "GLY", "A", 1, 0.0) + _residue(5, "GLY", "B", 1, 20.0) \
            + "END\n"
        guess = estimate_system(_file(tmp_path, text), {"chains": ["B"],
                                                         "nonbonded_method": "NoCutoff"})
        assert guess.chains == ["B"] and guess.residues == 1

    def test_several_assemblies_are_said_to_be_a_choice(self, tmp_path) -> None:
        second = self.OPERATORS.replace("BIOMOLECULE: 1", "BIOMOLECULE: 2").replace(
            "CHAINS: A", "CHAINS: B")
        text = self.OPERATORS + second + _residue(1, "GLY", "A", 1, 0.0) \
            + _residue(5, "GLY", "B", 1, 20.0) + "END\n"
        guess = estimate_system(_file(tmp_path, text), {"nonbonded_method": "NoCutoff"})
        assert guess.chains == ["A"]
        assert any("2 biological assemblies" in note for note in guess.notes)


class TestTheBox:

    def test_the_water_fills_the_box_the_padding_makes(self, tmp_path) -> None:
        path = _file(tmp_path, _residue(1, "GLY", "A", 1, 0.0) + "END\n")
        cube = estimate_system(path, {"box_shape": "cube", "solvent_padding_nm": 2.0,
                                      "ion_concentration_M": 0, "neutralize": False})
        assert cube.narrowest_nm == pytest.approx(4.0) and cube.width_nm == pytest.approx(4.0)
        assert cube.volume_nm3 == pytest.approx(64.0)
        from fastmdxplora.setup.estimate import WATERS_LOST_AT_THE_FACES

        # The box's water, less what the faces of the cell and a glycine take.
        full = WATER_PER_NM3 * 64.0 - WATERS_LOST_AT_THE_FACES * 16.0
        assert full - 20 < cube.waters < full
        assert cube.particles == cube.solute_atoms + 3 * cube.waters

    def test_a_dodecahedron_is_wider_face_to_face_than_its_narrowest(self, tmp_path) -> None:
        """The cutoff is held to the narrowest width of OpenMM's triclinic
        cell; a molecule is as far from its own image as the faces are
        apart, which is that over the square root of a half."""
        path = _file(tmp_path, _residue(1, "GLY", "A", 1, 0.0) + "END\n")
        guess = estimate_system(path, {"solvent_padding_nm": 2.0})
        assert guess.width_nm == pytest.approx(guess.narrowest_nm / math.sqrt(0.5))

    def test_a_four_site_water_is_four_particles(self, tmp_path) -> None:
        path = _file(tmp_path, _residue(1, "GLY", "A", 1, 0.0) + "END\n")
        three = estimate_system(path, {})
        four = estimate_system(path, {"water_model": "tip4pew"})
        assert four.particles - three.particles == three.waters

    def test_a_padding_setup_would_refuse_is_said(self, tmp_path) -> None:
        # A solute 3 nm long at 0.4 nm of padding with a 1.5 nm cutoff: the
        # solute sizes the box, and the growth needed is past what setup adds.
        long = "".join(_residue(1 + 4 * i, "GLY", "A", i + 1, 3.0 * i) for i in range(10)) + "END\n"
        guess = estimate_system(_file(tmp_path, long), {
            "solvent_padding_nm": 0.4, "nonbonded_cutoff_nm": 1.5, "box_shape": "dodecahedron"})
        assert guess.refuses and not guess.grows

    def test_without_a_periodic_cutoff_nothing_grows(self, tmp_path) -> None:
        path = _file(tmp_path, _residue(1, "GLY", "A", 1, 0.0) + "END\n")
        assert not estimate_system(path, {"nonbonded_method": "NoCutoff"}).grows
        assert estimate_system(path, {}).grows

    def test_the_cutoff_is_the_force_field_s_own(self, tmp_path) -> None:
        path = _file(tmp_path, _residue(1, "GLY", "A", 1, 0.0) + "END\n")
        charmm = estimate_system(path, {"forcefield": "charmm36"})
        amber = estimate_system(path, {"forcefield": "amber14"})
        assert charmm.narrowest_nm == pytest.approx(amber.narrowest_nm * 1.2, rel=1e-6)
        assert estimate_system(path, {"force_field": ["x.xml"]}).narrowest_nm == \
            pytest.approx(amber.narrowest_nm)
        assert estimate_system(path, {"forcefield": "nonsense"}).narrowest_nm == \
            pytest.approx(amber.narrowest_nm)


class TestWhatItCannotSay:

    def test_a_membrane_is_the_bilayer_s(self, tmp_path) -> None:
        path = _file(tmp_path, _residue(1, "GLY", "A", 1, 0.0) + "END\n")
        with pytest.raises(ValueError, match="bilayer"):
            estimate_system(path, {"membrane": "POPC"})

    def test_a_file_with_no_coordinates(self, tmp_path) -> None:
        with pytest.raises(ValueError, match="no coordinates"):
            estimate_system(_file(tmp_path, "HEADER nothing\nEND\n"), {})

    def test_the_first_model_and_the_first_location_only(self, tmp_path) -> None:
        one = _residue(1, "GLY", "A", 1, 0.0)
        alternate = one.replace("  GLY", " BGLY").replace(" GLY A", "BGLY A")
        text = "MODEL        1\n" + one + alternate + "ENDMDL\nMODEL        2\n" + one + "ENDMDL\n"
        guess = estimate_system(_file(tmp_path, text), {"nonbonded_method": "NoCutoff"})
        assert guess.residues == 1


#: Residues 20 to 22 and 1 to 10 of adenylate kinase (1AKE chain A), as
#: deposited: heavy atoms only.
TRIPEPTIDE = (
    "ATOM    135  N   ILE A  20      18.869  54.909  30.422  1.00 23.09           N\n"
    "ATOM    136  CA  ILE A  20      19.532  54.877  31.696  1.00 23.96           C\n"
    "ATOM    137  C   ILE A  20      20.645  55.887  31.681  1.00 29.63           C\n"
    "ATOM    138  O   ILE A  20      20.846  56.617  32.663  1.00 32.83           O\n"
    "ATOM    139  CB  ILE A  20      20.070  53.459  31.980  1.00 27.73           C\n"
    "ATOM    140  CG1 ILE A  20      18.829  52.589  32.155  1.00 24.67           C\n"
    "ATOM    141  CG2 ILE A  20      20.994  53.376  33.235  1.00 26.89           C\n"
    "ATOM    142  CD1 ILE A  20      19.248  51.106  32.066  1.00 25.96           C\n"
    "ATOM    143  N   MET A  21      21.352  55.962  30.577  1.00 22.68           N\n"
    "ATOM    144  CA  MET A  21      22.441  56.903  30.479  1.00 32.34           C\n"
    "ATOM    145  C   MET A  21      21.963  58.344  30.645  1.00 36.61           C\n"
    "ATOM    146  O   MET A  21      22.537  59.094  31.451  1.00 35.98           O\n"
    "ATOM    147  CB  MET A  21      23.126  56.713  29.136  1.00 34.45           C\n"
    "ATOM    148  CG  MET A  21      23.987  57.889  28.770  1.00 47.17           C\n"
    "ATOM    149  SD  MET A  21      24.666  57.634  27.140  1.00 58.05           S\n"
    "ATOM    150  CE  MET A  21      26.316  57.426  27.691  1.00 46.92           C\n"
    "ATOM    151  N   GLU A  22      20.892  58.740  29.957  1.00 35.15           N\n"
    "ATOM    152  CA  GLU A  22      20.467  60.117  30.020  1.00 35.34           C\n"
    "ATOM    153  C   GLU A  22      19.853  60.448  31.354  1.00 41.37           C\n"
    "ATOM    154  O   GLU A  22      20.085  61.541  31.858  1.00 49.63           O\n"
    "ATOM    155  CB  GLU A  22      19.479  60.433  28.914  1.00 42.27           C\n"
    "ATOM    156  CG  GLU A  22      18.092  59.796  28.979  1.00 68.08           C\n"
    "ATOM    157  CD  GLU A  22      17.103  60.339  27.946  1.00 82.33           C\n"
    "ATOM    158  OE1 GLU A  22      17.363  60.191  26.740  1.00 86.19           O\n"
    "ATOM    159  OE2 GLU A  22      16.077  60.905  28.360  1.00 89.10           O\n"
    "END\n"
)

DECAPEPTIDE = (
    "ATOM      1  N   MET A   1      26.981  53.977  40.085  1.00 40.83           N\n"
    "ATOM      2  CA  MET A   1      26.091  52.849  39.889  1.00 37.14           C\n"
    "ATOM      3  C   MET A   1      26.679  52.163  38.675  1.00 30.15           C\n"
    "ATOM      4  O   MET A   1      27.020  52.865  37.715  1.00 27.59           O\n"
    "ATOM      5  CB  MET A   1      24.677  53.310  39.580  1.00 38.06           C\n"
    "ATOM      6  CG  MET A   1      23.624  52.189  39.442  1.00 46.67           C\n"
    "ATOM      7  SD  MET A   1      21.917  52.816  39.301  1.00 61.54           S\n"
    "ATOM      8  CE  MET A   1      21.930  53.926  37.910  1.00 51.17           C\n"
    "ATOM      9  N   ARG A   2      26.861  50.841  38.803  1.00 28.23           N\n"
    "ATOM     10  CA  ARG A   2      27.437  49.969  37.786  1.00 25.76           C\n"
    "ATOM     11  C   ARG A   2      26.336  48.959  37.429  1.00 25.85           C\n"
    "ATOM     12  O   ARG A   2      25.745  48.313  38.312  1.00 25.74           O\n"
    "ATOM     13  CB  ARG A   2      28.653  49.266  38.349  1.00 21.92           C\n"
    "ATOM     14  CG  ARG A   2      29.870  50.188  38.416  1.00 39.05           C\n"
    "ATOM     15  CD  ARG A   2      31.033  49.532  39.173  1.00 51.33           C\n"
    "ATOM     16  NE  ARG A   2      32.318  50.244  39.125  1.00 59.73           N\n"
    "ATOM     17  CZ  ARG A   2      33.462  49.750  39.679  1.00 58.78           C\n"
    "ATOM     18  NH1 ARG A   2      33.522  48.572  40.308  1.00 58.56           N\n"
    "ATOM     19  NH2 ARG A   2      34.610  50.427  39.597  1.00 59.20           N\n"
    "ATOM     20  N   ILE A   3      26.039  48.836  36.139  1.00 21.59           N\n"
    "ATOM     21  CA  ILE A   3      24.961  47.988  35.671  1.00 23.90           C\n"
    "ATOM     22  C   ILE A   3      25.374  47.080  34.537  1.00 21.12           C\n"
    "ATOM     23  O   ILE A   3      26.029  47.614  33.642  1.00 24.59           O\n"
    "ATOM     24  CB  ILE A   3      23.802  48.880  35.202  1.00 23.84           C\n"
    "ATOM     25  CG1 ILE A   3      23.317  49.724  36.378  1.00 26.14           C\n"
    "ATOM     26  CG2 ILE A   3      22.660  48.010  34.642  1.00 19.29           C\n"
    "ATOM     27  CD1 ILE A   3      22.436  50.890  35.992  1.00 24.97           C\n"
    "ATOM     28  N   ILE A   4      25.062  45.774  34.541  1.00 20.44           N\n"
    "ATOM     29  CA  ILE A   4      25.194  44.925  33.360  1.00 17.83           C\n"
    "ATOM     30  C   ILE A   4      23.804  44.715  32.751  1.00 18.42           C\n"
    "ATOM     31  O   ILE A   4      22.824  44.536  33.484  1.00 16.35           O\n"
    "ATOM     32  CB  ILE A   4      25.789  43.561  33.720  1.00 16.64           C\n"
    "ATOM     33  CG1 ILE A   4      27.206  43.753  34.233  1.00 17.27           C\n"
    "ATOM     34  CG2 ILE A   4      25.829  42.650  32.463  1.00 21.39           C\n"
    "ATOM     35  CD1 ILE A   4      27.967  42.486  34.621  1.00 15.61           C\n"
    "ATOM     36  N   LEU A   5      23.655  44.874  31.424  1.00 19.74           N\n"
    "ATOM     37  CA  LEU A   5      22.428  44.503  30.712  1.00 19.86           C\n"
    "ATOM     38  C   LEU A   5      22.668  43.134  30.012  1.00 18.50           C\n"
    "ATOM     39  O   LEU A   5      23.614  42.932  29.232  1.00 17.64           O\n"
    "ATOM     40  CB  LEU A   5      22.088  45.547  29.675  1.00 22.23           C\n"
    "ATOM     41  CG  LEU A   5      22.076  47.021  30.069  1.00 27.37           C\n"
    "ATOM     42  CD1 LEU A   5      21.735  47.848  28.817  1.00 19.28           C\n"
    "ATOM     43  CD2 LEU A   5      21.088  47.249  31.193  1.00 24.82           C\n"
    "ATOM     44  N   LEU A   6      21.787  42.178  30.248  1.00 14.28           N\n"
    "ATOM     45  CA  LEU A   6      21.933  40.811  29.752  1.00 21.75           C\n"
    "ATOM     46  C   LEU A   6      20.711  40.529  28.870  1.00 23.97           C\n"
    "ATOM     47  O   LEU A   6      19.602  40.977  29.220  1.00 19.19           O\n"
    "ATOM     48  CB  LEU A   6      21.919  39.891  30.945  1.00 27.49           C\n"
    "ATOM     49  CG  LEU A   6      22.847  38.789  31.103  1.00 31.51           C\n"
    "ATOM     50  CD1 LEU A   6      24.254  39.345  31.210  1.00 34.32           C\n"
    "ATOM     51  CD2 LEU A   6      22.465  38.042  32.355  1.00 24.54           C\n"
    "ATOM     52  N   GLY A   7      20.800  39.799  27.764  1.00 18.09           N\n"
    "ATOM     53  CA  GLY A   7      19.604  39.512  26.973  1.00 20.21           C\n"
    "ATOM     54  C   GLY A   7      19.959  39.035  25.585  1.00 14.48           C\n"
    "ATOM     55  O   GLY A   7      21.049  39.324  25.122  1.00 15.84           O\n"
    "ATOM     56  N   ALA A   8      19.117  38.232  24.936  1.00 19.71           N\n"
    "ATOM     57  CA  ALA A   8      19.324  37.742  23.567  1.00 16.92           C\n"
    "ATOM     58  C   ALA A   8      19.530  38.886  22.568  1.00 17.35           C\n"
    "ATOM     59  O   ALA A   8      19.158  40.013  22.889  1.00 15.41           O\n"
    "ATOM     60  CB  ALA A   8      18.092  36.960  23.169  1.00 15.12           C\n"
    "ATOM     61  N   PRO A   9      20.069  38.660  21.359  1.00 20.54           N\n"
    "ATOM     62  CA  PRO A   9      20.108  39.651  20.294  1.00 17.47           C\n"
    "ATOM     63  C   PRO A   9      18.750  40.300  20.095  1.00 21.25           C\n"
    "ATOM     64  O   PRO A   9      17.760  39.552  20.084  1.00 17.47           O\n"
    "ATOM     65  CB  PRO A   9      20.574  38.908  19.047  1.00 19.04           C\n"
    "ATOM     66  CG  PRO A   9      20.687  37.425  19.450  1.00 25.54           C\n"
    "ATOM     67  CD  PRO A   9      20.695  37.403  20.958  1.00 17.37           C\n"
    "ATOM     68  N   GLY A  10      18.697  41.656  20.019  1.00 21.90           N\n"
    "ATOM     69  CA  GLY A  10      17.487  42.426  19.756  1.00 18.35           C\n"
    "ATOM     70  C   GLY A  10      16.476  42.449  20.905  1.00 19.36           C\n"
    "ATOM     71  O   GLY A  10      15.311  42.745  20.642  1.00 18.88           O\n"
    "END\n"
)
