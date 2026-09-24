"""A run given `setup_from` simulates the named system and prepares nothing.

`setup_from` exists so that replicas share one solvated system and differ
only in their seed. Outside umbrella campaigns it reached the simulation
phase alone: a study that kept setup in its plan still prepared, in every
run of a sweep, a box it never simulated -- and the record of that box, the
one beside each run, described atoms other than the ones simulated. A study
that left setup out had no record beside it at all, so the ligand's
chemistry was perceived from coordinates instead of read, its name was not
found, and the report said setup was not run.

One real replica study is run twice here: with setup still in its plan and
with it excluded. The ligand is
methylammonium, typed with the protein force field's own lysine atom types,
so the whole path runs on OpenMM without a small-molecule toolkit.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

LIGAND = "MAM"

#: Methylammonium, typed and charged as lysine's CE/NZ group: every bond,
#: angle and torsion it needs is already in amber14.
LIGAND_XML = """<ForceField>
 <Residues>
  <Residue name="MAM">
   <Atom name="C1" type="protein-C8" charge="0.0249"/>
   <Atom name="N1" type="protein-N3" charge="-0.3854"/>
   <Atom name="H1" type="protein-HP" charge="0.1135"/>
   <Atom name="H2" type="protein-HP" charge="0.1135"/>
   <Atom name="H3" type="protein-HP" charge="0.1135"/>
   <Atom name="H4" type="protein-H" charge="0.34"/>
   <Atom name="H5" type="protein-H" charge="0.34"/>
   <Atom name="H6" type="protein-H" charge="0.34"/>
   <Bond atomName1="C1" atomName2="N1"/>
   <Bond atomName1="C1" atomName2="H1"/>
   <Bond atomName1="C1" atomName2="H2"/>
   <Bond atomName1="C1" atomName2="H3"/>
   <Bond atomName1="N1" atomName2="H4"/>
   <Bond atomName1="N1" atomName2="H5"/>
   <Bond atomName1="N1" atomName2="H6"/>
  </Residue>
 </Residues>
</ForceField>
"""
LIGAND_NAMES = ("C1", "N1", "H1", "H2", "H3", "H4", "H5", "H6")


def _the_ligand():
    """Methylammonium as setup would have resolved it: charged, with
    hydrogens, in the order RDKit gives -- heavy atoms first."""
    from rdkit import Chem
    from rdkit.Chem import AllChem

    mol = Chem.AddHs(Chem.MolFromSmiles("C[NH3+]"))
    AllChem.EmbedMolecule(mol, randomSeed=3)
    return mol


def _a_setup_record(n_atoms: int, system: str) -> dict:
    """The shape setup writes, with the fields the readers look for."""
    return {
        "phase": "setup",
        "input": {"system": system, "form": "file"},
        "parameters": {"ph": 7.0, "forcefield": "amber14",
                       "solvent_padding_nm": 1.0, "box_shape": "cube",
                       "ion_concentration_M": 0.0,
                       "ligand": [f"ligands/{LIGAND}.sdf"],
                       "ligand_name": LIGAND, "ligand_net_charge": 1},
        "resolved_forcefield": {
            "source": "named", "name": "amber14",
            "xmls": ["amber14-all.xml", "amber14/tip3p.xml"],
            "water_model": "tip3p",
            "ligand": {"name": LIGAND, "net_charge": 1,
                       "files": [f"ligands/{LIGAND}.sdf"]}},
        "n_atoms_solvated": n_atoms,
    }


def _record_the_ligand(setup: Path) -> Path:
    from rdkit import Chem

    (setup / "ligands").mkdir(parents=True, exist_ok=True)
    path = setup / "ligands" / f"{LIGAND}.sdf"
    writer = Chem.SDWriter(str(path))
    writer.write(_the_ligand())
    writer.close()
    return path


def _placeholders(setup: Path, n_atoms: int = 12) -> Path:
    """A prepared system's three files, for code that only checks they are
    there, and its record."""
    setup.mkdir(parents=True, exist_ok=True)
    for name in ("system.xml", "state.xml", "topology.pdb"):
        (setup / name).write_text("<x/>", encoding="utf-8")
    (setup / "setup_parameters.json").write_text(
        json.dumps(_a_setup_record(n_atoms, "complex.pdb")), encoding="utf-8")
    return setup


def _names_in_its_config(run: Path, named) -> Path:
    run.mkdir(parents=True, exist_ok=True)
    (run / "resolved_config.yml").write_text(
        f"simulation:\n  setup_from: {named}\n", encoding="utf-8")
    return run


# ---------------------------------------------------------------------------
# Where a run's setup record is
# ---------------------------------------------------------------------------
class TestTheSetupRecordOfARun:

    def test_a_run_that_prepared_its_own_system_has_its_own(self, tmp_path):
        from fastmdxplora.simulation.pipeline import setup_records_of

        own = _placeholders(tmp_path / "run" / "setup")
        _placeholders(tmp_path / "earlier" / "setup")
        _names_in_its_config(tmp_path / "run", tmp_path / "earlier")

        assert setup_records_of(tmp_path / "run") == own

    @pytest.mark.parametrize("shape", ["setup", "shared_setup/setup", ""])
    def test_a_run_without_one_has_the_named_systems(self, tmp_path, shape):
        """A study directory, a set of windows' shared preparation, or the
        setup directory itself: resolved as the simulation phase resolves
        the same setting."""
        from fastmdxplora.simulation.pipeline import setup_records_of

        study = tmp_path / "earlier"
        prepared = _placeholders(study / shape if shape else study)
        run = _names_in_its_config(tmp_path / "run", study)

        assert setup_records_of(run) == prepared

    def test_the_simulation_record_names_it_too(self, tmp_path):
        from fastmdxplora.simulation.pipeline import setup_records_of

        prepared = _placeholders(tmp_path / "earlier" / "setup")
        run = tmp_path / "run"
        (run / "simulation").mkdir(parents=True)
        (run / "simulation" / "simulation_parameters.json").write_text(
            json.dumps({"parameters": {"prepared_from": str(tmp_path / "earlier")}}),
            encoding="utf-8")

        assert setup_records_of(run) == prepared

    def test_a_relative_name_is_read_from_where_the_run_was_started(
            self, tmp_path, monkeypatch):
        from fastmdxplora.simulation.pipeline import setup_records_of

        prepared = _placeholders(tmp_path / "runs" / "prepared" / "setup")
        run = _names_in_its_config(tmp_path / "runs" / "x", "runs/prepared")
        monkeypatch.chdir(tmp_path)

        assert setup_records_of(run) == prepared

    def test_the_manifest_is_the_latest_word(self, tmp_path):
        """A run that once prepared its own system and was then simulated
        from another still has the first `setup/` on disk."""
        from fastmdxplora.simulation.pipeline import setup_records_of

        run = tmp_path / "run"
        _placeholders(run / "setup")
        prepared = _placeholders(tmp_path / "earlier" / "setup")
        (run / "manifest.json").write_text(json.dumps({"phases": [
            {"name": "setup", "status": "skipped", "taken_from": str(prepared)}]}),
            encoding="utf-8")

        assert setup_records_of(run) == prepared

    def test_nothing_recorded_anywhere_is_none(self, tmp_path):
        from fastmdxplora.simulation.pipeline import setup_records_of

        run = _names_in_its_config(tmp_path / "run", tmp_path / "not-here")
        assert setup_records_of(run) is None
        assert setup_records_of(tmp_path / "empty") is None


# ---------------------------------------------------------------------------
# The plan: which phases run
# ---------------------------------------------------------------------------
def _explore_recording(tmp_path, monkeypatch, simulation, *, real_simulation=False):
    """Explore one system with the phases stood in for, returning the phases
    that ran and the result. The plan and the manifest are the real ones."""
    from fastmdxplora import orchestrator
    from fastmdxplora.orchestrator import FastMDXplora
    from tests.test_a_real_study_runs_end_to_end import TRI_ALANINE

    ran: list[str] = []
    real = orchestrator.FastMDXplora._resolve_phase_runner

    def resolve(phase):
        if phase == "simulation" and real_simulation:
            return real(phase)

        def stand_in(**_kwargs):
            ran.append(phase)
            return []
        return stand_in

    monkeypatch.setattr(orchestrator.FastMDXplora, "_resolve_phase_runner",
                        staticmethod(resolve))
    structure = tmp_path / "tri.pdb"
    structure.write_text(TRI_ALANINE, encoding="utf-8")
    run = FastMDXplora(system=str(structure), output_dir=tmp_path / "run",
                       options={"simulation": simulation})
    result = run.explore()
    return ran, result[0], tmp_path / "run"


class TestTheSetupPhaseIsNotRun:

    @pytest.mark.parametrize("key", ["setup_from", "prepared_from"])
    def test_a_named_system_takes_its_place(self, tmp_path, monkeypatch, key):
        prepared = _placeholders(tmp_path / "earlier" / "setup")

        ran, result, run = _explore_recording(
            tmp_path, monkeypatch, {key: str(tmp_path / "earlier")})

        assert ran == ["simulation", "analysis", "report"]
        assert not (run / "setup").exists()
        setup = result.phase("setup")
        assert setup.status == "skipped"
        assert Path(setup.taken_from) == prepared

    def test_the_manifest_names_the_system_simulated(self, tmp_path, monkeypatch):
        prepared = _placeholders(tmp_path / "earlier" / "setup")

        _ran, _result, run = _explore_recording(
            tmp_path, monkeypatch, {"setup_from": str(tmp_path / "earlier")})

        manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
        setup = [p for p in manifest["phases"] if p["name"] == "setup"]
        assert len(setup) == 1
        assert Path(setup[0]["taken_from"]) == prepared
        assert "setup_from" in setup[0]["message"]

    def test_a_name_pointing_at_nothing_is_still_refused(self, tmp_path, monkeypatch):
        """Nothing there to take, so setup runs as asked and the simulation
        phase refuses the name, as it did."""
        ran, result, _run = _explore_recording(
            tmp_path, monkeypatch, {"setup_from": str(tmp_path / "not-here")},
            real_simulation=True)

        assert ran == ["setup"]
        refused = result.phase("simulation")
        assert refused.status == "error"
        assert refused.message.startswith("setup_from points at")
        assert "holds a prepared system" in refused.message
        assert not result.phase("setup").taken_from


# ---------------------------------------------------------------------------
# The readers of a setup record
# ---------------------------------------------------------------------------
class TestTheLigandIsReadFromTheNamedSetup:
    """Salt bridges depend on the ligand's charge, so where the chemistry
    comes from is not a detail."""

    def _complex(self):
        md = pytest.importorskip("mdtraj")

        topology = md.Topology()
        chain = topology.add_chain()
        asp = topology.add_residue("ASP", chain)
        xyz = []
        for name, element, pos in (("CG", md.element.carbon, (0.0, 0.0, 0.0)),
                                   ("OD1", md.element.oxygen, (0.12, 0.05, 0.0)),
                                   ("OD2", md.element.oxygen, (-0.12, 0.05, 0.0))):
            topology.add_atom(name, element, asp)
            xyz.append(pos)
        cg, od1, od2 = list(topology.atoms)
        topology.add_bond(cg, od1)
        topology.add_bond(cg, od2)

        mol = _the_ligand()
        ligand = topology.add_residue(LIGAND, topology.add_chain())
        placed = mol.GetConformer().GetPositions() / 10.0
        placed = placed - placed[1] + np.array([0.0, 0.33, 0.0])
        atoms = [topology.add_atom(name, md.element.get_by_symbol(
                     mol.GetAtomWithIdx(i).GetSymbol()), ligand)
                 for i, name in enumerate(LIGAND_NAMES)]
        for bond in mol.GetBonds():
            topology.add_bond(atoms[bond.GetBeginAtomIdx()], atoms[bond.GetEndAtomIdx()])
        xyz.extend(map(tuple, placed))
        frame = np.array(xyz, dtype=float)
        return md.Trajectory(np.stack([frame, frame]), topology)

    def test_the_chemistry_came_from_that_file_with_its_charge(self, tmp_path):
        pytest.importorskip("rdkit")
        from fastmdxplora.analysis.pl_interactions import ProteinLigandInteractions

        prepared = _placeholders(tmp_path / "prepared" / "setup")
        sdf = _record_the_ligand(prepared)
        run = _names_in_its_config(tmp_path / "runs" / "x", tmp_path / "prepared")

        analysis = ProteinLigandInteractions(
            ligand_resname=LIGAND,
            output_dir=run / "analysis" / "pl_interactions")
        analysis.compute(self._complex())

        chemistry = analysis.findings["ligand_chemistry"]
        assert chemistry["source"] == "run"
        assert chemistry["bond_orders_perceived"] is False
        assert Path(chemistry["detail"]) == sdf
        assert chemistry["formal_charge"] == 1
        # Read where it is, not copied beside the run.
        assert not (run / "setup").exists()

    def test_the_analysis_phase_finds_the_ligand_by_name(self, tmp_path):
        from fastmdxplora.analysis.analyze import _detect_ligand_resname

        _placeholders(tmp_path / "prepared" / "setup")
        run = _names_in_its_config(tmp_path / "runs" / "x", tmp_path / "prepared")

        assert _detect_ligand_resname(run) == LIGAND


class TestTheReportDescribesTheNamedSystem:

    def _a_run(self, tmp_path):
        _placeholders(tmp_path / "prepared" / "setup", n_atoms=35012)
        run = tmp_path / "runs" / "x"
        (run / "simulation").mkdir(parents=True)
        (run / "simulation" / "simulation_parameters.json").write_text(json.dumps({
            "parameters": {"setup_from": str(tmp_path / "prepared"),
                           "timestep_fs": 2.0, "temperature_K": 300.0,
                           "integrator": "langevin_middle"},
            "duration_ns_actual": 0.5, "platform_used": "CPU"}), encoding="utf-8")
        return run

    def test_the_methods_name_its_force_field_and_box(self, tmp_path):
        from fastmdxplora.report.context import load_phase_context
        from fastmdxplora.report.document import _methods_section

        run = self._a_run(tmp_path)
        text = _methods_section(run, load_phase_context(run))

        assert "amber14-all.xml" in text and "tip3p" in text
        assert "35,012" in text
        assert "**box\\_shape**: `cube`" in text
        assert "`simulation.setup_from`" in text
        assert "Setup was not run in this workflow" not in text

    def test_the_slides_say_how_it_was_built(self, tmp_path):
        from fastmdxplora.report.slides import _setup_bullets

        bullets = _setup_bullets(self._a_run(tmp_path))

        assert "Force field: amber14-all.xml, amber14/tip3p.xml, tip3p water" in bullets
        assert "Box: cube, 1 nm padding" in bullets
        assert "35,012 atoms after solvation" in bullets

    def test_the_summary_states_its_size(self, tmp_path):
        from fastmdxplora.report.document import _study_in_one_paragraph

        paragraph = _study_in_one_paragraph(self._a_run(tmp_path))
        assert "35,012 atoms" in paragraph


class TestAnUnattendedStudyKeepsTheSystemItNamed:
    """The agent's staged route runs setup alone, prices the study on the
    particle count it leaves, then runs the rest. Given `setup_from`, the
    first stage prepares nothing: the count is the named system's, and the
    rest simulates that system rather than an empty `setup/` of its own."""

    def test_it_is_priced_and_simulated_on_the_named_system(self, tmp_path,
                                                            monkeypatch):
        from fastmdxplora import cost
        from fastmdxplora.agent import run_in_stages

        _placeholders(tmp_path / "prepared" / "setup", n_atoms=4321)
        priced: list[int] = []

        def estimate(config, *, particles, **_kwargs):
            priced.append(particles)
            return SimpleNamespace(seconds=1.0, hours=1 / 3600)

        monkeypatch.setattr(cost, "estimate_study", estimate)
        given: list[dict] = []

        def explore(*, config, output_dir):
            # What the orchestrator leaves: the setting, in the run's config.
            given.append(config)
            _names_in_its_config(Path(output_dir), config["simulation"]["setup_from"])

        named = str(tmp_path / "prepared")
        staged = run_in_stages(
            {"systems": [{"id": "a", "system": "x.pdb"}],
             "simulation": {"setup_from": named}},
            tmp_path / "run", explore=explore)

        assert priced == [4321]
        assert given[1]["simulation"]["setup_from"] == named
        assert staged.simulated


# ---------------------------------------------------------------------------
# The whole study, really run
# ---------------------------------------------------------------------------
def _a_prepared_system(root: Path) -> SimpleNamespace:
    """A tri-alanine and a methylammonium beside its carboxylate, solvated
    and written as setup writes it, with the ligand's resolved chemistry and
    the setup record beside it."""
    pytest.importorskip("openmm")
    import openmm
    from openmm import unit
    from openmm.app import PME, ForceField, HBonds, Modeller, PDBFile, Topology
    from openmm.app import element as elements

    from tests.test_a_real_study_runs_end_to_end import TRI_ALANINE

    (root / "tri.pdb").write_text(TRI_ALANINE, encoding="utf-8")
    (root / "mam.xml").write_text(LIGAND_XML, encoding="utf-8")
    forcefield = ForceField("amber14-all.xml", "amber14/tip3p.xml", str(root / "mam.xml"))
    peptide = PDBFile(str(root / "tri.pdb"))
    modeller = Modeller(peptide.topology, peptide.positions)
    modeller.addHydrogens(forcefield, pH=7.0)

    mol = _the_ligand()
    topology = Topology()
    residue = topology.addResidue(LIGAND, topology.addChain())
    atoms = [topology.addAtom(name, elements.Element.getBySymbol(
                 mol.GetAtomWithIdx(i).GetSymbol()), residue)
             for i, name in enumerate(LIGAND_NAMES)]
    for bond in mol.GetBonds():
        topology.addBond(atoms[bond.GetBeginAtomIdx()], atoms[bond.GetEndAtomIdx()])
    oxt = next(a for a in modeller.topology.atoms() if a.name == "OXT")
    anchor = np.array(modeller.positions[oxt.index].value_in_unit(unit.nanometer))
    placed = mol.GetConformer().GetPositions() / 10.0
    placed = placed - placed[1] + anchor + np.array([0.28, 0.0, 0.0])
    modeller.add(topology, [openmm.Vec3(*p) for p in placed] * unit.nanometer)
    complex_pdb = root / "complex.pdb"
    with complex_pdb.open("w", encoding="utf-8") as handle:
        PDBFile.writeFile(modeller.topology, modeller.positions, handle)

    modeller.addSolvent(forcefield, padding=1.0 * unit.nanometer, neutralize=True)
    system = forcefield.createSystem(modeller.topology, nonbondedMethod=PME,
                                     nonbondedCutoff=0.9 * unit.nanometer,
                                     constraints=HBonds)
    context = openmm.Context(system, openmm.VerletIntegrator(0.001 * unit.picoseconds),
                             openmm.Platform.getPlatformByName("Reference"))
    context.setPositions(modeller.positions)
    state = context.getState(getPositions=True, getVelocities=True)

    setup = root / "runs" / "prepared" / "setup"
    setup.mkdir(parents=True)
    (setup / "system.xml").write_text(openmm.XmlSerializer.serialize(system), encoding="utf-8")
    (setup / "state.xml").write_text(openmm.XmlSerializer.serialize(state), encoding="utf-8")
    with (setup / "topology.pdb").open("w", encoding="utf-8") as handle:
        PDBFile.writeFile(modeller.topology, modeller.positions, handle)
    sdf = _record_the_ligand(setup)
    (setup / "setup_parameters.json").write_text(json.dumps(
        _a_setup_record(system.getNumParticles(), str(complex_pdb))), encoding="utf-8")
    return SimpleNamespace(setup=setup, sdf=sdf, complex=complex_pdb,
                           atoms=system.getNumParticles())


def _atoms_in(pdb: Path) -> int:
    return sum(1 for line in pdb.read_text(encoding="utf-8").splitlines()
               if line.startswith(("ATOM", "HETATM")))


SETUP_IN_THE_PLAN = "setup in the plan"
SETUP_EXCLUDED = "setup excluded"


@pytest.fixture(scope="module", params=[SETUP_IN_THE_PLAN, SETUP_EXCLUDED])
def replicas(request, tmp_path_factory):
    """Two replicas of one bound complex, differing only in their seed, run
    from one prepared system -- the study `setup_from` exists for."""
    pytest.importorskip("openmm")
    pytest.importorskip("rdkit")
    from fastmdxplora import FastMDXplora

    root = tmp_path_factory.mktemp("replicas")
    prepared = _a_prepared_system(root)
    config = {
        "output": "runs/x",
        "systems": [{"system": str(prepared.complex), "id": "bound"}],
        # Minutes of dynamics are not the point: the smallest run that goes
        # through every phase.
        "simulation": {"setup_from": "runs/prepared", "duration_ns": 0.0001,
                       "nvt_steps": 20, "npt_steps": 20,
                       "minimize_max_iterations": 100, "platform": "CPU",
                       "trajectory_interval_steps": 10},
        "sweep": {"simulation.random_seed": [11, 12]},
        "analysis": {"include": ["rmsd", "ligand_rmsd", "pl_interactions"]},
    }
    if request.param == SETUP_EXCLUDED:
        config["exclude_phase"] = ["setup"]

    propagate = logging.getLogger("fastmdx").propagate
    with pytest.MonkeyPatch.context() as patch:
        patch.chdir(root)
        # Another run may be using the machine; one thread is plenty for a
        # thousand atoms.
        patch.setenv("OPENMM_CPU_THREADS", "1")
        results = FastMDXplora(config_data=config).explore()
    logging.getLogger("fastmdx").propagate = propagate

    members = sorted((root / "runs" / "x" / "runs").iterdir())
    return SimpleNamespace(variant=request.param, prepared=prepared,
                           results=results, members=members)


@pytest.mark.slow
class TestTwoReplicasOfOnePreparedSystem:

    def test_every_member_ran_to_the_end(self, replicas):
        assert len(replicas.results) == 2
        for result in replicas.results:
            assert result.status == "ok", result.message
            assert [(p.name, p.status) for p in result.phases] == [
                ("setup", "skipped"), ("simulation", "ok"),
                ("analysis", "ok"), ("report", "ok")]

    def test_no_member_prepared_a_system(self, replicas):
        assert len(replicas.members) == 2
        for member in replicas.members:
            assert not (member / "setup").exists()

    def test_a_member_that_would_have_prepared_one_says_why(self, replicas):
        expected = replicas.variant == SETUP_IN_THE_PLAN
        for member in replicas.members:
            log = (member / "fastmdxplora.log").read_text(encoding="utf-8")
            assert ("Preparing nothing: `setup_from` names" in log) is expected

    def test_the_manifest_names_the_system_simulated(self, replicas):
        for member in replicas.members:
            manifest = json.loads((member / "manifest.json").read_text(encoding="utf-8"))
            setup = next(p for p in manifest["phases"] if p["name"] == "setup")
            assert setup["status"] == "skipped"
            assert Path(setup["taken_from"]) == replicas.prepared.setup

    def test_every_member_simulates_the_named_system(self, replicas):
        for member in replicas.members:
            assert (_atoms_in(member / "simulation" / "topology.pdb")
                    == _atoms_in(replicas.prepared.setup / "topology.pdb")
                    == replicas.prepared.atoms)

    def test_the_ligand_is_found_and_read_from_the_named_setup(self, replicas):
        for member in replicas.members:
            analyses = json.loads((member / "analysis" / "analysis_manifest.json")
                                  .read_text(encoding="utf-8"))
            assert {name: result["status"] for name, result
                    in analyses["results"].items()} == {
                "rmsd": "ok", "ligand_rmsd": "ok", "pl_interactions": "ok"}
            options = json.loads((member / "analysis" / "pl_interactions" / "options.json")
                                 .read_text(encoding="utf-8"))
            chemistry = options["findings"]["ligand_chemistry"]
            assert chemistry["source"] == "run"
            assert Path(chemistry["detail"]) == replicas.prepared.sdf
            assert chemistry["formal_charge"] == 1

    def test_the_report_describes_the_named_system(self, replicas):
        for member in replicas.members:
            report = (member / "report" / "report.md").read_text(encoding="utf-8")
            assert "amber14-all.xml" in report
            assert f"{replicas.prepared.atoms:,}" in report
            assert "Setup was not run in this workflow" not in report
            outline = (member / "report" / "slides_outline.md").read_text(encoding="utf-8")
            assert "Box: cube, 1 nm padding" in outline
