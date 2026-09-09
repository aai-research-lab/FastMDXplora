"""Setup pipeline: prepare a simulation-ready system.

This module's public surface is the :func:`run` function called by the
FastMDXplora orchestrator. Starting in v0.2.0 it performs the full
chemistry pipeline:

  1. Resolve input form: file path, PDB ID (4 chars), or sequence.
  2. For a PDB ID, fetch the structure from RCSB.
  3. Run PDBFixer to repair missing residues/atoms and add hydrogens
     at the requested pH (see :func:`fastmdxplora.setup.pdbfix.fix_pdb_with_pdbfixer`).
  4. Solvate, ionize, parameterize with OpenMM and serialize the
     resulting ``System`` + ``State`` XMLs plus a topology PDB
     (see :func:`fastmdxplora.setup.prepare.prepare_system`).

Defaults are sensible general-purpose settings so users
between the two tools see consistent parameterization.

Graceful degradation
--------------------
PDBFixer and OpenMM are conda-forge-only packages in the optional
``[setup]`` extras. When they are not installed, this module still
classifies the input, writes the parameters manifest, and reserves the
canonical artifact paths. It records the ImportError in the manifest
and emits a presenter warning so users see exactly what is missing.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastmdxplora.dependencies import dependency_error_message, missing_dependencies
from fastmdxplora.config.schema import SETUP
from fastmdxplora.provenance import structure_provenance
from fastmdxplora.utils.logging import get_logger

if TYPE_CHECKING:
    from fastmdxplora.orchestrator import FastMDXplora

logger = get_logger("setup")


# Default parameters. The CHARMM36 + CHARMM36 water choice matches
# sensible protein-only defaults so users
# between the two tools see identical out-of-the-box parameterization.
#: What each option starts as, read from the schema that declares it.
#: This was a second copy of those values, and the two drifted: the pH
#: default was 7.4 in the schema and 7.0 here. There is one declaration
#: now, and a phase reads it rather than restating it.
DEFAULTS: dict[str, Any] = SETUP.defaults()


def _classify_input(system: str | None) -> str:
    """Return one of ``{"pdb_file", "pdb_id", "sequence"}``.

    Heuristics:
      - existing path with .pdb / .cif extension -> pdb_file
      - 4-character alphanumeric string -> pdb_id
      - longer alphabetic-only string -> sequence
    """
    if system is None:
        raise ValueError("setup phase requires a system input")

    p = Path(system)
    structure_suffixes = {".pdb", ".cif", ".pdbx"}
    if p.suffix.lower() in structure_suffixes:
        if p.exists():
            return "pdb_file"
        # Named a structure file and there is none. Falling through to the
        # message below told the author to supply a .pdb, which is what they
        # had just supplied -- so a path typed one directory off read as a
        # complaint about the file type. The path is relative to where the
        # command was run, not to the config that names it, and saying so is
        # most of the fix.
        raise FileNotFoundError(
            f"No structure at {system!r}. The path is read from where "
            f"fastmdx was run ({Path.cwd()}), not from the config file that "
            "names it, so a config beside a structure still needs the path "
            "written from the working directory.")
    if len(system) == 4 and system.isalnum():
        return "pdb_id"
    if system.isalpha():
        return "sequence"
    raise ValueError(
        f"Could not classify system input {system!r}. Expected the path to a "
        ".pdb, .cif or .pdbx file, or a 4-character PDB ID.\n\n"
        # A one-letter sequence is recognised below and then refused, because
        # building a structure from one needs a predictor this software does
        # not carry. Listing it here as an accepted form -- which this message
        # used to do -- sends somebody to try it, and it also found its way
        # into a manuscript describing the software.
        "A one-letter amino-acid sequence is recognised but cannot yet be "
        "built into a structure; predict one first and pass the file."
    )


def _fetch_pdb_from_rcsb(pdb_id: str, dest: Path) -> Path:
    """Fetch ``{pdb_id}.pdb`` from RCSB to ``dest``. Raises on HTTP error."""
    import urllib.request

    url = f"https://files.rcsb.org/download/{pdb_id.upper()}.pdb"
    logger.info("Fetching PDB from RCSB: %s", url)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        urllib.request.urlretrieve(url, dest)  # noqa: S310 -- trusted URL
    except OSError as exc:
        # A machine with no route out raises this as `[Errno -3] Temporary
        # failure in name resolution`, which is Python's words for a
        # situation the reader can act on and cannot act on from that
        # sentence. Clusters are commonly airgapped and this is the first
        # thing a run does there, so it is worth saying plainly.
        raise ValueError(
            f"Could not fetch {pdb_id.upper()} from RCSB: {exc}.\n\n"
            "Where the machine has no route to the internet -- a cluster "
            "compute node, commonly -- fetch the structure somewhere that "
            "does and pass the file:\n\n"
            f"    curl -O {url}\n\n"
            "then name it in place of the identifier. The path is read from "
            "where fastmdx was run."
        ) from exc
    return dest


#: Where each record type carries its chain identifiers, by column, from the
#: PDB format specification. Records naming a chain that was dropped describe
#: a structure the file no longer holds.
_CHAIN_COLUMNS = {
    "HELIX": (19, 31),
    "SHEET": (21, 32),
    "SSBOND": (15, 29),
    "LINK": (21, 51),
}

#: How near a heterogen has to come to a kept chain to be kept with it.
#: A ligand in a binding site is within a couple of Angstroms of the residues
#: that hold it; 5 A also catches one sitting in a shallow surface pocket
#: without reaching across to a neighbour that was not selected.
_LIGAND_KEEP_DISTANCE_NM = 0.5


def _select_chains(
    input_pdb: Path, chains: str | list[str], *, presenter: Any = None
) -> Path:
    """Keep only the named chains, and the heterogens that belong to them.

    A deposited entry is what crystallography or cryo-EM produced, not what
    anyone means to simulate. 5WYZ holds a biological dimer and both copies
    are wanted; 6B73 holds two copies of a receptor-G protein complex whose
    two-fold is not perpendicular to the membrane, so both together cannot be
    embedded and one alone can. Without a way to say which chains, the only
    options were all of them or a hand-edited file.

    Heterogens are kept by proximity rather than by their own chain ID. An
    entry is free to number a ligand into a chain of its own, so matching IDs
    would drop the ligand out of a kept binding site and the run would go on
    without it -- a wrong answer that completes, which is the worst kind.
    """
    import numpy as np

    # Split every element, not just a bare string. The field is declared a
    # list, so `--setup-chains A,C` arrives as ["A,C"] -- one element holding
    # two names -- while a config's `chains: [A, C]` arrives as two. Handling
    # only the outer shape read "A,C" as a single chain and refused a
    # structure that has both.
    given = [chains] if isinstance(chains, str) else list(chains or [])
    wanted = {
        part.strip()
        for element in given
        for part in str(element).replace(" ", ",").split(",")
        if part.strip()
    }
    if not wanted:
        return input_pdb

    lines = input_pdb.read_text(encoding="utf-8").splitlines()

    def chain_of(line: str) -> str:
        return line[21:22].strip()

    def coordinates(line: str) -> tuple[float, float, float]:
        return (float(line[30:38]) / 10.0, float(line[38:46]) / 10.0,
                float(line[46:54]) / 10.0)

    kept_chain_atoms: list[tuple[float, float, float]] = []
    present: set[str] = set()
    for line in lines:
        record = line[:6].strip().upper()
        if record not in {"ATOM", "HETATM"}:
            continue
        present.add(chain_of(line))
        if record == "ATOM" and chain_of(line) in wanted:
            try:
                kept_chain_atoms.append(coordinates(line))
            except ValueError:
                continue

    missing = sorted(wanted - present)
    if missing:
        raise ValueError(
            f"No chain named {', '.join(missing)} in this structure. It has "
            f"{', '.join(sorted(c for c in present if c))}."
        )
    if not kept_chain_atoms:
        raise ValueError(
            f"Chains {', '.join(sorted(wanted))} hold no polymer atoms, so "
            "there would be nothing to simulate."
        )

    anchors = np.asarray(kept_chain_atoms, dtype=float)

    def near_a_kept_chain(point: tuple[float, float, float]) -> bool:
        distances = np.linalg.norm(anchors - np.asarray(point), axis=1)
        return bool(distances.min() <= _LIGAND_KEEP_DISTANCE_NM)

    # One decision per residue, so a ligand is kept or dropped whole.
    verdicts: dict[tuple[str, str, str], bool] = {}
    for line in lines:
        if line[:6].strip().upper() != "HETATM":
            continue
        key = (chain_of(line), line[17:20].strip(), line[22:27].strip())
        if key in verdicts:
            continue
        if key[0] in wanted:
            verdicts[key] = True
            continue
        residue_points = [
            coordinates(other) for other in lines
            if other[:6].strip().upper() == "HETATM"
            and (chain_of(other), other[17:20].strip(), other[22:27].strip()) == key
        ]
        verdicts[key] = any(near_a_kept_chain(point) for point in residue_points)

    out: list[str] = []
    for line in lines:
        record = line[:6].strip().upper()
        if record == "ATOM":
            if chain_of(line) in wanted:
                out.append(line)
        elif record == "HETATM":
            key = (chain_of(line), line[17:20].strip(), line[22:27].strip())
            if verdicts.get(key):
                out.append(line)
        elif record == "SEQRES":
            # The declared sequence, per chain, in column 12. Dropping these
            # left PDBFixer with nothing to compare the model against, so
            # `findMissingResidues` found none: no terminal residues declined
            # -- correct by accident -- and no internal loops built either,
            # which is a chain break left in a structure that then simulated
            # anyway. Kept for the chains that survive; the numbering inside
            # each record is per chain, so a subset needs no renumbering.
            if line[11:12].strip() in wanted:
                out.append(line)
        elif record in {"HELIX", "SHEET", "SSBOND", "LINK"}:
            # Secondary structure and connectivity, also per chain. PDBFixer
            # does not read them, but a record naming a chain that is no
            # longer here describes a structure this file is not.
            if any(line[position:position + 1].strip() in wanted
                   for position in _CHAIN_COLUMNS.get(record, ())):
                out.append(line)
        elif record in {"TER", "END", "CRYST1", "MODEL", "ENDMDL"}:
            out.append(line)

    dropped_chains = sorted(c for c in present if c and c not in wanted)
    carried = sorted({
        f"{name}{seq}" for (chain, name, seq), keep in verdicts.items()
        if keep and chain not in wanted
    })
    logger.info(
        "Keeping chain(s) %s; dropping %s.%s",
        ", ".join(sorted(wanted)),
        ", ".join(dropped_chains) if dropped_chains else "nothing",
        (f" Carried with them: {', '.join(carried)}, which sit within "
         f"{_LIGAND_KEEP_DISTANCE_NM * 10:.0f} A of a kept chain."
         if carried else ""),
    )

    target = input_pdb.with_name("input_selected.pdb")
    target.write_text("\n".join(out) + "\n", encoding="utf-8")
    return target


def _resolve_input(
    system: str, input_form: str, setup_dir: Path
) -> Path:
    """Place the source PDB at ``setup_dir/input.pdb``. Returns its path."""
    target = setup_dir / "input.pdb"
    if input_form == "pdb_file":
        shutil.copy2(system, target)
    elif input_form == "pdb_id":
        _fetch_pdb_from_rcsb(system, target)
    elif input_form == "sequence":
        # Sequence -> PDB generation needs a builder (PyRosetta, Modeller,
        # AlphaFold). Out of scope for v0.2; record and bail.
        raise NotImplementedError(
            "A structure cannot be built from a sequence here: that needs a "
            "structure predictor, which this software does not carry. Predict "
            "the structure first -- with AlphaFold, ESMFold or a homology "
            "model -- and pass the resulting file, or give a 4-character PDB "
            "ID for a deposited structure."
        )
    else:
        raise ValueError(f"Unknown input_form {input_form!r}")
    return target



def _keep_heterogens(params: dict, input_pdb) -> bool:
    """Resolve the heterogen policy, reporting what it implies.

    ``drop`` and ``keep`` are unconditional and match the historical options.
    ``auto`` inspects the structure and decides per component, refusing to
    proceed when the structure does not determine the answer.
    """

    policy = str(params.get("heterogens", "auto")).strip().lower()
    if params.get("keep_heterogens"):
        policy = "keep"

    if policy == "keep":
        return True

    if policy == "auto":
        # Components worth simulating have already been written as ligand
        # files and routed through the small-molecule path, so the originals
        # are stripped here and re-added with real parameters.
        return False

    if policy != "drop":
        raise ValueError(
            f"heterogens: unknown policy {policy!r}; expected drop, keep, or auto"
        )
    return False




def _explicit_ligand_resnames(params: dict) -> tuple[str, ...]:
    """Component codes for chemistry the caller supplied by hand.

    A caller who passes `ligand:` themselves never reaches `_auto_ligands`,
    which is the only thing that records what preparation will re-add. Left
    unset, PDBFixer's report warned that the ligand had been removed one line
    before it was loaded and placed -- the message the kept/discarded split in
    `pdbfix` exists to prevent. The route that got the wrong sentence was the
    offline one: no network, so the SDF is handed over by hand.

    Returns empty where the resname is not stated, which is the honest answer:
    the file's own residue name is not read until parameterisation, and
    guessing here would risk vouching for a component that was genuinely
    dropped.
    """
    # Accepted under either spelling. The collective-variable blocks call
    # this `ligand_resname`, and one concept answering to two words
    # depending on which block it sits in is a thing to remember for no
    # reason.
    named = params.get("ligand_name") or params.get("ligand_resname")
    if not named:
        return ()
    names = [named] if isinstance(named, str) else list(named)
    return tuple(sorted({str(n).upper() for n in names if n}))


def _validate_ligand_forcefield(params: dict) -> None:
    """Ligand parameterization needs a force field that can do it.

    Checked before any OpenMM or OpenFF import, so the message is the same
    whichever backends happen to be installed.
    """
    from fastmdxplora.setup.forcefields import (
        available_forcefields,
        resolve_forcefield,
    )

    if params["force_field"]:
        raise ValueError(
            "A ligand was supplied with a raw `force_field` XML list. "
            "Protein-ligand parameterization needs the named `forcefield` "
            "selector (use forcefield='amber-openff'), which wires the OpenFF "
            "small-molecule generator."
        )
    choice = resolve_forcefield(params["forcefield"])
    if not choice.supports_ligand:
        valid = ", ".join(
            n for n in available_forcefields() if resolve_forcefield(n).supports_ligand
        )
        raise ValueError(
            f"Force field {choice.name!r} does not support ligands. For "
            f"protein-ligand systems use a ligand-capable force field: {valid}."
        )



#: One repaired complex per (structure, setup directory, pH), because that is
#: what determines it. `_repaired_complex` is called once per ligand copy and
#: writes a fixed path from unchanging arguments, so every call after the first
#: rebuilt a file identical to the one already on disk. On 5WYZ -- TLR8, a
#: homodimer carrying the same ligand in both chains -- PDBFixer takes 475 s,
#: and it ran twice; 6B73, with three ligands, ran it four times. The key holds
#: the setup directory, so runs in one batch process do not see each other's.
_REPAIRED_COMPLEXES = {}


def _repaired_complex(input_pdb, setup_dir, ph: float):
    """A structure with missing atoms rebuilt, for the pKa calculation.

    Crystal structures routinely leave surface side chains unmodelled where
    the density is poor. Computing a pKa in a structure with those gaps means
    computing it in the wrong electrostatic environment, so the ligand and the
    protein are repaired first. Falls back to the deposition if repair fails,
    since a less accurate pKa is better than none.

    Repaired once per structure rather than once per ligand: the result
    depends on the structure and the pH, and neither changes between the
    copies of a ligand in one complex.
    """
    from fastmdxplora.setup.pdbfix import fix_pdb_with_pdbfixer

    # setup_dir is already the setup directory, as it is for the ligand
    # files: appending "setup" again buried this in setup/setup/.
    repaired = Path(setup_dir) / "complex_for_pka.pdb"
    key = (str(input_pdb), str(repaired), float(ph))

    remembered = _REPAIRED_COMPLEXES.get(key)
    if remembered is not None and Path(remembered).is_file():
        return remembered

    repaired.parent.mkdir(parents=True, exist_ok=True)
    try:
        fix_pdb_with_pdbfixer(
            str(input_pdb), str(repaired), ph=ph,
            keep_heterogens=True,   # the ligand must be present to be assessed
            keep_water=False,
        )
        _REPAIRED_COMPLEXES[key] = repaired
        return repaired
    except Exception as exc:  # noqa: BLE001 - fall back rather than fail
        logger.warning(
            "Could not repair the structure for the pKa calculation (%s); "
            "using the deposition as given, which may have unmodelled side "
            "chains near the site.", exc,
        )
        # Remembered too. A repair that failed will fail the same way for the
        # next copy of the same ligand, and saying so once is enough.
        _REPAIRED_COMPLEXES[key] = input_pdb
        return input_pdb



def _retain_in_structure(input_pdb, setup_dir, keep_decisions):
    """Write a structure holding the polymer plus the components to retain.

    PDBFixer keeps heterogens all or nothing, so selecting a few means
    filtering the input first. Everything the classifier discarded is dropped
    here; what remains is prepared by the protein force field, in place, under
    its own residue name, at its own coordinates.
    """
    keep = {d.resname.upper() for d in keep_decisions}
    target = Path(setup_dir) / "retained.pdb"
    target.parent.mkdir(parents=True, exist_ok=True)

    from fastmdxplora.setup.heterogens import ION_NAMES

    lines: list[str] = []
    dropped: set[str] = set()
    ions: set[str] = set()
    for raw in Path(input_pdb).read_text(
        encoding="utf-8", errors="ignore"
    ).splitlines():
        record = raw[:6].strip()
        if record == "HETATM" and raw[17:20].strip().upper() not in keep:
            dropped.add(raw[6:11].strip())
            continue
        if record in ("ATOM", "HETATM") and raw[17:20].strip().upper() in ION_NAMES:
            ions.add(raw[6:11].strip())
        lines.append(raw)

    # CONECT records name atoms by serial number, and OpenMM builds bonds from
    # them. Left pointing at atoms that are no longer here, they bond whatever
    # now holds those serials: a filtered structure produced a bond between
    # two zincs 9.6 A apart, which the force field rightly rejected. Any
    # record naming a removed atom is dropped with it.
    #
    # A record naming a retained ion is dropped too, and for a separate reason.
    # The legacy PDB format writes coordination and covalency with the same
    # record type -- only mmCIF separates them -- so a CONECT tying a metal to
    # the residue holding it asserts a bond that is not there. The classifier
    # above already applies that rule to LINK records; it holds here as well.
    # The force field could not honour the bond in any case: these ions are
    # modelled as Lennard-Jones plus a charge, with no bonded term to apply,
    # and OpenMM's ion templates permit no external bonds, so the bond makes
    # the ion unmatchable. 1ZNI reaches this, with two zincs and three
    # chlorides tied to each other.
    kept: list[str] = []
    coordination = 0
    for raw in lines:
        if raw[:6].strip() != "CONECT":
            kept.append(raw)
            continue
        serials = [raw[i:i + 5].strip() for i in range(6, min(len(raw), 31), 5)]
        present = [serial for serial in serials if serial]
        if any(serial in dropped for serial in present):
            continue
        if any(serial in ions for serial in present):
            coordination += 1
            continue
        kept.append(raw)

    if coordination:
        logger.info(
            "Dropped %d connectivity record(s) naming a retained ion: they "
            "describe coordination rather than a covalent bond, which the "
            "non-bonded ion parameters already represent.",
            coordination,
        )

    target.write_text("\n".join(kept) + "\n", encoding="utf-8")
    return target


def _auto_ligands(params: dict, input_pdb, setup_dir, entry_id: str | None) -> list[str]:
    """Turn the classifier's SIMULATE decisions into files the ligand path takes.

    The chemistry a structure omits is fetched from RCSB, completed with
    hydrogens, and checked for a determined protonation in the complex. What
    comes back is an ordinary ligand file, so everything downstream is the
    existing protein-ligand route rather than a parallel one.

    Returns the ligand files written, empty when the structure holds nothing
    worth simulating.
    """
    from fastmdxplora.setup.ccd import fetch_chemistry
    from fastmdxplora.setup.forcefields import (
        available_forcefields,
        resolve_forcefield,
    )
    from fastmdxplora.setup.heterogens import (
        WATER_NAMES, Action, resolve, summarize,
    )
    from fastmdxplora.setup.protonation import (
        POISED_MARGIN, apply_settled_state, settle,
    )

    decisions = resolve(input_pdb, keep_water=bool(params.get("keep_water")))
    logger.info("Heterogen decisions:\n%s", summarize(decisions))
    simulate = [d for d in decisions if d.action is Action.SIMULATE]

    # A lone ion is kept and needs nothing fetched. Bond orders, formal
    # charges and aromaticity are what an SDF supplies and what a PDB cannot
    # express; a monatomic component has none of them, and the protein force
    # field carries its parameters directly. Asking for `ZN_ideal.sdf` asks
    # for a file describing bonds that do not exist -- and 4INS, which is
    # insulin with two structural zincs, could not be prepared at all.
    ions = [d for d in simulate if d.is_monatomic]
    if ions:
        logger.info(
            "Keeping %s as %s: a lone ion has no chemistry to retrieve, and "
            "the force field carries its parameters.",
            ", ".join(f"{d.resname} x{d.count}" if d.count > 1 else d.resname
                      for d in ions),
            "ions" if len(ions) > 1 or ions[0].count > 1 else "an ion")

    # And water, for the same reason with more force behind it. The water
    # model *is* part of the force field -- `amber14/tip3p.xml` carries HOH
    # directly -- so `HOH_ideal.sdf` describes a molecule the solvent is
    # already parameterising. Retained crystallographic water arrives here
    # because `keep_water: true` marks it SIMULATE, and the ion exemption
    # directly above was written for exactly this shape and never extended
    # to it.
    #
    # Both outcomes were wrong and the quieter one was worse. From a local
    # file the run refused, advising the user to `curl` chemistry for water.
    # From a PDB identifier the fetch succeeds, and the retained waters are
    # parameterised by openff as a small molecule -- a different water model
    # from the solvent poured around them, in the same box, with nothing
    # said.
    waters = [d for d in simulate if d.resname in WATER_NAMES]
    if waters:
        logger.info(
            "Keeping %s as water: the water model is part of the force "
            "field, so there is no chemistry to retrieve and no separate "
            "parameterisation to give it.",
            ", ".join(f"{d.resname} x{d.count}" if d.count > 1 else d.resname
                      for d in waters),
        )

    wanted = [d for d in simulate
              if not d.is_monatomic and d.resname not in WATER_NAMES]
    if not wanted:
        return []

    names = ", ".join(
        f"{d.resname} x{d.count}" if d.count > 1 else d.resname for d in wanted
    )

    if entry_id is None:
        raise ValueError(
            f"heterogens: auto identified components to simulate ({names}), but "
            "their chemistry can only be retrieved for a structure given by PDB "
            "identifier: a local file carries no entry to look them up in. "
            "Supply the ligands as SDF or MOL2 files instead. The "
            "dictionary's own entry for each is a file:\n\n"
            "    curl -O https://files.rcsb.org/ligands/download/XXX_ideal.sdf"
            "\n\n"
            "with XXX the component code. Its coordinates are idealised and "
            "do not matter: the ligand is placed where this structure has it."
        )

    # A ligand needs a force field that can parameterize small molecules. The
    # protein force field is a scientific choice, so it is not changed here on
    # the user's behalf.
    choice = resolve_forcefield(params.get("forcefield"))
    if not choice.supports_ligand:
        capable = ", ".join(
            n for n in available_forcefields() if resolve_forcefield(n).supports_ligand
        )
        raise ValueError(
            f"heterogens: auto identified components to simulate ({names}), but "
            f"force field {choice.name!r} cannot parameterize small molecules. "
            f"Choose a ligand-capable force field ({capable}), or set the "
            "heterogens policy to 'drop' to exclude them."
        )

    # Monatomic ions are kept in the structure rather than extracted. The
    # protein force field already carries validated ion parameters, and the
    # small-molecule route is both unnecessary and destructive: a zinc pulled
    # out to an SDF and re-added came back renamed, in a different chain, and
    # at coordinates that were not its coordination site.
    from fastmdxplora.setup.heterogens import ION_NAMES

    ions = [d for d in wanted if d.resname in ION_NAMES]
    molecules = [d for d in wanted if d.resname not in ION_NAMES]
    if ions:
        params["_retained_pdb"] = str(
            _retain_in_structure(input_pdb, setup_dir, ions)
        )
        logger.info(
            "Keeping %s in the structure; the protein force field provides "
            "ion parameters.",
            ", ".join(f"{d.resname} x{d.count}" if d.count > 1 else d.resname
                      for d in ions),
        )
    if not molecules:
        return []

    copies = [(d, het) for d in molecules for het in d.instances]
    # setup_dir is already the setup directory: input.pdb and prepared.pdb
    # sit directly in it. Appending "setup" again buried the ligands in
    # setup/setup/ligands.
    ligand_dir = Path(setup_dir) / "ligands"
    ligand_dir.mkdir(parents=True, exist_ok=True)

    files: list[str] = []
    names: list[str] = []
    charges: list[int] = []
    for decision, instance in copies:
        chemistry = fetch_chemistry(
            entry_id,
            decision.resname,
            chain=instance.chain,
            resseq=instance.resseq,
            expected_heavy_atoms=sum(
                1 for atom in instance.atoms if atom.element.upper() != "H"
            ),
        )
        # A pKa is a property of the environment, so the environment should be
        # the one that will be simulated: repaired, with the missing side
        # chains rebuilt. The raw deposition has gaps that would bias the
        # answer. Only built when the ligand is actually titratable, since
        # repairing costs a second and otherwise changes nothing.
        pka_structure = input_pdb
        if chemistry.titratable_groups:
            pka_structure = _repaired_complex(input_pdb, setup_dir, float(params["ph"]))

        state = settle(
            pka_structure,
            decision.resname,
            chain=instance.chain,
            resseq=instance.resseq,
            ph=float(params["ph"]),
            expected_ionizable=bool(chemistry.titratable_groups),
            margin=float(params.get("protonation_margin") or POISED_MARGIN),
            known_groups=tuple(chemistry.titratable_groups),
        )
        logger.info("%s %s%s: %s", decision.resname, instance.chain,
                    instance.resseq, state.reason)

        # The settled state has to be the one that reaches the force field.
        sdf_text, net_charge = apply_settled_state(
            chemistry.path.read_text(encoding="utf-8"), chemistry, state
        )

        # Files are per copy, because two copies can settle into different
        # protonation states and each needs its own chemistry on disk.
        suffix = "" if len(copies) == 1 else f"_{instance.chain}{instance.resseq}"
        target = ligand_dir / f"{decision.resname}{suffix}.sdf"
        target.write_text(sdf_text, encoding="utf-8")
        files.append(str(target))
        # Names are per component, because that is what a residue name means.
        # This used to generate one per copy -- `C00`, `C01`, `O02` -- whenever
        # a structure held more than one ligand instance, even where the
        # components were already distinct. 6B73's cholesterol, opioid agonist
        # and oleic acid all lost their codes that way, and 5WYZ's two copies
        # of 7VF became `700` and `701`. The component code is the identifier
        # the entry uses, that the analyses select on, and that the page
        # displays; a generated one leaves the built system unable to say what
        # is in it. Two copies of one component share a name in a PDB and are
        # told apart by chain and residue number, as they are here.
        names.append(decision.resname)
        charges.append(net_charge)

    # Remembered so preparation can report these as re-added with parameters
    # rather than warning that they were removed.
    params["_reinstated_heterogens"] = tuple(
        sorted({decision.resname for decision, _ in copies})
    )
    # Every component the classifier judged, so preparation does not warn
    # about ones whose fate has already been reported with a reason.
    params["_explained_heterogens"] = tuple(sorted({d.resname for d in decisions}))
    params["ligand_name"] = names[0] if len(names) == 1 else names
    if params.get("ligand_net_charge") is None:
        params["ligand_net_charge"] = charges[0] if len(charges) == 1 else charges
    return files


def run(
    *,
    orchestrator: "FastMDXplora",
    output_dir: Path,
    **options: Any,
) -> list[str]:
    """Run the setup phase.

    Parameters
    ----------
    orchestrator : FastMDXplora
        The parent orchestrator instance.
    setup_dir : pathlib.Path
        Where to write setup artifacts.
    **options
        Per-call overrides of the module-level :data:`DEFAULTS`.

    Returns
    -------
    list of str
        Paths (relative to ``setup_dir``) of artifacts produced.
    """
    # The orchestrator hands each phase its own directory, under the name every
    # phase uses. Here it is the setup directory, and calling it output_dir
    # invited "setup" being appended to it -- which happened to the ligand
    # files, to the structure built for the pKa calculation, and would have
    # happened again.
    setup_dir = output_dir

    params: dict[str, Any] = {**DEFAULTS, **options}

    # One ligand, either spelling. The collective-variable blocks call this
    # `ligand_resname` and setup has always called it `ligand_name`, and a
    # user who named the ligand once should not have to learn the other
    # word to bias the thing they named. Read from `options` rather than
    # `params`, because `ligand_name` carries a default and a default must
    # not outrank something the user actually wrote.
    if options.get("ligand_resname") and not options.get("ligand_name"):
        params["ligand_name"] = options["ligand_resname"]

    # Force-field selection is either the named selector OR a raw XML list,
    # not both. Check the user-supplied options (not merged defaults), since
    # `forcefield` always has a default.
    if options.get("forcefield") is not None and options.get("force_field"):
        raise ValueError(
            "Specify either `forcefield` (a named force field) or "
            "`force_field` (a raw list of OpenMM XML files), not both. "
            "The named selector is recommended; the raw list is an escape "
            "hatch for combinations the named registry does not cover."
        )
    # Surface an unknown named force field early with a clear message
    # (only when a raw list is not overriding it).
    if not params["force_field"]:
        from fastmdxplora.setup.forcefields import resolve_forcefield

        resolve_forcefield(params["forcefield"])  # raises ValueError if unknown

    # Ligand / force-field coherence (pure logic — validate here, before any
    # OpenMM/OpenFF dependency, so the user gets a clear error regardless of
    # which backends are installed).
    if params.get("ligand"):
        _validate_ligand_forcefield(params)

    input_form = _classify_input(orchestrator.system)
    logger.debug("setup: input form detected as %s", input_form)

    presenter = getattr(orchestrator, "_presenter", None)
    artifacts: list[str] = []
    notes: list[str] = []
    # Set before anything can fail, so the manifest records "no structure"
    # rather than whatever the attribute happens not to be. A sequence input
    # and a fetch that never landed both end here, and both are honest.
    orchestrator._structure_provenance = None

    # ---- Stage 1: resolve input ----------------------------------------
    try:
        input_pdb = _resolve_input(orchestrator.system, input_form, setup_dir)

        # Recorded here rather than at the manifest, because this is the last
        # moment the file is the one that arrived: chain selection rewrites
        # it, and the question being answered is where the structure came
        # from, not what this run then did to it.
        orchestrator._structure_provenance = structure_provenance(
            orchestrator.system, input_form, input_pdb)

        if params.get("chains"):
            input_pdb = _select_chains(
                input_pdb, params["chains"], presenter=presenter)

        artifacts.append("input.pdb")
        if presenter:
            label = (
                orchestrator.system if input_form == "pdb_id"
                else Path(orchestrator.system).name
            )
            presenter.step(f"Loaded input: {label}")
    except NotImplementedError as exc:
        # Sequence input — manifest-only fallback
        (setup_dir / "input.sequence").write_text(f"{orchestrator.system}\n", encoding="utf-8")
        artifacts.append("input.sequence")
        notes.append(str(exc))
        if presenter:
            presenter.step(str(exc), status="warning")
        _write_manifest(setup_dir, orchestrator, input_form, params, artifacts, notes)
        artifacts.append("setup_parameters.json")
        return artifacts
    except Exception as exc:  # noqa: BLE001 -- network, IO, refusals
        # The manifest is written first, so whatever was learned before the
        # failure survives for diagnosis. Then the failure is raised.
        #
        # It used to be swallowed, on the reasoning that the manifest was the
        # source of truth. In practice that meant a mistyped PDB identifier
        # produced an empty output directory and exit code 0, and any refusal
        # raised here was reported as a successful run. A phase that could not
        # do its job must say so through the channel callers actually read.
        #
        # Note that this is a different situation from an absent optional
        # dependency, which is handled below and does degrade gracefully by
        # design: not having OpenMM installed is a choice, whereas failing to
        # fetch a structure is a failure.
        notes.append(f"Failed to resolve input ({input_form}): {exc}")
        _write_manifest(setup_dir, orchestrator, input_form, params, artifacts, notes)
        raise

    # With the auto policy, the structure decides what to simulate and the
    # chemistry it omits is retrieved before preparation runs, so the rest of
    # the phase sees an ordinary protein-ligand job.
    #
    # Deliberately outside the input-resolution try above: that handler
    # downgrades failures to a warning and carries on, which would turn a
    # refusal into a run that reports success while having simulated nothing
    # of the kind.
    if str(params.get("heterogens", "auto")).strip().lower() == "auto" \
            and not params.get("keep_heterogens") and not params.get("ligand"):
        discovered = _auto_ligands(
            params,
            input_pdb,
            setup_dir,
            orchestrator.system if input_form == "pdb_id" else None,
        )
        if discovered:
            params["ligand"] = discovered
            _validate_ligand_forcefield(params)
            logger.info(
                "Prepared %d ligand file(s) from the structure: %s",
                len(discovered), ", ".join(Path(d).name for d in discovered),
            )
    elif params.get("ligand"):
        params["_reinstated_heterogens"] = _explicit_ligand_resnames(params)

    # ---- Stage 2: PDBFixer (or skip via fixed_pdb) ---------------------
    prepared_pdb = setup_dir / "prepared.pdb"
    fixed_pdb = params.get("fixed_pdb")
    if fixed_pdb:
        # User supplied an already-fixed PDB — skip PDBFixer entirely.
        fixed_src = Path(fixed_pdb)
        if not fixed_src.exists():
            notes.append(f"fixed_pdb not found: {fixed_src}")
            if presenter:
                presenter.step(f"fixed_pdb not found: {fixed_src}", status="warning")
            _write_manifest(setup_dir, orchestrator, input_form, params, artifacts, notes)
            artifacts.append("setup_parameters.json")
            return artifacts
        shutil.copy2(fixed_src, prepared_pdb)
        artifacts.append("prepared.pdb")
        if presenter:
            presenter.step(f"Using supplied fixed PDB: {fixed_src.name} (PDBFixer skipped)")
    else:
        try:
            from fastmdxplora.setup.pdbfix import fix_pdb_with_pdbfixer

            # When ions are being kept, PDBFixer runs on a structure already
            # filtered to the polymer plus those ions, so "keep heterogens"
            # retains exactly them and nothing else.
            retained = params.get("_retained_pdb")
            fix_pdb_with_pdbfixer(
                retained or str(input_pdb),
                str(prepared_pdb),
                ph=float(params["ph"]),
                keep_heterogens=(True if params.get("_retained_pdb")
                                 else _keep_heterogens(params, input_pdb)),
                keep_water=bool(params["keep_water"]),
                reinstated=tuple(params.get("_reinstated_heterogens", ())),
                explained=tuple(params.get("_explained_heterogens", ())),
                replace_nonstandard=bool(params["replace_nonstandard_residues"]),
                build_missing_termini=bool(params.get("build_missing_termini", False)),
                mutations=tuple(params.get("mutations") or ()),
                mutation_chain=params.get("mutation_chain"),
            )
            artifacts.append("prepared.pdb")
            if presenter:
                presenter.step(f"Fixed PDB with PDBFixer (pH={params['ph']})",
                                  explain="protonation")
        except ImportError as exc:
            missing = missing_dependencies()
            notes.append(
                dependency_error_message(missing)
                if missing
                else f"PDBFixer unavailable: {exc}"
            )
            if presenter:
                presenter.step(
                    notes[-1],
                    status="warning",
                )
            _write_manifest(setup_dir, orchestrator, input_form, params, artifacts, notes)
            artifacts.append("setup_parameters.json")
            return artifacts

    # ---- Stage 3: Solvate, ionize, parameterize, serialize -------------
    try:
        from fastmdxplora.setup.prepare import prepare_system

        produced = prepare_system(
            prepared_pdb,
            setup_dir,
            forcefield=params["forcefield"],
            force_field=params["force_field"],
            water_model=params["water_model"],
            ligand=params["ligand"],
            ligand_forcefield=params["ligand_forcefield"],
            # A list of names, one per ligand, must not be stringified: doing
            # so produced a single "name" of "['Z00', 'Z01', 'Z02']" and
            # defeated multi-ligand support one line after it was added.
            ligand_name=(
                params["ligand_name"]
                if isinstance(params["ligand_name"], (list, tuple))
                else str(params["ligand_name"])
            ),
            ligand_net_charge=params["ligand_net_charge"],
            ligand_pose=str(params.get("ligand_pose", "auto")),
            check_ligand_clashes=bool(params["check_ligand_clashes"]),
            ligand_clash_threshold_nm=float(params["ligand_clash_threshold_nm"]),
            solvent_padding_nm=float(params["solvent_padding_nm"]),
            membrane=params.get("membrane"),
            membrane_orient=bool(params.get("membrane_orient", False)),
            membrane_orientation_checked=bool(
                params.get("membrane_orientation_checked", False)),
            box_shape=str(params["box_shape"]),
            ion_positive=str(params["ion_positive"]),
            ion_negative=str(params["ion_negative"]),
            ion_concentration_M=float(params["ion_concentration_M"]),
            neutralize=bool(params["neutralize"]),
            nonbonded_method=str(params["nonbonded_method"]),
            nonbonded_cutoff_nm=float(params["nonbonded_cutoff_nm"]),
            ewald_error_tolerance=float(params["ewald_error_tolerance"]),
            use_switching_function=bool(params["use_switching_function"]),
            switch_distance_nm=params["switch_distance_nm"],
            dispersion_correction=bool(params["dispersion_correction"]),
            remove_cm_motion=bool(params["remove_cm_motion"]),
            constraints=str(params["constraints"]),
            rigid_water=bool(params["rigid_water"]),
            hydrogen_mass_amu=params["hydrogen_mass_amu"],
            temperature_K=float(params["temperature_K"]),
        )

        # Only the files. What preparation returns now includes the system
        # size, which the manifest wants and this loop does not: it lists
        # artifacts, and a number is not one.
        for _key, path in produced.items():
            if isinstance(path, (str, Path)):
                artifacts.append(Path(path).relative_to(setup_dir).as_posix())
        if presenter:
            # What it resolved to, not what was asked for: "auto" tells a
            # reader nothing, and the run recorded the answer.
            resolved = (produced.get("resolved_forcefield") or {}).get("name")
            if params["force_field"]:
                ff_label = ", ".join(params["force_field"])
            elif resolved:
                ff_label = str(resolved)
            elif str(params["forcefield"]) == "auto":
                # Bare, because the step wraps it in parentheses. The banner
                # above already says it was chosen automatically, and
                # "(amber-openff (auto))" was the same nesting this replaced.
                from fastmdxplora.setup.forcefields import AUTO_FORCEFIELD

                ff_label = str(AUTO_FORCEFIELD)
            else:
                ff_label = str(params["forcefield"])
            presenter.step(f"Solvated and parameterized ({ff_label})",
                           explain=("membrane" if params.get("membrane")
                                    else "solvation"))
            presenter.step("Wrote system.xml, state.xml, topology.pdb")
    except ImportError as exc:
        missing = missing_dependencies()
        notes.append(
            dependency_error_message(missing)
            if missing
            else f"OpenMM unavailable for parameterization: {exc}"
        )
        if presenter:
            presenter.step(
                notes[-1],
                status="warning",
            )
        _write_manifest(setup_dir, orchestrator, input_form, params, artifacts, notes)
        artifacts.append("setup_parameters.json")
        return artifacts
    except Exception as exc:  # noqa: BLE001 -- template mismatches, box guards
        # The same rule stage 1 states: the manifest is written first, so
        # whatever was learned before the failure survives for diagnosis,
        # and then the failure is raised.
        #
        # Only the ImportError branch above did this, so a run that resolved
        # its structure and then failed in preparation -- a residue no
        # template matches, a cutoff too big for its box -- left an output
        # directory with no manifest at all. The record lost is exactly the
        # one diagnosis wants: which structure arrived, by digest and entry,
        # for the failures where "which structure was this" is the first
        # question. The two machines differed on this for a week -- one had
        # OpenMM and lost the manifest, one lacked it and kept it -- which is
        # what it looks like when only the graceful branch writes the record.
        notes.append(f"preparation failed: {exc}")
        _write_manifest(setup_dir, orchestrator, input_form, params, artifacts, notes)
        raise

    # ---- Stage 4: Manifest --------------------------------------------
    _write_manifest(setup_dir, orchestrator, input_form, params, artifacts, notes,
                    n_atoms_solvated=(produced or {}).get("n_atoms_solvated"))
    artifacts.append("setup_parameters.json")

    if presenter:
        presenter.step("Wrote setup_parameters.json")

    logger.debug("setup: wrote %d artifact(s) to %s", len(artifacts), setup_dir)
    return artifacts


def _write_manifest(
    setup_dir: Path,
    orchestrator: "FastMDXplora",
    input_form: str,
    params: dict[str, Any],
    artifacts: list[str],
    notes: list[str],
    n_atoms_solvated: int | None = None,
) -> None:
    """Write ``setup_parameters.json`` with the full provenance record."""
    # Record what the force-field selection actually resolved to, so the
    # manifest is reproducible regardless of whether the user picked a named
    # force field or passed a raw XML list.
    resolved_ff: dict[str, Any]
    if params.get("force_field"):
        resolved_ff = {
            "source": "explicit_xml_list",
            "xmls": list(params["force_field"]),
            "water_model": params.get("water_model"),
        }
    else:
        from fastmdxplora.setup.forcefields import resolve_forcefield

        try:
            choice = resolve_forcefield(params.get("forcefield"))
            resolved_ff = {
                "source": "named",
                "name": choice.name,
                "xmls": list(choice.xmls),
                "water_model": params.get("water_model") or choice.water_model,
                "supports_ligand": choice.supports_ligand,
                "small_molecule_forcefield": choice.small_molecule_forcefield,
            }
        except ValueError:
            resolved_ff = {"source": "named", "name": params.get("forcefield")}

    # Record ligand parameterization when a ligand was supplied.
    if params.get("ligand"):
        ligand_files = (
            params["ligand"] if isinstance(params["ligand"], list)
            else [params["ligand"]]
        )
        resolved_ff["ligand"] = {
            "files": [str(f) for f in ligand_files],
            "name": params.get("ligand_name", "LIG"),
            "small_molecule_forcefield": (
                params.get("ligand_forcefield")
                or resolved_ff.get("small_molecule_forcefield")
            ),
            "net_charge": params.get("ligand_net_charge"),
        }
    canonical = {
        "input_pdb": "input.pdb",
        "prepared_pdb": "prepared.pdb",
        "solvated_pdb": "solvated.pdb",
        "topology_pdb": "topology.pdb",
        "system_xml": "system.xml",
        "state_xml": "state.xml",
    }
    manifest = {
        "phase": "setup",
        "input": {
            "system": orchestrator.system,
            "form": input_form,
            # Which structure, past the point the string above still says.
            # Absent for a sequence, and for a fetch that never landed.
            "structure": getattr(orchestrator, "_structure_provenance", None),
        },
        "parameters": params,
        # How big the system ended up. A methods section has to state it, and
        # it was only ever logged -- so the report had to say it was not
        # recorded, of a number the run had printed to the terminal.
        "n_atoms_solvated": n_atoms_solvated,
        "resolved_forcefield": resolved_ff,
        "artifacts_planned": canonical,
        "artifacts_written": list(artifacts),
        "notes": notes,
    }
    with (setup_dir / "setup_parameters.json").open("w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, default=str)
