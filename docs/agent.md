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

## Connecting a model

Nothing in FastMDXplora needs a model. The Agent does, and it asks once.

```
$ fastmdx agent set
  Model:
    [1] Anthropic
    [2] OpenAI
    [3] Other (any OpenAI-compatible URL)
  > 1
  Model [claude-sonnet-4-6]:
  API key (leave blank to read ANTHROPIC_API_KEY from the environment instead):
  > sk-ant-...

  ✓ Saved to ~/.config/fastmdxplora/model.json
  ✓ Key stored there, readable only by you. It is never written into a study.
```

| Provider | Default model | Environment variable |
|---|---|---|
| `anthropic` | `claude-sonnet-4-6` | `ANTHROPIC_API_KEY` |
| `openai` | `gpt-5` | `OPENAI_API_KEY` |
| `compatible` | whatever you name | `FASTMDX_MODEL_API_KEY` |

Option 3 covers DeepSeek, vLLM, Ollama, OpenRouter and most local servers,
because they speak the OpenAI chat shape. One entry rather than one per vendor:
a list of vendors goes stale and a protocol does not. It takes a base URL —
`https://api.deepseek.com`, `http://localhost:11434/v1`,
`http://localhost:8000/v1`.

**Nothing extra has to be installed.** `fastmdxplora.agent` ships with the
package; `pip install "fastmdxplora[agent]"` installs no additional
dependencies. The Agent talks to a model over the standard library, with no
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
on the internet. What is recorded is the provider and the model and nothing
else.

---

## The three modes

```bash
fastmdx agent "..." --assisted        # the default
fastmdx agent "..." --autonomous --budget-hours 40
fastmdx agent "..." --unvalidated
```

They are mutually exclusive, and each writes itself into the Config as
`agent: <mode>`.

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

**When it refuses, setup's output is kept.** It cost minutes and it is worth
having — a shorter study reuses it through `simulation.setup_from`, and the
particle count is what made the refusal possible. The message names the
estimate and the budget, because "too expensive" is usually answered by a
shorter run rather than a larger allowance, and you cannot choose without the
number.

Two other ways it stops: a setup that records no particle count, because
running on would spend an unknown amount — the one thing an unattended run must
not do; and an unmeasured machine, because no calibration means no ceiling. See
[Production runs and GPUs](production.md#knowing-how-long-before-committing-the-card)
for how to measure one.

### `unvalidated` — mark the work as unchecked

`unvalidated` is a **marking on the work**, not a bypass of the validator. It
says that a phase went outside the schema and nothing checked *the method*. The
study is still recorded, still reproducible, and still has a Config — what it
does not have is anything that checked the science.

The Config is validated exactly as in any other mode. A study asking for a
setting that does not exist is refused here too.

> **What it does today.** The mode is recorded in the Config and travels into
> the run's records. The behaviour it is meant to unlock — the Agent writing
> code of its own, outside the schema — is **specified and not yet built**, and
> `fastmdx agent --unvalidated` says so when you run it. The figure-stamping
> that is meant to go with the mode is **also not yet applied**, and the
> command's own message currently claims otherwise. Use `unvalidated` to record
> intent; do not rely on it to mark anything for you.

---

## Which phases were checked

`agent` sits at the study level and in every phase. The study level is the
answer for the whole thing; a phase sets its own where it differs.

```yaml
agent: assisted          # a model drafted the study
analysis:
  agent: unvalidated     # and the analysis went outside the schema
```

Two levels rather than one, because a single value cannot say what is true of a
real study. A simulation written by hand because the protocol matters, an
analysis explored outside the schema, a setup a model drafted — that is one
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

### Which model wrote it

```yaml
agent: assisted
agent_model: anthropic/claude-sonnet-4-5-20250929
```

`agent: assisted` says a model was involved, not which one, and six months on
that is the difference between a record and a note. "Why did this study pick
300 K" has a different answer depending on whether a frontier model or a 7B on
a laptop proposed it.

**An alias is not a version.** `claude-sonnet-4-6` names different software at
different times, because it moves when a new snapshot lands. Pin the dated
string where the record needs to identify what ran.

---

## How it gets to a valid Config

One sentence in, up to a few attempts, one Config out.

```
    request ──▶ prompt (instructions + generated schema) ──▶ model
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
                          └──▶ back to the model
```

**Structural refusals are retried; semantic ones are not.** A Config that does
not match the schema is answerable by reading it. A system that does not
determine its own protonation is not, and a model that retries it is guessing
at the question the software declined to guess at. The loop stops and returns
the refusal.

**The repair prompt withholds.** It names the offending setting and, where the
refusal registry permits, the legal set. It volunteers nothing further. A
validator that hands over the fix turns every rejection into a well-specified
task — and the rule the whole design turns on is that *the validator may say
what the schema permits, and may never say what the chemistry requires*.

**Cycles are counted and capped.** `--attempts` defaults to 3. Cheap validation
invites thrashing, and a Config that validates on the fortieth mutation
validates for reasons nobody chose. Exhausting the cap is a refusal, not a
fall-through to whatever last nearly worked.

`--phases` chooses which parts of the Config it writes; the default is
`setup,simulation`.

```bash
fastmdx agent "..." --phases setup,simulation,analysis --attempts 5
```

---

## The three channels

### From the CLI

```bash
fastmdx agent set                              # choose a model, once
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

The panel is a model picker, a box to describe the study in, a mode dropdown,
and the resulting YAML. **The attempts are shown rather than summarised** —
they are the only visible sign that anything checked the Config, and watching a
model correct itself teaches the Config language while you wait, which is the
path off the panel and onto the form.

Nothing in the GUI layer decides whether a Config is acceptable. The validator
does that, as it does for a Config written by hand.

**The key is typed in the browser, sent once, and stored server-side.** It is
never sent back: the endpoint reports which provider and model are set and
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
    max_cycles=4,
)

proposal.accepted   # True
proposal.cycles     # 2 — it took one repair
proposal.config     # the validated study, as a dict
proposal.refusal    # None here; the reason it stopped, otherwise
```

`complete` is the entire model interface: a callable taking a prompt string and
returning text.

```python
def my_model(prompt: str) -> str:
    ...
proposal = propose_config("…", complete=my_model)
```

No client object, no message array, no streaming — which is why swapping in a
local server, a mock, or a model this package has never heard of takes no
integration work. `proposal.config` is `None` unless validation accepted it.

Then run it like any Config:

```python
import fastmdxplora as fastmdx
fastmdx.FastMDXplora(config_data=proposal.config, output_dir="runs/study").explore()
```

---

## What the Agent will not do

- **It does not run simulations**, except under `--autonomous`, which runs the
  Config it just wrote and stops at the budget.
- **It does not hold a conversation.** Each request is independent: the repair
  loop is automatic, but there is no history and no "make it 20 ns instead".
  Refine by writing a better sentence, or by editing the Config it gave you.
- **It does not decide chemistry the software declined to decide.** A refusal
  that needs a scientific judgement stops the loop rather than being guessed
  around.
- **It does not filter your topic.** There are no content refusals. Every
  refusal you will see from it is a schema, chemistry or environment refusal
  from the ordinary registry.
- **It does not see your key in anything it writes.**

### What is not built yet

Two things are specified and incomplete, and it is better to know than to find
out:

- **`unvalidated` is recorded, not enforced.** The Agent cannot yet write code
  of its own, and figures from an unchecked phase are not yet stamped.
- **The GUI's "autonomous" option drafts but does not run.** The panel has no
  budget field, and running an Agent-written study without a person reading it
  first is a command-line workflow today:
  `fastmdx agent "…" --autonomous --budget-hours N`.

---

## See also

- **[The FastMDXplora Config](config.md)** — what it is writing
- **[FastMDXplora refusals](refusals.md)** — the vocabulary it is answering to
- **[The FastMDXplora Manifest](manifest.md)** — where the provenance lands
- **[Production runs and GPUs](production.md)** — budgets, calibration and campaigns
- **[How FastMDXplora is validated](validation.md)** — how well a model actually does at this, measured rather than assumed
