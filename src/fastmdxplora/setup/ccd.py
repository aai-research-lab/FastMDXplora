"""Obtain the chemistry a crystal structure leaves out.

A PDB ``HETATM`` record gives an element and three coordinates. It does not
give bond orders, formal charges, or hydrogens, and a small-molecule force
field needs all three. So a ligand cannot be parameterized from the structure
alone, which is why preparing a protein-ligand system has traditionally meant
extracting the ligand by hand and rebuilding its chemistry in another program.

RCSB publishes the missing information. The ModelServer returns a named
component *from a specific entry*, carrying that entry's coordinates with bond
orders applied from the Chemical Component Dictionary. One request closes the
gap, at the crystallographic pose, with no cheminformatics toolkit involved.

Two properties of this module matter more than its convenience:

* Every fetch is cached, so a run that succeeded once succeeds again without
  a network. The cache is the offline story; bundling the dictionary is not
  feasible, as it is measured in gigabytes.
* Nothing is accepted on trust. A component whose fetched atom count
  disagrees with the structure, or whose protonation at the requested pH is
  not determined, raises rather than proceeding. Placing a complete molecule
  at partial coordinates, or guessing a charge state, produces a trajectory
  that looks right and is not.
"""

from __future__ import annotations

import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from fastmdxplora.utils.logging import get_logger
from fastmdxplora.refusals import CodedError

logger = get_logger("setup.ccd")

MODEL_SERVER = "https://models.rcsb.org/v1"
LIGAND_DEFINITION = "https://files.rcsb.org/ligands/download"

#: Seconds to wait on RCSB before giving up and consulting the cache.
FETCH_TIMEOUT_S = 30

#: Groups whose protonation depends on pH within the range biology cares
#: about. A ligand carrying none of these has one sensible protonation state,
#: and the dictionary's is it. A ligand carrying any of them does not, and no
#: amount of care applied to the ligand in isolation will settle the question,
#: because the pKa that matters is the one it has *in the pocket*.
TITRATABLE_SMARTS: dict[str, str] = {
    "carboxylic acid": "[CX3](=O)[OX2H1]",
    "carboxylate": "[CX3](=O)[OX1H0-]",
    # An amidine is not an amine. Benzamidine, the ligand of half the trypsin
    # structures in the PDB, matched the amine pattern and would have been
    # protonated on its sp3 nitrogen, which is the wrong molecule: the cation
    # is delocalised and forms on the sp2 one. Guanidine is excluded here so
    # each group is named once, by its most specific description.
    "amidine": "[NX3][CX3;!$([CX3]([NX3])[NX3])]=[NX2]",
    "primary or secondary amine":
        "[NX3;H2,H1;!$(NC=O);!$(N[a]);!$(N[CX3]=[NX2])]",
    "tertiary amine":
        "[NX3;H0;!$(NC=O);!$(N[a]);!$([N+]);!$(N[CX3]=[NX2])]",
    "imidazole": "c1cnc[nH]1",
    "phosphate or phosphonate": "[PX4](=O)([OX2H1,OX1H0-])",
    "sulfonic acid": "[SX4](=O)(=O)[OX2H1,OX1H0-]",
    "thiol": "[SX2H1]",
    "tetrazole": "c1nnn[nH]1",
    # A pyridine-type nitrogen: two connections, no hydrogen, lone pair in the
    # ring plane and therefore basic. Restricted to six-membered rings, which
    # separates it from the azoles named above without excluding a fused system
    # like a purine or a pteridine. Basic nitrogens in five-membered rings that
    # are not azoles -- oxazole, thiazole -- are left out deliberately: their
    # pKa is around 1 to 3, so they are neutral across the whole biological
    # range and nothing is decided by naming them.
    "aromatic nitrogen": "[nX2;H0;!$([n+]);r6]",
    "guanidine": "[NX3][CX3](=[NX2])[NX3]",
}


class ChemistryUnavailableError(CodedError, RuntimeError):
    """The chemistry needed to parameterize a component could not be obtained."""

    default_code = "setup.chemistry.unavailable"


class ProtonationUndeterminedError(CodedError, RuntimeError):
    """The component's protonation at the requested pH is not determined.

    Raised rather than resolved. A ligand's pKa in a binding site is a
    property of the complex, not of the molecule: a carboxylate buried beside
    an aspartate may well be neutral, and shifts of two or three units are
    ordinary. Any answer computed from the ligand alone would be a confident
    guess at a question the ligand cannot answer.
    """

    default_code = "setup.chemistry.protonation_undetermined"


@dataclass(frozen=True)
class LigandChemistry:
    """A component with its chemistry resolved, ready for parameterization."""

    resname: str
    path: Path
    source: str
    from_cache: bool
    n_atoms: int
    formal_charge: int
    titratable_groups: tuple[str, ...] = ()


def cache_dir() -> Path:
    """Where fetched chemistry is kept between runs."""
    override = os.environ.get("FASTMDXPLORA_CACHE_DIR")
    base = Path(override) if override else Path.home() / ".cache" / "fastmdxplora"
    target = base / "ccd"
    target.mkdir(parents=True, exist_ok=True)
    return target


def _http_get(url: str, *, timeout: int = FETCH_TIMEOUT_S) -> str:
    """Fetch a URL as text, or raise ChemistryUnavailableError."""
    request = urllib.request.Request(
        url, headers={"User-Agent": "FastMDXplora (https://github.com/aai-research-lab/FastMDXplora)"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            return response.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
        raise ChemistryUnavailableError(f"could not reach {url}: {exc}") from exc


def _posed_url(entry: str, resname: str, chain: str, resseq: int) -> str:
    """ModelServer URL for one specific copy of a component."""
    query = urllib.parse.urlencode({
        "auth_asym_id": chain,
        "auth_seq_id": resseq,
        "label_comp_id": resname.upper(),
        "encoding": "sdf",
    })
    return f"{MODEL_SERVER}/{entry.lower()}/ligand?{query}"


def _definition_url(resname: str) -> str:
    """Chemical Component Dictionary entry, at idealized geometry."""
    return f"{LIGAND_DEFINITION}/{resname.upper()}_ideal.sdf"


def _sdf_atom_count(text: str) -> int:
    """Heavy plus hydrogen atom count from an SDF counts line."""
    lines = text.splitlines()
    if len(lines) < 4:
        raise ChemistryUnavailableError("the fetched file is not a valid SDF")
    try:
        return int(lines[3][0:3])
    except ValueError as exc:
        raise ChemistryUnavailableError("the SDF counts line is unreadable") from exc


def fetch_chemistry(
    entry: str,
    resname: str,
    *,
    chain: str,
    resseq: int,
    expected_heavy_atoms: int | None = None,
    fetcher=_http_get,
) -> LigandChemistry:
    """Obtain a component's chemistry at its pose in ``entry``.

    Tries the cache first, so a repeat run needs no network. ``fetcher`` is
    injectable so that tests never depend on RCSB being reachable.

    Raises
    ------
    ChemistryUnavailableError
        If the component cannot be obtained, or if what came back does not
        match the structure it is supposed to describe.
    """
    resname = resname.upper()
    entry = entry.lower()
    cached = cache_dir() / f"{entry}_{chain}_{resseq}_{resname}.sdf"

    if cached.is_file():
        text = cached.read_text(encoding="utf-8")
        source, from_cache = str(cached), True
    else:
        url = _posed_url(entry, resname, chain, resseq)
        try:
            text = fetcher(url)
        except ChemistryUnavailableError as exc:
            raise ChemistryUnavailableError(
                f"{resname} in {entry.upper()} could not be retrieved and is not "
                f"cached ({exc}). Supply it as an SDF or MOL2 file instead."
            ) from exc
        if not text.strip() or "$$$$" not in text:
            raise ChemistryUnavailableError(
                f"RCSB returned no usable chemistry for {resname} "
                f"{chain}{resseq} in {entry.upper()}."
            )
        source, from_cache = url, False

    _require_component(text, resname, chain, resseq, entry)

    if expected_heavy_atoms is not None:
        # The dictionary describes a complete molecule. A component modelled
        # at partial occupancy may be missing atoms in the density, and
        # placing the complete molecule at incomplete coordinates would
        # invent atomic positions.
        heavy = _count_heavy_atoms(text)
        if heavy != expected_heavy_atoms:
            raise ChemistryUnavailableError(
                f"{resname} {chain}{resseq} has {expected_heavy_atoms} heavy "
                f"atoms in the structure but {heavy} in its chemical "
                "definition, so it is only partially resolved. Supply a "
                "complete ligand explicitly, or exclude it."
            )

    # ModelServer returns heavy atoms only. A force field needs every atom:
    # benzene delivered as a bare six-carbon ring with aromatic bonds is a
    # radical, not benzene, and would be parameterized as one.
    text = _add_hydrogens(text, resname)
    if not from_cache:
        cached.write_text(text, encoding="utf-8")

    n_atoms = _sdf_atom_count(text)
    charge, groups = _inspect(text, resname)
    logger.info(
        "%s %s%s: %d atoms, formal charge %+d%s (%s)",
        resname, chain, resseq, n_atoms, charge,
        f", titratable: {', '.join(groups)}" if groups else "",
        "cached" if from_cache else "fetched from RCSB",
    )
    return LigandChemistry(
        resname=resname,
        path=cached,
        source=source,
        from_cache=from_cache,
        n_atoms=n_atoms,
        formal_charge=charge,
        titratable_groups=tuple(groups),
    )



def _require_component(text: str, resname: str, chain: str, resseq: int, entry: str) -> None:
    """Confirm the retrieved molecule is the one that was requested.

    The query identifies a position in the structure. If the position is
    wrong, a different component comes back and every downstream check reads
    it as the requested one: a mismatched atom count then looks like partial
    occupancy rather than the wrong molecule. Checking the name turns a
    misleading error into an accurate one.
    """
    header = "\n".join(text.splitlines()[:4]).upper()
    if resname.upper() in header:
        return
    raise ChemistryUnavailableError(
        f"asked RCSB for {resname} at {chain}{resseq} in {entry.upper()} and "
        f"received a different component (header: {text.splitlines()[0].strip()!r}). "
        "Check that the chain and residue number identify the intended copy."
    )



def _add_hydrogens(sdf_text: str, resname: str) -> str:
    """Return the component with explicit hydrogens at placed coordinates.

    The retrieved definition carries heavy atoms only, because that is what
    the experiment resolved. Hydrogen positions are inferred from the
    geometry, which is standard practice and is what minimization refines.
    """
    if _count_hydrogens(sdf_text) > 0:
        return sdf_text
    try:
        from rdkit import Chem, RDLogger

        RDLogger.DisableLog("rdApp.*")
    except ImportError as exc:
        raise ChemistryUnavailableError(
            f"RDKit is needed to add hydrogens to {resname} and is not "
            "installed. Install the ligand extra, or supply the ligand as a "
            "file that already carries them."
        ) from exc

    molecule = Chem.MolFromMolBlock(sdf_text, removeHs=False, sanitize=True)
    if molecule is None:
        raise ChemistryUnavailableError(
            f"the chemistry retrieved for {resname} could not be interpreted, "
            "so hydrogens could not be added."
        )
    with_hydrogens = Chem.AddHs(molecule, addCoords=True)
    block = Chem.MolToMolBlock(with_hydrogens)
    return f"{resname}\n" + block.split("\n", 1)[1] + "$$$$\n"


def _count_hydrogens(sdf_text: str) -> int:
    """Hydrogens declared in an SDF atom block."""
    lines = sdf_text.splitlines()
    total = _sdf_atom_count(sdf_text)
    count = 0
    for line in lines[4:4 + total]:
        symbol = line[31:34].strip() if len(line) > 34 else line.split()[3]
        if symbol.upper() == "H":
            count += 1
    return count


def _count_heavy_atoms(sdf_text: str) -> int:
    """Heavy atoms in an SDF atom block."""
    lines = sdf_text.splitlines()
    total = _sdf_atom_count(sdf_text)
    heavy = 0
    for line in lines[4:4 + total]:
        symbol = line[31:34].strip() if len(line) > 34 else line.split()[3]
        if symbol.upper() != "H":
            heavy += 1
    return heavy


def _inspect(sdf_text: str, resname: str) -> tuple[int, list[str]]:
    """Formal charge and any pH-dependent groups.

    RDKit is used when available; it arrives with the OpenFF toolkit, which
    the ligand path already requires. Without it the protonation question
    cannot be answered, and saying so is better than assuming the answer.
    """
    try:
        from rdkit import Chem
        from rdkit import RDLogger

        RDLogger.DisableLog("rdApp.*")
    except ImportError as exc:
        raise ChemistryUnavailableError(
            "RDKit is needed to check the protonation of "
            f"{resname} and is not installed. Install the ligand extra "
            "(conda install -c conda-forge rdkit), or supply the ligand "
            "explicitly with its protonation already assigned."
        ) from exc

    molecule = Chem.MolFromMolBlock(sdf_text, removeHs=False, sanitize=True)
    if molecule is None:
        raise ChemistryUnavailableError(
            f"the chemistry retrieved for {resname} could not be interpreted."
        )

    charge = Chem.GetFormalCharge(molecule)
    found: list[str] = []
    for label, smarts in TITRATABLE_SMARTS.items():
        pattern = Chem.MolFromSmarts(smarts)
        if pattern is not None and molecule.HasSubstructMatch(pattern):
            found.append(label)
    return charge, found


def require_determined_protonation(chemistry: LigandChemistry, ph: float) -> None:
    """Refuse a component whose charge state at ``ph`` is not determined.

    A ligand with no ionizable group has one protonation state and the
    dictionary's is it. A ligand with one does not, and the answer depends on
    the binding site rather than on the molecule, so it is not guessed here.
    """
    if not chemistry.titratable_groups:
        return
    groups = ", ".join(chemistry.titratable_groups)
    raise ProtonationUndeterminedError(
        f"{chemistry.resname} carries groups whose protonation depends on pH "
        f"({groups}), and the state it should adopt at pH {ph:g} is a property "
        "of the binding site rather than of the molecule: pocket-shifted pKa "
        "values of two or three units are ordinary. The chemical dictionary's "
        f"state has formal charge {chemistry.formal_charge:+d}, which may or "
        "may not be right here.\n"
        "Supply the ligand explicitly with the protonation you intend, as an "
        "SDF or MOL2 file, and set its net charge."
    )
