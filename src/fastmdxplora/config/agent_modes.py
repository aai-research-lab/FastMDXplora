"""Which phases were written by a model, and which were checked.

``agent`` sits at the top level and in every phase. The top level is the
study's answer; a phase sets its own where it differs. Absent everywhere
means a person wrote it, which is the default and the state of every study
run before this existed.

Two levels rather than one because a single value cannot say what is
actually true of a real study. A simulation written by hand because the
protocol matters, an analysis explored outside the schema, and a setup a
model drafted -- that is one study, and flattening it to a single word
loses the only thing a reader needs: *which part should I be suspicious
of*. A trajectory from a validated simulation is fine even when the
analysis over it was not, and marking it anyway is crying wolf. A mark
that appears on everything stops being read.

The resolution is ordinary: a phase's own value, else the study's, else
none. What is less ordinary, and deliberate, is that
:class:`AgentModes` keeps the departures rather than only the answers. A
study that says ``agent: assisted`` and then lets one phase go
unvalidated has made a claim at the top it does not keep throughout, and
the record should say so plainly rather than resolve to a tidy value that
hides it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "AgentModes",
    "MODES",
    "CHECKED_MODES",
    "resolve_agent_modes",
    "unchecked_phases",
]

#: Every value the field accepts, at either level.
MODES = ("assisted", "autonomous", "unvalidated")

#: The modes whose output went through the validator. `unvalidated` did
#: not, which is the whole of what separates it: the study is still
#: recorded, still reproducible, still has a config -- what it does not
#: have is anything that checked the method.
CHECKED_MODES = ("assisted", "autonomous")


@dataclass(frozen=True)
class AgentModes:
    """How each phase of one study was written.

    ``study`` is the top-level value and ``phases`` maps each phase to the
    value in force there, resolved. ``departures`` names the phases whose
    own value differs from the study's, which is the thing a reader is
    entitled to see rather than have to diff for.
    """

    study: str | None
    phases: dict[str, str | None] = field(default_factory=dict)
    departures: dict[str, str] = field(default_factory=dict)

    def of(self, phase: str) -> str | None:
        """The mode in force for one phase."""
        return self.phases.get(phase, self.study)

    def is_checked(self, phase: str) -> bool:
        """Whether this phase's output went through the validator.

        True for a phase a person wrote, which is the absent case: a human
        writing a config is exactly what the validator was built to check.
        """
        mode = self.of(phase)
        return mode is None or mode in CHECKED_MODES

    @property
    def any_unvalidated(self) -> bool:
        return any(not self.is_checked(phase) for phase in self.phases)

    def as_record(self) -> dict[str, Any]:
        """What the manifest carries.

        Per phase rather than one summary, because "this study was partly
        unvalidated" tells a reader to distrust all of it, and the point
        of the per-phase setting is that they need only distrust some.
        """
        record: dict[str, Any] = {
            "study": self.study,
            "phases": {name: mode for name, mode in self.phases.items()},
            "checked": {name: self.is_checked(name) for name in self.phases},
        }
        if self.departures:
            record["departures"] = dict(self.departures)
        return record

    def __str__(self) -> str:
        if self.study is None and not self.departures:
            return "written by hand"
        parts = [f"study: {self.study or 'by hand'}"]
        for phase, mode in sorted(self.departures.items()):
            parts.append(f"{phase}: {mode}")
        return ", ".join(parts)


def resolve_agent_modes(config: dict[str, Any],
                        phases: "tuple[str, ...] | None" = None) -> AgentModes:
    """Read the study's and each phase's mode out of a config.

    Reads rather than validates: a value outside :data:`MODES` was already
    refused by the loader, which holds the choices, and re-checking here
    would be a second place for the list to live.
    """
    from fastmdxplora.config.schema import PHASE_SCHEMAS

    wanted = phases if phases is not None else tuple(PHASE_SCHEMAS)
    study = config.get("agent")
    resolved: dict[str, str | None] = {}
    departures: dict[str, str] = {}

    for phase in wanted:
        block = config.get(phase)
        own = block.get("agent") if isinstance(block, dict) else None
        if own is not None and own != study:
            departures[phase] = own
        resolved[phase] = own if own is not None else study

    return AgentModes(study=study, phases=resolved, departures=departures)


def unchecked_phases(config: dict[str, Any]) -> tuple[str, ...]:
    """Phases whose output nothing checked, for the marking to read.

    Separate from :func:`resolve_agent_modes` because the artifact marking
    asks only this one question, and a caller that wants a list should not
    have to know the shape of the record to get it.
    """
    modes = resolve_agent_modes(config)
    return tuple(phase for phase in modes.phases
                 if not modes.is_checked(phase))
