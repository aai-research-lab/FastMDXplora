"""How well a model writes a study, measured rather than assumed.

``propose_config`` has a repair loop and a cycle counter, and nothing has
ever counted anything: it is tested against stubs that make the mistakes I
imagined. What a real model does with ``describe_schema()`` -- how often it
gets there, how many passes it needs, which mistakes recur -- decides
whether the natural language interface is worth having, and none of it is
known.

This is the instrument. It needs a completion function and nothing else,
so it runs against a hosted model, a local one, or a recorded fixture.

Two things are measured, and the second matters more.

**Does it validate.** Cycles to a config the validator accepts. This is
cheap, comparable between models, and a direct reading of whether the
generated schema description is doing its job.

**Does it mean what was asked.** A config can validate and be the wrong
study: 300 K when the request said 310, an unrequested barostat, the right
setting in the wrong phase block. Validation catches ill-formed, not
wrong, and a harness that reported only the first would flatter every
model and measure the thing nobody cares about.

So each request carries assertions about the config it should produce.
They are deliberately loose -- they check what the sentence actually
specified and leave everything else to the model -- because a strict
expected config would measure agreement with my taste rather than
correctness.

Usage::

    from fastmdxplora.agent.evaluate import REQUESTS, measure

    report = measure(my_completion)
    print(report)

With no completion function the requests are still worth reading: they are
a statement of what the interface is expected to handle.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fastmdxplora.agent.propose import Completion, propose_config

__all__ = ["Request", "Outcome", "Report", "REQUESTS", "measure"]


def _at(config: dict[str, Any], path: str) -> Any:
    """A dotted lookup, tolerating a missing branch."""
    node: Any = config
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


@dataclass(frozen=True)
class Request:
    """One thing to ask for, and what the answer has to contain.

    ``must`` maps a dotted config path to the value the sentence
    specified. Only what the sentence specified: a request that says
    "at pH 7.4" checks ``setup.ph`` and nothing else, because everything
    else was left to the model and marking it wrong for choosing would be
    measuring obedience rather than comprehension.
    """

    name: str
    text: str
    must: dict[str, Any] = field(default_factory=dict)
    #: Phases to describe. Fewer is a cheaper call and a smaller space for
    #: a model to go wrong in, and a request about setup has no business
    #: being shown the report options.
    phases: tuple[str, ...] = ("setup", "simulation")

    def failures(self, config: dict[str, Any]) -> list[str]:
        wrong: list[str] = []
        for path, expected in self.must.items():
            found = _at(config, path)
            if isinstance(expected, (int, float)) and isinstance(
                    found, (int, float)) and not isinstance(expected, bool):
                if abs(float(found) - float(expected)) > 1e-9:
                    wrong.append(f"{path} is {found!r}, asked for {expected!r}")
            elif found != expected:
                wrong.append(f"{path} is {found!r}, asked for {expected!r}")
        return wrong


#: Ordinary things somebody would ask for. Not adversarial: the question is
#: whether the interface works, and a suite of trick questions answers a
#: different one. The traps that are here are the ones a real request
#: contains by accident -- a unit named in the sentence, a phase boundary
#: that is obvious to a person and not to a schema.
REQUESTS: tuple[Request, ...] = (
    Request("plain", "Simulate ubiquitin, PDB 1UBQ, for 10 nanoseconds.",
            {"simulation.duration_ns": 10}),
    Request("ph", "Run 1UBQ at pH 6.5 for 5 ns.",
            {"setup.ph": 6.5, "simulation.duration_ns": 5}),
    Request("temperature",
            "Simulate 1UBQ at body temperature, 310 K, for 20 ns.",
            {"simulation.temperature_K": 310,
             "simulation.duration_ns": 20}),
    Request("timestep",
            "Run 1UBQ for 50 ns with a 4 femtosecond timestep.",
            {"simulation.timestep_fs": 4, "simulation.duration_ns": 50}),
    Request("box",
            "Simulate 1UBQ in a dodecahedral box for 10 ns.",
            {"setup.box_shape": "dodecahedron"}),
    Request("salt",
            "Run 1UBQ in 150 mM salt for 10 ns.",
            {"setup.ion_concentration_M": 0.15}),
    # The phase boundary a person does not see. Minimisation is a
    # simulation setting here and reads like a setup one.
    Request("no_minimise",
            "Run 1UBQ for 10 ns without minimising first.",
            {"simulation.minimize": False}),
    Request("membrane",
            "Simulate a membrane protein from 1UBQ in a POPC bilayer for "
            "20 ns. The orientation has already been checked.",
            {"setup.membrane": "POPC",
             "setup.membrane_orientation_checked": True},
            phases=("setup", "simulation")),
)


@dataclass(frozen=True)
class Outcome:
    """What happened for one request."""

    request: str
    accepted: bool
    cycles: int
    codes: tuple[str, ...]
    wrong: tuple[str, ...]

    @property
    def correct(self) -> bool:
        """Validated and meant what was asked. The only outcome that counts."""
        return self.accepted and not self.wrong


@dataclass(frozen=True)
class Report:
    outcomes: tuple[Outcome, ...]

    @property
    def accepted(self) -> int:
        return sum(1 for o in self.outcomes if o.accepted)

    @property
    def correct(self) -> int:
        return sum(1 for o in self.outcomes if o.correct)

    @property
    def mean_cycles(self) -> float:
        got = [o.cycles for o in self.outcomes if o.accepted]
        return sum(got) / len(got) if got else float("nan")

    @property
    def first_time(self) -> int:
        return sum(1 for o in self.outcomes if o.accepted and o.cycles == 1)

    def recurring_codes(self) -> dict[str, int]:
        """Which refusals came up, most often first.

        The most useful output here. A code that appears in most runs is
        not a model being careless; it is the schema description failing
        to say something, and it says where to look.
        """
        tally: dict[str, int] = {}
        for outcome in self.outcomes:
            for code in outcome.codes:
                tally[code] = tally.get(code, 0) + 1
        return dict(sorted(tally.items(), key=lambda kv: -kv[1]))

    def as_record(self) -> dict[str, Any]:
        return {
            "requests": len(self.outcomes),
            "accepted": self.accepted,
            "correct": self.correct,
            "first_time": self.first_time,
            "mean_cycles": self.mean_cycles,
            "codes": self.recurring_codes(),
            "outcomes": [
                {"request": o.request, "accepted": o.accepted,
                 "correct": o.correct, "cycles": o.cycles,
                 "codes": list(o.codes), "wrong": list(o.wrong)}
                for o in self.outcomes
            ],
        }

    def __str__(self) -> str:
        total = len(self.outcomes)
        lines = [
            f"{self.correct}/{total} correct, {self.accepted}/{total} valid, "
            f"{self.first_time} first time, "
            f"{self.mean_cycles:.1f} cycles on average",
        ]
        for outcome in self.outcomes:
            mark = "ok " if outcome.correct else ("valid" if outcome.accepted
                                                  else "  - ")
            note = ("; ".join(outcome.wrong) or ", ".join(outcome.codes)
                    or "")
            lines.append(f"  {mark} {outcome.request:14s} "
                         f"{outcome.cycles} cycle(s)  {note}")
        codes = self.recurring_codes()
        if codes:
            lines.append("  refusals seen: " + ", ".join(
                f"{code} x{n}" for code, n in codes.items()))
        return "\n".join(lines)


def measure(
    complete: Completion,
    *,
    requests: "tuple[Request, ...] | None" = None,
    max_cycles: int = 4,
    verbose_schema: bool = True,
) -> Report:
    """Run every request and report what happened.

    ``verbose_schema`` is worth varying. The help text is most of the
    prompt, and whether it earns those tokens is exactly the sort of thing
    this exists to settle rather than assume.
    """
    outcomes: list[Outcome] = []
    for request in (requests or REQUESTS):
        proposal = propose_config(
            request.text, complete, phases=list(request.phases),
            max_cycles=max_cycles, verbose_schema=verbose_schema)
        codes = tuple(a.refusal.code for a in proposal.attempts if a.refusal)
        wrong = tuple(request.failures(proposal.config)
                      if proposal.config else ())
        outcomes.append(Outcome(
            request=request.name, accepted=proposal.accepted,
            cycles=proposal.cycles, codes=codes, wrong=wrong))
    return Report(tuple(outcomes))
