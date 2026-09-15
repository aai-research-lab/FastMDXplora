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
source rather than by one function everyone uses. It is eight, and it is
computed here once.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from fastmdxplora.explain import EXPLANATIONS

SRC = pathlib.Path(__file__).resolve().parents[1] / "src"

#: The eight with no call site, named so that wiring one is a visible
#: change to this list rather than a silent improvement to a number.
#:
#: Each needs a step that already prints, so the explanation attaches to
#: something the reader is looking at rather than arriving on its own.
UNWIRED = {
    "convergence",
    "heterogens",
    "interactions",
    "ligand_chemistry",
    "ligand_parameters",
    "membrane_barostat",
    "restraints",
}


def _keys_named_anywhere() -> set[str]:
    """Every explanation key some call site actually asks for.

    Both spellings, because both are in use: the keyword form on
    `presenter.step`/`info`, and `on_explain` in the simulation runner --
    which also reaches three keys through the `_STAGE_EXPLANATIONS` table
    rather than by literal. A count that misses one form undercounts, which
    is how "five" was reported for what is eight.
    """
    blob = "\n".join(
        path.read_text(encoding="utf-8")
        for path in SRC.rglob("*.py")
        if path.name != "explain.py"
    )
    named = set(re.findall(r'(?:on_explain|_explain)\(\s*["\'](\w+)["\']', blob))
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
    """`minimize` and `production` carry no reference, against a README
    saying every step "cites the paper worth reading". Pinned rather than
    fixed: which paper belongs on each is a judgement, and inventing one to
    make a test pass would be worse than the gap."""

    WITHOUT = {"minimize", "production"}

    def test_the_list_is_accurate(self) -> None:
        missing = {name for name, entry in EXPLANATIONS.items()
                   if not getattr(entry, "reference", None)}
        assert missing == self.WITHOUT

    def test_everything_else_cites_something(self) -> None:
        for name, entry in EXPLANATIONS.items():
            if name in self.WITHOUT:
                continue
            assert getattr(entry, "reference", None), name
