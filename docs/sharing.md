# Sharing a study

A finished study can be handed to someone else as one file, and opened by their
FastMDXplora as the study it was: the same pages, the same frames, the same
numbers, with every file checked against what was packed. Put on Zenodo, it
has a DOI, and a DOI is all anyone needs to open it.

```bash
fastmdx report --output runs/3ptb --share 3ptb.zip --share-author "Adekunle Aina"
fastmdx report --output runs/3ptb --share 3ptb.zip --share-author "Adekunle Aina" --share-to zenodo
fastmdx gui --open 10.5281/zenodo.1234567
```

The first packs the study as its pages show it. The second also makes a
draft of it on Zenodo, for you to read and publish. The third opens a shared
study from its DOI; a Zenodo address or the archive's file does as well.

The file is a zip in a published format, [RO-Crate](https://www.researchobject.org/ro-crate/)
1.2: the study's files as they are laid out in its folder, and one more,
`ro-crate-metadata.json`, which says what the archive is and what each file in
it is, with its size and its SHA-256. A repository, a library or a program that
knows nothing of FastMDXplora can read that list. FastMDXplora reads it to
check the archive and to open it. The rules a FastMDXplora study keeps on top
of RO-Crate's own are a *profile*, given in full below.

---

## Making one

`--share FILE` on `fastmdx report` packs the study in `--output` as it stands.
It does not write the report again: a study whose report was never written is
shared without one. A study still running, one that
stopped short, or a study of several runs (share each run's folder) is
refused (`environment.share.not_finished`); share it once it has finished,
after `fastmdx resume` where it stopped.

| Flag | What it does |
|---|---|
| `--share FILE` | Writes the archive to `FILE`. |
| `--share-all` | Packs every file of the study, not only what its pages read (below). |
| `--share-license ID` | The licence the archive is shared under, as an SPDX identifier; `CC-BY-4.0` unless given. |
| `--share-author NAME` | An author of the archive, given once for each; `"NAME;ORCID"` adds an ORCID iD (`"Adekunle Aina;0000-0002-8215-7452"`). The report's `--author` unless given; one is needed. |
| `--share-to zenodo` | Also makes a draft of the archive on Zenodo (below); `zenodo-sandbox` on Zenodo's sandbox, for trying it out. |

The same study packed twice with the same flags on the same day gives the same
bytes: the files in name order, each with the time of the study's last phase.

### What it holds

By default an archive holds what the study's pages read once it has finished,
which is what a reader of it needs: the study can be looked at, every number
followed to the file it came from, and its Config run again.

| | Default | `--share-all` adds |
|---|---|---|
| The Config and the Manifest | `resolved_config.yml`, `manifest.json`, each phase's settings record | |
| What setup made | the deposited structure, the prepared protein, the system as simulated (`setup/topology.pdb`), each ligand's chemistry | the solvated system, the force field as OpenMM built it, the starting state |
| The trajectory | the production trajectory (or a joined study's whole trajectory) and the topology it is read with | each segment of a joined study |
| What the run recorded | energies, the live record the Overview plots, the cost, how the run ended | the final and minimised states, the checkpoint, the logs |
| The analyses | every analysis's numbers, options and figures (PNG) | the figures as SVG, the clusters' medoids as structures |
| The report | `report.md` and the pictures it shows | the PDF, the slides and the standalone page |
| Kept from the Viewer | saved views, named selections, scenes | movies |

### What is never in it

- **Conversations with the Agent** (`agent/conversations/`). They are the
  person's working notes, not the study's record.
- **The record of the process that ran it** (`.fastmdxplora_run.json`): the
  computer's name, its boot and the process number.
- **Tags and notes** (`study_tags.json`), which are the person's own.
- **What the study does not need:** the Viewer's scratch, the snapshots a
  run keeps as it goes (`live_frames/`; its last frame is kept, which the
  Viewer shows), what `--rerun` set aside (`previous/`, `superseded/`),
  earlier deposits (`deposit/`), the project bundle, which is a copy of the
  rest, and any file or folder whose name begins with a dot.

Settings of the installation itself, the AI model chosen and any key for it,
live outside the study and are never read.

### Paths

A study's records name its files where they were written:
`/home/someone/runs/3ptb/setup/ligands/BEN.sdf`. Shared as written, every
copy would carry the folder it ran in and the name of the person's home folder,
and the paths would name nothing on the reader's computer.

Every text file packed (`.json`, `.yml`, `.md`, `.csv`, `.dat`, `.log`, `.pdb`,
`.sdf`, `.svg`, `.html` and the like) is written with:

- the study's folder replaced by `__FASTMDX_STUDY__`
  (`__FASTMDX_STUDY__/setup/ligands/BEN.sdf`);
- any other path in the home folder replaced by `__FASTMDX_HOME__`
  (`__FASTMDX_HOME__/structures/x.pdb`): a file outside the study, on the
  computer that ran it;
- the computer's name replaced by `__FASTMDX_HOST__`.

The placeholders are safe in YAML, JSON and Markdown alike. Once written, each
packed text file is read again, and an archive in which the study's folder, the
home folder or the computer's name is still found is not written
(`environment.share.unscrubbed`, naming the file). Binary files (the
trajectory, figures, NumPy arrays) are packed as they are; none of their
formats records a path.

---

## Putting it on Zenodo

`--share-to zenodo` makes a **draft** on Zenodo, never a publication.
FastMDXplora:

1. makes a draft deposition and asks Zenodo to reserve its DOI;
2. writes that DOI into the archive's packing list as the archive's own
   `identifier`, so the archive names itself;
3. uploads the archive;
4. fills in the draft's description from the study's records: its title, the
   authors given, a summary from the report, the licence, keywords (the
   system, "molecular dynamics"), the software that made it
   (`isCompiledBy` FastMDXplora's repository) and the structure it started
   from (`isDerivedFrom` the PDB entry's DOI, where it is one);
5. prints the draft's address.

You open the address, read the draft, change what you want, and press
**Publish**. Until then nothing is public, and the draft can be deleted (a
share after that makes a new one).

The study is packed before Zenodo is asked anything, so a study that cannot
be shared leaves no draft behind, and the draft is recorded in the study as
soon as it exists, so a share that fails part way is finished by the next
one rather than leaving a second draft.

It needs a Zenodo token in `ZENODO_TOKEN` (`ZENODO_SANDBOX_TOKEN` for
`--share-to zenodo-sandbox`) with the **deposit:write** scope, made under
Applications in your Zenodo account's settings. FastMDXplora never
publishes. The token is read from the environment for the one command and
written nowhere.

The study keeps the record it was shared as in `shared_to.json`. Shared
again before you publish, the same draft is made again with the new
archive. Shared again after (analysed again, say), it is a draft of a new
version of the same record, which Zenodo makes only for a token that also
has the **deposit:actions** scope; the record's concept DOI always leads to
its latest version.

---

## Opening one

```bash
fastmdx gui --open 10.5281/zenodo.1234567
fastmdx gui --open https://zenodo.org/records/1234567
fastmdx gui --open ~/Downloads/3ptb.zip --open-into ~/studies
```

In the GUI, **Open a shared study** on All studies takes the same: a DOI, a
Zenodo address, or the path of a zip on this computer.

FastMDXplora:

1. **Finds the archive.** For a DOI (`10.5281/zenodo.N`, with or without
   `https://doi.org/`) or a published record's address
   (`https://zenodo.org/records/N`) it asks Zenodo for the record (a DOI
   names one version, and that version is opened), and takes the record's
   one zip. A record with none, or several, is refused
   (`environment.share.not_a_study`); download the study's and open the file.
2. **Downloads it**, up to a limit (2 GB unless `--open-most-gb` says
   otherwise; `environment.share.too_large`), and checks it against the MD5
   Zenodo recorded for it.
3. **Unpacks it into a folder of its own**, named from its title, in
   `--open-into` (else the current folder; in the GUI, where new studies
   go), and only into it: a member with an absolute path, a `..`, a link, a
   device or an encryption is refused, as is what packing never writes (the
   Agent's conversations, a process's record, a file whose name begins with
   a dot, scratch, what was set aside) and an archive that unpacks to more
   than four times the limit (`environment.share.unsafe`). A
   `ro-crate-preview.html` is never unpacked.
4. **Reads the packing list** and checks it keeps RO-Crate 1.2 and the rules
   of this profile (below) in a version it knows
   (`environment.share.not_a_study`).
5. **Checks every file**: each one listed is there, of the size listed, with
   the SHA-256 listed, and nothing is there that is not listed
   (`environment.share.unverified`, naming the file). Nothing is opened
   before every file has passed.
6. **Puts the study's own folder back**: `__FASTMDX_STUDY__` becomes the
   folder it was opened into, in the files the packing list names as holding
   it, each in its own syntax (escaped in JSON; in YAML as a value, whatever
   the folder's name holds), line endings kept. `__FASTMDX_HOME__` and
   `__FASTMDX_HOST__` are left as they are.
7. **Writes `shared_from.json`**: where it came from (the DOI, the record and
   its version, or the file), the archive's SHA-256, when it was opened and
   checked, and which files step 6 rewrote.
8. **Opens it** in the GUI.

Nothing in an archive is run. Its Config is a file like any other, run again
only by someone who chooses to. The report is rendered as every report is,
with raw HTML shown as text and links only within the study or to the web.

---

## The profile: a FastMDXplora study, version 1.0

Profile identifier: `https://w3id.org/fastmdxplora/study/1.0`.

An archive keeps this profile when everything below holds. "MUST" is a rule a
reader checks, and an archive that breaks one is not opened.

### The crate

1. It is an RO-Crate 1.2 in a zip, with `ro-crate-metadata.json` at the top.
   Its context is RO-Crate 1.2's, extended with `sha256`
   (`https://w3id.org/ro/terms/workflow-run#sha256`) and the `fmx:` terms
   (`https://w3id.org/fastmdxplora/terms#`).
2. The root dataset (`./`) MUST name this profile in `conformsTo`, and also
   names [Process Run Crate](https://w3id.org/ro/wfrun/process/0.5) 0.5, whose
   rules the phases' records keep (6 below). It MUST have a `name`,
   `datePublished`, `license` and at least one `author`, and `mainEntity` MUST
   be the Config, `resolved_config.yml`.
3. `fmx:package` on the root is `view` (the default) or `all`.
4. `fmx:placeholders` on the root lists each placeholder used, with the files
   that hold it.

### The files

5. Every file in the zip but `ro-crate-metadata.json` (and
   `ro-crate-preview.html`, where there is one) MUST be a `File` in the root's
   `hasPart`, its `@id` its path in the study, with `contentSize` in bytes and
   `sha256`. No `@id` of a file is absolute, a web address, or holds `..`.
   Each also has a `name` (as the Files page labels it), an `encodingFormat`,
   `fmx:phase` (`study`, `setup`, `simulation`, `analysis`, `report` or
   `viewer`) and `fmx:role`, one of:

   | `fmx:role` | What the file is |
   |---|---|
   | `config` | the Config the study ran, every setting filled in |
   | `manifest` | the Manifest |
   | `record` | a phase's settings, a run's record, an analysis's options |
   | `structure` | a structure: deposited, prepared, or the system as simulated |
   | `ligand` | a ligand's chemistry |
   | `system` | the force field and system, as OpenMM built it |
   | `state` | positions and velocities: starting, minimised, final |
   | `checkpoint` | a checkpoint, read only by the OpenMM build that wrote it |
   | `trajectory` | a trajectory |
   | `topology` | the topology a trajectory is read with |
   | `series` | numbers over the run: energies, the live record |
   | `analysis` | an analysis's numbers |
   | `figure` | a figure |
   | `report` | the report, its slides, its standalone page |
   | `log` | a log |
   | `view` | a saved view, a selection, a scene or a movie |

6. Each trajectory MUST name the topology it is read with
   (`fmx:readWith`), and says how many frames it holds (`fmx:frames`), how
   many atoms (`fmx:atoms`) and the time between frames in picoseconds
   (`fmx:savingIntervalPs`), where the study recorded it.

### The run

7. FastMDXplora is a `SoftwareApplication` entity with the `version` that ran
   the study and its `url`.
8. Each phase that ran is a `CreateAction`, as Process Run Crate describes:
   its `instrument` FastMDXplora, its `object` the files it read, its `result`
   the files it wrote, with its `startTime` and `endTime` from the Manifest.

### Reading a newer one

9. A reader opens an archive whose profile has the same MAJOR version as one
   it knows, ignoring terms it does not know; one of a newer MAJOR version is
   refused with the version it needs.

### An example

The packing list of the demo study, cut to one file of each kind:

```json
{
  "@context": [
    "https://w3id.org/ro/crate/1.2/context",
    {"sha256": "https://w3id.org/ro/terms/workflow-run#sha256",
     "fmx": "https://w3id.org/fastmdxplora/terms#"}
  ],
  "@graph": [
    {"@id": "ro-crate-metadata.json", "@type": "CreativeWork",
     "conformsTo": {"@id": "https://w3id.org/ro/crate/1.2"},
     "about": {"@id": "./"}},
    {"@id": "./", "@type": "Dataset",
     "name": "3PTB, trypsin with benzamidine",
     "description": "10 ns of trypsin with benzamidine bound, at 300 K, analysed and reported by FastMDXplora.",
     "datePublished": "2026-10-07",
     "license": {"@id": "https://spdx.org/licenses/CC-BY-4.0"},
     "author": [{"@id": "https://orcid.org/0000-0000-0000-0000"}],
     "conformsTo": [{"@id": "https://w3id.org/fastmdxplora/study/1.0"},
                    {"@id": "https://w3id.org/ro/wfrun/process/0.5"}],
     "mainEntity": {"@id": "resolved_config.yml"},
     "fmx:package": "view",
     "fmx:placeholders": [{"fmx:placeholder": "__FASTMDX_STUDY__",
                           "fmx:in": [{"@id": "manifest.json"}, {"@id": "resolved_config.yml"}]}],
     "hasPart": [{"@id": "resolved_config.yml"}, {"@id": "simulation/production.dcd"},
                 {"@id": "simulation/trajectory_topology.pdb"}]},
    {"@id": "resolved_config.yml", "@type": "File", "name": "The Config, every setting filled in",
     "encodingFormat": "application/yaml", "contentSize": "4113",
     "sha256": "…", "fmx:phase": "study", "fmx:role": "config"},
    {"@id": "simulation/production.dcd", "@type": "File", "name": "Production trajectory",
     "encodingFormat": "application/octet-stream", "contentSize": "3931672",
     "sha256": "…", "fmx:phase": "simulation", "fmx:role": "trajectory",
     "fmx:readWith": {"@id": "simulation/trajectory_topology.pdb"},
     "fmx:frames": 100, "fmx:atoms": 3276, "fmx:savingIntervalPs": 100.0},
    {"@id": "simulation/trajectory_topology.pdb", "@type": "File",
     "name": "Topology of the trajectory: the atoms it saved",
     "encodingFormat": "chemical/x-pdb", "contentSize": "265339",
     "sha256": "…", "fmx:phase": "simulation", "fmx:role": "topology"},
    {"@id": "#fastmdxplora", "@type": "SoftwareApplication", "name": "FastMDXplora",
     "version": "2.5.9", "url": "https://github.com/aai-research-lab/FastMDXplora"},
    {"@id": "#simulation", "@type": "CreateAction", "name": "Simulation",
     "instrument": {"@id": "#fastmdxplora"},
     "object": [{"@id": "setup/topology.pdb"}],
     "result": [{"@id": "simulation/production.dcd"}],
     "startTime": "2026-10-07T01:12:40+00:00", "endTime": "2026-10-07T02:02:47+00:00"},
    {"@id": "https://w3id.org/fastmdxplora/study/1.0", "@type": ["CreativeWork", "Profile"],
     "name": "A FastMDXplora study", "version": "1.0"}
  ]
}
```

---

## Refusals

| Code | When |
|---|---|
| `environment.share.not_finished` | The study is running, stopped short, or is a study of several runs. |
| `environment.share.unscrubbed` | A packed text file still holds the study's folder, the home folder or the computer's name. |
| `environment.share.not_a_study` | No packing list, not RO-Crate 1.2, not this profile, or a profile version this release does not know. |
| `environment.share.unsafe` | A member outside the folder, a link or a device, or more unpacked than the limit. |
| `environment.share.too_large` | The archive is over the download limit. |
| `environment.share.unverified` | A file missing, of another size or SHA-256, or not listed; or the archive not the one Zenodo recorded. |
| `environment.service.unreachable` | Zenodo could not be reached, refused the token, or did not answer as it documents. |
