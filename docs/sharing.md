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
shared without one. A study still running, or one that stopped short, is
refused (`environment.share.not_finished`); share it once it has finished,
after `fastmdx resume` where it stopped. A study of several runs (a sweep,
several systems, an umbrella's windows) is shared whole, as one archive,
once every one of its runs has finished (below).

| Flag | What it does |
|---|---|
| `--share FILE` | Writes the archive to `FILE`. |
| `--share-all` | Packs every file of the study, not only what its pages read (below). |
| `--share-license ID` | The licence the archive is shared under, as an SPDX identifier; `CC-BY-4.0` unless given. |
| `--share-author NAME` | An author of the archive, given once for each; `"NAME;ORCID"` adds an ORCID iD (`"Adekunle Aina;0000-0002-8215-7452"`). On Zenodo a name is given family name first; one written so already (`"Aina, Adekunle"`, a single comma) is sent as Zenodo keeps it, each part trimmed. The report's `--author` unless given; one is needed. |
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

### A study of several runs

Its folder is packed as it is laid out: its Config (`resolved_config.yml`, every
run it makes), its record of runs (`batch_manifest.json`), each run in
`runs/<id>/` with what the table above says of a study, and what the study made
of its runs together: the table of members (`members.json`), the comparison
(`comparison/`), a free energy, the seeds an umbrella's windows started from. It
is opened as the study it was, its runs and the pages that compare them
included. A run still going, one that stopped short, or one the record of runs
plans that never ran keeps the whole study back
(`environment.share.not_finished`, naming the run); a folder in `runs/` the
record of runs does not name (an earlier attempt) is left out.

### A prepared system a run simulated from

A run given `simulation.setup_from` (or `prepared_from`) prepared nothing and
simulated a system prepared elsewhere: the study's own shared one
(`shared_setup/setup/`), or another study's. What the run needs of it goes in
whatever the package: the system as OpenMM built it (`system.xml`, which the
run finds it by, from its SHA-256), the state it starts from, its topology and
its setup record (`setup_parameters.json`, with the ligand's chemistry beside
it). A system kept outside the study, or in a folder of it a shared study leaves
out (one whose name begins with a dot, one set aside such as `previous/`, a
run's folder the record of runs does not name), is carried once, however many runs used it, in `prepared/<name>/`,
named from the folder the runs name it by (`prepared/preparation/` where that
folder's name holds the person's or the computer's), and every path to that folder is said as the
folder it is carried in (`__FASTMDX_STUDY__/prepared/<name>`, below): as the
runs name it, typed from a home folder (`~/prep`) included, and where its setup
record says it was made, from the structure setup kept beside what it made
(`.../setup/retained.pdb`, for a preparation made in one folder and copied to
another; not carried by default, though named so). Setup keeps that structure
only where it kept ions in place; for a preparation without it, the folder it
was made in is kept as written. What setup read (another
study's structure, a ligand file given) is never said so: it is kept as
written; nor is a folder of the study itself, or of another system found. Each run's record of the way
from it to the system (`relative_to_run`) is written as the way in the archive,
so a run opened elsewhere finds it as it did where it ran. The system is found
as the run finds it: where its record says relative to the run, so a study
moved or copied together with its preparation is shared as it is now, then
where it was, each checked by its content; the paths its records give for
where the system was when the study ran are said as the folder it is carried in
too. A run whose system is not found, or
is another one now, keeps the study back (`environment.share.not_finished`,
naming the run and the system): shared without it, the run would be opened
with no record of the system it simulated; so does one kept as links, which
are never packed (put the files they lead to in their place). `system.xml` and
the states beside it are packed byte for byte, never read for paths, as a run finds its system
by `system.xml`'s SHA-256. A window of an umbrella study simulates from its
seed, which keeps no setup record; the preparation the seed was taken from is
carried the same way.

### What is never in it

- **Conversations with the Agent** (`agent/conversations/`, a run's too). They
  are the person's working notes, not the study's record.
- **The record of the process that ran it** (`.fastmdxplora_run.json`): the
  computer's name, its boot, the process number and when it began.
- **Tags and notes** (`study_tags.json`), which are the person's own.
- **Where a study fetched back from another machine was sent from**
  (`fetched.json`): folders of this computer; each run finds its system
  without it.
- **What the study does not need:** the Viewer's scratch, the snapshots a
  run keeps as it goes (`live_frames/`; its last frame is kept, which the
  Viewer shows), what `--rerun` set aside (`previous/`, `superseded/`),
  earlier deposits (`deposit/`), the project bundle, which is a copy of the
  rest, and any file or folder whose name begins with a dot.

A file or folder of the study kept as a link (a trajectory linked to scratch,
say) is never packed, and keeps the study back (`environment.share.not_finished`,
naming each): shared without it, the study would be opened missing what it
leads to. Put the files it leads to in its place (or remove one that leads
nowhere). So does a file or folder that would be packed and cannot be read:
make it readable.

Settings of the installation itself, the model the Agent uses and any key for it,
live outside the study and are never read.

### Paths

A study's records name its files where they were written:
`/home/someone/runs/3ptb/setup/ligands/BEN.sdf`. Shared as written, every
copy would carry the folder it ran in and the name of the person's home folder,
and the paths would name nothing on the reader's computer.

Every text file packed (`.json`, `.yml`, `.md`, `.csv`, `.dat`, `.log`, `.pdb`,
`.sdf`, `.svg`, `.html` and the like) is written with:

- the study's folder replaced by `__FASTMDX_STUDY__`
  (`__FASTMDX_STUDY__/setup/ligands/BEN.sdf`), and the folder of a prepared
  system carried from outside it by the folder it is carried in
  (`__FASTMDX_STUDY__/prepared/reference/setup`);
- any other path in the home folder replaced by `__FASTMDX_HOME__`
  (`__FASTMDX_HOME__/structures/x.pdb`): a file outside the study, on the
  computer that ran it; another person's home folder named as a shell names it
  (`~<name>/`) too;
- a run's own folder as its records give it, where the record of runs gives
  another (a run carried on elsewhere), replaced by its place in the study
  (`__FASTMDX_STUDY__/runs/<id>`), when no other run gives the same folder;
- for a study fetched back from another machine, each input carried named as
  that machine named it (`inputs/<name>`, `<remote folder>/inputs/<name>`)
  replaced by the folder it is carried in;
- the computer's name replaced by `__FASTMDX_HOST__`, and a record's own
  field for it (`"host"`) whatever the name, a short one (`vm`) included.

Any other path, outside the study and the home folder (a structure read from
`/data/pdb/x.pdb`), is kept as it is written, a folder named for the person in
it included (`/scratch/<name>/pdb/x.pdb`). So, for a study fetched back from
another machine, is the folder it was sent to there, where a record names it
outside the folder the study ran in (the Config sent,
`/scratch/<name>/fastmdxplora-jobs/job42/study.yml`), and the login it was
reached by (`<name>@login1.cluster.edu`): read the packed records, or keep such
files in the study, where that matters.

The placeholders are safe in YAML and JSON. In a Markdown file, outside code,
`__FASTMDX_HOME__` and `__FASTMDX_HOST__` are written with their underscores
escaped (`\_\_FASTMDX_HOST\_\_`), so that they show as written rather than as
a word in bold.

Once written, each packed text file is read again, and an archive in which the
study's folder, the home folder or the computer's name is still found is not
written (`environment.share.unscrubbed`, naming the file). Binary files (the
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
be shared (a disk with no room for the archive included) leaves no draft
behind, and the draft is recorded in the study as soon as it exists, so a
share that fails part way is finished by the next one rather than leaving a
second draft.

Zenodo limits how many requests it takes a minute (it answers 429, saying
when it may be asked again) and is at times briefly unable to answer (502,
503, 504). Each request is then asked again, once the time Zenodo gives has
come (else after 1, 2, 4 and 8 seconds, never more than a minute), up to
five times; so is one whose connection was lost or not made, the upload
included. A request that makes a draft or a new version is asked again
only after a 429, which Zenodo gives before doing anything. A new draft is
asked for under a title of its own (`FastMDXplora: sharing <study>
(<eight letters and digits>)`, the study's folder name in plain letters,
replaced by the study's title once it is filled in), kept in `shared_to.json` first. Where the answer to making it is lost
once the request had gone (a 5xx, a connection cut while waiting for the
answer, or an answer naming another draft or record), your drafts are
looked through for that title for up to about two
minutes; at the next share, they are looked through first and, if it is not
among them, it is asked for again under the same title. The one found is
taken as this one: a draft you made is never taken, and two found of that
title are named for you to delete one (`environment.service.unusable_response`);
one Zenodo listed only after it was asked for again is named once the share
is done, for you to delete.
Where Zenodo does not list your drafts at the next share, nothing is made
until it does. The new archive is uploaded before the draft's earlier
one is taken away: a share that Zenodo stops part way keeps the draft as it
was, says so with its address, and sharing again finishes that draft. An
archive of 10 MB or more says how much of it has gone at each tenth.

A study is shared to Zenodo by one command at a time: a second started
while one runs is refused (`environment.share.not_finished`), as two would
each make a draft (on a disk that keeps no locks, and on Windows, two at
once are not told apart: share one at a time there). A study's
`shared_to.json` names its folder too: a study copied by hand with it, while
the one it was copied from still holds the same draft or record (or another
version of the same record, or a draft asked for and its answer lost), is refused
(`environment.share.not_finished`), saying to delete the copy's
`shared_to.json` to share it as a study of its own; a study moved, or the same
folder reached by another path (a container's bind mount, say), goes on with
its draft or record. Not told apart: a copy made before its `shared_to.json`
named its folder (as every one written by 2.6.0 or before this release), and a
copy of a study moved since it last shared (neither folder is the one named),
and a copy whose original's folder this account may not look into.
To share a copy as a study of its own, delete its `shared_to.json` first.

It needs a Zenodo token in `ZENODO_TOKEN` (`ZENODO_SANDBOX_TOKEN` for
`--share-to zenodo-sandbox`) with the **deposit:write** scope, made under
Applications in your Zenodo account's settings. FastMDXplora never
publishes. The token is read from the environment for the one command and
written nowhere.

The study keeps the record it was shared as in `shared_to.json`. Shared
again before you publish, the same draft is made again with the new
archive; the archive the study put there before is taken away, a file you
put beside it (a paper, a figure, or a `.zip` of the study's name holding
something else, which keeps the share back) stays, and a description written there
by you, not the study, is kept as you left it, as are creators,
keywords, an embargo, a licence or a kind you gave a draft the study had
not described yet, or wrote there while its archive was uploading (only
the version is the study's to set). A draft or record shared by an
earlier release, which kept no account of what it wrote, is described
again only where its description is the one such a share wrote with the
study's summary now, or none: one whose summary differs (the study
analysed again since, or you rewrote it) may be yours, and is kept, said
so (clear it there, and the next share writes the study's). Its title,
creators, keywords, licence and access, which the study cannot tell from
yours, stay as the draft holds them in that record (change them there);
once this release has written it, its description stays the study's
while it is what the study wrote, whatever you change of the rest. A
licence or access you set on a draft is a change of yours too: from then
on only the version is the study's to set. A `.zip` put on the draft while
the study's archive uploads is said once it is up, for you to delete
before you publish. Shared again after (analysed again, say), it is a draft
of a new version of the same record, which Zenodo makes only for a token
that also has the **deposit:actions** scope; the record's concept DOI
always leads to its latest version. Its version is counted on from the
latest one published (one you published by hand included; only one Zenodo
says is the last, else the share waits to be asked again; a version published
by hand as the share began its draft is counted on from at the next share), in that one's
own form: 3 after 2, 1.3 after 1.2, v4 after v3. Zenodo keeps one draft
of a new version at a time, and gives it to whoever asks: one there
already, begun on its site or by a share whose answer was lost or named
another, cannot be
told apart, so it is left alone (publish or delete it, then share again;
one still holding the record as it was, its words and files the same,
is said to be deleted, as published it would be the record again). One a share began and could not
remember, its disk full just after, is named for you to delete. One a
share began and you deleted is begun again from the published record,
holding its archive. What you changed on the record before publishing it
is carried into its new version, not written over; an edit you saved on it
and did not publish keeps the share back until you publish or discard it
there (`environment.service.unusable_response`). Only an archive the study put there is taken away: another `.zip` on
the draft keeps the share back, as a record of two archives opens as
neither.

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
   Zenodo recorded for it as it lands. A download cut short goes on from
   where it stopped (asking for the rest of the file, an HTTP range), and
   one Zenodo says to ask again for is asked again after the wait it asks
   for, each up to five times in a row without a byte more
   (`environment.service.unreachable`, saying how far it got); a server
   that sends the whole file again is read from its start, and a part of
   it from anywhere but where the download stopped is asked for again. A
   piece is counted only once it is written here: a disk that fills, as it
   downloads or unpacks, says so (`environment.share.too_large`), and is
   never taken for a connection lost or a damaged archive. A server that
   will not send the rest of the file (416) is asked for the whole of it,
   unless it says the file ends where the download has got to (a size
   recorded larger than the file): then its MD5 decides. A page of
   text in place of the file (a gateway's) is a try that failed, asked again.
   A download that has had nothing for 30 seconds counts as stalled, and is
   asked for again from where it stopped. A folder made to open into is not
   left behind when the study is refused, and what an open killed part way
   leaves (`.fastmdx-opening-<computer>.<digest of its name>-...`) is taken away by the next open
   into that folder on the same computer, told from one still running by
   the lock each holds while it runs (on a disk that keeps no locks, or on
   Windows, it is left for you to delete). An archive of 10 MB or
   more says how much of it has come at each tenth.
3. **Unpacks it into a folder of its own**, named from its title, in
   `--open-into` (else the current folder; in the GUI, where new studies
   go), and only into it: a member with an absolute path, a `..`, a link, a
   device or an encryption is refused, as is what packing never writes (the
   Agent's conversations, a run's too, a process's record, a file whose name begins with
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
8. **Opens it** in the GUI; a study of several runs as one, with its runs.

Nothing in an archive is run. Its Config is a file like any other, run again
only by someone who chooses to. The report is rendered as every report is,
with raw HTML shown as text and links only within the study or to the web.

---

## The profile: a FastMDXplora study, version 1.1

Profile identifier: `https://w3id.org/fastmdxplora/study/1.1`. Version 1.1
adds a study of several runs and a prepared system carried from outside the
study (9 and 10 below) to 1.0; an archive of 1.0 keeps 1.1 too. A study of
several runs is opened by FastMDXplora 2.6.1 or later: 2.6.0 refuses its
record of runs.

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
   The root's `hasPart` may also name `Dataset`s (9 and 10 below).
   Each also has a `name` (as the Files page labels it), an `encodingFormat`,
   `fmx:phase` (`study`, `setup`, `simulation`, `analysis`, `report` or
   `viewer`) and `fmx:role`, one of:

   | `fmx:role` | What the file is |
   |---|---|
   | `config` | the Config the study ran, every setting filled in |
   | `manifest` | the Manifest, a run's, or a study of several runs' record of runs |
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
   Its `@id` is `#<phase>`, and in a study of several runs `#<run>/<phase>`,
   named "Simulation of the run `<run>`" and so on.

### A study of several runs

9. The root lists each run in `fmx:runs`, and each is a `Dataset`, its
   `@id` its folder (`runs/<id>/`), in the root's `hasPart` too: its `name`,
   its `hasPart` the files of the run, its `mainEntity` the run's Config
   (`runs/<id>/resolved_config.yml`), `fmx:system` the system it ran and
   `mentions` its phases (8). Each file of a run says which in `fmx:run`.
   The root's `mainEntity` is the study's Config, every run it makes.
10. A prepared system carried from outside the study, or from a folder of it
    the archive leaves out, is a `Dataset`, its
    `@id` the folder it is carried in (`prepared/<name>/`), in the root's
    `hasPart`: its `hasPart` its files, each with `fmx:phase` `setup`, and
    `fmx:usedBy` the runs that simulated from it.

### Reading a newer one

11. A reader opens an archive whose profile has the same MAJOR version as one
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
     "conformsTo": [{"@id": "https://w3id.org/fastmdxplora/study/1.1"},
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
    {"@id": "https://w3id.org/fastmdxplora/study/1.1", "@type": ["CreativeWork", "Profile"],
     "name": "A FastMDXplora study", "version": "1.1"}
  ]
}
```

---

## Refusals

| Code | When |
|---|---|
| `environment.share.not_finished` | The study is running or stopped short; a run of a study of several did not finish or never ran; a run's prepared system is not found, is another one now, or is kept as links; a file or folder of the study is a link; a file or folder of it cannot be read; it is being shared already; or its `shared_to.json` is the one of a study it was copied from. |
| `environment.share.unscrubbed` | A packed text file still holds the study's folder, the home folder or the computer's name. |
| `environment.share.not_a_study` | Opening: an archive that cannot be read or is no zip, no packing list, not RO-Crate 1.2, not this profile, or a profile version this release does not know. Sharing: a study's record of runs names no run, or a run by a name that is no folder's. |
| `environment.share.unsafe` | A member outside the folder, a link or a device, or more unpacked than the limit. |
| `environment.share.too_large` | The archive is over the download limit, or the disk it is opened into has no room for it. |
| `environment.share.unverified` | A file missing, of another size or SHA-256, or not listed; or the archive not the one Zenodo recorded. |
| `environment.service.unreachable` | Zenodo could not be reached, refused the token, or did not answer as it documents, still after five tries where it said to ask again. |
| `environment.service.unusable_response` | Zenodo holds what a share cannot take as its own: two drafts under the title it asked for one with (delete all but one); a draft of a new version there already, or one changed since it was begun (publish or delete it); an edit saved on the published record and not published (publish or discard it); or a `.zip` on the draft the study did not put there (delete it, or keep it in a record of its own); or an answer for the study's draft or record that is neither (share again later); or the draft deleted on Zenodo during the share (share again, and a new one is made). |
