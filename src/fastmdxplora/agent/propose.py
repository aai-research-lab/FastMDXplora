"""Propose a study, have it refused, repair it, try again.

The smallest useful thing an AI model can do with this package, and
the safest. A deterministic validator gates every attempt before anything
reaches a GPU, so a wrong proposal costs a few seconds and a retry rather
than a trajectory.

What the AI model does here is narrow on purpose. It writes YAML and it
repairs YAML. It does not decide whether a run converged, whether a
difference is meaningful, or whether a structure is worth simulating.
Those stay in code, where they were already.

Three properties are worth stating because they are choices and not
accidents.

**The validator does not hand over the answer.** It names the offending
setting and, where the schema holds a complete set, what that set is. It
does not say what the chemistry requires. Telling an AI model the legal box
shapes costs nothing and saves a round trip; telling it what protonation
state to use would be inventing an answer, and the software does not have
one. :class:`~fastmdxplora.refusals.Refusal` enforces which is which by
reading the registry rather than the raise site.

**Repair cycles are counted and capped.** Counted because the number is
a measurement: how many attempts an AI model needs to reach a valid config is
a direct reading of its domain competence, comparable across AI models, and
free to collect. Capped because cheap validation invites thrashing, and an
AI model that mutates fields until something passes will eventually produce
a config that validates and is scientifically wrong -- which is the
failure this whole mechanism exists to prevent. Exhausting the cap is a
refusal, not a fall-through to whatever last validated.

**Semantic refusals end the loop.** A structural refusal is answerable
from the schema and worth retrying. A refusal because the pH margin does
not determine a protonation state is not negotiable by trying again, and
an AI model that retries it is guessing. The loop stops and says so.

No AI model client is imported here and no key is handled. The caller passes
a function that takes a prompt and returns text; what is behind it is
none of this package's business.
"""

from __future__ import annotations

import re
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
    API, a local AI model, or a recorded fixture for a test should not have
    to satisfy anything more, and this package should not know which of
    those it is talking to.
    """

    def __call__(self, prompt: str) -> str: ...  # pragma: no cover


@dataclass(frozen=True)
class Attempt:
    """One pass round the loop.

    Kept whole rather than reduced to its outcome, because the sequence of
    attempts is the interesting artefact. It shows what an AI model got wrong
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
    #: A plain answer, when the message was a question rather than a
    #: request for a study. "What does density tell me?" wants a
    #: paragraph, not a config and not a refusal, and a loop with no way
    #: to say one produced configs for questions.
    answer: str | None = None
    #: An action the person asked for, by name: "run", "stop", "open
    #: viewer". The person's instruction is the click. The loop returns it
    #: and the caller carries it out through the same door the button
    #: uses, so the mode's gates -- a budget for autonomous, control for
    #: stop -- apply to a word in the thread as they do to a press.
    action: str | None = None
    #: What the AI model looked at with the software's tools before it
    #: answered (:mod:`fastmdxplora.agent.tools`), in order: shown under
    #: the answer, so a size or a time in it can be read against the
    #: software's own finding.
    looks: tuple[Any, ...] = ()
    #: What the action is to be done with, where it takes anything: the
    #: windows and the values for `rerun windows`, the analyses for
    #: `analyze again`. Numbers and names read from the
    #: reply's one line by a strict pattern, checked again by the software
    #: and confirmed by the person before anything runs.
    arguments: dict[str, Any] | None = None
    #: A scene the answer proposes, to show what it is about: read from one
    #: `SHOW:` line by a strict pattern, and written only when the person
    #: presses its button. The Agent's tools only look; this only offers.
    scene: dict[str, Any] | None = None
    #: The candidates a question names, where it names any (a structure's
    #: deposited entries, most often), for the person to choose from.
    choices: tuple[str, ...] = ()
    #: What the Agent says beside a config, in a sentence or two.
    note: str | None = None
    #: What the AI model's calls cost, in the provider's counts, where the
    #: completion reports them (:class:`~fastmdxplora.agent.turns.Usage`).
    usage: dict[str, int] | None = None
    #: How the AI model replied: ``"tools"`` (calls delivered as data) or
    #: ``"text"`` (a reply read by a pattern).
    protocol: str = "text"

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
            "looks": [look.tool for look in self.looks],
            "protocol": self.protocol,
            "usage": self.usage,
        }


#: What the Agent is told, paragraph by paragraph: each as the text protocol
#: says it, then, where replying by tool calls says it otherwise, that way.
#: Written once each, so the two ways of replying cannot drift apart on what
#: the Agent is for; they differ only in how a reply is made.
_PARAGRAPHS: tuple[tuple[str, str | None], ...] = (
    ("""\
Write a FastMDXplora study config as YAML. Reply with the YAML only: no
prose, no explanation, no code fences.""",
     """\
Write a FastMDXplora study config by calling `propose_config` with the
config, as a mapping in the shape the file holds it, and `reasons`: for
each setting you set, its dotted name (`setup.ph`,
`simulation.duration_ns`), why it has that value in a sentence, the values
you set aside where there were some, and `asked: true` where the request
stated the value. The software validates the config. A refusal comes back
as the tool's result, with what would fix it where the software can say;
call `propose_config` again with the config corrected. Anything to say
beside the config goes in `note`, in a sentence or two."""),
    ("""\
Include only settings the request calls for. Every setting has a default
that is there for a reason, and a config that sets everything is harder
to read and no more correct.""",
     None),
    ("""\
An unknown key is refused rather than ignored, so do not invent settings.""",
     None),
    ("""\
This is a conversation, not a form. When there is a conversation so far,
read it: the request may refer to it ("the same but at 320 K", "run
it", "why did that fail?"). When there is a current config, a request
is a change to it unless it plainly describes a different study: return
the whole config with the change applied, and keep everything the
person did not ask to change. Do not start over.""",
     None),
    ("""\
A file attached to a message is there to be read. Use it, and when you
do, name it: "the setup manifest records `ligand_pose: auto`" rather
than "the ligand was posed automatically". If it was cut in the middle,
say so if the answer might lie there.""",
     None),
    ("""\
A system named in words is written as the PDB identifier find_structure
finds for it, never one recalled: a recalled identifier is how a study of
the wrong molecule validates perfectly ("trpcage" was once written as
1UAO, which is chignolin). Name the identifier back with what it is ("1L2Y,
the NMR structure of trp-cage"), so a wrong one is visible. Where more
than one entry fits the request (hen egg-white or T4 lysozyme; an NMR or a
crystal structure), ask the person which, naming the entries found. Where
nothing can be looked up, ask for the identifier or a structure file.""",
     None),
    ("""\
Where the message lists your defaults (the workspace's
fastmdx-defaults.yml), they fill what a study leaves unset, wherever it
runs. Leave a setting the request does not state to them, rather than
writing FastMDXplora's default or a value of your own, and say so beside
the config ("310 K, your default"). A value the request states wins over
them; one the request leaves open that you would change, say why.""",
     None),
    ("""\
Continuing a study leaves one study, not two. The extra production runs
as that study's next segment, inside it; every finished segment is then
joined into one trajectory; and the analyses and the report are rerun
over the whole of it. The person joins nothing by hand. A 0.5 ns study
extended by 0.1 ns ends with a 0.6 ns trajectory and a report
describing all of it. The join still refuses a gap or segments from two
studies, and a study killed mid-run is resumed from its last checkpoint
with the frames it wrote after that checkpoint left out, so the pieces
meet rather than overlap.""",
     None),
    ("""\
Ask for it in the simulation block, with `resume_from` naming the study
directory. `systems` and the other blocks are not needed: the study
being continued supplies them.""",
     None),
    ("""\
    simulation:
      resume_from: ./fastmdxplora_1L2Y_study_20260920180944
      duration_ns: 0.6      # the total production the study should end with""",
     None),
    ("""\
`extra_ns: 0.1` instead says how much more to run. With neither, the
remainder of what that study planned is run, which is what resuming
means, and an absent length there never means the default.""",
     None),
    ("""\
`resume_from` naming a CHECKPOINT FILE rather than a study is the raw
mechanism underneath: the run starts from those coordinates and writes
its own trajectory, joining and analysing nothing. Name the study
unless somebody asked for a single segment.""",
     None),
    ("""\
A plan already met is not a study that cannot be continued. "Production
already reached 0.500 ns, which is the 0.500 ns this study planned"
means resuming has nothing left to do and extending past it is exactly
what to offer -- do not answer it with a fresh run of the same molecule
when the person asked for more of this one.""",
     None),
    ("""\
Continuing a study that stopped: the "continuing this study" block in
the run status answers a request to continue, extend or resume THIS
study -- nothing else. A request for a new study, even of the same
molecule, is a new config written from what the person asked for; do
not answer it with the continuation block, and do not tell somebody who
asked for a fresh run that a study cannot be continued. When they do ask
to continue, answer with the config that block gives: it names the study
in `resume_from`, so the study is extended in place from where it last
stopped, in the same solvated system with no minimisation and no
equilibration. `duration_ns` there is the TOTAL production the study
should end with, as above; set it to the total asked for, or replace it
with `extra_ns` for an amount more. Never add `minimize`, `nvt_steps`
or `npt_steps` to a continuation, and never write `resume_from` from scratch
when that block is there; where it says the study cannot be continued,
say why and offer a fresh run instead.""",
     None),
    ("""\
What would fix a study that stopped: the "what would fix it" block in the
run status gives, for each refusal, the fix, the command or config that
runs it, and what it costs at the study's own speed. Asked why a study
stopped, or what to do about it, answer from that block. Name the fix,
give its command or config exactly as written, and say the price. Where it
says the decision is the person's, say so and name where it is recorded;
never offer a value the block does not give.""",
     None),
    ("""\
"The same settings as that one" refers to a config you can see: the
current config, or the config the active run used, which the run status
carries. Copy the settings from there rather than inferring them from
the run's numbers; "simulated so far" includes equilibration and is not
the production length.""",
     None),
    ("""\
Not every message wants a config. If the person asks a question -- about
molecular dynamics, about a setting, about what the run is doing or why
it stopped -- answer it: reply with a single paragraph starting `SAY:`
and nothing else. Use what the conversation and the run status say; do
not guess at what happened. Quote a study's numbers as its analyses
recorded them, with their errors and units, and name the analysis each
comes from (the software lists each one named under the answer, with its
record and figure); where an analysis says its mean is not
determined, say that too. Asked whether a run passed or can be
trusted, answer from the checks the run status ticks, by name, and from
what the withheld means need where the status gives it.""",
     """\
Not every message wants a config. If the person asks a question (about
molecular dynamics, about a setting, about what the run is doing or why
it stopped), answer it in a paragraph of plain text and call no reply
tool. Use what the conversation and the run status say; do
not guess at what happened. Quote a study's numbers as its analyses
recorded them, with their errors and units, and name the analysis each
comes from (the software lists each one named under the answer, with its
record and figure); where an analysis says its mean is not
determined, say that too. Asked whether a run passed or can be
trusted, answer from the checks the run status ticks, by name, and from
what the withheld means need where the status gives it."""),
    ("""\
An answer about something in the open study that can be seen (a frame,
residues that move or hold the ligand, a colouring by one of its results)
may end with one more line proposing a scene that shows it. The person
writes the scene with a button; you write nothing. The line is
`SHOW: frame 40; colour result:rmsf; highlight resSeq 20 to 25; labels yes;
name loop at 40`, any of these parts and each once: `frame` a frame of the
trajectory as the Viewer plays it, from 0; `colour` one of chain, spectrum,
residue, element, secondary_structure, monochrome, or `result:` and an
analysis the study ran, such as result:rmsf; `representation` one of
cartoon, backbone, sticks, ball and stick, surface, lines, spheres;
`superposed` backbone or pocket; `highlight` an MDTraj selection;
`labels` yes or no; `name` a few words. Propose one only where it shows
what the answer says, and never for a general question.""",
     """\
An answer about something in the open study that can be seen (a frame,
residues that move or hold the ligand, a colouring by one of its results)
may come with a scene that shows it: call `show_scene` beside the answer.
The person writes the scene with a button; you write nothing. Its parts,
any of them and each once: `frame` a frame of the trajectory as the Viewer
plays it, from 0; `colour` one of chain, spectrum, residue, element,
secondary_structure, monochrome, or `result:` and an analysis the study
ran, such as result:rmsf; `representation` one of cartoon, backbone,
sticks, ball and stick, surface, lines, spheres; `superposed` backbone or
pocket; `highlight` an MDTraj selection; `labels` true or false; `name` a
few words. Propose one only where it shows what the answer says, and never
for a general question."""),
    ("""\
You can act, but only when told to, and one action at a time. When the
person plainly instructs you -- "run it", "stop", "open the viewer" --
reply with a single line `DO: <action>` and nothing else, where the
action is one of: run, stop, run the fix, open viewer, open overview, open
report, open builder, show config, download config. `run the fix` runs the
first fix marked [runs here] in the run status (a resume, windows run again)
when the person says to carry it out ("resume it", "rerun those windows",
"do that"); the software shows them its command and price and asks first.
In an umbrella study, told to run windows again at settings they name
("rerun window 3 at 6000", "windows 2 and 5 again for 4 ns"), reply
`DO: rerun windows 3 at 6000` or `DO: rerun windows 2 5 for 4 ns`: the
windows by number, then `at <force constant>` and `for <length> ns` as they
gave them. Use only the values they gave; if they asked for a stiffer spring
or a longer run without a number, ask for it rather than choose one.
Told to analyse the study open again, or to add an analysis to it ("analyse
it again", "add SASA and hydrogen bonds"), reply `DO: analyze again` to run
the analyses it ran last, or `DO: analyze again rmsd rg sasa hbonds` naming
every analysis to run by its name in the software, those it ran included
where they are to stay; told to write its report again, `DO: write the
report again`. Nothing is simulated; the software says what is replaced
and asks first. Setup and simulation are not run again on a study that
has them: for that, write a new study from it (`simulation.setup_from`,
`simulation.resume_from`).
The person's instruction is the click; do not act on a question, on a request for a config, or
because you think they would want it. Never act twice in one reply. If
they ask for a change and to run it in one message, write the config
and say "say run when you have read it" -- one step of seeing what is
about to run is what assisted mode promises. Stopping a run is
irreversible, so `DO: stop` is confirmed with the person before it
happens, and so is a `DO: run` their message did not plainly ask for;
you need not ask, the software does.""",
     """\
You can act, but only when told to, and one action at a time. When the
person plainly instructs you ("run it", "stop", "open the viewer"), call
`act` with the action and call nothing else: run, stop, run the fix, open
viewer, open overview, open report, open builder, show config, download
config. `run the fix` runs the first fix marked [runs here] in the run
status (a resume, windows run again) when the person says to carry it out
("resume it", "rerun those windows", "do that"); the software shows them
its command and price and asks first. In an umbrella study, told to run
windows again at settings they name ("rerun window 3 at 6000", "windows 2
and 5 again for 4 ns"), act `rerun windows` with the `windows` by number
and `force_constant` or `duration_ns` as they gave them. Use only the
values they gave; if they asked for a stiffer spring or a longer run
without a number, ask for it rather than choose one. Told to analyse the
study open again, or to add an analysis to it ("analyse it again", "add
SASA and hydrogen bonds"), act `analyze again`, alone to run the analyses
it ran last, or with `analyses` naming every analysis to run by its name in
the software, those it ran included where they are to stay; told to write
its report again, act `write the report again`. Nothing is simulated; the
software says what is replaced and asks first. Setup and simulation are
not run again on a study that has them: for that, write a new study from
it (`simulation.setup_from`, `simulation.resume_from`).
The person's instruction is the click; do not act on a question, on a
request for a config, or because you think they would want it. Never act
twice in one reply. If they ask for a change and to run it in one message,
write the config and say "say run when you have read it" in its `note`:
one step of seeing what is about to run is what assisted mode promises.
Stopping a run is irreversible, so a stop is confirmed with the person
before it happens, and so is a run their message did not plainly ask for;
you need not ask, the software does."""),
    ("""\
You are the FastMDXplora Agent. Asked who or what you are, say so by
that name, then what you do, in a sentence each. Asked which AI model or
engine runs you, say it is the one chosen in Settings and name it if the
current config's `agent_model` shows it; otherwise say to look in
Settings. Do not volunteer the AI model unasked, do not present it as who
you are, and do not repeat a phrase across turns because it was used
once. Asked what you know beyond this software, answer plainly: the
molecular dynamics this job needs, and general knowledge you would not
lean on here.""",
     None),
    ("""\
Write the way a careful colleague writes, not the way an AI model writes.
Short sentences. One idea per sentence. No em dashes and no en dashes;
use a comma, a full stop, or a new sentence. No colon-then-list where
prose would do. No "I'd be happy to", no "great question", no summary
of what you just said. Say the thing and stop. Plain text, with
emphasis only where it earns its place: **bold** for the one thing to
notice, *italic* for a term, `code` for a setting name or a value, a
bare URL for a link. No headings, no bullet lists in an answer.""",
     None),
    ("""\
Never invent a structure. A study needs a `systems:` entry whose `system`
is a PDB identifier or a file path. Take it from the request. If the
request names a molecule by its common name and you know a PDB identifier
for it with confidence, use that one and say so in `id` -- that is
looking up, not inventing. If the request names nothing, or names a
molecule with several deposited structures and does not say which, do not
choose: reply with a single line starting `ASK:` that names the
candidates you know and asks which, and nothing else. Do not borrow a
structure from an example.""",
     """\
Never invent a structure. A study needs a `systems:` entry whose `system`
is a PDB identifier or a file path. Take it from the request. If the
request names a molecule by its common name and you know a PDB identifier
for it with confidence, use that one and say so in `id`: that is
looking up, not inventing. If the request names nothing, or names a
molecule with several deposited structures and does not say which, do not
choose: call `ask_person` with the question and the candidates you know as
`choices`, and call nothing else. Do not borrow a structure from an
example."""),
)


def _instructions(protocol: str = "text") -> str:
    """The instructions for one way of replying: ``"text"`` (a line read by
    a pattern) or ``"tools"`` (calls the provider delivers as data)."""
    tools = protocol == "tools"
    return "\n\n".join(said_with_tools if tools and said_with_tools else said
                       for said, said_with_tools in _PARAGRAPHS) + "\n"


_INSTRUCTIONS = _instructions("text")


def _stopping_instructions() -> str:
    """How to write a study that runs until it is determined. Its list of measures
    is read from the analyses, so it names none the validator refuses."""
    from fastmdxplora.simulation.stopping import judgeable_analyses

    return f"""\
A study can run until what it is for is determined, rather than for a length
picked in advance. Write `simulation.stop_when` when the person asks for a
quantity to a precision ("to within 0.1 nm", "to 5%"), asks to run until
it converges or is long enough to trust, or asks a question whose answer
is one of the recorded means. Name each quantity by the analysis that
records it; only these record one mean a rule can judge:
{", ".join(judgeable_analyses())}. Give each either `standard_error`, in
the analysis's own unit, or `relative_error`, a fraction of its mean. Use
the precision the person states. Where they state none, choose the one
that would answer their question: the plan shows it on its "Stops when"
line, where they read it and can change it before anything runs, so never
present it as theirs. Always give `max_duration_ns`, the most production
any run may reach; `duration_ns` is then only the first piece, a few
nanoseconds. The rule requires replicas that agree, because one run can
look equilibrated while trapped in one state and its error bar cannot show it, so
sweep `simulation.random_seed` over three values:

    sweep:
      simulation.random_seed: [1, 2, 3]
    simulation:
      duration_ns: 5
      stop_when:
        measures:
          - {{analysis: rmsd, standard_error: 0.01}}
        max_duration_ns: 50

Write `independent_starts: not_required` only when the person asks for a
single run. Do not add a rule to a study that asked for a length. Asked
why a study ran as long as it did, or whether it knew what it was asked,
answer from the stopping record in the run status, round by round, and
say plainly when it stopped at its ceiling with a quantity not determined.

"""


#: The turns of a conversation the AI model is given whole, the latest.
KEPT_WHOLE = 12
#: The turns before those it is given by their first sentence, so a long
#: thread still knows what was settled in it ("the force field settled at the start").
KEPT_IN_BRIEF = 48
_SENTENCE_END = re.compile(r"(?<=[.!?])\s")


def _first_sentence(text: str, most: int = 200) -> str:
    text = " ".join(str(text or "").split())
    if text.startswith("Wrote a config:"):
        return "Wrote a config."
    first = _SENTENCE_END.split(text, maxsplit=1)[0]
    return first if len(first) <= most else first[:most - 3].rstrip() + "..."


def earlier_in_brief(history: list[dict[str, str]] | None) -> str:
    """The turns before the last :data:`KEPT_WHOLE`, each by its first
    sentence, or nothing where there are none. They were dropped: in a long
    thread the Agent forgot the force field settled twenty turns back."""
    earlier = list(history or [])[:-KEPT_WHOLE][-KEPT_IN_BRIEF:]
    lines = []
    for turn in earlier:
        said = _first_sentence(turn.get("text") or "")
        if said:
            who = "Person" if turn.get("role") == "user" else "Agent"
            lines.append(f"- {who}: {said}")
    if not lines:
        return ""
    return ("## Earlier in this conversation\n"
            f"Each turn before the last {KEPT_WHOLE} by its first sentence; ask the "
            "person where one matters and its words are not here.\n" + "\n".join(lines) + "\n")


def prompt_for(request: str, *, phases: list[str] | None = None,
               verbose: bool = True,
               history: list[dict[str, str]] | None = None,
               current_config: str | None = None,
               run_status: str | None = None,
               attachments: list[dict[str, Any]] | None = None,
               tools: Any = None, defaults: Any = None) -> str:
    """The first prompt: what the language is, and what is wanted.

    The schema description is generated, so it cannot name a setting
    validation would refuse. `verbose` keeps each setting's help text,
    which is where the refusals are explained -- an AI model told that setup
    refuses rather than embedding a protein sideways proposes fewer
    studies that will be refused.
    """
    parts = [_INSTRUCTIONS, "\n", _stopping_instructions()]
    if tools is not None:
        parts += [tools.describe(), "\n"]
    parts += [describe_schema(phases=phases, verbose=verbose), "\n\n"]
    brief = earlier_in_brief(history)
    if brief:
        parts += [brief, "\n"]
    if history:
        parts.append("## The conversation so far\n")
        for turn in history[-KEPT_WHOLE:]:
            who = "Person" if turn.get("role") == "user" else "Agent"
            parts.append(f"{who}: {str(turn.get('text') or '').strip()}\n")
        parts.append("\n")
    parts.append(_this_message(request, current_config=current_config,
                               run_status=run_status, attachments=attachments,
                               defaults=defaults))
    return "".join(parts)


def _this_message(request: str, *, current_config: str | None = None,
                  run_status: str | None = None,
                  attachments: list[dict[str, Any]] | None = None,
                  defaults: Any = None) -> str:
    """What changes from one message to the next: the current config, what
    the run is doing, the files attached, and what the person asked. Last
    in either protocol, after everything that stays the same."""
    parts: list[str] = []
    if current_config:
        parts.append(f"## The current config\n```yaml\n{current_config.strip()}\n```\n\n")
    if run_status:
        parts.append(f"## What the run is doing\n{run_status.strip()}\n\n")
    if attachments:
        parts.append("## Files attached to this message\n")
        for a in attachments:
            name = str(a.get("name") or "file")
            note = " (head and tail; the middle was cut)" if a.get("truncated") else ""
            parts.append(f"### {name}{note}\n```\n{str(a.get('text') or '').strip()}\n```\n\n")
    if defaults is not None and defaults.values:
        parts.append(f"## Your defaults ({defaults.path.name})\n{defaults.said()}\n\n")
    parts.append(f"## The study wanted\n{request}\n")
    return "".join(parts)


def repair_prompt_for(previous: str, refusal: Refusal, *, give_remedy: bool = True) -> str:
    """The follow-up: what was wrong, and what would fix it.

    The permitted set is included where the registry says it may be, and
    withheld where it says it may not -- read from
    :attr:`Refusal.permitted`, which gates on the code rather than on
    whatever the raise site happened to pass.

    The remedy is given within the same rule (decided 2026-10-07): what the
    schema holds, the setting's name, an install command, and nothing where
    the answer is a scientific judgement. The builder, the Agent's own
    `check_config` and an AI app's `check_study` already said it; withheld
    here, it cost an attempt to learn what the software knew. Withholding
    it is an arm of the evaluation, not a rule of the product:
    ``give_remedy=False`` is that arm, the repair the registered
    measurements were made with.
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
    fix = _what_would_fix(refusal) if give_remedy else ""
    if fix:
        lines.append(f"What would fix it: {fix}")
    lines += [
        "", "Here is what you sent:", "", previous, "",
        "Send the corrected YAML only.",
    ]
    return "\n".join(lines)


def _what_would_fix(refusal: Refusal) -> str:
    """The remedy, as far as the refusal registry lets it be said: the
    permitted values, the setting's name, an install command, and nothing
    where the answer is a scientific judgement. The same words the builder,
    the Agent's `check_config` and an AI app's `check_study` give."""
    from fastmdxplora.remedies import remedy_for

    try:
        return str(remedy_for(refusal, where="the config").fix or "")
    except Exception:  # noqa: BLE001 - the refusal stands without its fix
        return ""





ACTIONS = ("run", "stop", "run the fix", "open viewer", "open overview", "open report",
           "open builder", "show config", "download config")

# The person's message when it is itself the instruction to run: "run it",
# "start the study", "go ahead". Read from what they typed, never from the
# reply, so an AI model cannot supply it.
_TOLD_TO_RUN = re.compile(
    r"(?:(?:ok|okay|yes|please|now|then|right|so),?\s+)*"
    r"(?:(?:run|start|launch)(?:\s+(?:it|this|that|the\s+(?:study|run|config|simulation)))?"
    r"|go(?:\s+ahead)?)"
    r"(?:\s+(?:now|please))*")


def told_to_run(message: str) -> bool:
    """Whether the person's own message plainly says to run.

    `DO: run` starts work on this machine, and the reply that carries it
    comes from an AI model, which reads the person's files and can be wrong or
    be told what to say by one of them. The prompt asks it to act only when
    told; this is the check that does not depend on the AI model agreeing. A
    run the message did not plainly ask for is confirmed with the person
    first, as a stop always is.
    """
    said = " ".join(str(message or "").lower().split()).strip(" .!")
    return _TOLD_TO_RUN.fullmatch(said) is not None


def _action_in(raw: str) -> str | None:
    """An action, if the reply is one: a first line `DO: <action>`.

    Only the named actions, and only one. Anything else after DO: is not
    an action, and a reply that is not exactly one line is not an action
    either -- an AI model that says "DO: run" and then keeps talking is not
    acting, it is narrating, and the person should see the narration.
    """
    said = _do_line(raw)
    return said if said in ACTIONS else None


def _do_line(raw: str) -> str | None:
    lines = [line for line in (raw or "").splitlines() if line.strip()]
    if len(lines) != 1:
        return None
    line = lines[0].strip()
    if not line.upper().startswith("DO:"):
        return None
    return " ".join(line[3:].split()).lower().rstrip(".")


_NUMBER = r"\d+(?:\.\d+)?(?:e[+-]?\d+)?"
_WINDOWS = re.compile(r"rerun windows?\s+(?P<windows>\d+(?:\s*(?:,\s*and|,|and)?\s*\d+)*)"
                      r"(?P<rest>.*)")
_HELD_AT = rf"at\s+(?P<k>{_NUMBER})(?:\s*kj/mol(?:/(?:nm|rad)(?:\^2|\u00b2|2))?)?"
_FOR = rf"for\s+(?P<ns>{_NUMBER})\s*ns"
_SETTINGS = [re.compile(rf"(?:\s+{_HELD_AT})?(?:\s+{_FOR})?"),
             re.compile(rf"(?:\s+{_FOR})?(?:\s+{_HELD_AT})?")]


def _windows_in(raw: str) -> dict[str, Any] | None:
    """`DO: rerun windows 3 5 at 6000 for 4 ns`, read as the windows and the
    values, or None. The whole line has to be that and nothing else: a
    number is read only where the pattern puts one."""
    said = _do_line(raw)
    found = _WINDOWS.fullmatch(said or "")
    if found is None:
        return None
    for pattern in _SETTINGS:
        rest = pattern.fullmatch(found["rest"])
        if rest is not None:
            break
    else:
        return None
    windows = [int(n) for n in re.findall(r"\d+", found["windows"])]
    return {"windows": sorted(set(windows)),
            "force_constant": float(rest["k"]) if rest["k"] else None,
            "duration_ns": float(rest["ns"]) if rest["ns"] else None}

_ANALYSE_AGAIN = re.compile(r"analy[sz]e (?:it |the study )?again(?:\s+(?:with\s+)?"
                            r"(?P<names>[a-z0-9_]+(?:\s*(?:,\s*and|,|and)?\s*[a-z0-9_]+)*))?")
_REPORT_AGAIN = re.compile(r"write (?:the |its )?report again")


def _again_in(raw: str) -> tuple[str, dict[str, Any]] | None:
    """`DO: analyze again rmsd rg` or `DO: write the report again`, read as
    the action and the analyses named, or None. The whole line has to be
    that; the names are checked against the software's own by the caller."""
    said = _do_line(raw) or ""
    if _REPORT_AGAIN.fullmatch(said):
        return "write the report again", {}
    found = _ANALYSE_AGAIN.fullmatch(said)
    if found is None:
        return None
    names = [name for name in re.split(r"[\s,]+", found["names"] or "")
             if name and name != "and"]
    return "analyze again", {"analyses": list(dict.fromkeys(names)) or None}


def _answer_in(raw: str) -> str | None:
    """A plain answer, if the reply is one: a first line starting SAY:."""
    lines = (raw or "").splitlines()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.upper().startswith("SAY:"):
            rest = stripped[4:].strip()
            tail = "\n".join(lines[i + 1:]).strip()
            return (rest + ("\n" + tail if tail else "")).strip() or "\u2026"
        return None
    return None

#: What a `SHOW:` line may say, each part once: `frame 40; colour
#: result:rmsf; highlight resSeq 20 to 25; labels yes; name loop`.
_SHOWN_REPRESENTATIONS = {"cartoon": "cartoon", "backbone": "backbone", "sticks": "sticks",
                          "ball and stick": "ballAndStick", "ballandstick": "ballAndStick",
                          "surface": "surface", "lines": "lines", "spheres": "spacefill",
                          "spacefill": "spacefill"}
_SHOWN_COLOURS = ("chain", "spectrum", "residue", "element", "secondary_structure",
                  "monochrome")
_RESULT_COLOUR = re.compile(r"^result:[a-z0-9_]{1,40}$")
_SCENE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,59}$")


def _scene_in(answer: str) -> tuple[str, dict[str, Any] | None]:
    """The answer without its `SHOW:` line, and the scene that line proposes.

    The line is the answer's last and is read whole or not at all: a part
    the pattern does not allow drops the proposal, never the answer, and
    nothing is written until the person presses the button it becomes.
    """
    lines = (answer or "").rstrip().splitlines()
    if not lines or not lines[-1].strip().upper().startswith("SHOW:"):
        return answer, None
    kept = "\n".join(lines[:-1]).strip() or "\u2026"
    parts: dict[str, Any] = {}
    for part in lines[-1].strip()[5:].split(";"):
        key, _, value = part.strip().partition(" ")
        key, value = key.lower(), " ".join(value.split())
        if not value or key in parts:
            return kept, None
        parts[key] = value
    return kept, _scene_from(parts)


def _scene_from(parts: dict[str, Any]) -> dict[str, Any] | None:
    """A scene from its parts, each checked as the `SHOW:` line's are, or
    None where any part is not one the Viewer takes. The same rule for the
    line and for the `show_scene` tool's arguments."""
    scene: dict[str, Any] = {}
    for key, given in (parts or {}).items():
        key = str(key).lower()
        if given is None:
            continue
        if isinstance(given, bool):
            value = "yes" if given else "no"
        else:
            value = " ".join(str(given).split())
        if not value:
            return None
        if key == "frame" and value.isdigit():
            scene[key] = int(value)
        elif key == "representation" and value.lower() in _SHOWN_REPRESENTATIONS:
            scene[key] = _SHOWN_REPRESENTATIONS[value.lower()]
        elif key == "colour" and (value.lower() in _SHOWN_COLOURS
                                  or _RESULT_COLOUR.match(value.lower())):
            scene[key] = value.lower()
        elif key == "superposed" and value.lower() in ("backbone", "pocket"):
            scene[key] = value.lower()
        elif key == "highlight" and len(value) <= 200 and all(c not in value for c in "`<>"):
            scene[key] = value
        elif key == "labels" and value.lower() in ("yes", "no"):
            scene[key] = value.lower() == "yes"
        elif key == "name" and _SCENE_NAME.match(value):
            scene[key] = value
        else:
            return None
    return scene or None


def _question_in(raw: str) -> str | None:
    """The question a reply carries, if the reply is one.

    A line starting ``ASK:`` and nothing else. Looked for on the first
    non-blank line so an AI model that adds a courtesy sentence after it still
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
    """YAML out of a reply, tolerating what an AI model adds anyway: fences,
    and a sentence before or after the config.

    A sentence before the YAML was read as a key ("Here is the config:")
    and refused as an unknown setting; one after a fence, or after the YAML,
    made the whole reply unreadable. Each cost an attempt and taught the
    person nothing. Only prose around the config is left out, and only
    where it is plainly prose: a line that could be part of the YAML (a
    key, dotted, quoted or capitalised; an indented line; a list item; a
    comment) is kept, so a misspelled or misplaced setting stays in and the
    validator refuses it by name rather than it being lost.

    One fenced block with nothing YAML-like outside it is the config. Any
    other reply has its fence lines removed, as before, and is read whole
    where that reads as a mapping of settings; else with the prose lines at
    its start and its end trimmed.
    """
    import yaml

    lines = (raw or "").strip().splitlines()
    fences = [i for i, line in enumerate(lines) if line.strip().startswith("```")]
    candidates: list[str] = []
    if len(fences) == 2:
        inside = lines[fences[0] + 1:fences[1]]
        before = lines[:fences[0]]
        if before and before[-1].rstrip().endswith(":") and not before[-1][:1].isspace():
            # "Here:" or "Config:" over the block is its label, not a key:
            # a key with nothing after it but a fence holds nothing.
            before = before[:-1]
        outside = before + lines[fences[1] + 1:]
        if not any(_yaml_like(line) for line in outside):
            candidates.append("\n".join(inside))
    kept = [line for i, line in enumerate(lines) if i not in fences]
    candidates.append("\n".join(kept))
    while kept and not _yaml_like(kept[0]):
        kept = kept[1:]
    while kept and not _yaml_like(kept[-1]):
        kept = kept[:-1]
    candidates.append("\n".join(kept))
    for number, candidate in enumerate(candidates, 1):
        try:
            parsed = yaml.safe_load(candidate)
        except yaml.YAMLError:
            continue
        if isinstance(parsed, dict) and parsed and (
                number == len(candidates) or not _a_sentence_as_key(parsed)):
            return parsed
    return None


#: A line that could belong to YAML: a key at the start (a word, dotted,
#: hyphenated or quoted, then a colon), an indented line, a list item or a
#: comment. "Here is the config:" is not one; neither is a sentence.
_YAML_KEY = re.compile(r"""^["']?[A-Za-z_][\w.\-]*["']?\s*:(?:\s|$)""")


def _yaml_like(line: str) -> bool:
    return bool(line.strip()) and (line[:1] in (" ", "\t", "-", "#")
                                   or _YAML_KEY.match(line) is not None)


def _a_sentence_as_key(parsed: dict[str, Any]) -> bool:
    """Whether a key is a sentence ("Here is the config"): the whole reply
    read prose as YAML, and the trimmed one is to be taken instead."""
    return any(not isinstance(key, str) or " " in key.strip() for key in parsed)


def _repaired(first: str, previous: str, refusal: Refusal, as_registered: bool = False) -> str:
    """The next prompt after a refusal: what was sent and why it was
    refused, then the first prompt whole. The repair alone, as it was,
    carried no request, no schema and no conversation, so an AI model
    could patch the YAML it was shown but could not read again what had
    been asked. It still begins as a repair, so a caller that tells one
    from a first prompt by its opening still can. ``as_registered`` gives
    the repair as the registered measurements were made with: the refusal
    and the reply, nothing more."""
    if as_registered:
        return repair_prompt_for(previous, refusal, give_remedy=False)
    return (f"{repair_prompt_for(previous, refusal)}\n\n"
            f"## What you were asked, and everything you were given with it\n\n{first}")


#: Attempts in all, the first included, before a request is refused. One
#: meaning and one number for every way in: the command line's
#: ``--attempts``, the browser, and the Python call. The command line said
#: three and meant three in all while the others said four, and its help
#: called them corrections, which would have been four. Three is measured:
#: on the evaluation set a valid Config took at most two with the schema's
#: help text and at most three without it.
DEFAULT_ATTEMPTS = 3


def propose_config(
    request: str,
    complete: Completion,
    *,
    phases: list[str] | None = None,
    max_cycles: int = DEFAULT_ATTEMPTS,
    verbose_schema: bool = True,
    history: list[dict[str, str]] | None = None,
    current_config: str | None = None,
    run_status: str | None = None,
    attachments: list[dict[str, Any]] | None = None,
    tools: Any = None,
    as_registered: bool = False,
    defaults: Any = None,
) -> Proposal:
    """Ask for a config, and keep asking until it validates or the cap.

    Parameters
    ----------
    request
        What the study should do, in whatever words the caller has.
    complete
        Prompt in, text out. No client is constructed here.
    max_cycles
        Attempts in all, the first included, before giving up
        (:data:`DEFAULT_ATTEMPTS`). An AI model that has not produced a valid
        config in that many passes over a generated schema description is
        not converging, and further passes mostly produce configs that
        validate for reasons nobody chose.

    tools
        A :class:`~fastmdxplora.agent.tools.Toolbox`, to let the AI model
        look with the software's tools before it answers. A look is not an
        attempt: it runs nothing and is not validated, and at most
        :data:`~fastmdxplora.agent.tools.MOST_LOOKS` are taken per answer.
    as_registered
        Ask as the registered measurements were made: in the text protocol
        whatever the completion takes, and each repair the refusal and the
        reply alone, without what would fix it. For the harnesses in
        :mod:`fastmdxplora.agent.evaluate` and :mod:`fastmdxplora.validation`,
        whose counts are comparable only under the protocol they were
        written for.

    defaults
        Your defaults (:mod:`fastmdxplora.config.defaults_file`), where the
        workspace keeps a fastmdx-defaults.yml: listed to the AI model in
        the message, and filled into an accepted config's unset settings,
        each recorded in its `decisions`, so the config shown is the one
        that runs.

    Returns
    -------
    Proposal
        With ``config`` set when validation accepted one, and with the
        last refusal when it did not.

    Notes
    -----
    Stops early on a semantic refusal. A structural one says the config
    does not match the schema, which an AI model can fix by reading. A
    semantic one says the *system* does not determine what to do -- an
    undetermined protonation, an ambiguous structure -- and no rewording
    of the config changes that. Retrying it would be the AI model guessing at
    a question the software declined to guess at, which is the behaviour
    this design exists to prevent.
    """
    proposal = _asked(request, complete, phases=phases, max_cycles=max_cycles,
                      verbose_schema=verbose_schema, history=history,
                      current_config=current_config, run_status=run_status,
                      attachments=attachments, tools=tools, as_registered=as_registered,
                      defaults=defaults)
    return _with_your_defaults(proposal, defaults)


def _with_your_defaults(proposal: Proposal, defaults: Any) -> Proposal:
    """An accepted config with your defaults filling what it leaves unset.

    Validated again filled: a default that does not fit the study the AI
    model wrote (a barostat's setting beside an NVT run, say) leaves the
    config as accepted, and the note says which file disagreed and why, so
    the run, which fills the same defaults, is not the first to say so."""
    if proposal.config is None or defaults is None:
        return proposal
    import dataclasses

    from fastmdxplora.config.defaults_file import with_defaults

    filled, names = with_defaults(proposal.config, defaults)
    if not names:
        return proposal
    try:
        import copy

        validate_config(copy.deepcopy(filled), require_systems=True)
    except ConfigError as exc:
        said = (f"Your defaults ({defaults.path.name}) do not fit this study, so they "
                f"are not filled in: {exc}")
        return dataclasses.replace(
            proposal, note=f"{proposal.note}\n\n{said}" if proposal.note else said)
    return dataclasses.replace(proposal, config=filled)


def _asked(
    request: str,
    complete: Completion,
    *,
    phases: list[str] | None = None,
    max_cycles: int = DEFAULT_ATTEMPTS,
    verbose_schema: bool = True,
    history: list[dict[str, str]] | None = None,
    current_config: str | None = None,
    run_status: str | None = None,
    attachments: list[dict[str, Any]] | None = None,
    tools: Any = None,
    as_registered: bool = False,
    defaults: Any = None,
) -> Proposal:
    """The loop behind :func:`propose_config`, before your defaults."""
    from fastmdxplora.agent.tools import MOST_LOOKS, use_in

    turn = None if as_registered else getattr(complete, "turn", None)
    if callable(turn):
        # The AI model replies by tool calls, delivered as data
        # (:mod:`fastmdxplora.agent.conversation`); the same rules, the same
        # validator. A server that turns tools away is asked in text.
        from fastmdxplora.agent.conversation import propose_with_tools
        from fastmdxplora.agent.turns import NoToolCalling

        try:
            return propose_with_tools(
                request, turn, phases=phases, max_cycles=max_cycles,
                verbose_schema=verbose_schema, history=history,
                current_config=current_config, run_status=run_status,
                attachments=attachments, tools=tools, defaults=defaults)
        except NoToolCalling:
            # Turned away on the first turn: this conversation is asked in
            # text, and the next starts in text, where the completion can
            # note it.
            noted = getattr(complete, "turned_away", None)
            if callable(noted):
                noted()

    attempts: list[Attempt] = []
    first = prompt_for(request, phases=phases, verbose=verbose_schema,
                       history=history, current_config=current_config,
                       run_status=run_status, attachments=attachments, tools=tools,
                       defaults=defaults)
    prompt = first
    refusal: Refusal | None = None

    def looked() -> tuple[Any, ...]:
        return tuple(tools.looks) if tools is not None else ()

    number = 0
    asked_to_look = 0
    while number < max_cycles:
        raw = complete(prompt + (tools.said_so_far() if tools is not None else ""))
        wanted = use_in(raw) if tools is not None else None
        if wanted is not None and asked_to_look < MOST_LOOKS:
            # Not an answer: the tool is run and the AI model asked again with
            # what it said. Looks are counted apart from attempts, since
            # nothing was proposed.
            asked_to_look += 1
            tools.use(*wanted)
            continue
        number += 1
        if wanted is not None:
            # Asked to look again with the looking used up. Not a config,
            # and not to be read as one: `USE: x` parses as a mapping.
            refusal = Refusal(
                code="config.file.unparseable",
                message="The looks for this reply are used up; answer from what "
                        "the software said.",
                details={"attempt": number})
            attempts.append(Attempt(number, raw, None, refusal))
            continue
        act = _action_in(raw)
        if act:
            return Proposal(config=None, attempts=tuple(attempts), action=act,
                            looks=looked())
        again = _windows_in(raw)
        if again is not None:
            return Proposal(config=None, attempts=tuple(attempts), action="rerun windows",
                            arguments=again, looks=looked())
        asked_again = _again_in(raw)
        if asked_again is not None:
            return Proposal(config=None, attempts=tuple(attempts), action=asked_again[0],
                            arguments=asked_again[1], looks=looked())
        said = _answer_in(raw)
        if said:
            said, scene = _scene_in(said)
            return Proposal(config=None, attempts=tuple(attempts), answer=said,
                            scene=scene, looks=looked())
        asked = _question_in(raw)
        if asked:
            # The request is short of something an AI model cannot supply and
            # should not guess. Stop here; retrying would only ask an AI model
            # to invent what it was told not to.
            return Proposal(config=None, attempts=tuple(attempts),
                            question=asked, looks=looked())
        config = _parse(raw)

        if config is None:
            refusal = Refusal(
                code="config.file.unparseable",
                message="The reply was not a YAML mapping.",
                details={"attempt": number},
            )
            attempts.append(Attempt(number, raw, None, refusal))
            prompt = _repaired(first, raw, refusal, as_registered)
            continue

        try:
            # `require_systems=True`, because a proposed study is one
            # somebody means to run. The default is False so that a partial
            # config can be checked -- a GUI form mid-edit, a fragment --
            # and the agent inherited that leniency without meaning to.
            #
            # Found in use: asked for a water simulation, the AI model wrote
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
                # let the AI model guess at what the software declined to.
                break
            prompt = _repaired(first, raw, refusal, as_registered)
            continue

        attempts.append(Attempt(number, raw, config, None))
        return Proposal(config=config, attempts=tuple(attempts), looks=looked())

    return Proposal(config=None, attempts=tuple(attempts), refusal=refusal, looks=looked())
