"""Propose a study, have it refused, repair it, try again.

The smallest useful thing a language model can do with this package, and
the safest. A deterministic validator gates every attempt before anything
reaches a GPU, so a wrong proposal costs a few seconds and a retry rather
than a trajectory.

What the model does here is narrow on purpose. It writes YAML and it
repairs YAML. It does not decide whether a run converged, whether a
difference is meaningful, or whether a structure is worth simulating.
Those stay in code, where they were already.

Three properties are worth stating because they are choices and not
accidents.

**The validator does not hand over the answer.** It names the offending
setting and, where the schema holds a complete set, what that set is. It
does not say what the chemistry requires. Telling a model the legal box
shapes costs nothing and saves a round trip; telling it what protonation
state to use would be inventing an answer, and the software does not have
one. :class:`~fastmdxplora.refusals.Refusal` enforces which is which by
reading the registry rather than the raise site.

**Repair cycles are counted and capped.** Counted because the number is
a measurement: how many attempts a model needs to reach a valid config is
a direct reading of its domain competence, comparable across models, and
free to collect. Capped because cheap validation invites thrashing, and a
model that mutates fields until something passes will eventually produce
a config that validates and is scientifically wrong -- which is the
failure this whole mechanism exists to prevent. Exhausting the cap is a
refusal, not a fall-through to whatever last validated.

**Semantic refusals end the loop.** A structural refusal is answerable
from the schema and worth retrying. A refusal because the pH margin does
not determine a protonation state is not negotiable by trying again, and
a model that retries it is guessing. The loop stops and says so.

No model client is imported here and no key is handled. The caller passes
a function that takes a prompt and returns text; what is behind it is
none of this package's business.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from fastmdxplora.config.describe import describe_schema
from fastmdxplora.config.loader import ConfigError, validate_config
from fastmdxplora.refusals import Kind, Refusal, refusal_of

__all__ = [
    "Attempt",
    "Proposal",
    "Completion",
    "propose_config",
    "prompt_for",
    "repair_prompt_for",
]


class Completion(Protocol):
    """Anything that turns a prompt into text.

    Deliberately the whole interface. A caller wiring this to a hosted
    API, a local model, or a recorded fixture for a test should not have
    to satisfy anything more, and this package should not know which of
    those it is talking to.
    """

    def __call__(self, prompt: str) -> str: ...  # pragma: no cover


@dataclass(frozen=True)
class Attempt:
    """One pass round the loop.

    Kept whole rather than reduced to its outcome, because the sequence of
    attempts is the interesting artefact. It shows what a model got wrong
    and what it did when told, which is the evidence for whether the
    description is doing its job, and it is what a benchmark would score.
    """

    number: int
    raw: str
    config: dict[str, Any] | None
    refusal: Refusal | None

    @property
    def accepted(self) -> bool:
        return self.refusal is None and self.config is not None


@dataclass(frozen=True)
class Proposal:
    """What came of asking.

    ``config`` is present only when validation accepted it. There is no
    partially-valid result and no best-effort fall-back: a config that has
    not passed the validator is not a config this package will run, and
    returning one with a warning attached would put the caller in the
    position of deciding, which is the position the validator exists to
    take away.
    """

    config: dict[str, Any] | None
    attempts: tuple[Attempt, ...]
    refusal: Refusal | None = None
    #: A question back, when the request does not say enough to write a
    #: study from. Neither accepted nor refused: nothing was proposed. The
    #: first shape of this loop had only the other two outcomes, so a
    #: request that named no structure got one invented -- the example
    #: from the missing-`systems` refusal, copied verbatim, twice over
    #: with two different examples. An example in a refusal is read as
    #: the answer, whatever it says.
    question: str | None = None

    @property
    def accepted(self) -> bool:
        return self.config is not None

    @property
    def cycles(self) -> int:
        """Attempts taken. One means it was right first time."""
        return len(self.attempts)

    def as_record(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "cycles": self.cycles,
            "refusal": self.refusal.as_dict() if self.refusal else None,
            "codes": [a.refusal.code for a in self.attempts if a.refusal],
        }


_INSTRUCTIONS = """\
Write a FastMDXplora study config as YAML. Reply with the YAML only: no
prose, no explanation, no code fences.

Include only settings the request calls for. Every setting has a default
that is there for a reason, and a config that sets everything is harder
to read and no more correct.

An unknown key is refused rather than ignored, so do not invent settings.

Never invent a structure. A study needs a `systems:` entry whose `system`
is a PDB identifier or a file path. Take it from the request. If the
request names a molecule by its common name and you know a PDB identifier
for it with confidence, use that one and say so in `id` -- that is
looking up, not inventing. If the request names nothing, or names a
molecule with several deposited structures and does not say which, do not
choose: reply with a single line starting `ASK:` that names the
candidates you know and asks which, and nothing else. Do not borrow a
structure from an example.
"""


def prompt_for(request: str, *, phases: list[str] | None = None,
               verbose: bool = True) -> str:
    """The first prompt: what the language is, and what is wanted.

    The schema description is generated, so it cannot name a setting
    validation would refuse. `verbose` keeps each setting's help text,
    which is where the refusals are explained -- a model told that setup
    refuses rather than embedding a protein sideways proposes fewer
    studies that will be refused.
    """
    return (
        f"{_INSTRUCTIONS}\n"
        f"{describe_schema(phases=phases, verbose=verbose)}\n\n"
        f"## The study wanted\n{request}\n"
    )


def repair_prompt_for(previous: str, refusal: Refusal) -> str:
    """The follow-up: what was wrong, and nothing more.

    The permitted set is included where the registry says it may be, and
    withheld where it says it may not -- read from
    :attr:`Refusal.permitted`, which gates on the code rather than on
    whatever the raise site happened to pass.

    No remedy is offered beyond that. A validator that hands over the fix
    turns every rejection into a well-specified task, which flatters the
    measurement and moves the domain reasoning out of the part being
    measured.
    """
    lines = [
        "That config was refused.", "",
        f"Reason: {refusal.message}", "",
    ]
    option = refusal.details.get("option")
    if option:
        lines.append(f"The setting at fault is `{option}`.")
    permitted = refusal.permitted
    if permitted:
        lines.append("Permitted values: "
                     + ", ".join(repr(v) for v in permitted) + ".")
    suggestion = refusal.details.get("suggestion")
    if suggestion:
        lines.append(f"The nearest permitted name is `{suggestion}`.")
    lines += [
        "", "Here is what you sent:", "", previous, "",
        "Send the corrected YAML only.",
    ]
    return "\n".join(lines)



def _question_in(raw: str) -> str | None:
    """The question a reply carries, if the reply is one.

    A line starting ``ASK:`` and nothing else. Looked for on the first
    non-blank line so a model that adds a courtesy sentence after it still
    reads as asking; anything that parses as YAML instead is a config.
    """
    for line in (raw or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.upper().startswith("ASK:"):
            return stripped[4:].strip() or "The request does not say enough."
        return None
    return None


def _parse(raw: str) -> dict[str, Any] | None:
    """YAML out of a reply, tolerating the fences a model adds anyway."""
    import yaml

    text = raw.strip()
    if text.startswith("```"):
        lines = [l for l in text.splitlines() if not l.strip().startswith("```")]
        text = "\n".join(lines)
    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    return parsed if isinstance(parsed, dict) else None


def propose_config(
    request: str,
    complete: Completion,
    *,
    phases: list[str] | None = None,
    max_cycles: int = 4,
    verbose_schema: bool = True,
) -> Proposal:
    """Ask for a config, and keep asking until it validates or the cap.

    Parameters
    ----------
    request
        What the study should do, in whatever words the caller has.
    complete
        Prompt in, text out. No client is constructed here.
    max_cycles
        Attempts before giving up. Four is a judgement: a model that has
        not produced a valid config in four passes over a generated schema
        description is not converging, and further passes mostly produce
        configs that validate for reasons nobody chose.

    Returns
    -------
    Proposal
        With ``config`` set when validation accepted one, and with the
        last refusal when it did not.

    Notes
    -----
    Stops early on a semantic refusal. A structural one says the config
    does not match the schema, which a model can fix by reading. A
    semantic one says the *system* does not determine what to do -- an
    undetermined protonation, an ambiguous structure -- and no rewording
    of the config changes that. Retrying it would be the model guessing at
    a question the software declined to guess at, which is the behaviour
    this design exists to prevent.
    """
    attempts: list[Attempt] = []
    prompt = prompt_for(request, phases=phases, verbose=verbose_schema)
    refusal: Refusal | None = None

    for number in range(1, max_cycles + 1):
        raw = complete(prompt)
        asked = _question_in(raw)
        if asked:
            # The request is short of something a model cannot supply and
            # should not guess. Stop here; retrying would only ask a model
            # to invent what it was told not to.
            return Proposal(config=None, attempts=tuple(attempts),
                            question=asked)
        config = _parse(raw)

        if config is None:
            refusal = Refusal(
                code="config.file.unparseable",
                message="The reply was not a YAML mapping.",
                details={"attempt": number},
            )
            attempts.append(Attempt(number, raw, None, refusal))
            prompt = repair_prompt_for(raw, refusal)
            continue

        try:
            # `require_systems=True`, because a proposed study is one
            # somebody means to run. The default is False so that a partial
            # config can be checked -- a GUI form mid-edit, a fragment --
            # and the agent inherited that leniency without meaning to.
            #
            # Found in use: asked for a water simulation, the model wrote
            # `output`, `simulation.duration_ns` and no `systems` at all.
            # That passed, reported "Accepted first time", and produced a
            # study with nothing in it to simulate. An empty `systems` list
            # was already refused; an absent one was not.
            validate_config(config, require_systems=True)
        except ConfigError as exc:
            refusal = refusal_of(exc)
            attempts.append(Attempt(number, raw, config, refusal))
            if refusal.kind != Kind.STRUCTURAL:
                # Not answerable by rewriting the config. Stop rather than
                # let the model guess at what the software declined to.
                break
            prompt = repair_prompt_for(raw, refusal)
            continue

        attempts.append(Attempt(number, raw, config, None))
        return Proposal(config=config, attempts=tuple(attempts))

    return Proposal(config=None, attempts=tuple(attempts), refusal=refusal)
