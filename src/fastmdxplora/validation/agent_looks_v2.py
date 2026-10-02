"""Whether the Agent's looking helps, on questions memory cannot answer.

The first registration (``preregistration/agent-looks.md``) asked about
well-known entries at the default settings, and an AI model answered every one
from memory, with tools and without. Its counts could not separate the arms,
and its rule took the number nearest the software's from a reply that gave a
range. This set, registered in ``preregistration/agent-looks-v2.md`` before
any reply to it was seen, changes both.

**The questions are about what no AI model has seen.** Four structure files are
made from Protein Data Bank entries here, at the start of a run: a trimmed
peptide, a fragment renumbered from 201, a kinase's chain with its ligand
renamed, and one chain of a haemoglobin. The settings asked about are not the
defaults. An AI model that answers from memory answers about the entry it
recognises, not the file.

**A reply is judged on the answer it commits to.** Each request ends by
asking for one line ``ANSWER:`` with the number alone, or the names alone (or
``none``). Only that line is judged: its first number for a number, and for
names the set it gives, which must be the software's exactly. A reply without
the line commits to nothing and does not agree.

Run it with an AI model chosen (`fastmdx agent model`), on a machine that can
fetch from the PDB::

    python -m fastmdxplora.validation.agent_looks_v2 --repeats 3 --out agent_looks_v2.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastmdxplora.validation.agent_looks import (Question, _histidines, _table, numbers_in,
                                                 run)

__all__ = ["FILES", "SETTINGS", "questions", "prepare", "committed", "judge_committed",
           "declined", "build_parser", "main"]

#: The files a run makes, and what each is made from.
FILES = {
    "trimmed.pdb": "1L2Y, first model, protein residues 1 to 12",
    "renumbered.pdb": "1UBQ, protein residues 10 to 60, renumbered 201 to 251",
    "kinase.pdb": "1AKE, chain A and its AP5, the ligand renamed LG7, no water",
    "alpha.pdb": "1HHO, chain A, protein only",
}

#: Settings that are not the defaults, asked about by name.
SETTINGS = {"solvent_padding_nm": 1.6, "ion_concentration_M": 0.3, "box_shape": "dodecahedron"}
SETTINGS_1UBQ = {"solvent_padding_nm": 2.0, "ion_concentration_M": 0.5,
                 "box_shape": "dodecahedron"}

_NUMBER = (" End your reply with one line that reads `ANSWER: ` followed by the number "
           "alone.")
_NAMES = (" End your reply with one line that reads `ANSWER: ` followed by the residue "
          "names alone, separated by commas, or `none`.")


def prepare(work: str | Path) -> dict[str, Path]:
    """The files, made from the PDB entries into ``work``."""
    import mdtraj as md

    from fastmdxplora.gui.preview import structure_file

    folder = Path(work).expanduser().resolve()
    folder.mkdir(parents=True, exist_ok=True)

    def load(entry: str) -> Any:
        return md.load(str(structure_file(entry, None)))

    made = {name: folder / name for name in FILES}
    trimmed = load("1L2Y")[0]
    trimmed.atom_slice(trimmed.topology.select("protein and resSeq 1 to 12")).save_pdb(
        str(made["trimmed.pdb"]))
    fragment = load("1UBQ")
    fragment = fragment.atom_slice(fragment.topology.select("protein and resSeq 10 to 60"))
    for residue in fragment.topology.residues:
        residue.resSeq += 191
    fragment.save_pdb(str(made["renumbered.pdb"]))
    kinase = load("1AKE")
    chain_a = kinase.topology.chain(0)
    ligand = next(r for r in kinase.topology.residues if r.name == "AP5")
    kinase = kinase.atom_slice([a.index for a in kinase.topology.atoms
                                if a.residue.chain is chain_a or a.residue is ligand])
    for residue in kinase.topology.residues:
        if residue.name == "AP5":
            residue.name = "LG7"
    kinase.save_pdb(str(made["kinase.pdb"]))
    alpha = load("1HHO")
    alpha.atom_slice(alpha.topology.select("chainid 0 and protein")).save_pdb(
        str(made["alpha.pdb"]))
    return made


def _estimate(system: str, setup: dict[str, Any]) -> dict[str, Any]:
    from fastmdxplora.gui.preview import preview_of_config

    answer = preview_of_config({"systems": [{"system": system}], "setup": dict(setup)})
    if not answer.get("ok"):
        from fastmdxplora.refusals import StudyError

        raise StudyError(f"No preview of {system}: {answer.get('reason')}",
                         code="setup.structure.unreadable")
    return answer["estimate"]


def _said(setup: dict[str, Any]) -> str:
    return (f"a {setup['box_shape']} box, {setup['solvent_padding_nm']:g} nm of solvent "
            f"padding and {setup['ion_concentration_M']:g} M NaCl")


def questions(files: dict[str, Path]) -> tuple[Question, ...]:
    """The registered questions, about the files a run made."""
    import mdtraj as md

    from fastmdxplora.structure_info import count_structure

    trimmed, renumbered = str(files["trimmed.pdb"]), str(files["renumbered.pdb"])
    kinase, alpha = str(files["kinase.pdb"]), str(files["alpha.pdb"])
    return (
        Question("particles_trimmed",
                 f"About how many atoms will the solvated system have if I simulate "
                 f"{trimmed} with {_said(SETTINGS)}?" + _NUMBER,
                 "preview_setup", lambda: _estimate(trimmed, SETTINGS)["particles"], 0.10,
                 "the estimate is within 3% of setup; 10% tells it from a guess"),
        Question("box_trimmed",
                 f"How wide will the box be for {trimmed} with {_said(SETTINGS)}, face to "
                 "face, in nm?" + _NUMBER,
                 "preview_setup", lambda: _estimate(trimmed, SETTINGS)["width_nm"], 0.05,
                 "a twentieth of the width is about a tenth of the padding"),
        Question("particles_1ubq_settings",
                 f"About how many atoms will the solvated system have if I simulate 1UBQ "
                 f"with {_said(SETTINGS_1UBQ)}?" + _NUMBER,
                 "preview_setup", lambda: _estimate("1UBQ", SETTINGS_1UBQ)["particles"], 0.10,
                 "as for the first"),
        Question("residues_renumbered",
                 f"How many protein residues does {renumbered} have?" + _NUMBER,
                 "inspect_structure",
                 lambda: count_structure(Path(renumbered))["protein_residues"], 0.0, "a count"),
        Question("selection_renumbered",
                 f"How many atoms does the selection `name CA and resSeq 1 to 10` match in "
                 f"{renumbered}?" + _NUMBER,
                 "check_selection",
                 lambda: int(len(md.load(renumbered).topology.select(
                     "name CA and resSeq 1 to 10"))), 0.0, "a count"),
        Question("ligands_kinase",
                 f"Which ligands does {kinase} contain? Give their residue names." + _NAMES,
                 "inspect_structure",
                 lambda: sorted(str(n) for n in
                                count_structure(Path(kinase)).get("ligand_resnames") or []),
                 0.0, "exactly the names the file gives"),
        Question("histidines_alpha",
                 f"A study of {alpha} with the default settings: how many histidines will "
                 "the system setup builds have?" + _NUMBER,
                 "inspect_structure", lambda: _histidines(alpha), 0.0, "a count"),
    )


_ANSWER = re.compile(r"\bANSWER\W{0,3}:\s*([^\n]*)", re.IGNORECASE)


def committed(reply: str) -> str | None:
    """The answer a reply commits to: what follows the last ``ANSWER:`` to
    the end of its line, without the emphasis or quoting around it, or None.
    Asked for on a line of its own, and read where it is."""
    found = _ANSWER.findall(reply or "")
    return found[-1].strip().strip("*`_ .") if found else None


def judge_committed(question: Question, truth: Any, reply: str) -> tuple[bool, str]:
    """Whether the answer a reply commits to is the software's, and why."""
    line = committed(reply)
    if line is None:
        return False, "commits to no answer (no ANSWER line)"
    if isinstance(truth, (list, tuple)):
        given = {name.upper() for name in re.split(r"[\s,;]+", line)
                 if name and name.lower() not in ("and", "none")}
        wanted = {str(name).upper() for name in truth}
        if given == wanted:
            return True, "names exactly " + (", ".join(sorted(wanted)) or "none")
        return False, (f"names {', '.join(sorted(given)) or 'none'} where the file has "
                       f"{', '.join(sorted(wanted)) or 'none'}")
    stated = numbers_in(line)
    if not stated:
        return False, f"commits to {line!r}, which is not a number"
    target, given_number = float(truth), stated[0]
    said = f"commits to {given_number:g} against {target:g}"
    if not target:
        return given_number == 0, said
    off = abs(given_number - target) / abs(target)
    within = off <= question.tolerance + 1e-12
    return within, said + ("" if within else f", {100 * off:.1f}% off")


def declined(trials: list[dict[str, Any]]) -> dict[str, int]:
    """Per arm, the answers that committed to nothing: no ``ANSWER:`` line,
    or for a number a line holding none."""
    out: dict[str, int] = {}
    for trial in trials:
        why = str(trial.get("why") or "")
        if trial.get("kind") == "answer" and (why.startswith("commits to no answer")
                                              or why.endswith("which is not a number")):
            out[trial["arm"]] = out.get(trial["arm"], 0) + 1
    return out


def _digests(files: dict[str, Path]) -> dict[str, str]:
    return {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in files.items()}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m fastmdxplora.validation.agent_looks_v2",
        description="Ask the configured AI model questions about files it has not seen, with "
                    "the software's tools and without, and count the answers it commits to "
                    "that agree with the software.")
    parser.add_argument("--repeats", type=int, default=3,
                        help="how many times each question is asked in each arm")
    parser.add_argument("--work", default="agent_looks_v2_files",
                        help="where the structure files are made (default: %(default)s)")
    parser.add_argument("--truths-only", action="store_true",
                        help="make the files and print the software's answers; ask nothing")
    parser.add_argument("--out", help="write every reply and the counts here, as JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    from fastmdxplora import __version__

    files = prepare(args.work)
    asked = questions(files)
    if args.truths_only:
        for question in asked:
            try:
                print(f"{question.name}: {question.truth()}")
            except Exception as exc:  # noqa: BLE001 - said, and the rest still printed
                print(f"{question.name}: could not be computed ({exc})")
        for name, digest in _digests(files).items():
            print(f"{name}: sha256 {digest}")
        return 0
    from fastmdxplora.agent import completion_for, load_choice

    chosen = load_choice()
    result = run(completion_for(), repeats=args.repeats, questions=asked,
                 judge_with=judge_committed)
    result.update({
        "declined": declined(result["trials"]),
        "registration": "preregistration/agent-looks-v2.md",
        "model": f"{chosen.provider}/{chosen.model}" if chosen is not None else None,
        "version": __version__, "repeats": args.repeats,
        "when": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "files": {name: {"made_from": FILES[name], "sha256": digest}
                  for name, digest in _digests(files).items()},
    })
    print(_table(result))
    for arm, count in sorted(result["declined"].items()):
        print(f"{arm}: {count} answer{'s' if count != 1 else ''} committed to nothing")
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=2, default=str)
        print(f"Every reply is in {args.out}.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
