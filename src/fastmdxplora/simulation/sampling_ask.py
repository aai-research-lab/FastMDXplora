"""How much longer a study has to run for the means it withheld.

An analysis that withholds its mean for want of sampling records how many
more frames would give it ten independent samples, and in nanoseconds where
the run's clock is known (``findings.mean.shortfall``). A person deciding
whether to extend wants one figure for the study, not one per analysis: the
largest, since a run long enough for the slowest measure is long enough for
the rest. And what it would cost, which this run has already measured: its
own `cost.json` says how long its steps took here.

A planning figure, not a promise. Where a run could not resolve its own
correlation time the figure is a lower bound, and the honest use of it is to
run that much and measure again.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastmdxplora.statistics import MINIMUM_EFFECTIVE_SAMPLES

__all__ = ["SamplingAsk", "sampling_asked_for"]


@dataclass(frozen=True)
class SamplingAsk:
    """The production a study still needs for its withheld means."""

    #: Production to add, rounded up to two significant figures.
    more_ns: float
    #: Whether any analysis behind the figure could not resolve its own
    #: correlation time, which makes the figure a floor.
    lower_bound: bool
    #: The analyses that asked, the one needing most first.
    analyses: tuple[str, ...]
    #: Wall time at this run's own speed, or None where it left no record.
    seconds: float | None = None
    #: Where that speed was measured ("CUDA", "CPU").
    platform: str = ""
    #: The independent samples the figure gives each: the fewest a mean may
    #: rest on, or the count that resolves a correlation time where every
    #: one asked for that; None where they asked for different counts.
    target: float | None = MINIMUM_EFFECTIVE_SAMPLES

    def said(self) -> str:
        """The quantities that asked, as a reader names them."""
        # In the sentence's lower case, as its other names are ("Solvent
        # accessible surface area" stood capitalised beside "polar SASA");
        # a name that is an abbreviation keeps its capitals.
        def in_the_sentence(name: str) -> str:
            # The first word's letters up to a space, hyphen, slash or
            # comma: "End-to-end", "Apo/holo" and "Protein-ligand" lower too.
            import re

            found = re.match(r"[A-Za-z]+", name)
            first = found.group(0) if found else ""
            plain = len(first) > 1 and first[1:].islower()
            return name[:1].lower() + name[1:] if plain else name

        names = [in_the_sentence(_named(name)) for name in self.analyses]
        return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]

    def as_text(self) -> str:
        said = self.said()
        said = said[:1].upper() + said[1:]
        one = len(self.analyses) == 1
        amount = f"{'at least ' if self.lower_bound else ''}{_ns(self.more_ns)} more production"
        # One count where every mean asked for it; where they asked for
        # different ones, "each 25" was untrue of a mean asking for 10.
        wanted = (f"{self.target:g} independent samples" if self.target
                  else "the independent samples it asked for")
        text = (f"{said} withheld {'its mean' if one else 'their means'} for want of "
                f"sampling: {amount} should give {'it' if one else 'each'} {wanted}")
        if self.lower_bound:
            text += (" (a floor: the run is too short to resolve its own correlation "
                     "time, so run that much and estimate again)")
        if self.seconds is not None:
            where = f" on {self.platform}" if self.platform else ""
            text += f"; at this run's own speed{where}, about {_duration(self.seconds)}"
        return text + "."

    def config(self, study: str | Path) -> dict[str, Any]:
        """The config that extends the study by this much, in place."""
        return {"simulation": {"resume_from": str(Path(study).resolve()),
                               "extra_ns": self.more_ns}}

    def as_record(self) -> dict[str, Any]:
        return {"more_ns": self.more_ns, "lower_bound": self.lower_bound,
                "analyses": list(self.analyses), "seconds": self.seconds,
                "platform": self.platform}


def sampling_asked_for(study: str | Path) -> SamplingAsk | None:
    """What the study's analyses asked for, or None where none asked in
    nanoseconds (every mean measured, or no clock to say it in)."""
    root = Path(study)
    analysis = root / "analysis"
    if not analysis.is_dir():
        return None
    asked: list[tuple[float, str, bool]] = []
    for options in sorted(analysis.glob("*/options.json")):
        try:
            record = json.loads(options.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        findings = (record.get("findings") or {}) if isinstance(record, dict) else {}
        if not isinstance(findings, dict):
            continue
        name = str(record.get("analysis") or options.parent.name) if isinstance(
            record, dict) else options.parent.name
        for said, mean in _means_of(name, findings):
            shortfall = mean.get("shortfall")
            more = shortfall.get("more_ns") if isinstance(shortfall, dict) else None
            if not isinstance(more, (int, float)) or isinstance(more, bool) \
                    or not math.isfinite(more) or more <= 0:
                continue
            target = shortfall.get("target_independent")
            target = float(target) if isinstance(target, (int, float)) and not isinstance(
                target, bool) and math.isfinite(target) and target > 0 \
                else float(MINIMUM_EFFECTIVE_SAMPLES)
            asked.append((float(more), said, bool(shortfall.get("lower_bound")), target))
    if not (analysis / "thermodynamics" / "options.json").is_file():
        # Analysed without its thermodynamics: the Overview still takes those
        # means from the run's energy file and withholds them, so their asks
        # are counted too (they were left out, and the figure fell below the
        # potential energy's own).
        for key, mean in _thermodynamic_means_shown(root).items():
            shortfall = mean.get("shortfall") if isinstance(mean, dict) else None
            more = shortfall.get("more_ns") if isinstance(shortfall, dict) else None
            if isinstance(more, (int, float)) and not isinstance(more, bool) \
                    and math.isfinite(more) and more > 0:
                target = shortfall.get("target_independent")
                asked.append((float(more), key, bool(shortfall.get("lower_bound")),
                              float(target) if isinstance(target, (int, float)) and target > 0
                              else float(MINIMUM_EFFECTIVE_SAMPLES)))
    if not asked:
        return None
    asked.sort(key=lambda item: -item[0])
    more_ns = _round_up(asked[0][0])
    seconds, platform = _time_here(root, more_ns)
    return SamplingAsk(
        more_ns=more_ns,
        # A floor where any ask was one: a smaller ask that is a floor may
        # turn out larger than the largest that is not.
        lower_bound=any(floor for _, _, floor, _ in asked),
        analyses=tuple(name for _, name, _, _ in asked),
        seconds=seconds, platform=platform,
        target=asked[0][3] if len({target for *_, target in asked}) == 1 else None)


def _thermodynamic_means_shown(root: Path) -> dict[str, Any]:
    """The thermodynamic means the Overview shows for the study, by key."""
    try:
        from fastmdxplora.gui.overview_view import _thermodynamics

        means = _thermodynamics(root).get("means") or {}
    except Exception:  # noqa: BLE001 - a record, not a verdict
        return {}
    return means if isinstance(means, dict) else {}


def _means_of(name: str, findings: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Every mean an analysis recorded, by the name a reader knows it by:
    the trajectory's mean by its analysis, a further quantity beside it by
    its own (helix fraction, polar SASA), and a group read from another
    record (thermodynamics) by each of its quantities, as the Overview and
    the Analysis page list them."""
    means: list[tuple[str, dict[str, Any]]] = []
    for key, entry in findings.items():
        if not isinstance(entry, dict):
            continue
        if key == "mean":
            means.append((name, entry))
        elif "shortfall" in entry or "mean" in entry:
            means.append((key, entry))
        else:
            means += [(inner, quantity) for inner, quantity in entry.items()
                      if isinstance(quantity, dict)]
    return means


def _round_up(value: float) -> float:
    """Up, to two significant figures: a planning figure rounded down is a
    run that stops short."""
    if value <= 0:
        return 0.0
    scale = 10 ** (math.floor(math.log10(value)) - 1)
    return round(math.ceil(value / scale - 1e-9) * scale, 12)


def _time_here(root: Path, more_ns: float) -> tuple[float | None, str]:
    """Wall time for ``more_ns`` of production at the speed this study ran.

    From the last segment's `cost.json`, which is where a continuation
    resumes from: steps, the seconds they took and the timestep. The
    seconds include minimisation and equilibration, so the figure errs
    long.
    """
    from fastmdxplora.simulation.resume import last_segment

    try:
        where = last_segment(root)
    except Exception:  # noqa: BLE001 - the study itself, then
        where = root
    for folder in dict.fromkeys((Path(where), root)):
        try:
            cost = json.loads((folder / "simulation" / "cost.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        try:
            steps, seconds = float(cost["steps"]), float(cost["seconds"])
            timestep_fs = float(cost["timestep_fs"])
        except (KeyError, TypeError, ValueError):
            continue
        if steps > 0 and seconds > 0 and timestep_fs > 0:
            return (more_ns * 1e6 / timestep_fs) * (seconds / steps), str(cost.get("platform") or "")
    return None, ""


def _ns(value: float) -> str:
    return length_said(value)


def length_said(ns: float) -> str:
    """A simulated length in the unit it reads in, as the pages give it:
    picoseconds under one nanosecond ("11.4 ps", not "0.0114 ns"), to a
    tenth of a picosecond; nanoseconds from there, as given."""
    value = float(ns)
    ps = value * 1000.0
    if abs(round(ps, 1)) >= 1000.0:
        # Four figures, never in powers of ten: "12000 ns", not "1.2e+04 ns".
        return f"{float(f'{value:.4g}'):f}".rstrip("0").rstrip(".") + " ns"
    if 0 < abs(ps) < 0.05:
        # One frame saved every few steps is hundredths of a picosecond.
        return f"{ps:.1g} ps"
    return f"{round(ps, 1):g} ps"


def _duration(seconds: float) -> str:
    minutes = max(1, int(round(seconds / 60.0)))
    if minutes < 60:
        return f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    if hours < 48:
        return f"{hours} h {minutes} min" if minutes else f"{hours} h"
    return f"{hours / 24:.1f} days"


def _named(analysis: str) -> str:
    """An analysis by its heading on the Analysis page ("rg" was said as
    "rg withheld its mean")."""
    try:
        from fastmdxplora.gui.report_dashboard import ANALYSIS_SECTION_BY_FOLDER
    except Exception:  # noqa: BLE001 - a name, never a failure
        return analysis
    return ANALYSIS_SECTION_BY_FOLDER.get(analysis) or " ".join(
        _SAID_AS.get(word, word) for word in analysis.split("_"))


#: Words a quantity's key spells in lower case that are said otherwise.
_SAID_AS = {"sasa": "SASA", "rmsd": "RMSD", "rmsf": "RMSF", "rg": "Rg", "qvalue": "Q-value"}
