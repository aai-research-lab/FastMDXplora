"""One model of a structure file that holds several.

An NMR entry deposits an ensemble: twenty or forty conformers, each a
MODEL of the same molecule. Setup prepared the first and said nothing of
the rest. `setup.model` names which one to prepare, as the file numbers
them, and sweeping it gives replicas that start from different structures
of the ensemble: the independent starts a stopping rule asks for, which
replicas over a random seed, sharing one structure, give only as far as
their dynamics carry them apart.
"""

from __future__ import annotations

from typing import Any

__all__ = ["models_in", "one_model"]

#: Records that belong to a model: kept for the one asked for, dropped for
#: the rest.
_IN_A_MODEL = {"ATOM", "HETATM", "ANISOU", "TER", "SIGATM", "SIGUIJ"}


def models_in(lines: list[str]) -> list[int]:
    """The models a structure holds, by the numbers its MODEL records give
    (their order where a record gives none); [1] for a file without them."""
    numbers: list[int] = []
    for line in lines:
        if line[:6].strip().upper() == "MODEL":
            try:
                numbers.append(int(line[6:].split()[0]))
            except (IndexError, ValueError):
                numbers.append(len(numbers) + 1)
    return numbers or [1]


def one_model(lines: list[str], number: Any) -> list[str]:
    """The structure's lines with model ``number`` alone, every record
    outside the models (the header, the assembly, connectivity) kept.
    Refused where the file holds no such model."""
    from fastmdxplora.refusals import StudyError

    available = models_in(lines)
    try:
        wanted = int(number)
    except (TypeError, ValueError):
        wanted = None
    if wanted is None or isinstance(number, bool) or wanted not in available:
        shown = (available if len(available) <= 12
                 else available[:6] + ["..."] + available[-3:])
        raise StudyError(
            f"setup.model is {number!r}, and the structure holds "
            f"{len(available)} model{'' if len(available) == 1 else 's'}, numbered "
            f"{', '.join(map(str, shown))}.",
            code="config.option.not_permitted", option="setup.model", given=number,
            permitted=available if len(available) <= 100 else None)
    if len(available) == 1:
        return list(lines)
    kept: list[str] = []
    inside: int | None = None
    ordinal = 0
    for line in lines:
        record = line[:6].strip().upper()
        if record == "MODEL":
            inside = available[ordinal]
            ordinal += 1
            continue
        if record == "ENDMDL":
            inside = None
            continue
        if record in _IN_A_MODEL and inside is not None:
            if inside == wanted:
                kept.append(line)
            continue
        kept.append(line)
    return kept
