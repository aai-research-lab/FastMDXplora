"""Which biological assembly a deposited structure is simulated as.

A deposited entry holds the asymmetric unit, which is what the crystal
repeated, not what the molecule is. The depositors say what it is in
REMARK 350: each biological assembly, which chains it holds, who assigned
it, and the operators that build it. 1AKE holds two copies of a monomer,
each its own assembly, and every one of its chains was simulated together.
1HHO holds half a haemoglobin tetramer, whose other half the operators
generate, and the half was simulated as though it were the molecule.

Chosen here, where no ``chains`` were given:

* The authors' assemblies before software-predicted ones.
* Among assemblies holding the same things -- the same sequences and the
  same heterogens, water aside -- the one needing least modelling: fewest
  missing residues, then fewest residues missing atoms, then the lower mean
  alpha-carbon B-factor, then the lower number.
* Assemblies that hold different things are not chosen between. Which of
  them is simulated is the study, not the quality of its starting model,
  so setup stops and names each, with the ``chains`` that selects it.

An assembly the operators build beyond the deposited chains is built: each
operator is applied to the chains it names, with the heterogens selection
keeps beside them, and each copy gets chain IDs of its own. Copies that
land on top of one another are refused rather than simulated.
"""

from __future__ import annotations

import shutil
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from fastmdxplora.utils.logging import get_logger

logger = get_logger("setup.assembly")

_WATER = {"HOH", "WAT", "DOD", "H2O", "TIP", "TIP3", "SOL"}


@dataclass
class Assembly:
    number: int
    by_authors: bool = False
    by_software: bool = False
    size: str = ""
    # (chains, operators) for each APPLY block; an operator is 3 rows of 4.
    parts: list[tuple[list[str], list[list[list[float]]]]] = field(default_factory=list)

    @property
    def chains(self) -> list[str]:
        seen: list[str] = []
        for chains, _ in self.parts:
            seen.extend(c for c in chains if c not in seen)
        return seen

    @property
    def copies(self) -> int:
        """How many copies of its chains the operators make."""
        return max((len(ops) for _, ops in self.parts), default=1)

    @property
    def generated(self) -> bool:
        """Whether an operator other than the identity is needed."""
        return any(not _is_identity(op) for _, ops in self.parts for op in ops)

    @property
    def assigned_by(self) -> str:
        if self.by_authors and self.by_software:
            return "the authors, and software agrees"
        return "the authors" if self.by_authors else "software"

    def describe(self) -> str:
        size = f"{self.size.lower()}, " if self.size else ""
        return (f"assembly {self.number} ({size}assigned by {self.assigned_by}): "
                f"chain{'s' if len(self.chains) != 1 else ''} {', '.join(self.chains)}"
                + (f", {self.copies} copies by symmetry" if self.generated else ""))


def _is_identity(op: list[list[float]]) -> bool:
    expected = ([1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0])
    return all(abs(a - b) < 1e-4 for row, want in zip(op, expected) for a, b in zip(row, want))


def read_assemblies(lines: list[str]) -> list[Assembly]:
    """The biological assemblies REMARK 350 declares, in order."""
    found: list[Assembly] = []
    current: Assembly | None = None
    rows: dict[int, list[list[float]]] = {}

    def close_operators() -> None:
        if current is not None and current.parts and rows:
            current.parts[-1][1].extend(rows[k] for k in sorted(rows) if len(rows[k]) == 3)
        rows.clear()

    for line in lines:
        if not line.startswith("REMARK 350"):
            continue
        body = line[10:].strip()
        upper = body.upper()
        if upper.startswith("BIOMOLECULE:"):
            close_operators()
            try:
                current = Assembly(number=int(body.split(":", 1)[1].split()[0]))
            except (IndexError, ValueError):
                current = None
                continue
            found.append(current)
        elif current is None:
            continue
        elif upper.startswith("AUTHOR DETERMINED BIOLOGICAL UNIT:"):
            current.by_authors = True
            current.size = body.split(":", 1)[1].strip()
        elif upper.startswith("SOFTWARE DETERMINED QUATERNARY STRUCTURE:"):
            current.by_software = True
            current.size = current.size or body.split(":", 1)[1].strip()
        elif upper.startswith("APPLY THE FOLLOWING TO CHAINS:"):
            close_operators()
            current.parts.append((_chain_list(body.split(":", 1)[1]), []))
        elif upper.startswith("AND CHAINS:") and current.parts:
            current.parts[-1][0].extend(_chain_list(body.split(":", 1)[1]))
        elif upper.startswith("BIOMT") and current.parts:
            fields = body.split()
            try:
                serial = int(fields[1])
                rows.setdefault(serial, []).append([float(v) for v in fields[2:6]])
            except (IndexError, ValueError):
                continue
    close_operators()
    return [a for a in found if a.chains]


def _chain_list(text: str) -> list[str]:
    return [c.strip() for c in text.replace(" ", ",").split(",") if c.strip()]


@dataclass
class Choice:
    """What was decided, for the log and for the setup record."""
    chosen: Assembly
    alternatives: list[Assembly]
    criterion: str
    keep: list[str] | None  # chains to keep, or None where the file is the assembly

    def record(self) -> dict[str, Any]:
        return {
            "assembly": self.chosen.number,
            "assigned_by": self.chosen.assigned_by,
            "size": self.chosen.size.lower(),
            "chains": self.chosen.chains,
            "generated_by_symmetry": self.chosen.generated,
            "copies": self.chosen.copies,
            "chosen_because": self.criterion,
            "alternatives": [a.number for a in self.alternatives],
        }


def choose_assembly(
    input_pdb: Path,
    *,
    select: Callable[..., Path],
) -> Choice | None:
    """The assembly to simulate, or None where the file declares none.

    ``select`` is setup's own chain selection, used on a scratch copy so
    that what each assembly would hold is what that selection would keep,
    heterogens carried by proximity included.
    """
    from fastmdxplora.refusals import StudyError

    lines = input_pdb.read_text(encoding="utf-8", errors="replace").splitlines()
    assemblies = read_assemblies(lines)
    if not assemblies:
        return None
    present = sorted({line[21:22].strip() for line in lines
                      if line.startswith("ATOM") and line[21:22].strip()})
    usable = [a for a in assemblies if set(a.chains) <= set(present)]
    if not usable:
        return None
    authors = [a for a in usable if a.by_authors]
    candidates = authors or usable
    criterion = ("it is the only assembly the authors assigned" if authors and len(authors) == 1
                 else "it is the only assembly declared" if len(candidates) == 1 else "")

    if len(candidates) > 1:
        contents = {a.number: _contents(input_pdb, a, present, select) for a in candidates}
        if len(set(contents.values())) > 1:
            options = "; ".join(
                f"{a.describe()}, holding {_say(contents[a.number])}, selected by "
                f"`chains: [{', '.join(a.chains)}]`" for a in candidates)
            raise StudyError(
                f"This structure's biological assemblies hold different things, so "
                f"which one is simulated is the study, not a detail setup should "
                f"decide: {options}. Name the one to simulate with `chains`.",
                code="setup.structure.assembly_ambiguous",
                assemblies=[a.number for a in candidates])
        ranked = sorted(candidates, key=lambda a: (*_quality(lines, a.chains), a.number))
        chosen = ranked[0]
        criterion = _why(lines, ranked)
    else:
        chosen = candidates[0]

    keep = None if sorted(chosen.chains) == present else list(chosen.chains)
    return Choice(chosen=chosen, alternatives=[a for a in usable if a is not chosen],
                  criterion=criterion, keep=keep)


def _contents(input_pdb: Path, assembly: Assembly, present: list[str],
              select: Callable[..., Path]) -> tuple:
    """The sequences and heterogens an assembly would put in the box."""
    with tempfile.TemporaryDirectory() as scratch:
        copy = Path(scratch) / "input.pdb"
        shutil.copy2(input_pdb, copy)
        kept = (copy if sorted(assembly.chains) == present
                else select(copy, list(assembly.chains), quiet=True))
        lines = kept.read_text(encoding="utf-8", errors="replace").splitlines()
    sequences: dict[str, list[str]] = {}
    heterogens: Counter = Counter()
    seen: set[tuple[str, str, str]] = set()
    for line in lines:
        if line.startswith("SEQRES"):
            sequences.setdefault(line[11:12].strip(), []).extend(line[19:70].split())
        elif line.startswith("HETATM"):
            key = (line[21:22], line[17:20].strip(), line[22:27])
            if key[1] not in _WATER and key not in seen:
                seen.add(key)
                heterogens[key[1]] += 1
    polymers = tuple(sorted(" ".join(s) for s in sequences.values())) * assembly.copies
    return (tuple(sorted(polymers)),
            tuple(sorted((name, n * assembly.copies) for name, n in heterogens.items())))


def _say(contents: tuple) -> str:
    polymers, heterogens = contents
    chains = f"{len(polymers)} polymer chain{'s' if len(polymers) != 1 else ''}"
    ligands = ", ".join(f"{n} {name}" for name, n in heterogens) or "no heterogens"
    return f"{chains} and {ligands}"


def _quality(lines: list[str], chains: list[str]) -> tuple[int, int, float]:
    """Missing residues, residues missing atoms, and mean alpha-carbon B."""
    wanted = set(chains)
    missing_residues = missing_atoms = 0
    b_factors: list[float] = []
    for line in lines:
        if line.startswith("REMARK 465"):
            fields = line[10:].split()
            # "  MET A     1" rows; the header rows have no three-letter name.
            if len(fields) >= 3 and len(fields[0]) == 3 and fields[0].isalpha() \
                    and fields[1] in wanted and fields[2].lstrip("-").isdigit():
                missing_residues += 1
        elif line.startswith("REMARK 470"):
            fields = line[10:].split()
            if len(fields) >= 4 and len(fields[0]) == 3 and fields[0].isalpha() \
                    and fields[1] in wanted and fields[2].lstrip("-").isdigit():
                missing_atoms += 1
        elif line.startswith("ATOM") and line[12:16].strip() == "CA" \
                and line[21:22].strip() in wanted:
            try:
                b_factors.append(float(line[60:66]))
            except ValueError:
                continue
    mean_b = sum(b_factors) / len(b_factors) if b_factors else float("inf")
    return missing_residues, missing_atoms, round(mean_b, 2)


def _why(lines: list[str], ranked: list[Assembly]) -> str:
    """The first criterion on which the chosen assembly beat the next."""
    first, second = ranked[0], ranked[1]
    a, b = _quality(lines, first.chains), _quality(lines, second.chains)
    names = ("fewer missing residues", "fewer residues missing atoms",
             "the lower mean alpha-carbon B-factor")  # each read after "it has"
    for name, mine, theirs in zip(names, a, b):
        if mine != theirs:
            detail = (f"{mine:.1f} against {theirs:.1f} A^2" if isinstance(mine, float)
                      else f"{mine} against {theirs}")
            return f"it has {name}: {detail} for assembly {second.number}"
    return f"it has the lower number, and assembly {second.number} is equivalent"


def say_the_choice(choice: Choice) -> None:
    """The choice, its reason, and what else there was, in the setup log."""
    chosen = choice.chosen
    if choice.keep is None and not chosen.generated and not choice.alternatives:
        logger.info("The file is the biological assembly: %s.", chosen.describe())
        return
    others = "; ".join(f"{a.describe()}, selected by `chains: [{', '.join(a.chains)}]`"
                       for a in choice.alternatives)
    logger.info(
        "Simulating the biological %s, because %s.%s",
        chosen.describe(), choice.criterion,
        f" Also declared: {others}." if others else "")



def _nearest_part(records: list[str], assembly: Assembly, extra: list[str]) -> dict[int, list[str]]:
    import numpy as np

    def points(chains) -> np.ndarray:
        rows = []
        for line in records:
            if line[21] in chains:
                try:
                    rows.append([float(line[30:38]), float(line[38:46]), float(line[46:54])])
                except ValueError:
                    continue
        return np.asarray(rows, dtype=float).reshape(-1, 3)

    parts = [points(set(chains)) for chains, _ in assembly.parts]
    placed: dict[int, list[str]] = {}
    for chain in extra:
        mine = points({chain})
        if not len(mine):
            continue
        reach = [float(np.min(np.linalg.norm(part[:, None] - mine[None], axis=2)))
                 if len(part) else float("inf") for part in parts]
        placed.setdefault(int(np.argmin(reach)), []).append(chain)
    return placed


_CHAIN_IDS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
#: Closer than this between heavy atoms of two copies is two copies in one
#: place, not an interface: a misread operator, not a molecule.
_OVERLAP_A = 1.0


def build_assembly(input_pdb: Path, assembly: Assembly, chain_columns: dict[str, tuple[int, ...]]
                   ) -> tuple[Path, dict[str, list[str]]]:
    """Write the assembly the operators make from ``input_pdb``.

    ``input_pdb`` holds the assembly's deposited chains and the heterogens
    kept with them. Each operator after the identity makes a copy of the
    chains its part names, under chain IDs not otherwise in use. SEQRES,
    SSBOND and LINK are copied with the chains they name: PDBFixer compares
    the model with SEQRES to find missing residues, and the heterogen layer
    reads LINK. Returns the written file and, for each deposited chain, the
    chain IDs of its copies in operator order.
    """
    import numpy as np

    from fastmdxplora.refusals import StudyError

    lines = input_pdb.read_text(encoding="utf-8", errors="replace").splitlines()
    coordinate_records = [line for line in lines if line[:6] in ("ATOM  ", "HETATM")]
    in_use = {line[21] for line in coordinate_records}
    # The chains each part moves, and any heterogen chain kept beside them.
    free = iter(c for c in _CHAIN_IDS if c not in in_use)
    copies: dict[str, list[str]] = {}
    plans: list[tuple[list[list[float]], dict[str, str]]] = []
    # A heterogen under a chain ID of its own moves with the part whose
    # chains it sits beside, so each copy of a site gets its own ligand.
    extras = _nearest_part(coordinate_records, assembly, sorted(in_use - set(assembly.chains)))
    for index, (chains, operators) in enumerate(assembly.parts):
        moved = list(chains) + extras.get(index, [])
        for operator in operators:
            if _is_identity(operator):
                mapping = {c: c for c in moved}
            else:
                try:
                    mapping = {c: next(free) for c in moved}
                except StopIteration:
                    raise StudyError(
                        f"Assembly {assembly.number} needs more chains than a PDB "
                        f"file can name ({len(_CHAIN_IDS)}). Name the chains to "
                        "simulate with `chains`.",
                        code="setup.structure.assembly_ambiguous",
                        assemblies=[assembly.number]) from None
            for source, target in mapping.items():
                copies.setdefault(source, []).append(target)
            plans.append((operator, mapping))

    out: list[str] = []
    placed: list[tuple[str, np.ndarray]] = []
    serial = 0
    for operator, mapping in plans:
        rotation = np.array([row[:3] for row in operator], dtype=float)
        shift = np.array([row[3] for row in operator], dtype=float)
        block: list[str] = []
        heavy: list[list[float]] = []
        for line in coordinate_records:
            target = mapping.get(line[21])
            if target is None:
                continue
            try:
                xyz = np.array([float(line[30:38]), float(line[38:46]), float(line[46:54])])
            except ValueError:
                continue
            x, y, z = rotation @ xyz + shift
            serial += 1
            if serial > 99999:
                raise StudyError(
                    f"Assembly {assembly.number} has more atoms than a PDB file can "
                    "number (99,999). Name the chains to simulate with `chains`.",
                    code="setup.structure.assembly_ambiguous", assemblies=[assembly.number])
            block.append(f"{line[:6]}{serial:5d}{line[11:21]}{target}{line[22:30]}"
                         f"{x:8.3f}{y:8.3f}{z:8.3f}{line[54:]}")
            if line[:6] == "ATOM  " and line[76:78].strip() != "H":
                heavy.append([x, y, z])
        label = ",".join(sorted(set(mapping.values())))
        mine = np.asarray(heavy, dtype=float)
        for other_label, other in placed:
            if len(mine) and len(other):
                closest = min(float(np.min(np.linalg.norm(other - point, axis=1)))
                              for point in mine[:: max(1, len(mine) // 2000)])
                if closest < _OVERLAP_A:
                    raise StudyError(
                        f"Building assembly {assembly.number} put the copy as chains "
                        f"{label} {closest:.2f} A from chains {other_label}, which is "
                        "two copies in one place rather than an interface. The "
                        "file's operators are not read as they were meant; name the "
                        "chains to simulate with `chains`.",
                        code="setup.structure.assembly_ambiguous",
                        assemblies=[assembly.number])
        placed.append((label, mine))
        # As a deposited file lays it out: each chain's polymer closed by TER,
        # then the heterogens. Without the TER, PDBFixer read the waters and
        # ligands into the protein chain, could not align it with its SEQRES,
        # and found no residue missing in any copy.
        for chain in dict.fromkeys(line[21] for line in block if line.startswith("ATOM")):
            out.extend(line for line in block if line.startswith("ATOM") and line[21] == chain)
            out.append("TER")
        out.extend(line for line in block if line.startswith("HETATM"))

    # SEQRES chain by chain, each copy's records together. PDBFixer reads a
    # chain's declared sequence from consecutive records; interleaved, it
    # read thirteen-residue fragments and found nothing missing.
    declared: dict[str, list[str]] = {}
    for line in lines:
        if line[:6].strip() == "SEQRES":
            declared.setdefault(line[11:12], []).append(line)
    header: list[str] = [line[:11] + target + line[12:]
                         for source, records in declared.items()
                         for target in copies.get(source, [source])
                         for line in records]
    for line in lines:
        record = line[:6].strip()
        if record == "SEQRES":
            continue
        elif record in chain_columns:
            columns = chain_columns[record]
            targets = copies.get(line[columns[0]:columns[0] + 1], [])
            for n in range(len(targets) or 1):
                copied = list(line)
                for column in columns:
                    source = line[column:column + 1]
                    if source in copies and n < len(copies[source]):
                        copied[column] = copies[source][n]
                header.append("".join(copied))
        elif record in ("CRYST1",):
            header.append(line)
    target = input_pdb.with_name("input_assembly.pdb")
    target.write_text("\n".join(header + out + ["END"]) + "\n", encoding="utf-8")
    return target, copies
