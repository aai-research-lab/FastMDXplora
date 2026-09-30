"""The selection a clicked atom is, in the language a Config speaks.

Clicking an atom in the viewer said its residue, chain and name, and left the
person to write the selection for it: where `resid 189` and `resSeq 189` name
different residues in most deposited structures and nothing tells the two
apart (see docs/selections.md). This writes the selection for the residue and
for the atom, and checks each against the topology the analyses read, so
what is copied is what an analysis will select.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

__all__ = ["selection_for", "states_for", "topology_the_analyses_read"]

_NAME = re.compile(r"^[A-Za-z0-9'*+-]{1,8}$")


def topology_the_analyses_read(root: Path | str) -> Path | None:
    """The structure an analysis of this study selects atoms in: the one the
    analysis phase recorded, else the one written beside the trajectory,
    else the prepared system."""
    base = Path(root)
    try:
        manifest = json.loads((base / "analysis" / "analysis_manifest.json")
                              .read_text(encoding="utf-8"))
    except (OSError, ValueError):
        manifest = {}
    resolved = manifest.get("resolved") if isinstance(manifest.get("resolved"), dict) else {}
    for candidate in (resolved.get("topology"), manifest.get("topology_input")):
        if isinstance(candidate, str) and candidate and Path(candidate).is_file():
            return Path(candidate)
    for candidate in (base / "simulation" / "trajectory_topology.pdb",
                      base / "setup" / "topology.pdb"):
        if candidate.is_file():
            return candidate
    return None


def selection_for(root: Path | str, *, chain: str, resseq: Any, resname: str,
                  atom: str) -> dict[str, Any]:
    """The selections for a residue and one of its atoms, each checked.

    ``chain`` is the chain's letter as the viewer shows it, ``resseq`` the
    residue's number in the file, ``resname`` its name and ``atom`` the
    atom's. The chain is written as MDTraj's index, since that is what its
    selection language takes, and is found by letter and confirmed by the
    residue's name; where the letter does not find it, by number and name.
    """
    try:
        number = int(str(resseq).strip())
    except (TypeError, ValueError):
        return {"ok": False, "reason": "no residue number to select by"}
    resname = str(resname or "").strip().upper()
    atom = str(atom or "").strip()
    if not _NAME.match(resname) or (atom and not _NAME.match(atom)):
        return {"ok": False, "reason": "not a residue or atom name"}
    where = topology_the_analyses_read(root)
    if where is None:
        return {"ok": False, "reason": "no topology to check a selection against"}
    try:
        import mdtraj as md

        topology = md.load_topology(str(where))
    except Exception as exc:  # noqa: BLE001 - the panel says why
        return {"ok": False, "reason": f"could not read {where.name}: {exc}"}

    matches = [residue for residue in topology.residues
               if residue.resSeq == number and residue.name.upper() == resname]
    if not matches:
        water = resname in ("HOH", "WAT", "SOL", "TIP3", "T3P", "SPC")
        return {"ok": False, "against": where.name,
                "reason": (f"{resname} {number} is not in {where.name}, the topology "
                           "the analyses read"
                           + (": water is not saved with the trajectory unless "
                              "simulation.save_selection keeps it" if water else ""))}
    lettered = [r for r in matches if str(getattr(r.chain, "chain_id", "") or "") == str(chain)]
    chosen = lettered or matches
    chains = {r.chain.index for r in chosen}
    several_chains = topology.n_chains > 1
    if len(chains) > 1:
        return {"ok": False, "against": where.name,
                "reason": f"{resname} {number} is in more than one chain, and chain "
                          f"{chain!r} does not say which"}
    index = chains.pop()
    wanted = next(r for r in chosen if r.chain.index == index)
    residue = (f"chainid {index} and resSeq {number}" if several_chains
               else f"resSeq {number}")
    if {topology.atom(i).residue.index for i in topology.select(residue)} != {wanted.index}:
        # Another residue shares the number (a ligand numbered 1 beside the
        # protein's first residue): the name tells them apart.
        residue += f" and resname {resname}"
    answer: dict[str, Any] = {"ok": True, "against": where.name,
                              "residue": {"selection": residue,
                                          "atoms": int(len(topology.select(residue)))}}
    if atom:
        # Quoted where the name carries a character the language would read
        # as an operator: O5' and the like.
        name = atom if re.fullmatch(r"[A-Za-z0-9]+", atom) else f'"{atom}"'
        one = f"{residue} and name {name}"
        try:
            count = int(len(topology.select(one)))
        except Exception:  # noqa: BLE001 - a name the language cannot take
            count = 0
        if count:
            answer["atom"] = {"selection": one, "atoms": count}
    return answer


#: The residue a name is, whatever state a force field named it by.
_FAMILY = {"HIS": "HIS", "HID": "HIS", "HIE": "HIS", "HIP": "HIS", "HSD": "HIS",
           "HSE": "HIS", "HSP": "HIS", "ASP": "ASP", "ASH": "ASP", "GLU": "GLU",
           "GLH": "GLU", "LYS": "LYS", "LYN": "LYS"}

#: Settings of a study that tie it to what that study made, left out of a
#: new study made from it: the prepared system, the segment carried on, who
#: wrote it.
_NOT_CARRIED = {"output", "agent", "agent_model"}
_NOT_CARRIED_SIMULATION = {"setup_from", "prepared_from", "resume_from", "extra_ns"}


def states_for(root: Path | str, *, chain: str, resseq: Any, resname: str) -> dict[str, Any]:
    """A clicked residue's protonation states, for a new study of the same
    structure with that residue set: the key `setup.residue_states` names it
    by, found in the structure setup builds (not in the viewer's copy, whose
    chains a force field may have renamed), the states it takes with what
    each is, and the study's own Config to start from."""
    import copy

    import yaml

    from fastmdxplora.setup.pdbfix import RESIDUE_STATE_MEANING, RESIDUE_STATES

    family = _FAMILY.get(str(resname or "").strip().upper())
    if family is None:
        return {"ok": False, "reason": f"{resname} has no protonation state to choose; "
                                       "HIS, ASP, GLU and LYS do."}
    try:
        number = int(str(resseq).strip())
    except (TypeError, ValueError):
        return {"ok": False, "reason": "no residue number"}
    base = Path(root)
    try:
        config = yaml.safe_load((base / "resolved_config.yml").read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        config = None
    if not isinstance(config, dict):
        return {"ok": False, "reason": "This study has no Config to start a new one from."}
    if isinstance(config.get("options"), dict):
        config = {**{k: v for k, v in config.items() if k != "options"}, **config["options"]}
    phases = config.get("include_phase")
    if isinstance(phases, list) and phases and "setup" not in phases:
        return {"ok": False, "reason": "This study did not prepare a structure, so a "
                                       "residue's state is not its to set."}
    systems = config.get("systems") or []
    system = systems[0].get("system") if systems and isinstance(systems[0], dict) else None
    if not system:
        return {"ok": False, "reason": "This study names no structure."}
    try:
        from fastmdxplora.gui.preview import structure_file, titratable_residues
        from fastmdxplora.setup.estimate import estimate_system

        built = titratable_residues(estimate_system(structure_file(str(system), None), {}).atoms)
    except Exception as exc:  # noqa: BLE001 - said, not raised
        from fastmdxplora.refusals import refusal_of

        return {"ok": False, "reason": f"{system} could not be read: {refusal_of(exc).message}"}
    matches = [r for r in built if r["resname"] == family and r["number"] == number]
    lettered = [r for r in matches if str(r["chain"]) == str(chain or "")]
    chosen = lettered or matches
    if len(chosen) != 1:
        return {"ok": False, "reason": (
            f"{family} {number} is not in {system} as setup builds it."
            if not chosen else
            f"{family} {number} is in more than one chain of {system}, and chain "
            f"{chain!r} does not say which.")}
    key = chosen[0]["key"]
    new = {k: copy.deepcopy(v) for k, v in config.items() if k not in _NOT_CARRIED}
    simulation = new.get("simulation")
    if isinstance(simulation, dict):
        new["simulation"] = {k: v for k, v in simulation.items()
                             if k not in _NOT_CARRIED_SIMULATION}
    setup = dict(new.get("setup") or {})
    current = (setup.get("residue_states") or {}).get(key) \
        if isinstance(setup.get("residue_states"), dict) else None
    new["setup"] = setup
    return {"ok": True, "key": key, "resname": family, "system": str(system),
            "current": current,
            "states": [{"state": s, "meaning": RESIDUE_STATE_MEANING.get(s, "")}
                       for s in RESIDUE_STATES[family]],
            "config": new}
