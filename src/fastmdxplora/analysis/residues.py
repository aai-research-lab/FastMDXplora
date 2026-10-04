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
every trypsin run. The loader reads the codes from the file the topology
came from, PDB or mmCIF, whether given as the topology or loaded as the
trajectory itself; a structure that has any gains an ``insertion`` column,
and single labels read ``184A``.

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
    """Read a PDB or mmCIF topology's insertion codes; returns how many
    residues had one.

    Called for whichever file gave the topology: an external topology, or a
    PDB or mmCIF loaded as the trajectory itself. Reading only an external
    ``.pdb`` lost the codes of both, and trypsin's 184A and 184 came out as
    two residues numbered 184.
    """
    from pathlib import Path

    path = Path(str(topology_path))
    suffix = path.suffix.lower()
    if suffix in (".cif", ".pdbx", ".mmcif"):
        return _remember_mmcif_insertion_codes(path)
    if suffix not in (".pdb", ".ent"):
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


def _mmcif_atom_sites(lines: list[str]) -> tuple[list[str], list[list[str]]]:
    """The ``_atom_site`` loop of an mmCIF file: its column names and rows.

    Tokenised as the format quotes: a value with a space in it is wrapped in
    single or double quotes, and a quote only closes where whitespace or the
    end of the line follows it, so ``"O5'"`` is one value.
    """
    import re

    token = re.compile(r"""'(?:[^']|'(?=\S))*'(?=\s|$)|"(?:[^"]|"(?=\S))*"(?=\s|$)|\S+""")
    columns: list[str] = []
    rows: list[list[str]] = []
    pending: list[str] = []
    in_loop = reading = False
    for raw in lines:
        line = raw.strip()
        if line == "loop_":
            if reading:
                break
            in_loop, columns = True, []
            continue
        if in_loop and line.startswith("_atom_site."):
            columns.append(line.split()[0][len("_atom_site."):])
            continue
        if in_loop and columns and not reading:
            reading = True
        if not reading:
            in_loop = in_loop and (not line or line.startswith("_"))
            continue
        if not line or line.startswith("#") or line.startswith(("_", "loop_", "data_")):
            break
        pending.extend(value[1:-1] if value[:1] in "'\"" and len(value) > 1 else value
                       for value in token.findall(line))
        while len(pending) >= len(columns):
            rows.append(pending[:len(columns)])
            pending = pending[len(columns):]
    return columns, rows


def _remember_mmcif_insertion_codes(path: Any) -> int:
    """Insertion codes from ``_atom_site.pdbx_PDB_ins_code``, keyed as the
    atoms MDTraj reads from the file are: the atom's ``id``, the residue name
    and number by author (``auth_comp_id``, ``auth_seq_id``), first model."""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return 0
    columns, rows = _mmcif_atom_sites(lines)
    where = {name: i for i, name in enumerate(columns)}
    if "pdbx_PDB_ins_code" not in where or "id" not in where:
        return 0

    def given(row: list[str], *names: str) -> str:
        for name in names:
            if name in where and row[where[name]] not in ("?", "."):
                return row[where[name]]
        return ""

    model = None
    found = 0
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        here = given(row, "pdbx_PDB_model_num") or "1"
        model = here if model is None else model
        if here != model:
            continue
        code = given(row, "pdbx_PDB_ins_code")
        if not code:
            continue
        name = given(row, "auth_comp_id", "label_comp_id")
        number = given(row, "auth_seq_id", "label_seq_id")
        try:
            key = (int(given(row, "id")), name, int(number))
        except ValueError:
            continue
        _INSERTION_CODES[key] = code[:1]
        residue = (given(row, "auth_asym_id", "label_asym_id"), number, code)
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


def named(residue: Any, *, qualified: bool) -> str:
    """One residue by name for a table row: ``ASP189``, ``GLY184A`` with an
    insertion code, and ``A:ASP189`` where the chain is qualified.

    ``str(residue)`` gives the name and number alone, so the same residue of
    two chains, or 184 and 184A of one, shared a row and their frames were
    added together."""
    try:
        base = f"{residue.name}{residue.resSeq}{insertion_code(residue)}"
    except (AttributeError, TypeError):
        base = str(residue)
    return f"{chain_name(residue)}:{base}" if qualified else base
