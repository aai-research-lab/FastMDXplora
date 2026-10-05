"""What can be run again on a study where it is, and the command that does it.

A study's analysis and report are worth running again: an analysis added,
or the same ones under a release that analyses them better, and a report
that says what they now show. Neither touches the simulation. Each is run
by the phase command itself (`fastmdx analyze --output <study> --rerun`,
`fastmdx report --output <study> --rerun`), which starts from the settings
the study recorded and keeps what it replaces in `previous/<phase>`
(`fastmdxplora.replaced`). This module only checks, before anything runs,
what that command would do on a study, and names it, for the GUI's
**Analyze again** and **Write it again**, ``run_phases_again`` in
``fastmdx mcp`` and the Agent's ``DO: analyze again``:

- only phases whose inputs the study holds: an analysis needs the study's
  trajectory; a report reads whatever the study holds;
- an analysis run again writes the report again too where the study has
  one (`fastmdx explore --include-phase analysis report`), since a report
  from the analyses before would contradict the figures beside it;
- refused while any run of the study is going.

Setup and simulation are not offered: what follows them would describe a
system or frames that are no longer there. A new study made from this one
(``simulation.setup_from``, ``simulation.resume_from``) is the way to run
them again.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from fastmdxplora.refusals import StudyError
from fastmdxplora.replaced import PREVIOUS

__all__ = ["PHASES", "PREVIOUS", "Again", "analyses_named", "frames_of", "offered",
           "phases_named", "plan", "recorded_analyses"]

#: The phases offered to run again on a study, in the order they run.
PHASES = ("analysis", "report")
#: The words a phase is given by, on the command line and from an AI model.
_SAID = {"analysis": "analysis", "analyse": "analysis", "analyze": "analysis",
         "analyses": "analysis", "report": "report"}
#: Why the phases before are not offered, and what does it instead.
_NOT_IN_PLACE = (
    "Setup and simulation are not run again in a study that has them: the "
    "analysis and report that follow would then describe a system or frames "
    "that are no longer there. Start a new study from this one: "
    "`simulation.setup_from` names it to simulate its prepared system again, "
    "`simulation.resume_from` to carry its production on.")


@dataclass(frozen=True)
class Again:
    """What a run again will do, checked before anything runs."""

    study: Path
    phases: tuple[str, ...]
    analyses: tuple[str, ...] | None
    runs: tuple[Path, ...]
    #: The study of several runs whose comparison is built again after.
    campaign: Path | None = None
    #: The report was added because the study has one.
    report_added: bool = False
    #: Runs of a study of several with nothing to analyse, each with why.
    left_out: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    def command(self) -> list[str]:
        """The phase command that does it, as typed after ``fastmdx``."""
        where = ["--output", str(self.study)]
        if self.phases == ("report",):
            return ["report", *where, "--rerun"]
        chosen = list(self.analyses) if self.analyses is not None else []
        if self.phases == ("analysis",):
            return ["analyze", *where, "--rerun", *(["--analyses", *chosen] if chosen else [])]
        return ["explore", *where, "--include-phase", *self.phases, "--rerun",
                *(["--analyze-analyses", *chosen] if chosen else [])]

    def said(self) -> str:
        """What it does, in a sentence or three, for a person to agree to."""
        what = " and ".join("the report" if phase == "report" else "the analyses"
                            for phase in self.phases)
        where = (f"each of its {len(self.runs)} runs" if len(self.runs) > 1
                 else "this study")
        lines = [f"Run {what} again on {where}, from the study's records and the "
                 "settings it recorded. Nothing is simulated."]
        if self.analyses is not None and "analysis" in self.phases:
            lines.append("Analyses: " + ", ".join(self.analyses) + ".")
        if self.report_added:
            lines.append("The report is written again too, as the study has one: one "
                         "from the analyses before would contradict them.")
        kept = " and ".join(f"{phase}/" for phase in self.phases)
        lines.append(f"The {kept} there now {'are' if len(self.phases) > 1 else 'is'} kept "
                     f"in {PREVIOUS}/, in place of what was kept there before.")
        if self.campaign is not None and "analysis" in self.phases:
            lines.append("The comparison of the runs is built again after.")
        for name, why in self.left_out:
            lines.append(f"{name} {why}")
        return " ".join(lines)

    def as_dict(self) -> dict[str, Any]:
        return {"study": str(self.study), "phases": list(self.phases),
                "analyses": list(self.analyses) if self.analyses is not None else None,
                "runs": [str(run) for run in self.runs],
                "campaign": str(self.campaign) if self.campaign else None,
                "report_added": self.report_added,
                "left_out": [{"run": name, "why": why} for name, why in self.left_out],
                "said": self.said(), "command": ["fastmdx", *self.command()]}


def phases_named(given: Iterable[Any]) -> tuple[str, ...]:
    """The phases asked for, in the order they run; refused for any other."""
    named: set[str] = set()
    for word in given:
        text = str(word).strip().lower()
        phase = _SAID.get(text)
        if phase is None:
            if text in ("setup", "simulation", "simulate"):
                raise StudyError(_NOT_IN_PLACE, code="config.option.not_permitted",
                                 option="phases", context="again", given=text,
                                 permitted=list(PHASES))
            raise StudyError(f"{word!r} is not a phase that runs again: analysis or report.",
                             code="config.option.not_permitted", option="phases",
                             context="again", given=str(word), permitted=list(PHASES))
        named.add(phase)
    if not named:
        raise StudyError("Name a phase to run again: analysis, report, or both.",
                         code="config.option.not_permitted", option="phases", context="again",
                         given=None, permitted=list(PHASES))
    return tuple(phase for phase in PHASES if phase in named)


def analyses_named(given: Iterable[Any] | None) -> tuple[str, ...] | None:
    """The analyses asked for, checked against those this release has."""
    if given is None:
        return None
    import fastmdxplora.analysis  # noqa: F401  (fills the registry)
    from fastmdxplora.analysis.orchestrator import available_analyses

    known = available_analyses()
    chosen: list[str] = []
    for word in given:
        name = str(word).strip()
        if name not in known:
            from difflib import get_close_matches

            near = get_close_matches(name, known, n=1)
            raise StudyError(f"{name!r} is not an analysis this release has"
                             + (f"; did you mean {near[0]!r}?" if near else ".")
                             + f" It has: {', '.join(known)}.",
                             code="analysis.unknown", given=name, permitted=list(known),
                             suggestion=near[0] if near else None)
        if name not in chosen:
            chosen.append(name)
    if not chosen:
        raise StudyError("Choose at least one analysis to run again.",
                         code="config.option.not_permitted", option="analyses", context="again",
                         given=None, permitted=list(known))
    return tuple(chosen)


def _runs_of(campaign: Path) -> list[Path]:
    from fastmdxplora.gui.browse import is_study

    folder = campaign / "runs"
    if not folder.is_dir():
        return []
    return [run for run in sorted(folder.iterdir()) if run.is_dir() and is_study(run)]


def frames_of(run: Path) -> Path | None:
    """The trajectory an analysis of ``run`` reads, or None where it has none:
    its joined trajectory if it was extended, its own production, or a
    trajectory from outside it that it recorded analysing."""
    from fastmdxplora.analysis.analyze import study_trajectory

    joined, _ = study_trajectory(run)
    if joined is not None:
        return joined
    own = run / "simulation" / "production.dcd"
    if own.is_file() and own.stat().st_size > 0:
        return own
    from fastmdxplora.config import ConfigError
    from fastmdxplora.config.recorded import RECORDED, phase_settings

    try:
        named = phase_settings(run / RECORDED, "analysis", study=run).get("trajectory")
    except (ConfigError, OSError):
        return None
    if named and Path(str(named)).expanduser().is_file():
        return Path(str(named)).expanduser()
    return None


def _going(runs: Iterable[Path]) -> list[str]:
    from fastmdxplora.simulation.resume import _still_running

    return [run.name for run in runs if _still_running(run)]


def plan(study: str | Path, phases: Iterable[Any], analyses: Iterable[Any] | None = None) -> Again:
    """What running ``phases`` again on ``study`` will do; refused, with a
    code, where it cannot be done."""
    from fastmdxplora.batch.explorer import campaign_of
    from fastmdxplora.gui.browse import is_study

    root = Path(study).expanduser().resolve()
    if not root.is_dir() or not is_study(root):
        raise StudyError(f"{root} is not a study: it holds none of the records a study "
                         "writes.", code="environment.path.not_found", path=str(root))
    asked = phases_named(phases)
    chosen = analyses_named(analyses)

    several = (root / "batch_manifest.json").is_file()
    runs = _runs_of(root) if several else [root]
    campaign = root if several else campaign_of(root)
    going = _going([root, *runs])
    if going:
        raise StudyError(f"{', '.join(going)} {'is' if len(going) == 1 else 'are'} still "
                         "running. Run it again once it has stopped.",
                         code="environment.workspace.run_going")

    left_out: list[tuple[str, str]] = []
    if "analysis" in asked:
        left_out = [(run.name, "has no trajectory, so it has nothing to analyse.")
                    for run in runs if frames_of(run) is None]
        if len(left_out) == len(runs):
            where = root / "simulation" / "production.dcd"
            raise StudyError(f"{root.name} has no trajectory to analyse: its simulation has "
                             "not written one" + (" in any of its runs" if several else "")
                             + ".", code="environment.path.not_found", path=str(where))
    if not runs:
        raise StudyError(f"{root.name} holds no runs to write a report of.",
                         code="environment.path.not_found", path=str(root / "runs"))
    added = False
    if "analysis" in asked and "report" not in asked and any(
            (run / "report").is_dir() for run in runs):
        asked, added = PHASES, True
    return Again(study=root, phases=asked, analyses=chosen, runs=tuple(runs),
                 campaign=campaign, report_added=added,
                 left_out=tuple(left_out) if several else ())


def recorded_analyses(study: str | Path) -> list[str]:
    """The analyses a study ran last, to offer them ticked: those its last
    analysis made, else those it recorded asking for."""
    import json

    root = Path(study)
    if (root / "batch_manifest.json").is_file():
        runs = _runs_of(root)
        root = runs[0] if runs else root
    try:
        made = json.loads((root / "analysis" / "analysis_manifest.json")
                          .read_text(encoding="utf-8")).get("results")
    except (OSError, ValueError, AttributeError):
        made = None
    if isinstance(made, dict) and made:
        return list(made)
    from fastmdxplora.config import ConfigError
    from fastmdxplora.config.recorded import RECORDED, phase_settings

    try:
        asked = phase_settings(root / RECORDED, "analysis", study=root).get("include")
    except (ConfigError, OSError):
        return []
    return [str(name) for name in asked] if isinstance(asked, list) else []


def offered(study: str | Path) -> dict[str, Any]:
    """What can be run again on ``study``, and why not, for a page to offer."""
    from fastmdxplora.refusals import refusal_of

    root = Path(study).expanduser().resolve()
    out: dict[str, Any] = {"study": str(root),
                           "several": (root / "batch_manifest.json").is_file(),
                           "recorded": recorded_analyses(root)}
    for phase in PHASES:
        try:
            found = plan(root, [phase])
        except Exception as exc:  # noqa: BLE001 - said, not raised
            out[phase] = {"can": False, "why": refusal_of(exc).message,
                          "code": refusal_of(exc).code}
            continue
        out[phase] = {"can": True, "runs": len(found.runs),
                      "report_too": found.report_added, "said": found.said(),
                      "left_out": [{"run": name, "why": why} for name, why in found.left_out]}
    return out
