"""How a residue is named in what the analyses write.

By its deposited number, and by its chain as well wherever there is more
than one. The number alone was used everywhere, so on a structure with
several copies of one chain -- the tetramer setup builds from 1STP, the
haemoglobin from 1HHO -- four residues answered to each number: per-residue
SASA could not be tabulated at all, and RMSF, secondary structure and
dihedrals reported success over tables in which "residue 13" meant four
different residues.

A structure with one chain is named exactly as before, so no result that
was right changes. With several chains:

* tables gain a ``chain`` column beside ``residue``, which stays the number;
* labels that must be one string -- a column name, a contact partner --
  are written ``A:13``.

One definition, so the modules cannot drift apart again: six of them had
each written their own, and one had got it right.
"""

from __future__ import annotations

from typing import Any

import numpy as np


def chain_name(residue: Any) -> str:
    """The chain's deposited ID, or its place among the polymer chains.

    MDTraj before 1.11 drops the ID when it slices a trajectory, and every
    analysis slices to its selection. Lettered by position among all chains,
    a tetramer with a ligand in each site read A, C, E, G -- letters that
    look deposited and are not. Among the polymer chains they read A to D,
    which is what setup writes."""
    chain = residue.chain
    given = str(getattr(chain, "chain_id", "") or "").strip()
    if given:
        return given
    topology = getattr(chain, "topology", None)
    polymer = ([c.index for c in topology.chains if any(_is_polymer(r) for r in c.residues)]
               if topology is not None else [])
    place = polymer.index(chain.index) if chain.index in polymer else int(chain.index)
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    return letters[place] if place < len(letters) else str(place)


def number(residue: Any) -> int:
    try:
        return int(residue.resSeq)
    except (AttributeError, TypeError, ValueError):
        return int(residue.index)


def several_chains(topology: Any) -> bool:
    """Whether the structure has more than one polymer chain.

    Polymer chains only, and of the whole structure. Ions, waters and a
    ligand are often chains of their own, so counting every chain named
    nearly every single-protein run as several; and a residue list that
    happens to come from one chain of a tetramer is still ambiguous."""
    polymer = {r.chain.index for r in topology.residues if _is_polymer(r)}
    return len(polymer) > 1


_NUCLEOTIDES = {"A", "C", "G", "U", "T", "I", "DA", "DC", "DG", "DT", "DU", "DI",
                "RA", "RC", "RG", "RU", "ADE", "CYT", "GUA", "THY", "URA"}


def _is_polymer(residue: Any) -> bool:
    """Protein or nucleic acid. Older MDTraj raises from ``is_nucleic``
    rather than answering, so the standard nucleotide names stand in."""
    try:
        if residue.is_protein:
            return True
    except (AttributeError, NotImplementedError):
        pass
    try:
        return bool(residue.is_nucleic)
    except (AttributeError, NotImplementedError):
        return str(getattr(residue, "name", "")).strip().upper() in _NUCLEOTIDES


def columns(residues: list[Any], topology: Any) -> dict[str, np.ndarray]:
    """``{"residue": numbers}``, with ``"chain"`` first where there are several."""
    numbers = np.array([number(r) for r in residues])
    if not several_chains(topology):
        return {"residue": numbers}
    return {"chain": np.array([chain_name(r) for r in residues]), "residue": numbers}


def label(residue: Any, *, qualified: bool) -> str | int:
    """One residue as one label: its number, or ``A:13`` where qualified."""
    return f"{chain_name(residue)}:{number(residue)}" if qualified else number(residue)


def plot_by_chain(ax: Any, table: Any, value: str, *, spread: str | None = None,
                  **style: Any) -> None:
    """One line per chain against the deposited numbering, with a legend.

    Copies of one chain overlay, so a difference between them is visible
    where it is and a symmetric assembly reads as one curve."""
    for name, rows in table.groupby("chain", sort=False):
        x = rows["residue"].to_numpy()
        y = rows[value].to_numpy()
        line, = ax.plot(x, y, label=f"chain {name}", **style)
        if spread is not None:
            s = rows[spread].to_numpy()
            ax.fill_between(x, y - s, y + s, alpha=0.12, color=line.get_color())
    ax.legend(fontsize="small", ncol=min(4, table["chain"].nunique()))
