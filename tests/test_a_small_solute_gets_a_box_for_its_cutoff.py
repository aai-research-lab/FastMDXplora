"""A small solute gets a box its cutoff fits in, and the record says so.

The defaults (1.0 nm of padding, a dodecahedron, a 1.0 nm cutoff) could not
prepare a peptide of a few residues: OpenMM sizes a padded box as the
solute's bounding sphere plus the padding, or twice the padding for a solute
smaller than that, so the box came out 1.41 nm at its narrowest where the
cutoff needs 2.2 with room for the barostat. Setup grew the padding by at
most 0.5 nm, 0.56 was needed, and it failed with "box edge 1.41 nm" of a box
whose edges were 2.0. Found in the MDXplora rehearsal (alanine dipeptide),
and again building this release's GUI (tri-alanine).

Where the padding sizes the box, the box grown to is the smallest the cutoff
allows for any solute that small, so it is grown to; the padding used is
recorded and the methods say it, with what the padding measures.
"""

from __future__ import annotations

import pytest

from tests.test_setup_phase import MINI_PDB


@pytest.fixture(scope="module")
def prepared(tmp_path_factory):
    pytest.importorskip("openmm")
    pytest.importorskip("pdbfixer")
    from fastmdxplora.setup.pdbfix import fix_pdb_with_pdbfixer
    from fastmdxplora.setup.prepare import prepare_system

    root = tmp_path_factory.mktemp("small")
    (root / "mini.pdb").write_text(MINI_PDB)
    fixed = root / "prepared.pdb"
    fix_pdb_with_pdbfixer(str(root / "mini.pdb"), str(fixed), ph=7.0)
    return prepare_system(fixed, root / "out", box_shape="dodecahedron",
                          solvent_padding_nm=1.0, nonbonded_cutoff_nm=1.0)


def test_the_defaults_prepare_a_tripeptide(prepared) -> None:
    assert prepared["box"]["smallest_width_nm"] == pytest.approx(2.2, abs=1e-3)


def test_the_padding_used_is_recorded(prepared) -> None:
    # 1.1 / (sqrt(2) / 2): half the size the cutoff needs.
    assert prepared["resolved"]["solvent_padding_nm"] == pytest.approx(1.5556, abs=1e-4)


class TestTheMethodsSayIt:

    def _methods(self, tmp_path, setup):
        from fastmdxplora.report.methods import methods_paragraphs

        return methods_paragraphs(tmp_path, setup, {})

    def test_what_the_padding_measures(self, tmp_path) -> None:
        text = self._methods(tmp_path, {"parameters": {
            "solvent_padding_nm": 1.2, "box_shape": "dodecahedron",
            "ion_concentration_M": 0.15}})
        assert ("solvated in a dodecahedron box sized to leave at least 1.20 nm "
                "between the solute and its nearest periodic image") in text
        assert "grown from" not in text

    def test_a_grown_padding_is_the_one_given(self, tmp_path) -> None:
        text = self._methods(tmp_path, {
            "parameters": {"solvent_padding_nm": 1.0, "box_shape": "dodecahedron"},
            "resolved": {"solvent_padding_nm": 1.5556}})
        assert ("at least 1.56 nm between the solute and its nearest periodic image "
                "(grown from the 1.00 nm asked for, so that the box is at least "
                "twice the cutoff across)") in text


def test_the_setting_says_what_it_measures() -> None:
    from fastmdxplora.config.schema import PHASE_SCHEMAS

    help_text = PHASE_SCHEMAS["setup"].get("solvent_padding_nm").help
    assert "nearest periodic image" in help_text
    assert "box wall" not in help_text


def test_a_refusal_of_a_box_names_its_narrowest_width() -> None:
    """Not an edge: a dodecahedron's narrowest width is shorter than its edges."""
    from fastmdxplora.setup.prepare import _what_would_fit

    text = _what_would_fit(smallest_nm=2.4, padding_nm=0.4, nonbonded_cutoff_nm=1.5,
                           box_shape="cube")
    assert "is 2.40 nm at its narrowest" in text
    assert "solvent_padding_nm: 1.30" in text
    assert "box_shape: cube" not in text
    text = _what_would_fit(smallest_nm=1.2, padding_nm=0.4, nonbonded_cutoff_nm=1.5,
                           box_shape="dodecahedron")
    assert "or box_shape: cube with solvent_padding_nm:" in text


class TestARetryStartsFromTheSolute:
    """A retry deleted the water and kept the ions the attempt had added:
    they counted as solute and sized the next box by where they landed, and
    stayed beside the next attempt's own."""

    @pytest.fixture(scope="class")
    def built(self, tmp_path_factory):
        pytest.importorskip("openmm")
        pytest.importorskip("pdbfixer")
        from openmm import app

        from fastmdxplora.setup.pdbfix import fix_pdb_with_pdbfixer
        from fastmdxplora.setup.prepare import prepare_system

        root = tmp_path_factory.mktemp("retry")
        (root / "mini.pdb").write_text(MINI_PDB)
        fixed = root / "prepared.pdb"
        fix_pdb_with_pdbfixer(str(root / "mini.pdb"), str(fixed), ph=7.0)

        def build(name, padding):
            made = prepare_system(fixed, root / name, box_shape="dodecahedron",
                                  solvent_padding_nm=padding, nonbonded_cutoff_nm=1.0)
            topology = app.PDBFile(str(made["topology_pdb"])).topology
            counts = {}
            for residue in topology.residues():
                counts[residue.name] = counts.get(residue.name, 0) + 1
            return made["box"]["smallest_width_nm"], counts

        return {"grown": build("grown", 1.0), "again": build("again", 1.0),
                "direct": build("direct", 1.5556)}

    def test_the_same_study_builds_the_same_box(self, built) -> None:
        assert built["grown"][0] == pytest.approx(built["again"][0], abs=1e-9)
        assert built["grown"][1] == built["again"][1]

    def test_a_grown_box_is_the_box_built_at_that_padding(self, built) -> None:
        grown, direct = built["grown"], built["direct"]
        assert grown[0] == pytest.approx(direct[0], abs=1e-3)
        assert (grown[1].get("NA"), grown[1].get("CL")) == (direct[1].get("NA"),
                                                             direct[1].get("CL"))
