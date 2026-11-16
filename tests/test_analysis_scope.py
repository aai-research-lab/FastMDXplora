"""Tests for the analysis `scope` setting.

`scope` resolves to a default atom selection for analyses that don't define
their own (the solvent-blind ones: rg, sasa, ss, qvalue, hbonds), so they
never run on the full solvated system (water + ions) by accident. Analyses
with their own meaningful default (e.g. "name CA") keep it. An explicit
per-analysis or orchestrator-wide `selection` overrides the scope.

These tests exercise the resolver and the orchestrator's selection-assignment
logic without running real MD: a tiny in-memory trajectory is enough to drive
the orchestrator, and we capture the selection each analysis is built with.
"""

from __future__ import annotations

import sys

import numpy as np
import pytest

from fastmdxplora.analysis.orchestrator import (
    VALID_SCOPES,
    AnalysisOrchestrator,
    _resolve_scope,
    register_analysis,
)
from fastmdxplora.analysis.base import Analysis


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------
class TestResolveScope:
    def test_solute_no_ligand_is_protein(self):
        assert _resolve_scope("solute", None) == "protein"

    def test_solute_with_ligand(self):
        assert _resolve_scope("solute", "LIG") == 'protein or resname "LIG"'

    def test_protein(self):
        assert _resolve_scope("protein", "LIG") == "protein"

    def test_ligand(self):
        assert _resolve_scope("ligand", "DRG") == 'resname "DRG"'

    def test_all_is_none(self):
        assert _resolve_scope("all", None) is None

    def test_unknown_scope_raises(self):
        with pytest.raises(ValueError, match="Unknown analysis scope"):
            _resolve_scope("bogus", None)

    def test_ligand_scope_without_ligand_raises(self):
        with pytest.raises(ValueError, match="requires a ligand"):
            _resolve_scope("ligand", None)

    def test_case_insensitive(self):
        assert _resolve_scope("PROTEIN", None) == "protein"

    def test_valid_scopes_constant(self):
        assert VALID_SCOPES == ("solute", "protein", "ligand", "all")


# ---------------------------------------------------------------------------
# Orchestrator selection assignment (the actual behavior under test)
# ---------------------------------------------------------------------------
# Two probe analyses that record the selection they were constructed with:
# one with no own default (like rg/sasa/qvalue) and one with a "name CA"
# default (like rmsd/cluster).
_CAPTURED: dict[str, str | None] = {}


class _ProbeNoDefault(Analysis):
    name = "probe_nodefault"
    description = "probe (no own default selection)"
    default_selection = None

    def __init__(self, **kwargs):
        _CAPTURED["probe_nodefault"] = kwargs.get("selection")
        super().__init__(**kwargs)

    def compute(self, traj):
        return np.zeros(traj.n_frames)

    def plot(self, result, ax):
        pass


class _ProbeCADefault(Analysis):
    name = "probe_ca"
    description = "probe (name CA default)"
    default_selection = "name CA"

    def __init__(self, **kwargs):
        _CAPTURED["probe_ca"] = kwargs.get("selection")
        super().__init__(**kwargs)

    def compute(self, traj):
        return np.zeros(traj.n_frames)

    def plot(self, result, ax):
        pass


@pytest.fixture(autouse=True)
def _register_probes():
    """Register the probe analyses for the duration of each test, then remove
    them so they don't leak into the global registry (other tests assert the
    exact set of registered analyses)."""
    from fastmdxplora.analysis import orchestrator as _orch

    _CAPTURED.clear()
    try:
        register_analysis("probe_nodefault", _ProbeNoDefault)
        register_analysis("probe_ca", _ProbeCADefault)
    except Exception:
        pass
    yield
    _orch._REGISTRY.pop("probe_nodefault", None)
    _orch._REGISTRY.pop("probe_ca", None)


@pytest.fixture
def tiny_traj(tmp_path):
    """A 2-frame, 2-residue PDB trajectory good enough to drive the orchestrator."""

    pdb = tmp_path / "tiny.pdb"
    pdb.write_text(
        "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00  0.00           C\n"
        "ATOM      2  CA  GLY A   2       3.800   0.000   0.000  1.00  0.00           C\n"
        "END\n"
    )
    return str(pdb)


@pytest.mark.parametrize("names", ["3GR", '"3GR"', "'3GR'", "LIG 3GR", '"LIG" "3GR"'])
def test_digit_leading_names_select_every_named_ligand(names):
    import mdtraj as md

    top = md.Topology()
    chain = top.add_chain()
    for name in ("ALA", "LIG", "3GR", "HOH"):
        top.add_atom("CA", md.element.carbon, top.add_residue(name, chain))
    wanted = [1, 2] if "LIG" in names else [2]
    assert top.select(_resolve_scope("ligand", names)).tolist() == wanted
    assert top.select(_resolve_scope("solute", names)).tolist() == [0, *wanted]
    from fastmdxplora.analysis.residues import resname_selection

    assert top.select(resname_selection(names)).tolist() == wanted


class TestScopeAssignment:
    def _run(self, tiny_traj, tmp_path, **kwargs):
        ao = AnalysisOrchestrator(
            tiny_traj, output_dir=tmp_path / "out", **kwargs
        )
        ao.run(include=["probe_nodefault", "probe_ca"])
        return dict(_CAPTURED)

    def test_solute_scope_fills_nodefault_only(self, tiny_traj, tmp_path):
        cap = self._run(tiny_traj, tmp_path, scope="solute")
        # No-default analysis picks up the scope selection (protein, no ligand).
        assert cap["probe_nodefault"] == "protein"
        # CA-default analysis keeps its own default (scope does not clobber).
        assert cap["probe_ca"] is None  # base class applies "name CA" later

    def test_ligand_scope_uses_resname(self, tiny_traj, tmp_path):
        cap = self._run(tiny_traj, tmp_path, scope="solute", ligand_resname="LIG")
        assert cap["probe_nodefault"] == 'protein or resname "LIG"'

    def test_protein_scope(self, tiny_traj, tmp_path):
        cap = self._run(tiny_traj, tmp_path, scope="protein")
        assert cap["probe_nodefault"] == "protein"

    def test_all_scope_is_none(self, tiny_traj, tmp_path):
        cap = self._run(tiny_traj, tmp_path, scope="all")
        # 'all' resolves to None -> no-default analysis runs on all atoms.
        assert cap["probe_nodefault"] is None

    def test_explicit_selection_overrides_scope(self, tiny_traj, tmp_path):
        cap = self._run(tiny_traj, tmp_path, scope="protein", selection="name CA")
        # Orchestrator-wide selection wins over scope for the no-default one.
        assert cap["probe_nodefault"] == "name CA"


# ---------------------------------------------------------------------------
# Regression: solvent-blind analyses must SLICE to the selection before the
# expensive computation, not just carry the selection string. This is the
# fix for the hang on solvated systems (qvalue enumerating water residue
# pairs). Uses a real protein+water trajectory.
# ---------------------------------------------------------------------------
@pytest.fixture
def protein_water_traj(tmp_path):
    """A 2-frame trajectory: 6 ALA protein residues + 200 waters."""
    import mdtraj as md

    lines = []
    ai = 1
    # Give each atom a distinct position; SASA hard-aborts if any two atoms
    # are exactly superimposed, so spread them in 3D.
    offsets = {"N": (0.0, 0.0, 0.0), "CA": (1.5, 0.0, 0.0),
               "C": (1.5, 1.5, 0.0), "O": (0.0, 1.5, 1.0)}
    for ri in range(1, 7):
        base_z = ri * 3.8
        for nm, el in [("N", "N"), ("CA", "C"), ("C", "C"), ("O", "O")]:
            dx, dy, dz = offsets[nm]
            lines.append(
                f"ATOM  {ai:5d}  {nm:<3s} ALA A{ri:4d}    "
                f"{ri*4.0+dx:8.3f}{dy:8.3f}{base_z+dz:8.3f}  1.00  0.00           {el}"
            )
            ai += 1
    for wi in range(1, 201):
        lines.append(
            f"ATOM  {ai:5d}  O   HOH B{wi:4d}    "
            f"{50.0+wi*0.5:8.3f}{50.0:8.3f}{50.0:8.3f}  1.00  0.00           O"
        )
        ai += 1
    lines.append("END")
    pdb = tmp_path / "pw.pdb"
    pdb.write_text("\n".join(lines) + "\n")
    traj = md.load(str(pdb))
    return md.join([traj, traj])


class TestSolventBlindAnalysesSlice:
    def test_qvalue_slices_to_protein(self, protein_water_traj, tmp_path):
        """qvalue must operate on the 6 protein residues, not 206 total —
        proving it slices BEFORE enumerating residue pairs (the hang fix)."""
        from fastmdxplora.analysis.qvalue import QValue

        assert protein_water_traj.n_residues == 206
        qv = QValue(selection="protein", output_dir=tmp_path / "qv")
        result = qv.compute(protein_water_traj)  # would be huge/hang if unsliced
        assert np.asarray(result).shape == (2,)

    @pytest.mark.xfail(
        sys.platform == "win32",
        reason=(
            "Computes surface areas for real, so it meets the MDTraj "
            "defect that returns unwritten frames on Windows. Not "
            "strict: which calls are affected varies, so the test "
            "passes whenever the retry finds a complete answer."
        ),
        strict=False,
    )
    def test_sasa_slices_to_protein(self, protein_water_traj, tmp_path):
        from fastmdxplora.analysis.sasa import SASA

        sasa = SASA(selection="protein", output_dir=tmp_path / "sasa")
        df = sasa.compute(protein_water_traj)
        assert len(df) == 2  # 2 frames, total mode

    def test_hbonds_slices_to_protein(self, protein_water_traj, tmp_path):
        from fastmdxplora.analysis.hbonds import HBonds

        hb = HBonds(selection="protein", output_dir=tmp_path / "hb")
        df = hb.compute(protein_water_traj)
        assert len(df) == 2

    def test_rg_already_slices(self, protein_water_traj, tmp_path):
        from fastmdxplora.analysis.rg import Rg

        rg = Rg(selection="protein", output_dir=tmp_path / "rg")
        result = rg.compute(protein_water_traj)
        assert np.asarray(result).shape[0] == 2
class TestScopeSchemaAndCLI:
    def test_schema_has_scope(self):
        from fastmdxplora.config.schema import PHASE_SCHEMAS
        f = PHASE_SCHEMAS["analysis"].get("scope")
        assert f is not None
        assert f.default == "solute"

    def test_cli_has_scope(self):
        from fastmdxplora.cli.main import _ANALYSIS_OPTIONS, _build_parser

        assert any(flag == "scope" for flag, _kw, _opts in _ANALYSIS_OPTIONS), (
            "--scope not found in _ANALYSIS_OPTIONS"
        )
        # The accepted values are asked of the parser, not the option table.
        # The schema declares them once and the parser reads them from there,
        # so the table no longer carries a copy -- and the parser is what a
        # user meets.
        analyze = _build_parser()._subparsers._group_actions[0].choices["analyze"]
        choices = next(a.choices for a in analyze._actions if a.dest == "scope")
        assert set(choices) == {"solute", "protein", "ligand", "all"}
