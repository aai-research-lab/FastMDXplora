# The FastMDXplora Manifest

A [Config](config.md) says what a study is. The **Manifest** says what
happened.

`manifest.json`, at the root of every run directory, records every phase that
ran, every artifact it produced, every setting it used, and the exact software
stack it ran on. It is what makes a finished run readable by somebody who was
not there — including you, a year later.

```bash
cat runs/study/manifest.json
```

The two records are complements, not duplicates:

| | |
|---|---|
| `resolved_config.yml` | **What was asked for.** A valid Config. Feed it back to `--config` to run the study again |
| `manifest.json` | **What happened.** Which phases ran, what they produced, what refused, and on what |

---

## What is in it

```json
{
  "tool": "FastMDXplora",
  "version": "2.5.6",
  "source":      { "commit": "a1b2c3d4e5f6", "dirty": false, "branch": "main" },
  "environment": { "openmm": "8.1.2", "mdtraj": "1.10.0", "rdkit": "2024.03.5",
                   "openmmplumed": null, "python": "3.11.9", "platform": "linux" },
  "doi": "10.1002/jcc.70350",
  "citation": "…",
  "system": "3PTB",
  "output_dir": "/scratch/aaina/runs/trypsin",
  "phases": [ … ],
  "options": { "setup": {"ph": 6.5}, "simulation": {"duration_ns": 100.0} },
  "agent":   { "study": "assisted", "phases": {…}, "checked": {…}, "departures": {…} }
}
```

### Top level

| Key | What it is |
|---|---|
| `tool` | Always `"FastMDXplora"` |
| `version` | The version of the session that wrote the file |
| `source` | Commit, dirty flag and branch — **only for a run from a source checkout**; `null` for an installed copy |
| `environment` | The result-bearing packages that were loaded while the run happened |
| `doi`, `citation` | How to cite the software |
| `system` | The system input, as it was given |
| `output_dir` | Absolute path of the run directory, on the machine it ran on |
| `phases` | One record per phase — see below |
| `options` | The merged per-phase settings the run used |
| `agent` | The [Agent](agent.md) modes, where any were set |
| `versions_seen`, `version_note` | Present only where phases were produced by more than one version |

### A phase record

```json
{
  "name": "setup",
  "status": "ok",
  "output_dir": "/scratch/aaina/runs/trypsin/setup",
  "started_at": "2026-09-15T09:14:02.118374+00:00",
  "finished_at": "2026-09-15T09:16:48.902551+00:00",
  "message": "Phase 'setup' completed.",
  "artifacts": ["input.pdb", "prepared.pdb", "solvated.pdb", "topology.pdb",
                "system.xml", "state.xml", "setup_parameters.json"],
  "produced_by": { "version": "2.5.6", "host": "gpu-node-07",
                   "environment": { "openmm": "8.1.2", "python": "3.11.9" } }
}
```

`status` is `ok`, `skipped` or `error`. An errored phase also carries a
`refusal` object — the same structured record an in-process caller would have
received:

```json
"refusal": {
  "code": "setup.chemistry.protonation_undetermined",
  "kind": "semantic",
  "disclosure": "nothing",
  "retryable": false,
  "details": {"resname": "LIG", "ph": 7.4, "margin": 1.0}
}
```

The classification travels with the record. A reader a year from now does not
need this version of this software to know that a refusal was environmental and
worth retrying. See [FastMDXplora refusals](refusals.md).

`artifacts` are paths relative to that phase's directory.

---

## Why `environment` is there

The listed packages are read from what was **actually loaded during the run**,
not from what could have been imported.

| Value | Means |
|---|---|
| `"8.1.2"` | Loaded, at that version |
| `"loaded"` | Used, version could not be determined |
| `null` | **This run did not use it** |

Those packages decide numbers. The same study, the same settings and the same
structure produced an interaction table with 292 rows on one machine and 73 on
another; the difference was in the stack, and without this record it could not
have been explained.

`source` exists for the same reason at a finer grain. The version string is
written when the package is installed, so an editable install carries whatever
it was at that moment — a study of ours came back stamped `2.3.0` for a run that
used a feature `2.3.0` did not have. A checkout records its commit and whether
the tree was dirty. An installed copy records nothing under `source`, because
the distribution was built from a tag and the version is the whole answer.

---

## One run, several sessions

A Manifest is **merged, not overwritten**. Running one phase at a time:

```bash
fastmdx setup   --system 3PTB --output runs/trypsin
fastmdx simulate --output runs/trypsin
fastmdx analyze  --output runs/trypsin
```

leaves one `manifest.json` holding all three phases, each recording its own
`produced_by`. Phase records are keyed by name, so a re-run replaces that phase
in place and keeps the rest, in first-seen order.

Where phases came from different versions, the top-level `version` is the
session that wrote the file, `versions_seen` lists them all, and a
`version_note` says so.

The Manifest is written at the end of a session. A session that crashes partway
through a phase leaves no Manifest for that session — but the phase's own
record (`setup_parameters.json`, `simulation_parameters.json`) *is* written
before the failure is re-raised, deliberately, so the parameters that produced
the failure are not lost with it.

---

## The other records

`manifest.json` is the index. Four other files record what one phase or one
measure actually did, including the defaults it took — which is also what
`resolved_config.yml` is meant to carry and
[does not carry yet](config.md#reproducing-a-run).

| File | What it records |
|---|---|
| `setup/setup_parameters.json` | Every resolved setup parameter, the input structure's provenance, the force field that was chosen, and the solvated atom count |
| `simulation/simulation_parameters.json` | Every resolved simulation parameter, the platform actually used, the pressure used, frames written, and the length actually reached |
| `analysis/analysis_manifest.json` | The plan, the load settings, frame/atom/residue counts, and a result record per analysis |
| `analysis/<name>/options.json` | One measure's selection, every option, its findings, and **the format of the `.dat` file beside it** |

**The input structure's checksum lives in `setup/setup_parameters.json`**,
under `input.structure`, not in `manifest.json`:

```json
"input": {
  "system": "3PTB",
  "form": "pdb_id",
  "structure": {
    "form": "pdb_id", "given": "3PTB",
    "sha256": "…", "bytes": 148223,
    "retrieved_at": "2026-09-15T09:14:04+00:00",
    "url": "https://files.rcsb.org/download/3PTB.pdb",
    "entry": "3PTB", "deposited": "1982-07-14"
  }
}
```

A report saying "PDB 3PTB" is checkable against the exact bytes that entered
the run.

`options.json` separates **what was set** from **what was worked out** — the
`options` block and the `findings` block — so a cutoff you chose and a cutoff
read from this run's own first minimum in g(r) are not confusable. Read the
`data_format` field rather than guessing from the `.dat` extension.

### A campaign

A study of more than one run writes a Manifest per run under `runs/<id>/`, plus
two files at the campaign root:

| File | What it records |
|---|---|
| `batch_manifest.json` | The campaign: `n_runs`, the `execution` block, every system, the sweep, and a result record per run |
| `members.json` | What each member concluded, and whether the spread between members is an error or a result |

An umbrella study also writes `pmf.json`. See
[Reading the results](results.md#comparing-runs).

---

## Reading a Manifest

It is JSON. Most of the time that is all you need:

```python
import json
from pathlib import Path

manifest = json.loads(Path("runs/study/manifest.json").read_text())

manifest["version"]
manifest["environment"]["openmm"]
[(p["name"], p["status"]) for p in manifest["phases"]]

failed = [p for p in manifest["phases"] if p["status"] == "error"]
for p in failed:
    print(p["name"], p["refusal"]["code"], "-", p["message"])
```

There is one helper worth knowing about, because it handles a case a naive
reader gets wrong:

```python
from pathlib import Path
from fastmdxplora.validation.cross_tool import load_manifest, find_artifact

run = Path("runs/study")               # both take a Path, not a string
manifest = load_manifest(run)
traj = find_artifact(run, manifest, "production.dcd")
```

**A Manifest written on a cluster records that machine's absolute paths.**
`find_artifact` re-roots an absolute path under the run directory you actually
have, which is what you want after an `rsync` home. Reading `output_dir`
literally is what fails.

In-process, the same information comes back from `explore()` as
`RunResult`/`PhaseResult` objects — see
[The FastMDXplora API](api.md#what-comes-back).

---

## What a Manifest is not

**It is not a resume point.** Nothing in FastMDXplora reads a Manifest to
continue a run. What resumes is a phase boundary — a finished `setup/` or
`simulation/` in a directory is reused rather than redone, and `--include`
picks up from the phase that stopped. Within the simulation phase,
`simulation.resume_from` takes an OpenMM checkpoint, not a Manifest. See
[Production runs and GPUs](production.md#when-it-stops-early).

**It is not the Config.** `manifest["options"]` holds the merged per-phase
settings, but not `systems`, `output`, `include`/`exclude` or the sweep. To
re-run a study, use `resolved_config.yml`.

**It does not checksum the Config.** The input *structure* is checksummed, in
the setup record above; the Config is not.

---

## Where the Manifest is read

It is not a file that only people open. The Manifest is how parts of
FastMDXplora find things:

- **The report** decides which sections to write, and how to word them, from
  which phases ran.
- **The GUI** reads it to populate the Overview and Report pages for a finished
  run.
- **A campaign's comparison** reads `batch_manifest.json` to build
  `comparison/` and `members.json`.
- **Cross-tool validation** locates a run's trajectory and topology through it,
  because the provenance index is the contract rather than guessed filenames.
- **`project_bundle.zip`** has `manifest.json` and `resolved_config.yml`
  appended to it after the report phase finishes, so the bundle you send
  somebody carries the record of what produced it.

---

## See also

- **[Reading the results](results.md)** — the run directory, and what each number is worth
- **[The FastMDXplora Config](config.md)** — the other half of the pair
- **[FastMDXplora refusals](refusals.md)** — what a `refusal` block means
