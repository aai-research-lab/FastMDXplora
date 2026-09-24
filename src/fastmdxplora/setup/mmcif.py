"""A deposited mmCIF file, written as the PDB records setup reads.

Setup takes a structure's PDB records at their word: SEQRES for the residues
the model is missing, REMARK 350 for the biological assembly, REMARK 465 and
470 for what the experiment did not see, and fixed columns for chains. An
mmCIF file given to it was copied to ``input.pdb`` whatever it held and read
as PDB text, so every one failed at the first number; the format is also the
only one some entries come in. Written here from the categories that hold
the same facts, in the depositors' own numbering, chain IDs and insertion
codes, and checked against entries deposited in both formats.

A structure the PDB format cannot hold -- more than 99,999 atoms, more than
62 chains, or a chain ID longer than one character -- is refused, since
setup works in that format and a truncated file would be a different
structure.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

_CHAIN_IDS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"


def _rows(block: Any, name: str) -> list[dict[str, str]]:
    table = block.getObj(name)
    if table is None:
        return []
    keys = table.getAttributeList()
    return [dict(zip(keys, table.getRow(i))) for i in range(table.getRowCount())]


def _given(value: str | None) -> str:
    return "" if value in (None, "?", ".") else str(value)


def _atom_name(name: str, element: str) -> str:
    # PDB columns 13-16: a one-letter element's name starts in column 14.
    if len(name) >= 4 or len(element.strip()) == 2:
        return f"{name:<4s}"[:4]
    return f" {name:<3s}"


def to_pdb_lines(path: str | Path) -> list[str]:
    """The records setup reads, from the first model of an mmCIF file."""
    from openmm.app.internal.pdbx.reader.PdbxReader import PdbxReader

    from fastmdxplora.refusals import StudyError

    blocks: list[Any] = []
    with Path(path).open(encoding="utf-8", errors="replace") as handle:
        PdbxReader(handle).read(blocks)
    if not blocks or blocks[0].getObj("atom_site") is None:
        raise StudyError(f"{path} holds no atoms mmCIF can name (no atom_site).",
                         code="setup.structure.unreadable", path=str(path))
    block = blocks[0]
    atoms = _rows(block, "atom_site")
    first_model = _given(atoms[0].get("pdbx_PDB_model_num")) or "1"
    atoms = [a for a in atoms if (_given(a.get("pdbx_PDB_model_num")) or "1") == first_model]

    chains = list(dict.fromkeys(_given(a.get("auth_asym_id")) or _given(a.get("label_asym_id"))
                                for a in atoms))
    too_long = [c for c in chains if len(c) != 1]
    if len(atoms) > 99_999 or len(chains) > len(_CHAIN_IDS) or too_long:
        raise StudyError(
            f"{path} holds {len(atoms):,} atoms in {len(chains)} chains"
            + (f", with chain IDs such as {too_long[0]!r}" if too_long else "")
            + ". Setup works in PDB format, which holds at most 99,999 atoms and "
            "62 one-character chains, and a structure cut to fit would be a "
            "different structure. Name the chains to simulate with `chains`, "
            "from a file holding only those.",
            code="setup.structure.too_large", path=str(path))

    label_to_chain: dict[str, str] = {}
    for atom in atoms:
        label_to_chain.setdefault(_given(atom.get("label_asym_id")),
                                  _given(atom.get("auth_asym_id")) or _given(atom.get("label_asym_id")))

    lines: list[str] = []
    cell = _rows(block, "cell")
    symmetry = _rows(block, "symmetry")
    if cell:
        c = cell[0]
        group = _given(symmetry[0].get("space_group_name_H-M")) if symmetry else ""
        try:
            lines.append(f"CRYST1{float(c['length_a']):9.3f}{float(c['length_b']):9.3f}"
                         f"{float(c['length_c']):9.3f}{float(c['angle_alpha']):7.2f}"
                         f"{float(c['angle_beta']):7.2f}{float(c['angle_gamma']):7.2f} "
                         f"{group:<11s}{_given(c.get('Z_PDB')):>4s}")
        except (KeyError, ValueError):
            pass
    lines.extend(_remark_350(block, label_to_chain))
    lines.extend(_remark_465_470(block, first_model))
    lines.extend(_seqres(block))

    serial = 0
    last_polymer: dict[str, int] = {}
    body: list[tuple[str, str]] = []
    for atom in atoms:
        chain = _given(atom.get("auth_asym_id")) or _given(atom.get("label_asym_id"))
        record = "ATOM  " if atom.get("group_PDB") == "ATOM" else "HETATM"
        serial += 1
        element = _given(atom.get("type_symbol")).upper()
        name = _given(atom.get("auth_atom_id")) or _given(atom.get("label_atom_id"))
        residue = (_given(atom.get("auth_comp_id")) or _given(atom.get("label_comp_id")))[:3]
        number = _given(atom.get("auth_seq_id")) or _given(atom.get("label_seq_id")) or "0"
        charge = _given(atom.get("pdbx_formal_charge"))
        charge = "" if charge in ("", "0") else (f"{abs(int(charge))}{'-' if int(charge) < 0 else '+'}"
                                                 if charge.lstrip("-+").isdigit() else "")
        line = (f"{record}{serial:5d} {_atom_name(name, element)}"
                f"{_given(atom.get('label_alt_id'))[:1] or ' '}{residue:>3s} {chain}"
                f"{int(number):4d}{_given(atom.get('pdbx_PDB_ins_code'))[:1] or ' '}   "
                f"{float(atom['Cartn_x']):8.3f}{float(atom['Cartn_y']):8.3f}{float(atom['Cartn_z']):8.3f}"
                f"{float(_given(atom.get('occupancy')) or 1.0):6.2f}"
                f"{float(_given(atom.get('B_iso_or_equiv')) or 0.0):6.2f}          "
                f"{element:>2s}{charge:2s}")
        body.append((chain, line))
        if record == "ATOM  ":
            last_polymer[chain] = len(body) - 1
    # TER closes each chain's polymer, before its heterogens, as a deposited
    # PDB file has it: PDBFixer reads the heterogens into the chain otherwise.
    closes = set(last_polymer.values())
    for index, (_, line) in enumerate(body):
        lines.append(line)
        if index in closes:
            lines.append("TER")
    lines.append("END")
    return lines


def _seqres(block: Any) -> list[str]:
    """SEQRES per chain in the depositors' IDs, from the declared sequence."""
    by_chain: dict[str, list[tuple[int, str]]] = {}
    for row in _rows(block, "pdbx_poly_seq_scheme"):
        chain = _given(row.get("pdb_strand_id"))
        try:
            by_chain.setdefault(chain, []).append((int(row["seq_id"]), row["mon_id"]))
        except (KeyError, ValueError):
            continue
    lines: list[str] = []
    for chain, residues in by_chain.items():
        # One residue per position: a microheterogeneous position lists two.
        names = [name for _, name in sorted(dict(residues).items())]
        for start in range(0, len(names), 13):
            chunk = " ".join(f"{name[:3]:>3s}" for name in names[start:start + 13])
            lines.append(f"SEQRES {start // 13 + 1:3d} {chain} {len(names):4d}  {chunk}")
    return lines


def _remark_350(block: Any, label_to_chain: dict[str, str]) -> list[str]:
    """The biological assemblies, with the operators each applies to which chains."""
    operators = {row["id"]: [[float(row[f"matrix[{r}][{c}]"]) for c in (1, 2, 3)]
                             + [float(row[f"vector[{r}]"])] for r in (1, 2, 3)]
                 for row in _rows(block, "pdbx_struct_oper_list")}
    generated: dict[str, list[tuple[list[str], list[str]]]] = {}
    for row in _rows(block, "pdbx_struct_assembly_gen"):
        ids = _operator_ids(_given(row.get("oper_expression")))
        if ids is None:
            continue  # a product of operator sets, as for a virus capsid
        chains = list(dict.fromkeys(label_to_chain.get(label.strip(), "")
                                    for label in _given(row.get("asym_id_list")).split(",")
                                    if label.strip()))
        generated.setdefault(row["assembly_id"], []).append(([c for c in chains if c], ids))
    lines: list[str] = []
    for row in _rows(block, "pdbx_struct_assembly"):
        parts = generated.get(row["id"])
        if not parts or any(i not in operators for _, ids in parts for i in ids):
            continue
        how = _given(row.get("details")).lower()
        size = _given(row.get("oligomeric_details")).upper()
        lines.append(f"REMARK 350 BIOMOLECULE: {row['id']}")
        if "author" in how:
            lines.append(f"REMARK 350 AUTHOR DETERMINED BIOLOGICAL UNIT: {size}")
        if "software" in how:
            lines.append(f"REMARK 350 SOFTWARE DETERMINED QUATERNARY STRUCTURE: {size}")
        for chains, ids in parts:
            lines.append(f"REMARK 350 APPLY THE FOLLOWING TO CHAINS: {', '.join(chains)}")
            for n, operator_id in enumerate(ids, start=1):
                for r, values in enumerate(operators[operator_id], start=1):
                    lines.append(f"REMARK 350   BIOMT{r} {n:3d}{values[0]:10.6f}{values[1]:10.6f}"
                                 f"{values[2]:10.6f}{values[3]:15.5f}")
    return lines


def _operator_ids(expression: str) -> list[str] | None:
    """``1``, ``1,2,3`` or ``(1-4)`` as operator IDs; None for a product such
    as ``(1-60)(61-88)``, which REMARK 350 has no form for."""
    text = expression.strip()
    if text.count("(") > 1:
        return None
    text = text.strip("()")
    ids: list[str] = []
    for part in text.split(","):
        part = part.strip()
        if "-" in part and all(p.strip().isdigit() for p in part.split("-", 1)):
            low, high = (int(p) for p in part.split("-", 1))
            ids.extend(str(i) for i in range(low, high + 1))
        elif part:
            ids.append(part)
    return ids or None


def _remark_465_470(block: Any, model: str) -> list[str]:
    """Residues and atoms the experiment did not see, in the depositors' IDs."""
    lines: list[str] = []
    for row in _rows(block, "pdbx_unobs_or_zero_occ_residues"):
        # Unobserved (flag 1), as REMARK 465 lists; zero occupancy (flag 0)
        # is a different record in the PDB format, and a residue modelled.
        if (_given(row.get("PDB_model_num")) not in ("", model) or _given(row.get("polymer_flag")) != "Y"
                or _given(row.get("occupancy_flag")) not in ("", "1")):
            continue
        lines.append(f"REMARK 465     {_given(row.get('auth_comp_id')):>3s} "
                     f"{_given(row.get('auth_asym_id'))} {_given(row.get('auth_seq_id')):>5s}"
                     f"{_given(row.get('PDB_ins_code'))}")
    missing: dict[tuple[str, str, str, str], list[str]] = {}
    for row in _rows(block, "pdbx_unobs_or_zero_occ_atoms"):
        if (_given(row.get("PDB_model_num")) not in ("", model) or _given(row.get("polymer_flag")) != "Y"
                or _given(row.get("occupancy_flag")) not in ("", "1")):
            continue
        key = (_given(row.get("auth_comp_id")), _given(row.get("auth_asym_id")),
               _given(row.get("auth_seq_id")), _given(row.get("PDB_ins_code")))
        missing.setdefault(key, []).append(_given(row.get("auth_atom_id")))
    for (name, chain, number, code), atoms in missing.items():
        lines.append(f"REMARK 470     {name:>3s} {chain}{number:>4s}{code or ' '}   {' '.join(atoms)}")
    return lines
