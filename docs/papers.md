# Reproducing a paper's MD studies

Give FastMDXplora a paper, and it lists the molecular dynamics studies the
paper reports. Choose one, several or all, and each is written as a config
that sets what the paper states, as the paper states it, with the paper's own
words as each setting's reason. Once a study has run, its report sets what the
paper reports beside what the study determined.

```bash
fastmdx config --paper paper.pdf                                  # list its studies
fastmdx config --paper paper.pdf --paper-studies S1,S3 -f sod1.yml
fastmdx config --paper 10.1371/journal.pone.0247841 --paper-studies all
fastmdx config --paper paper.pdf --paper-si si.pdf --paper-studies S2
```

The first lists the paper's studies and writes nothing. The second writes S1
and S3, as `sod1-s1.yml` and `sod1-s3.yml` (one study is written to `-f` as
it is). The third fetches an open-access paper by its DOI. The fourth reads
the supporting information too, where a paper's methods often are. Each file
then runs as any config does, with `fastmdx explore --config`.

In the GUI it is **Or reproduce a paper's MD studies** on the Config Builder's
first step: the paper, its supporting information, **Read the paper**, then
each study with how each of its settings came from the paper, **Open in the
builder** for one, and **Download the chosen configs** for several. The Agent
reads a paper when asked to reproduce one (its tool `studies_in_paper`), and an
AI app through the MCP tools `read_paper` and `check_paper_studies`.

---

## How a paper is read

The paper's text is read here: from a PDF page by page, from the JATS XML an
open-access archive serves section by section and table by table, from a
Word file or plain text whole. Each part keeps where it is (`Methods`,
`Table 2`, `p. 4`, `SI p. 3`), so a value can be said with its place.

An AI model, the one chosen with `fastmdx agent model`, is then asked three
things, each with the paper's text before it: which MD studies the paper
reports, the settings of each protocol they share, and the results each
study reports. A study is one starting system (its structure, mutations,
ligands, membrane) under one force field and water model, at one temperature,
by one method. Independent repeats of it are one study with a number of
replicas.

**Nothing the AI model says is used as it says it.** Every value comes with
the words of the paper it was read from, and those words are looked for in
the paper, letter for letter once spacing, case, accents, hyphenation and line
breaks are set aside (a PDF writes `Å` as `A˚` and breaks words over lines).
The number is then read from those words here, in this software's unit: a
temperature in kelvin, a length of simulated time in nanoseconds, a distance
in nanometres. A value is used only where its words are the paper's **and**
hold it:

| What the AI model gave | What happens |
|---|---|
| Words in the paper that hold the value | Used, with the words as its reason |
| Words not in the paper | Not used (`not_found`): the AI model wrote them |
| The paper's words, without the value in them | Not used (`unread`): a total divided by the replicas, a unit misread, a number from elsewhere |
| Words that send the reader to another paper | Not used (`by_reference`), and said |
| Words that put it in the supporting information | Not used (`in_si`): give the supporting information |

A force field, a water model, a thermostat or a box is held only where every
one the AI model names is named by the words too, however each spells it
(`ff14SB`, `AMBER ff14SB`, `Amber14SB`). A number is held only whole: words
that begin or end inside one ("5 µs" in "1.5 µs") are not the paper's. A
count of replicas is held only beside what it counts ("three independent
runs", "in triplicate", "3 x 100 ns"), and whether the system was neutralised
only where the words say so or say it was not. A result's error is taken as
an error of the mean, and over how many runs, only where the paper says so
beside it; otherwise it is compared as a spread. A description (what the
system is, how it was minimised) is kept as the AI model's and never set as a
value.

A reply from the AI model cut off part way, or not the JSON asked for, is
asked for again, and then refused: nothing is read from it.

A reading is kept under the paper's digest and the AI model's name, so asking
again for other studies of the same paper asks the AI model nothing.

---

## How a study becomes a config

Each setting is said with one of five words:

| Word | Meaning |
|---|---|
| as stated | Set as the paper states it |
| not stated | The paper does not say; this software's value is used, or one the paper's other settings imply, with why |
| differs here | The paper states it, and it is done here otherwise, with how |
| needs you | What the paper gives cannot be set from its words alone |
| not possible here | This software cannot run it |

A study is **ready** where every setting is as stated or not stated,
**runs, with differences** where some differ, **needs you** where something
must be supplied first, and **cannot run here** where anything is not
possible. A study that cannot run is never written.

What it does, setting by setting:

Every setting a study always has (the temperature, pressure, timestep, box,
padding, salt, constraints, electrostatics, ensemble, thermostat,
equilibration, water model and number of replicas) is said, the paper's
value or this software's. Where the paper gives a temperature, timestep,
water model or number of replicas this could not check (words it does not
contain, or by reference to another paper), the study needs you.

- **The structure.** A PDB entry the paper names is fetched. A model, a
  docked pose, a peptide built extended, or a snapshot of an earlier
  simulation needs you: give its file (the paper's deposit, where it has one).
  Mutations are made by setup; ligands the entry holds are kept by setup where
  it can parameterise them, and its plan says which.
- **The force field.** Named as the paper names it, from the files OpenMM
  ships: ff14SB, ff19SB, ff99SB, ff99SB-ILDN, ff03, ff15ipq, AMBER-FB15,
  CHARMM36 and CHARMM36m for proteins; OL15, OL21, bsc1 and OL3 for nucleic
  acids; CHARMM36, Lipid17 and Lipid21 for lipids; TIP3P, CHARMM's TIP3P,
  TIP3P-FB, TIP4P-Ew, TIP4P-FB, TIP4P/2005, OPC, OPC3, SPC/E and TIP5P for
  water. ff19SB, OL21 and CHARMM36m are in OpenMM 8.3 and later, Lipid21 in
  8.4. CHARMM22, CHARMM27, CHARMM22*, ff99SB*-ILDN, ff99SB-disp, ff03w,
  DES-Amber, Tumuc1, OPLS, GROMOS and Martini are not shipped by OpenMM, and
  the polarizable Drude and AMOEBA are not run here: the study cannot run. An
  older AMBER protein force field (ff99SB, ff03 and the like) carries its own
  nucleic-acid parameters, so beside OL15, OL21 or bsc1 it cannot run. A
  force field written as OpenMM's files carries no cutoff of its own: an
  AMBER one is run with a plain 1.0 nm cutoff and no switch, a CHARMM one
  switched from 0.2 nm inside its cutoff (1.2 nm), each said where the paper
  is silent.
- **A ligand.** Ions, water and "none" named among a study's ligands are no
  ligand; ions the entry holds are kept by setup. A small molecule is
  parameterised here only beside ff14SB: a ligand study in
  another AMBER force field differs (ff14SB is used, said so), one in a
  CHARMM force field cannot run. GAFF and GAFF2 are used as stated, with
  AM1-BCC charges (RESP differs); CGenFF differs (OpenFF Sage is used).
- **The box.** A cube, a truncated octahedron or a rhombic dodecahedron as
  stated. **Padding is doubled**: a paper's "1 nm from the solute to the box's
  edge" is 2 nm to the nearest periodic image, as `solvent_padding_nm` measures
  it, and the box is the same. A paper that names only the ions that
  neutralised the system gets no salt beyond them.
- **The physics.** Temperature, pressure, timestep, cutoff, switch, Ewald or
  PME, constraints and the hydrogen mass as stated. OpenMM keeps temperature by
  Langevin dynamics and pressure by a Monte Carlo barostat, so a paper's
  Nosé-Hoover, v-rescale, Parrinello-Rahman or Berendsen differs, the ensemble
  the same; a GROMACS force switch is a potential switch here and differs. A
  4 fs step without a stated hydrogen mass needs you.
- **The protocol.** Equilibration lengths and production as stated;
  independent replicas as a sweep over the random seed, so the study's
  replicas are compared as replicas. Restraints during equilibration are
  released in this software's steps, which may not be the paper's.
- **The method.** Plain MD runs, and so does plain MD analysed with a
  Markov-state model. Umbrella sampling, metadynamics and steered
  MD need you to write their collective variable from the paper's description.
  Replica exchange (bias exchange included), alchemical free energy,
  accelerated MD, QM/MM, coarse-grained models, implicit solvent,
  milestoning and weighted ensemble cannot run here. A method's details make
  a study one of these only where they name it in a clause that neither
  denies it nor gives it to another work ("compared with REMD from ref. 12"
  leaves plain MD plain).
- **A membrane.** A bilayer of one lipid this software builds, however the
  paper words it ("a POPC bilayer of 144 lipids"), in a rectangular box; a
  mixture cannot run here. Beside an AMBER force field that names no lipid
  force field, Lipid17 is used, said so.

The config carries `decisions` (each setting's reason, the paper's words with
where they are) and `paper` (the paper's DOI and title, which study, the AI
model that read it, each setting's word, and the results the paper reports for
the study). A study that needs you lists what in `paper.needs`, and the
validator refuses it while anything is listed: supply each, then delete
`paper.needs`. In the Config Builder, **I have supplied what it needs** does
the same; where a setting it needed is still as the study was opened (the
production, the structure), it names it first, and clears the list only when
pressed again.

`--paper-until-determined` runs each study until the results the paper reports
with an error are determined here to that error (`simulation.stop_when`, each
in its analysis's unit), at most the paper's own length; a study whose results
carry no error runs the paper's length.

What is listed for each study is how much it simulates (`3 x 300 ns`); how long
that takes on this machine is said by the Config Builder once a study is opened
there, from the structure, as for any study.

---

## Where a paper comes from

A file: a PDF, the paper's JATS XML (`.xml`, `.nxml`), a Word file (`.docx`) or
text (`.txt`, `.md`), up to 100 MB. A scanned PDF has no text to read and is
refused (`environment.paper.unreadable`).

An identifier: a DOI, a PMCID (`PMC1234567`) or an arXiv identifier. Europe PMC
is asked first; a paper in its open-access collection is fetched as JATS XML,
with its supporting information where Europe PMC serves it. A bioRxiv or
medRxiv preprint is fetched as bioRxiv's JATS XML, an arXiv one as its PDF. A
paper that is free to read but not licensed for programs to fetch, or not open
access at all, is not fetched (`environment.paper.not_open`): download its PDF
and give the file. Nothing is sent but the identifier, and what is fetched is
kept, so a paper is fetched once.

---

## Set beside the paper

A study written from a paper has, in its report, **Reproducing the paper**: each
result the paper reports for it (kept in the config before the study ran)
beside the mean this study's analysis determined, in one unit, with a verdict
that says only what the numbers allow.

| Verdict | When |
|---|---|
| agrees | Within twice the combined error of the two means |
| disagrees | Further apart than that; how many combined errors is said |
| within the paper's spread | The paper's ± is a standard deviation without the number of runs it is over, so not the error of its mean: this mean is inside that spread |
| outside the paper's spread | Outside it |
| the paper gives no error | The difference is said, and no verdict |
| not determined here | This study's mean was not determined, and why |
| not compared | There is no mean here to set beside it |

The paper's error of its mean is its standard error, or its standard
deviation over the runs or blocks it names divided by their square root, or
its 95% interval's half-width divided by 1.96. A study of several replicas is
compared by the mean of its replicas' means, whose error is their spread over
the square root of their number (never less than one run's own error divided
by it), in the study's comparison report; each run's own report compares that
run alone. A quantity here is computed over this software's own atoms and from
its own reference (an RMSD from the first frame over the alpha carbons, by
default): where the paper's description says otherwise, the two are not the
same quantity, whatever the verdict, and the section says so.

---

## What this does not do

- It reads the text and tables. A value given only in a figure is not read.
- It does not run a method this software does not run, or a force field
  OpenMM does not ship, in another's place.
- It does not make up a setting. A setting the paper does not state takes this
  software's value, and the config says so beside it.
- It does not reproduce a paper's inputs from its deposit (a `.prmtop`, `.top`
  or `.mdp`); a structure from a deposit is given as a file.
