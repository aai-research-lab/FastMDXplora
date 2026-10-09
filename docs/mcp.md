# FastMDXplora from your AI app (MCP server)

An AI app that speaks the [Model Context
Protocol](https://modelcontextprotocol.io) (an AI chat app, an AI coding tool,
or an AI agent your lab builds) can design, check, run and read FastMDXplora
studies on your machine. You talk to the AI app; the AI app starts `fastmdx mcp`
and asks FastMDXplora for what it needs.

Two things hold however the AI app is asked:

- **The AI app's model writes, the validator judges.** The AI app's model
  writes a study's config in the config language and gives it to
  `check_study`; the validator accepts it or refuses it with what would fix it,
  exactly as it does a config typed by hand. Nothing it has not accepted is
  saved or run, and a config written in an AI app is recorded as one
  (`agent: assisted`).
- **Nothing bypasses the checks.** A study runs only from a config file the
  validator accepted, whose plan was checked as the file now is, with your
  go-ahead.

## Setting it up

The AI app starts the server itself, so you give it the command once, in its
settings. Most AI apps take a configuration file of this shape:

```json
{
  "mcpServers": {
    "fastmdxplora": {
      "command": "/full/path/to/fastmdx",
      "args": ["mcp", "--workspace", "/Users/you/studies"]
    }
  }
}
```

- **`command`** is the full path that `which fastmdx` prints in the environment
  FastMDXplora is installed in. An AI app started from the desktop has not
  activated that environment, so a bare `fastmdx` is usually not found.
- **`--workspace`** is the one folder the AI app's tools may use: studies are
  written there, and configs and structures are read from there. A path that
  leaves it, by `..` or by a link, is refused; so is a config naming a file or
  folder outside it (a structure, a trajectory, a study to continue from), and a
  study holding a link out of it is not read. Without `--workspace`, the folder
  the AI app starts the server in is used, which is rarely the one you mean; the
  top of the file system and your home folder itself are refused.
- **`--read-only`** offers no tool that starts or stops a study: the AI app
  can still write, check and save configs and read studies, and you run a study
  with `fastmdx explore --config FILE` or from the GUI.

AI apps that add a server from the command line take the same command:
`fastmdx mcp --workspace /Users/you/studies`.

No AI model of your own is needed: the AI app's model does the writing. The
one tool that calls another AI model is `ask_agent` (below).

## What the AI app can do

### Tools

| Tool | What the software says |
|---|---|
| `find_structure` | The PDB entries a molecule's name answers to, by the names entries give their molecules or their titles, grouped by protein with the most studied first, each by its best-resolved entry of the protein alone and the first such entry determined, with its method, resolution, chains and ligands (for a designed peptide, the entries the first determined first); AlphaFold DB's predicted models, with their mean pLDDT, where the PDB holds none or where asked. Where more than one could be meant, the AI app is told to ask you which; offline, it says it cannot reach the PDB and offers nothing |
| `inspect_structure` | The chains, residues, ligands, ions and water a structure holds, the residues whose protonation state a study may set, any side chain by a structural metal |
| `check_selection` | How many atoms, and which residues, an MDTraj selection matches |
| `preview_setup` | What setup will build (particles, box, solute, water, ions) and how long the study takes on this machine, where it has been timed |
| `check_study` | Whether the validator accepts a config, and if not why and what would fix it; if so the plan, defaults marked, [your defaults](config.md#your-defaults-fastmdx-defaultsyml) filled where it leaves a setting unset (each named, and a config they would make the validator refuse refused here, naming the file), whether this machine can run it, and its `plan_id` |
| `save_study` | A config written as a new file, once the validator accepts it, with your defaults filled in (added under its blocks, its comments and order kept) and recorded in its `decisions`, its plan and `plan_id`; a file is never written over. Recorded as written in an AI app (`agent: assisted`, and in `agent_model` the AI app, as it names itself; it does not name its model) unless the config says otherwise, or `by_hand` says you wrote it |
| `list_machines` | The machines inspected from this computer, as last recorded, each with its GPUs and whether it is ready for this computer's code, and the jobs whose results come back into the workspace; nothing is asked of the machines |
| `start_study` | A checked config run on this machine, or with `machine` sent to run on one of yours (below) |
| `stop_study` | A running study stopped at its next frame, with a checkpoint there |
| `remote_status` | How a job sent to another machine is doing, and the last lines of its log; asked of the machine at most every 30 s |
| `fetch_study` | A finished job's results brought into its folder in the workspace, each size said first (below); not offered by a read-only server |
| `cancel_study` | A job on another machine stopped, the machine asked first whether it is still going; you are asked where the AI app can ask (as `stop_study`); its folder there stays; not offered by a read-only server |
| `run_phases_again` | A study's analysis, its report or both run again in its folder from the settings it recorded, simulating nothing, once you agree: the analyses you name (by default those it ran last), the report written again too where it has one, what is replaced kept in `previous/`, a study of several runs run by run with its comparison built again; setup and simulation are not run again in place; not offered by a read-only server |
| `list_studies` | The studies in the workspace, newest first, with their state, the means they recorded, and the tags and note you gave each (or only those with a tag); and the config files not yet run |
| `tag_study` | Tags added to a study as you ask, kept in its folder and shown on its card in the GUI; it only adds: removing a tag, or writing the study's note, is yours in the GUI; not offered by a read-only server |
| `read_study` | Where a study stands (its step and time left while it runs) and what it recorded: its config, what its analyses found with errors and units, the checks, why it stopped and what would fix it |
| `methods_of_study` | A study's methods paragraphs as its report gives them: preparation, protocol, how each mean and its error were determined, any rule it ran until, an AI model's part, and the software; a preparation outside the workspace is not read |
| `compare_studies` | The settings two studies differ in, and the means each recorded, a difference marked resolved only past twice its combined standard error, and one that cannot be judged (no error, or a mean not determined) said to be not assessed |
| `views_of_study` | The views the person saved with a study in the GUI (each a frame, a representation and colouring, a superposition), the selections they named, and the scenes written with it; `write_scene` starts from a view by its name |
| `make_movie` | A movie of a study's frames into its `movies/` folder, as its Viewer in the GUI makes one: a view you saved (or the Viewer as it opens), changed as asked, frames from one to another every so many, frames in between for a smoother movie, at 10 to 60 frames a second and up to 4K, rendered by the Viewer in a browser with no window and encoded by ffmpeg on this computer; not offered by a read-only server |
| `write_scene` | A view of a study written as a scene file (MolViewSpec, `scenes/<name>.mvsx`), for you to open in the GUI or on molstar.org: a frame, a representation and colouring (a result such as `result:rmsf` among them), the frames superposed and fitted to the first frame, the starting structure or the deposited one, your selections, and atoms highlighted |
| `ask_agent` | Optional: the FastMDXplora Agent's answer, for when you ask for the Agent (below) |

A mean is given with its standard error, to the decimal place of the error's
second significant figure. A tool that cannot do what it was asked says why,
and what would fix it, as an error the AI app's model can act on.

### Prompts

Most AI apps offer these as slash commands. Each starts a piece of work the way
FastMDXplora does it, the AI app's model writing and the validator judging, and
nothing run without your word:

| Prompt | |
|---|---|
| **Design a study** | The AI app's model writes it from what you want to learn, checked until the validator accepts it; you see the plan before anything runs |
| **Explain what a study found** | Each number from the study's own record, with its error and unit |
| **Why did a study stop?** | The reason and the fix, its command and its cost, from the record |
| **Continue a study** | More production, from the config the study's record gives, extended in place and analysed as one study |

### What it can read

Two guides: **working with studies** (the order of work, and what a mean, "not
determined", "resolved" and a refusal mean) and **the config language**, every
setting a config can carry with what it does and its default. And each study in
the workspace, as its record, which most AI apps let you attach to a message.

### The FastMDXplora Agent, if you ask for it

`ask_agent` is the Agent as the GUI has it: it looks with the software's own
tools, a study it writes is accepted by the validator before it comes back,
saved as a new file with its plan and `plan_id` and recorded as its AI model's,
and it answers questions or asks you what only you can say. It runs nothing.

It is optional, offered last, and the AI app is told to use it only when you
ask for the Agent. Which AI model it writes with:

- **The AI app's model**, where the AI app lends it (the protocol's *sampling*,
  which the AI app declares; it may ask you first). Nothing is paid twice and no
  key of yours is used. What the Agent sends an AI model, its instructions and
  the config language with your request, is then sent to the AI app, which may
  show it to you before it answers, as it is sent to your provider when your own
  key is used. In the 2026-07-28 protocol each reply is a round of the call,
  carried back in its signed state, so the Agent's looks and corrections still
  happen between replies. If the AI app declines, nothing is written, and your
  own key is not used in its place.
- **Otherwise, your own AI model**: the one you chose once, in a terminal, with
  `fastmdx agent model` (see [Connecting an AI model](agent.md#connecting-an-ai-model)),
  called on your own API key, each call paid for on top of the AI app's own.

Its answer says which it was, and a study it writes records the AI model in
`agent_model`: the AI app's name and the model the AI app named, such as
`app-name/model-name`, or your provider and AI model. What it writes is
judged by the same validator as what the AI app's model writes.

## Starting a study

`start_study` runs a config file in the workspace, and only:

- while its `plan_id` is the one `check_study` gave for the file as it is now;
  a changed file is checked again, and its plan shown again;
- when the config does not say `agent: autonomous` (that it runs without being
  shown to anyone), since here its plan is shown first;
- when no other study is running in the workspace, so each has the machine to
  itself and its timings mean what they say;
- into a results folder not already used: the config's `output`, or, without
  one, a folder named after the file, beside it. A continuation runs in the
  study it continues.

Where the AI app can put a form in front of you, you are asked first, with the
plan, the results folder and the time this machine is known to take, and only a
ticked **Go ahead** starts it. Where it cannot, its own approval of the call is
the gate; most AI apps ask before a tool that is not read-only runs. The run is
started as the GUI starts it, writes its log beside its results, and goes on
after the AI app closes; the GUI shows it, and `read_study` says how far it
has got. A start is said once the run is going: one that ends as it starts says
so, with the end of its log.

`stop_study` asks the same way before it stops one. The run is given time to
reach its next frame and write a checkpoint there (20 s by default,
`FASTMDX_STOP_GRACE_SECONDS`, and 10 s more), as the GUI's Stop gives it; one
that has not stopped by then is ended, with what is left of its process group,
and a line in its log says so. That is watched from a process of its own, so it
happens whether or not the AI app is still open, and the run is identified
again first, so a process number given to something else since is never
signalled.

The GUI keeps the same rule. Every start, from the GUI's **Run** or an AI
app's `start_study`, holds the workspace's starting lock
(`.fastmdxplora-starting`, a lock the operating system holds for the process,
so none is left behind by one that crashed) from its check to the start, and
adds the run to the workspace's list of runs started there
(`.fastmdxplora-runs.json`). So neither starts while the other's study runs,
and two never start at the same moment. A refusal names the study running and
who started it. The GUI keeps the rule in the folder it was started in and in
the folder it puts new studies in (never your home folder): give the AI app
either as `--workspace` and the two take turns. An AI app also finds a run
started by hand in its workspace, by the record the run keeps; the GUI goes by
the list.

## Running on another machine

`start_study` with `machine` sends the checked config to run on one of your
machines instead, over your own `ssh`, as [`fastmdx remote send`](remote.md)
does:

- **Only machines you inspected at a terminal** (`fastmdx remote --machine
  gpu-box`), listed by `list_machines`. An AI app never adds a machine, never
  installs FastMDXplora on one (it names `fastmdx remote install` for you to
  run), and never types a password: a machine whose `ssh` asks for one is
  reached only for ten minutes after you sign in to it at a terminal with
  `fastmdx remote --machine gpu-box`, which keeps its connection open.
- **Only once you agree here.** You are asked with the plan, the machine, what
  runs it there, and every file sent with its size. Where the AI app cannot put
  that question to you, nothing is sent: its own approval of the call is not
  enough to send a study off this computer (`remote.send.unconfirmed`), and you
  are given the `fastmdx remote send` command instead.
- **Only the files in the config's own folder travel**, never one elsewhere in
  the workspace or outside it (`remote.input.outside`). A prepared study named
  by `setup_from` or `resume_from` may sit beside it.
- **Only where its GPUs have room.** A workstation's GPUs are shared; the
  question says what else sent from this computer is running there, each GPU's
  free memory, and the one the study goes to (a config that names its own GPUs
  keeps them). A study that does not fit on its GPU now is refused before you
  are asked (`remote.machine.no_room`). A cluster's scheduler gives each job its
  GPU.

The results come back to the config's `output`, or a folder named after the
file, inside the workspace and not already used. `remote_status` says how the
job is doing; `fetch_study` brings it back once it has finished, saying first
how much it brings, with the trajectories and checkpoints left on the machine
unless asked for. Where the AI app cannot ask you, only a fetch under 100 MB goes
ahead (`remote.fetch.unconfirmed`). The AI app reaches only jobs whose results
come back into its workspace.

## The protocol

`fastmdx mcp` speaks MCP over standard input and output, without an SDK, in
both of its eras. The AI app is what the protocol calls the client:

- **2026-07-28**, where every request carries its version and the AI app's
  capabilities and there is no session. A question for you mid-call is an
  `input_required` answer, and the AI app asks the call again with yours.
- **2025-11-25, 2025-06-18, 2025-03-26 and 2024-11-05**, for AI apps that open
  with `initialize`. A question for you is a request sent to the AI app.

Standard input and output carry the protocol and nothing else; everything that
would print there is sent to standard error, which AI apps keep as the server's
log, and nothing the tools start can read the protocol's input. When the AI app
closes the server's input, calls still being served have five seconds to answer.
A call the AI app cancels stops where it is: a study it would have started is
not started.
Lists and reads carry caching hints: the tools, prompts and guides for an hour,
the studies for five seconds. The test suite drives the server with the
protocol's own client library in both eras.

## See also

- **[The FastMDXplora Agent](agent.md)**: the Agent `ask_agent` asks, and how a
  package can give it tools of its own
- **[The FastMDXplora Config](config.md)**: what it writes
- **[FastMDXplora refusals](refusals.md)**: what a refusal says, and its fix
- **[Production runs and GPUs](production.md)**: when a study stops early
