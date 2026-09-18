# The FastMDXplora GUI

```bash
fastmdx gui
```

A browser tab opens, and everything FastMDXplora does is in it: designing a
study, starting it, watching it happen, and reading the results.

The GUI is not a cut-down version of the command line. It offers **every
setting the software has** — all 304 analysis options across 27 analyses, and
all 117 phase and top-level settings — because the form is generated from the
same declaration the CLI and the [Config](config.md) are built from rather than
written by hand. Adding a setting to the schema puts a control in the GUI;
nothing has to be kept in step.

Those three counts are checked against the software on every release, because a
count in a document nobody recomputes is a claim that decays — and this one is
load-bearing: it is the sentence that says the GUI is not a cut-down command
line.

So any system FastMDXplora can study can be built here: a protein on its own, a
protein with a ligand, a membrane protein, an umbrella or metadynamics or
steered study, or a trajectory from another engine. Whatever it writes is
checked by the same validator before you see it, so a study that would fail on
a cluster fails in the GUI instead, while you are still looking at the form
that produced it.

**The page builds one system at a time.** A campaign across several systems and
a `sweep` across settings are written in a [Config](config.md) by hand — the
form has no control for either, and neither survives a round trip through
**Open for editing**. An umbrella study is the exception and does work here: it
is one system, and the windows are expanded from the block.

It is a local web interface, not a web app. The server runs on your machine, as
you. Nothing is uploaded anywhere.

---

## The pages

| | |
|---|---|
| **Agent** | [The FastMDXplora Agent](agent.md): a conversation that writes, edits, runs and reads a study |
| **Builder** | The [Config](config.md) builder: four questions, the phases as tiles |
| **Overview** | A live run: health first, then what the sidebar has no room for: the live charts, the structure as it is written, and once there are results, the recorded numbers |
| **Viewer** | The molecule in 3D, live while running and played back afterwards; follows the run by default |
| **Analysis** | The figures and tables, grouped |
| **Report** | The report itself, rendered as a document, with downloads for what was produced and a notice for what could not be |
| **Files** | Everything the run wrote, grouped by phase |

Three things are on every page. The **sidebar**: the study, its stage and
progress, the run's controls, the nav, a *Cite* line, and at its foot the
settings trigger, which shows the Agent's engine and mode and opens a popup
with the theme, the Agent's summary, display preferences and the links. The
**side panel**: a *Log* tab, which is what the command line prints, sorted so
a refusal is a red-edged block and the explain text a quiet one, with filters
and a scroll-to-newest toggle; and a *Files* tab, which opens any of the
run's files in place. And two **seams** between the columns that drag, with a
double-click to reset; the panel collapses to a tab on the right edge. The
centre keeps a reading width, except the Viewer, which fills.

Three schemes, from the settings popup: Graphite, Ink and Paper. Green done,
amber qualified and red refused mean the same in all three.

---

## Designing a study

One page builds any run, and it asks three questions.

**What have you got?** A structure, a trajectory, or a Config you already have.
Choosing a trajectory greys out setup and simulation, because there is nothing
to prepare or run. Every path field has a **Browse** button, so there is no
typing a path and finding out later that it was wrong.

**What should happen?** Which phases, and which analyses. The analyses are
grouped — shape and size, flexibility, conformations, folding, the ligand,
protein and ligand together — and each explains what it measures, taken from
the analysis itself rather than written out a second time.

**Anything to change?** Every setting, at the value it will actually use.
Nothing is hidden behind an "advanced" panel: a setting you cannot see is a
setting you cannot check.

They are grouped by what they decide rather than listed in the order they were
declared — *the structure*, *the ligand*, *the membrane*, *solvent, ions and
the box*, *the force field*, *how forces are computed* for setup; *how long it
runs*, *where it starts*, *conditions*, *the integrator*, *enhanced sampling*,
*restraints*, *where it runs*, *what gets written*, *watching it run* for
simulation. `fastmdx explore --help` reads in the same sections, from the same
declaration, so moving between the terminal and the GUI is not learning a
second arrangement.

A setting that is a **block** rather than a value — `umbrella`, `steered`,
`metadynamics` — gets a box you write the block into, one setting per line,
exactly as it appears in a Config. An example of the right shape sits in the
box until you type.

`plumed` is the exception, because it is one script with an on-switch rather
than a mapping of settings, and writing a working `.dat` file inside YAML
block-scalar indentation is where a stray tab changes a PLUMED input. It gets a
control of its own: the switch, a path with the same **Browse** button, and a
place to write the PLUMED input directly. Text written there wins over a path —
the rule is stated on the control the moment both are filled — and the first
content to arrive turns the switch on.

### Taking the Config with you

Four buttons at the bottom of the page:

- **Download config** writes the YAML for exactly what is on screen, as
  `fastmdxplora.yml`. Take it to a cluster and `fastmdx explore --config` runs
  the same study.
- **Copy the command** puts the equivalent `fastmdx explore` invocation on the
  clipboard — only the settings that differ from their defaults, so it stays
  readable. A study the command line cannot express — more than one system, a
  PLUMED script — is **refused by name rather than translated with pieces
  missing**, and the Config stays its language.
- **Download a script** writes the same study as a runnable Python file, in the
  shape [the API page](api.md) documents.
- **Run** starts it here.

One study, three languages, all from the same declaration — and round-trip
tests hold the command to reparsing into the same Config it came from.

And in the other direction: choose **A config I already have** and the GUI will
**check it** (syntax and every setting, without running anything), **run it
as-is**, or **open it for editing**. It never rewrites the file you gave it.

**Open for editing** fills the page in from the file, but only with what the
page can hold: the first system, `output`, `include`, the four phase blocks and
the per-analysis options. A `sweep`, an `execution` block, any system after the
first, and the `agent`/`agent_model` provenance are dropped, so re-saving a
campaign from the form gives you a single-system study. **Run it as-is** leaves
the file untouched and is the route for those.

When you press **Run**, the Config is written into the output directory as
`exploration.yml` before anything starts, so the run is repeatable from its own
output. **Run as-is** writes nothing and runs the file you named, unmodified.

---

## Watching it happen

Once a run starts, the page becomes a live view of it.

**Progress** through the phases, showing the stages the run will actually reach
— an analysis-only run shows one stage, not seven greyed-out ones.

**Telemetry**: temperature, energy, density and box volume as they are written,
so a system going wrong is visible while it is going wrong rather than
afterwards.

**The molecule, in 3D.** The structure is drawn as it is simulated, and once
frames exist the trajectory plays back. For a protein–ligand system the ligand
and its binding pocket are picked out, so what the ligand is doing is visible
without loading anything into another program.

The page polls the run directory every three seconds by default. The data comes
from files the running simulation writes (`simulation/live_status.json`,
`live_metrics.csv`, `live_events.log` and a capped history of live frames) —
`simulation.live_telemetry` turns those on and off, and nothing leaves the
machine.

Live frames are a read-only side channel: they never feed values back into
OpenMM, and failures there are swallowed, so visualisation cannot stop or
change a scientific simulation.

One run at a time. Starting a second returns *"A FastMDXplora workflow is
already running."*

---

## When there is nothing to show

A page with no run behind it says so rather than drawing an empty chart. If
setup has not finished there is no structure to draw; if production has not
started there are no frames to play. Each says which, and what would produce
it.

That is deliberate. An empty axis and a missing measurement look the same on
screen, and only one of them means something is wrong.

---

## Reading the results

Point the GUI at a finished run and it opens on it:

```bash
fastmdx gui --output runs/my_study
```

Every figure, every table, the report, and the trajectory to play back, without
re-running anything. It reads a finished run as happily as it drives a live
one.

This is also how you watch a run happening somewhere else. Keep a cluster's
output directory in sync and the GUI reads it as it fills:

```bash
# on your laptop, in one terminal
rsync -az --delete user@cluster:/scratch/$USER/runs/my_run/ ~/runs/my_run/

# in another
fastmdx gui --output ~/runs/my_run
```

Re-run the `rsync` as often as you like; the GUI picks up whatever is there.

**There is no comparison view.** A campaign's cross-run comparison is written
to `comparison/` at the campaign root, by the batch layer once every run has
finished, and read there — see
[Reading the results](results.md#comparing-runs).

---

## `fastmdx gui` in full

| Flag | What it does | Default |
|---|---|---|
| `--output DIR` | The run to watch or read. Without it, the GUI opens in "design a new study" mode | current directory, home mode |
| `--host` | What to bind to | `127.0.0.1` |
| `--port` | Which port. If it is busy, the next free one is used and reported | `8765` |
| `--no-browser` | Print the URL instead of opening a tab | opens a tab |
| `--ligand-resname NAME` | Which residue the viewer treats as the ligand | auto-detected |
| `--binding-pocket-cutoff-A X` | How near counts as the pocket | `5.0` |

**That is the whole of it.** The dashboard flags — `--dashboard-host`,
`--dashboard-port`, `--dashboard-refresh-seconds`,
`--dashboard-max-playback-frames` and the rest — belong to `explore` and the
four phase commands, which can start the same server alongside a run:

```bash
fastmdx explore --config study.yml --dashboard --dashboard-stop-on-complete
```

See [The FastMDXplora CLI](cli.md#watching-a-run-from-the-same-command).

Four other ways in:

```bash
fastmdx                 # no subcommand at all: the GUI on the current directory
fastmdx gui             # design a study
fastmdx agent           # the same server, opened at the Agent panel
fastmdx explore … --dashboard
```

---

## Who can reach it

**There is no login anywhere, so the bind address is the whole of the trust
model.**

By default that is `127.0.0.1`, the loopback interface. A browser tab on the
same machine can reach it; nothing else on the network can.

### Binding somewhere else

`--host 0.0.0.0` opens it to the network, and FastMDXplora reduces what it will
do. These return **403** off loopback:

| Refused off loopback | Why |
|---|---|
| `/api/browse`, `/api/inspect-directory` | They walk the filesystem for the folder picker |
| `/api/load-config`, `/api/check-config` | They read a file the caller names and quote the line a parse error came from, which is a file-content oracle. A planted token and an AWS key were both recovered this way |
| `/api/run`, `/api/run-config`, `/api/explore/start`, `/api/explore/stop`, `/api/explore/validate` | They start and stop work on this machine |
| `/api/open-output` | It opens a folder on the machine running the server |
| `/api/agent/model`, `/api/agent/propose` | One stores an API key, the other spends it |

What remains is the live view of the run and its artifacts, which is what a
colleague watching a job needs. That is a **narrower** exposure, not a safe one,
and a warning says so at startup.

### Prefer a tunnel

It needs no flag and exposes nothing:

```bash
ssh -L 8765:localhost:8765 you@labbox
```

Then open `http://localhost:8765` at home. SSH carries the traffic, your SSH
key is the authentication, and the GUI still believes it is serving a local
browser tab. It chains through a jump host, which covers the cluster case.

### On a shared machine, loopback is not private

This is the case the gate above does not cover, and it is worth reading twice.

On your own workstation, `127.0.0.1` means you. On a machine where other people
hold shell accounts — a cluster login node, a shared server — it means *every
logged-in user*. Any of them can `curl http://127.0.0.1:8765`, and nothing is
disabled, because the bind address is loopback. The interface runs as you: it
reads what you can read and submits under your account.

So: **do not run the GUI on a login node.** Take an interactive job, run it on
the compute node, and tunnel through the login node to reach it — the same
pattern as Jupyter on HPC:

```bash
salloc --gres=gpu:1 --time=4:00:00
# on the compute node:
fastmdx gui --output runs/my-study
# from your laptop, through the login node:
ssh -J you@login.cluster -L 8765:localhost:8765 you@gpu-node-07
```

A workstation you are the only user of has none of this problem. A machine
where `who` lists other people does.

---

## The API behind it

The GUI is a thin layer over a JSON API on the same port, which is worth
knowing if you want to drive it from a script. Requests are capped at 1 MB.

**Reading**

| Endpoint | What it returns |
|---|---|
| `GET /api/app-state` | The current run: mode, status, active run, log path, exit code, whether a run can be launched |
| `GET /api/schema` | Every setting, its type, control, default, help and choices |
| `GET /api/status` | Phase and stage status from the telemetry files |
| `GET /api/metrics` | Metric rows from `live_metrics.csv` |
| `GET /api/events` | Recent lines from `live_events.log` |
| `GET /api/results`, `/api/analyses` | The summary, system info, phases, analyses and plots |
| `GET /api/artifacts`, `/api/files` | Artifact records for the Report page |
| `GET /api/structure-info`, `/api/ligands` | Atom, residue, chain and ligand counts |
| `GET /api/playback-info`, `/api/live-frame-index`, `/api/live-coordinates` | The viewer's frames |
| `GET /api/protein-preview` | The cached preview image |
| `GET /artifacts/<path>` | Any file under the run root, `?download=1` to attach |
| `GET /structure/topology.pdb`, `/structure/live-frame.pdb`, `/structure/playback.pdb` | Structures for the viewer |
| `GET /analysis-figures-svg.zip` | Every analysis figure, zipped |

**Writing**

| Endpoint | What it does |
|---|---|
| `POST /api/config` | Build, validate and return the YAML for what is on screen, plus the equivalent CLI command and Python script. `"full": true` in the body restates every default |
| `POST /api/check-config` | Validate a Config file without running it |
| `POST /api/load-config` | Read a Config into form state. Never writes the file |
| `POST /api/run` | Start a run from form state |
| `POST /api/run-config` | Start a run from a Config file, unmodified |
| `POST /api/explore/stop` | Terminate the running workflow |
| `POST /api/agent/model` | Read or set the [Agent](agent.md)'s model choice. Never returns the key |
| `POST /api/agent/propose` | A sentence to a validated Config |

`GET /api/schema` is the one worth knowing about: it is the same declaration
the CLI builds its flags from, so anything reading it stays in step with the
software automatically.

---

## Not the same thing: `report/dashboard.html`

The report phase writes a **static** `dashboard.html` into the run directory.
It opens in a browser tab with no server running, and travels inside
`project_bundle.zip`. It is the thing to send somebody. `fastmdx gui` is the
live interface, and they are not the same file.
