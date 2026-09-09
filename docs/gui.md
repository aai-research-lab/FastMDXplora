# The FastMDXplora GUI

```bash
fastmdx gui
```

That is the whole of it. A browser tab opens, and everything FastMDXplora does
is in it: designing a run, starting it, watching it happen, and reading the
results.

The GUI is not a cut-down version of the command line. It offers **every
setting the software has** — all 263 analysis options across 23 analyses, and
all 108 phase and top-level settings — because the form is generated from the
same declaration the CLI and the config file are built from, rather than
written by hand. Adding a setting to the schema puts a control in the GUI;
nothing has to be kept in step.

So any system FastMDXplora can study can be built here: a protein on its own,
a protein with a ligand, a membrane protein, an umbrella or metadynamics or
steered study, a trajectory from another engine, or a sweep across many
systems at once. And any config file it can run, the GUI can write. Anything
you can do in a config file you can do here, and anything you set here you can
take away as a config file — checked by the same validator before you see it,
so a configuration that would fail on a cluster fails in the GUI instead,
while you are still looking at the form that produced it.

---

## Designing a run

One page builds any run, and it asks three questions.

**What have you got?** A structure, a trajectory, or a config file you already
have. Choosing a trajectory greys out setup and simulation, because there is
nothing to prepare or run.

**What should happen?** Which phases, and which analyses. The analyses are
grouped — shape and size, flexibility, conformations, folding, the ligand,
protein and ligand together — and each one explains what it measures, taken
from the analysis itself rather than written out a second time.

**Anything to change?** Every setting, at the value it will actually use.
Nothing is hidden behind an "advanced" panel: a setting you cannot see is a
setting you cannot check.

They are grouped by what they decide rather than listed in the order they were
declared — *the structure*, *the ligand*, *the membrane*, *solvent, ions and
the box*, *the force field*, *how forces are computed* for setup; *how long it
runs*, *where it starts*, *conditions*, *the integrator*, *enhanced sampling*,
*restraints*, *where it runs*, *what gets written*, *watching it run* for
simulation. Each group says what it is about. `fastmdx explore --help` reads
in the same sections, from the same declaration, so moving between the
terminal and the GUI is not learning a second arrangement.

Every path field has a **Browse** button, so there is no typing a path and
finding out later that it was wrong.

A setting that is a *block* rather than a value — `umbrella`, `steered`,
`metadynamics` — gets a box you write the block into, one setting per line,
exactly as it appears in a config file. An example of the right shape sits in
the box until you type. These used to be single-line fields, which meant the
GUI was the one interface where enhanced sampling could not be set up.

`plumed` is the exception, because it is one script with an on-switch rather
than a mapping of settings, and writing a working `.dat` file inside YAML
block-scalar indentation is where a stray tab changes a PLUMED input. It gets
a control of its own: the switch, a path with the same **Browse** button every
other path field has, and a place to write the PLUMED input directly, exactly
as it would appear in the file. Text written there wins over a path — the rule
is stated on the control the moment both are filled — and the first content to
arrive turns the switch on.

### Taking the config with you

Four buttons at the bottom of the page:

- **Download config** writes the YAML for exactly what is on screen. Take it
  to a cluster and `fastmdx explore --config study.yml` runs the same thing.
- **Copy the command** puts the equivalent `fastmdx explore` invocation on the
  clipboard — only the settings that differ from their defaults, so it stays
  readable. A study the command line cannot express — a sweep, a PLUMED
  script — is refused by name rather than translated with pieces missing,
  and the config file stays its language.
- **Download a script** writes the same study as a runnable Python file, the
  shape the [API page](api.md) documents.
- **Run** starts it here.

One study, three languages, all from the same declaration — and round-trip
tests hold the command to reparsing into the same configuration it came from.

And in the other direction: choose *A config I already have* and the GUI will
**check it** — syntax and settings, without running anything — **run it
as-is**, or **open it for editing**, which fills the whole page in from the
file. It never rewrites the file you gave it.

---

## Watching it happen

Once a run starts, the page becomes a live view of it.

**Progress** through the phases, showing the stages the run will actually
reach — an analysis-only run shows one stage, not seven greyed-out ones.

**Telemetry**: temperature, energy, density and box volume as they are
written, so a system going wrong is visible while it is going wrong rather
than afterwards.

**The molecule, in 3D.** The structure is drawn as it is simulated, and once
frames exist the trajectory plays back. For a protein-ligand system the ligand
and its binding pocket are picked out, so what the ligand is doing is visible
without loading anything into another program.

---

## Reading the results

Point the GUI at a finished run and it opens on it:

```bash
fastmdx gui --output runs/my_study
```

Every figure, every table, the report, and the trajectory to play back,
without re-running anything.

This is also how you watch a run happening somewhere else. Keep a cluster's
output directory in sync and the GUI reads it as it fills:

```bash
# on your laptop, in one terminal
rsync -az --delete user@cluster:/scratch/$USER/runs/my_run/ ~/runs/my_run/

# in another
fastmdx gui --output ~/runs/my_run
```

Re-run the `rsync` as often as you like; the GUI picks up whatever is there.

---

## Where it runs, and who can reach it

The GUI binds to `127.0.0.1` by default, so it is reachable from your machine
and nowhere else, and there is no authentication because on that address there
is no network exposure to authenticate against.

`--dashboard-host` will bind it elsewhere, and that sentence stops being true
the moment you use it. **There is still no authentication.** What the flag does
instead is refuse the endpoints that need to trust the caller: starting and
stopping a run, opening a local folder, walking the server's filesystem for the
folder picker, and reading a config file the caller names. Those all return 403
on a non-loopback bind. What remains reachable is the run's own artifacts and
its live status, which is what a colleague watching a job needs.

That is a narrower exposure, not a safe one. **Prefer the SSH tunnel below to
binding a public address**, which is the arrangement the rest of this page
assumes.

To use it against a machine you have SSH access to, forward the port rather
than binding to a public address:

```bash
ssh -L 8765:127.0.0.1:8765 user@remote
```

Then open `http://127.0.0.1:8765` locally.

| Option | What it does | Default |
|---|---|---|
| `--dashboard-port` | Which port to serve on | `8765` |
| `--no-browser` | Do not open a tab; print the URL instead | opens a tab |
| `--dashboard-refresh-seconds` | How often the page re-reads the run | `2` |
| `--dashboard-ligand-resname` | Force which residue is the ligand | auto-detected |
| `--dashboard-binding-pocket-cutoff-A` | How near counts as the pocket | `5.0` |
| `--dashboard-max-playback-frames` | Frames to load for playback | `200` |

---

## When there is nothing to show

A page with no run behind it says so rather than drawing an empty chart. If
setup has not finished there is no structure to draw; if production has not
started there are no frames to play. Each says which, and what would produce
it.

That is deliberate. An empty axis and a missing measurement look the same on
screen, and only one of them means something is wrong.

---

## The API behind it

The GUI is a thin layer over a JSON API on the same port, which is worth
knowing if you want to drive it from a script:

| Endpoint | What it returns |
|---|---|
| `/api/app-state` | The current run: phases, progress, artifacts |
| `/api/schema` | Every setting, its default, its explanation |
| `/api/browse` | Directory listing for the file picker |
| `/api/check-config` | Validation for a config file, without running it |
| `/api/load-config` | A config file as form state |
| `/api/run` | Start a run from form state |
| `/api/run-config` | Start a run from a config file, unmodified |
| `/api/telemetry` | Live metrics from a running simulation |
| `/api/frames` | Coordinates for the viewer |
| `/api/open-output` | Open the run's directory in the file manager |

`/api/schema` is the one worth knowing about: it is the same declaration the
CLI builds its flags from, so anything reading it stays in step with the
software automatically.
