# FastMDXplora in an assistant

An assistant that speaks the [Model Context Protocol](https://modelcontextprotocol.io)
(a chat app or a code editor) can design, check, run and read FastMDXplora
studies on your machine. You talk to the assistant; the assistant starts
`fastmdx mcp` and asks FastMDXplora for what it needs.

Two things hold however the assistant is asked:

- **The assistant writes, the validator judges.** The assistant writes a
  study's config in the config language and gives it to `check_study`; the
  validator accepts it or refuses it with what would fix it, exactly as it does
  a config typed by hand. Nothing it has not accepted is saved or run, and a
  config the assistant wrote is recorded as written with one (`agent: assisted`).
- **Nothing bypasses the checks.** A study runs only from a config file the
  validator accepted, whose plan was checked as the file now is, with your
  go-ahead.

## Setting it up

The assistant starts the server itself, so you give it the command once, in its
settings. Most clients take a configuration file of this shape:

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
  FastMDXplora is installed in. An app started from the desktop has not
  activated that environment, so a bare `fastmdx` is usually not found.
- **`--workspace`** is the one folder the assistant's tools may use: studies are
  written there, and configs and structures are read from there. A path that
  leaves it, by `..` or by a link, is refused; so is a config naming a file or
  folder outside it (a structure, a trajectory, a study to continue from), and a
  study holding a link out of it is not read. Without `--workspace`, the folder
  the client starts the server in is used, which is rarely the one you mean; the
  top of the file system and your home folder itself are refused.
- **`--read-only`** offers no tool that starts or stops a study: the assistant
  can still write, check and save configs and read studies, and you run a study
  with `fastmdx explore --config FILE` or from the GUI.

Clients that add a server from the command line take the same command:
`fastmdx mcp --workspace /Users/you/studies`.

No model of your own is needed: the assistant's model does the writing. The
one tool that calls another is `ask_agent` (below).

## What the assistant can do

### Tools

| Tool | What the software says |
|---|---|
| `inspect_structure` | The chains, residues, ligands, ions and water a structure holds, the residues whose protonation state a study may set, any side chain by a structural metal |
| `check_selection` | How many atoms, and which residues, an MDTraj selection matches |
| `preview_setup` | What setup will build (particles, box, solute, water, ions) and how long the study takes on this machine, where it has been timed |
| `check_study` | Whether the validator accepts a config, and if not why and what would fix it; if so the plan, defaults marked, whether this machine can run it, and its `plan_id` |
| `save_study` | A config written as a new file, once the validator accepts it, with its plan and `plan_id`; a file is never written over. Recorded as written with an assistant (`agent: assisted`, and in `agent_model` the app, as it names itself; it does not name its model) unless the config says otherwise, or `by_hand` says you wrote it |
| `start_study` | A checked config run on this machine (below) |
| `stop_study` | A running study stopped at its next frame, with a checkpoint there |
| `list_studies` | The studies in the workspace, newest first, with their state and the means they recorded; and the config files not yet run |
| `read_study` | Where a study stands (its step and time left while it runs) and what it recorded: its config, what its analyses found with errors and units, the checks, why it stopped and what would fix it |
| `compare_studies` | The settings two studies differ in, and the means each recorded, a difference marked resolved only past twice its combined standard error, and one that cannot be judged (no error, or a mean not determined) said to be not assessed |
| `ask_agent` | Optional: the FastMDXplora Agent's answer, for when you ask for the Agent (below) |

A mean is given with its standard error, to the decimal place of the error's
second significant figure. A tool that cannot do what it was asked says why,
and what would fix it, as an error the assistant can act on.

### Prompts

Most clients offer these as slash commands. Each starts a piece of work the way
FastMDXplora does it, the assistant writing and the validator judging, and
nothing run without your word:

| Prompt | |
|---|---|
| **Design a study** | The assistant writes it from what you want to learn, checked until the validator accepts it; you see the plan before anything runs |
| **Explain what a study found** | Each number from the study's own record, with its error and unit |
| **Why did a study stop?** | The reason and the fix, its command and its cost, from the record |
| **Continue a study** | More production, from the config the study's record gives, extended in place and analysed as one study |

### What it can read

Two guides: **working with studies** (the order of work, and what a mean, "not
determined", "resolved" and a refusal mean) and **the config language**, every
setting a config can carry with what it does and its default. And each study in the workspace, as its record, which most
clients let you attach to a message.

### The FastMDXplora Agent, if you ask for it

`ask_agent` is the Agent as the GUI has it: it looks with the software's own
tools, a study it writes is accepted by the validator before it comes back,
saved as a new file with its plan and `plan_id` and recorded as its model's,
and it answers questions or asks you what only you can say. It runs nothing.

It is optional, offered last, and the assistant is told to use it only when you
ask for the Agent. Which model it writes with:

- **The app's own**, where the app lends it (the protocol's *sampling*, which
  the app declares; it may ask you first). Nothing is paid twice and no key of
  yours is used. What the Agent sends a model, its instructions and the config
  language with your request, is then sent to the app, which may show it to
  you before it answers, as it is sent to your provider when your own key is
  used. In the 2026-07-28 protocol each reply is a round of the call,
  carried back in its signed state, so the Agent's looks and corrections still
  happen between replies. If the app declines, nothing is written, and your own
  key is not used in its place.
- **Otherwise, yours**: the one you chose once, in a terminal, with
  `fastmdx agent model` (see [Connecting a model](agent.md#connecting-a-model)),
  called on your own API key, each call paid for on top of the assistant's own.

Its answer says which it was, and a study it writes records the model in
`agent_model`: the app's name and the model the app named, such as
`app-name/model-name`, or your provider and model. What it writes is
judged by the same validator as what the assistant writes.

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

Where the client can put a form in front of you, you are asked first, with the
plan, the results folder and the time this machine is known to take, and only a
ticked **Go ahead** starts it. Where it cannot, its own approval of the call is
the gate; most clients ask before a tool that is not read-only runs. The run is
started as the GUI starts it, writes its log beside its results, and goes on
after the assistant closes; the GUI shows it, and `read_study` says how far it
has got. A start is said once the run is going: one that ends as it starts says
so, with the end of its log.

`stop_study` asks the same way before it stops one. The run is given time to
reach its next frame and write a checkpoint there (20 s by default,
`FASTMDX_STOP_GRACE_SECONDS`, and 10 s more), as the GUI's Stop gives it; one
that has not stopped by then is ended, with what is left of its process group,
and a line in its log says so. That is watched from a process of its own, so it
happens whether or not the assistant is still open, and the run is identified
again first, so a process number given to something else since is never
signalled.

The GUI keeps the same rule. Every start, from the GUI's **Run** or an
assistant's `start_study`, holds the workspace's starting lock
(`.fastmdxplora-starting`, a lock the operating system holds for the process,
so none is left behind by one that crashed) from its check to the start, and
adds the run to the workspace's list of runs started there
(`.fastmdxplora-runs.json`). So neither starts while the other's study runs,
and two never start at the same moment. A refusal names the study running and
who started it. The GUI keeps the rule in the folder it was started in and in
the folder it puts new studies in (never your home folder): give the assistant
either as `--workspace` and the two take turns. An assistant also finds a run
started by hand in its workspace, by the record the run keeps; the GUI goes by
the list.

## The protocol

`fastmdx mcp` speaks MCP over standard input and output, without an SDK, in
both of its eras:

- **2026-07-28**, where every request carries its version and the client's
  capabilities and there is no session. A question for you mid-call is an
  `input_required` answer, and the client asks the call again with yours.
- **2025-11-25, 2025-06-18, 2025-03-26 and 2024-11-05**, for clients that open
  with `initialize`. A question for you is a request sent to the client.

Standard input and output carry the protocol and nothing else; everything that
would print there is sent to standard error, which clients keep as the server's
log, and nothing the tools start can read the protocol's input. When the client
closes the server's input, calls still being served have five seconds to answer.
A call the client cancels stops where it is: a study it would have started is
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
