"""What would fix a refusal, and what the fix costs here.

A study that stops says why, and until now that was where it ended. The
person, or the Agent answering for them, then had to work out three further
things the software already knew: what to change, the command that runs the
change, and how long that takes on this machine. A stopped run is carried on
by `fastmdx resume`; an umbrella window that sampled too little is run again,
longer, with `--rerun-window`; a setting the schema refused has a complete
set of values it would take. And the study has measured its own speed, so
the price of each is arithmetic rather than a guess.

What a remedy may say is bounded by the registry, as every refusal is (see
:mod:`fastmdxplora.refusals`):

- ``permitted_values``: the setting and the values it takes;
- ``field_only``: the setting to change, never a value for it;
- ``action``: the exact step, where the refusal records one;
- ``nothing``: that the decision is the person's, and where a decision of
  that kind is recorded. Never what it should be.

The price is production, in nanoseconds, and the wall time that takes at
the speed this study ran (its own `cost.json`). That speed includes
minimisation and equilibration, so the time errs long. Where no speed was
ever measured here, no time is given: a figure from somewhere else would be
a guess with this study's name on it.
"""

from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from fastmdxplora.refusals import Disclosure, Refusal, code_for, known

__all__ = ["Price", "Remedy", "remedies_of", "remedy_for", "windows_again"]

#: The command that carries a study on from where it stopped.
RESUME = "fastmdx resume"

#: Where the person's decision is recorded, for refusals whose answer only
#: the person has. These name the place, never the answer: the registry
#: marks these codes ``nothing``, and a value suggested here would be the
#: software claiming chemistry it does not know. The sets are the ones each
#: refusal's own message already offers.
DECIDED_IN: dict[str, tuple[str, ...]] = {
    "setup.chemistry.protonation_undetermined": (
        "setup.ligand", "setup.ph", "setup.heterogens"),
    "setup.chemistry.charge_undetermined": ("setup.ligand_net_charge", "setup.ligand"),
    "setup.structure.residue_state_unmatched": ("setup.residue_states",),
    "setup.structure.mutation_mismatch": ("setup.mutations", "setup.mutation_chain"),
}

#: The setting a ``field_only`` or ``permitted_values`` refusal is about,
#: where its record does not name one itself.
SETTINGS: dict[str, tuple[str, ...]] = {
    "setup.chemistry.charge_contradicted": ("setup.ligand_net_charge", "setup.ligand"),
    "setup.structure.chain_unknown": ("setup.chains", "setup.mutation_chain"),
    "setup.structure.mutation_unparseable": ("setup.mutations",),
    "setup.structure.residue_state_unparseable": ("setup.residue_states",),
    "setup.structure.residue_state_not_permitted": ("setup.residue_states",),
    "setup.ligand.multiple_molecules": ("setup.ligand",),
    "setup.ligand.pose_unavailable": ("setup.ligand_pose",),
    "setup.ligand.clash": ("setup.ligand", "setup.ligand_clash_threshold_nm",
                           "setup.check_ligand_clashes"),
    "setup.forcefield.unknown": ("setup.forcefield",),
    "setup.forcefield.incompatible": ("setup.forcefield",),
    "setup.membrane.no_belt": ("setup.membrane",),
    "setup.membrane.orientation_unchecked": ("setup.membrane",),
    "setup.membrane.lipid_unparameterized": ("setup.membrane",),
    "setup.membrane.packing_failed": ("setup.random_seed", "setup.membrane",
                                      "setup.membrane_orient"),
    "environment.budget.absent": ("budget_hours",),
    "environment.budget.exhausted": ("budget_hours",),
}

#: What a retryable refusal waits for before `fastmdx resume` can succeed,
#: said of one run or of several.
_RETRY_AFTER = {
    "simulation.run.stopped": ("Carry it on from where it stopped.",
                               "Carry them on from where they stopped."),
    "simulation.run.interrupted": ("Carry it on from its last checkpoint.",
                                   "Carry them on from their last checkpoints."),
    "environment.service": ("Once the service answers again, carry it on.",
                            "Once the service answers again, carry them on."),
}
_RETRY_OTHERWISE = ("Once what stopped it has cleared, carry it on.",
                    "Once what stopped them has cleared, carry them on.")


@dataclass(frozen=True)
class Price:
    """What a fix runs, and how long that takes at this study's speed."""

    #: Production the fix runs, across every run it touches.
    production_ns: float
    #: Equilibration it runs too, in all, where it starts runs again from
    #: the top.
    equilibration_ns: float = 0.0
    #: How many runs the production is spread over.
    runs: int = 1
    #: Wall time one after another at this study's own speed, or None
    #: where no speed was measured here.
    seconds: float | None = None
    #: Where that speed was measured ("CUDA", "CPU").
    platform: str = ""
    #: Whether the production is a floor rather than an estimate.
    lower_bound: bool = False

    def as_text(self) -> str:
        if self.production_ns <= 0 and self.equilibration_ns <= 0:
            return "no simulation"
        amount = f"{'at least ' if self.lower_bound else ''}{_ns(self.production_ns)} of production"
        if self.equilibration_ns > 0:
            amount += f" and {_ns(self.equilibration_ns)} of equilibration"
        if self.runs > 1:
            amount += f" across {self.runs} runs"
        if self.seconds is None:
            return amount + "; no speed has been measured here, so no time is given"
        where = f" on {self.platform}" if self.platform else ""
        together = " one after another" if self.runs > 1 else ""
        return (f"{amount}; at this study's own speed{where}, "
                f"about {_duration(self.seconds)}{together}")

    def as_record(self) -> dict[str, Any]:
        return {"production_ns": self.production_ns,
                "equilibration_ns": self.equilibration_ns, "runs": self.runs,
                "seconds": self.seconds, "platform": self.platform,
                "lower_bound": self.lower_bound}


@dataclass(frozen=True)
class Remedy:
    """A refusal, what would fix it, and what that costs."""

    #: The refusal answered, as the registry names it.
    code: str
    #: What it is about: "the study", a run, some windows.
    where: str
    #: The refusal, in its own first sentence.
    why: str
    #: What to do, in a sentence or two.
    fix: str
    #: The settings the fix changes. Values only where `permitted` gives them.
    settings: tuple[str, ...] = ()
    #: The complete set a setting takes, where the registry allows it.
    permitted: tuple[Any, ...] | None = None
    #: The one of `permitted` nearest what was given, where there is one:
    #: a spelling, offered, never applied.
    suggestion: Any = None
    #: A command that does it, ready to run.
    command: str = ""
    #: The same command as the arguments after `fastmdx`, where it is one
    #: this software runs: what the GUI runs when the person says to.
    #: Empty for anything else, an install command included, which is never
    #: run for the person.
    argv: tuple[str, ...] = ()
    #: A config that does it, where one does.
    config: dict[str, Any] | None = None
    #: Whether only the person can decide it.
    decision: bool = False
    price: Price | None = None
    #: The runs or windows it covers, by name.
    covers: tuple[str, ...] = field(default_factory=tuple)

    def as_text(self, *, with_config: bool = True) -> str:
        text = f"{self.where}: {self.why} Fix: {self.fix}"
        if self.command:
            text += f" Run: `{self.command}`."
        if self.price is not None:
            text += f" Costs {self.price.as_text()}."
        if with_config and self.config:
            import yaml

            block = yaml.safe_dump(self.config, sort_keys=False,
                                   default_flow_style=None).strip()
            text += f"\n```yaml\n{block}\n```"
        return text

    def as_lines(self) -> list[str]:
        """For the console: the refusal, then what fixes it and its price."""
        lines = [f"{self.where}: {self.why}", f"  Fix: {self.fix}"]
        if self.command:
            lines.append(f"  Run: {self.command}")
        if self.price is not None:
            lines.append(f"  Costs {self.price.as_text()}.")
        return lines

    def as_record(self) -> dict[str, Any]:
        return {"code": self.code, "where": self.where, "why": self.why,
                "fix": self.fix, "settings": list(self.settings),
                "permitted": list(self.permitted) if self.permitted is not None else None,
                "suggestion": self.suggestion,
                "command": self.command, "argv": list(self.argv), "config": self.config,
                "decision": self.decision, "covers": list(self.covers),
                "price": self.price.as_record() if self.price else None}


# ---------------------------------------------------------------------------
# One refusal
# ---------------------------------------------------------------------------
#: Refusals of a key rather than of a value: their permitted list is of keys.
_KEY_NOT_KNOWN = frozenset({"config.option.unknown"})


def _whose_keys(found: Refusal) -> str:
    context = found.details.get("context")
    if isinstance(context, str) and re.fullmatch(r"[a-z_]+", context):
        return f"`{context}` settings"
    return "top-level keys" if context == "top-level" else "keys allowed there"


def remedy_for(refusal: Refusal | dict[str, Any] | None, *,
               study: str | Path | None = None, where: str = "the study") -> Remedy:
    """What would fix one refusal, within what the registry lets be said.

    ``study`` is the folder the refusal was recorded in. With it, the fix
    is priced: a run started again from the top costs its whole plan, a
    stopped one what remains of it.
    """
    found = _as_refusal(refusal)
    root = Path(study).expanduser().resolve() if study else None
    spec = code_for(found.code)
    why = _first_sentence(found.message) or spec.summary
    if spec.retryable:
        return _resume(found.code, why, where, [root] if root else [], root)
    if found.code.startswith("analysis.sampling.") and root is not None:
        asked = _longer(found.code, why, where, root)
        if asked is not None:
            return asked

    settings: tuple[str, ...] = ()
    permitted = None
    suggestion = None
    command = ""
    decision = False
    if spec.disclosure == Disclosure.PERMITTED_VALUES:
        settings = _named(found) or SETTINGS.get(found.code, ())
        permitted = found.permitted
        near = found.details.get("suggestion")
        suggestion = near if permitted and near in permitted else None
        if found.code in _KEY_NOT_KNOWN and settings:
            # The refusal is of the key, so its fix renames the key; the
            # permitted list is of keys. "Set `simulation.temprature_K` to
            # one of: agent, barostat_frequency, ..." asked for a key's
            # value to be a key name.
            fix = (f"Rename `{settings[0]}` to `{near}`." if near and near in (permitted or ())
                   else f"Use one of the {_whose_keys(found)}: "
                        f"{', '.join(map(str, permitted or ()))}.")
        elif permitted and settings:
            fix = f"Set `{settings[0]}` to one of: {', '.join(map(str, permitted))}."
        elif permitted:
            fix = f"Use one of: {', '.join(map(str, permitted))}."
        else:
            fix = "Use one of the values the refusal lists."
    elif spec.disclosure == Disclosure.FIELD_ONLY:
        settings = _named(found) or SETTINGS.get(found.code, ())
        fix = (f"Change {_either(settings)}. The value is yours to choose; "
               "the refusal says what it has to satisfy."
               if settings else "Change what the refusal names.")
    elif spec.disclosure == Disclosure.ACTION:
        command = str(found.details.get("install_command") or "")
        path = found.details.get("path")
        if command:
            fix = "Install what it needs."
        elif path:
            fix = f"Correct the file it names, {path}, as the refusal says."
        else:
            fix = "Take the step the refusal names."
    else:
        decision = True
        settings = DECIDED_IN.get(found.code, ())
        fix = ("The software does not know the answer here, so it suggests "
               "nothing; the choice is yours.")
        if settings:
            fix += f" A choice of this kind is recorded in {_either(settings)}."
    price = _from_the_top(root, spec) if root is not None else None
    return Remedy(code=found.code, where=where, why=why, fix=fix,
                  settings=tuple(settings), permitted=permitted, suggestion=suggestion,
                  command=command, decision=decision, price=price)


def _as_refusal(refusal: Refusal | dict[str, Any] | None) -> Refusal:
    if isinstance(refusal, Refusal):
        return refusal if known(refusal.code) else Refusal("unclassified", refusal.message)
    record = dict(refusal or {})
    if not known(str(record.get("code") or "")):
        record["code"] = "unclassified"
    return Refusal.from_record(record)


def _named(found: Refusal) -> tuple[str, ...]:
    """The settings a refusal's own record names, as dotted paths."""
    context = found.details.get("context")
    names: list[str] = []
    for key in ("option", "options"):
        value = found.details.get(key)
        for item in value if isinstance(value, (list, tuple)) else [value]:
            if isinstance(item, str) and item:
                names.append(_dotted(item, context))
    return tuple(dict.fromkeys(names))


def _dotted(name: str, context: Any) -> str:
    if "." in name or not isinstance(context, str):
        return name
    if not re.fullmatch(r"[a-z_][a-z0-9_.]*", context):
        return name
    return f"{context}.{name}"


# ---------------------------------------------------------------------------
# A study, from its records
# ---------------------------------------------------------------------------
def remedies_of(study: str | Path) -> list[Remedy]:
    """What would fix each thing that stopped this study, from its records.

    Empty for a study that finished with nothing refused, and for one still
    running: a run in progress has not refused anything yet. Stopped runs
    come first, as one command for all of them, since `fastmdx resume`
    carries a whole study on at once.
    """
    root = Path(study).expanduser().resolve()
    from fastmdxplora.simulation.resume import _still_running

    try:
        if _still_running(root):
            return []
    except Exception:  # noqa: BLE001 - a record, not a verdict
        pass
    batch = _read(root / "batch_manifest.json")
    if isinstance(batch, dict):
        return _of_a_batch(root, batch)
    interrupted = _interrupted(root)
    if interrupted is not None:
        # Its record still says it is going: any Manifest is an earlier
        # run's, and says nothing of this one.
        return [interrupted]
    manifest = _read(root / "manifest.json")
    if not isinstance(manifest, dict):
        return []
    failed = _failed_phase(manifest)
    if failed is None:
        return []
    name, refusal = failed
    return [remedy_for(refusal, study=root, where=f"the study's {name}")]


#: The refusal said of a run that ended without recording its end.
INTERRUPTED_CODE = "simulation.run.interrupted"


def _interrupted(root: Path) -> Remedy | None:
    """`fastmdx resume` for a run that ended without saying so
    (`telemetry.how_the_run_ended`), or None. Where its process was seen to
    be gone, the GUI may run it; where only a day of silence says so, the
    run may be on another machine, and carrying it on is the person's to
    decide once they have looked there."""
    from fastmdxplora.gui.telemetry import how_the_run_ended

    try:
        ended = how_the_run_ended(root)
    except Exception:  # noqa: BLE001 - a record, not a verdict
        ended = None
    if ended is None:
        return None
    if ended == "process":
        why = ("The run ended without recording its end: its process is gone "
               "(the machine restarted, or it was ended by a scheduler or by hand).")
    else:
        why = ("Nothing has been written for a day, and no process on this machine "
               "runs it. If it runs on another machine or under a scheduler, look "
               "there first: two runs must not write to one study.")
    remedy = _resume(INTERRUPTED_CODE, why, "the study", [root], root)
    return remedy if ended == "process" else replace(remedy, decision=True)


def _of_a_batch(root: Path, batch: dict[str, Any]) -> list[Remedy]:
    planned = {str(p.get("run_id")): p for p in batch.get("planned") or []
               if isinstance(p, dict)}
    stopped: list[tuple[str, Path, str]] = []
    unstarted: list[tuple[str, Path]] = []
    windows: dict[int, tuple[str, Path, dict[str, Any] | None]] = {}
    remedies: list[Remedy] = []
    for run in batch.get("runs") or []:
        if not isinstance(run, dict):
            continue
        run_id = str(run.get("run_id") or "a run")
        folder = _folder_of(root, run)
        index = _window_index(planned.get(run_id))
        if run.get("status") == "skipped":
            said = str(run.get("message") or "")
            if said.startswith("Not started"):
                unstarted.append((run_id, folder))
            elif index is not None and said.startswith(("Not submitted", "Cancelled")):
                # Never run because another window failed: a window kept
                # must have its production, so these run with the failed one.
                windows[index] = (run_id, folder, None)
            continue
        if run.get("status") != "error":
            continue
        refusal = _refusal_of_run(run)
        if run.get("error_type") == "Stopped" or _retryable(refusal):
            stopped.append((run_id, folder, str(refusal.get("code") or "simulation.run.stopped")))
            continue
        if index is not None:
            windows[index] = (run_id, folder, refusal)
            continue
        remedies.append(remedy_for(refusal, study=folder, where=run_id))
    if stopped or unstarted:
        code = next((c for _, _, c in stopped if known(c)), "simulation.run.stopped")
        names = [run_id for run_id, _, _ in stopped] + [run_id for run_id, _ in unstarted]
        why = (f"{_counted(len(stopped), 'run')} stopped where "
               f"{'it' if len(stopped) == 1 else 'they'} can be carried on"
               if stopped else "")
        if unstarted:
            why += (" and " if why else "") + f"{_counted(len(unstarted), 'run')} did not start"
        why = why[0].upper() + why[1:] + "."
        if code != "simulation.run.stopped":
            why += f" {code_for(code).summary}"
        remedy = _resume(code, why, "the study",
                         [folder for _, folder, _ in stopped] + [f for _, f in unstarted], root,
                         unstarted=len(unstarted))
        remedies.insert(0, _covering(remedy, names))
    if any(refusal is not None for _, _, refusal in windows.values()):
        remedies.append(_failed_windows(root, batch, windows))
    remedies.extend(_umbrella(root, batch, planned, skip=set(windows)))
    return remedies


def windows_again(study: str | Path, windows: Any, *,
                  force_constant: Any = None, duration_ns: Any = None) -> Remedy:
    """Umbrella windows the person names, run again at settings they name.

    Not an answer to a refusal: the person has looked at the windows and
    wants some of them again, held harder or run longer. The values are
    theirs; the software checks them, builds the command from the study's
    own record (`resolved_config.yml`, which says what every window ran
    with) and prices it at the study's own speed. Every other window is
    kept. Refused where the study has no windows, a window is not one of
    its own, or a value is not a positive number.
    """
    import math

    from fastmdxplora.refusals import StudyError
    from fastmdxplora.simulation.umbrella import spring_unit

    root = Path(study).expanduser().resolve()
    batch = _read(root / "batch_manifest.json")
    planned = {str(p.get("run_id")): p for p in (batch or {}).get("planned") or []
               if isinstance(p, dict)} if isinstance(batch, dict) else {}
    folders = {index: _folder_of(root, {"output_dir": None, "run_id": run_id})
               for run_id, spec in planned.items()
               if (index := _window_index(spec)) is not None}
    if not folders:
        raise StudyError("Windows are run again in an umbrella study, and this study "
                         "has none.", code="config.option.inapplicable")
    try:
        indices = sorted({int(i) for i in (windows if isinstance(windows, (list, tuple))
                                           else [windows])})
    except (TypeError, ValueError):
        raise StudyError("Name the windows to run again by their numbers.",
                         code="config.option.wrong_type") from None
    if not indices:
        raise StudyError("Name the windows to run again.",
                         code="config.option.missing_companion")
    unknown = [i for i in indices if i not in folders]
    if unknown:
        raise StudyError(
            f"This study has no window {', '.join(str(i) for i in unknown)}. "
            f"Its windows are numbered {min(folders)} to {max(folders)}.",
            code="config.option.not_permitted")
    values: dict[str, float | None] = {}
    for name, given in (("force constant", force_constant), ("length", duration_ns)):
        if given is None or given == "":
            values[name] = None
            continue
        try:
            number = float(given)
        except (TypeError, ValueError):
            number = float("nan")
        if not (math.isfinite(number) and number > 0):
            raise StudyError(f"A window's {name} has to be a positive number; "
                             f"{given!r} was given.", code="config.option.wrong_type")
        values[name] = number
    held, length = values["force constant"], values["length"]

    source = root / "resolved_config.yml"
    if not source.is_file():
        config = batch.get("config")
        source = Path(str(config)) if config else source
    argv = ["explore", "-c", str(source), "--output", str(root)]
    if length is not None:
        argv += ["--simulate-duration-ns", f"{length:g}"]
    argv += ["--rerun-window", *(str(i) for i in indices)]
    if held is not None:
        argv += ["--rerun-force-constant", f"{held:g}"]

    first = next(iter(planned.values()), {})
    variable = (((first.get("options") or {}).get("simulation") or {}).get("umbrella")
                or {}).get("collective_variable")
    named = _windows_named(indices)
    fix = f"Run {named} again"
    if held is not None:
        fix += f", held at {held:g} {spring_unit(variable)}"
    if length is not None:
        fix += f"{',' if held is None else ''} with {_ns(length)} of production" + (
            " each" if len(indices) > 1 else "")
    fix += ", keeping every other window, and recombine the free energy."

    production = equilibration = 0.0
    seconds: float | None = 0.0
    platform = ""
    for index in indices:
        planned_ns, again = _planned(folders[index])
        ns = length if length is not None else planned_ns
        production += ns
        equilibration += again
        rate, measured = _speed(folders[index], root)
        platform = platform or measured
        seconds = None if rate is None or seconds is None else seconds + rate * (ns + again)
    return Remedy(
        code="", where=named, why="Asked for.", fix=fix,
        settings=tuple(s for s, v in (("simulation.umbrella.force_constant", held),
                                      ("simulation.duration_ns", length)) if v is not None),
        **_command(argv),
        price=Price(production_ns=_round(production), equilibration_ns=_round(equilibration),
                    runs=len(indices), seconds=seconds, platform=platform),
        covers=tuple(sorted(run_id for run_id, spec in planned.items()
                            if _window_index(spec) in indices)))


def _counted(n: int, noun: str) -> str:
    return f"{n} {noun}{'' if n == 1 else 's'}"


def _resume(code: str, why: str, where: str, folders: list[Path],
            study: Path | None, *, unstarted: int = 0) -> Remedy:
    """`fastmdx resume`, priced at what remains of each run it carries on."""
    said = next((fix for prefix, fix in _RETRY_AFTER.items()
                 if code == prefix or code.startswith(prefix + ".")), _RETRY_OTHERWISE)
    carried = len(folders) - unstarted
    family = said[carried > 1] if carried else ""
    if unstarted:
        family += (" " if family else "") + (
            "The run that did not start is run." if unstarted == 1
            else "The runs that did not start are run.")
    production = equilibration = 0.0
    seconds: float | None = 0.0
    platform = ""
    for folder in folders:
        left, again = _what_remains(folder)
        production += left
        equilibration += again
        rate, where_measured = _speed(folder, study)
        platform = platform or where_measured
        if rate is None or seconds is None:
            seconds = None
        else:
            seconds += rate * (left + again)
    price = (Price(production_ns=_round(production), equilibration_ns=_round(equilibration),
                   runs=len(folders), seconds=seconds, platform=platform)
             if folders else None)
    said = _command(["resume", str(study)]) if study else {"command": RESUME, "argv": ()}
    return Remedy(code=code, where=where, why=why,
                  fix=f"{family} Production already written is kept, and the "
                      "analyses run over the whole.",
                  **said, price=price)


def _longer(code: str, why: str, where: str, root: Path) -> Remedy | None:
    """More production, by what the withheld means asked for."""
    from fastmdxplora.simulation.sampling_ask import sampling_asked_for

    try:
        ask = sampling_asked_for(root)
    except Exception:  # noqa: BLE001 - a record, not a verdict
        ask = None
    if ask is None:
        return None
    return Remedy(
        code=code, where=where, why=why,
        fix=f"Extend the study in place by {_ns(ask.more_ns)}, which should give "
            f"{', '.join(ask.analyses)} enough independent samples.",
        settings=("simulation.extra_ns",), config=ask.config(root),
        price=Price(production_ns=ask.more_ns, seconds=ask.seconds,
                    platform=ask.platform, lower_bound=ask.lower_bound))


# ---------------------------------------------------------------------------
# Umbrella windows
# ---------------------------------------------------------------------------
def _failed_windows(root: Path, batch: dict[str, Any],
                    windows: dict[int, tuple[str, Path, dict[str, Any] | None]]) -> Remedy:
    """Windows that failed: what fixes the first, then run them again, with
    any never started because of it."""
    indices = sorted(windows)
    failed = [i for i in indices if windows[i][2] is not None]
    run_id, folder, refusal = windows[failed[0]]
    first = remedy_for(refusal, study=folder, where=run_id)
    then = ("Once that is decided, run" if first.decision else "Then run")
    fix = f"{first.fix} {then} {_windows_named(indices)} again, keeping the rest."
    if len(indices) > len(failed):
        fix += (f" {_counted(len(indices) - len(failed), 'window')} of those never "
                "started because of the failure, and a window kept must have its "
                "production.")
    return Remedy(code=first.code, where=_windows_named(failed), why=first.why,
                  fix=fix, settings=first.settings, permitted=first.permitted,
                  decision=first.decision,
                  **_rerun(root, batch, indices),
                  price=_windows_again(root, [windows[i][1] for i in indices]),
                  covers=tuple(windows[i][0] for i in indices))


def _umbrella(root: Path, batch: dict[str, Any], planned: dict[str, Any], *,
              skip: set[int]) -> list[Remedy]:
    """The free energy's own refusal, where it made one."""
    payload = _read(root / "pmf.json")
    if not isinstance(payload, dict) or not payload.get("refused"):
        return []
    why = _first_sentence(str(payload["refused"]))
    folders = {index: _folder_of(root, {"output_dir": None, "run_id": run_id})
               for run_id, spec in planned.items()
               if (index := _window_index(spec)) is not None}
    by_id = {index: run_id for run_id, spec in planned.items()
             if (index := _window_index(spec)) is not None}

    off = _windows_off_plan(planned, folders)
    if off:
        indices = sorted(off)
        return [Remedy(
            code="config.option.conflicting", where=_windows_named(indices), why=why,
            fix="Run those windows again with the settings the config now gives them, "
                "keeping the rest; or restore the settings they ran with, which costs "
                "nothing.",
            **_rerun(root, batch, indices),
            price=_windows_again(root, [folders[i] for i in indices]),
            covers=tuple(by_id[i] for i in indices))]

    thin = [t for t in payload.get("thin") or []
            if isinstance(t, dict) and int(t.get("window", -1)) not in skip]
    if thin:
        return [_thin_windows(root, batch, payload, thin, folders, by_id, why)]

    design = payload.get("next_study") or {}
    if isinstance(design, dict) and design.get("centres") and design.get("force_constants"):
        return [_a_new_design(root, payload, design, folders, why)]
    if skip:
        return []
    return [Remedy(
        code="simulation.windows.no_sampling", where="the free energy", why=why,
        fix="The refusal describes what each window sampled; no new set of windows "
            "could be designed from them.",
        settings=("simulation.umbrella.centres", "simulation.umbrella.force_constant"))]


def _thin_windows(root: Path, batch: dict[str, Any], payload: dict[str, Any],
                  thin: list[dict[str, Any]], folders: dict[int, Path],
                  by_id: dict[int, str], why: str) -> Remedy:
    """Windows that recorded too few values: longer, and by how much.

    A window's values after the discard grow in proportion to its
    production, so the length that gives the thinnest the values it needs
    is its own length scaled by the ratio. Rounded up; the others then have
    more than they need.
    """
    from fastmdxplora.simulation.resume import production_done_ns

    needed = int((payload.get("plan") or {}).get("minimum_samples") or 200)
    indices = sorted(int(t["window"]) for t in thin)
    longest = 0.0
    unscalable = False
    for entry in thin:
        index, samples = int(entry["window"]), int(entry.get("samples") or 0)
        folder = folders.get(index)
        try:
            done = production_done_ns(folder) if folder else 0.0
        except Exception:  # noqa: BLE001
            done = 0.0
        if folder is not None and done <= 0:
            done = _planned(folder)[0]
        if samples <= 0 or done <= 0:
            unscalable = True
            continue
        longest = max(longest, done * needed / samples)
    length = _round_up(longest) if longest > 0 else None
    several = len(indices) > 1
    if length is None:
        fix = (f"Run {'them' if several else 'it'} again for longer; how much longer "
               "cannot be read from a window that recorded nothing.")
        return Remedy(code="simulation.windows.no_sampling", where=_windows_named(indices),
                      why=why, fix=fix, settings=("simulation.duration_ns",),
                      **_rerun(root, batch, indices),
                      covers=tuple(by_id.get(i, str(i)) for i in indices))
    fix = (f"Run {'them' if several else 'it'} again with {_ns(length)} of production"
           f"{' each' if several else ''}, which should record the {needed} values a "
           f"histogram needs, keeping every other window.")
    if unscalable:
        fix += " One recorded nothing, so for it that length is a guess."
    rate, platform = _speed(folders.get(indices[0]), root)
    equilibration = _planned(folders.get(indices[0]))[1]
    seconds = None if rate is None else rate * len(indices) * (length + equilibration)
    return Remedy(
        code="simulation.windows.no_sampling", where=_windows_named(indices), why=why,
        fix=fix, settings=("simulation.duration_ns",),
        **_rerun(root, batch, indices, duration_ns=length),
        price=Price(production_ns=_round(length * len(indices)),
                    equilibration_ns=_round(equilibration * len(indices)), runs=len(indices),
                    seconds=seconds, platform=platform, lower_bound=unscalable),
        covers=tuple(by_id.get(i, str(i)) for i in indices))


def _a_new_design(root: Path, payload: dict[str, Any], design: dict[str, Any],
                  folders: dict[int, Path], why: str) -> Remedy:
    """Gaps between windows: the design the windows themselves measured."""
    centres = [round(float(c), 4) for c in design["centres"]]
    forces = [round(float(k)) for k in design["force_constants"]]
    covers = design.get("covers") or [centres[0], centres[-1]]
    worst = design.get("worst_predicted_overlap")
    fix = (f"Run a new study with the windows their sampling implies: {len(centres)} windows "
           f"from {float(covers[0]):g} to {float(covers[1]):g}")
    fix += (f", worst overlap {float(worst):.2f} predicted." if isinstance(worst, (int, float))
            else ".")
    fix += " Sampling these windows for longer would not close the gaps."
    some = next(iter(sorted(folders.items())), (None, None))[1]
    length, equilibration = _planned(some)
    rate, platform = _speed(some, root)
    seconds = None if rate is None else rate * len(centres) * (length + equilibration)
    return Remedy(
        code="simulation.windows.no_sampling", where="the windows", why=why, fix=fix,
        settings=("simulation.umbrella.centres", "simulation.umbrella.force_constant"),
        config={"simulation": {"umbrella": {"centres": centres, "force_constant": forces}}},
        price=Price(production_ns=_round(length * len(centres)),
                    equilibration_ns=_round(equilibration * len(centres)), runs=len(centres),
                    seconds=seconds, platform=platform))


def _windows_off_plan(planned: dict[str, Any], folders: dict[int, Path]) -> list[int]:
    from fastmdxplora.simulation.umbrella import plan_from_expanded, windows_off_the_plan

    plan = plan_from_expanded({"systems": [spec.get("options") or {}
                                           for spec in planned.values()]})
    if plan is None:
        return []
    return [index for index, _ in windows_off_the_plan(folders, plan)]


def _rerun(root: Path, batch: dict[str, Any], indices: list[int], *,
           duration_ns: float | None = None) -> dict[str, Any]:
    """`fastmdx explore --rerun-window` for these windows, as the command a
    person types and as the arguments the GUI runs it with."""
    config = batch.get("config")
    source = Path(str(config)) if config else None
    if source is None or not source.is_file():
        source = root / "resolved_config.yml"
    argv = ["explore", "-c", str(source), "--output", str(root)]
    if duration_ns is not None:
        argv += ["--simulate-duration-ns", f"{duration_ns:g}"]
    argv += ["--rerun-window", *(str(i) for i in indices)]
    return _command(argv)


def _command(argv: list[str]) -> dict[str, Any]:
    return {"command": "fastmdx " + shlex.join(argv), "argv": tuple(argv)}


def _windows_again(root: Path, folders: list[Path]) -> Price:
    """Windows run again from the top, each its whole plan."""
    production = equilibration = 0.0
    seconds: float | None = 0.0
    platform = ""
    for folder in folders:
        length, again = _planned(folder)
        production += length
        equilibration += again
        rate, measured = _speed(folder, root)
        platform = platform or measured
        seconds = None if rate is None or seconds is None else seconds + rate * (length + again)
    return Price(production_ns=_round(production), equilibration_ns=_round(equilibration),
                 runs=len(folders), seconds=seconds, platform=platform)


def _window_index(spec: Any) -> int | None:
    if not isinstance(spec, dict):
        return None
    block = ((spec.get("options") or {}).get("simulation") or {}).get("umbrella") or {}
    index = block.get("index") if isinstance(block, dict) else None
    return int(index) if isinstance(index, (int, float)) and not isinstance(index, bool) else None


def _windows_named(indices: list[int]) -> str:
    if len(indices) == 1:
        return f"window {indices[0]}"
    return "windows " + ", ".join(str(i) for i in indices[:-1]) + f" and {indices[-1]}"


# ---------------------------------------------------------------------------
# Records and arithmetic
# ---------------------------------------------------------------------------
def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _failed_phase(manifest: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    for phase in manifest.get("phases") or []:
        if isinstance(phase, dict) and phase.get("status") == "error":
            name = str(phase.get("name") or phase.get("phase") or "run")
            refusal = dict(phase.get("refusal") or {})
            refusal.setdefault("code", "unclassified")
            refusal.setdefault("message", str(phase.get("message") or ""))
            return name, refusal
    return None


def _refusal_of_run(run: dict[str, Any]) -> dict[str, Any]:
    for phase in run.get("phases") or []:
        if isinstance(phase, dict) and phase.get("status") == "error":
            refusal = dict(phase.get("refusal") or {})
            refusal.setdefault("code", "unclassified")
            refusal.setdefault("message", str(phase.get("message") or run.get("message") or ""))
            return refusal
    return {"code": "unclassified", "message": str(run.get("message") or "")}


def _retryable(refusal: dict[str, Any]) -> bool:
    code = str(refusal.get("code") or "")
    return known(code) and code_for(code).retryable


def _folder_of(root: Path, run: dict[str, Any]) -> Path:
    relative = run.get("output_dir_relative")
    if relative and (root / relative).is_dir():
        return (root / relative).resolve()
    given = run.get("output_dir")
    if given and Path(str(given)).is_dir():
        return Path(str(given)).resolve()
    return root / "runs" / str(run.get("run_id") or "")


def _covering(remedy: Remedy, names: list[str]) -> Remedy:
    from dataclasses import replace

    return replace(remedy, covers=tuple(names))


def _planned(folder: Path | None) -> tuple[float, float]:
    """(production, equilibration) in ns, as the run's own config plans them,
    resolved by the runner's own function: a run that names no length runs
    the runner's default, not none, and its equilibration is a number of
    steps, longer at a longer timestep."""
    import yaml

    from fastmdxplora.config.schema import SIMULATION
    from fastmdxplora.simulation.runner import plan_stages

    sim: dict[str, Any] = {}
    if folder is not None:
        try:
            config = yaml.safe_load((Path(folder) / "resolved_config.yml")
                                    .read_text(encoding="utf-8")) or {}
            sim = dict(config.get("simulation") or {})
        except (OSError, yaml.YAMLError, AttributeError):
            sim = {}

    def number(key: str, kind: type) -> Any:
        try:
            return None if sim.get(key) is None else kind(sim[key])
        except (TypeError, ValueError):
            return None

    timestep = number("timestep_fs", float) or float(
        SIMULATION.defaults().get("timestep_fs") or 2.0)
    steps = plan_stages(
        duration_ns=number("duration_ns", float), timestep_fs=timestep,
        nvt_steps=number("nvt_steps", int), npt_steps=number("npt_steps", int),
        production_steps=number("production_steps", int),
        nvt_duration_ns=number("nvt_duration_ns", float),
        npt_duration_ns=number("npt_duration_ns", float))
    per_ns = 1_000_000.0 / timestep
    return (steps["production_steps"] / per_ns,
            (steps["nvt_steps"] + steps["npt_steps"]) / per_ns)


def _what_remains(folder: Path) -> tuple[float, float]:
    """(production left, equilibration to run again) for a stopped run.

    Carried on from a checkpoint, a run needs only the rest of its
    production. One that stopped before production began starts again from
    the top, since setup and equilibration leave nothing to continue from.
    """
    from fastmdxplora.simulation.resume import extension_of

    planned, equilibration = _planned(folder)
    try:
        plan = extension_of(folder)
    except Exception:  # noqa: BLE001 - a record, not a verdict
        plan = None
    if plan is not None and plan.possible:
        return max(0.0, plan.production_planned_ns - plan.production_done_ns), 0.0
    done = plan.production_done_ns if plan is not None else 0.0
    if done > 0:
        return max(0.0, planned - done), 0.0
    return planned, equilibration


def _speed(folder: Path | None, study: Path | None) -> tuple[float | None, str]:
    """Seconds per nanosecond at the speed this study ran, and where.

    The run's own record first, then any other run of the study: replicas
    and windows share the machine, and a run that stopped before production
    measured nothing of its own.
    """
    from fastmdxplora.simulation.sampling_ask import _time_here

    candidates: list[Path] = []
    if folder is not None:
        candidates.append(Path(folder))
    if study is not None:
        candidates.append(Path(study))
        runs = Path(study) / "runs"
        if runs.is_dir():
            candidates.extend(sorted(p for p in runs.iterdir() if p.is_dir()))
    for candidate in dict.fromkeys(candidates):
        try:
            seconds, platform = _time_here(candidate, 1.0)
        except Exception:  # noqa: BLE001
            continue
        if seconds is not None and seconds > 0:
            return seconds, platform
    return None, ""


def _from_the_top(root: Path, spec: Any) -> Price | None:
    """A study run again from the top: its setup, equilibration and
    production. An analysis refusal reruns the analyses and no simulation."""
    if spec.phase in ("analysis", "report"):
        return Price(production_ns=0.0)
    production, equilibration = _planned(root)
    if production <= 0:
        return None
    rate, platform = _speed(root, root)
    seconds = None if rate is None else rate * (production + equilibration)
    return Price(production_ns=production, equilibration_ns=equilibration,
                 seconds=seconds, platform=platform)


def _first_sentence(message: str) -> str:
    text = " ".join(str(message or "").split())
    if not text:
        return ""
    match = re.search(r"(?<=[.!?])\s+(?=[A-Z`])", text)
    sentence = text[:match.start()] if match else text
    return sentence if sentence.endswith((".", "!", "?")) else sentence + "."


def _either(settings: tuple[str, ...]) -> str:
    quoted = [f"`{s}`" for s in settings]
    if len(quoted) == 1:
        return quoted[0]
    return ", ".join(quoted[:-1]) + f" or {quoted[-1]}"


def _round(value: float) -> float:
    """A sum of planned lengths, without the float noise of adding them."""
    return round(float(value), 9)


def _round_up(value: float) -> float:
    from fastmdxplora.simulation.sampling_ask import _round_up as up

    return up(value) if value > 0 else 0.0


def _ns(value: float) -> str:
    return f"{value:g} ns"


def _duration(seconds: float) -> str:
    from fastmdxplora.simulation.sampling_ask import _duration as said

    return said(seconds)
