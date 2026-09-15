"""Tests for the setup phase.

Two test strategies:

1. **Mock-based**: PDBFixer + OpenMM aren't installable in the sandbox
   (conda-forge only), so we mock the chemistry calls and verify the
   pipeline invokes the right APIs with the right arguments. Catches
   wiring bugs without needing 250MB of conda packages.

2. **Real end-to-end**: marked with ``@pytest.mark.skipif`` and only
   runs when PDBFixer + OpenMM are actually installed. Verifies the full
   PDB-to-system pipeline produces a usable OpenMM ``System``.

A simple TrpCage-style PDB is bundled for the real test.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from fastmdxplora.dependencies import MissingDependency
from fastmdxplora.setup import pdbfix as _pdbfix_mod
from fastmdxplora.setup import prepare as _prepare_mod
from fastmdxplora.setup.pipeline import (
    DEFAULTS,
    _classify_input,
    _resolve_input,
    run as setup_run,
)


# ---------------------------------------------------------------------------
# Optional-deps detection
# ---------------------------------------------------------------------------
try:
    import openmm  # noqa: F401
    import pdbfixer  # noqa: F401
    HAS_SETUP_DEPS = True
except ImportError:
    HAS_SETUP_DEPS = False


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
MINI_PDB = """\
HEADER    DUMMY                                   01-JAN-26                     
ATOM      1  N   ALA A   1       0.000   0.000   0.000  1.00  0.00           N
ATOM      2  CA  ALA A   1       1.458   0.000   0.000  1.00  0.00           C
ATOM      3  C   ALA A   1       2.009   1.420   0.000  1.00  0.00           C
ATOM      4  O   ALA A   1       1.251   2.390   0.000  1.00  0.00           O
ATOM      5  CB  ALA A   1       1.988  -0.773  -1.199  1.00  0.00           C
ATOM      6  N   GLY A   2       3.332   1.549   0.000  1.00  0.00           N
ATOM      7  CA  GLY A   2       3.972   2.849   0.000  1.00  0.00           C
ATOM      8  C   GLY A   2       5.486   2.705   0.000  1.00  0.00           C
ATOM      9  O   GLY A   2       6.008   1.593   0.000  1.00  0.00           O
ATOM     10  N   ALA A   3       6.171   3.845   0.000  1.00  0.00           N
ATOM     11  CA  ALA A   3       7.623   3.845   0.000  1.00  0.00           C
ATOM     12  C   ALA A   3       8.174   5.265   0.000  1.00  0.00           C
ATOM     13  O   ALA A   3       7.416   6.235   0.000  1.00  0.00           O
ATOM     14  CB  ALA A   3       8.153   3.072  -1.199  1.00  0.00           C
ATOM     15  OXT ALA A   3       9.400   5.400   0.000  1.00  0.00           O
END
"""


@pytest.fixture
def mini_pdb(tmp_path: Path) -> Path:
    """A 5-atom single-ALA PDB on disk."""
    p = tmp_path / "mini.pdb"
    p.write_text(MINI_PDB)
    return p


@pytest.fixture
def stub_orchestrator(tmp_path: Path, mini_pdb: Path):
    """A minimal stand-in for FastMDXplora."""
    orch = MagicMock()
    orch.system = str(mini_pdb)
    orch.output_dir = tmp_path / "proj"
    orch.output_dir.mkdir()
    orch._presenter = None
    return orch


# ===========================================================================
# _classify_input
# ===========================================================================
class TestClassifyInput:
    def test_pdb_file(self, mini_pdb: Path):
        assert _classify_input(str(mini_pdb)) == "pdb_file"

    def test_cif_file(self, tmp_path: Path):
        p = tmp_path / "x.cif"
        p.write_text("# fake cif")
        assert _classify_input(str(p)) == "pdb_file"

    def test_pdb_id(self):
        assert _classify_input("1L2Y") == "pdb_id"
        assert _classify_input("1abc") == "pdb_id"

    def test_sequence(self):
        # Long alphabetic-only -> sequence
        assert _classify_input("MKTAYIAKQRQISFVKSHFSRQ") == "sequence"

    def test_none_raises(self):
        with pytest.raises(ValueError, match="requires a system"):
            _classify_input(None)

    def test_unrecognized_raises(self):
        with pytest.raises(ValueError, match="Could not classify"):
            _classify_input("not_a_real_thing_with_spaces and stuff")


# ===========================================================================
# _resolve_input
# ===========================================================================
class TestResolveInput:
    def test_pdb_file_copies(self, mini_pdb: Path, tmp_path: Path):
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        result = _resolve_input(str(mini_pdb), "pdb_file", out_dir)
        assert result.exists()
        assert result.read_text(encoding="utf-8") == MINI_PDB

    def test_sequence_raises_not_implemented(self, tmp_path: Path):
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        with pytest.raises(NotImplementedError, match="structure predictor"):
            _resolve_input("MKTAYIA", "sequence", out_dir)

    def test_pdb_id_uses_urllib(self, tmp_path: Path):
        """RCSB fetch path goes through urllib.urlretrieve."""
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        with patch("urllib.request.urlretrieve") as m:
            # Make the mock create the file so the test can continue
            def fake_fetch(url, dest):
                Path(dest).write_text("HEADER fetched\nEND\n")
                return dest, None
            m.side_effect = fake_fetch
            result = _resolve_input("1L2Y", "pdb_id", out_dir)
            assert m.called
            url_arg = m.call_args[0][0]
            assert "1L2Y" in url_arg
            assert "rcsb.org" in url_arg
            assert result.exists()


# ===========================================================================
# pdbfix.fix_pdb_with_pdbfixer — signature and import-error contract
# ===========================================================================
class TestPDBFixWrapper:
    def test_pdbfixer_wrapper_signature(self):
        """fix_pdb_with_pdbfixer exposes the expected PDBFixer kwargs."""
        import inspect

        sig = inspect.signature(_pdbfix_mod.fix_pdb_with_pdbfixer)
        params = sig.parameters
        assert "input_pdb" in params
        assert "output_pdb" in params
        # All chemistry options are keyword-only with the documented defaults
        assert params["ph"].default == 7.0
        assert params["keep_heterogens"].default is False
        assert params["keep_water"].default is False

    def test_missing_input_raises_filenotfound(self, tmp_path: Path):
        """Missing input PDB raises FileNotFoundError before PDBFixer is touched."""
        with patch.dict("sys.modules", {"openmm.app": MagicMock(), "pdbfixer": MagicMock()}):
            with pytest.raises(FileNotFoundError):
                _pdbfix_mod.fix_pdb_with_pdbfixer(
                    str(tmp_path / "nope.pdb"), str(tmp_path / "out.pdb")
                )

    def test_import_error_message_mentions_conda(self):
        """When PDBFixer is missing the error tells the user how to install."""
        # Force the import to fail by removing the modules.
        with patch.dict("sys.modules", {"openmm.app": None, "pdbfixer": None}):
            with pytest.raises(ImportError, match="conda-forge"):
                _pdbfix_mod.fix_pdb_with_pdbfixer("/dev/null", "/tmp/out.pdb")

    @pytest.mark.skipif(HAS_SETUP_DEPS, reason="run only when deps absent")
    def test_real_call_raises_importerror_in_sandbox(self, mini_pdb: Path, tmp_path: Path):
        """Without the deps installed, calling the wrapper raises ImportError."""
        with pytest.raises(ImportError):
            _pdbfix_mod.fix_pdb_with_pdbfixer(
                str(mini_pdb), str(tmp_path / "fixed.pdb")
            )


# ===========================================================================
# prepare.prepare_system — mocked OpenMM
# ===========================================================================
class TestPrepareSystem:
    def test_the_default_resolves_to_a_ligand_capable_stack(self):
        """Unspecified means auto, which must be able to take a ligand.

        The default heterogen policy prepares a bound ligand without being
        asked, so a protein-only default would refuse on any structure that
        has one.
        """
        from fastmdxplora.setup.forcefields import (
            DEFAULT_FORCEFIELD,
            resolve_forcefield,
        )
        assert DEFAULT_FORCEFIELD == "auto"
        choice = resolve_forcefield(None)
        assert choice.supports_ligand
        assert choice.small_molecule_forcefield
        assert _prepare_mod.DEFAULT_PADDING_NM == 1.0
        assert _prepare_mod.DEFAULT_IONIC_STRENGTH_M == 0.15

    def test_missing_input_raises(self, tmp_path: Path):
        """Missing prepared PDB raises FileNotFoundError."""
        # Even with the OpenMM import succeeding (mocked), the file check
        # should run first.
        with patch.object(_prepare_mod, "_import_openmm", return_value={"unit": MagicMock()}):
            with pytest.raises(FileNotFoundError):
                _prepare_mod.prepare_system(tmp_path / "nope.pdb", tmp_path)

    def test_import_error_message_is_helpful(self):
        """The lazy importer raises a helpful ImportError if OpenMM missing."""
        # Patch openmm import to fail by monkey-patching builtins.__import__
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "openmm" or name.startswith("openmm."):
                raise ImportError(f"No module named {name!r}")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fake_import):
            with pytest.raises(ImportError, match="conda-forge"):
                _prepare_mod._import_openmm()

    def test_calls_modeller_addsolvent_with_right_args(self, mini_pdb: Path, tmp_path: Path):
        """The pipeline calls Modeller.addSolvent with the user-supplied options."""
        captured: dict = {}

        # Build a fake OpenMM module dict
        fake_modeller = MagicMock()
        fake_modeller_instance = MagicMock()
        fake_modeller_instance.topology = MagicMock(
            getNumAtoms=MagicMock(return_value=42),
            getPeriodicBoxVectors=MagicMock(return_value=None),
        )
        fake_modeller_instance.positions = MagicMock()

        def capture_addsolvent(ff, **kw):
            captured["kwargs"] = kw

        fake_modeller_instance.addSolvent.side_effect = capture_addsolvent
        fake_modeller.return_value = fake_modeller_instance

        fake_pdbfile = MagicMock()
        fake_pdbfile.return_value.topology = MagicMock()
        fake_pdbfile.return_value.positions = MagicMock()

        fake_system = MagicMock()
        fake_ff_class = MagicMock()
        fake_ff_class.return_value.createSystem.return_value = fake_system

        fake_context = MagicMock()
        fake_state = MagicMock()
        fake_context.getState.return_value = fake_state

        fake_openmm = MagicMock()
        fake_openmm.Context.return_value = fake_context
        fake_openmm.XmlSerializer.serialize.return_value = "<xml/>"

        fake_omm = {
            "openmm": fake_openmm,
            "unit": MagicMock(nanometer=1, molar=1, kelvin=1, picoseconds=1, amu=1),
            "ForceField": fake_ff_class,
            "HBonds": object(),
            "Modeller": fake_modeller,
            "PDBFile": fake_pdbfile,
            "PME": object(),
        }

        with patch.object(_prepare_mod, "_import_openmm", return_value=fake_omm):
            _prepare_mod.prepare_system(
                mini_pdb,
                tmp_path / "out",
                solvent_padding_nm=1.5,
                ion_concentration_M=0.10,
                ion_positive="K+",
                ion_negative="Cl-",
            )

        # Verify the captured kwargs
        kw = captured["kwargs"]
        # padding is a Quantity-like product — we made the unit-namespace
        # values all 1, so the numeric value passes through unchanged
        assert kw["padding"] == 1.5
        assert kw["positiveIon"] == "K+"
        assert kw["negativeIon"] == "Cl-"
        assert kw["ionicStrength"] == 0.10
        assert kw["neutralize"] is True

    def test_writes_expected_artifacts(self, mini_pdb: Path, tmp_path: Path):
        """All four artifact files appear at the expected paths after the call."""
        out = tmp_path / "out"

        def fake_pdb_writefile(top, pos, fh, keepIds=True):
            fh.write("HEADER fake\nEND\n")

        fake_pdbfile_cls = MagicMock()
        fake_pdbfile_cls.writeFile.side_effect = fake_pdb_writefile
        fake_pdbfile_inst = MagicMock(topology=MagicMock(), positions=MagicMock())
        fake_pdbfile_cls.side_effect = lambda path: fake_pdbfile_inst

        fake_modeller_inst = MagicMock(
            topology=MagicMock(
                getNumAtoms=MagicMock(return_value=42),
                getPeriodicBoxVectors=MagicMock(return_value=None),
            ),
            positions=MagicMock(),
        )
        fake_omm = {
            "openmm": MagicMock(XmlSerializer=MagicMock(serialize=MagicMock(return_value="<xml/>"))),
            "unit": MagicMock(nanometer=1, molar=1, kelvin=1, picoseconds=1, amu=1),
            "ForceField": MagicMock(return_value=MagicMock(createSystem=MagicMock(return_value=MagicMock()))),
            "HBonds": object(),
            "Modeller": MagicMock(return_value=fake_modeller_inst),
            "PDBFile": fake_pdbfile_cls,
            "PME": object(),
        }

        with patch.object(_prepare_mod, "_import_openmm", return_value=fake_omm):
            artifacts = _prepare_mod.prepare_system(mini_pdb, out)

        assert (out / "solvated.pdb").exists()
        assert (out / "topology.pdb").exists()
        assert (out / "system.xml").exists()
        assert (out / "state.xml").exists()
        # The files. Preparation also reports things the methods section has
        # to state and that were previously only logged -- the system size and
        # the box -- which are provenance rather than artifacts. Splitting on
        # what a value *is* rather than naming each exception keeps this from
        # needing an edit every time the record learns something new.
        assert artifacts["n_atoms_solvated"] == 42
        paths = {k for k, v in artifacts.items()
                 if isinstance(v, (str, Path))}
        assert paths == {
            "solvated_pdb", "topology_pdb", "system_xml", "state_xml"
        }
        assert "box" in artifacts


# ===========================================================================
# Setup pipeline.run() — graceful degradation
# ===========================================================================
class TestPipelineRun:
    def test_pdb_input_writes_manifest(self, stub_orchestrator):
        """A PDB file input always produces input.pdb + setup_parameters.json."""
        out_dir = stub_orchestrator.output_dir / "setup"
        out_dir.mkdir()
        artifacts = setup_run(orchestrator=stub_orchestrator, output_dir=out_dir)
        assert "input.pdb" in artifacts
        assert "setup_parameters.json" in artifacts
        manifest = json.loads((out_dir / "setup_parameters.json").read_text(encoding="utf-8"))
        assert manifest["phase"] == "setup"
        assert manifest["input"]["form"] == "pdb_file"

    def test_the_resolved_force_field_is_recorded(self, stub_orchestrator):
        """`auto` must never leave a run ambiguous about what it used."""
        out_dir = stub_orchestrator.output_dir / "setup"
        out_dir.mkdir()
        setup_run(orchestrator=stub_orchestrator, output_dir=out_dir)
        manifest = json.loads((out_dir / "setup_parameters.json").read_text(encoding="utf-8"))
        # Default uses the named selector; the resolved block records the XMLs.
        assert manifest["parameters"]["forcefield"] == "auto"
        resolved = manifest["resolved_forcefield"]
        assert resolved["source"] == "named"
        # What auto chose, recorded concretely rather than as the word "auto".
        assert resolved["name"] == "amber-openff"
        assert resolved["xmls"], "the manifest must name the XML files used"

    def test_openmm_parameterization_failure_writes_manifest(
        self, stub_orchestrator, mini_pdb
    ):
        """A missing OpenMM parameterization backend preserves actionable output."""
        out_dir = stub_orchestrator.output_dir / "setup"
        out_dir.mkdir()
        with (
            patch(
                "fastmdxplora.setup.prepare.prepare_system",
                side_effect=ImportError("openmm missing"),
            ),
            patch(
                "fastmdxplora.setup.pipeline.missing_dependencies",
                return_value=[MissingDependency("OpenMM", "openmm.app", "openmm")],
            ),
        ):
            artifacts = setup_run(
                orchestrator=stub_orchestrator,
                output_dir=out_dir,
                fixed_pdb=str(mini_pdb),
            )

        assert artifacts[-1] == "setup_parameters.json"
        manifest = json.loads(
            (out_dir / "setup_parameters.json").read_text(encoding="utf-8")
        )
        assert "conda install -c conda-forge openmm" in " ".join(manifest["notes"])

    @pytest.mark.skipif(HAS_SETUP_DEPS, reason="graceful-degradation path only")
    def test_skips_chemistry_when_deps_missing(self, stub_orchestrator):
        """Without PDBFixer/OpenMM installed, chemistry is skipped but manifest written."""
        out_dir = stub_orchestrator.output_dir / "setup"
        out_dir.mkdir()
        artifacts = setup_run(orchestrator=stub_orchestrator, output_dir=out_dir)
        # input.pdb + manifest, but no prepared/solvated/system/state
        assert "input.pdb" in artifacts
        assert "prepared.pdb" not in artifacts
        assert "system.xml" not in artifacts
        manifest = json.loads((out_dir / "setup_parameters.json").read_text(encoding="utf-8"))
        notes = " ".join(manifest["notes"])
        assert "PDBFixer" in notes or "OpenMM" in notes

    def test_user_options_override_defaults(self, stub_orchestrator):
        """User-supplied kwargs override DEFAULTS in the manifest.

        This verifies option *plumbing* (kwargs reach the recorded
        manifest), so the chemistry is mocked: whether a particular force
        field can template a given residue is a separate concern, exercised
        by the real end-to-end tests. Mocking keeps this test about the
        manifest, and avoids coupling it to force-field/residue matching.
        """
        out_dir = stub_orchestrator.output_dir / "setup"
        out_dir.mkdir()
        # Mock prepare_system so no real solvation/parameterization runs;
        # return an empty dict of produced artifacts.
        with patch.object(_prepare_mod, "prepare_system", return_value={}):
            setup_run(
                orchestrator=stub_orchestrator,
                output_dir=out_dir,
                ph=6.5,
                ion_concentration_M=0.25,
                force_field=["amber14-all.xml", "amber14/tip3pfb.xml"],
            )
        manifest = json.loads((out_dir / "setup_parameters.json").read_text(encoding="utf-8"))
        assert manifest["parameters"]["ph"] == 6.5
        assert manifest["parameters"]["ion_concentration_M"] == 0.25
        assert manifest["parameters"]["force_field"] == [
            "amber14-all.xml", "amber14/tip3pfb.xml"
        ]

    def test_sequence_input_records_fallback(self, tmp_path: Path):
        """Sequence input yields the manifest-only fallback with a clear note."""
        from unittest.mock import MagicMock as MM

        orch = MM()
        orch.system = "MKTAYIAKQRQISFVKSHFSRQ"
        orch.output_dir = tmp_path
        orch._presenter = None
        out_dir = tmp_path / "setup"
        out_dir.mkdir()
        artifacts = setup_run(orchestrator=orch, output_dir=out_dir)
        assert "input.sequence" in artifacts
        manifest = json.loads((out_dir / "setup_parameters.json").read_text(encoding="utf-8"))
        assert manifest["input"]["form"] == "sequence"
        assert any("structure predictor" in n for n in manifest["notes"])


# ===========================================================================
# Defaults
# ===========================================================================
class TestDefaults:
    def test_charmm36_protein_default(self):
        """Pipeline default uses the named charmm36 selector; raw list off."""
        assert DEFAULTS["forcefield"] == "auto"
        assert DEFAULTS["force_field"] is None

    def test_physiological_ionic_strength(self):
        assert DEFAULTS["ion_concentration_M"] == 0.15

    def test_hbond_constraints_for_2fs_timestep(self):
        assert DEFAULTS["constraints"] == "HBonds"

    def test_room_temperature(self):
        assert DEFAULTS["temperature_K"] == 300.0


# ===========================================================================
# Real end-to-end (skipped when deps absent)
# ===========================================================================
@pytest.mark.skipif(not HAS_SETUP_DEPS, reason="needs pdbfixer + openmm")
class TestRealSetup:
    """End-to-end test that actually runs PDBFixer + OpenMM.

    Skipped in the sandbox; runs on developer machines with the
    ``[md]`` extras (or conda-forge equivalents) installed.
    """

    def test_minimal_pdb_through_full_pipeline(self, mini_pdb, tmp_path):
        from fastmdxplora.setup.prepare import prepare_system
        from fastmdxplora.setup.pdbfix import fix_pdb_with_pdbfixer

        prepared = tmp_path / "prepared.pdb"
        fix_pdb_with_pdbfixer(str(mini_pdb), str(prepared), ph=7.0)
        assert prepared.exists()

        out = tmp_path / "out"
        artifacts = prepare_system(prepared, out)
        # All four artifacts on disk
        for key in ("solvated_pdb", "topology_pdb", "system_xml", "state_xml"):
            assert artifacts[key].exists()

        # Round-trip: System XML deserializes
        import openmm

        with artifacts["system_xml"].open() as fh:
            system = openmm.XmlSerializer.deserialize(fh.read())
        # The system has more particles than the input (we added water)
        assert system.getNumParticles() > 5

    def test_cutoff_larger_than_half_box_raises_clear_error(
        self, mini_pdb, tmp_path
    ):
        """A cutoff > half the box yields an actionable error, not OpenMM's.

        With small padding and a large cutoff, the periodic-box constraint
        is violated; the guard in prepare_system should raise a ValueError
        naming the cutoff, the box, and how to fix it.
        """
        from fastmdxplora.setup.prepare import prepare_system
        from fastmdxplora.setup.pdbfix import fix_pdb_with_pdbfixer

        prepared = tmp_path / "prepared.pdb"
        fix_pdb_with_pdbfixer(str(mini_pdb), str(prepared), ph=7.0)

        out = tmp_path / "out"
        with pytest.raises(ValueError, match="cutoff.*exceeds half"):
            prepare_system(
                prepared,
                out,
                solvent_padding_nm=0.4,   # tiny box
                nonbonded_cutoff_nm=1.5,  # cutoff > half the box
            )


class TestTheSoftwareDoesNotAdvertiseWhatItCannotDo:
    """A sequence is recognised and then refused, because building a structure
    from one needs a predictor this software does not carry.

    The message for an unrecognised input listed a one-letter sequence among
    the accepted forms. Anyone reading it would try one and reach a
    NotImplementedError two steps later, and the claim also found its way into
    a manuscript describing the software.
    """

    def test_an_unrecognised_input_is_told_what_actually_works(self) -> None:
        import pytest

        from fastmdxplora.setup.pipeline import _classify_input

        with pytest.raises(ValueError) as raised:
            _classify_input("no-such-structure.xyz")

        message = str(raised.value)
        assert ".pdb" in message and "4-character PDB ID" in message
        assert "or a one-letter amino-acid sequence." not in message

    def test_and_told_that_a_sequence_is_not_one_of_them(self) -> None:
        """Said rather than omitted, because a sequence is a reasonable thing
        to try and silence about it is its own confusion."""
        import pytest

        from fastmdxplora.setup.pipeline import _classify_input

        with pytest.raises(ValueError, match="cannot yet be built"):
            _classify_input("no-such-structure.xyz")

    def test_a_sequence_is_refused_with_what_to_do_instead(self) -> None:
        import pytest

        from fastmdxplora.setup.pipeline import _resolve_input

        with pytest.raises(NotImplementedError) as raised:
            _resolve_input("ACDEFGHIKLM", "sequence",
                           __import__("pathlib").Path("/tmp"))

        message = str(raised.value)
        assert "structure predictor" in message
        assert any(name in message for name in ("AlphaFold", "ESMFold"))


class TestSetupSaysWhatItChose:
    """`forcefield: auto` is not a force field until a registry says
    which, and a switching distance left unset is nine tenths of the
    cutoff -- a rule, not a number.

    Both were worked out inside the phase and went no further, so the
    file meant to reproduce the run wrote `null` for each and left the
    question to whatever version replayed it. What the phase decided is
    recorded now, and only what it decided: a value the caller supplied
    is already in the config that asked for it, and repeating it here
    would claim the phase had chosen something it merely accepted.
    """

    @staticmethod
    def _resolved(params=None, forcefield=None, prepared=None):
        from fastmdxplora.setup.pipeline import _resolved_settings

        return _resolved_settings(params or {}, forcefield or {}, prepared)

    def test_the_registry_choice_is_recorded(self) -> None:
        got = self._resolved(forcefield={
            "xmls": ["amber14-all.xml", "amber14/tip3p.xml"],
            "water_model": "tip3p"})
        assert got["force_field"] == ["amber14-all.xml", "amber14/tip3p.xml"]
        assert got["water_model"] == "tip3p"

    def test_what_the_caller_gave_is_left_alone(self) -> None:
        got = self._resolved(
            params={"water_model": "tip4pew", "force_field": ["mine.xml"]},
            forcefield={"xmls": ["amber14-all.xml"], "water_model": "tip3p"})
        assert "water_model" not in got
        assert "force_field" not in got

    def test_the_switching_distance_comes_from_preparation(self) -> None:
        got = self._resolved(prepared={"switch_distance_nm": 0.9})
        assert got["switch_distance_nm"] == 0.9

    def test_a_method_without_a_switching_function_records_none(self) -> None:
        """`None` means the question did not arise, and a key whose value
        is None says nothing a default did not already say."""
        got = self._resolved(prepared={"switch_distance_nm": None})
        assert "switch_distance_nm" not in got

    def test_the_small_molecule_force_field_follows_the_ligand(self) -> None:
        got = self._resolved(forcefield={
            "ligand": {"name": "BEN", "forcefield": "openff-2.2.1"}})
        assert got["ligand_forcefield"] == "openff-2.2.1"

    def test_the_ligand_name_is_never_written_back(self) -> None:
        """The pipeline resolves it, and where several ligands are found
        it becomes a list. The field is declared `str`, so a config
        carrying the list fails its own validation on replay. What was
        prepared is under `resolved_forcefield.ligand`, which is the shape
        that question already had.
        """
        from fastmdxplora.config.schema import PHASE_SCHEMAS

        assert PHASE_SCHEMAS["setup"].get("ligand_name").type is str
        got = self._resolved(
            params={"ligand_name": ["BEN", "LIG"]},
            forcefield={"ligand": {"name": ["BEN", "LIG"]}})
        assert "ligand_name" not in got

    def test_nothing_private_is_carried(self) -> None:
        """Setup injects `_retained_pdb` and its neighbours into its own
        parameters. The record is built from named values rather than by
        copying them, so there is nothing to filter."""
        got = self._resolved(params={
            "_retained_pdb": "/tmp/kept.pdb",
            "_reinstated_heterogens": ("BEN",),
            "_explained_heterogens": ("HOH",),
        })
        assert not any(name.startswith("_") for name in got)
