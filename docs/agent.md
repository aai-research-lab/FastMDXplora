# The FastMDXplora Agent

The FastMDXplora Agent is the natural-language interface. You describe a study
in a sentence; it writes a [FastMDXplora Config](config.md).

```bash
fastmdx agent "simulate trypsin with benzamidine bound at pH 6.5 for 100 ns"
```

```yaml
systems:
  - system: 3PTB
setup:
  ph: 6.5
  forcefield: amber-openff
  ligand_name: BEN
simulation:
  duration_ns: 100
agent: assisted
agent_model: anthropic/claude-sonnet-4-6
```

That Config then runs like any other:

```bash
fastmdx explore --config study.yml
```

The Agent stands where a human stands. It is a fourth way of producing a
Config, alongside the [GUI](gui.md), the [CLI](cli.md) and the
[API](api.md) — and it is reachable **through all three of them**.

---

## The Agent has no privileges

This is the load-bearing claim, so it is worth stating plainly.

**A Config the Agent writes goes through exactly the same validator, by the
same code, as a Config you type by hand.** There is no separate path, no
relaxed mode, no special case. `fastmdxplora.agent` imports the core; the core
imports nothing from the Agent, and a test asserts the direction.

```python
# fastmdxplora/agent/propose.py
from fastmdxplora.config.loader import ConfigError, validate_config
...
try:
    validate_config(config)
except ConfigError as exc:
    ...
```

Three consequences:

- **A proposal that did not validate carries no Config at all** — not the last
  thing that nearly worked with a caveat attached. There is no partially-valid
  result and no best-effort fallback.
- **The Agent cannot name a setting that does not exist**, because the schema
  description it writes from is generated from the same declaration the
  validator checks against.
- **Removing the Agent changes nothing about what a valid study is.** A caller
  bypassing it and calling `validate_config` directly is refused in the same
  way, by the same code, with the same message.

The one thing an Agent-written study carries that a hand-written one does not
is the `agent:` setting, which is **provenance, not permission**. Including
`agent: unvalidated` — see below — the Config itself is still validated.

---

## Connecting an AI model

Nothing in FastMDXplora needs an AI model. The Agent does, and it asks once.

```
$ fastmdx agent model
  No AI model chosen yet.

  AI model:
    [1] Anthropic
    [2] OpenAI
    [3] Other (any OpenAI-compatible URL)
  > 1
  AI model [claude-sonnet-5-5]:
  API key (leave blank to read ANTHROPIC_API_KEY from the environment instead):
  > sk-ant-...

  ✓ Saved to ~/.config/fastmdxplora/model.json
  ✓ Key stored there, readable only by you. It is never written into a study.
```

| Provider | AI model offered first | Environment variable |
|---|---|---|
| `anthropic` | the newest `claude-sonnet` it offers (`claude-sonnet-4-6` where it cannot be asked) | `ANTHROPIC_API_KEY` |
| `openai` | the newest `gpt-N` it offers (`gpt-5` where it cannot be asked) | `OPENAI_API_KEY` |
| `compatible` | whatever you name | `FASTMDX_MODEL_API_KEY` |

The AI model offered first is read from the provider's own list, asked with
the key in the environment or the one stored, and kept to one family: a
provider's newest may be its largest and dearest, or its smallest. Any other
it lists can be chosen, and any name typed. A stored key is sent only to the
provider and address it was stored for, never to another being looked at.

Option 3 covers DeepSeek, vLLM, Ollama, OpenRouter and most local servers,
because they speak the OpenAI chat shape. One entry rather than one per vendor:
a list of vendors goes stale and a protocol does not. It takes a base URL —
`https://api.deepseek.com`, `http://localhost:11434/v1`,
`http://localhost:8000/v1`.

**Nothing extra has to be installed.** `fastmdxplora.agent` ships with the
package; `pip install "fastmdxplora[agent]"` installs no additional
dependencies. The Agent talks to an AI model over the standard library, with no
vendor client library anywhere in it.

### Where the key lives

In one file outside any study, readable only by its owner — or in the
environment, **which is checked first**. That is how a cluster job or a CI run
supplies one without anybody storing it.

| | |
|---|---|
| `$FASTMDXPLORA_CONFIG_DIR/model.json` | if that variable is set |
| `$XDG_CONFIG_HOME/fastmdxplora/model.json` | otherwise, defaulting to `~/.config/` |
| `%APPDATA%\fastmdxplora\model.json` | on Windows |

Written with mode `0600`.

**The key never enters a Config, a Manifest, a log line or an error message.**
Those files get shared, pasted into issues and committed; a key in one is a key
on the internet. What is recorded is the provider and the AI model and nothing
else.

---

## The three modes

```bash
fastmdx agent "..." --assisted        # the default
fastmdx agent "..." --autonomous --budget-hours 40
fastmdx agent "..." --unvalidated
```

They are mutually exclusive, and each writes itself into the Config as
`agent: <mode>`. The budget writes itself in too, as `budget_hours`, a
top-level key with a floor of zero. It is read by `explore` whichever door
the Config came through: a budgeted Config runs in stages, setup first, then
a price, then the rest only if it fits. Required for `autonomous`, which runs
without being shown to anybody; optional in every other mode, and never wrong
to set on a study that will run for days.

### `assisted` — draft it and stop

The default. The Config prints, the repair attempts print with it, and `-o`
also writes it to a file. Nothing runs. You are there to read it.

```bash
fastmdx agent "simulate ubiquitin at pH 6.5 for 50 ns" -o ubiquitin.yml
fastmdx explore --config ubiquitin.yml
```

### `autonomous` — draft it and run it

Nobody is there to read it, so something else has to stop it, and that is a
budget.

```bash
fastmdx agent "simulate ubiquitin for 50 ns" --autonomous --budget-hours 40
```

**Without `--budget-hours` it refuses.** A default allowance would be a number
nobody chose deciding how much of somebody's card to spend.

The budget needs a figure, and the figure does not exist when the Agent
finishes writing. Cost scales with the *solvated* particle count, which depends
on box shape, padding and ion concentration — decisions setup makes. A protein
of 2,000 atoms is 60,000 solvated, and guessing from the residue count would be
inventing the water.

So the run goes in two parts:

```
setup                 cheap, minutes, and it settles the count
estimate              from that count, on this machine
simulation onwards    the expensive part, if it fits
```

The gate sits where the information first exists and before the cost is
incurred. Earlier it would be guessing; later there would be nothing left to
stop.

**A study of several runs is priced on all of them.** Each replica of a sweep
and each system of a campaign is prepared and counted on its own; an umbrella
study prepares the one system its windows share and is priced on every window,
each with its own equilibration, and on the pull that seeds them where it asks
for one.

**When it refuses, setup's output is kept.** It cost minutes and it is worth
having — a shorter study reuses it through `simulation.setup_from`, and the
particle count is what made the refusal possible. The message names the
estimate and the budget, because "too expensive" is usually answered by a
shorter run rather than a larger allowance, and you cannot choose without the
number.

Two other ways it stops: a setup that records no particle count, because
running on would spend an unknown amount — the one thing an unattended run must
not do; and a machine that cannot be measured, because no calibration means no
ceiling. A machine never measured is measured on the study's own prepared system
first; see
[Production runs and GPUs](production.md#knowing-how-long-before-committing-the-card).

### `unvalidated` — mark the work as unchecked

`unvalidated` is a **marking on the work**, not a bypass of the validator. It
says that a phase went outside the schema and nothing checked *the method*. The
study is still recorded, still reproducible, and still has a Config — what it
does not have is anything that checked the science.

The Config is validated exactly as in any other mode. A study asking for a
setting that does not exist is refused here too.

> **What it does today.** The mode is recorded in the Config, travels into the
> run's records, and stamps every figure the marked phase plots. The behaviour
> it is meant to unlock — the Agent writing code of its own, outside the schema
> — is **specified and not yet built**, and `fastmdx agent --unvalidated` says
> so when you run it. So the marking works; what it currently marks is a study
> that stayed inside the schema anyway.

---

## Which phases were checked

`agent` sits at the study level and in every phase. The study level is the
answer for the whole thing; a phase sets its own where it differs.

```yaml
agent: assisted          # an AI model drafted the study
analysis:
  agent: unvalidated     # and the analysis went outside the schema
```

Two levels rather than one, because a single value cannot say what is true of a
real study. A simulation written by hand because the protocol matters, an
analysis explored outside the schema, a setup an AI model drafted — that is one
study, and flattening it to a word loses the only thing a reader needs: which
part to be suspicious of.

The consequence is concrete. A trajectory from a validated simulation is fine
even when the analysis over it was not. Marking it anyway is crying wolf, and a
mark that appears on everything stops being read.

**Set the phase-level value, not only the study-level one.** The per-phase
values are what travel into the [Manifest](manifest.md), which keeps the
departures rather than resolving them away:

```json
{
  "study": "assisted",
  "phases": {"setup": "assisted", "analysis": "unvalidated"},
  "checked": {"setup": true, "analysis": false},
  "departures": {"analysis": "unvalidated"}
}
```

A study claiming `assisted` at the top and letting one phase go unvalidated has
made a claim it does not keep throughout. Resolving to a tidy value would hide
that; naming the departure does not.

`agent` absent everywhere means a person wrote it, so every study run before
this existed stays truthful without being rewritten.

### Which AI model wrote it

```yaml
agent: assisted
agent_model: anthropic/claude-sonnet-4-5-20250929
```

`agent: assisted` says an AI model was involved, not which one, and six months on
that is the difference between a record and a note. "Why did this study pick
300 K" has a different answer depending on whether a frontier AI model or a 7B on
a laptop proposed it.

**An alias is not a version.** `claude-sonnet-4-6` names different software at
different times, because it moves when a new snapshot lands. Pin the dated
string where the record needs to identify what ran.

---

## How it gets to a valid Config

One sentence in, up to a few attempts, one Config out.

```
    request ──▶ prompt (instructions + generated schema) ──▶ AI model
                                                              │
                          ┌───────────────────────────────────┘
                          ▼
                    validate_config
                          │
        ┌─────────────────┼──────────────────┐
        ▼                 ▼                  ▼
     accepted        structural          anything else
        │             refusal              refusal
        ▼                 │                  │
     Config          repair prompt         stop
                          └──▶ back to the AI model
```

**Structural refusals are retried; semantic ones are not.** A Config that does
not match the schema is answerable by reading it. A system that does not
determine its own protonation is not, and an AI model that retries it is guessing
at the question the software declined to guess at. The loop stops and returns
the refusal.

**A repair says what would fix it, and no more than the registry allows.**
It names the offending setting, the legal set where the refusal registry
permits it, and the remedy within the same rule: the setting's name, an
install command, and nothing where the answer is a scientific judgement. The
rule the whole design turns on is that *the validator may say what the
schema permits, and may never say what the chemistry requires*. The builder,
the Agent's `check_config` and an AI app's `check_study` give the same
words. A repair also carries everything the first request did (the
request, the config language, the conversation, the run), so the AI model
reads again what was asked rather than patching only what it was shown.

**Cycles are counted and capped.** Three attempts in all, the first included,
from the command line (`--attempts`), the browser and Python alike. Cheap
validation invites thrashing, and a Config that validates on the fortieth
mutation validates for reasons nobody chose. Exhausting the cap is a refusal, not a
fall-through to whatever last nearly worked.

`--phases` chooses which parts of the Config it writes; the default is
`setup,simulation`.

### Replies by tool calls

Where the AI model takes tool calls, which the providers named above and
most compatible servers do, the Agent declares its tools to the provider and
a reply comes as data rather than as text read by a pattern:

| Tool | What it carries |
|---|---|
| `propose_config` | The Config, a reason for each setting set (`why`, the `alternatives` set aside, and whether the request stated it), and a sentence beside it |
| `ask_person` | A question, and the candidates where there are some |
| `act` | One action, and what it takes (the windows and their values, the analyses) |
| `show_scene` | A scene beside an answer |

A plain answer is the reply's own text, and the looks (below) are tools of
the same kind. Each reason is written into the study's
[`decisions`](config.md), so the report's **Why these settings** says why
each setting was chosen; a decision may be about `systems` and `sweep` too.
A reason is the person's only where the request itself states the value
(310 in "at 310 K"); the AI model saying it was asked is not enough, and
"body temperature" makes 310 K the Agent's reading. A decision the Config
already holds as the person's is kept as it is, and a reason about
anything but a setting's dotted name is set aside and said, never a cause
to refuse a Config. A refusal comes back as `propose_config`'s result, in
the same conversation, and every call is answered in the turn after it.
Text written beside a look is what the AI model is about to do, not its
reply. The sentence beside a Config is shown under it on the page, and a
question's candidates are said with the question.

The instructions, the tools and the config language are the same for every
message, and they are sent first, as a system prompt the provider keeps in
its cache; what changes (the run, the current Config, the files attached,
the request) comes after them. After the first message of a conversation
most of what is sent is read from the cache. What each reply cost is
recorded, and `fastmdx agent` says it:

```
  Used: 1 call, 17,899 tokens in (17,806 written to the cache), 498 out
  Used: 2 calls, 29,170 tokens in (28,770 cached, 300 written to the cache), 412 out
```

The first message of a conversation writes the instructions to the cache
(at a little more than the usual price); a message after it reads them from
there (at about a tenth), for as long as the cache is kept. With Anthropic
that is a setting, **Keep the instructions cached** in the Agent's settings
(or asked by `fastmdx agent model`): `auto`, the default, keeps them for an
hour on the Agent page, where a plan is read before it is answered, and for
five minutes from the command line and `ask_agent`, where a request is
usually asked once; `1h` and `5m` keep them so everywhere. An hour costs
twice the input price to write instead of 1.25 times, once, and each read
renews it. Other providers keep their caches as they decide. `fastmdx agent` says the
reply as a terminal shows it: "Thinking...", each look in the page's words,
the answer without
its Markdown emphasis, a link as its words and its address, and "Accepted
first time." or "Accepted after 2 attempts." for a Config.

A server that does not take tool calls (some local ones) says so on the
first message of a conversation, and is asked in the text protocol from
then on, which is the same loop with the reply read from its first line:
`SAY:` an answer, `ASK:` a question, `DO:` an action, `USE:` a look,
`SHOW:` a scene, and otherwise the YAML of a Config, which is found among
any prose around it. Only the words such servers use are read that way: a
refusal that merely mentions tools is a fault and is said as one, and a
server that took tools and refuses them later in the same reply has failed.
What is noted is noted for that provider, AI model and address, and
`fastmdx agent model` says which a server takes. A rate limit or an
overloaded service is asked again after the wait the provider gives. A
provider's error that quotes the key back has it replaced before it is
said.

```bash
fastmdx agent "..." --phases setup,simulation,analysis --attempts 5
```

---

## The three channels

### From the CLI

```bash
fastmdx agent model                            # choose an AI model, once
fastmdx agent "simulate 1UBQ for 50 ns"        # a sentence
fastmdx agent -f request.txt -o study.yml      # from a file, written to a file
fastmdx agent "..." --autonomous --budget-hours 12
fastmdx agent                                  # no request: opens the GUI panel
```

Every flag is in [The FastMDXplora CLI](cli.md#agent).

### From the GUI

```bash
fastmdx gui        # the workbench, with an Agent section in the sidebar
fastmdx agent      # the same server, opened at that section
```

One browser and one codebase. `fastmdx agent` with no request passes `#agent`
in the URL fragment, which the page reads on load to decide where to start. Two
commands that started two servers would be two things to learn for one thing to
use.

**Conversations belong to studies.** A conversation belongs to the study
it is about, and sees that study's context. It lives inside the study
folder, at `<study>/agent/conversations/`, so copying a
study carries the conversations that made it — the record stays with the
data. A conversation about no study lives at the workspace level. Under the
composer, at the right: the mode, which
opens the Agent's settings; *Conversations*, a list grouped by study with
the loaded one first, where opening a conversation from another study loads
that study; and *New conversation*, which starts a fresh thread and keeps
the last. Every exchange is saved as it happens; a reload shows the thread
as it was. A conversation that launches a run moves into the study it
created.

**Its questions about a study are answered from the study's records when no
AI model is set.** The start page offers three about the study open: what it
found, whether it ran long enough, and what would strengthen it most. With no
AI model set, each is answered from what the study recorded, and the answer
says so: the report's line on what the run supports, each mean with its
standard error or the reason it gave none, the checks the run was held to,
how much more production the withheld means need, what that takes at the
run's own speed and the command that extends the study in place, and that a
single run's error cannot show a state the run never left, which replicas
started independently can. Nothing is said that the records do not hold.
Typed rather than asked from the start page, a question still needs an AI
model. With one set, the three go to it, and it reads the same records with
its tools.

The page is a conversation. What you said sits on the right; what came back
sits under the Agent's icon, at the left: the refusals as the Agent corrected
itself, then the Config as a version of the study, or an answer, or a
question. Newest at the bottom, where the message box is. Enter sends;
Shift+Enter breaks a line. Every message can be copied, edited or retried,
and an edit or a retry cuts the thread from that message on, so the
conversation continues from there rather than with a fork in it.

**The page speaks as the Agent.** The AI model it thinks with is its own and
is named only in its settings (and by `fastmdx agent model`): the page and
`fastmdx agent` say "Thinking...", "What I read for this reply", "From this
study's records", never which AI model or provider. Its replies are set in
Inter, as the rest of the page.

**Its icon says what it is doing**, coloured: working (pulsing), waiting for
you (a question or a confirmation), done, or could not finish. While it
works, each step is a line as it happens: "Looking up "trp-cage" in the
PDB...", then "Looked up "trp-cage" in the PDB", "Writing the config...".

**Each Config is a version of the study.** "Study, version 2" says what
changed from the version before ("Temperature 310 K -> 330 K") with the whole
study under it, each value with where it came from: *you asked* (your
message said it), *your default* (your `fastmdx-defaults.yml`), *Agent's
choice* with the Agent's reason under it, or *default* (FastMDXplora's own);
a value set with no decision recorded says nothing rather than a guess. The
version it replaced folds to one line with **Show** and **Use this version**,
which makes it the newest again without asking the AI model. Only the newest
version has **Run on this machine**, so an older one cannot be run by a
press meant for another; "run it" runs the newest.

**A question comes with its candidates as buttons**, each entry's identifier
first and what it is under it ("2LZM, T4 phage, X-ray 1.7 Å, 1987"); a press
answers with the identifier, and typing still answers. A message that is not
an answer is sent as it is: the conversation carries the question.

**The message box's words follow the situation**: "Describe a study, or ask
about one in this folder" with nothing open, "Change something, or say run
it" after a Config, "Pick one above, or type your answer" after candidates,
"Answer above, or type yes or no" while a confirmation waits, "Ask how it is
going, or tell me to stop it" while a study runs, "Ask why it stopped, or what
would fix it" for one that stopped.

**Useful or Wrong** under each reply marks it, kept with the conversation for
the Agent's evaluation (a second press takes it back), beside what the reply
took in tokens ("17,899 tokens in (17,806 from the cache) · 498 out").

**On a phone** the bar across the top carries FastMDXplora's mark, the study
and the pages; the conversations are behind its chat icon.

**The attempts are shown rather than summarised.** They are the only visible
sign that anything checked the Config, and watching an AI model correct itself
teaches the Config language while you wait.

**A Config is said as a plan** above its actions: the system, the force field
and water, the solvent and box, the conditions, the equilibration and
production lengths, any enhanced sampling, the analyses and the report, each
at the value the run will take, with the ones the Config leaves to their
defaults marked; and the checks the run will be held to (each observable
equilibrated, its correlation time resolved, at least ten independent
samples per mean, the temperature within 5 K of its target, the potential
energy's range per ns per atom), which the report ticks after the run and the
Agent is given ticked. It ends with what setup would build, about how many
particles in what box, and how long the study would take on this machine
where the machine has been timed, worked out as the builder works them out,
so the cost is read before the run rather than learned from setup's log.

Under a Config are *Run on this machine*, *Open in the builder* and *More*:
*Show the config*, *Download config*, *Copy the command*, *Download a
script*, and a checkbox to write every setting rather than only the ones the
Agent set. They are the builder's own functions, on this Config, so the file,
the command and the script are exactly what the builder would produce; a form
in the builder is left as it was. *Open in the builder* puts the Config there
to change, and is not the way out.

A refused *Run on this machine* says what would fix it under the button, as the
builder's refusals do: the setting to change, or the install command where a
package is missing. Where the fix is a setting of the study, **Ask the Agent
to fix it** sends the refusal into the thread as your next message and the
Agent rewrites its Config. A budget, an install and a choice only you can
make are said and not handed to the AI model. An `autonomous` Config that
carries its own `budget_hours` runs with it when the Settings field is empty.

The engine, the mode and the GPU-hour ceiling are in Settings, at the foot of
the sidebar. They are set once.

Nothing in the GUI layer decides whether a Config is acceptable. The validator
does that, as it does for a Config written by hand.

**The key is typed in the browser, sent once, and stored server-side.** It is
never sent back: the endpoint reports which provider and AI model are set and
never the secret, so a page that never receives a key cannot leak one to a
screenshot, an extension or a bug report. A browser cannot hold a secret —
anything the page keeps is readable by anything else the page runs.

Both Agent endpoints are refused off loopback, because one stores an API key
and the other spends it. See
[The FastMDXplora GUI](gui.md#who-can-reach-it).

### From Python

```python
from fastmdxplora.agent import propose_config, completion_for, load_choice

proposal = propose_config(
    "Simulate ubiquitin at pH 7.4 for 10 ns",
    complete=completion_for(load_choice()),
    phases=["setup", "simulation"],
    max_cycles=3,       # attempts in all, the first included
)

proposal.accepted   # True
proposal.cycles     # 2 — it took one repair
proposal.config     # the validated study, as a dict
proposal.refusal    # None here; the reason it stopped, otherwise
proposal.usage      # what the calls cost, where the completion reports it
```

`complete` is the entire AI model interface: a callable taking a prompt string and
returning text. A completion that also has a `turn` (as `completion_for`'s
does) is asked by tool calls: `turn(system, messages, tools)` returns a
`fastmdxplora.agent.turns.Turn` (its text, its calls and their cost).

```python
def my_model(prompt: str) -> str:
    ...
proposal = propose_config("…", complete=my_model)
```

No client object, no message array, no streaming — which is why swapping in a
local server, a mock, or an AI model this package has never heard of takes no
integration work. `proposal.config` is `None` unless validation accepted it.

Then run it like any Config:

```python
import fastmdxplora as fastmdx
fastmdx.FastMDXplora(config_data=proposal.config, output_dir="runs/study").explore()
```

---

## What the Agent sees, and what it can do

Each request goes to the AI model with three things beside the schema:

- **The conversation so far**, the last twelve turns whole, and up to 48
  before them by their first sentence, so a request that refers to one can
  be read and what was settled early in a long thread is not lost.
- **The current Config**, the last one the Agent wrote. A request is a change
  to it unless it plainly describes a different study: the whole Config comes
  back with the change applied and everything else kept. "Make it 5 ns" is an
  edit, not a new study.
- **What the run is doing**: status, stage, the step and the fraction
  complete, elapsed and remaining time, the last error, the health verdict;
  the config the run used, as it was written (or, for a study run from a
  config file, the resolved one without what nobody decided), so "the same
  settings as that one" has something to copy from; once analyses have run, what they found,
  per analysis: the mean, its standard error and unit, the effective sample
  count, and how many frames were discarded as unequilibrated; how much longer
  the study must run for the means it withheld, and what that takes here; and
  whether the study can be continued, with the config that would continue it.
  "Is the
  RMSD converged?" is answered from those numbers, "why did it stop?" from
  the error, and "how far along?" from the step, not from a guess.
- **Your defaults**, the values in the
  [`fastmdx-defaults.yml`](config.md#your-defaults-fastmdx-defaultsyml) that
  applies where the study will be written, each with its `why`. The Agent
  uses one where the request leaves the setting open, and says it is yours
  ("310 K, your default"); a value the request states is the request's.
  The Config it writes has them filled in, recorded in `decisions` with the
  file as their source, as a run would. Where they do not fit the study
  (a setting that the rest of the Config refuses), the Config is kept
  without them and that is said under it.
- **A file you attached.** The `+` at the left of the composer opens a
  picker on the study's own folder, or the workspace for a thread about no
  study. A chosen file goes with that message as context: text types only,
  six at most, a long log kept as its head and tail with the cut marked.
  The message records the file's name, path, size and digest, not its
  bytes. The Agent is told to cite a file when it uses it: *the setup
  manifest records `ligand_pose: auto`*, not a paraphrase.

The Agent does not open files on its own: its tools read a structure you
name, the PDB's and AlphaFold DB's records, the studies in the workspace and
their records (below), and nothing else. What else it needs, it is
handed; what you want it to see, you attach.

A reply is one of four things:

| | |
|---|---|
| **A Config** | YAML. Validated, repaired if refused, shown with its actions. |
| **A question** | When the request is short of something only you can supply, a structure most often. The Agent never invents one. Your next message answers it, and goes back with the request it answers. |
| **An answer** | A paragraph, when you asked something rather than asked for something. No Config, no actions. Under it, each analysis the paragraph names, with the mean the study recorded for it (its error and unit, or that the mean is not determined); choosing one opens its figure on the Analysis page. The value is the record's, whatever the paragraph says, so a number can be checked where it is read. |
| **An action** | One of: run, stop, run the fix, open viewer, open overview, open report, open builder, show config, download config; `rerun windows` with the windows and the values you named; `analyze again` with the analyses you named; or `write the report again`. |

### Looking before it answers

Before it replies, the Agent may look with the software's own tools, up to
four times per reply:

| Tool | What the software tells it |
|---|---|
| `find_structure` | The PDB entries a name answers to ("lysozyme", "trp-cage"), by the names the entries give their molecules or their titles: grouped by protein with the most studied first, each by its best-resolved entry of the protein alone (unmutated, unfused, with no other protein) and the first such entry determined (1VII for the villin headpiece), with method, resolution, year, organism, chains and ligands; for a name of no protein (a designed peptide), the entries the first determined first (1L2Y for trp-cage). AlphaFold DB's predicted models, with their mean pLDDT, where the PDB holds none or where asked. A PDB identifier is described as it is |
| `inspect_structure` | The chains, protein residues, ligands, ions and water a structure holds (a PDB identifier or a PDB or mmCIF file), the residues whose protonation state a study may set, any side chain within 3 Å of a structural metal, and what is worth knowing about it |
| `preview_setup` | What setup will build from a Config (particles, box, solute, water, ions, a padding grown for the cutoff) and how long the whole study takes on this machine, where it has been timed: what the builder says under a structure |
| `check_config` | Whether the validator accepts a Config, and if not, why and what would fix it; if so, the plan you will read, defaults marked |
| `check_selection` | How many atoms, and which residues, an MDTraj selection matches in a structure, as `fastmdx select` says |
| `read_study` | Another study's record, not the one on screen: its Config, what its analyses found, the checks it was held to, how long it ran and why, and what would fix it |
| `methods_of_study` | A study's methods paragraphs as its report gives them, written from what it recorded, to quote when asked how it was set up, simulated or analysed, or for a methods section |
| `studies_in_paper` | The MD studies a paper reports, read by FastMDXplora and every value checked against the paper's words, each said as ready, runs with differences, needs you or cannot run here; with `study`, that study's config, each setting's reason the paper's words. See [Reproducing a paper's MD studies](papers.md) |
| `list_studies` | The studies in the workspace, newest first, each with its system, state, length, force field, tags, your note and the means it recorded; optionally only those with a tag |
| `compare_studies` | How two studies differ: each setting one asks for and the other does not, and their means side by side, a difference called resolved only where it is more than the stated multiple of its combined standard error |
| `current_view` | In the GUI, what the page shows (the study, the frame, the view and the residues chosen) as where to look, then the facts from that study's record or structure, read by `read_study`, `inspect_structure` or `check_selection`. It reads the page's own view, never one the AI model writes |

It is told to look rather than guess: to find a structure named in words
before writing its PDB identifier, to preview before stating a size or a
time, to inspect a structure before choosing its chains, ligand or a residue's
state, to check a selection before writing one into a Config, and to quote
what the software said rather than a number of its own. The tools only look:
nothing is run, written or started by one, and a look is not one of the
attempts a Config is allowed. Hosted, a tool reads inside the workspace only,
as the builder does.

`list_studies` and `compare_studies` are the ones an AI app calls through
`fastmdx mcp`, written once, so the Agent and an AI app give the same
answer. A study holding a link out of the workspace is neither listed nor
read.

#### A structure named in words

The Agent was once given six names and their identifiers in its
instructions, and an AI model recalled the rest: "trpcage" was written as
1UAO, which is chignolin, and the study validated perfectly. Now no
identifier is recalled. A system named in words is looked up with
`find_structure`, and the identifier is named back with what it is ("1L2Y,
the NMR structure of trp-cage"), so a wrong one is visible. Where more than
one entry fits (hen egg-white or T4 lysozyme, an NMR or a crystal
structure), the Agent asks which, naming the entries found. A model from
AlphaFold DB is said as a prediction, with its confidence, and its file is
yours to download into the workspace. Where the PDB cannot be reached, or
answers with an error, nothing is guessed in its place: the Agent asks for
the identifier or a structure file. A search takes at most a minute in all,
and an answer is kept for a week, so a conversation does not ask twice.

What it looked at is folded under its reply, each look in words ("Looked up
"trp-cage" in the PDB, checked the config"), with what it found in a line and
what the software said in full under that, and kept with the thread. From the
command line, `fastmdx agent` prints a line for each, in the same words.

Under it, **What I read for this reply**: each step the Agent took for
this reply (each time its AI model was asked), what it was given, as it
went. Replying by tool calls, its instructions (the same each step, shown
once), the tools it could use and the messages it had not been given
before, a look's result among them; in text, each prompt. The page and the
terminal speak as the Agent: its AI model is named only in its settings. It is kept beside the conversation, as
`agent/conversations/receipts/<sha256>.json` (the newest 100, the system
prompt once in `receipts/systems/`), each text bounded to 64,000 characters
with the cut marked, read only when the fold is opened and checked against
its SHA-256 then. A message about a study that is no longer the one open, or
a reply that comes back after another study was opened, is refused
(`agent.view.changed`); what was sent is kept with the study it was about.

#### Tools from outside

A package installed beside FastMDXplora can give the Agent tools of its own,
such as a service's record of what a lab has run before, without this package
changing. It names them under the `fastmdxplora.agent_tools` entry point:

```toml
[project.entry-points."fastmdxplora.agent_tools"]
lab = "my_service.agent_tools:tools"
```

where `tools` is an `AgentTool`, a list of them, or a function returning
either:

```python
from fastmdxplora.agent.tools import AgentTool, ToolRefused

def _history(box, asked):
    lab = asked.get("lab")
    if not lab:
        raise ToolRefused("Name the lab as `lab`.")
    return f"{lab} ran 3 studies of 1UBQ at 300 K."  # what was found, in words

tools = [AgentTool("lab_history", "`lab` (its name).",
                   "the studies a lab has run before.", _history)]
```

A program can pass the same objects for one toolbox only, as
`Toolbox(extra=(...))`. Either way a tool is held to the rules above: it only
looks, what it says is quoted to the AI model as the software's finding, and
`box.path_for` is the rule for any path it reads. A name already taken keeps
its first owner, a name must be lower case (letters, digits and underscores),
and a tool that fails to load is left out with a warning rather than stopping
the Agent.

### Written as it goes

The reply is shown as the AI model writes it, and each look as it is taken; a
config appears line by line. While it is written the send button is a stop:
pressing it ends the reply, the request to the AI model is closed with it, and
nothing it had written is kept. The next message goes on from yours. Both
stream shapes the providers use are read, content-block events and the OpenAI
chat shape, so a local or compatible server streams too.

### Showing what an answer is about

An answer about something in the open study that can be seen (a frame,
residues that move or hold the ligand, a colouring by one of its results) may
end with a proposed scene. It is shown under the answer in words ("A scene to
show this: frame 40, coloured by rmsf, resSeq 20 to 25 highlighted"), with a
name you can change and a **Write this scene** button. Nothing is written until
you press it: the scene is then kept with the study (`scenes/<name>.mvsx`,
MolViewSpec) and shown in the Viewer, and a thread opened again says it was
written. The Agent writes the proposal as one line, `SHOW: frame 40; colour
result:rmsf; highlight resSeq 20 to 25`, read by a strict pattern: a part it
does not allow drops the proposal and keeps the answer.

### Acting

**Your instruction is the click.** "Run it" typed into the thread does what
pressing *Run on this machine* does, through the same door, so the mode's
gates apply to a word as they do to a press: an `autonomous` run still needs
its budget. The run is said under the message that started it, "Running
version 2, started 13:41", with **Watch on the Overview** and **Stop** (which
asks first); read again later, "Started version 2 at 13:41". Nothing happens
silently.

**When it ends, what it found**, under the conversation, once: how it ended
and how long it took, what it found with each error, whether it ran long
enough, and what would strengthen it, written from the study's records
(`POST /api/agent/run-summary`), so it costs no tokens. A run that ended while
the page was closed is said when the conversation is next opened. A study of
several runs is said when no process runs it any more ("The runs ended: 2
completed, 1 failed", and "did not finish" for a run skipped after a Stop or
a failure), with what they found together.

**It never acts unasked.** Not on a question, not on a request for a Config,
not because it thinks you would want it, and never twice in one reply. A
reply that names an action and then keeps talking is shown as prose and not
carried out.

**A run you did not plainly ask for is confirmed.** The AI model reads what you
attach, and a file can tell it what to say, so the prompt is not what decides.
The software reads your own message: *run it*, *start the study*, *go ahead*
and the like run at once; after anything else a `run` asks first, and only
*yes* or *run it* starts it. The question names the version and what it is,
with **Run it** and **Not now**:

```
Run version 2 on this machine?
1L2Y trp-cage · 330 K, 1 bar · 10 ns, 2 fs steps · about 3,305 particles
```

**Stopping is confirmed.** A run stopped is hours gone, so `stop` asks first,
naming where the run is:

```
Stop the run at production step 16,000?
What it has written so far is kept.
```

with **Stop it** and **Keep running**. Anything that is not *yes* is *Not
stopped*.

**A change and a run in one message** writes the Config and says *say run
when you have read it*. One step of seeing what is about to run is what
`assisted` promises.

### Continuing a study that stopped

Say *continue it*, or *continue to 0.5 ns total*, on a study that reached
production and stopped. The Agent is handed a config that continues it:
`simulation.resume_from` naming the study, and `duration_ns` as the total
production the study should end with (or `extra_ns` for an amount more), the
same meanings the command line gives them. What is done is read from the
record: the parent's resolved config has the equilibration lengths, the
checkpoint's sidecar has the step. Stopped at whole-run step 321,000 with
100,000 of equilibration, 0.442 ns of production is done; 0.5 ns in all
leaves 0.058. The study is extended in place from its last checkpoint, with
the same prepared system and no minimisation or equilibration, which is what
makes it the same trajectory rather than a new run from a snapshot; every
segment is then joined and the analyses rerun, and the GUI watches the study
while it does. The Agent never writes `resume_from` by hand. Where a study
cannot be continued, because it has no production checkpoint or its method
deposits bias a checkpoint does not carry, it says why and offers a fresh
run.

### Why it stopped, and what fixes it

Ask why a study stopped, or what to do now, and the Agent answers from the
study's own record of what would fix it: the fix, the command or config
that runs it, and what it costs at the speed the study ran. A stopped run
is `fastmdx resume`; umbrella windows that sampled too little are run again
longer with `--rerun-window`, by the length the thinnest needs; gaps between
windows get the design their sampling implies. Where the answer is a choice
only you can make, such as a ligand's protonation, the Agent says so and
names where it is recorded, and offers no value. See
[What would fix it](refusals.md#what-would-fix-it-and-what-it-costs).

Tell it to carry the fix out (*resume it*, *rerun those windows*) and it
replies `run the fix`: the first fix that is this software's own command, a
resume or windows run again, is shown to you with its command and price, and
runs when you say yes. A fix waiting on a choice only you can make, a setting
to change or an install command is never run for you.

In an umbrella study, name the windows and what they should run with
(*rerun window 3 at 6000*, *windows 2 and 5 again for 4 ns*) and the Agent
replies `rerun windows 3 at 6000`. The software reads the windows and the
numbers from that line, checks them against the study, builds the command
from the study's own record (`--rerun-window` with `--rerun-force-constant`
or a length) and asks you, with the spring in its unit and the price at the
study's speed. Every other window is kept. The Agent uses the values you gave;
asked for a stiffer spring or a longer run without a number, it asks for one.

Ask it to analyse the study open again, or to add an analysis to it (*add
SASA and hydrogen bonds*, *analyse it again*), and it replies `analyze again`
with the analyses by their names in the software (`analyze again rmsd rg
sasa hbonds`), or alone for those the study ran last; ask for its report
again and it replies `write the report again`. The software checks the
names against its own, says what will be written again and that what it
replaces is kept in the study's `previous/` folder, and runs the phase
command with `--rerun` on the study when you say yes (`fastmdx analyze
--output <study> --rerun`, or `fastmdx report` for the report). Nothing is simulated. Setup and simulation
are not run again on a study that has them; the Agent writes a new study
from it instead (`simulation.setup_from`, `simulation.resume_from`).

### Saying what "done" means before the run

Ask for a quantity to a precision (*simulate chignolin until its RMSD is
determined to 0.01 nm*), or to run until something is determined, and the Agent
writes the study's stopping rule, `simulation.stop_when`: the quantities, the
error each must reach, three replicas over the seed, and the most production
any run may reach. The plan shows it on its **Stops when** line before
anything runs, so the criterion is committed to before any data is seen,
and the code, not the AI model, judges it afterwards. Where you state no
precision, the Agent chooses one to answer your question; it is in the plan
for you to change. A rule the study cannot keep (no replicas, an analysis
that records no mean, a ceiling below the first piece) is refused by the
same validator that gates every proposal, so the Agent repairs it before you
see it. Asked afterwards why the study ran as long as it did, the Agent
answers from the record of each round. See
[Running until it is determined](production.md#running-until-it-is-determined).

## What the Agent will not do

- **It does not invent a structure.** A request that names none gets a
  question back. A request that names a molecule with several deposited
  structures gets the candidates and a question.
- **It does not decide chemistry the software declined to decide.** A refusal
  that needs a scientific judgement stops the loop rather than being guessed
  around.
- **It does not filter your topic.** There are no content refusals. Every
  refusal you will see from it is a schema, chemistry or environment refusal
  from the ordinary registry.
- **It does not see your key in anything it writes.**

### What is not built yet

One thing is specified and incomplete, and it is better to know than to find
out:

- **`unvalidated` is recorded and marked, not enforced.** The mode reaches the
  Config, the Manifest and every figure the marked phase plots. What it is
  meant to unlock — the Agent writing code of its own, outside the schema — is
  not built, so there is currently nothing outside the schema for it to mark.

---

## See also

- **[The FastMDXplora Config](config.md)** — what it is writing
- **[FastMDXplora refusals](refusals.md)** — the vocabulary it is answering to
- **[The FastMDXplora Manifest](manifest.md)** — where the provenance lands
- **[Production runs and GPUs](production.md)** — budgets, calibration and campaigns
- **[How FastMDXplora is validated](validation.md)** — how well an AI model actually does at this, evaluated rather than assumed
