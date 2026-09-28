"""An explanation nobody can see is not an explanation.

The README says "Every step explains itself and cites the paper worth
reading", and `docs/how_it_works.md` and the manuscript both state a count of
sixteen. Sixteen is the size of the table; it is not how many can reach a
user. An entry is only ever printed if some call site names its key --
`presenter.step(..., explain="protonation")`, or `on_explain("minimize")`
-- and eight of the sixteen name nothing.

This file exists to stop the number drifting. It was reported as seven
unreachable, then eleven, then five, in the course of one afternoon,
because each count was taken by a different hand-written regex over the
source rather than by one function everyone uses. It was eight, then seven,
and now none: each is said beneath the step it explains. It is computed
here once.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from fastmdxplora.explain import EXPLANATIONS

SRC = pathlib.Path(__file__).resolve().parents[1] / "src"

#: Those with no call site, named so that adding one is a visible change to
#: this set rather than a silent drop in a number. Empty: every explanation
#: is said beneath the step it explains.
UNWIRED: set[str] = set()


def _keys_named_anywhere() -> set[str]:
    """Every explanation key some call site actually asks for.

    Every spelling in use: the keyword form on `presenter.step`/`info`,
    `presenter.explanation` beneath output that is not a step, and
    `on_explain` in the simulation runner --
    which also reaches three keys through the `_STAGE_EXPLANATIONS` table
    rather than by literal. A count that misses one form undercounts, which
    is how "five" was reported for what is eight.
    """
    blob = "\n".join(
        path.read_text(encoding="utf-8")
        for path in SRC.rglob("*.py")
        if path.name != "explain.py"
    )
    named = set(re.findall(r'(?:on_explain|_explain|\.explanation)\(\s*["\'](\w+)["\']', blob))
    named |= set(re.findall(r'explain=\(?["\'](\w+)["\']', blob))
    named |= set(re.findall(r'else\s+["\'](\w+)["\']\)', blob))
    table = re.search(r"_STAGE_EXPLANATIONS\s*[:=][^{]*\{(.*?)\}", blob, re.S)
    if table:
        named |= set(re.findall(r':\s*["\'](\w+)["\']', table.group(1)))
    return named & set(EXPLANATIONS)


class TestTheCountIsWhatItIs:

    def test_the_unwired_list_is_accurate(self) -> None:
        """Both directions, so the list cannot rot in either."""
        assert set(EXPLANATIONS) - _keys_named_anywhere() == UNWIRED

    def test_wiring_one_means_taking_it_off_the_list(self) -> None:
        assert not (UNWIRED & _keys_named_anywhere()), (
            "an explanation on the unwired list now has a call site; take it "
            "off the list, and update the count wherever it is stated"
        )

    def test_a_new_explanation_arrives_wired_or_listed(self) -> None:
        """The failure mode this file is for. An entry added to the table
        with no call site currently changes nothing a user sees, and the
        stated count goes up regardless."""
        accounted = _keys_named_anywhere() | UNWIRED
        assert set(EXPLANATIONS) <= accounted, (
            f"neither wired nor listed: {sorted(set(EXPLANATIONS) - accounted)}"
        )


class TestWhatIsWiredActuallyResolves:

    @pytest.mark.parametrize("key", sorted(_keys_named_anywhere()))
    def test_the_key_a_call_site_names_exists(self, key: str) -> None:
        """The other direction of the same defect: a call site naming a key
        the table does not have prints nothing and says nothing."""
        from fastmdxplora.explain import explain

        assert explain(key) is not None

    @pytest.mark.parametrize("key", sorted(_keys_named_anywhere()))
    def test_it_has_something_to_say(self, key: str) -> None:
        entry = EXPLANATIONS[key]
        assert entry.as_text().strip()


class TestTheOnesWithoutACitation:
    """`minimize` and `production` carried no reference. They now cite the
    two LiveCoMS best-practice papers that cover them: Braun et al. 2019 on
    preparing a system, and Grossfield et al. 2018 on what a trajectory
    supports. Pinned, so an entry added without one is seen."""

    WITHOUT: set[str] = set()

    def test_the_list_is_accurate(self) -> None:
        missing = {name for name, entry in EXPLANATIONS.items()
                   if not getattr(entry, "reference", None)}
        assert missing == self.WITHOUT

    def test_everything_else_cites_something(self) -> None:
        for name, entry in EXPLANATIONS.items():
            if name in self.WITHOUT:
                continue
            assert getattr(entry, "reference", None), name


def test_the_heterogens_are_explained_beneath_their_decisions(tmp_path) -> None:
    """Only where there were decisions to explain: a bare peptide has none."""
    from tests._the_phase import a_real_setup
    from tests.test_a_real_study_runs_end_to_end import TRI_ALANINE

    sulfate = "".join(
        f"HETATM{900 + i:5d} {name:<4} SO4 B 901    {x:8.3f}{y:8.3f}{z:8.3f}"
        f"  1.00  0.00           {element}\n"
        for i, (name, element, x, y, z) in enumerate([
            ("S", "S", 12.0, 12.0, 12.0), ("O1", "O", 13.4, 12.0, 12.0),
            ("O2", "O", 11.5, 13.3, 12.0), ("O3", "O", 11.5, 11.3, 13.2),
            ("O4", "O", 11.5, 11.3, 10.8)]))
    with_sulfate = TRI_ALANINE.replace("END", sulfate + "END")
    ran = a_real_setup(tmp_path, pdb_text=with_sulfate)
    assert "heterogens" in ran.explained
    (tmp_path / "bare").mkdir()
    bare = a_real_setup(tmp_path / "bare")
    assert "heterogens" not in bare.explained
