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

The same holds for insertion codes. Trypsin is numbered 184A, 184, 188A,
188, 221A, 221, and MDTraj keeps the number and drops the code, so two
residues answered to 184 in a single chain and per-residue SASA failed on
every trypsin run. The loader reads the codes from the topology file; a
structure that has any gains an ``insertion`` column, and single labels
read ``184A``.

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


#: Insertion codes read from topology files, by the first atom's serial,
#: residue name and number: MDTraj keeps an atom's serial when it slices a
#: trajectory, and drops the code with everything else it does not model.
_INSERTION_CODES: dict[tuple[int, str, int], str] = {}


def remember_insertion_codes(topology_path: Any) -> int:
    """Read a PDB topology's insertion codes; returns how many residues had one."""
    from pathlib import Path

    path = Path(str(topology_path))
    if path.suffix.lower() not in (".pdb", ".ent"):
        return 0
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return 0
    found = 0
    seen: set[tuple[str, str, str]] = set()
    for line in lines:
        if not line.startswith(("ATOM", "HETATM")) or line[26:27] in (" ", ""):
            continue
        residue = (line[21], line[22:26], line[26])
        try:
            key = (int(line[6:11]), line[17:20].strip(), int(line[22:26]))
        except ValueError:
            continue
        _INSERTION_CODES[key] = line[26]
        if residue not in seen:
            seen.add(residue)
            found += 1
    return found


def insertion_code(residue: Any) -> str:
    """The residue's insertion code, or "" where it has none or none is known."""
    if not _INSERTION_CODES:
        return ""
    for atom in residue.atoms:
        serial = getattr(atom, "serial", None)
        if serial is None:
            return ""
        return _INSERTION_CODES.get((int(serial), residue.name, number(residue)), "")
    return ""


def has_insertions(topology: Any) -> bool:
    return bool(_INSERTION_CODES) and any(insertion_code(r) for r in topology.residues)


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
    """``{"residue": numbers}``, with ``"chain"`` first where there are several
    and ``"insertion"`` after it where any residue has an insertion code."""
    named: dict[str, np.ndarray] = {}
    if several_chains(topology):
        named["chain"] = np.array([chain_name(r) for r in residues])
    named["residue"] = np.array([number(r) for r in residues])
    if has_insertions(topology):
        named["insertion"] = np.array([insertion_code(r) for r in residues])
    return named


def distinct(topology: Any) -> bool:
    """Whether the number alone names each residue, as it always did."""
    return not several_chains(topology) and not has_insertions(topology)


def label(residue: Any, *, qualified: bool) -> str | int:
    """One residue as one label: its number, ``184A`` where it has an
    insertion code, and ``A:13`` where the chain is qualified."""
    code = insertion_code(residue)
    base = f"{number(residue)}{code}" if code else number(residue)
    return f"{chain_name(residue)}:{base}" if qualified else base


def plot_by_chain(ax: Any, table: Any, value: str, *, spread: str | None = None,
                  **style: Any) -> None:
    """One line per chain against the deposited numbering, with a legend.

    Copies of one chain overlay, so a difference between them is visible
    where it is and a symmetric assembly reads as one curve."""
    groups = table.groupby("chain", sort=False) if "chain" in table else [("", table)]
    for name, rows in groups:
        x = rows["residue"].to_numpy()
        y = rows[value].to_numpy()
        line, = ax.plot(x, y, label=f"chain {name}" if name else None, **style)
        if spread is not None:
            s = rows[spread].to_numpy()
            ax.fill_between(x, y - s, y + s, alpha=0.12, color=line.get_color())
    if "chain" in table:
        ax.legend(fontsize="small", ncol=min(4, table["chain"].nunique()))
