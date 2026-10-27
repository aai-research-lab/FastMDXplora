"""What a campaign of unusual proteins on a GPU found, each said before it runs.

Run on aailab01 (RTX 4090, CUDA) from configs written to reach the edges:

* `water_model: tip4pew` beside a named force field built the force field
  from `amber14/tip3p.xml` and a box of four-site water, and setup stopped
  on "The force field has no parameters for HOH". A three-site `spce` box
  would have run with TIP3P's parameters under the name SPC/E.
* 1STP's biotin is deposited on chain A only; the assembly's operators
  build B, C and D. Its chemistry was asked of RCSB at B300, which the entry
  does not hold, and the empty answer was refused as "a different component".
* 1JBL (SFTI-1) is a cyclic peptide, Gly1 linked to Asp14. It was prepared,
  simulated and reported as a linear peptide with two charged ends, and
  nothing said the ring was gone.
"""

from __future__ import annotations

import gzip
from pathlib import Path
from types import SimpleNamespace

import pytest

from fastmdxplora.config.loader import ConfigError, validate_config
from fastmdxplora.refusals import StudyError, refusal_of
from fastmdxplora.setup import ccd, pipeline

DEPOSITED = Path(__file__).parent / "data" / "assemblies"


# -- The water a named force field brings ---------------------------------

@pytest.mark.parametrize("setup", [
    {"forcefield": "amber14", "water_model": "tip4pew"},
    {"water_model": "spce"},
    {"forcefield": "charmm36", "water_model": "tip5p"},
])
def test_another_water_beside_a_named_force_field_is_refused(setup) -> None:
    with pytest.raises(ConfigError, match="brings its own water") as caught:
        validate_config({"systems": [{"system": "1L2Y"}], "setup": setup})
    assert caught.value.refusal.code == "config.option.conflicting"


@pytest.mark.parametrize("setup", [
    {"water_model": "tip3p"},
    {"forcefield": "amber-fb15", "water_model": "tip3p"},
    {"forcefield": "charmm36", "water_model": "tip3p"},
    {"force_field": ["amber14-all.xml", "amber14/tip4pew.xml"], "water_model": "tip4pew"},
])
def test_the_force_field_s_own_water_or_files_given_run(setup) -> None:
    validate_config({"systems": [{"system": "1L2Y"}], "setup": setup})


def test_a_system_s_own_water_names_its_run() -> None:
    with pytest.raises(ConfigError, match="for run 's2'"):
        validate_config({"systems": [{"system": "1L2Y"},
                                     {"system": "1UBQ", "setup": {"water_model": "opc"}}]})


def test_the_refusal_gives_the_files_that_pair_them() -> None:
    with pytest.raises(ConfigError, match=r"charmm36\.xml, charmm36/tip4pew\.xml"):
        validate_config({"systems": [{"system": "1L2Y"}],
                         "setup": {"forcefield": "charmm36", "water_model": "tip4pew"}})


# -- A ligand on a chain the operators built ------------------------------

def test_a_built_copy_s_ligand_is_asked_for_where_it_was_deposited(tmp_path, monkeypatch) -> None:
    path = tmp_path / "input.pdb"
    path.write_bytes(gzip.decompress((DEPOSITED / "1STP.pdb.gz").read_bytes()))
    params: dict = {"heterogens": "auto", "ph": 7.4}
    built = pipeline._the_chains_to_simulate(SimpleNamespace(_assembly=None), path,
                                             "pdb_id", params)
    assert params["_copied_from"] == {"A": "A", "B": "A", "C": "A", "D": "A"}
    # Chain A's biotin taken out, so the first copy asked for is a built one.
    lines = [line for line in built.read_text().splitlines()
             if not (line.startswith("HETATM") and line[17:20] == "BTN" and line[21] == "A")]
    built.write_text("\n".join(lines) + "\n")

    asked: list[tuple[str, int]] = []

    class Asked(Exception):
        pass

    def fetch(entry, resname, *, chain, resseq, **_):
        asked.append((chain, resseq))
        raise Asked

    monkeypatch.setattr(ccd, "fetch_chemistry", fetch)
    monkeypatch.setattr(pipeline, "_refuse_without_a_charge_provider", lambda params: None)
    with pytest.raises(Asked):
        pipeline._auto_ligands(params, built, tmp_path, "1stp")
    assert asked == [("A", 300)]


# -- A backbone closed into a ring ----------------------------------------

_SFTI = """\
LINK         N   GLY A   1                 C   ASP A  14     1555   1555  1.34
ATOM      1  N   GLY A   1       0.000   0.000   0.000  1.00  0.00           N
ATOM      2  CA  GLY A   1       1.450   0.000   0.000  1.00  0.00           C
ATOM      3  N   ARG A   2       2.500   1.000   0.000  1.00  0.00           N
ATOM      4  N   ASP A  14      -1.000   1.000   0.000  1.00  0.00           N
ATOM      5  C   ASP A  14      -1.300   0.000   0.000  1.00  0.00           C
TER
END
"""


def test_a_cyclic_peptide_is_refused_rather_than_opened(tmp_path) -> None:
    path = tmp_path / "1jbl.pdb"
    path.write_text(_SFTI)
    with pytest.raises(StudyError, match="ring") as caught:
        pipeline._refuse_a_backbone_ring(path)
    refusal = refusal_of(caught.value)
    assert refusal.code == "setup.structure.cyclic_backbone"
    assert "GLY A1 N to ASP A14 C" in str(caught.value)


@pytest.mark.parametrize("link", [
    # A lysine's side chain to a glycine's C: an isopeptide crosslink.
    "LINK         NZ  LYS A  48                 C   GLY A  76     1555   1555  1.33",
    # A peptide bond across a gap in the numbering, inside the chain.
    "LINK         N   ARG A   2                 C   GLY A   1     1555   1555  1.33",
    # Two chains joined end to end are not one chain's ring.
    "LINK         N   GLY A   1                 C   ASP B  14     1555   1555  1.34",
])
def test_links_that_close_no_ring_are_left_alone(tmp_path, link) -> None:
    path = tmp_path / "linked.pdb"
    path.write_text(link + "\n" + _SFTI.split("\n", 1)[1])
    pipeline._refuse_a_backbone_ring(path)


# -- A budget after a setup that failed -----------------------------------

def test_a_budget_says_the_setup_s_own_refusal(tmp_path) -> None:
    """1AON with `budget_hours`: setup refused its ADP, and the study then
    said setup had finished without a particle count, and kept it for reuse."""
    from fastmdxplora.agent import run_in_stages
    from fastmdxplora.orchestrator import PhaseResult, RunResult

    def explore(*, config, output_dir):
        return [RunResult(run_id="1AON", system="1AON", status="error", phases=[
            PhaseResult(name="setup", status="error", message="ADP is not determined",
                        refusal={"code": "setup.chemistry.protonation_undetermined",
                                 "message": "ADP is not determined"})])]

    staged = run_in_stages({"systems": [{"system": "1AON"}]}, tmp_path,
                           budget_hours=0.01, explore=explore)
    assert not staged.setup_done
    assert staged.refusal.code == "setup.chemistry.protonation_undetermined"
    assert "ADP is not determined" in staged.refusal.message
    assert staged.stopped_at == "setup"


# -- A modified residue of the chain --------------------------------------

def _deposition(entry: str) -> list[str]:
    return gzip.decompress((DEPOSITED / f"{entry}.pdb.gz").read_bytes()).decode().splitlines()


def _ca(path: Path, chain: str, number: int) -> tuple[float, float, float]:
    for line in path.read_text().splitlines():
        if (line.startswith(("ATOM", "HETATM")) and line[12:16] == " CA "
                and line[21] == chain and int(line[22:26]) == number):
            return (float(line[30:38]), float(line[38:46]), float(line[46:54]))
    raise AssertionError(f"no CA at {chain}{number}")


def test_a_modified_residue_is_made_standard_where_it_was_deposited(tmp_path) -> None:
    """1HVR's oxidised cysteines (CSO) went with the heterogens, and the gap
    they left was built as a cysteine of new coordinates. 3PTB's Cys22 is
    written here as CSO, as a deposition writes one."""
    pytest.importorskip("pdbfixer")
    from fastmdxplora.setup.pdbfix import fix_pdb_with_pdbfixer

    lines = [("HETATM" + line[6:17] + "CSO" + line[20:])
             if line.startswith("ATOM") and line[17:20] == "CYS" and int(line[22:26]) == 22
             else line for line in _deposition("3PTB")]
    given = tmp_path / "3ptb_cso.pdb"
    given.write_text("\n".join(lines) + "\n")
    out = tmp_path / "fixed.pdb"
    fix_pdb_with_pdbfixer(str(given), str(out), ph=7.0)
    assert _ca(out, "A", 22) == pytest.approx(_ca(given, "A", 22), abs=1e-3)
    assert any(line[17:20] == "CYS" and int(line[22:26]) == 22
               for line in out.read_text().splitlines() if line.startswith("ATOM"))


# -- A chain given as its alpha carbons ------------------------------------

def test_a_trace_of_alpha_carbons_is_refused(tmp_path) -> None:
    pytest.importorskip("pdbfixer")
    from fastmdxplora.setup.pdbfix import fix_pdb_with_pdbfixer

    trace = [line for line in _deposition("3PTB")
             if line.startswith("ATOM") and line[12:16] == " CA "]
    given = tmp_path / "trace.pdb"
    given.write_text("\n".join(trace) + "\nEND\n")
    with pytest.raises(StudyError, match="trace of the chain") as caught:
        fix_pdb_with_pdbfixer(str(given), str(tmp_path / "fixed.pdb"), ph=7.0)
    assert refusal_of(caught.value).code == "setup.structure.trace_only"


def test_a_few_residues_missing_their_side_chains_are_built(tmp_path) -> None:
    pytest.importorskip("pdbfixer")
    from fastmdxplora.setup.pdbfix import fix_pdb_with_pdbfixer

    lines = [line for line in _deposition("3PTB")
             if not (line.startswith("ATOM") and 30 <= int(line[22:26]) <= 34
                     and line[12:16] != " CA ")]
    given = tmp_path / "gapped.pdb"
    given.write_text("\n".join(lines) + "\n")
    fix_pdb_with_pdbfixer(str(given), str(tmp_path / "fixed.pdb"), ph=7.0)


# -- A small box, under-filled -------------------------------------------

@pytest.mark.parametrize("volume_nm3, factor", [(29.92, 1.0), (36.5, 1.068)])
def test_the_box_is_sized_for_the_density_it_will_reach(volume_nm3, factor) -> None:
    """1L2Y at 0.1 nm of padding was solvated at 0.82 g/mL, grown only to
    the fixed margin above twice the cutoff, and the barostat took it under
    that in production."""
    app = pytest.importorskip("openmm.app")
    from openmm import unit

    from fastmdxplora.setup.prepare import _contraction_to_density

    topology = app.Topology()
    chain = topology.addChain()
    for _ in range(1000):
        residue = topology.addResidue("HOH", chain)
        for name, element in (("O", "O"), ("H1", "H"), ("H2", "H")):
            topology.addAtom(name, app.Element.getBySymbol(element), residue)
    edge = volume_nm3 ** (1 / 3)
    vectors = [[(edge if i == j else 0.0) * unit.nanometer for j in range(3)] for i in range(3)]
    assert _contraction_to_density(topology, vectors, unit) == pytest.approx(factor, abs=2e-3)


def test_a_ligand_is_placed_from_the_structure_setup_chose(tmp_path, monkeypatch) -> None:
    """The pose is read from the assembly built, or the chains kept, where
    setup made one: from the deposited `input.pdb`, 1STP's built copies of
    biotin had none, and `chains: [B]` would have read chain A's copy first."""
    pytest.importorskip("openmm")
    from fastmdxplora.setup import prepare as prepare_module
    from tests.test_a_ligand_needs_a_charge_provider import TRIPEPTIDE

    prepared = tmp_path / "prepared.pdb"
    prepared.write_text(TRIPEPTIDE)
    (tmp_path / "input.pdb").write_text(TRIPEPTIDE)
    built = tmp_path / "input_assembly.pdb"
    built.write_text(TRIPEPTIDE)
    read: list[Path] = []

    class Placed(Exception):
        pass

    def pose(molecule, structure, name, **_):
        read.append(Path(structure))
        raise Placed

    monkeypatch.setattr(prepare_module, "load_ligand", lambda *a, **k: object())
    monkeypatch.setattr(prepare_module, "pose_by_policy", pose)
    with pytest.raises(Placed):
        prepare_module.prepare_system(prepared, tmp_path, forcefield="amber-openff",
                                      ligand=[str(tmp_path / "BTN.sdf")],
                                      ligand_name="BTN", ligand_poses_from=built)
    assert read == [built]


def test_a_modified_residue_of_a_chain_not_kept_goes_with_the_heterogens(tmp_path) -> None:
    """1HVR with `chains: [B]` carries chain A's CSO67, near chain B, beside
    it. Made standard alone it was a cysteine with no template, and setup
    stopped; it goes as it did, with the heterogens."""
    pytest.importorskip("pdbfixer")
    from fastmdxplora.setup.pdbfix import fix_pdb_with_pdbfixer

    deposited = _deposition("3PTB")
    lone = [("HETATM" + line[6:17] + "CSO" + line[20:21] + "C" + line[22:])
            for line in deposited
            if line.startswith("ATOM") and line[17:20] == "CYS" and int(line[22:26]) == 22]
    lines = [line for line in deposited if not line.startswith("END")] + ["TER"] + lone + ["END"]
    given = tmp_path / "3ptb_lone_cso.pdb"
    given.write_text("\n".join(lines) + "\n")
    out = tmp_path / "fixed.pdb"
    fix_pdb_with_pdbfixer(str(given), str(out), ph=7.0)
    assert not any(line[21] == "C" for line in out.read_text().splitlines()
                   if line.startswith(("ATOM", "HETATM")))


# -- A residue of the chain deposited as HETATM, in a built assembly --------

def test_a_hetatm_residue_of_the_chain_stays_in_each_built_copy(tmp_path) -> None:
    """1A8O's selenomethionines are HETATM records in the chain. Building its
    dimer wrote them after each copy's TER with the heterogens, and MSE 151,
    the first residue, began a chain of its own and was lost as an
    unresolved terminus. 1STP's Ala13, its first, is written here as MSE."""
    lines = [("HETATM" + line[6:17] + "MSE" + line[20:])
             if line.startswith("ATOM") and int(line[22:26]) == 13 else line
             for line in _deposition("1STP")]
    given = tmp_path / "input.pdb"
    given.write_text("\n".join(lines) + "\n")
    built = pipeline._the_chains_to_simulate(SimpleNamespace(_assembly=None), given,
                                             "pdb_id", {})
    written = built.read_text().splitlines()
    for chain in "ABCD":
        mine = [n for n, line in enumerate(written)
                if line.startswith(("ATOM", "HETATM")) and line[21] == chain
                and line[17:20] != "BTN" and line[17:20] != "HOH"]
        first = [n for n in mine if int(written[n][22:26]) == 13]
        closing = next(n for n in range(mine[-1], len(written)) if written[n].startswith("TER"))
        # Its first residue first, before the TER that closes it.
        assert first and first == mine[:len(first)], chain
        assert all(n < closing for n in mine), chain
        # The biotins are still heterogens, none inside a chain.
        assert not any(written[n][17:20] == "BTN" for n in range(mine[0], closing)), chain


# -- A phosphorylated residue made standard ---------------------------------

def test_a_phosphate_taken_off_a_residue_is_warned_of(tmp_path) -> None:
    """1QMZ's TPO160, the phosphate that activates CDK2, became a threonine
    said only at INFO. 3PTB's Ser32 is written here as SEP."""
    pytest.importorskip("pdbfixer")
    import logging

    from fastmdxplora.setup.pdbfix import fix_pdb_with_pdbfixer

    lines = [("HETATM" + line[6:17] + "SEP" + line[20:])
             if line.startswith("ATOM") and line[17:20] == "SER" and int(line[22:26]) == 32
             else line for line in _deposition("3PTB")]
    given = tmp_path / "3ptb_sep.pdb"
    given.write_text("\n".join(lines) + "\n")
    heard: list[logging.LogRecord] = []
    handler = logging.Handler(logging.WARNING)
    handler.emit = heard.append
    logger = logging.getLogger("fastmdx.setup.pdbfix")
    logger.addHandler(handler)
    try:
        fix_pdb_with_pdbfixer(str(given), str(tmp_path / "fixed.pdb"), ph=7.0)
    finally:
        logger.removeHandler(handler)
    said = [record.getMessage() for record in heard if record.levelno >= logging.WARNING]
    assert any("SEP" in line and "without their phosphate" in line for line in said), said


# -- DNA is the polymer, not a ligand ----------------------------------------

def test_dna_is_not_offered_as_a_ligand(tmp_path) -> None:
    """1BNA's dodecamer was offered as 24 ligands: the Viewer drew its bases
    as ball and stick with a binding pocket around them."""
    from fastmdxplora.gui.structure_info import count_structure
    from fastmdxplora.ligand_detection import detect_ligands

    keys = [("A", name, str(n)) for n, name in enumerate("DC DG DC DG DA DA".split(), 1)]
    keys += [("B", "DT5", "13"), ("B", "RU3", "24"), ("C", "ATP", "500")]
    assert detect_ligands(keys)["resnames"] == ["ATP"]
    lines = [f"ATOM  {n:5d}  P   {name:>3s} A{n:4d}    {n:8.3f}{n:8.3f}{n:8.3f}  1.00  0.00           P"
             for n, name in enumerate("DC DG DA DT".split(), 1)]
    path = tmp_path / "dna.pdb"
    path.write_text("\n".join(lines + ["END"]) + "\n")
    assert count_structure(path)["ligand_resnames"] == []
