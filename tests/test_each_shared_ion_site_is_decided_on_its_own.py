"""Each site an ion is split over is decided on its own, by its own copies.

Two ions of one element within 1.5 A are alternate positions for one site.
Every overlapping pair went into a single group, so two split zincs 20 A
apart read as four copies of one site: refused as a tie when each site on its
own was clear, or resolved by keeping one zinc for both. Where a site did
resolve, the copy kept was the first in the file while the log named the one
of higher occupancy, and the structure handed to PDBFixer kept every copy
under that residue name anyway.
"""

from __future__ import annotations

import io
import logging
from pathlib import Path

import pytest

from fastmdxplora.setup.heterogens import (
    Action,
    AmbiguousStructureError,
    resolve,
)
from tests.test_a_coordinated_ion_reaches_the_prepared_system import (
    FURTHER_OUT,
    ON_THE_HISTIDINE,
    PEPTIDE,
    _at,
    _prepared,
    _residue,
    _write,
    _zinc,
)

#: A second copy of the peptide, and so of its histidine, this far along x.
APART = 20.0


def _moved(where: tuple[float, float, float]) -> tuple[float, float, float]:
    return (round(where[0] + APART, 3), where[1], where[2])


#: Chain B: the same tripeptide, 20 A along, with its own histidine site.
SECOND_PEPTIDE = [
    f"{line[:6]}{int(line[6:11]) + 100:5d}{line[11:21]}B{line[22:30]}"
    f"{float(line[30:38]) + APART:8.3f}{line[38:]}"
    for line in PEPTIDE
]


def _copy(serial: int, seq: int, where, occupancy: float) -> str:
    return _zinc(serial, where, seq=seq, occupancy=occupancy)


def _one_site(first: float, second: float) -> list[str]:
    """Two zincs 0.3 A apart, both on the histidine, in file order."""
    return [_copy(22, 401, ON_THE_HISTIDINE, first),
            _copy(23, 402, FURTHER_OUT, second)]


def _two_sites(first: tuple[float, float], second: tuple[float, float]):
    return SECOND_PEPTIDE + _one_site(*first) + [
        _copy(24, 403, _moved(ON_THE_HISTIDINE), second[0]),
        _copy(25, 404, _moved(FURTHER_OUT), second[1]),
    ]


def _zinc_decision(path: Path):
    return next(d for d in resolve(path) if d.resname == "ZN")


class TestTheCopyOfHigherOccupancyIsTheOneKept:
    """The 0.30 copy is first in the file; the 0.70 copy is the one meant."""

    def test_the_decision_holds_it(self, tmp_path: Path) -> None:
        zinc = _zinc_decision(_write(tmp_path, _one_site(0.30, 0.70)))
        assert zinc.action is Action.SIMULATE
        assert [h.label for h in zinc.instances] == ["ZN A402"]

    def test_the_log_names_the_copy_kept(self, tmp_path: Path) -> None:
        """Read from the module's own logger, as the water test explains."""
        logger = logging.getLogger("fastmdx.setup.heterogens")
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        previous = logger.level
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        try:
            zinc = _zinc_decision(_write(tmp_path, _one_site(0.30, 0.70)))
        finally:
            logger.removeHandler(handler)
            logger.setLevel(previous)
        (kept,) = zinc.instances
        assert f"{kept.label} at occupancy 0.70 kept over ZN A401" \
            in stream.getvalue()

    def test_the_prepared_structure_holds_it_alone(
            self, tmp_path: Path) -> None:
        prepared, record, _ = _prepared(tmp_path, _one_site(0.30, 0.70))
        zinc = _residue(prepared, "ZN")
        assert len(zinc) == 1
        assert _at(zinc[0]) == FURTHER_OUT
        assert int(zinc[0][22:26]) == 402
        assert record["parameters"]["_retained_ions"] == ["ZN A402"]


class TestSeparateSitesAreSeparateQuestions:
    """0.70/0.30 at one histidine and 0.60/0.40 at another, 20 A apart."""

    def test_neither_is_refused_and_each_keeps_its_major_copy(
            self, tmp_path: Path) -> None:
        zinc = _zinc_decision(
            _write(tmp_path, _two_sites((0.70, 0.30), (0.60, 0.40))))
        assert zinc.action is Action.SIMULATE
        assert [h.label for h in zinc.instances] == ["ZN A401", "ZN A403"]

    def test_both_sites_are_in_the_prepared_structure(
            self, tmp_path: Path) -> None:
        prepared, _, _ = _prepared(
            tmp_path, _two_sites((0.70, 0.30), (0.60, 0.40)))
        zinc = sorted(_at(line) for line in _residue(prepared, "ZN"))
        assert zinc == sorted([ON_THE_HISTIDINE, _moved(ON_THE_HISTIDINE)])

    def test_a_tie_at_one_site_names_that_site_alone(
            self, tmp_path: Path) -> None:
        with pytest.raises(AmbiguousStructureError) as caught:
            resolve(_write(tmp_path, _two_sites((0.70, 0.30), (0.52, 0.48))))
        said = str(caught.value)
        assert "ZN A403, ZN A404" in said
        assert "ZN A401" not in said


class TestATieStillStops:

    def test_indistinguishable_occupancies_are_refused(
            self, tmp_path: Path) -> None:
        """Nothing in the structure says which position is real."""
        with pytest.raises(AmbiguousStructureError, match="same site"):
            resolve(_write(tmp_path, _one_site(0.52, 0.48)))
