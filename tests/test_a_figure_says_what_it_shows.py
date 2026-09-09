"""What a figure claims, and whether a reader can act on it.

Three defects found by printing the software's own output and looking at it
rather than by reading its source, 2026-09-09, on a finished
trypsin-benzamidine run.
"""

from __future__ import annotations

import matplotlib
import numpy as np
import pandas as pd
import pytest

matplotlib.use("Agg")

from matplotlib.ticker import MaxNLocator  # noqa: E402

# `_is_a_fraction_axis` and `colour` are imported inside the tests that use
# them, not here. A module-scope import of a symbol the fix introduces turns
# "the fix is missing" into a collection error that takes the file down and
# names no assertion -- the failure mode patch 0009 recorded.
from fastmdxplora.analysis.plotting import _finalise_axes, new_figure  # noqa: E402


def _interactions_table():
    """The twenty rows the real run drew, with their atom pairs.

    Fourteen of these share a residue with another row: four hydrogen bonds
    to ASP189, five hydrophobic contacts to TRP215, four to GLN192, two to
    VAL213.
    """
    rows = [
        ("hydrogen_bond", "ASP189", "OD1", "N1", 0.985, 0.010, True),
        ("hydrogen_bond", "ASP189", "OD2", "N2", 0.972, 0.012, True),
        ("hydrogen_bond", "GLY219", "O", "N2", 0.968, 0.035, True),
        ("hydrogen_bond", "SER190", "OG", "N1", 0.915, 0.019, True),
        ("hydrogen_bond", "ASP189", "OD1", "N2", 0.856, 0.026, True),
        ("hydrophobic", "VAL213", "CG1", "C4", 0.355, 0.021, True),
        ("hydrophobic", "VAL213", "CG2", "C5", 0.264, 0.019, True),
        ("hydrophobic", "TRP215", "CZ2", "C3", 0.232, 0.020, True),
        ("hydrophobic", "TRP215", "CH2", "C4", 0.150, 0.016, True),
        ("hydrophobic", "TRP215", "CZ3", "C2", 0.147, 0.017, True),
        ("hydrophobic", "GLN192", "CB", "C6", 0.070, 0.011, True),
        ("hydrophobic", "GLN192", "CG", "C1", 0.063, 0.010, True),
        ("hydrophobic", "TRP215", "CE2", "C6", 0.061, 0.011, True),
        ("hydrophobic", "GLN192", "CD", "C5", 0.050, 0.010, True),
        ("hydrogen_bond", "GLY216", "O", "N1", 0.022, 0.008, False),
        ("hydrogen_bond", "ASP189", "OD2", "N1", 0.019, 0.008, False),
        ("hydrophobic", "TRP215", "CD2", "C1", 0.017, 0.007, False),
        ("hydrophobic", "GLN192", "CA", "C2", 0.016, 0.007, False),
        ("hydrogen_bond", "GLY226", "O", "N2", 0.009, 0.005, False),
        ("hydrogen_bond", "CYS220", "SG", "N1", 0.008, 0.005, False),
    ]
    return pd.DataFrame(rows, columns=[
        "kind", "residue", "protein_atom_name", "ligand_atom_name",
        "occupancy", "standard_error", "well_sampled"])


class TestEveryRowCanBeToldFromEveryOther:
    """A row a reader cannot identify is a row they cannot cite."""

    def test_no_two_rows_share_a_label(self):
        from fastmdxplora.analysis.pl_interactions import _row_label

        table = _interactions_table()
        labels = [_row_label(row) for row in table.itertuples()]

        assert len(set(labels)) == len(labels), (
            "Rows sharing a label cannot be told apart, cannot be cited, and "
            "give a reader nothing to warn them that rows on one residue are "
            "different atom pairs and must not be summed.")

    def test_the_label_names_both_atoms(self):
        from fastmdxplora.analysis.pl_interactions import _row_label

        row = next(_interactions_table().itertuples())
        label = _row_label(row)

        assert "ASP189" in label
        assert "OD1" in label and "N1" in label

    def test_a_table_without_atom_names_still_labels_something(self):
        """An older table, or one written before the names were recorded."""
        from fastmdxplora.analysis.pl_interactions import _row_label

        older = pd.DataFrame([{"kind": "hydrogen_bond", "residue": "ASP189"}])
        label = _row_label(next(older.itertuples()))

        assert "ASP189" in label

    def test_nan_atom_names_do_not_reach_the_label(self):
        from fastmdxplora.analysis.pl_interactions import _row_label

        table = pd.DataFrame([{
            "kind": "hydrophobic", "residue": "VAL213",
            "protein_atom_name": np.nan, "ligand_atom_name": np.nan}])
        label = _row_label(next(table.itertuples()))

        assert "nan" not in label.lower()

    def test_the_compute_path_records_the_atom_names(self):
        """The columns the label needs are declared, empty result included."""
        from fastmdxplora.analysis.pl_interactions import (
            ProteinLigandInteractions)
        import inspect

        source = inspect.getsource(ProteinLigandInteractions)
        assert 'record["protein_atom_name"]' in source
        assert 'record["ligand_atom_name"]' in source
        # The empty-result frame declares the same columns, or a caller that
        # found nothing gets a differently-shaped table than one that did.
        empty = source.split("if not occupancies:")[1].split("]")[0]
        assert "protein_atom_name" in empty and "ligand_atom_name" in empty


class TestAFractionAxisIsLabelledAtItsEnd:
    """Twelve bars at 1.00 above a scale whose last number was 0.90."""

    def test_a_zero_to_one_axis_is_recognised(self):
        from fastmdxplora.analysis.plotting import _is_a_fraction_axis

        fig, ax = new_figure(title="t")
        ax.set_xlim(0, 1)
        assert _is_a_fraction_axis(ax, "x") is True

    def test_an_ordinary_axis_is_not(self):
        from fastmdxplora.analysis.plotting import _is_a_fraction_axis

        fig, ax = new_figure(title="t")
        ax.set_xlim(0, 100)
        assert _is_a_fraction_axis(ax, "x") is False

    def test_the_end_of_the_axis_gets_a_tick(self):
        fig, ax = new_figure(title="t")
        ax.barh([0, 1, 2], [1.0, 0.99, 0.5])
        ax.set_xlim(0, 1)
        _finalise_axes(ax)

        ticks = ax.xaxis.get_major_locator().tick_values(0, 1)
        assert np.any(np.isclose(ticks, 1.0)), (
            "A fraction axis whose last label is 0.90 cannot be read where "
            "the bars are, which is at the top of the scale.")

    def test_the_default_locator_is_what_went_wrong(self):
        """The defect itself, pinned so it cannot come back unnoticed.

        Reverting `plotting.py` to show these failing is not possible in
        isolation -- sixteen analyses now import `colour` from it, so the
        change moves as one. This asserts the behaviour that was wrong
        instead, which is the more durable statement anyway.
        """
        ticks = MaxNLocator(nbins=7).tick_values(0, 1)

        assert (ticks[1] - ticks[0]) == pytest.approx(0.15)
        assert not np.any(np.isclose(ticks, 1.0)), (
            "Matplotlib's default step set includes 1.5, so a 0-1 axis at "
            "this tick budget stops labelling at 0.90. That is what put "
            "twelve bars reaching 1.00 above a scale that never said so.")

    def test_a_torsion_axis_keeps_its_sixties(self):
        """Why this is not applied to every axis.

        A degree axis wants 60, which needs the step sizes the fraction rule
        drops. Restricting them everywhere would fix one figure and spoil
        every Ramachandran and dihedral plot in the package.
        """
        ticks = MaxNLocator(nbins=7).tick_values(-180, 180)
        assert np.any(np.isclose(ticks, 180.0))
        assert (ticks[1] - ticks[0]) == pytest.approx(60.0)


class TestATitleNamesWhatIsDrawn:

    def test_the_contact_figure_does_not_promise_a_count(self):
        from fastmdxplora.analysis.contacts import Contacts

        analysis = Contacts.__new__(Contacts)
        analysis._user_title = None
        title = analysis.figure_title()

        assert "count" not in title.lower(), (
            "The figure draws the per-residue frequency only; the per-frame "
            "count is in pl_contacts.dat. A title naming both sends a reader "
            "looking in the figure for something that is not in it.")
        assert "frequency" in title.lower()

    def test_the_analysis_still_describes_both(self):
        from fastmdxplora.analysis.contacts import Contacts

        assert "count" in Contacts.description.lower()


class TestOneModuleDecidesWhatThingsLookLike:
    """`plotting` calls itself a single point of style configuration.

    It was not one. Twenty-eight hardcoded hex colours across `analysis/*.py`
    bypassed the palette it installs, and disagreed with each other: four
    different blues appeared in one report. Roles fix the instances; the
    structural test below closes the class, so the next one cannot be
    written rather than being found in a printed figure months later.
    """

    #: Keywords whose value decides what colour something is drawn in.
    COLOUR_KEYWORDS = frozenset({
        "color", "colour", "edgecolor", "markeredgecolor", "ecolor",
        "facecolor", "markerfacecolor", "labelcolor",
    })

    def _offenders(self):
        import ast
        import pathlib

        import fastmdxplora.analysis as package

        root = pathlib.Path(package.__file__).parent
        found = []
        for path in sorted(root.glob("*.py")):
            if path.name == "plotting.py":
                continue  # where the values are allowed to live
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    for keyword in node.keywords:
                        if keyword.arg not in self.COLOUR_KEYWORDS:
                            continue
                        value = keyword.value
                        if (isinstance(value, ast.Constant)
                                and isinstance(value.value, str)
                                and value.value.startswith("#")):
                            found.append(
                                f"{path.name}:{value.lineno} "
                                f"{keyword.arg}={value.value!r}")
                elif isinstance(node, ast.Dict):
                    for key, value in zip(node.keys, node.values):
                        if not (isinstance(key, ast.Constant)
                                and key.value in self.COLOUR_KEYWORDS):
                            continue
                        if (isinstance(value, ast.Constant)
                                and isinstance(value.value, str)
                                and value.value.startswith("#")):
                            found.append(
                                f"{path.name}:{value.lineno} "
                                f"{key.value!r}: {value.value!r}")
        return found

    def test_no_analysis_names_a_colour_of_its_own(self):
        offenders = self._offenders()
        assert offenders == [], (
            "These name a colour instead of a role. Use "
            "`plotting.colour(\"SERIES\")` and its siblings, so the palette "
            "is decided in one place and greyscale reaches every figure:\n  "
            + "\n  ".join(offenders))

    def test_a_role_answers_differently_in_greyscale(self):
        from fastmdxplora.analysis import plotting

        try:
            in_colour = plotting.colour("SERIES")
            plotting.use_greyscale(True)
            in_grey = plotting.colour("SERIES")
        finally:
            plotting.use_greyscale(False)

        assert in_colour != in_grey
        assert plotting.colour("SERIES") == in_colour

    def test_an_unknown_role_is_refused_rather_than_defaulted(self):
        from fastmdxplora.analysis import plotting

        with pytest.raises(KeyError) as caught:
            plotting.colour("SUBTLE_BLUE")
        assert "SERIES" in str(caught.value), (
            "The refusal should name the roles that do exist; a silent "
            "fallback is how a figure gets drawn in a colour nobody chose.")

    def test_every_role_has_both_a_colour_and_a_grey(self):
        from fastmdxplora.analysis import plotting

        assert (set(plotting._ROLES_IN_COLOUR)
                == set(plotting._ROLES_IN_GREY)), (
            "A role defined in one table and not the other is a figure that "
            "changes meaning when greyscale is switched on.")


class TestAskingForBothColourAndGreyscale:
    """A paper wants colour online and greyscale in print.

    Deciding before the journal is chosen means re-running the analysis to
    change it. Drawing both costs one extra render.
    """

    def _run_one(self, tmp_path, **kwargs):
        import mdtraj as md
        from fastmdxplora.analysis.orchestrator import AnalysisOrchestrator

        top = md.Topology()
        chain = top.add_chain()
        residue = top.add_residue("ALA", chain)
        for index in range(6):
            top.add_atom("CA" if index == 0 else f"C{index}",
                         md.element.carbon, residue)
        traj = md.Trajectory(
            np.random.RandomState(1).rand(40, 6, 3).astype(np.float32), top)
        traj.time = (np.arange(40) + 1) * 10.0
        traj.save_dcd(str(tmp_path / "t.dcd"))
        traj[0].save_pdb(str(tmp_path / "t.pdb"))

        orchestrator = AnalysisOrchestrator(
            trajectory=str(tmp_path / "t.dcd"),
            topology=str(tmp_path / "t.pdb"),
            output_dir=tmp_path / "out",
            saving_interval_ps=10.0,
            **kwargs)
        results = orchestrator.run(include=["rg"])
        drawn = sorted(p.name for p in (tmp_path / "out" / "rg").glob("*.png"))
        return results["rg"], drawn

    def test_colour_alone_writes_one_figure(self, tmp_path):
        result, drawn = self._run_one(tmp_path, figure_colours="colour")
        assert drawn == ["rg.png"]
        assert result.figure_path.name == "rg.png"

    def test_greyscale_alone_still_writes_the_primary_name(self, tmp_path):
        """Not `rg_greyscale.png`: the report looks for `rg.png`."""
        result, drawn = self._run_one(tmp_path, figure_colours="greyscale")
        assert drawn == ["rg.png"]

    def test_both_writes_the_colour_figure_and_a_grey_copy(self, tmp_path):
        result, drawn = self._run_one(tmp_path, figure_colours="both")

        assert drawn == ["rg.png", "rg_greyscale.png"]
        # The primary keeps its meaning, so a reader that knows only about
        # figure_path is unaffected by asking for both.
        assert result.figure_path.name == "rg.png"
        names = {p.name for p in result.artifacts}
        assert {"rg.png", "rg_greyscale.png"} <= names

    def test_the_default_is_colour(self, tmp_path):
        result, drawn = self._run_one(tmp_path)
        assert drawn == ["rg.png"]

    def test_an_american_spelling_is_accepted(self, tmp_path):
        """`centres`/`centers` was the fourth time; this is not the fifth."""
        result, drawn = self._run_one(tmp_path, figure_colours="grayscale")
        assert drawn == ["rg.png"]

    def test_the_mode_is_restored_even_when_drawing_raises(self):
        from fastmdxplora.analysis import plotting

        before = plotting.greyscale_is_on()
        with pytest.raises(RuntimeError):
            with plotting.drawn_in("greyscale"):
                raise RuntimeError("boom")
        assert plotting.greyscale_is_on() is before, (
            "Process-wide state left switched by an exception would draw "
            "every later figure in a mode nobody asked for.")

    def test_a_misspelling_is_refused_by_name(self):
        from fastmdxplora.analysis.plotting import settle_figure_colours

        with pytest.raises(ValueError) as caught:
            settle_figure_colours("rainbow")
        message = str(caught.value)
        assert "rainbow" in message
        for accepted in ("colour", "greyscale", "both"):
            assert accepted in message

    def test_the_setting_reaches_every_interface(self):
        """One declaration, three interfaces -- the batch 3 lesson.

        `execution.mode` had its accepted values written twice and reached
        neither the parser nor the form. This asserts the declaration is the
        single source, so the flag and the form control come from it.
        """
        from fastmdxplora.config.schema import ANALYSIS

        field = next(f for f in ANALYSIS.fields
                     if f.name == "figure_colours")
        assert field.choices == ("colour", "greyscale", "both")
        assert field.default == "colour"
