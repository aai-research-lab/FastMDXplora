"""Every tool FastMDXplora offers an AI model, each defined once.

Two interfaces offer tools to an AI model: the Agent, in the GUI and
`fastmdx agent`, which looks with them before it answers; and `fastmdx mcp`,
which an AI app calls. Each had its own list, written at different times,
and they drifted: an AI app could list a study's saved views and the
machines a study can be sent to, which the Agent could not; the Agent could
read a file of a study and the docs, which an AI app could not; and each
read a study's record and checked a config its own way, so the two could be
told different things about one study.

Here each tool is defined once: its name, what it tells or does, its
arguments, whether it only looks, which interfaces offer it, and the
function that does it. The Agent's toolbox (:mod:`fastmdxplora.agent.tools`)
and `fastmdx mcp`'s tools (:mod:`fastmdxplora.mcp.tools`) are built from
this list. A tool one interface does not offer says why, and a test holds
both interfaces to the list, so a tool added for one is not missed by the
other.

Each tool is told to an AI model by ``what``: the Agent reads it after the
tool's name, and an AI app reads it with its first letter in capitals and
``to_ai_app`` after it, where that says something only an AI app needs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = ["AGENT", "AI_APP", "ACTION", "ByInterface", "OFFERED", "Offered", "REPLIES",
           "arguments_said", "offered_by", "offered_named", "schema_for"]

#: The two interfaces, as the list names them.
AGENT = "agent"
AI_APP = "ai_app"

#: How an AI app is told a tool that only reads this computer, and one that
#: also asks the PDB or another machine.
_READS = {"readOnlyHint": True, "openWorldHint": False}
_LOOKS = {"readOnlyHint": True, "openWorldHint": True}

#: Why the Agent does not offer a tool that changes something: it acts
#: through its `act` reply, which the person confirms on the page, as a run
#: is. Offering these to the Agent is 1.8's actions, with remote phase C.
ACTION = ("it changes something: the Agent acts through its `act` reply, which the "
          "person confirms on the page, and this action is not yet one of those")


@dataclass(frozen=True)
class ByInterface:
    """An argument told differently to each: a config is an object to the
    Agent, which writes it, and a file or YAML text to an AI app."""

    agent: dict[str, Any]
    ai_app: dict[str, Any]

    def to(self, interface: str) -> dict[str, Any]:
        return self.agent if interface == AGENT else self.ai_app


@dataclass(frozen=True)
class Offered:
    """One tool, as both interfaces offer it.

    ``does`` is the function that does it, as ``module:name``. A tool the
    Agent offers is one of its looks, ``(toolbox, arguments) -> str``, and
    an AI app's server runs the same function in its workspace. A tool
    only an AI app offers is the server's own, ``(context, arguments) ->
    str``. ``apart`` says why one interface does not offer it.
    """

    name: str
    title: str
    what: str
    arguments: dict[str, Any]
    does: str
    required: tuple[str, ...] = ()
    offered: tuple[str, ...] = (AGENT, AI_APP)
    apart: str = ""
    #: Said to an AI app only, after ``what``.
    to_ai_app: str = ""
    #: Said to the Agent only, after ``what``.
    to_agent: str = ""
    #: How an AI app is told what calling it does (MCP's annotations).
    annotations: dict[str, bool] = field(default_factory=lambda: dict(_READS))
    #: Starts or stops work on this machine or another: left out of a
    #: server that does not run studies.
    acts: bool = False
    #: The name an AI app calls it by, where that is not ``name``.
    ai_app_name: str = ""
    #: Where an AI app's server says more than the look (what runs the
    #: config checked): the server's own function, which runs ``does``.
    ai_app_does: str = ""
    #: The look as the person reads it on the Agent's page: done, being
    #: done, and the argument it names.
    look_words: tuple[str, str, str] = ()

    @property
    def only_looks(self) -> bool:
        return bool(self.annotations.get("readOnlyHint")) and not self.acts

    def name_for(self, interface: str) -> str:
        return (self.ai_app_name or self.name) if interface == AI_APP else self.name

    def told_to(self, interface: str) -> str:
        """What the tool is said to do, as ``interface`` is told it."""
        if interface == AGENT:
            return f"{self.what} {self.to_agent}".strip()
        said = self.what[:1].upper() + self.what[1:]
        return f"{said} {self.to_ai_app}".strip()


def schema_for(tool: Offered, interface: str) -> dict[str, Any]:
    """The tool's arguments as a JSON schema, as ``interface`` is told them."""
    properties = {name: (spec.to(interface) if isinstance(spec, ByInterface) else spec)
                  for name, spec in tool.arguments.items()}
    schema: dict[str, Any] = {"type": "object", "properties": properties,
                              "additionalProperties": False}
    if tool.required:
        schema["required"] = list(tool.required)
    return schema


def arguments_said(tool: Offered) -> str:
    """The arguments in words, for the Agent's text protocol: those it
    needs, then those it may give."""
    if not tool.arguments:
        return "nothing."

    def one(name: str) -> str:
        spec = tool.arguments[name]
        spec = spec.to(AGENT) if isinstance(spec, ByInterface) else spec
        said = str(spec.get("description") or "").strip().rstrip(".")
        word = said.split(" ", 1)[0]
        if not any(letter.isupper() for letter in word[1:]):
            # "A molecule's name" said as part of a sentence; "AlphaFold"
            # and "MDTraj" as they are.
            said = said[:1].lower() + said[1:]
        return f"`{name}` ({said})" if said else f"`{name}`"

    needed = [one(name) for name in tool.arguments if name in tool.required]
    optional = [one(name) for name in tool.arguments if name not in tool.required]
    parts = []
    if needed:
        parts.append(", ".join(needed))
    if optional:
        parts.append(("and optionally " if needed else "optionally ") + ", ".join(optional))
    return ", ".join(parts) + "."


# ---------------------------------------------------------------------------
# The arguments several tools take
# ---------------------------------------------------------------------------
STRUCTURE = ByInterface(
    agent={"type": "string", "description": (
        "A PDB identifier such as 1UBQ, or the path to a PDB or mmCIF file.")},
    ai_app={"type": "string", "description": (
        "A PDB identifier such as 1UBQ, or a PDB or mmCIF file in the workspace.")})
CONFIG = ByInterface(
    agent={"type": "object", "description": "A study config, as the file holds it."},
    ai_app={"type": "string", "description": (
        "A study config: the path of a YAML file in the workspace, or the YAML itself.")})
STUDY = ByInterface(
    agent={"type": "string", "description": (
        "A study's folder, as list_studies names it or as given to --output.")},
    ai_app={"type": "string", "description": "A study's folder in the workspace."})
JOB = {"type": "string", "description": (
    "A job sent from here, by its name: its results folder's name, as list_machines "
    "lists it.")}
_REPRESENTATIONS = ["cartoon", "backbone", "sticks", "ballAndStick", "lines", "surface",
                    "spacefill"]

_A = "fastmdxplora.agent.tools"
_M = "fastmdxplora.mcp.tools"
_R = "fastmdxplora.mcp.remote_tools"

#: Every tool, in the order an AI app's server lists them.
OFFERED: tuple[Offered, ...] = (
    Offered(
        "find_structure", "Find a structure by its name",
        "the PDB entries a name answers to, grouped by protein, the most studied "
        "first, each with its method, resolution, chains and ligands; and AlphaFold "
        "DB's predicted models where the PDB holds no structure or where asked. "
        "Look here before you write a PDB identifier for a system named in words.",
        {"query": {"type": "string", "description": (
            "A molecule's name, such as lysozyme or trp-cage, or a PDB identifier.")},
         "organism": {"type": "string", "description": (
             "A species as the PDB names it, such as Homo sapiens.")},
         "most": {"type": "integer", "minimum": 1, "maximum": 10, "description": (
             "How many entries to offer; 5 if not given.")},
         "predicted": {"type": "boolean", "description": (
             "AlphaFold DB's models as well as the PDB's entries.")}},
        f"{_A}:_find_structure", required=("query",),
        to_ai_app="Where more than one could be meant, ask the person which. Offline, it "
                  "says it cannot reach the PDB and offers nothing.",
        annotations=dict(_LOOKS),
        look_words=("Looked up {} in the PDB", "Looking up {} in the PDB", "query")),
    Offered(
        "inspect_structure", "Inspect a structure",
        "the chains, protein residues, ligands, ions and water a structure holds, "
        "the residues whose protonation state a study may set, any side chain by "
        "a structural metal, and what is worth knowing about it.",
        {"system": STRUCTURE}, f"{_A}:_inspect_structure", required=("system",),
        to_ai_app="Look here before simulating it.", annotations=dict(_LOOKS),
        look_words=("Inspected {}", "Inspecting {}", "system")),
    Offered(
        "check_selection", "Check an atom selection",
        "how many atoms and which residues an MDTraj selection matches in a "
        "structure. Residue numbers from a paper or the PDB are `resSeq`; `resid` "
        "counts from zero.",
        {"system": STRUCTURE,
         "expression": {"type": "string", "description": "An MDTraj selection."}},
        f"{_A}:_check_selection", required=("system", "expression"),
        annotations=dict(_LOOKS),
        look_words=("Checked a selection", "Checking a selection", "")),
    Offered(
        "preview_setup", "Preview what setup builds",
        "what setup will build from a config (particles, box, solute, water, ions, a "
        "padding grown for the cutoff) and how long the whole study takes on this "
        "machine, where it has been timed.",
        {"config": CONFIG}, f"{_A}:_preview_setup", required=("config",),
        to_ai_app="An estimate; setup's own numbers replace it once it has run.",
        annotations=dict(_LOOKS),
        look_words=("Previewed what setup builds", "Previewing what setup builds", "")),
    Offered(
        "check_config", "Check a study before it runs",
        "whether the validator accepts a config, and if not why and what would fix "
        "it; if so, the plan the person will read, defaults marked, the person's "
        "defaults (fastmdx-defaults.yml) filling what it leaves unset, the study it "
        "carries on, if any, and whether this machine can run it.",
        {"config": CONFIG}, f"{_A}:_check_config", required=("config",),
        to_ai_app="It also gives the plan_id that start_study needs.",
        ai_app_name="check_study",
        ai_app_does=f"{_M}:_check_study",
        look_words=("Checked the config", "Checking the config", "")),
    Offered(
        "save_study", "Save a study config",
        "Write a config into the workspace as a new file, once the validator accepts it. "
        "A file is never written over: a changed study is saved under a new name. A "
        "config you wrote is recorded as written in an AI app (agent: assisted).",
        {"name": {"type": "string", "description": "The file's name, such as ubq_300K."},
         "config": {"type": "string", "description": "The config, as YAML."},
         "by_hand": {"type": "boolean", "description": (
             "True only when the person wrote this config themselves and asked only for "
             "it to be saved: it is then recorded as theirs. Default false.")}},
        f"{_M}:_save_study", required=("name", "config"), offered=(AI_APP,),
        apart="the Agent proposes a config with its `propose_config` reply, and the "
              "person saves it on the page",
        annotations={"readOnlyHint": False, "destructiveHint": False,
                     "idempotentHint": False, "openWorldHint": False}),
    Offered(
        "list_machines", "List the machines a study can be sent to",
        "the machines inspected from this computer, as last recorded, each with its "
        "GPUs and whether it is ready for this computer's code; and the jobs sent from "
        "here whose results come back into the workspace. Nothing is asked of the "
        "machines. A machine is added, or made ready, by the person at a terminal.",
        {}, f"{_A}:_list_machines",
        look_words=("Listed the machines a study can be sent to",
                    "Listing the machines a study can be sent to", "")),
    Offered(
        "start_study", "Start a study",
        "Run a checked config on this machine, in the workspace, once the person has "
        "agreed to its plan. Needs the plan_id check_study gave for the file as it is "
        "now. Where the AI app can ask, the person is asked here too. Results go to the "
        "config's `output`, or a folder named after the file beside it, never one in "
        "use. The run goes on after the AI app closes; one study runs at a time. With "
        "`machine`, it is sent to run on that machine instead, where its GPUs have "
        "room for it, only once the person agrees here (where they cannot be asked, "
        "nothing is sent), with only the files in the config's folder; fetch_study "
        "brings the results back.",
        {"config": {"type": "string", "description": "A config file in the workspace."},
         "plan_id": {"type": "string", "description": "From check_study, for this file."},
         "machine": {"type": "string", "description": (
             "A ready machine from list_machines, to run it there instead of on this "
             "one.")}},
        f"{_M}:_start_study", required=("config", "plan_id"), offered=(AI_APP,),
        apart=ACTION,
        annotations={"readOnlyHint": False, "destructiveHint": False,
                     "idempotentHint": False, "openWorldHint": True}, acts=True),
    Offered(
        "run_phases_again", "Run a study's analysis or report again",
        "Run a study's analysis, its report or both again in its folder, from the "
        "settings it recorded, simulating nothing, once the person agrees: to add an "
        "analysis, or to analyse its frames with this release. An analysis run again "
        "writes the report again too where the study has one. What is replaced is kept "
        "in the study's previous/ folder. A study of several runs is run again run by "
        "run, and the comparison of its runs built again. Setup and simulation are not "
        "run again in place: a new study with simulation.setup_from or "
        "simulation.resume_from does that.",
        {"study": STUDY,
         "phases": {"type": "array", "items": {"type": "string",
                                               "enum": ["analysis", "report"]},
                    "description": "analysis, report, or both."},
         "analyses": {"type": "array", "items": {"type": "string"}, "description": (
             "The analyses to run, by name, such as rmsd, rg or pl_interactions "
             "(default: those the study ran last).")}},
        f"{_M}:_run_phases_again", required=("study", "phases"), offered=(AI_APP,),
        apart=ACTION,
        annotations={"readOnlyHint": False, "destructiveHint": True,
                     "idempotentHint": False, "openWorldHint": False}, acts=True),
    Offered(
        "stop_study", "Stop a study",
        "Stop a study running on this machine. A run in production stops at its next "
        "frame with a checkpoint there, so it can be carried on.",
        {"study": STUDY}, f"{_M}:_stop_study", required=("study",), offered=(AI_APP,),
        apart=ACTION,
        annotations={"readOnlyHint": False, "destructiveHint": True,
                     "idempotentHint": True, "openWorldHint": False}, acts=True),
    Offered(
        "remote_status", "How a study on another machine is doing",
        "how a job sent from here is doing, asked of its machine (waiting, running "
        "with its progress, done, failed with why), and the last lines of its log; "
        "every job still waiting or running, if none is named. An answer under 30 s "
        "old is given again rather than asked anew.",
        {"job": JOB}, f"{_A}:_remote_status", annotations=dict(_LOOKS),
        look_words=("Asked how a sent study is doing", "Asking how a sent study is doing", "")),
    Offered(
        "fetch_study", "Fetch a study from another machine",
        "Bring a finished job's results into its results folder in the workspace, once "
        "the person agrees to what it brings: each size is said first. Trajectories "
        "and checkpoints stay on the machine unless asked for. Where the person cannot "
        "be asked, only a fetch under 100 MB goes ahead.",
        {"job": JOB,
         "with_trajectory": {"type": "boolean", "description": (
             "Bring trajectories and checkpoints too (default false).")}},
        f"{_R}:_fetch_study", required=("job",), offered=(AI_APP,), apart=ACTION,
        annotations={"readOnlyHint": False, "destructiveHint": False,
                     "idempotentHint": True, "openWorldHint": True}, acts=True),
    Offered(
        "cancel_study", "Stop a study on another machine",
        "Stop a job sent from here. Where the AI app can ask, the person is asked "
        "first; where it cannot, its own approval of the call is the gate. Its folder "
        "on the machine stays.",
        {"job": JOB}, f"{_R}:_cancel_study", required=("job",), offered=(AI_APP,),
        apart=ACTION,
        annotations={"readOnlyHint": False, "destructiveHint": True,
                     "idempotentHint": True, "openWorldHint": True}, acts=True),
    Offered(
        "list_studies", "List the studies here",
        "the studies in the workspace, newest first: each one's system, kind, state, "
        "length, force field, tags, the person's note and what its analyses recorded "
        "(means with their errors); then the YAML files at its top.",
        {"tag": {"type": "string", "description": (
            "Only the studies with this tag, whatever its case.")}},
        f"{_A}:_list_studies",
        look_words=("Listed the studies here", "Listing the studies here", "")),
    Offered(
        "tag_study", "Tag a study",
        "Add tags to a study, as the person asks: short words of their own such as wild "
        "type or JCIM Fig. 4, kept in the study's folder beside its records and shown on "
        "its card in the GUI. Tags are only added; the person removes one, or writes the "
        "study's note, in the GUI.",
        {"study": STUDY,
         "tags": {"type": "array", "items": {"type": "string"}, "description": (
             "The tags to add, each up to 40 characters on one line.")}},
        f"{_M}:_tag_study", required=("study", "tags"), offered=(AI_APP,), apart=ACTION,
        annotations={"readOnlyHint": False, "destructiveHint": False,
                     "idempotentHint": True, "openWorldHint": False}, acts=True),
    Offered(
        "read_study", "Read a study",
        "a study's record: where it stands (running, with its step and time left, or "
        "finished), its config, what its analyses found (means, errors, units), the "
        "checks it was held to, how long it ran and why, and what would fix it if it "
        "stopped. The analysis named is given first.",
        {"study": STUDY,
         "analysis": {"type": "string", "description": (
             "The analysis asked about, such as rmsd or rg: given first.")}},
        f"{_A}:_read_study", required=("study",),
        to_agent="The study open, if one is, is in what you were told already.",
        look_words=("Read a study's record", "Reading a study's record", "")),
    Offered(
        "read_study_file", "Read a file of a study",
        "one text file of a study, its head and tail where it is long: a record, a "
        "log, a manifest. Not a trajectory or an image.",
        {"study": STUDY,
         "file": {"type": "string", "description": (
             "The file's path inside the study, such as analysis/rmsd/options.json.")}},
        f"{_A}:_read_study_file", required=("study", "file"),
        to_agent="It is shown as the Files page shows it.",
        look_words=("Read {} in a study", "Reading {} in a study", "file")),
    Offered(
        "methods_of_study", "The methods of a study",
        "a study's methods paragraphs as its report gives them, written from what it "
        "recorded: preparation, protocol, how each mean and its error were determined, "
        "any rule it ran until, an AI model's part, the software. Look here before you "
        "write or answer about a methods section.",
        {"study": STUDY}, f"{_A}:_methods_of_study", required=("study",),
        to_ai_app="Quote them as they are; a gap they name was not recorded.",
        look_words=("Read a study's methods", "Reading a study's methods", "")),
    Offered(
        "compare_studies", "Compare two studies",
        "how two studies differ: each setting one asks for that the other does not, "
        "and what each recorded side by side, a difference called resolved only where "
        "it is more than the stated multiple of its combined standard error. Quote it "
        "rather than comparing means yourself.",
        {"first": STUDY, "second": STUDY}, f"{_A}:_compare_studies",
        required=("first", "second"),
        look_words=("Compared two studies", "Comparing two studies", "")),
    Offered(
        "views_of_study", "The views and scenes of a study",
        "the views the person saved with a study in the GUI (each a frame, a "
        "representation and colouring, a superposition), the selections they named, "
        "and the scenes written with it.",
        {"study": STUDY}, f"{_A}:_views_of_study", required=("study",),
        to_ai_app="A view's name starts write_scene from it.",
        look_words=("Read a study's saved views", "Reading a study's saved views", "")),
    Offered(
        "write_scene", "Write a scene of a study",
        "Write a view of a study as a scene file (MolViewSpec, .mvsx) in its scenes "
        "folder, for the person to open: the atoms at a frame, the study's secondary "
        "structure, a representation and colouring, the selections they named in the GUI, "
        "and atoms to highlight. Use it to show what an answer is about, such as the "
        "residues that move most coloured by RMSF, or the pocket at a frame. A scene of "
        "the same name is replaced.",
        {"study": STUDY,
         "name": {"type": "string", "description": (
             "The scene's name: letters, digits, spaces, dots, dashes, underscores.")},
         "view": {"type": "string", "description": (
             "A view the person saved with the study in the GUI, to start from.")},
         "frame": {"type": "integer", "description": (
             "A frame of the trajectory as the GUI plays it, from 0.")},
         "representation": {"type": "string", "enum": _REPRESENTATIONS,
                            "description": "How the protein is represented."},
         "colour": {"type": "string", "description": (
             "chain, spectrum, residue, element, secondary_structure, monochrome, or one "
             "of the study's per-residue results as result:<analysis>, such as "
             "result:rmsf.")},
         "superposed": {"type": "string", "enum": ["none", "backbone", "pocket"],
                        "description": "Frames fitted to a reference, on this."},
         "superposed_to": {"type": "string", "enum": ["first", "start", "deposited"],
                           "description": (
                               "What the frames are fitted to: the first frame (default), "
                               "the structure the run started from, or the deposited "
                               "structure the study was given.")},
         "smoothed_over": {"type": "integer", "enum": [1, 3, 5, 9, 15], "description": (
             "Each atom's fitted position averaged over this many frames centred on the "
             "one shown (default 1, not smoothed); only with `superposed`.")},
         "highlight": {"type": "string", "description": (
             "An MDTraj selection to show as orange sticks, such as resSeq 189 to 195.")},
         "labels": {"type": "boolean", "description": (
             "Label each highlighted residue (default false).")}},
        f"{_M}:_write_scene", required=("study", "name"), offered=(AI_APP,),
        apart="the Agent shows a scene in the Viewer with its `show_scene` reply, which "
              "the person keeps or not",
        annotations={"readOnlyHint": False, "destructiveHint": False,
                     "idempotentHint": True, "openWorldHint": False}),
    Offered(
        "make_movie", "Make a movie of a study",
        "Make a movie of a study's frames as its Viewer in the GUI makes one, into its "
        "movies folder: each frame rendered as a view the person saved shows it (or as "
        "the Viewer opens), changed as asked, with its simulated time, and encoded by "
        "ffmpeg on this computer. It takes a minute or more: a browser with no window "
        "renders every frame. A study without frames gives its structure turned once. "
        "A movie of the same name is replaced.",
        {"study": STUDY,
         "name": {"type": "string", "description": (
             "The movie's name: letters, digits, spaces, dots, dashes, underscores "
             "(default movie).")},
         "view": {"type": "string", "description": (
             "A view the person saved with the study in the GUI, to show.")},
         "representation": {"type": "string", "enum": _REPRESENTATIONS,
                            "description": "How the protein is represented."},
         "colour": {"type": "string", "description": (
             "chain, spectrum, residue, element, secondary_structure, monochrome, or one "
             "of the study's per-residue results as result:<analysis>.")},
         "superposed": {"type": "string", "enum": ["none", "backbone", "pocket"],
                        "description": "Frames fitted to the first frame, on this."},
         "from": {"type": "integer", "description": "The first frame, from 0 (default 0)."},
         "to": {"type": "integer", "description": (
             "The last frame (default the last); before `from`, the movie plays "
             "backwards.")},
         "every": {"type": "integer", "description": "Every Nth frame (default 1)."},
         "between": {"type": "integer", "enum": [0, 1, 3, 7], "description": (
             "Frames put in between each two, each atom moved in a straight line: "
             "smoother, not more simulation (default 0).")},
         "fps": {"type": "integer", "enum": [10, 15, 24, 25, 30, 60],
                 "description": "Frames a second (default 24)."},
         "size": {"type": "string", "enum": ["1280x720", "1920x1080", "3840x2160"],
                  "description": "Width x height in pixels (default 1920x1080)."},
         "turn": {"type": "boolean", "description": (
             "Turn the camera once about the screen's vertical over the movie.")},
         "time": {"type": "boolean", "description": (
             "Stamp each frame's simulated time (default true).")}},
        f"{_M}:_make_movie", required=("study",), offered=(AI_APP,), apart=ACTION,
        annotations={"readOnlyHint": False, "destructiveHint": False,
                     "idempotentHint": True, "openWorldHint": False}, acts=True),
    Offered(
        "read_docs", "Read the docs",
        "what the docs of this installed version of FastMDXplora say: the passages "
        "that answer a question, each with its page and section, or a page's "
        "sections, or one section whole; asked nothing, the list of pages. Look here "
        "before you answer how the software, its pages, its buttons or its commands "
        "work.",
        {"query": {"type": "string", "description": (
            "What to find in the docs, in words, such as what the Useful and Wrong "
            "buttons do.")},
         "page": {"type": "string", "description": (
             "A page's name as the list of pages gives it, such as agent or gui: with "
             "`query`, the passages from that page alone; without, its sections.")},
         "section": {"type": "string", "description": (
             "A heading on that page, to read that section whole; where a heading is on "
             "the page twice, the headings above it too, as `A > B`.")},
         "part": {"type": "integer", "minimum": 1, "description": (
             "Which part of a long section; 1 if not given.")}},
        f"{_A}:_read_docs",
        look_words=("Read the docs on {}", "Reading the docs on {}", "query")),
    Offered(
        "current_view", "What the page shows",
        "what the page shows (the study, frame, view and residues chosen; the "
        "section of the Report or the Analysis page; the Config Builder's step, the "
        "setting last worked on and the settings changed), as where to look and not "
        "as what is so, then the facts from the study's records, its topology, the "
        "structure or the config schema, read by the code that reads them.",
        {}, f"{_A}:current_view_tool", offered=(AGENT,),
        apart="it reads the page the person is on, in the GUI; an AI app has no page",
        look_words=("Checked what the page shows", "Checking what the page shows", "")),
    Offered(
        "ask_agent", "Ask the FastMDXplora Agent (optional; may use your API key)",
        "Optional: only when the person asks for FastMDXplora's own Agent. It writes "
        "with this AI app's model where the AI app lends it (the AI app may ask the "
        "person first); otherwise with a second AI model, the one the person chose with "
        "`fastmdx agent model`, on their own API key, each call paid for on top of this "
        "conversation. Without it, write the config yourself and give it to "
        "check_study; the validator judges it either way. The Agent writes or changes a "
        "study from a description, or answers about one; a study it writes is accepted "
        "by the validator before it is returned, saved in the workspace with its plan "
        "and plan_id, and recorded as its AI model's; the answer says which AI model "
        "wrote and whose. Nothing is run.",
        {"request": {"type": "string", "description": (
            "What the study should do or what to ask, in the person's words.")},
         "config": {"type": "string", "description": (
             "The study config being changed, if any: a file in the workspace or the "
             "YAML. The Agent returns the whole config with the change.")},
         "study": {"type": "string", "description": (
             "A study folder the request is about, if any: its record (where it stands, "
             "what it found, why it stopped) is given to the Agent.")},
         "phases": {"type": "array", "items": {"type": "string", "enum": [
             "setup", "simulation", "analysis", "report"]}, "description": (
             "The phases whose settings the Agent is told about. Default: setup and "
             "simulation; add analysis or report to have it set those.")},
         "save": {"type": "boolean", "description": (
             "Save an accepted study as a new file (default true).")}},
        f"{_M}:_ask_agent", required=("request",), offered=(AI_APP,),
        apart="it is the Agent itself, asked from an AI app",
        annotations={"readOnlyHint": False, "destructiveHint": False,
                     "idempotentHint": False, "openWorldHint": True}),
)

#: The Agent's replies, which are how it answers rather than tools it looks
#: with: none is offered to an AI app, which answers in its own words.
REPLIES: dict[str, str] = {
    "propose_config": "the Agent's config for the person to use or not; an AI app "
                      "saves one with save_study",
    "ask_person": "the Agent's question to the person on the page; an AI app asks in "
                  "its own conversation",
    "act": "the Agent's step for the person to confirm on the page; an AI app calls "
           "start_study, stop_study or run_phases_again, each confirmed as it is called",
    "show_scene": "the Agent's scene shown in the Viewer; an AI app writes one with "
                  "write_scene",
    "keep_plan": "the Agent's plan shown under its reply as a checklist; an AI app keeps "
                 "its own",
}


def offered_by(interface: str) -> tuple[Offered, ...]:
    """The tools ``interface`` offers, in the list's order."""
    return tuple(tool for tool in OFFERED if interface in tool.offered)


def offered_named(name: str) -> Offered | None:
    """The tool either interface calls ``name``."""
    return next((tool for tool in OFFERED
                 if name in (tool.name, tool.ai_app_name)), None)
