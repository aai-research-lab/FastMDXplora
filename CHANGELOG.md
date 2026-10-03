# Changelog

All notable changes to FastMDXplora are documented in this file.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) ·
Versioning: [SemVer 2.0.0](https://semver.org/spec/v2.0.0.html)

## [Unreleased]

### Dashboard research features on `context-aware-agent`

The Agent composer now offers server-resolved evidence inspection and per-message
view opt-out. Suggested configurations have an explicit before/after review;
review expires and is invalidated by changed drafts or studies. Agent authorship
survives builder loading, and Run here requires fresh human review of the exact
Agent draft. The research toolbar is moved away from the composer and Agent text
uses consistent interface typography. Scientific settings are never applied by
the model.

The princeote feature branch adds a context-aware explanation Agent with packaged
error knowledge and human-reviewed draft suggestions, portable screenshot
bookmarks, rotating MP4/GIF trajectory exports, future preparation provenance and
an expandable saved-stage audit. Protein and graph selections support verified
residue explanations and pinned comparisons without changing scientific source
data. Subscription adapters cover OpenAI, Claude, Kimi and Gemini; Copilot is
removed. Codex models are ordered Astra, Sol 6.1, Sol 6, Luna 6, then the remaining
models, with model-specific reasoning controls and readable dark dropdowns.

**The full implementation framework is not complete.** OpenAI sign-in/inference
has live acceptance; the other providers still need real account tests. Remaining
context/draft review controls, clip options, audit visuals and final scientific,
browser and packaging gates are listed in the
[current milestone status and change description](docs/dashboard-feature-status.md).
This branch update does not integrate upstream main or declare a new release.

The [completion and aesthetics plan](docs/dashboard-completion-plan.md) specifies
the remaining controls and acceptance checks, plus a new M8 design milestone
covering typography, contrast, layout, responsive panels and keyboard use. This
is planned work; the redesign and remaining features are not yet implemented.

### A hosted GUI shows its service's name and the person signed in

**`fastmdx gui --hosted --product-name NAME --product-tagline TEXT`** shows the
service's name and line at the top of the sidebar, on the loading screen and in
the window's title, in place of FastMDXplora's; the citation and the links to
this software stay. The person is named at the foot of the sidebar with their
initials, from `X-FastMDX-Account-Name`, which the proxy adds to each request,
so a name changed at the service shows on the next page load. `--account-url`
is now the first item of the menu that opens there, not a link of its own.

### A hosted GUI can send a study to its service's compute

**`fastmdx gui --hosted --runs-url PATH`** adds **Run on a GPU** beside
**Run here** in the builder, and **Run it on a GPU** beside **Run it as it
is** for a config file. The first saves the config in the workspace, beside
the results folder it will write (`<folder>.yml`, never over an existing
file); both then open the service's page with the config and the folder
filled in, where the person confirms. The GUI starts nothing itself: the
compute is the service's. Only a path on the same site is accepted.

### `fastmdx resume` in a new container

**`fastmdx resume` no longer takes a new process for the run it carries on.** A
study stopped in one container and carried on in another met its old run's
record naming a process number that, in the new container, could be the
resume's own or its shell's, and the resume refused the study as still running.
A run's record now says which host and boot wrote it, a record from another is
not a live run here, and the resume never counts itself or the processes that
started it. The GUI no longer tries to adopt a run recorded on another machine.

### A budget works on a machine never measured

**`--budget-hours` no longer refuses every study on a machine without a stored
measurement.** It stopped after setup with "This machine has not been measured",
which a new container, a fresh cloud GPU or a new cluster node always is, and it
priced the platform "unknown", so a machine measured on CUDA was refused as a
different machine too. The study is now priced for the platform the simulation
phase will choose, and a machine with no measurement for it is measured on the
study's own prepared system, which covers PME and its force field, as argon did
not. Only a machine that cannot be timed at all stops for want of a number.

### A hosted GUI links back to its service

**`fastmdx gui --hosted --account-url PATH`** shows **Your account** in the
sidebar, linking to the service's own page for the person. Without it a
person inside the GUI had no way back to the service's pages, signing out
included. Only a path on the same site is accepted.

### A residue of an ion's name holding several atoms is a question

**A copy of an ion's name with more than one atom in it is no longer kept as
one ion.** A residue named ZN holding two zinc atoms, coordinated by the
protein, was written into the structure as it stood, where no force field
template matches it; from a local file it was sent for chemistry instead, with
advice to fetch `ZN_ideal.sdf`. Setup now stops and names the residue and its
atoms, since which ions they are is not the structure's to say. Such a copy
away from the protein is discarded, as before.

### A dry run says what each run will do about setup

**`--dry-run` no longer lists setup for runs that prepare nothing.** A run
whose `setup_from` names a prepared system simulates that system, and umbrella
windows prepared alike share one preparation, but a campaign's dry run listed
setup for every run. It now leaves setup out where it is skipped and says why:
the system that will be simulated, the shared folder the windows' system is
prepared in, or that `setup_from` names a folder with no prepared system in it
and the simulation will refuse. Umbrella windows are no longer warned that each
will solvate a box of its own.

### A seeded umbrella window finds its setup record

**The windows of an umbrella study seeded from a pull find their setup
record.** A seeded window simulates its seed, a copy of the prepared system with
a starting state and no setup record, and it named the seed as the system it
simulated. The report, re-analysis and the ligand's chemistry then found no
setup record for the window. Each seed now records the preparation it was
taken from (`seeded_from.json`), relative to itself and by the SHA-256 of its
`system.xml`, and the setup record is found through it, also after the study
has moved. A different preparation at that place is refused, as for any run.

### A shared umbrella system is reused only when it is checked

**An umbrella study run again no longer reuses a shared system it cannot
check.** A shared system prepared before `prepared_for.json` was written was
reused with a warning, whatever it had been prepared from. It is now checked
against its own setup record, and one with neither record is refused with the
new code `setup.prepared.unverifiable`. The structure is identified by the
SHA-256 of its file as well as by its path, so a structure edited in place is
another structure, and the same file under another path is the same one.
`--force-overwrite` prepares the system again, as before.

### A fetched study finds the prepared system it was sent with

**`fastmdx remote fetch` ties a study back to the prepared system it was sent
with.** A study given `setup_from` travels with the prepared system under
`inputs/<name>` and records that name, which means nothing on this computer, so
a study fetched back found no setup record for re-analysis or the report, and
fetch said nothing. The job now records where each input was sent from, fetch
writes that beside the results as `fetched.json`, and the prepared system is
found there, checked by the SHA-256 of its `system.xml`. Fetch names any run
whose prepared system is not on this computer or has been prepared again since.

### CHARMM36 membranes take their lipids from `charmm36.xml`

**A membrane built with a CHARMM36 force field list lacking lipid templates
now gets them.** The lipid fallback named `charmm36/waters.xml`, which OpenMM
does not ship, so such a list was refused over a file that does not exist.
CHARMM36's lipids are in `charmm36.xml`, which is what is added now, and the
lipid's template is checked to be there once any file is added, rather than
failing later at the residue template.

### Every bilayer OpenMM builds gets the membrane barostat

**DMPC, DOPC, DPPC, DLPC and DLPE membranes are now coupled as membranes.**
OpenMM names the lipids it builds by its patches' three-character names
(`DMP`, `DOP`, `DPP`, `DLP`, `POP`), and the check that chooses the barostat
knew only `POP` and the full names, so only POPC and POPE bilayers got the
membrane barostat; the others were coupled isotropically, which squeezes the
bilayer and gives the wrong area per lipid. The crash diagnosis and the
default bilayer of a `membrane_depth` coordinate missed the same lipids. All
three now read one list (`fastmdxplora.lipids`).

**A soluble protein with pyrophosphate bound is no longer coupled as a
membrane.** `POP` is OpenMM's name for POPC and the PDB's code for
pyrophosphate, and one residue of it was enough to choose the membrane
barostat. A system is now a bilayer when it holds at least 20 lipids.

### A membrane protein is placed before its bilayer is built

**The protein is oriented and centred for the bilayer, by a fit checked against
OPM.** OpenMM builds the bilayer in the xy plane at z = 0 and takes the
protein's frame as it is. Nothing centred it, so the bilayer sat at z = 0 of
whatever frame the file was in, and `membrane_orient` rotated a structure by
its longest axis and centred it on its centroid, which is wrong for anything
with a soluble domain: a GPCR with a fusion partner or a G protein. The
membrane normal, centre and hydrophobic thickness are now fitted from where
the protein's lipid-facing surface is apolar. Across 65 membrane proteins in
OPM (170 fits from random starting frames, 25 proteins held out) the fitted
normal came within 21° of OPM's every time and 3 to 4° at the median, and the
centre within 0.1 nm at the median. A structure that is not a membrane protein
is refused (`setup.membrane.no_belt`), and one lying at an angle to z with
nothing said about it is refused with the angle stated. Copies of one chain
are compared by the symmetry that relates them, which in one bilayer turns the
normal onto itself: the old test compared each copy's longest axis, and
refused a porin trimer, whose monomers are as wide as they are tall.

**An OPM file is recognised and keeps its frame.** Its membrane marker
pseudo-atoms were read as a molecule to parameterise; they are now taken out,
and OPM's orientation, centre and published thickness are used.

**`membrane_center_z_nm`** places the bilayer in a structure oriented
elsewhere, with `membrane_orientation_checked: true`.

**The setup record says how the protein was placed and what was built**, under
`bilayer`: the placement, the fitted thickness, the tilt of the input, the
rotation, the number of lipids in each leaflet and the box. The methods
paragraph says the same, instead of describing a dodecahedron box of water and
an isotropic barostat, and the simulation record names the barostat.

**Advice before a membrane run:** a bilayer below or within 6 K of its lipid's
main transition (DPPC at the default 300 K is in the gel phase's range), and
NPT equilibration under 1 ns, which is short for a bilayer's area to settle.
The box-too-small advice no longer fires on a membrane's rectangular box.
Setup says what it is doing while OpenMM packs the bilayer, which reports
nothing for minutes, and names a component the force field cannot describe
there as it does during solvation. A four-site water given with a membrane is
refused in words; it failed inside the packing with OpenMM's advice to call a
Modeller method.

### The bilayer is measured

**`area_per_lipid`, `bilayer_thickness` and `lipid_order`** measure the
bilayer itself, and run automatically wherever there is one: the area per
lipid with the protein's cross section in the hydrophobic core taken out, the
phosphate-to-phosphate thickness, and the deuterium order parameter S_CD of
every acyl-chain carbon by chain. They are what a membrane run is checked
against before anything about the protein in it is believed. The bilayer
centre is found across the periodic boundary, lipids are assigned to leaflets
every frame, chains are found from the bonds so any force field's naming
works (AMBER's split head and tail residues included), and lipids that do not
form a bilayer normal to z are refused rather than measured. A new page,
`docs/membranes.md`, covers the lipids and their transition temperatures,
placement, equilibration, and what to compare the bilayer against.

### Found running a membrane dimer end to end

**`order_parameters` saves a homo-oligomer's values.** Copies numbered alike
come back as a table naming the chain, and saving that as numbers failed, so
the order parameters of every symmetric dimer were computed and lost.

**`end_to_end` measures each chain where the selection spans several.** It
refused a selection of several chains unless `by_chain: true` was set, so the
default plan failed on every multi-chain protein. `by_chain` now defaults to
true: one distance per chain, as a table naming each chain, and one column as
before for a single chain. `by_chain: false` still refuses several chains
rather than measure from one chain's start to another's end.

**A production run no longer prints a line of MDTraj's source.** Its reporter
for a saved subset hands the DCD writer double-precision coordinates and the
writer warns about the cast, at the start of every run that leaves the water
out, which is every run by default.

### A Config with `system:` is told the key is `systems:`

**A Config naming its structure with a top-level `system:` is answered with the
key it wants.** `explore` said "explore requires a system", which reads as
though the file had not been read, and the per-phase commands said "`systems`
must be a non-empty list of mappings". Both now show the value given as
`systems:` with one `- system:` entry.

**`OrientationWarning` is deprecated.** Nothing raises it since the orientation
check became a fit; importing it still works and says it will be removed in
3.0.

### A run asked to stop ends on a frame

**SIGTERM or Ctrl-C during production ends the run where it can be carried
on.** A scheduler, a cloud provider taking a machine back, a container
platform and the GUI's Stop button all send SIGTERM, and the run ended at
once, keeping only its last interval checkpoint, so up to
`checkpoint_interval_steps` of production were run again by `fastmdx resume`.
Now the run steps on to its next frame, writes a checkpoint there, and ends
with a refusal marked retryable (`simulation.run.stopped`), which `fastmdx
resume` reads as an interruption; the joined trajectory keeps its spacing. It
does so when the next frame can be reached within 20 seconds, inside the 30 or
so a scheduler allows before it kills; `FASTMDX_STOP_GRACE_SECONDS` sets the 20.
A second signal is obeyed at once. The GUI's Stop waits for it rather than
killing the run after five seconds.

### `fastmdx resume` carries on a study of several runs

**A sweep, several systems or an umbrella study's windows are resumed by one
command.** `fastmdx resume` refused a study of several runs, whose runs had to
be resumed one by one, and nothing then rebuilt what the study says across
them. Now each run that started is carried on as a study of one is, each that
never started is run, the runs share out the workers and devices the study
asked for, and the aggregate, an umbrella study's free energy and the
comparison are rebuilt. A structure the study named by a relative path is found
beside the study's folder. A study any run of which is still going is refused.

### A study run without OpenMM names what is missing

**Setup that prepared no system is no longer shown as complete.** Without
OpenMM or PDBFixer, setup still reads the structure and records its heterogen
decisions, and the record still says it finished, since leaving an optional
backend uninstalled is a choice. On screen it now ends with a warning, "setup
finished without preparing a system", where it said "setup complete" over a
folder with no `system.xml`. A sequence given as the system is shown the same
way.

**The simulation names the missing package.** It said "No setup outputs found,
run setup first" before naming OpenMM, sending somebody to rerun a phase that
would stop in the same place. It now says only what is missing and how to
install it.

**The advice for analysis alone works.** It said `--include analyze report`,
which the plan refuses (`analyze` is the command; the phase is `analysis`). It
now says `--include-phase analysis report`. The message also reads "OpenMM and
PDBFixer" and "it is" for one package.

### A study carried on after a stop is finished

**A second `fastmdx resume` of a study carried on after a stop has nothing to
do.** The study's Manifest kept its simulation phase as it had stopped, beside
the analyses of the joined trajectory, so a second resume read a study still
to carry on and tried to join the stopped piece again, which the join refuses:
"Segments [0] did not finish". Once production is carried on and joined, the
phase says so, with the segments and frames joined, and keeps the stop it came
back from as `carried_on_from`. Found resuming a stopped study of several runs
twice.

### A study's resolved config runs again

**A `resolved_config.yml` fed back is no longer refused.** It records the
force field as asked (`forcefield: auto`) and the files that became
(`force_field: [amber14-all.xml, amber14/tip3p.xml]`), and says it can be fed
back to reproduce the study; fed back, setup refused it with "Specify either
`forcefield` ... or `force_field`, not both". A list that is exactly what the
name resolves to is now read as the name, which also decides the cutoff and
whether a ligand can be parameterized, as it did the first time. A name and a
different list are still refused. Found when a resume had to start a stopped
run again from its setup.

**A run started again by `fastmdx resume` says how it ended.** Where
production had not begun, the study is run from its start; it was reported as
done whatever became of that run, so one that failed again came back as
carried on. Its structure is also found from the study's folder outwards, so a
run of a campaign finds a structure named relative to where the study was
started, wherever the resume is run.

### A study of several runs stops as one

**The GUI's Stop reaches every run of a parallel study.** It signals the
process it started, which for a study running its runs in parallel is the
parent, and the workers never heard it: they ran on until the GUI gave up
waiting and killed the parent, and were left running with no one watching.
The parent now passes SIGTERM on to each worker (Ctrl-C reaches them from the
terminal), and the GUI ends whatever is left of a run it had to kill.

**A worker stopped outside production ends its run with a record.** It died,
and a worker dying breaks the pool, so every other run lost its result with
it. The phase it was in now ends with the retryable refusal
`simulation.run.stopped`, and `fastmdx resume` runs that phase again. In
production the run ends on a frame, as before.

**A stopped study starts nothing more.** Run one at a time, a study stopped in
one run went on to the next. Now no further run starts, each run not started
says so, and the study ends with "Batch stopped" and the one command that
carries all of it on. A second SIGTERM within two seconds of the first, as
when one is sent to the process group and passed on by the parent too, counts
once; a second Ctrl-C still means stop now.

**A run being carried on shows its progress.** The study's progress line read
each run's status from the run's own folder, which a run being carried on no
longer writes, so it stood at the step where the run had stopped.

### Metadynamics on a distance runs on a grid

**A bias on `distance` or `ligand_distance` is kept on a grid.** Only
torsions were gridded, because PLUMED stops a run whose variable leaves its
grid and a distance was taken to have no ceiling, so every distance study ran
on the hill sum, whose cost per step grows with every hill: on one torsion
run the slowing over its first 8,000 hills puts 100 ns at about 19 hours,
against about 7 on a grid. A distance does have a ceiling, since PLUMED
measures it by the minimum image: the farthest vertex of the periodic cell's
Voronoi cell (half the diagonal of a cube, the image distance over the square
root of two for the rhombic dodecahedron studies are built in). The grid runs
from 0 to that, a tenth larger to cover any growth of the cell, taken from the
cell equilibration leaves when the bias goes on.

**So are `angle` (0 to π), `q` (0 to 1), `coordination` (0 to its number of
pairs) and `membrane_depth` in a rectangular cell (half its height either
side, a tenth larger).** Each bound is one the variable cannot cross. A grid
over two million points is left out; `ligand_rmsd` and `radius_of_gyration`
have no bound and stay on the hill sum. Checked with PLUMED 2.9 on each
variable: the variable never left its grid, and the bias on the grid matched
the hill sum to 0.0005 kJ/mol over 3,000 depositions.

### An umbrella study sets its own resample count

**`bootstrap_resamples` in the umbrella block.** The resamples behind a free
energy's interval, and a binding free energy's, were fixed at two hundred for
every study: on seventeen windows the curve took 0.04 s and its interval
13.6 s, and a study of thirty-five windows spent minutes before any curve could
be looked at. A study now sets the count: fewer for a quick look, more for a
figure, 0 for the curve with no interval. Two hundred stays the default, and
the count used is recorded in `pmf.json`. A value that is not a whole number
of 0 or more is refused.

### Every step's explanation reaches the person

**The seven explanations nothing printed are said beneath the step they
explain.** Why heterogens are classified, beneath the decisions; why a
ligand's chemistry is looked up, and why it needs parameters of its own, where
setup takes them; why the solute is held while the water settles, as
equilibration starts; why a bilayer's pressure is coupled in its plane and
along its normal separately, where that barostat goes on; why a mean is given
with the independent samples it rests on, beneath the analyses; and why
interactions are typed rather than counted, after the interaction analysis.
Each was written and none reached anyone.

**`minimize` and `production` cite what is worth reading**: Braun et al.
(LiveCoMS 2019) on preparing a system, and Grossfield et al. (LiveCoMS 2018)
on what a trajectory supports. The ligand parameters text no longer says
OpenFF makes them whatever force field was chosen.

### A report names the software that produced each phase

**The Methods and Reproducibility sections no longer credit the installation
that wrote the report.** A study simulated under one release and analysed or
reported again under another was said to have been set up, simulated and
analysed with the later one, and the libraries named were those installed
where the report was written. Each phase's own record is now read: "System
setup and simulation were performed with FastMDXplora 2.5.4, and analysis with
FastMDXplora 2.5.8", with a library whose version differs between phases given
by phase. A system prepared by another study (`setup_from`) is credited to
what that study recorded. A run that recorded nothing says so.

**The Manifest records what computed the charges and the bias.** Beside the
Python packages, each phase's `environment` now names the AM1-BCC charge
program (`am1bcc`: AmberTools or OpenEye) with AmberTools' version, and
PLUMED's version, read from the conda environment. Neither is a Python module,
so nothing recorded which build ran. pandas and matplotlib are recorded too.

### A binding free energy says where its reference state does not hold

**An umbrella study along `ligand_distance` now measures, from its windows,
four things the standard-state conversion rests on.** Each is recorded under
`binding.reference` in `pmf.json`.

- The ligand must be free on the shell (or the cone's cap) where the curve is
  read as bulk. Where protein occupies part of it, the number is withheld with
  the radius, the open share and what it would cost (`kT ln(1/f)`), measured
  against the tail's own 0.6 kJ/mol.
- In a system with a bilayer and no cone, the number is withheld, since bulk
  is a slab of water.
- A warning names any window whose ligand sits where the bound state has
  backbone, since the pull went through the protein there.
- A warning names any window whose ligand turned too slowly for its
  orientation to be sampled.

**The binding free energy is printed.** It was written to `pmf.json` and
said nowhere. The console now gives the number with its standard error, or
why it was withheld, and each warning.

### An umbrella window runs again in place

**`fastmdx explore --config study.yml --rerun-window 9`** runs window 9 again
in `runs/window-09`, with whatever the Config now gives it (a stiffer spring in
its place in the `force_constant` list, a longer run), keeps every other
window, and recombines the free energy from the whole set. The earlier run is
moved to `superseded/`. From Python, `explore(rerun_windows=[9])`. Before, a
study that refused over one window could only be run again whole, or as a
subset config that renumbered from `window_00`.

**A free energy is no longer recombined from windows that ran otherwise.**
Each window is unbiased with the centre and spring the Config gives it, and a
Config edited after the windows ran recombined them with the new ones,
shifting the curve with nothing to show it. Each window's own record is now
compared with the Config, and a window that differs is named instead.

### A failed study raises, when asked

**`explore(check=True)`** raises `StudyFailed` if any run failed, instead of
returning the failure among the results; **`RunResult.raise_for_status()`**
does the same for one run. The exception's refusal is the failed phase's, so
`exc.code` is what that phase recorded; `exc.failed` holds every run that
failed and `exc.results` everything the study returned. A script that did not
look at the results carried on as though a failed study had succeeded. The
default is unchanged until the next breaking release, when a failure will
raise by default.

### The GUI runs a study once, and CI runs supersede only their own kind

**`python -m fastmdxplora` runs the `fastmdx` command**, and the GUI starts
every study that way. It used `python -m fastmdxplora.cli.main`, which the CLI
package had already imported, so Python ran the module a second time as the
program, with every class in it defined twice, and a RuntimeWarning opened
every GUI-started run's log.

**A CI run cancels only a run of the same kind on the same branch.** A push
followed by `gh workflow run` cancelled the push's run with the one just asked
for, and a push near 03:00 cancelled the nightly corpus run.

**The container's build check names OpenMM's release and commit apart.**
OpenMM reports a release build as `8.4.0.dev-4768436` (its `release` flag is
never set), which read as an image carrying a development build.

### The equilibration log is closed when production begins

**A run no longer keeps `equilibration_energy.csv` open after it ends.** The
log was taken off the simulation when production began and "closed" through a
`close()` that OpenMM's reporter does not have, so the file stayed open until
the reporter was collected: one open file for every run of a study run in one
process. The suite, which was thought to need more than 1,024 open files,
passes in two halves at a limit of 1,024 with at most 69 open at once.

### A charged ligand's binding free energy says what the box does to it

**An umbrella study along `ligand_distance` now says when the ligand carries a
net charge.** Nothing corrects the free energy for the periodic box: the
ligand and the receptor interact with each other's images and with the
background that neutralises them, and to leading order that shifts the curve
by `k q_L q_R / eps * 2 pi (r_u^2 - r_b^2) / (3 V)` between the bound state and
bulk. The warning gives both charges, read from the prepared system, the
number for the study's box with water's dielectric constant, and the ions
there to screen it; `binding.reference.charge` in `pmf.json` records them. A
+1 ligand and a +6 receptor in a 190 nm^3 box, 0.9 and 2.9 nm apart, come to
about 0.9 kJ/mol. Averaged over directions, the estimate agrees with a direct
Ewald sum to within 5%.

### The molecule viewer and the charts are tested in a browser

**Stepping a frame in the viewer shows the next frame.** With Follow ticked,
as it is by default, the first press of the viewer's next-frame button loaded
playback at the newest frame, and "next" from there went nowhere; the
transport's own buttons stopped following and these did not. Stepping a frame
now stops following, as choosing a frame anywhere else does.

**`molecule-viewer.js` and `charts.js` run in CI.** Nothing had run them: the
browser tests covered the frame, the composer and the run builder, and every
check on the viewer read its source for a string. They are now driven in
Chromium on a study with a protein, a ligand, water, ions, a trajectory and an
energy log: the structure drawn without solvent, the water toggle, every style
and ligand control, playback stepping with the atoms moving, water as an
overlay during playback, and the charts' values and drawing, with any page
error failing the test. Every job also checks that each GUI script parses,
with Node.

### A ligand that leaves its site is said to, and its RMSD has no mean

**`ligand_rmsd` now measures the ligand's distance to the site it started in.**
Followed across the periodic boundary, as it has to be, a ligand that has left
has an RMSD that is the length of a path through solvent, growing without
bound however long the run, and its mean was reported as though it were a
pose. The closest heavy-atom distance to the site (the protein heavy atoms
within 0.5 nm of the ligand in the reference frame), by minimum image and so
bounded by the box, is now written to `ligand_site_distance.dat`. Where the
ligand is away from the site (no heavy atom within 0.6 nm), the findings say
from when and for what share of the frames, the figure shades those frames,
and no mean RMSD is given.

### A cached ligand is handed on with its hydrogens

**A component read from the chemistry cache without hydrogens is now
completed on disk.** Hydrogens were added to the text and the atoms counted
in it, but the file handed to the ligand path was the cached one as it was,
so a copy cached without them reached the force field as a bare heavy-atom
ring: benzene as a radical. The completed text is written back to the cache
whenever hydrogens were added.

The preparation of a ligand found in the structure (fetched, completed,
written per copy, and cached for the next study) and what setup says about
its source and parameters are now tested without the network. They were
covered only by the tests that reach RCSB.

### The package links the release's documentation

**PyPI's Documentation link now opens `/en/stable/`**, built from the newest
release tag, instead of `/en/latest/`, which is `main` and can describe
settings the installed release does not have.

### The report's analysis figures open from the report

**`report.md` now links each analysis figure from its own folder.** The report
is written in `report/` and linked the figures as `analysis/rmsd/rmsd.png`,
which is where they are from the study's root. Markdown resolves a link
against the file's folder, so a viewer showed a broken image for every
analysis, the PDF (rendered from `report/` as well) left them out without a
word, and the GUI's Report page asked for `report/analysis/...` and got a 404
for each. Only the summary figure, written in `report/`, ever appeared. The
links are now `../analysis/...`, worked out from wherever the report is
written; the reweighting figure had the same fault.

The captions read `rmsd` and `cluster: kmeans` rather than repeating the
analysis's name after a dash, and the default title is
`FastMDXplora Study: <system>`.

### Every scheme can be read

**Paper's cards are light.** The scheme redefined the ground and the text and
left the cards at the dark scheme's colour, so every card on a Paper page was a
near-black slab with near-black text on it: the health headline, the figure
captions and the chart titles measured 1.1 to 1.6 to 1. The status colours are
now shades of the same hues that read on white, and they reach the status
tokens, which were resolved on the root element with the dark scheme's
values; the scheme is now set there. The live charts draw in the scheme's
colours and redraw when it changes, where they had drawn the dark scheme's
grey axes on white.

A first visit takes the system's light or dark setting; a scheme chosen in the
settings is kept as before. Stages a run did not do are struck through rather
than faded to half opacity, and the viewer's frame label stays dark over a
white viewer background. Ten stylesheet rules named colours that were never
defined, and fell back to fixed dark-scheme values; they use the scheme's.

Each page is now opened in each scheme in a browser, and every piece of
visible text is measured against what is behind it: anything under 3:1 fails.

### A figure's caption gives the figure's number

**The captions under the analysis figures, and the dashboard's table, now give
the mean each analysis settled on.** They gave the mean of every row of the
data file, equilibration included, under a figure giving the mean after
equilibration with its error: an RMSD card read "avg 0.0157" beneath "mean
after equilibration 0.01297 ± 0.0021 nm". A caption now reads "mean 0.0130 ±
0.0021 nm after equilibration, 21 independent samples", to the precision the
error allows, and adds "too few to measure" under ten independent samples. A
series too short against its own correlation time says so in its caption and
its table row, where both had read as measurements. On a biased run a caption
gives the reweighted mean, or says there is no unbiased one. A study analysed
before findings were recorded keeps the plain mean, called "mean over all
frames"; RMSF's is "mean over residues".

### The Overview's preview shows the molecule

**The structure preview on the Overview is no longer a black box.** It framed
the prepared system when the page opened and kept that camera for every frame
after it, which is right for frames of the same system in the same place. The
engine writes its frames about its own origin, several nanometres from where
setup centred the system, so a finished study opened with the preview looking
at empty space. A camera that no longer frames the structure is set again; one
that does is kept, as before.

A peptide of fewer than eight residues is drawn with its atoms as well as its
cartoon, which has nothing to shape a ribbon from at that length. The viewer's
label named a live frame by its step, as "frame 6000" of a run that had written
a hundred, and flipped between that and the count of frames written as the two
updates arrived; it now reads "step 6,000".

### The frame gives the page the room it needs

**The GUI's page gets the width.** Opened on a finished study at 1440 by 900,
the log took a fixed 560 pixels on every page and left the page 640, and the
viewer's canvas got the 240 its information pane left over. The log is now
420 pixels wide until dragged, open while a study runs and closed once it has
finished, until it is opened or closed by hand, which is remembered. The
viewer's layout follows the width of its page rather than of the window: the
canvas takes the page, and the information pane goes beneath it when the page
is narrower than 900 pixels. The canvas is as tall as the window allows.

**The navigation is grouped and cannot be cut off.** The study's pages come
first (Overview, Viewer, Analysis, Report, Files), then the two ways to start
another (Agent, Config), under headings, and the block sits above the study's
progress: below it, a 900-pixel window hid "Files" under the settings trigger.

**A phone shows the page.** At 390 pixels the sidebar and the log each took
the screen. The study and its pages are now a bar across the top that scrolls
sideways, the log is closed, and the page is the screen.

A finished study no longer offers "Pause" (updates of a run that has ended),
and its last frame is marked "LATEST" rather than "LIVE". The study is named
by its structure file, "tri-ala" rather than the file's path, which stays on
hover. Simulated time reads "0.012 ns" rather than "0.012000 ns".

### An analysis is drawn from its numbers, and a point opens its frame

**The Analysis page draws each series from its data.** It showed each analysis
as the figure it wrote, a picture from which neither a value nor the frame
behind it could be taken. RMSD, radius of gyration, hydrogen bonds, SASA,
native contacts, ligand RMSD and RMSF are now drawn from the file the figure
was drawn from, with the frames the analysis left out as equilibration
shaded, and the mean of the rest with its error, or with no band where the
run is too short for one. Pointing at the line gives the value, the time and
the frame; the arrow keys move along it. The figure, drawn at publication
settings, is one click away on each card.

**Choosing a point opens that frame in the viewer**, when the trajectory
analysed is the one the viewer plays, and choosing a residue of the RMSF shows
that residue in the structure, drawn and labelled. A point's frame is worked
out as the analysis loaded the trajectory, with its stride and first frame,
and its time as the loader set it. A series longer than 4,000 points is
thinned evenly for drawing.

The Analysis page is drawn again only when its content changes; every poll
had replaced the cards and reloaded each figure.

### The Agent says what it can be asked

**The Agent's page opens on what can be asked of it.** It opened on an empty
thread reading "Nothing yet. Say what you want to run.", with no word of what
the Agent does, whether a model was set, or that it could be asked about the
study already open. It now offers questions about the open study (what it
found, whether the run is long enough, what would strengthen it) once the study
has run, and three studies to start: a protein in water, replicas compared, and
a binding free energy. A suggestion fills the composer rather than sending, so
it can be changed first. The page says what the chosen mode does, and where no
model is set, says so beside a button that opens the settings.

### The Overview's numbers say whose they are

**The atom count on the Overview is the system simulated.** It gave the
trajectory's count alone, 47 for a peptide simulated in 6,560 atoms of water,
beside a report saying 6,560. The card now gives the simulated system and what
the trajectory kept of it ("6,560, simulated; 47 kept in the trajectory"), and
the statistics table's row is called "Atoms in the trajectory".

**The live charts' axes do not go below zero for a quantity that cannot.** The
axis was the series with a margin either side, so the speed chart of every run,
whose first sample is 0 ns/day, was labelled -0.381. The phase and statistics
tables on the Overview, which had no style of their own, are laid out as
tables: headings over their columns, numbers aligned.

### The viewer's label and the preview's caption say only what is known

The viewer's label left a field with nothing in it as a dash, so a structure
with no run behind it showed a frame, an age and a simulated time with no value
in any of them; such fields are no longer shown. The Overview's preview said "The newest frame, as
it is written." under a study that had finished, and now says "The last frame
the run wrote." once the run has stopped. The BibTeX entry on the Cite page
wraps inside its card, where it ran out of it.

### An Agent's config is said as a plan

**The Agent's reply says what its config will do.** It showed the refusals the
Agent worked through and a row of buttons, and what it had written was behind
"Show the config", as YAML: that it would run for 2 ns because no length was
given, or build a dodecahedron, was learned by reading the file. The reply now
says it in lines, before anything runs: the system, force field, solvent,
conditions, equilibration and production, any umbrella windows, metadynamics
or pulling, replicas and other swept values, the analyses and any ceiling on
cost. Each takes the value the run will take, and those the config does not set
are marked as defaults. The lengths are resolved by the runner's own function
and the umbrella windows by the umbrella module's, so the plan cannot say
something the run would not do. A conversation reopened shows its plans again.

### The viewer answers the keyboard

Every control in the viewer was a button to find and click, so stepping
through a trajectory was a click per frame. Space now plays and pauses, the
arrow keys step a frame (ten with Shift), Home and End go to the first and last
frames, R centres the structure and F fills the screen. The keys work only on
the viewer's page and never while something is being typed, and are listed
under the page's title.

### A box grown for its cutoff has the ions it should, and a small solute gets one

**A box re-solvated to clear the cutoff no longer keeps the first attempt's
ions.** Where the box first built was too narrow for the cutoff, setup deleted
its water and solvated again with more padding, but the ions that attempt had
added stayed: they counted as solute, so the next box was sized by where they
had happened to land (the same study came out 2.28, 2.36 and 2.53 nm at its
narrowest), and they stayed in the system beside the second attempt's own. A
tripeptide at 0.15 M had 3 ion pairs where 2 were due. A retry now starts from
the solute as it was before any water. Studies whose setup log says
"Re-solvating" were prepared with the extra ions.

**The padding added is worked out from OpenMM's own sizing.** OpenMM sizes a
padded box as the solute's bounding sphere plus the padding, counted once, or
twice the padding for a solute smaller than that. The growth took the width to
rise by twice the shape's factor per nanometre of padding, which holds only
while the padding sizes the box; for any solute that sizes it the width rises
by half that, so the growth undershot by half and three attempts left the box
under the margin it aimed for. The rule is now applied directly and checked
against the OpenMM installed.

**A small solute is prepared with the defaults.** 1.0 nm of padding in a
dodecahedron with a 1.0 nm cutoff needs 1.56 nm of padding for a peptide of a
few residues; setup added at most 0.5 nm and failed with a message calling the
box's narrowest width its edge. Where the padding sizes the box, the box grown
to is the smallest the cutoff allows for any solute that small, and it is grown
to whatever the amount. Where the solute sizes it and more than 0.5 nm is
needed, setup stops adjusting as before, and the refusal that follows names the
padding that would run, for this shape and for a cube, and calls the box's
narrowest width what it is. The padding used is recorded under
`resolved` and the methods give it. They also say what padding measures: the
least distance between the solute and its nearest periodic image, which the
setting's description had called the distance to the box's wall. The builder's
advice uses the same rule and says whether setup will grow the box or refuse.
The guardrail corpus's box case now builds its box as OpenMM would, where it had
made the box a fixed fraction of the padding; its outcome is unchanged.

### A budget prices a study of several runs, all of it

**`budget_hours` no longer refuses every umbrella study, replica sweep and
campaign.** The staged route prepares a study, reads the particle count setup
recorded and prices the rest; it read the count from `setup/` beside the study,
which only a study of one run has. A study of several keeps each run's system
under `runs/<id>/` and an umbrella study's shared one under `shared_setup/`, so
setup ran and the study stopped with "Setup finished without recording how many
particles the solvated system holds", and `--autonomous`, which requires a
budget, could run none of them. Each run is now counted where the batch layer
prepares it, and the estimate is the sum over the runs, each window with its own
equilibration, plus the pull that seeds an umbrella study's windows where it
asks for one; the refusal says how many runs it priced. Each run then simulates
the system prepared for it.

**An umbrella study asked only to prepare prepares the one system its windows
share.** `include_phase: [setup]` prepared a system for every window, a box of
water per window, which is what sharing exists to prevent; and a study naming
`setup_from` whose windows are seeded from a pull began the pull, hours of
simulation, when it had been asked only to prepare.

### The test suite keeps to settings of its own

**Running the tests no longer leaves the machine measured as something it is
not.** A test recorded a calibration in the real settings directory, so a
workstation that had run the suite was measured as a CUDA machine doing a step
of 30,000 particles in 8.4 ms, and every budget and time estimate there was
priced on that. The suite now gives itself a settings directory and a cache
for the session, and each test a settings directory of its own, so the
person's calibration, chosen model, remote machines and fetched chemistry are
left alone. Delete `~/.config/fastmdxplora/calibration.json` on a machine that
has run the suite before and has not been measured since, or measure it again.

### The builder says what setup will build, and how long it will take

**Under the structure, while the settings are open,** the builder now says what
setup will build from it: about how many particles, in a box of what shape and
width, the padding setup will grow it to where the cutoff needs more (or that
setup will refuse it), the solute's residues, atoms and net charge, the ligands
kept, the water and the ions; how long the study will take on this machine,
every run of it, where the machine has been timed; and what is worth knowing
about the structure under these settings, each with a button that opens the
setting it is about. It is said again half a second after a setting changes.
The box, the count and the time had been learned from setup's log, minutes into
a run.

It is worked out, not built (`fastmdxplora.setup.estimate`). The solute is
counted from its residues' templates, hydrogens, missing atoms and the gaps
setup builds included, in the biological assembly setup will build, copies
from symmetry operators included; the box is OpenMM's rule and setup's growth
for the cutoff; the water is OpenMM's pre-equilibrated box less the volume
within 0.40 nm of the solute's atoms and what the cell's faces lose, both
measured on OpenMM's own builds; the ions are OpenMM's count for that water.
Against the setup phase it came within 1.8% on adenylate kinase (20,223
particles) and 3.0% on haemoglobin built from its symmetry operators (35,630),
and the tests hold it to the setup phase's own record of a decapeptide and a
tripeptide. `POST /api/preview-system` answers on this machine only, and a
PDB identifier is fetched once and kept.

### The Agent's plan ends with what the study would build and cost

**A proposed Config's plan now ends with the system setup would build and the
time here:** about how many particles in what box, the padding grown for the
cutoff where it would be, and how long the study would take on this machine,
every run of it, where the machine has been timed. The page asks for them once
the builder holds the proposal, as the builder's preview does, so a structure
named by its identifier is fetched once and the reply does not wait on it. A
proposal that prepares nothing, or whose structure cannot be read, ends where
it did.

### A study of several runs gives each run's mean with its error

**The report page of a study of several runs, before the comparison is
written, now gives each finished run's mean with its standard error and its
unit, and marks a mean the analysis said is not a measurement.** It read an
`uncertainty` and a `unit` that no analysis writes (its test's fixture had
written them, and the analyses write `standard_error`), so every mean stood
bare, and a series too short to measure stood among the results as though it
were one. The table now says the means are over the frames after
equilibration.

### The builder's estimate leaves out what setup discards

**The estimate of what setup will build no longer counts the heterogens setup
discards by name**: under `heterogens: auto`, the crystallization additives
(glycerol, sulfate, phosphate, buffers) and the salt of the liquor, keeping of
the ions only the metals that sit in sites, as setup does by their
coordination. Haemoglobin's phosphate had been listed among the ligands kept.

### Runs are compared on their equilibrated means, with their errors

**The comparison of a study's runs now uses the mean each run's analysis
recorded, over the frames after equilibration and with its standard error**,
where it took the mean of every frame, equilibration included, and gave none.
The trend plots carry the errors as bars; the report calls a difference
between the ends of a sweep a trend only where it is more than twice its
error, and says where the runs do not tell the ends apart, where it had said
"increases" or "decreases" whenever two numbers differed. A mean a run said is
not a measurement is marked. `comparison_summary.csv` adds each mean's
standard error, the frames discarded and what the mean is over. The overlays
are drawn against each run's own clock where every run recorded one, so runs
saved at different intervals line up; they were drawn against the frame.

**Replicas are compared as replicas.** A seed is no longer taken as a sweep
axis to plot a trend against, and a study whose runs differ only by seed gets
a section setting the spread of their means against the error each run
estimated for itself, with the verdict `members.json` has always held and the
report never showed.

### A first page load is answered whole

**The GUI no longer answers a first page load with a server error on whichever
route lost a race.** The routes import what they need when first asked, a page
asks for many things at once, each in a thread of its own, and the package's
imports go round in a circle (`fastmdxplora.analysis` imports every analysis,
each of which imports `analysis.plotting`); two threads importing into it
together were refused by Python's import locks with "deadlock detected". The
Agent's plan was found without its size and time that way. The server now
imports those modules before it serves anything.

### The builder draws the system in its box

**Beside the numbers, the builder now draws what setup will build**: the chains
it keeps, copies from the assembly's symmetry included and coloured as chains,
and the ligands, inside the periodic cell it will build, to scale and turned
off its axes so it reads as a solid. The cell is the shape the name means, a
cube, a rhombic dodecahedron or a truncated octahedron, worked out from the box
vectors OpenMM lays out (the points nearer the origin than any periodic
image), so how much water surrounds the protein is seen rather than read. It is
said again as the settings change, with the numbers.

### The Agent is told when a mean is not a measurement

**What the Agent reads of a study's analyses now says when an analysis
withheld its mean, and why**, and when it gave a mean it said is not a
measurement. A series too short to measure records no mean, and the Agent was
told nothing of it: asked whether the RMSD had settled it had neither a number
nor a reason.

### The GUI follows the system's light or dark until a scheme is chosen

**Until a colour scheme is chosen in Settings, the GUI follows the system's,
as it changes.** The scheme the system asked for on a first visit was stored
as though chosen, so a system that went dark at sunset left the page light for
good. Only a scheme chosen on the page is kept now.

### The viewer's transport sits under its canvas

**The trajectory's play, step and scrub controls now sit directly under the
viewer's canvas**, above the display options, where they had been the last of
five rows. A long path in the builder's note on where results are saved now
wraps instead of pushing a phone's page sideways.

### An Agent's reply restored before the builder is ready still works

**A restored Agent reply's actions and its plan's size and time no longer do
nothing on a slow page load.** The reply hands its config to the builder, and
the builder draws from its list of settings; where the config arrived before
that list, drawing it threw, so *Download config*, *Copy the command*, *Run
here* and the plan's last two lines silently did nothing. A config that
arrives first now waits for the list.

### A mean is recorded with its unit, and the Agent is told it

**Each analysis now writes its mean's unit beside it** (`unit` in
`findings.mean`, read from its own axis: nm for an RMSD, nm² for an area per
lipid, empty for a count or a fraction), and the captions, the table of a
study's runs and the Agent read it from there. The Agent was handed each mean
as a bare number, so a model asked for the RMSD had nothing to say whether
0.013 was nm or Angstrom, and a mean whose error was withheld reached it as
"± nan". The captions and the run table knew the unit only for five analyses
named in the GUI; an end-to-end distance or an area per lipid had none.
Studies analysed before this keep the units known for those five.

### An Agent's answer shows what the study recorded, and opens its figures

**Under an Agent's answer about a study, each analysis the answer names is
listed with the mean the study recorded for it**, with its error and unit, or
marked where the analysis said its mean is not a measurement; choosing one
opens the Analysis page on that analysis's figure. The value is the record's,
whatever the answer said, so a number in the prose can be checked where it is
read; before, checking one meant finding the figure and reading its caption.
The analyses are found by what the answer says, and the Agent is asked to name
the analysis behind each number it quotes.

### A withheld mean says how much longer the run must be, and what that takes

**An analysis that withholds its mean for want of sampling now records how
much longer the run must be** (`shortfall` beside the mean: further frames,
and nanoseconds where the run's clock is known), and the report's convergence
section, under **What would support it**, gives the figure for the study: the
largest ask, rounded up, what it would take at the speed the study ran (from
its `cost.json`), and the config that extends the study in place. The Agent is
given the same, with that config where the study can be continued. Each mean
ended "the remedy is a longer run" and nothing said how much longer.

`sampling_shortfall` asked for nothing where a run could not resolve its own
correlation time: the ten samples such a run appears to hold are an upper
bound, which is why its mean is withheld, and the shortfall counted them as
met. It now asks for at least as long again and says the figure is a floor
(`lower_bound`).

### A run is held to checks its plan states, and ticked against them after

**An Agent's plan now states the checks the run will be held to** (each
measure equilibrated, its correlation time measurable, at least ten
independent samples per mean, the temperature within 5 K of its target, the
potential energy's range per ns per atom), **and the report's convergence
section ticks each after the run**: passed, failed, or not judged and why. The
Agent is given the same ticks, so "did it pass?" is answered against what was
promised. The checks are one list, with the thresholds the report's findings
already used, so a check and a finding cannot disagree.

### The runs of a study are compared on one page

**The Analysis page of a study of several runs now shows the runs side by
side:** one table with the settings that differ between them and the mean
each run recorded, with its error (or marked where the analysis said it is not
a measurement); a difference from the first run is marked only where it is
more than twice the two runs' combined standard error, the rule the written
comparison uses for a trend, and replicas, which differ only by seed, are set
against the errors they estimated for themselves instead. Under it, one
measure's series from every finished run, overlaid, in a palette told apart
by the colour-blind. The page had been empty for such a study: each run's
figures were inside the run, and the comparison was a document written once
the last run finished. `GET /api/runs-compared` gives the table, and
`/api/series` takes `run=ID` for one run's series.

### The viewer has one transport and one toolbar of named icons

**The viewer's thirty text buttons in five rows are now a transport and a
toolbar of icons.** The transport under the canvas steps, plays, goes to
either end and plays backwards, its play button showing what pressing it does;
the view's icons reset it, zoom, spin (one button now, where Spin and Stop were
two), fill the screen and save a picture; how the molecule is drawn and
coloured are two lists. Every icon is named to a screen reader and in its
tooltip, with its key where it has one, and shows where the keyboard is. A row
that repeated three of the transport's buttons is gone. The pocket cutoff,
the one place the viewer's angstroms meet the analyses' nanometres, is said in
both.

### The file picker says a path it cannot open as text

**The file picker put the server's refusal into the page as markup**, and the
refusal names the path it was asked for, which can come from a config written
by somebody else: a field holding `nowhere<img src=x onerror=...>` ran the
script when its picker opened. It is shown as text now, as is the builder's
reason for offering no analyses. Found by driving the picker and the Agent's
send path in a browser, which no test had done: a question typed and sent, a
file attached through the picker, the answer with what the study recorded,
and the thread kept with the study.

### The GUI is drawn from one set of tokens

**Every size on the page is now one of eight on a scale, every tint is mixed
from the scheme's own accent, and one rule rings whatever the keyboard is
on.** There were eighteen font sizes, the smallest (10px) on the uppercase
labels and file actions, where reading was hardest; those actions are now
sentence case at the size of every other control. Ninety-seven hover,
selection and active tints were written as the dark scheme's colours, so on
Paper, whose accent is blue, they were all still cyan. A notice appeared on a
fixed near-black carrying the scheme's text, which on Paper is near-black too;
and three buttons' sizes never applied, their `font` shorthands made invalid
by an `inherit` inside them. The contrast check in every scheme now reads the
mixed colours it had been skipping.

### Clusters are drawn in the figures' own palette

**The cluster figures now take their colours from the plotting module, as
every other analysis figure does:** Okabe and Ito's palette in colour, and
greys with a shape for each cluster when greyscale is asked for. They had
Tableau's ten colours written in, and came out in colour in a greyscale
report.

### A clicked atom is given as a selection

**Clicking an atom in the viewer now gives, on the Selection tab, the
selection for its residue and for the atom** (`chainid 0 and resSeq 189`,
`... and name CA`), each checked against the topology the analyses read and
with a button to copy it. The tab gave the residue, chain and atom's names and
left the selection to be written by hand, in a language where `resid 189` and
`resSeq 189` name different residues in most deposited structures. A residue
the analyses do not read (water, unless it is saved with the trajectory) is
said to be missing from it. `GET /api/selection` answers the same.

### `fastmdx diff`: what two studies were asked to do differently

**`fastmdx diff FIRST SECOND` lists the settings two studies, or two Configs,
differ in** (`--json` for a program). A study is read from its
`resolved_config.yml`; a phase setting either side leaves out is taken at its
default, so a short Config and the full one it resolves to compare as the same
study; and a path inside a study's own folder is said from it, so two studies'
trajectories do not differ for being in two folders. It exits 1 where they
differ, so it can gate a script.

### A residue can be given the protonation state asked for

**`setup.residue_states` gives named residues the protonation state asked
for, in place of setup's own choice:** `{A:57: HIP}` charges trypsin's
catalytic histidine; histidine takes HID, HIE or HIP, aspartate ASH or ASP,
glutamate GLH or GLU, lysine LYN or LYS. Setup chose every state from the
residue's usual pKa at the pH, and a histidine's tautomer from its hydrogen
bonds, with no way to say otherwise, which for a catalytic or metal-binding
histidine is wrong as often as not. A residue the structure does not hold, or
a state it cannot take, stops setup with a named refusal; what was set is in
the setup record's notes and in an Agent's plan.

### An extended study counts its first piece whole

**A study extended more than once no longer loses production from its count.**
A checkpoint from before the step counter was understood could carry a
whole-run step, and a step past the plan's production was read as one and had
the equilibration taken off it. Once a study had been extended, the plan it was
read against was the one the join had written over the study's own, with the
last piece's length: a first piece of 300 production steps, extended by 50, was
counted as 100, and the study's production went down as it grew, so an
extension to a total asked for too much. Only a sidecar that does not record
its trajectory's interval, which every checkpoint since has, is converted now.

### A study can run until what it is for is known

**`simulation.stop_when` runs a study until the measures it names are known as
well as asked, or a ceiling is reached, rather than for a length fixed before
anything was known:** `{measures: [{analysis: rmsd, standard_error: 0.01},
{analysis: sasa, relative_error: 0.05}], max_duration_ns: 50}`. `duration_ns`
is the first piece; after each, the analyses are read, and every run is
extended by what the numbers ask for (a withheld mean its own shortfall; a mean
with an error enough frames for the error to fall to what was asked, bounded
per round), joined and analysed again.

**By default the runs must be replicas, and must agree.** A run trapped in one
state settles and shrinks its error bar all the same; only runs started
independently can show it. So the rule asks for one system swept over
`random_seed`, is met only when the replicas' means agree within their own
errors, and judges the larger of the error they claim together and the error
their spread shows. `independent_starts: not_required` accepts one run's own
precision, and the record says what that leaves unchecked. A study that cannot
keep the rule (no replicas, a ceiling below the first piece, a method that
cannot be run in pieces, an umbrella study, a measure no analysis in it
computes) is refused before anything runs, in the plan as in the run.

Each round is recorded in `stopping.json`; the report, or for replicas the
comparison report, says how long the study ran and why, round by round; the
Agent is given the record; the plan says when the study stops; and a budget
prices it at its ceiling. In a parallel study the runs are extended side by
side.

### The Agent says what "done" means before the run

**Asked for a quantity to a precision, or to run until something is known,
the Agent writes the study's stopping rule** (`simulation.stop_when`): the
measures, the error each must reach, replicas over the seed and a ceiling,
shown on the plan's "Stops when" line before anything runs and judged by the
code after it. It is told which analyses record a mean a rule can judge,
and to present a precision it chose as its own choice, not the person's.

**A stopping rule the study cannot keep is now refused when the config is
validated**, not only when the study starts: `fastmdx check-config`, the
GUI's check and the Agent's repair loop see it, and the Agent repairs a rule
without replicas by adding them. A measure whose analysis records no single
mean (`rmsf`, `cluster`) is refused with the list of those that do; before,
it ran to its ceiling judging nothing. A mean that is never recorded during
a run stops the rule at once, since more production cannot supply it. One
system given to `FastMDXplora()` without a config is refused a rule when it
is constructed, before anything is created.

### A study running until it knows shows whether the answer is settling

**The Overview of a study with `simulation.stop_when` has a card, "Running
until it is known", that answers the question the rule asks.** For each
measure: the standard error after each round against the error asked for,
with the production at which it would reach it if it keeps falling as one
over the root of the frames (the estimate the next piece is sized by); the
mean after each round with every replica's own mean and error beside it, so
replicas that disagree are seen as well as said; the piece now running and
its time at the speed the study has run; and every round with what was
decided. It is read from the study's record (`GET /api/stopping`), so the
page and the report agree, and it asks again only while the study runs.

The record is now written before the first piece runs, so a study says what
it is running until from its start; a resumed study keeps the rounds it had;
and the record says how many runs were extended at once, which is what a
round's time rests on.

### The page is told when the study changes

**The GUI now hears of a change as it happens, instead of asking for
everything every three seconds.** It asked for seven answers a poll from each
open tab whether or not anything had changed, and was still up to three
seconds behind the run. `GET /api/stream` is a server-sent event stream: the
server looks at the study's files (their times and sizes, three folders deep,
twice a second) and the run's state, and sends one event when any of them is
different; the page then asks for what it draws, once. What the run writes
appears within about a second, and an idle page asks for nothing. The poll
stays as a fall-back, every thirty seconds while the stream is open and at
the refresh setting where it cannot be. Only that something changed is sent,
never what, so every answer still comes through its own route and checks; a
stream ends when the server shuts down, so stopping the GUI is not held by an
open tab.

### The builder asks what a study is for

**`simulation.stop_when` is a form in the builder, not YAML in a box.** It was
drawn as a mapping box whose placeholder read "measures: [object Object]".
Now each measure is chosen from the analyses that record one mean, with the
error it must reach in its own unit (named: "to ± nm") or as a percentage of
its mean; then the most production any run may reach, and whether replicas
must agree. Where they must and the study has none, one button sweeps the
seed over three values; unticked, the form says what one run's precision
leaves unchecked. A row being written is kept until it is whole, and only
whole rows reach the config.

The unit each analysis records is now known by name for all of those that
record a mean (end-to-end and pair distances, ligand RMSD, area per lipid,
bilayer thickness, moments of inertia, coordination number), as their own
axes state it, for the form and for studies analysed before units were
recorded.

### Equilibrated, converged or determined, never "settled"

**What the software says now uses the physics' own words.** "Settled" is not
a physics term, and it was standing for three that are: an observable
*equilibrates* (the structure, the solvent, a bilayer's area, a window at
its restraint), a bias or a surface *converges*, and a mean or a protonation
state is *determined*. The statistics had dropped the word already; it
lingered in the Agent's instructions, two refusals (a run whose segment means
drift "had not equilibrated at the scale of the whole run"), the log, the
builder, the Overview, the documentation, and two cases of the guardrail
corpus, now "a run that never equilibrated" and "a run that equilibrated,
transient included". A study with a stopping rule runs until each measure is
*determined* to the precision asked ("Determined as asked", "Running until
it is determined"), and a run trapped in one state is said to look
equilibrated there, which is the trap. The `Settled` alias and the `settled`
key in older records are still read.

### A preparation can be repeated

**Preparing one structure twice now gives one system, not two.** OpenMM's
Modeller places the hydrogens it adds at random before minimising them, and
picks at random which waters become ions, both from Python's `random`, which
setup never seeded; the hydrogens set the solute's extent, and the box and
its water follow. Six preparations of a decapeptide came out at 4,265 to
4,556 atoms in boxes 2.855 to 2.916 nm wide. `setup.random_seed` now seeds
those choices; where it is not given a seed is drawn, recorded in
`setup_parameters.json` and written into `resolved_config.yml`, so every
preparation can be repeated from the study it produced. Setup's minimisations
run on one CPU thread (several threads add forces in whatever order they
finish, which moved a hydrogen by 0.03 Angstrom and, once in four, the water
by nine atoms), the velocities written to `state.xml` are drawn from the same
seed, and Python's own random state is put back afterwards. The same seed
now gives the same atoms at the same positions; a bilayer, which OpenMM packs
with dynamics of its own random stream, is the exception and is said to be.

The methods paragraph states the seed. A sweep over `setup.random_seed`
counts as replicas, and runs that share a setup seed are no longer warned
that they will be prepared differently. The claim that "solvation does not
place water the same way twice" is corrected wherever it was made: it was the
unseeded hydrogens and ions. A test of setup's size estimate that measured
this spread as much as the estimate (it failed once, 4,406 against 4,619)
now prepares from a fixed seed.

### A study that stops says what would fix it, and what the fix costs

**When a study or `fastmdx resume` stops, the console prints what would fix
each thing that stopped it, the command that runs the fix, and how long that
takes at the speed the study itself ran.** A stopped run, and runs a stop
kept from starting, are one `fastmdx resume` priced at what remains of each.
Umbrella windows that recorded too few values are run again with
`--rerun-window` at the length that gives the thinnest the values it needs;
windows that ran with settings the config no longer gives are named to run
again; a window that failed is run again with every window it kept from
starting, which `--rerun-window` otherwise refuses; gaps between windows get
the design the windows measured, as a config. Any other refusal is answered
within what the registry lets it say: a setting's values where the schema
holds them all, the setting alone where the value is a judgement, the install
command where there is one, and, where only the person can decide (a
ligand's protonation), where that choice is recorded and never what it
should be.

The price is production and equilibration in nanoseconds and the wall time
at the study's own recorded speed, or another run's of the same study; with
no speed measured, no time is given. The Agent's run status carries the
same, and it answers "why did it stop, and what now?" from it;
`fastmdx resume --json` carries it as `remedies`; `fastmdxplora.remedies`
gives it to a program.

### A residue's protonation state is chosen from the structure in the builder

**`setup.residue_states` has a control of its own in the builder,** in place
of a box for YAML that needed the chain, the number and the state's name
known beforehand. The preview of what setup will build now lists every
residue of the structure that can take another state (histidine, aspartate,
glutamate, lysine) with the states each takes, and the builder offers them by
kind, each state with what it is ("HIE, neutral, hydrogen on NE2"). Where a
structural metal is within 3 Å of a residue's side chain, the atom and the
distance are said beside it ("NE2 is 2.1 Å from ZN A:301"), a fact about the
structure; the state is left to the person. Each histidine is marked in the
picture of the system and a click on one adds it and asks for its state;
residues given one are marked with it. A row reaches the config once its
state is chosen.

### A refused setting is said on its own field in the builder

**When the builder's config is refused, the refusal is said on the field of
the setting it is about, with what would fix it.** It was one line under the
buttons, with the setting folded away in a closed section further up the
page. The refusal now reaches the page with the setting it names and its fix
(`fastmdxplora.remedies`), from showing, downloading or copying the config
and from **Run**; the section is opened, the field outlined and focused, and
the fix is said within what the refusal may say. Where the schema holds a
spelling near what was given ("did you mean 'dodecahedron'?"), a button
offers it and nothing changes until it is pressed; changing the field takes
the refusal away. A number outside what a quantity can be now records the
block it was in, so its field can be found.

### The report says a seeded preparation repeats

**The report's Reproducibility section still said that solvation could not be
seeded and that running the configuration again gave a different atom count,
after 1130 made the preparation repeat.** It now reads the study's records:
where setup recorded its random seed, a rerun of `resolved_config.yml` is said
to prepare the same system, with the seed and whether it was drawn, and a
bilayer, which OpenMM packs with a random stream of its own, is named as the
exception; a study prepared before the seed was recorded is still said to give
a slightly different atom count. The dynamics are said separately: where
`simulation.random_seed` was given a rerun starts from the same velocities,
and whether it repeats step for step depends on the platform, since OpenMM's
GPU platforms can differ in the last bit of a force unless asked for
deterministic forces; where none was given, a rerun is a new trajectory of
the same system.

### Each figure says what made it, and the command that makes it again

**A chip at the foot of each figure on the Analysis page names the release
that drew it, and opens what made it:** the packages whose versions decide
its numbers, when it was made, the trajectory and how many frames it rests
on (and every how many), the selection and the options, and the command that
draws it again. All of it was recorded (the analysis manifest, the
analysis's `options.json`, the Manifest's record of who produced the phase)
and none of it was shown, so a figure copied into a paper left its study
behind. The command reruns the one analysis over the same frames, with the
same selection and options written out rather than left to defaults, into a
folder of its own beside the study; it is the command line's own rendering,
and a test runs it and gets the same numbers byte for byte. Where a setting
has no flag, the config is given instead; a figure made by another release
says so.

### A figure is drawn at the width a journal prints it

**`analysis.figure_width` draws every figure at a journal's column width,
with its type sized for it:** `single_column` (89 mm, 7 to 8 pt type, lines
and ticks thinned to match) or `double_column` (183 mm), beside `page`, the
6.5 in the figures have always been drawn at. A page-width figure scaled into
one column took its 9 pt ticks to about 5 pt, below what most journals accept.
The figure is drawn by the analysis itself at that size, so nothing is
rescaled after the fact, and an analysis's own proportions are kept. The
width is recorded in the analysis manifest, and the chip on each figure (1135)
gives the command that draws it again at one column or two; a test draws the
RMSD at one column from that command and gets a figure 88 mm wide over the
same numbers.

### The Agent looks with the software's own tools before it answers

**The Agent may look before it replies**, up to four times: a reply that
opens `USE: <tool>` runs the tool and the model is asked again with what the
software said. `inspect_structure` gives a structure's chains, protein
residues, ligands, ions and water, the residues whose protonation state a
study may set and any side chain by a structural metal; `preview_setup` what
setup will build from a Config and how long the study takes here;
`check_config` whether the validator accepts a Config, and if not what would
fix it, and if so the plan; `check_selection` what an MDTraj selection
matches; `read_study` another study's record (its Config, findings, checks,
rounds and fixes), so "compare this with last week's run" is answered from
both records. It wrote sizes, times and chain names from what it was given, which
for anything the software measures meant guessing. It is told to look rather
than guess and to quote the software rather than contradict it. The tools
only look, a look is not an attempt, and hosted they read inside the
workspace only; a tool reads a structure or a study's record and nothing
else. What it looked at
is folded under its reply as **Checked with the software** and kept with the
thread, and `fastmdx agent` prints a line for each (`fastmdxplora.agent.tools`).

### A fix the software runs is run from the Overview or by the Agent, when asked

**A study that stopped has a "What would fix it" card on the Overview**: each
thing that stopped it, its fix, the command and what it costs at the study's
own speed (1131). A fix that is this software's own command, `fastmdx resume`
or windows run again with `--rerun-window`, has a **Run it** button, which
asks once more with the price in view before anything starts, and the run is
followed on the Overview. Told to carry the fix out (*resume it*, *rerun those
windows*), the Agent replies `DO: run the fix`: the first such fix is shown
with its command and price, and runs when the person says yes. The command's
arguments are the ones the remedy built from the study's record, never text
from a request or a reply; a fix waiting on a choice only the person can
make, a setting to change and an install command are said and never run.
`GET /api/fixes` and `POST /api/fix` are answered on loopback only.

### The figure tests leave the logger as they found it

**A test module's fixture that ran the command line left the package logger
unable to propagate for every test after it** (1135's figure tests), so a
later `caplog` assertion read nothing: the suite's own restoring fixture takes
its baseline after module fixtures are set up, and took the command line's.
The fixture now puts the logger back itself, and a test says so. Found by the
full suite, where a test of the command line's logging failed after it.

### The GUI imports the modules this round's routes use before it serves

**A test of a first page load, seven requests at once, answered 500 to one
of them once in a full run under load** and passed in 24 bursts of eight
fresh servers since. The modules 1131-1138 added to what the routes import
on first use (`fastmdxplora.remedies`, the figure chip's, the fixes' and the
Agent's tools, and the command line's parser the chip consults) are now
imported ahead with the others, as 1104 did for the analyses, since two
first requests importing them together can meet in the package's circular
imports. The test now names the route that answered 500 and what it said,
so a recurrence says where to look.

### A refused Run here in the Agent says what would fix it

**A Config the Agent's *Run here* refused came back as the refusal alone**,
while the same refusal in the builder carried its fix (1133). Every coded
refusal of *Run here* now carries its remedy, shown under the button: the
setting to change, or the install command where OpenMM or another package is
missing (that refusal is now coded `environment.backend.missing`). Where the
fix is a setting of the study, **Ask the Agent to fix it** sends the refusal
into the thread as the person's next message and the Agent rewrites the
Config; a budget, an install and a choice only the person can make are said
and not handed to the model. An `autonomous` Config that carries its own
`budget_hours` was refused for the Settings field being empty; it now runs
with the Config's ceiling, the field taking precedence where both are given.

### A window runs again at a force constant the person names

**Holding one umbrella window harder meant writing `force_constant` as a list
by hand**, every other window at the constant it ran with, before
`--rerun-window` would run it again. `--rerun-force-constant K` does it: the
windows `--rerun-window` names are held at K and every other window keeps its
own, and the study's `resolved_config.yml` records the list, so the next
recombination and the next window run again from it read the springs the
windows ran with. The first Config, which still gives the window its old
spring, is refused by name rather than recombined with the wrong one.
`windows_held_at` in `fastmdxplora.simulation.umbrella` gives the same Config
from Python.

### Windows run again from the GUI at the values the person names

**Told "rerun window 3 at 6000" or "windows 2 and 5 again for 4 ns", the
Agent replies `DO: rerun windows 3 at 6000`**, and the software does the rest:
it reads the windows and the numbers from that one line by a strict pattern,
checks them against the study (its own windows, positive numbers), builds the
command from the study's record (`--rerun-window` with `--rerun-force-constant`
or `--simulate-duration-ns`), and asks the person with the spring in its unit
and the price at the study's own speed. `POST /api/fix` takes the windows and
values as well as a fix's number, on loopback only; the Agent uses only the
values the person gave, and asks for one it was not given. A window run with
settings the config no longer gives is now said in its variable's units
(rad and kJ/mol/rad^2 for a torsion, where it said nm).

**A fix was priced with equilibration and production the runner does not
run.** The price read a run's lengths with defaults of its own: a run naming
no length was priced at no production (the runner runs 2 ns), and
equilibration at 1.5 ns whatever the timestep (the runner's is a number of
steps: 3 ns at 4 fs). It now reads them through the runner's `plan_stages`.

### Looking at a structure says when setup builds more chains than the file

**The Agent's `inspect_structure` gave the file's chains and residue count
beside the assembly's titratable residues without saying which was which**:
for 1HHO, chains A and B and 287 protein residues beside 38 histidines, twice
the file's, since setup builds the tetramer the file declares. It now says
that setup builds the biological assembly, names its chains, and says the
residues listed are the assembly's. Found writing the questions that measure
the Agent's looking.

### Whether the Agent's looking helps is measured, not assumed

**`python -m fastmdxplora.validation.agent_looks`** asks the configured model
six questions the software answers itself (a solvated system's size, a box's
width, a structure's residues and ligands, the atoms a selection matches, the
histidines of the assembly setup builds), with the tools and without, and
counts the replies that agree with the software and how many looked with the
tool each question calls for. The questions, the tolerances and how a reply is
judged are fixed in `preregistration/agent-looks.md` before any reply was
seen. Every reply is kept in the output file.

### The Report page's figures say what made them

**The chip 1135 put on each Analysis figure is now under each of the same
figures on the Report page**: the release, the packages, the frames, the
selection and options, and the command that draws it again, with the two
column widths. One chip and one panel, from the same record
(`figure_provenance`, now also in the report's payload).

### The viewer measures a distance, an angle and a dihedral

**The viewer said where a clicked atom was and nothing about how far it was
from another.** With **Measure** on (the ruler in the view tools, or M), two
atoms clicked give their distance, three the angle at the middle one and four
the dihedral about the middle bond (MDTraj's sign), drawn in the structure
and listed in the Selection tab in angstroms and nanometres or degrees. They
are measured in the frame on screen, as drawn, and follow the trajectory as
it plays; Escape clears them. Two atoms can be measured over every frame:
**Over every frame** gives the command that runs `pair_distance` over the
study's trajectory, with the minimum image, into a folder of its own, from
the atoms' selections, each checked to name one atom
(`GET /api/measure-over-frames`). Run, it writes MDTraj's distance at every
frame.

### A plan says a membrane's box and a ligand named by its residue

**The plan said a membrane study would be built in a dodecahedron**, the
`box_shape` default, which setup does not use for a membrane: its box is
rectangular, built from whole patches of bilayer. It now says so. **A ligand
named only by its residue in the structure** (`setup.ligand_name`, with no
file) was not in the plan at all; it is now said, and a file and a residue
name together are said together. Found writing the builder's starting
points.

### An umbrella study opened in the builder keeps its umbrella

**A Config handed to the builder as a mapping** (the Agent's reply, through
its Download config, Copy the command and Open in the builder) **was checked
in place**, and the validator expands an umbrella block into a run per window
in place, so the form was filled from the first window. The umbrella was gone:
what was downloaded, copied or run from the builder was one plain run of the
structure. It is checked on a copy now, as a Config read from a file always
was, and what the builder writes runs every window with its centre and force
constant. The Agent's own Run here was not affected: it runs the Config it
wrote. Found writing the builder's starting points.

### The builder offers studies to start from

**A first study began at an empty form.** The Config page now opens with
**Start from an example**: a protein in water, a protein and its ligand, a
membrane protein, a study run until a quantity is determined, and a free
energy along a distance. Each is a complete Config the validator accepts,
following a recipe on the examples page and naming a real structure so it
runs as it stands; each tile says what it is for and, from its plan, what it
will run. Chosen, it is loaded into the form as any Config is, and the note
says what to change first. Folded, the gallery stays folded
(`gui/starters.py`, in the schema payload).

### Every study in the workspace, and two compared

**The GUI showed one study at a time**, and another was opened by walking to
its folder in the picker. **All studies**, at the top of the sidebar, finds
the studies under the workspace, or a folder chosen, and says each in a card:
its structure, the kind of study (one run, replicas, a sweep, umbrella
windows, a trajectory analysed), where it stands (completed, running,
stopped, failed), when it began, the means it recorded with their errors,
whether an umbrella study's free energy was recombined or refused, and a
figure it drew. Search narrows the cards as typed; **Open** makes one the
study on screen. Two chosen are **compared**: the settings in which they
differ, defaults filled in, as `fastmdx diff` says them, and the means both
recorded, a difference marked resolved only past twice their combined
standard error, as the Analysis page marks runs of one study. Everything is
read from the studies' records (`gui/workspace.py`); the routes are loopback
only, as browsing folders is, and inside the workspace when hosted. The page
scripts are now told when a page opens (`navigate`), which the Report page
already listened for and was never sent.

### A browser test waits for a chart as long as the others

`test_the_charts_redraw_in_the_new_scheme` waited 30 s for the temperature
chart's first value, the only browser test left at that; beside a full suite
on two cores it took longer once. It waits 60 s, as the rest do.

### The Agent's reply is shown as it is written, and can be stopped

**A reply came back whole**: the thread said "Thinking" for as long as the
model took, a config or a long answer included, and nothing stopped it but
closing the page. The model is now asked for a stream (content-block events or
the OpenAI chat shape, so a local or compatible server streams too), and the page
is sent each piece as it arrives, each look the Agent takes as it takes it,
and at the end the same answer as before (`POST /api/agent/propose-stream`,
one JSON event a line). While a reply is written the send button stops it:
the request to the model is closed with it and nothing it had written is
kept. A browser without streams asks for the reply whole.

### A residue clicked in the viewer offers its states for a new study

**The builder's preview offered each residue's protonation states before a
run (1132); after one, the viewer did not.** A histidine, aspartate,
glutamate or lysine clicked in the viewer now offers its states in the
Selection tab, each with what it is, and says what this study set it to or
that setup chose. Choosing one opens the Config page with this study's
Config and that residue in `setup.residue_states`: the residue is named as
setup builds the structure (its chain and number there, whatever the force
field renamed it in the trajectory), and what tied the Config to this study
(its output, the prepared system it reused, the segment it carried on, who
wrote it) is left out. Nothing runs until the person runs it
(`GET /api/residue-states`).

### A model of an ensemble is prepared by name, and the models are starts

**An NMR entry deposits its molecule as an ensemble; setup prepared the first
model and said nothing of the rest.** `setup.model` names the model to
prepare, as the file numbers them, and a model the file does not hold is
refused with the numbers it does. Unset on a file of several, setup's record
and the builder say how many there are. Swept, the models are replicas that
start from different structures: the plan says so ("3, from models 1, 8, 15
of the ensemble"), a stopping rule counts them as the independent starts it
asks for (replicas over a seed share one structure and test trapping only as
far as their dynamics carry them apart), and the comparison, the Analysis
page and the Studies page treat them as replicas. The builder's estimate reads
the model named (`fastmdxplora.setup.ensemble`).

### Two browser tests wait for what they read, as CI showed they must

CI on `1bd10e9` failed two browser tests on timing alone. The contrast test
read the Overview while the loading screen was fading out, so its text was
half transparent over the page and read as unreadable; it now waits until the
loading screen is gone. The citation test gave a smooth scroll 15 s where the
other browser tests allow 60; it allows 60.

### What a trajectory gives is analysed, computed or determined

A simulation is not an experiment, and what the software said of its results
now uses the words of the field. A mean the analysis withholds is "not
determined" (it was "not a measurement") in the report, the Analysis page,
the comparison of runs, the Studies page and the Agent's answers. A stopping
rule's entries are quantities, the builder's section of analyses is
"Analyses", and the convergence checks read "each observable equilibrates
before it is averaged" and "each observable's correlation time is resolved by
the run". Refusals, warnings and the docs say computed, analysed, sampled,
defined or resolved where each is meant. "Measured" stays where it is the
right word: a machine's speed, an experimental order parameter or structure,
the viewer's ruler, and record keys such as `not_a_measurement` and
`stop_when.measures`, which keep their names so existing studies still read.

### The GUI imports everything its routes reach before it serves

CI on macOS with Python 3.13 answered 500 to `/api/schema` in a first page
load of seven requests at once. The schema route imports the builder's
schema, and through it `setup.pdbfix`, `simulation.stopping` and the
analysis descriptions, only when first asked, while the builder's preview
imports into the same `setup` package: two threads importing into the
package's circular imports together is refused by Python as a deadlock. The
server imported a hand-kept list of modules ahead to prevent exactly this
(1104), and the list lacked the schema route's. The list is now read from
the source (`gui/route_imports.py`): every module of the package the server
imports, at the top of a file or inside a function, followed to the end, 178
of them, found as files and read as text so that finding them imports
nothing. The GUI starts in the same time as before. A failing route now logs
its traceback at debug level, and the concurrent first-load test shows it.

### The starters test reloads once the fold is kept

The builder keeps the starters card's fold when the card's toggle event
fires, a moment after the click. The test clicked and reloaded at once, and
under load the reload came first. It now reloads once the fold is kept.

### A module is found by its name as Python spells it

CI on macOS failed on `e0e5829`, every Python: the GUI's list of modules to
import before serving (1158) held `fastmdxplora.agent.Queue`, which is a
class that `from fastmdxplora.agent import Queue` names. macOS's file system
ignores case, so looking for `agent/Queue.py` found `agent/queue.py`, while
Python's import, which does not ignore case, refused the name. Module files
are now matched by exact name, folder by folder. A test makes the file system
ignore case as macOS's does, on any platform.

### The default stage lengths are times: 500 ps, 1 ns and 2 ns

The defaults were step counts, 250,000 steps of NVT, 500,000 of NPT and
1,000,000 of production. At the default 2 fs timestep those are the 500 ps,
1 ns and 2 ns every text said; at 4 fs, which hydrogen mass repartitioning
allows, they ran 1 ns, 2 ns and 4 ns, while the plan, the methods paragraph
and the Agent said 500 ps and 1 ns. They are times now (decided 2026-09-30),
kept once in `simulation/lengths.py`, and the steps follow the run's
timestep. The runner, the cost estimate and the ensemble a config implies
each kept their own copy of the step counts, and the continuation of a
stopped study already counted times, so at 4 fs it and the runner disagreed;
all four read the one set now. A study at 4 fs that states no lengths
equilibrates for half the steps it did, and produces for half; a study that
states its lengths, in steps or in time, runs as it did.

### A ligand in a structure given by identifier needs no file

Asked for benzene in T4 lysozyme L99A (181L), the Agent looked at the
structure, was told "BNZ, HED looks like a ligand and has no chemistry",
asked for a benzene file and whether to leave out HED. Setup needed
neither: for a structure given by PDB identifier, `heterogens: auto`
fetches each ligand's chemistry from the entry and discards
crystallisation additives (HED) and ions from the liquor by itself. The
advisory checked for an identifier in the path it was given, and every
caller gave it the file the identifier had been fetched into, so it said
the same in the builder. It is not said now of a structure given by
identifier, nor of a study's prepared system. The Agent's look at a
structure says what setup does with each heterogen, and for a ligand to be
simulated, that setup fetches its chemistry from the entry, or, for a
structure given as a file, that its chemistry must be given. The help for
`heterogens` and `ligand`, which the Agent reads, says where the chemistry
comes from, and the advisory is plural where the ligands are.

### A link in a reply ends where the address does

The Agent wrote "save https://files.rcsb.org/ligands/download/BNZ_ideal.sdf."
and the page linked the address with the sentence's full stop inside it,
which is not a file RCSB has. A full stop, comma, colon, semicolon or mark
at the end of an address is now left after the link, except the semicolon
of an escaped character, which is the address's.

### The first run of the Agent's looking, recorded

Run on 2026-09-30 with three repeats: 18 of 18 replies agreed with the
software with tools, all 18 having looked with the tool each question
calls for, and 18 of 18 without. By the pre-registered rule no question
separates the arms. The replies show why: these are well-known entries,
and their counts are within what the model knows; without tools it gave
the two sizes as ranges and said each count was from memory. The result
is added to `preregistration/agent-looks.md` below what was registered,
which is unchanged, with what a set that could separate the arms needs.

### A report stays shown through a failed fetch

CI on `e0e5829` (Ubuntu, Python 3.11) failed waiting for a figure's chip to
be visible on the Report page. The page asks for the report several times
as it opens (on loading, on being shown, when the study's state changes). A
fetch that failed after one had succeeded hid the document, and the next
success found the same text already rendered and returned, so the report,
its figures and their chips stayed hidden until the report changed. A
failed fetch now leaves a report already shown, and one that fails before
any has succeeded is tried again, up to five times, further apart each
time: a finished study's state does not change, so nothing else would ask.
A test fails the fetches in a browser, and fails on the old page.

### A figure is plotted, and a molecule rendered

What the software said of its figures and the viewer now uses the field's
words. A figure, a curve, a surface or a chart is plotted: the chip under a
figure says "Plotted for" its width and gives the command that plots it
again, an analysis with nothing to show says it has no surface, result or
pull to plot, and `figure_colours` and `figure_width` say how a figure is
plotted. The viewer's representation is chosen under "Representation", and
a structure in the builder or the viewer is rendered. A ligand's chemistry
file is said to give a form, not to draw it. "Drawn" stays where it is the
statistical word: a seed or velocities drawn at random, pairs drawn once. A
test holds pages, messages and the docs to it.

### The Report page's chip tests say what the page holds when they fail

Both tests of a figure's chip on the Report page failed on CI's Ubuntu 3.11
job, the only one that runs the browser tests, on `e0e5829` and on
`38505df`, and pass here with the same browser, the same order, coverage
and the newest Markdown. Waiting in vain, they now say what the page holds:
whether the report or its empty state is shown, the addresses of its
figures, whether the chip code is there, what `/api/report` returned, and
what the page logged.

### Ctrl+C says the GUI stopped, on a line of its own

Stopped with Ctrl+C, the GUI printed nothing. The terminal had echoed `^C`,
and without a newline after it the shell's next prompt began on the same
line, which zsh marks with a `%`. It now prints "GUI stopped." on a line of
its own, from `fastmdx gui` and from a GUI left open after `fastmdx
explore`. A test starts the GUI as its own process and stops it with one
interrupt.

### The Report page renders the report on any install

The two chip tests that failed on CI's browser job said why once 1167 let
them: the report route answered `rendered: 'plain'`. The `markdown` library
was in the `[pdf]` extra alone, so an install without it, CI's `.[test]` and
`.[md]` among them, showed the report in the GUI as plain Markdown, with no
figure in it and no chip under one. It is a dependency of the package now,
in `pyproject.toml` and in the conda recipe's base group, and `fastmdx info`
no longer lists it as an optional backend for the PDF. A test holds both
declarations to it.

### The stopping rule's stated precision, pre-registered

A study run until it is determined stops at the first look where its error
is within the target, and an error read low at that look is the one it
reports. `fastmdxplora.validation.stopping_calibration` asks whether that
stated precision holds: series with a known true mean are made round by
round, recorded as every analysis records a series, and judged and
extended by the rule's own loop, `run_until_known`, so nothing of the rule
is reimplemented. Seven cases, from fast and slow correlated series to two
slowly exchanging states, and the check under the rule that withholds a
mean whose run does not resolve its correlation time, counted on its own.
What is counted, the seeds and what is claimed are in
`preregistration/stopping-calibration.md`, written before any coverage
was computed. Two seams make this possible without a copy of anything: what
an analysis records of a series is one function, `statistics.mean_record`,
which every analysis now calls, and the loop can be told how much
production a run has done.

### The stopping rule's stated precision, as counted: it does not hold

The calibration pre-registered in 1170 was run on its registered set (seven
cases, 5000 studies, and 6000 series for the check under the rule), and no
claim holds. A single run that stops reports an error about half the size the
truth needs: the truth was within one reported error in 42.7% of studies of a
fast-decorrelating series and 31.0% of a slow one, against a floor of 62.4%,
and within two in 70.9% and 51.1%. Three replicas came closest (64.4% and
91.6%), but 20% of fast and 66% of slow replica studies reached their
ceiling, because the check that withholds a mean whose run does not resolve
its own correlation withheld 19% to 41% of stationary series 50 to 250 times
their inefficiency, while giving a mean for 11% of series 1.25 times it. A
transient shared by replicas from one start left them a mean biased by about
one error. Studies trapped in one of two states looked determined in 7.4%
(one start) and 4.6% (drawn starts) of cases, every one with the truth
outside two errors. The counts, the causes found afterwards (the start
discarded where the error reads smallest, the halving check's noise, the
sample mean's bias on the inefficiency, the few degrees of freedom of one
run's error, and the stop itself) are in
`preregistration/stopping-calibration.md`; every study is in
`preregistration/stopping-calibration-registered.json`. The documentation of
`stop_when`, and the report's *How long it ran, and why*, no longer say its
error is "biased a little low": they say how far, and to read it as a lower
bound.

### An error on a mean holds the truth about as often as it says

Chosen on the registered set of the stopping rule's calibration (1171), after
the counts there showed single-run errors about half the size the truth
needed. Every analysis's mean and error change with it:

- **The inefficiency is corrected for the sample mean.** An autocorrelation
  taken about the sample mean reads low by about `g/N` at every lag; summed
  over the lags kept, the inefficiency was a tenth low at 25 times its own
  length. The sum itself is now taken by Fourier transform, the same numbers
  in `n log n`.
- **After a discard, the error is the whole run's**, scaled to the frames
  kept, unless discarding gained at least twice the independent samples. The
  start is chosen where what remains reads least correlated, so an error from
  what remains read low: 60% within one error on stationary series, where the
  whole run's gave 66%.
- **The start discarded is the latest that keeps within a tenth of the most
  independent samples**, rather than the one that keeps the most, which leaves
  the most of a relaxation in the mean.
- **A correlated run resolves its correlation time at 25 independent
  samples** (`statistics.RESOLVED_SAMPLES`), replacing the check that halved
  the series, which withheld 19% to 41% of long stationary series at random
  and passed the means whose errors were too small. A withheld mean's
  shortfall asks for 25.
- **Each mean records its error's degrees of freedom** (`degrees_of_freedom`,
  the frames over the lags summed), and `stop_when` widens each error by
  Student's t at them, and judges a single run alone only once its mean rests
  on 50 independent samples (`stopping.JUDGED_ALONE`).

Counted on the registered set, single runs and replicas of stationary series
held the truth within one error in 65% to 70% of studies and within two in
91% to 97% (from 31% to 65% and 51% to 92%). A single fast run now stops at a
median of 25 ns rather than 6.6. Not remedied: a relaxation shared by replicas
from one structure still biases their mean by about 0.4 of its error, slow
replicas reached a 200 ns ceiling in 41% of studies, and studies trapped in
one of two states looked determined in about one in ten. The counts, what was
tried and not taken, and what the held-out set was to show are in
`preregistration/stopping-calibration.md`.

### The calibrated error holds on a held-out set

The remedy of 1172 was counted on the held-out set its registration named
(5000 studies and 6000 series with seeds it had not seen), with the claims it
had to meet written down and committed first. Every one held: the stated
precision for single fast and slow runs and for fast and slow replicas (65.4%
to 69.2% of studies within one error, 93.5% to 96.4% within two), and the rule
usable on fast replicas, slow single runs and the transient (93.4% to 99.0%
determined before the ceiling). As expected, a transient shared by replicas
from one start still biases them (60.3% and 87.3%), slow replicas still
reach a 200 ns ceiling in 44% of studies, and a study trapped in one of two
states looked determined in 9% to 13%. Every study is in
`preregistration/stopping-calibration-heldout.json`.

### A figure an answer cites stays in view

Opening a cited figure from an Agent's answer scrolled to its card, and the
figures and charts around it then took their height and moved it: the RMSD
card ended a whole card above the view here, and CI's browser test of it
timed out (Ubuntu 3.11, on `52877c1`), most likely for this reason. For a few seconds after it is
opened, while the page's content changes size, the card is brought back,
unless the reader scrolls, clicks or presses a key. The card is looked for
whenever results arrive, for up to a minute, where it was looked for six
seconds; the Analysis page records which figure it was opened on; and its
grid of figures is rendered again only when a figure changed (every poll
reloaded each one where a study has no sections). The browser test waits on
what the page keeps rather than on the mark that fades, and says what the page
held if it times out.

### Replicas average from the start they share

Replicas over a seed begin in one structure and share its relaxation, and
each run's own equilibration detection kept a little of it: on the stopping
rule's calibration, three such replicas left their pooled mean biased by
about half its error, the one case 1172 did not remedy (60.3% within one
error and 87.3% within two). Before replicas are judged, the start is now
found again on their frame-by-frame average (`statistics.shared_start`),
where the relaxation is the same and the noise smaller, and each run's mean is
taken from the later of its own start and that one, written into its record
with `start_shared_with_replicas` (`simulation.stopping.share_the_start`,
read from the series each analysis writes; a run without it is judged as
written). `summarise` and `mean_record` take `start_at_least`. On the
registered set the transient holds (65.1% and 93.0%), at a median of 44 ns per
replica rather than 32, and studies trapped in one of two states looked
determined less often (7.2% and 4.2%, from 10.8% and 8.6%). A single run is
unchanged. What a second held-out set has to show for it to be adopted is in
`preregistration/stopping-calibration.md`, committed before it was counted.

### The shared start holds on a second held-out set

The start replicas share (1175) was counted on a second held-out set
(indices it had not seen, with its claims committed first), and every claim
held: the stated precision for fast, slow and relaxing replicas (67.4% to
68.6% of studies within one error, 94.2% to 94.3% within two; the relaxing
case's mean `(value - truth) / error` +0.18) and the rule usable on fast and
relaxing replicas (97.9% and 98.6%). Slow replicas still reach a 200 ns
ceiling in half their studies, and studies trapped in one of two states
looked determined in 6.2% (one start) and 3.4% (drawn starts). The
documentation and the report's *How long it ran, and why* now say that a
state no run left is what the error does not include.

### Replicas run for a fixed length are compared from the start they share

The shared start of 1175 applied only to a study run until it is determined.
A campaign of replicas run for a fixed length (members differing only by a
seed or by an ensemble's model) set the spread of its members' means against
their errors, and pooled them, from each run's own start, which keeps a little
of the relaxation they share. `aggregate_members`, and so `members.json`, the
comparison report and the GUI's runs compared, now take each member's mean
from the start the replicas share, written into its record as the rule writes
it. A record already written from it is not written again, and a study that
cannot be written to is read as it stands. Members that are variants keep
their own starts.

### A campaign of replicas checks the error each run states

`python -m fastmdxplora.validation.replica_calibration <campaign>` sets the
spread of replicas' means against the mean error each run states, for every
analysis that wrote a series of its frames in every replica, three ways: as
recorded when the runs were analysed, computed again by this release from
each run's own start, and from the start the replicas share. It writes
nothing in the campaign, and refuses members that are variants rather than
replicas. It is the read-back for a set of replicas run before 1172 (V5r's
errors were five to ten times too small): the same trajectories, read by the
calibrated estimator, without simulating again.

### What the records say matches what the code does

**Help, docs and records that said something the code does not are
corrected.** The `chains` help and the configuration reference said the
default is every chain; setup takes the biological assembly the file declares,
and every chain only where it declares none. `MIN_PYTHON` said 3.9 while the
package requires 3.10. The resume figure quoted 8×10⁻⁸ nm where the code and
its test measure about 1×10⁻⁹ nm at the same thread count. The manifest now
records which model wrote an agent-written study (`agent.model`), which only
the resolved config kept. The config language shown to a model gives each
number's range and says what each section is for, and describes `execution`
when asked. Stale docstrings (checkpoint sealing, sequence input, the
metadynamics surface) now describe the code.

### A campaign says when runs will share a GPU

**More parallel workers than listed devices is now said.** With `devices: [0]`
and `workers: 3`, three runs shared one card, each slower than alone, and
nothing said so. The dry run and the run say how many runs will share a card,
and that listing a device twice is how two runs on one card are asked for.

### A study's log holds only the study

**A study's log file is detached when the call that ran it returns.** It was
attached when the study was made and never taken off, so what a Python session
logged afterwards went into that study's `fastmdxplora.log`, and moving the
folder broke logging for the rest of the session. Each call (`explore`,
`setup`, `simulate`, `analyze`, `report`) now logs to the file for its own
length.

### The structure summary counts the first model

**The GUI's structure summary counts what setup will read.** Every model of a
multi-model file was counted, so a 20-model NMR entry showed twenty times its
atoms and its ligand twenty times over. The first model is counted, and the
number of models is given beside it.

### Joining finds a moved study's segments by their record

**Joining identifies a continued study by the prepared system its segments
recorded.** A segment's config names the study it continued by the path it
had, relative to wherever the command ran, so for a moved study it named
nothing and the join fell back to comparing settings. The segment's own record,
found relative to it and checked by content, is read first.

### The GUI adopts only the run a study recorded

**A running study is adopted by the GUI only when its process has the command
the run recorded.** Any `fastmdx` process was taken for the run, so with two
studies running, a stale record whose process number had gone to the other
study's run was adopted, and Stop would have stopped the other study. `fastmdx
resume` uses the same check to tell whether a study is still running.

## [2.5.8] - 2026-09-28

This release closes the GUI to other machines and other sites, and fixes
results that 2.5.7 could get wrong without a warning. Off loopback the GUI
serves only the watched run's results, text from a study is shown as text, a
reply from the Agent alone no longer starts a run, and a page on another site
can no longer drive the GUI. A molecule away from the protein is measured in
its nearest periodic copy; the correlation time no longer misses a slow mode,
so standard errors and withheld verdicts can differ from 2.5.7's where frames
alternate; interaction rows are named by chain; a ligand's pose is matched
atom for atom; a stated cutoff is kept; and umbrella studies, which 2.5.7
refused, run again. Some studies that ran under 2.5.7 now stop with a reason:
a ligand whose net charge cannot be read, a ligand pose the structure holds and
cannot give, and an ion whose alternate locations tie. For services that run
FastMDXplora for other people, the GUI can be served behind a proxy that signs
people in, each release is published as a Docker image, and `fastmdx resume`
carries a stopped study on to its end. AmberTools is now a declared
dependency, and Windows is no longer supported.

### `fastmdx resume` carries a stopped study on to its end

**A study stopped part-way, by a restart, a time limit or a GPU taken back, is
finished by one command.** `fastmdx resume STUDY` reads how far it got and does
what is left, once: nothing if it finished; the analyses and report if
production did; the rest of production from the last sealed checkpoint if it
had begun; the whole study again if it had not. It refuses a study still
running and one that stopped with a refusal, unless the refusal is marked worth
retrying, and it does not discard production it cannot continue. Running it
twice does the work once, so a service can run it whenever a job restarts;
`--json` prints the outcome for a program. The help for
`checkpoint_interval_steps` and the runner's notes no longer say there is no
resume.

### A Docker image with each release

**Each release is published as `ghcr.io/aai-research-lab/fastmdxplora:<version>`**,
beside the Apptainer image on the release page, for services that run
FastMDXplora in containers. It is written from `container/fastmdx.def` by
`container/docker_from_def.py`, so the installation and the checks that fail a
bad build are the Apptainer image's own, and a study gives the same answer in
either. It runs as a user that is not root, with `/workspace` as its home and
working folder. See `docs/hosting.md`.

### The GUI can be served to other people

**`fastmdx gui --hosted` serves the GUI behind a proxy that signs people in.**
It answers only requests carrying the proxy's secret (read from
`FASTMDX_PROXY_SECRET`), only under the names given with `--allowed-host`, and
only inside `--workspace`: every path a request names is read there, one
outside it is refused, and answers show paths as `~/...`, never the server's
layout. It refuses to start without a secret, a name, or with the top of the
file system as its workspace. Nothing changes without `--hosted`. See
`docs/hosting.md`.

### Python continues a study as the command line does

**`simulation.resume_from` naming a study directory was a continuation only on
the command line.** From Python the same config went to the batch layer, which
refused it for naming no system. `FastMDXplora(config=...).explore()` now
extends the study in place as `fastmdx explore` does, with `duration_ns` the
total and `extra_ns` an amount more, returns one result for the study, and a
dry run says what it would run. A segment that fails is reported as a failed
simulation rather than as a join that could not be made.

### A ligand pose the structure holds and cannot give is refused

**With `ligand_pose: auto`, a structure holding a residue of the ligand's name
whose pose could not be taken fell back to the supplied file's coordinates**:
a different atom count, bonds that do not match, fewer copies than ligands,
or a structure that would not read. On a complex that starts the ligand away
from its site, the seventeen-Angstrom failure, with a log line to say so.
Setup now refuses (`setup.ligand.pose_unavailable`) and says to use
`ligand_pose: file` where the file's pose is the one meant. The file's pose
still stands where the structure holds no residue of that name, or where
there is no structure at all.

### One segment number is one folder

**`segment-1` and `segment-001` were both segment one.** The join held
whichever listed later and a continuation resumed from whichever it met
first. Two readers also rebuilt a segment's folder from its number as
`segment-NNN`, so a folder named `segment-1` was counted and never read: its
production was left out of what was done, and a killed one's frames were not
trimmed. Two folders with one number are refused
(`simulation.resume.segment_named_twice`) with both named, and every reader
uses the folder it found.

### The Agent is told one thing, whichever way it is asked

- **Attempts.** The command line gave the Agent three attempts in all and
  its help called them corrections, which would be four; the browser and
  Python gave four. It is three in all, the first included, everywhere.
- **Continuing a study.** The prompt told the Agent to continue a study by
  naming it in `resume_from`, with `duration_ns` the total production it
  should end with. The browser's run status handed it the raw checkpoint
  form instead, with `duration_ns` the amount more, which ran as a separate
  study and joined nothing. The browser now offers the study form. A config
  continuing a study is run inside that study, its segments joined and its
  analyses rerun, and the GUI watches the study while it does: it had
  watched a new, empty folder and marked the run failed. The config is kept
  under the workspace's `continuations/`, so the study's own record of how
  it was started is not replaced.

### The records and pages say what happened

- **The methods said each heterogen decision was recorded in
  `setup_parameters.json`; it was only logged.** The decisions, with their
  reasons and copies, are now written there as `heterogen_decisions`, and the
  sentence appears only where they are.
- **The report's settings list said "Production MD was performed" of a study
  that ran none** (`duration_ns: 0`).
- **The GUI showed studies that finished as failed.** Every study was held to
  a prepared system in its own `setup/` and a finished simulation, so an
  analysis of a supplied trajectory, a setup-only study, a run given
  `setup_from` and a study of several runs failed on exit; a run the GUI
  adopted was judged by a Manifest `status` that does not exist. A study is
  now held to the phases it recorded, and a failure names the phase or the
  runs that failed.
- **A slide deck that could not be written was said at debug level and
  recorded nowhere.** It is recorded in `not_produced.json` beside a missing
  PDF, each entry with the code `report.format.unavailable`.

### Each sugar and each ion is judged by its own bonds

**A free sugar was discarded as a glycan when another copy of its name was on
an asparagine.** Whether a sugar belonged to a glycan was decided per residue
name, and an inner sugar counted as glycan whenever the structure was
glycosylated anywhere, so a NAG in an active site beside a glycosylated
asparagine, or a lactose bonded only to itself, was removed without a
question. The glycan is now followed from the protein, sugar by sugar,
through the LINK records; a component with glycan copies and free copies
stops and names both. **An ion LINKed only to a water was kept as
"coordinated by the protein"**, 30 A from it: a LINK now counts only to a
partner that stays in the system, and the reason names it. **An NMR entry's
heterogens were counted once per model**, so a zinc read as twenty atoms and
setup asked for an SDF; only the first model, the one prepared, is read.
Present in 2.5.6 and 2.5.7.

### A moved study still finds the system it simulated, and only that one

**A run given `setup_from` recorded the prepared system by path alone, as it
was typed.** Moving or copying the study, or fetching it to another machine,
left re-analysis, the report and replica comparison unable to find it, and a
different prepared system at the old path was used without a word: its
ligand chemistry and atom count described atoms the run never simulated. The
run now records the system as given, as resolved, relative to itself and by
the SHA-256 of its `system.xml`, finds it relative to the run first, and
refuses a system whose content differs (`analysis.data.not_this_system`). A
campaign's manifest lists members relative to the campaign, and aggregation
and the comparison report read them that way. The GUI and the cross-tool
comparison read the setup record of the system a run simulated rather than
the run's own `setup/`, which a run given `setup_from` does not have. Present
in 2.5.6 and 2.5.7.

### A ligand's pose is taken atom for atom

**With the pose taken from the structure, the supplied file's heavy atoms took
the crystal positions in the order the two files happened to list them.** Only
the count was checked, so two files listing the atoms differently put atoms
on each other's places: a C-C bond of 1.5 A came out at 3.0 and 4.5 A with no
error, since the clash check measures the ligand against the protein, not
against itself. Each atom now goes to the crystal atom of the same element
bonded to the same neighbours, the crystal's bonds read from its geometry;
where symmetry allows more than one answer, the one that fits the supplied
geometry best is taken. A residue whose atoms cannot be matched that way is
not used as the pose: `ligand_pose: structure` refuses, `auto` keeps the
file's coordinates and says why. Present in 2.5.6 and 2.5.7.

### `duration_ns: 0` equilibrates and stops

`duration_ns` is the production length, so zero now means no production: the
study is set up, minimised and equilibrated, and ends there. The runner read
zero as unset and ran the default 2 ns, while the cost estimate counted none.
The analysis phase of such a study is recorded as skipped, with the reason,
rather than as completed, and the methods paragraph says no production was
run instead of describing coordinates that were never written.

### A ligand charge that cannot be read is refused

**A supplied ligand whose net charge could not be read went ahead as
"unknown", and a stated `ligand_net_charge` went into the record unchecked.**
Formal charges in an SDF or MOL2 are whole numbers, so failing to sum them
means the file was not read as the chemistry it describes, and the check that
refuses a stated charge the file contradicts was skipped in exactly that case.
Setup now refuses with `setup.chemistry.charge_undetermined` and says to check
the file's hydrogens and formal charges. Present in 2.5.6 and 2.5.7.

### A stated zero equilibration is zero

**`npt_duration_ns: 0` ran a nanosecond of NPT and recorded NVT.** The runner
read a zero equilibration length as unset and ran the default, while the
ensemble resolver, the cost estimate and the run's record read it as no stage:
the record said constant-volume production of a run that produced at constant
pressure. `nvt_duration_ns: 0` ran the default 500 ps the same way. A stated
zero is now zero everywhere, as `npt_steps: 0` always was. Present in 2.5.6 and
2.5.7.

### Re-analysing an older run keeps the version that produced it

**A Manifest merged onto one from before phases recorded their version lost
it.** Re-analysing a 2.5.4 run under 2.5.7 left `"version": "2.5.7"`, no
`versions_seen`, and nothing saying the trajectory came from 2.5.4. A phase
carried over without `produced_by` now gets `{"version": <the version that
wrote the Manifest it was in>, "inferred": true}`, and `versions_seen` keeps
every version recorded before, including one whose phases have all been
re-run since.

### An interaction is counted for the chain it happened in

**`pl_interactions` added the same residue of two chains together.** Its
residue rows and pair rows were named by residue name and number alone, so on
a dimer the frames in which a ligand touched SER45 of either chain were one
row with one occupancy, and 184 and 184A of one chain merged the same way.
Rows are now named by chain where there are several (`A:SER45`) and by
insertion code (`GLY184A`), as `pl_contacts` already named them, from one
helper for both. A single chain without insertion codes reads as before.
Present in 2.5.6 and 2.5.7.

### Frames that alternate no longer read as independent

**A slow correlation under a fast, alternating one was invisible.** The
statistical inefficiency summed the autocorrelation lag by lag and stopped at
the first lag that was not positive. A stiff restraint sampled more slowly
than the mode it restrains makes consecutive frames anti-correlated, so the
sum stopped at lag one and every frame counted as independent: on a series
whose true inefficiency is 120 it returned 1.0, and the halving test then
called the correlation resolved. 21 of the 35 windows of the benzamidine
unbinding study reported a correlation time they could not have measured. The
sum now runs over adjacent pairs of lags (Geyer's initial positive sequence),
which recovers the slow part and gives the same answer as before where
nothing alternates. Standard errors, block lengths and the resolved verdict
all follow. The message withholding an error now quotes the replica
calibration: one run's error was about ten times smaller than the spread of
ten replicas. Present in 2.5.6 and 2.5.7.

### A molecule away from the protein is measured beside it

**A ligand that had left the pocket could be measured a box length away.**
Loading a trajectory made every solute molecule an anchor, and an anchor
stays in whichever periodic copy the engine wrote it. The radius of gyration,
SASA or RMSD of a selection holding such a ligand was then measured from the
wrong copy: on the T4 unbound control, 150 of 2,000 frames, the radius of
gyration of protein and benzene off by up to 0.028 nm. The protein and nucleic
chains now anchor, and every other solute molecule is moved to the copy
nearest them, searched exactly, in any box shape. A bound ligand is unchanged.
Present in 2.5.6 and 2.5.7. The choice of anchors also works on the older
MDTraj that Python 3.10 installs, where asking whether a residue is nucleic
raises, and if the placement fails the molecules are still made whole.

### Off loopback, only the run's results leave the machine

**2.5.7 bound beyond loopback served the Agent's conversations, and with no
run open, text files under the folder the GUI was started in.**

The conversation routes are refused off loopback, but 2.5.7 keeps a study's
conversations inside it, at `agent/conversations/`, and `/artifacts/`,
`/api/file-text` and the file list served everything in the study. With no
run open, `/api/file-text` read under the workspace, which is wherever
`fastmdx gui` was typed; from a home folder that reached the API key
the Agent stores in `~/.config/fastmdxplora/model.json`. 2.5.6 had neither.

Off loopback every GET is now refused unless it is listed, as every POST
already was; the list is what watching a run needs. Files are served there
only from the run being watched, only from a folder FastMDXplora wrote, and
never a hidden file or the Agent's conversations, which the file list no
longer offers on loopback either. The Files preview reads the run being
watched, never the workspace. A viewer off loopback can no longer have the
preview or the playback rebuilt, or ask for more frames than the server was
started with. A dashboard on loopback, the default, was not exposed.

### Text from a study is shown as text

**A name in a study could run script in the GUI.** The report page and the
Files preview rendered Markdown with its raw HTML, and a link could be
`javascript:`. A report quotes the names a structure file and a config
give, and the preview renders any `.md` in a study, so a name carrying
`<img onerror=...>` ran as the GUI, which can start runs and read the
Agent's conversations; the overview loads the report, so opening the study
was enough. A link in the Agent's reply could close its own attribute and
add a handler. An HTML file in a study was shown in an unsandboxed frame,
with the GUI's standing.

Raw HTML in Markdown is now shown as written, and a link or image goes only
to the web, to mail or within the study; the PDF is rendered the same way,
so a name cannot have WeasyPrint fetch or attach a local file. A reply's
link ends at a quote. HTML, SVG and XML files from a study are served, and
framed, in a sandbox of their own: their scripts run and cannot reach the
GUI. The report's interactive page, opened from the GUI, therefore no
longer remembers its card sizes between visits. Present in 2.5.6 and 2.5.7.

### A model's reply alone does not start a run

**In the GUI's Agent, `DO: run` in a reply pressed Run.** The only rule
against acting unasked was in the prompt, and the model reads the files a
person attaches, so a file could tell it to start work on the machine. The
server now reads the person's own message: a plain instruction, such as *run
it* or *go ahead*, runs as before, and any other `DO: run` asks *Run the study
above? Say yes.* and waits, as a stop always has. Present in 2.5.6 and 2.5.7.

### A page on another site cannot drive the GUI

**Any website open in the person's browser could use the GUI on loopback.**
A browser sends requests to `127.0.0.1` for any page, and the server
answered whatever reached it: a form posted as text/plain could switch the
served folder, store a model and key, or start a run, and a name made to
resolve to `127.0.0.1` let a page read the answers, conversations included.
On loopback the server now answers only to a loopback name, and it refuses a
POST whose `Origin` is not its own and an API request the browser marks as
cross-site. Scripts and `curl`, which send no `Origin`, are answered as
before. Present in 2.5.6 and 2.5.7.

### A ligand is given its charges on a fresh install

**A fresh conda-forge install of 2.5.7 could not parameterize any ligand.**

A ligand parameterized with an OpenFF or GAFF small-molecule force field,
which includes the default stack, takes AM1-BCC partial charges, and the
OpenFF toolkit computes them by calling AmberTools' `sqm`. conda-forge's
openff-toolkit no longer depends on AmberTools and FastMDXplora never
declared it, so a new environment had nothing to compute them with. Setup
repaired and solvated a protein-ligand system and then failed with the
toolkit's own error, "No registered toolkits can provide the capability
assign_partial_charges", which names neither the charge model nor the
package that was missing.

AmberTools is now a run dependency of the conda-forge package, and is in
`environment.yml` and the container image, whose build charges a small
molecule and fails if it cannot. `fastmdx info` reports **AM1-BCC charges**,
naming AmberTools or OpenEye where one will compute them and
`conda install -c conda-forge ambertools` where nothing will; `info --json`
carries the same row, so `fastmdx remote` counts a machine without it as not
ready and adds AmberTools to that environment. Where nothing can compute the
charges, setup refuses as soon as it knows the study has a ligand, before
PDBFixer or solvation, with `setup.environment.charges_unavailable` and the
install command. A study without a ligand is unaffected. From PyPI,
AmberTools has to come from conda-forge alongside the OpenFF toolkit.

### AmberTools is found without activating the environment

The OpenFF toolkit looks for AmberTools on `PATH`, once, when it is first
imported. Running the environment's Python by its full path, as a script, a
batch job or a command sent over `ssh` does, left the environment's own
programs off `PATH`, so AmberTools was installed and a ligand still could not
be given charges. Importing FastMDXplora now puts the directory holding the
running interpreter's programs at the end of `PATH` when it is missing.

### Windows is no longer supported

Continuous integration, the package metadata and the documentation now cover
Linux and macOS only, because AmberTools, which every ligand needs for its
charges, has no Windows build. The classifiers name Linux and macOS in place
of "OS Independent", the Windows legs are gone from the test matrix, and the
installation guide says Windows is not supported. `fastmdx info` states the
platform, and on Windows says plainly that it is not supported; nothing
refuses to run there.

### An umbrella study runs again

**2.5.7 refused every umbrella study before it started**, saying "`from` is
missing". Each window was checked as though a person had written it, after
the study had been expanded into windows that carry a centre and an index in
place of the study's `from`, `to` and `n_windows`. The study is still checked
as it was written, before it is expanded; the windows made from it are not
checked a second time. Present in 2.5.7 only.

### An occupancy tie is decided by one rule

**An ion written at two alternate locations of one residue took the first
where they tied**, and the higher however slight the lead, while the same tie
between two residues, or between a ligand's conformations, stopped setup. It
now stops too, and a lead of less than 0.10 is a tie for all three. **The
0.10 tolerance was compared in floating point**, so occupancies of 0.55 and
0.45 resolved while 0.60 and 0.50 tied; both differ by 0.10, and both now
resolve. Present in 2.5.6 and 2.5.7.

### A stated cutoff or switch is kept

**`nonbonded_cutoff_nm: 1.0` written in a config was ignored for CHARMM36.** The
schema handed over 1.0 nm and a switching function whether or not anybody wrote
them, so setup could not tell a stated 1.0 nm from silence: the run used the
force field's 1.2 nm, and a stated `use_switching_function` was replaced by the
force field's either way, each with only a log line. The record kept the
schema's 1.0 nm, so the methods paragraph of a CHARMM36 run cut off at 1.2 said
1.0. Both settings now default to unset: the force field decides only then,
what it decided is recorded in `setup_parameters.json` and
`resolved_config.yml`, and a stated value is kept even where it equals the
force field's own. The GUI offers the switch as default, on or off rather than
a checkbox that read "off" for a run that switched. Present in 2.5.6 and
2.5.7.

### The banner shows a Python study's own setup

The run's opening banner read its setup values only from a config named on the
command line, so a study given from Python announced pH 7.4 and the automatic
force field while it prepared at the pH and with the force field it was given.

## [2.5.7] — 2026-09-24

This release is for running a study where the compute is, and for carrying on
one that has already run. `fastmdx remote` inspects a machine over your own
ssh, installs there once you confirm, sends a study and brings it back, and a
study can now be resumed or extended in place and stay one study, joined and
reanalysed over its whole length. Structures are read as their authors
deposited them: the biological assembly from REMARK 350, mmCIF as mmCIF, and
each residue named by its chain and insertion code. The GUI holds more than
one study, lets a run outlive the server, and keeps the Agent's conversations
with the study they are about. Several defects in 2.5.6 that changed what was
simulated or reported are fixed, the most consequential being coordinated
metal ions missing from the prepared system, and a network exposure in two
GUI routes added during this cycle is closed before release.

### Off loopback, the dashboard answers only what it lists

A dashboard bound beyond loopback (`fastmdx gui --host`, or
`--dashboard-host` on `explore`) has no login, so what it answers there is
the whole of its security. It refused a list of POST routes and answered the
rest, and `/api/explore/switch`, added with Load study, was not on the list.
Anyone who could reach the port could re-root the server onto any folder on
the host shaped like a study, after which that folder's files were served
through `/artifacts/` and `/api/file-text`; the route's two error messages
also said whether a path existed. Stored Agent conversations could be read
through `GET /api/agent/conversation` and `/api/agent/conversations`.

Off loopback every POST is now refused unless it is listed as open, so a
route added later is refused by default. The list holds only `/api/config`,
which builds a config from the posted form and reads no file. The
conversation reads are refused there too. Neither route is in 2.5.6, so no
release was exposed, only installs of the development branch between those
routes and this fix. A dashboard on loopback, the default `127.0.0.1`, was
never affected.

### A coordinated ion reaches the prepared system

**2.5.6 prepared structures with coordinated metal ions without them.**

Under the default `heterogens: auto`, a lone ion the classifier decided to
keep, a catalytic zinc or the structural calcium of trypsin, was logged as
kept and then removed by PDBFixer with every other heterogen, because the
filtered structure that carries kept components to PDBFixer was never written
for it. The prepared system lacked the ion, and the study simulated an empty
site. With the ligand supplied as a file, the structure's ions were not
considered at all and went the same way, unreported.

The filtered structure is now written before anything can return, holding
exactly the copies the decisions keep (and crystallographic water, where
asked for), and the setup record lists the ions kept. A supplied ligand file
no longer changes this: its path decides the ions by the same rules, refuses
an ion the structure does not determine as the auto path does, and reports
what the filter left out. Trypsin keeps its calcium beside a benzamidine file
as it does without one.

Split ion sites are decided site by site. An ion modelled at alternate
locations within one residue is one ion, at its location of highest
occupancy, and no longer asks for an SDF. Copies in separate residues were
put into one group whenever any two of the same element lay within 1.5 A, so
two split zincs 20 A apart were refused as one four-copy site or lost a site,
and a site that resolved kept the copy first in the file while the log named
the copy of higher occupancy. Copies now form sites by connectivity, each
site keeps its copy of highest occupancy or stops on a tie, and only that
copy reaches PDBFixer.

### A placed ligand's hydrogens turn with it

When a ligand's pose is taken from the structure, the heavy atoms of the
supplied SDF or MOL2 are moved onto the crystal coordinates. The hydrogens
were then shifted by the displacement of the first heavy atom alone, with no
rotation, so wherever the file's frame differed from the crystal's the X-H
bonds came out several angstroms long. Ligands placed this way in 2.5.6 began
minimisation with their hydrogens misplaced. Each hydrogen is now placed from
the heavy atom it is bonded to (the nearest heavy atom where the file has no
bonds), turned by the Kabsch rotation that fits the file's heavy atoms onto
the structure's.

### A named prepared system is the one simulated, and is checked

A run given `simulation.setup_from` still ran setup when setup was in its
plan. Each run of a replica sweep solvated a box it never simulated, and the
setup record beside it described those atoms rather than the ones simulated.
With setup excluded, the analyses and the report found no setup record at
all, so the protein-ligand interactions perceived the ligand's chemistry from
its coordinates instead of reading it from the file it was prepared from. The
formal charge was among what was perceived, and it decides every salt bridge,
so interaction tables from such runs can change.

Setup is now dropped from the plan when `setup_from` names a prepared system.
The log says so, and the manifest records the setup phase as skipped, with
`taken_from` naming the system simulated. The analyses, the report and the
Agent's staged run read that system's setup record, and the interaction
record carries the formal charge it was judged with.

A study whose runs share one prepared system reused it whenever one existed,
so a re-run asking for pH 5.0 simulated every window in the pH 7.4 system
prepared before while its resolved config said 5.0. What the shared system
was prepared from is now recorded beside it. A mismatch is refused as
`setup.prepared.mismatch`, naming the settings that differ, and
`--force-overwrite` prepares it again. A shared system prepared before this
release has no record, and is reused with a warning.

### Splits and reports follow the ensemble production ran in

Whether production has a barostat decides the caveat a study split into
segments carries, since the barostat's adaptive move size is not in a
checkpoint and re-adapts after every join, and it decides what the report
says was simulated. In 2.5.6 both read the wrong evidence.

The split verdict read `pressure_bar` and `pressure_atm` from the config. A
default study names neither and produces at constant pressure, so a default
study split into segments joined with no qualification, while a resolved
config always carries a pressure, so a constant-volume continuation was
qualified. The verdict now asks the resolver the runner uses, and a queued
study records the caveat only when it is split.

The report's settings list gave the ensemble as `None`, and the methods
paragraph and the slides described production as NPT at a pressure whether or
not a barostat ran, so an NVT production was reported as NPT. The runner now
records the ensemble production ran in under `resolved`, and the settings
list, the methods text and the slides read it, stating a pressure only where
a barostat ran.

### Restraints are released in stages whether or not the run is watched

With `simulation.restrain` set and `live_telemetry: false`, NVT and NPT
equilibration ran through a stage runner that took no progress callback. The
restraints were held at full strength through both stages and released all at
once as production began, rather than stepped down through
`restraint_release` during equilibration. Runs made that way in 2.5.6 started
production from a solute let go in one step. Both stage runners now drive the
release ladder.

### Studies run on other machines

`fastmdx remote` reaches a workstation or a cluster with your own ssh and
`~/.ssh/config`. A study's Config never names a machine; the records are kept
with your other settings.

`fastmdx remote --machine NAME` inspects and records what the machine has:
GPUs and the CUDA version its driver supports, conda and every environment
holding FastMDXplora (under the base, `~/.conda/envs`, `envs_dirs` in
`~/.condarc` and `CONDA_ENVS_PATH`), Apptainer, SLURM partitions, scratch,
free space and internet access. It asks the installation matching this
computer's code what it can load, through the new `fastmdx info --json`. A
release matches by version; a checkout matches only by commit, with no
uncommitted changes on either side. Inspecting changes nothing on the
machine. Where the machine is not ready, it prints the plan: a conda-forge
install with `cuda-version` pinned to what the driver supports, a checkout
brought to this computer's commit with `git fetch` and `merge --ff-only`, or
a backend added to an environment that cannot load it.

- `remote install` runs that plan once you answer yes at the terminal. There
  is no flag to skip the question.
- `remote send -c study.yml` checks the Config here, refuses a machine not
  holding this computer's code, copies the Config and every file it names,
  and starts `fastmdx explore` there: as a detached process group on a
  workstation, or as an `sbatch` job asking for a GPU on SLURM
  (`--partition`, `--time`). `--dry-run` shows what would be sent.
- `remote status` gives each job's exit code, scheduler state, stage, share
  of its planned steps, and the last lines of its log.
- `remote fetch` refuses a job still waiting or running, brings the run
  folder back without trajectories and checkpoints unless `--with-trajectory`
  is given, and checks that the manifest names the code that sent it.
- `remote cancel` stops a job and leaves its folder; `remote forget` removes
  a machine's record; `fastmdx remote` alone lists machines and jobs without
  connecting.

### A structure is simulated as its biological assembly

**A structure given without `chains` may now simulate different chains from
those 2.5.6 simulated.**

A deposited entry holds the asymmetric unit, which is what the crystal
repeated rather than what the molecule is. Every deposited chain was
simulated whatever REMARK 350 said, so 1AKE ran as two copies of a monomer,
1HHO as half a haemoglobin and 1STP as a quarter of streptavidin. Where no
`chains` are named, setup now takes the authors' assembly before a
software-predicted one. Among assemblies holding the same sequences and
heterogens it picks the one needing least modelling (fewest missing residues,
then fewest residues missing atoms, then the lower mean alpha-carbon
B-factor) and says why. Where they hold different things it stops with
`setup.structure.assembly_ambiguous` and names each with the `chains` that
selects it.

An assembly the symmetry operators build beyond the deposited chains is
built. Each copy gets chain IDs of its own, with its own heterogens and its
own SEQRES, SSBOND and LINK records, so PDBFixer finds the same missing
residues in every copy, and copies that land on one another are refused. The
assembly chosen is recorded in the setup record, and naming `chains` selects
chains as before.

### An mmCIF file is read as mmCIF

A `.cif` given to setup was copied to `input.pdb` and read as PDB text, so
every one failed. It is now converted to the records setup reads, from the
mmCIF categories that hold them, with the original kept beside it as
`input.cif`. An entry RCSB serves only as mmCIF is fetched as mmCIF, and a
structure larger than the PDB format can hold, which setup works in, is
refused as `setup.structure.too_large`. Checked against four entries
deposited in both formats.

### A residue is named by its chain and insertion code

**Per-residue tables of structures with more than one polymer chain gain a
chain column, and their labels read `A:13`.**

The deposited number alone named every residue. On a structure with several
copies of one chain, per-residue SASA could not be tabulated, and RMSF,
secondary structure, dihedrals, order parameters, contacts and the B-factor
comparison wrote tables in which one number meant several residues. The
trajectory topology was written through MDTraj, which drops insertion codes,
so trypsin's 184A and 184 shared a number in every file the analyses read,
and per-residue SASA failed on every trypsin run.

The topology is now written by OpenMM with the deposited IDs kept. Residues
are labelled by chain where there is more than one polymer chain, and by
insertion code where the structure has any, with an insertion column and
labels like `184A`. A single chain without insertion codes is written exactly
as before. The report's region highlights and the dashboard read both forms.

### A study is continued in place, and stays one study

A run that stopped short, or finished and needs longer, can be carried on as
the same study. `simulation.resume_from` naming a study directory continues
it: the extra production runs as the study's next segment, `segment-001`
onward inside the study folder, every finished segment is joined into one
trajectory, and the analyses and the report are rerun over the whole of it.
`simulation.extra_ns` asks for that much more production and `duration_ns`
beside it for the total the study should end with; with neither, the
remainder of the study's own plan runs, which is resuming. The flags are
`--resume-from` and `--extra-ns` on `simulate`, prefixed on `explore`. A
checkpoint file given to `resume_from` is still the raw mechanism underneath,
starting from those coordinates and joining nothing, which is what a
segmented campaign wants.

The study's analysis and report are replaced, since they described a shorter
trajectory, and the segments themselves are never touched. A continuation
reuses its parent's prepared system and inherits its identity, so the check
that joined segments belong to one study rests on the solvated box they share
rather than on settings resolved twice. Segments are joined against the
trajectory's own topology, so a run that saved a subset of atoms joins. A
continuation analyses its own joined trajectory, not the parent's file named
in the parent's config, and playback plays the joined trajectory. A study
whose plan is met says so, and names the setting that goes past it.

Every checkpoint has a sidecar, `checkpoint.chk.json`, giving the stage,
step, ensemble, temperature, timestep, frame interval and study, and whether
the run finished. A plan that would minimise or equilibrate a production
checkpoint again, or continue it at another timestep, is refused; a
checkpoint without a sidecar loads as before, with a warning. Every
checkpoint is sealed as it is written, the checkpoint and its seal written as
a pair and renamed into place, so a kill cannot leave a whole checkpoint that
cannot be shown to be whole. `resume_unsealed` accepts a checkpoint with no
seal, one written by hand or by an earlier version.

A killed run is resumed from its last checkpoint, and the frames it wrote
after that checkpoint are left out of the join, so the pieces meet rather
than overlap; where those frames cannot be counted, the join is refused. The
checkpoint interval is rounded up to a multiple of the frame interval, a
continuation keeps its parent's frame interval, and a join refuses segments
written at different spacings. A study extended a second time resumes from
its highest-numbered segment's checkpoint and counts production across every
segment, on the command line and in the continuation the Agent is offered in
the browser; development builds before this ran the first extension's span
again and held it twice in the joined trajectory.

### A run outlives the server, and a server adopts it

A run started from the GUI sat in the terminal's process group, so Ctrl-C on
the server killed a day-long simulation mid-step along with it. The run is
now its own session and survives the server. Stop still stops it, and on
shutdown the server says what is still running, where, and how to stop it.

The run records its process ID in the study folder at start and removes it on
exit, so a run started from the command line can be adopted too. A server
opened on that folder, at launch or through Load study, checks that the
process is alive and is this run, then holds it: the sidebar says running,
progress shows, and Stop reaches it. A process is judged by what it runs, the
`fastmdx` entry point, the `fastmdxplora.cli` module or the study named as an
argument, and never by the interpreter's path, which inside a conda
environment named `fastmdxplora` matches any Python. A process whose command
line cannot be read is not adopted, since Stop might then reach whatever the
operating system gave the number to. On Windows, liveness is read through
`OpenProcess` without signalling the process, the command line through
`Get-CimInstance`, and a slow answer is asked for again in the background
every two seconds for a minute.

### The GUI holds more than one study

The GUI was bound to the folder given at launch. Load available study, in the
study block, opens the folder picker and switches the dashboard to another
study's output, refusing a folder that is not one. Another study can be
viewed while a run goes on: the study block reads top-down as loaded, running
and available, the running study shows its progress with a View button back
to it, and Stop reaches it from any page. A folder that is not a run says
what it is, the runs one level down or nothing run yet, rather than being
described as a run with its telemetry off.

A study of several runs, as a sweep writes under `runs/<id>/`, is shown as
one. Its root lists each run with where it stands and a way in, a run names
its study and its swept values with a way back, and the sidebar shows seven
runs, the running ones first, with More adding three at a time. The Report
page at the root serves the batch comparison once every run has completed,
and before that a table of what the finished runs settled on and how many are
still to come.

### The output folder is named for its system

**A default output folder is now
`fastmdxplora_<system>_study_<UTC timestamp>`.**

`fastmdxplora_output_20260919_022007` said nothing about what it held, and
two of the places that named folders used a fixed name with no timestamp, so
a second run could land in the first run's folder. One rule now names every
default folder, for the orchestrator, the batch explorer, the command line
and the GUI's launch: `fastmdxplora_1UAO_study_20260919022007`, or
`fastmdxplora_study_<timestamp>` with no system. The browser no longer names
a folder; the server applies the rule with the system it knows.

### A file is read beside the work

The side panel's preview reads every type that can be attached to a message
for the Agent, over one list, and is confined to the run's own folder. It has
two modes, and remembers the choice. Code is the file's bytes with line
numbers, and the default, because somebody checking a config wants the file.
View renders by type: a sortable table for CSV and TSV, a collapsible tree
for JSON, folds by indentation for YAML and OpenMM's XML, Markdown by the
same function as the Report page, atoms, residues and chains before a
structure file's text, the name, atom count and bond count of an SDF or MOL2,
and a log coloured by level. What is rendered is escaped first, and a `.py`
is Code only. The header gives the file's size, line count and digest, a file
past 200 KB shows its head and tail and says how much of the middle is left
out, and a PDF fills the preview.

The Files page is the catalogue. View opens a file in the panel, Open is
offered only for what the browser shows itself (a PDF, an image, an HTML
page), and Download and Copy path always. Its list holds while a run writes
to the folder, where a live frame deleted between listing and reading used to
fail the whole request, and a fold that was opened stays open across polls.

### The Agent's conversations are kept with their study

A conversation lived only in the browser's memory, and a refresh emptied it.
Conversations are now stored by the server, one file each, inside the study
they are about at `<study>/agent/conversations/`, so copying a study carries
the conversations that made it; one about no study is kept at the workspace
level. New starts a fresh thread and keeps the last. Conversations lists them
grouped by study, the loaded one first, and deleting one is asked for and
never a side effect. Opening a conversation from another study loads that
study first, and a conversation that launches a run moves into the study it
created.

A file can be attached to a message with **+**: text types only, at most six
to a message, a file past 200 KB cut to its head and tail. The transcript
records each file's name, path, size and digest, not its bytes, and the Agent
is told to cite a file when it uses it.

The Agent sees what the sidebar shows: the step, the fraction complete,
elapsed and remaining time, simulated time and speed, and the active run's
resolved config, from which it copies settings rather than inferring them
from the run's numbers. Asked to continue a stopped study, it is handed a
config planned from the record and sets only the length. A stop it asks about
is recorded as a question, and the stop only once confirmed. A system named
in words is written as its identifier and the identifier named back, so a
wrong one is visible, and an unknown name is asked about. It calls itself the
FastMDXplora Agent and, asked, names the engine chosen in Settings.

### The report says what it measured

**The Convergence table's count of adequately sampled observables may fall.**

The table judged adequate sampling at five independent samples while the
report's prose and the Agent used ten, so rg at 8.3 was adequate in the table
and not in the section above it. All three read the one bar in
`statistics.py`, which is ten.

Read against a real study's report, several lines are fixed. FastMDXplora and
the libraries it calls are separate sentences, and the Tools sentence names
eleven libraries rather than five, adding NumPy, SciPy, matplotlib, pandas
and scikit-learn, any of whose versions can change a number. Every setting
used fills in what the given settings determine, production steps from the
duration and timestep, durations from step counts and the pressure in both
units, rather than printing `None`, and empty parameters are dropped. The
residue count separates protein residues from ions and cofactors. Every
analysis section opens with a sentence saying what its figure shows, and the
summary figure has one panel per analysis, up to twelve, where it named
twelve files of which two exist only in per-residue SASA mode.

The record holds more. `resolved_config.yml` is written before the first
phase and again at the end, so a run in progress or stopped has one.
`setup_parameters.json` records the periodic box: its vectors, perpendicular
widths, the smallest of them, and its volume. A PMF whose figure cannot be
drawn says so, and that `pmf.json` is unaffected. A PDF refusal names every
missing library at once, and the Report page no longer says the PDF is
missing after a later run made it.

### The phase lists have one name everywhere

**`include` and `exclude` at the top of a config are now `include_phase` and
`exclude_phase`.**

They read as the same thing as `analysis.include` and `analysis.exclude`,
which choose analyses. The phase lists are `include_phase` and
`exclude_phase` in the config, `--include-phase` and `--exclude-phase` on the
command line, the same in the GUI, and `explore(include_phase=...)` in
Python; the analysis lists keep their names. The earlier spellings are still
accepted everywhere, until the release that removes every earlier spelling at
once, and both spellings given with different values are refused rather than
guessed between. The places that dropped a phase list and ran more than
asked, among them the staged runner of a budgeted study and a config opened
in the GUI form, now keep it.

### Every setting reaches a study through every interface

Each of the 125 settings is carried with a non-default value through the
command line, `cli_command`, `python_script`, the GUI payload, the page
itself in a browser and the resolved config, and compared. The few that do
not survive are listed with the reason, and a new gap fails the suite. Among
the gaps it found and closed:

- `--sweep AXIS=VALUES`, once per axis, runs a study over several values of a
  setting from the command line, and the GUI's run builder gains rows of a
  setting and its values. Both read values through one function.
- A block setting (`umbrella`, `steered`, `metadynamics`) can be given on the
  command line as a YAML mapping, as can `analysis.options` and
  `report.region_highlights`, and a setting declared int-or-float is read as
  a number.
- `--agent`, `--agent-model` and `--budget-hours` are flags on `explore`, and
  a boolean flag has both directions, so `--no-explain` reaches the study.
- `cli_command` and `python_script` now say `verbose`, `explain` and the
  execution block, write a block as JSON rather than a Python repr, and hand
  a study with a sweep, several systems or an execution block to the API
  whole.
- A study opened in the GUI form keeps its phases, its sweep, `explain`,
  `verbose` and its execution block, so a parallel study reopened and started
  again no longer runs sequentially.

The GUI's unused second launch path, `/api/explore/defaults`,
`/api/explore/validate`, `/api/explore/config` and `/api/explore/start`, is
removed. The launch that remains refuses before starting a process when the
chemistry stack it needs is not installed.

### The Python API validates a study as every other route does

A system passed to the Python API with a separate options mapping reached
the phases without validation, so a misspelled `duraton_ns` ran the default
duration and a temperature of -50 K was planned as given. It now passes the
same validator as a configuration file, the command line and the GUI,
before anything is created. So do the settings given to a single phase:
`setup()`, `simulate()`, `analyze()` and `report()`, and the `fastmdx` phase
commands built on them.

### Python 3.10 is the floor

`requires-python` is `>=3.10, <3.14`. OpenMM's last release with a Python 3.9
wheel was 8.1.1, so a 3.9 install could not simulate, and 3.9 reached end of
life in October 2025; refusing at install is clearer than installing what
cannot run.

### The frame, from using it

The browser opens once the server answers, where on a finished study's folder
it arrived early and showed "unable to connect". Folding either side column
gives its width to the centre, which stays centred with both folded. Page
headers are one sticky row, and the Builder tab is Config. The Agent's
composer sits at the foot with its buttons centred in Chrome and Firefox
alike, its controls (the mode, Conversations, New) under it, and a message is
edited in place. One Output button opens the folder and copies its path, Cite
sits in the settings popup beside the version, and the log's "why" filter
shows the explanations, which had never been written to the log. A browser
tab closed mid-request is not logged as a route failure.

### Smaller fixes

- The gate that keeps the fraction of native contacts, Q, off chains too
  short for tertiary contacts used a separation of 4 whatever was set, so Q
  was planned where it then failed or left out where it would have run. The
  analysis plan now receives the per-analysis options.
- A failed run closes its trajectory and energy files. OpenMM's reporters
  have no `close()`, so nothing was closed on any path, and a sweep run in
  one process kept both open for every run that failed. A figure whose
  drawing or saving fails is closed too.
- `fastmdx info` survives a backend that ends the interpreter on import: that
  backend is reported broken, with how the interpreter ended, and the rest
  are probed in a fresh process.
- A missing preferred platform names only the plugin failures that explain
  it; the rest go to the debug log.
- A molecule alone in a periodic box is made whole, where imaging raised
  because MDTraj anchored on no molecule. Seeding and the cross-tool
  benchmark reached this.
- Parallel runs are given the device with the most room, where a run's place
  in the queue could leave two runs on one card and the other idle. A device
  listed twice has room for two.
- The preview reads a file's line endings as text does, so Markdown on
  Windows renders as it does on the Report page.

### Housekeeping

More than 150 tests that searched a function's source for a phrase, and so
passed with the behaviour broken, now run the code; converting them found the
restraint, fold-gate, reporter and `fastmdx info` faults above. A ratchet
lists the tests that still read source, each with its reason, and a new one
fails until it is listed. The GUI's browser tests run on CI, on the Ubuntu
3.11 job, and every setting is checked through the page there. The resume
comparison tests hold at any matched thread count. The GPU shakedown script
stops at a failed run and reports every join. The documentation describes the
study block, conversations, attached files and continuation, and tests hold
those claims against the code.

## [2.5.6] — 2026-09-18

A pull and its umbrella windows now come from one configuration, and a
study can be written from a sentence. Those are the two reasons this
release exists. Around them: every refusal given a name a program can act
on, a study designed from a short pilot and priced before it spends
anything, runs that split and rejoin without becoming a different study,
selections given one vocabulary and one documented trap, free energies
given an error bar or withheld with a reason, a box that grows by its own
shape rather than a cube's, and a validation corpus that for the first
time actually runs.

### The thermodynamics analysis could not find the record it needs

`Thermodynamics` looked beside the run for `state_data.csv` and
`production_state.csv`. The simulation phase writes neither. It writes
`simulation/energy.csv`, which six other places in the tree already read --
the dashboard telemetry, the report, the convergence check, the server's
file registry. The two names being searched for exist only inside the
validation corpus, which builds them in a temporary directory as a fixture.

So the analysis raised `FileNotFoundError` on every real run while its tests
passed, because the fixture wrote the filename the code expected rather than
the one the runner produces. `energy.csv` now joins the search order, in the
analysis and in the orchestrator.

*Contributed by Victory0102.*

### Two-dimensional metadynamics reads periodicity from PLUMED

Which biased variables are circular was decided by scanning the PLUMED
script for `TORSION` or `ANGLE` anywhere in the text, then inferring per-axis
periodicity separately for the one- and two-variable cases with the logic
inline in the pipeline. It is now one function, `periodic_dimensions`, which
reads each variable's own action definition and returns a flag per axis.
Reconstructing the bias from hills is likewise now a single N-dimensional
routine that takes per-axis periodicity, rather than separate paths that had
to agree.

This matters for any surface biased on two circular variables, φ and ψ being
the usual pair.

*Contributed by Victory0102.*

### The harmonized benchmark was not harmonized for hydrophobic contacts

**Interaction counts from a harmonized cross-tool run will change.**

`HARMONIZED_PARAMETERS` puts the independent tool on our geometric criteria
so that a disagreement means a disagreement about chemistry rather than about
cutoffs. Its hydrophobic distance read 4.5 A. Ours is 4.0 A -- PLIP's
threshold, and the docstring on our own rule says so in the same breath as
noting that ProLIF uses 4.5. So the one comparison the harmonization exists
to make fair was being made at two different cutoffs, and the gap was
attributed to something other than the cutoff.

The comment directly above that table says to confirm the values against the
pinned test before trusting a harmonized run. Nobody had.

Two further repairs to the same comparison. Molecule healing makes each
molecule whole but does not put the ligand and the protein in the same
periodic image, so the ligand is now reimaged to the protein's minimum-image
copy before the independent tool sees it -- our own distances already use
the minimum image, and the two were not being measured the same way.
And an interaction family the native analysis explicitly *refused* to measure
was being compared as though it had measured zero. A refusal is not a
measurement, and those families are now excluded from the table by name.

The ligand-free negative control no longer manufactures an empty interaction
table. It verifies the ligand's absence from the topology and reports that,
which is the claim the control actually makes.

*Contributed by Tomato_Cultivator (@Paradoxicaly).*

### A phase records what produced it

`fastmdx analyze` run directly against a finished simulation rewrote
`manifest.json` with only the analysis phase, erasing the setup and
simulate records. `self.results` is populated by the `explore` loop and by
nothing else, so a single-phase command reached the manifest writer with an
empty list. The phase result is now recorded before the manifest is
written, and the writer merges what is already on disk: previous records
kept, order preserved, a repeated phase replaced by its latest run,
malformed shapes skipped rather than fatal.

Preserving them raised a second problem. Every other manifest field is
recomputed by whoever writes the file, so a run simulated under 2.5.4 on
the cluster and analysed under a later version on a workstation came back
claiming one version and one machine produced all of it. That is worse than
the gap it replaced: a missing record sends you to look, a confident wrong
one does not.

Each phase now carries `produced_by` -- the version, host and package
environment that produced *that* phase -- stamped where the phase runs.
Phases recorded before this field existed keep no `produced_by` at all,
which is the honest value. Where a manifest holds phases from more than one
version, `versions_seen` and a short note appear at the top level, so a
reader who checks only the header is not told a single version produced the
lot.

*Contributed by Tomato_Cultivator (@Paradoxicaly), with per-phase
provenance added in review.*

### A platform that loads is not a platform that runs

`auto` tries CUDA, then OpenCL, then CPU, and probes each GPU platform
before choosing it. The probe built a Context around a System holding one
particle and no forces, which compiles almost no kernels -- so a platform
whose kernels will not build passed the probe and failed on the real system
a second later.

Found on a workstation whose driver rejected the installed OpenMM build's
PTX. `openmm.testInstallation` reported `CUDA - Error computing forces`
while `auto` selected CUDA, and every run died with
`CUDA_ERROR_UNSUPPORTED_PTX_VERSION` -- with a working OpenCL platform
sitting next in the candidate list, never tried.

The probe now computes a force on two particles with a `NonbondedForce`,
which is what `testInstallation` does and what separates the two outcomes.
A GPU platform that cannot compile its kernels is now skipped, with its
reason logged, and the next candidate is used.

- Tests: 3,191 → 3,218 collected.

### A pull and its umbrella windows come from one configuration

A window whose centre sits beyond a barrier cannot be started from a
bound-state frame. A harmonic restraint holds a system where it already is;
it cannot carry one across a barrier. A window seeded on the wrong side
stays on the wrong side however long it runs, and its histogram describes
the wrong basin while every completion check passes.

The remedy is standard -- pull once, and start each window from the frame
nearest its own centre. Doing that used to mean running a steered
simulation, writing a script to pick frames out of its trajectory, and
assembling the starting structures by hand: three tools and a directory of
intermediate files, outside the configuration and outside the provenance
record.

`steered` may now appear beside `umbrella` in the same study.

```yaml
simulation:
  steered:
    collective_variable: ligand_distance
    ligand_name: BEN
    select_atoms: "resSeq 189 to 195 and name CA"
    to: 2.0
    steps: 5000000
    force_constant: 5000.0
  umbrella:
    collective_variable: ligand_distance
    ligand_name: BEN
    select_atoms: "resSeq 189 to 195 and name CA"
    force_constant: 3000.0
    from: 0.4
    to: 2.0
    n_windows: 30
```

The pull runs first, each window takes the frame nearest its centre as its
starting structure, and the windows then run as an ordinary campaign. The
pull's work is not a free energy and is not reported as one: it depends on
how fast the anchor moved, and its purpose here is starting structures.

Six things that are easy to get wrong, and are now handled:

- **The coordinate is measured under the minimum-image convention.**
  Without it a ligand that crosses a periodic boundary appears to be
  nanometres from its site. In a rhombic dodecahedron the fractional
  reduction alone is not sufficient either; a search over the neighbouring
  cell images is. A pull measured wrongly seeds every window wrongly.
- **The recomputed coordinate is checked against PLUMED's own `COLVAR`.**
  If the selections handed to the seeder are not the ones that were biased,
  the study is refused by name rather than seeded from a coordinate nothing
  drove.
- **A seeding pull records every atom.** `save_selection` leaves water out
  by default; a starting structure is a complete set of positions and a
  subset cannot become one. A pull that will seed windows sets
  `save_selection: all` for itself.
- **Velocities are drawn fresh at temperature.** A pulled frame's velocities
  point along the pull, which is the one direction that would bias the first
  picoseconds of equilibrium sampling.
- **The pull is re-anchored after equilibration**, so it starts from where
  the equilibrated system is rather than from where the input structure was.
- **A seed whose energy is not finite is refused**, with the window and the
  measured coordinate named.

`umbrella.seed_from` reuses a pull that has already finished, so a campaign
that has to be relaunched does not repeat it. `setup_from` reuses a prepared
system from an earlier study: preparation is the human activity and setup is
the first automated phase, so `setup_from` is the name it is given, and it
defaults to the earlier study's output directory rather than to the internal
path beneath it. `prepared_from` continues to work.

`umbrella.from` now defaults to the coordinate measured in the supplied
structure after equilibration, rather than requiring a number the user has
to measure first.

### Selections say which atoms, in one language

Four places ask which atoms -- what an analysis measures, what goes into the
trajectory, what is held still during equilibration, and what a biased
coordinate is measured between -- and they now use one vocabulary.
`select_atoms` names the selection wherever a collective variable takes
exactly one; where it takes two, `select_atoms_a` and `select_atoms_b` say
which is which, and a bare `select_atoms` on a two-group variable is refused
rather than assigned to a group and hoped over. `analysis.select_atoms`
joins `analysis.selection`. The role-specific names -- `site_selection`,
`bilayer_selection`, `axis_selection` -- all still work and say more where
they apply. `ligand_name` and `ligand_resname` are the same key.

Every expression is MDTraj's, and `docs/selections.md` is new: it documents
the four sites, the per-variable table, when a selection is resolved, and
one trap in particular.

**`resid` is not the residue number.** `resid 189` is the 190th residue in
the file, counting from zero; `resSeq 189` is the residue numbered 189. For
a structure numbered from 1 with no gaps they coincide, and for a PDB entry
they generally do not. In trypsin, numbered 16-245 with gaps,
`resid 189 to 195` selects Ile212 through Gly219 while `resSeq 189 to 195`
selects Asp189 through Ser195. Both are real selections, neither is empty,
and nothing downstream can tell which was meant. This project ran a
27-window umbrella campaign on the first of those before noticing.

A biasing block's selections are now resolved against the prepared system
before any window is launched, so an empty selection costs a validation
error rather than a preparation and a failed run.

### A free energy carries an error bar, or says why it cannot

Reported free energies had no uncertainty attached. They now carry one, from
the block-bootstrap over the windows for a PMF and from the reweighted
average for a metadynamics surface -- and where the estimate is known to be a
lower bound rather than an interval, the number is withheld and the reason
named instead of being printed beside a caveat. A figure wrong in a knowable
direction is worse than no figure, because the caveat is read once and the
number is used thereafter.

A metadynamics surface now carries its own convergence band and is labelled
as carrying one.

### The box grows by its own shape, not by a cube's

Padding was applied as though the cell were a cube. A rhombic dodecahedron's
perpendicular widths are not its edge length, so a padding that looked
generous left a solute closer to its periodic image along the short axis
than the requested clearance -- which matters most for exactly the runs that
pull a ligand away from a protein, since the pulling direction is the one
that has to stay clear.

The requested clearance is now measured in the box that will actually be
built. Relatedly, nine quantities that need the unit cell now consult it;
three did before.

### Preparation is a result, and it is cached

A preparation that fails is a result and is now recorded as one, so a
campaign that retries does not repeat a failure it has already paid for. The
complex is repaired once per structure rather than once per ligand copy,
which is what made two structures look hung when they were working: 900
seconds of real work, now reported as such.

### Metadynamics reads its own stored heights

The stored hill heights are already tempered, and were being tempered a
second time on readback. The bias is now looked up rather than resummed from
every hill.

### Interface parity is a test, not a promise

The claim that graphical controls, command-line flags and Python options are
generated from one set of declarations is now asserted by 53 tests across 12
contracts, rather than maintained as documentation.

### The validation corpus can run the corpus

The corpus job installed with pip and the corpus needs conda, so the job had
been passing without running anything it was written to run. It now runs, on
a machine with somewhere to run it, and the end-to-end seeding test runs
there too -- the path that opens trajectories and builds OpenMM contexts,
which no other test reached and where both of the seeding bugs above lived.

Also: `fastmdx info` is the command, not `fastmdx doctor`; a corpus test that
asserted the installer now asserts the capability; and tests that shell out
to a tool declare it.

### A study can be written from a sentence

`propose_config` has always taken a callable -- prompt in, text out -- which
is a reasonable thing to ask of a developer and an unreasonable thing to ask
of somebody who wants to run a simulation. `fastmdx agent` is that callable,
built from a choice made once.

```
fastmdx agent "trypsin with benzamidine, 100 ns, umbrella along the
               unbinding coordinate" -o study.yml
```

Two providers by name and a third taking any OpenAI-compatible base URL,
which covers DeepSeek, vLLM, Ollama, OpenRouter and most local servers. They
speak the same shape, so there is nothing to add per vendor: a list of
vendors goes stale and a protocol does not.

What the Agent writes goes through the same `validate_config`, by the same
code, with the same refusals, as a config typed by hand. When the validator
refuses, the refusal goes back and the Agent repairs; `--attempts` bounds
that loop and defaults to 3 on measured evidence rather than on a guess. The
repair attempts print as they happen, because they are the only visible sign
that anything checked the config.

The key lives in one file outside any study, owner-only, and the environment
is read first so a cluster job or a CI run never has to store one. It never
enters a config, a manifest, a log line or an error message. `agent_model`
records which model wrote a study, because "why did this pick 300 K" has a
different answer depending on whether a frontier model or a 7B on a laptop
proposed it, and an alias is not a version.

`agent/evaluate.py` and `scripts/measure_nli.py` measure the interface
rather than assume it. Two quantities, and the second is the point: *valid*
is cycles to a config the validator accepts, *correct* is whether it meant
what was asked. A config can validate and be the wrong study -- 300 K where
the sentence said 310, a setting simply omitted, or a concentration written
in millimolar where the field is molar. The evaluation set is fourteen
sentences across easy, medium and hard.

### The Agent in the browser is a conversation with the run in front of it

`fastmdx agent` with no request opens the same server `fastmdx gui` starts,
landing on `#agent`. One browser, one codebase; two commands that started two
servers would be two things to learn for one thing to use.

The panel is a conversation that can see the config, the run and the
results. It edits rather than restarts, answers questions about what is on
screen, and when told to, runs, stops with a confirmation, and opens pages.
It never acts unasked. Every field of a config it writes reaches the run
through one renderer, so what the thread shows is what launches.

The key is typed in the browser, sent once, and stored server-side, because
a browser cannot hold a secret: anything the page keeps is readable by
anything else the page runs. It is never sent back, and a test asserts the
response contains no key at all -- a page that never receives one cannot
leak one. `unvalidated` is refused at this door with the same code the CLI
gives, because a mode that existed in one door and not the other would be
two pieces of software wearing one name.

### A short pilot says what an umbrella study should be

An umbrella study's window spacing and force constant decide whether it
produces a free energy or a refusal, and neither can be read off the
structure. Finding them has meant running a study, watching it refuse,
changing something and running it again.

Every window already carries the number that fixes both. It comes to rest
where its restraint's pull matches the free energy's, so its displacement
times its force constant is the gradient of the surface there -- the one
place an umbrella study reports the slope directly. Read a handful of
windows that way, run briefly, and they measure the gradient along the whole
coordinate.

`design_from_a_pilot` solves the two requirements together. A window has to
stay within half the distance to its neighbour, which bounds the constant
from below; neighbours have to overlap, `d <= 2.5 sigma`, which bounds it
from above. Asking a window to use four fifths of the room it is allowed
gives `d = 2.5 kT / G` and `k = G^2 / kT`. On the trypsin-benzamidine
coordinate a stretch measuring 223 kJ/mol/nm gives 0.0344 nm at 12,970
kJ/mol/nm^2, against the 0.0344 nm at 13,000 that study reached by hand over
two days and three attempts.

The design checks itself before it runs. `predicted` says where every window
it proposes would come to rest, how much of its allowance that uses, and the
area it would share with its neighbour, computed from the distributions the
restraints imply rather than asserted. `measured_over` says where the
readings stop. A proposed study can be refused, repaired and proposed again
without leaving the planner.

### A study is priced between setup and the part that costs something

`--autonomous` now runs, in two stages with an estimate between them. Cost
scales with the solvated particle count, which depends on box shape, padding
and ion concentration -- decisions setup makes. A protein of 2,000 atoms is
60,000 solvated, so guessing from the residue count would be inventing the
water, which is most of the atoms. Setup settles the count, the estimate
comes from that count on this machine, and the simulation starts only if it
fits the budget.

The gate sits where the information first exists and before the cost is
incurred: earlier it would be guessing, later there would be nothing left to
stop. Refusing keeps setup's output, so a shorter study reuses it through
`setup_from`, and the refusal names both figures -- the estimate and the
budget -- because the answer to "too expensive" is usually a shorter run
rather than a larger allowance, and a caller cannot make that choice without
the numbers.

`--autonomous` refuses without `--budget-hours` rather than defaulting. A
default allowance would be a number nobody chose deciding how much of
somebody's card to spend.

### A cone on the angle, and the room it took out of the bulk added back

A window on a distance holds the ligand at a radius and leaves it free on
the sphere of that radius. The bulk reference the recombination uses,
`-2kT ln r`, says only that the room at radius r is `4 pi r^2`.

Two things have to be true for that to mean anything, and on a real site
neither was. A study of trypsin and benzamidine measured both: the sphere 1
nm from its S1 site is 12 per cent outside the protein and 50 per cent at 2
nm, because the coordinate's origin is a centroid of backbone atoms and is
buried. And one window's ligand visited between a twentieth and a third of
what was open to it, because a sphere of radius 2 nm has 50 square
nanometres of surface.

`cone` answers both at once: a flat-bottomed wall on the angle between the
site-to-ligand line and an axis fixed in the protein, confining the ligand
to a cap of solid angle `Omega = 2 pi (1 - cos theta)`. Flat-bottomed rather
than harmonic, so inside the cone there is no bias at all. The axis is the
direction from a named group's centre to the site, so it turns with the
molecule instead of pointing at a corner of the box. The cone is measured
off the pull rather than chosen, and each window starts inside the wall it
will run under.

The solid angle is integrated rather than assumed. A wall is a quadratic
penalty, not a cliff: at 5,000 kJ/mol/rad^2 the ligand reaches about three
degrees past thirty, which is seven per cent more cap, and that number
enters a binding free energy as a logarithm. `solid_angle` integrates the
wall's own Boltzmann factor over the sphere.

Running inside a cone changes one side of the binding free energy and not
the other: the bulk state gives up `4 pi / Omega` of its room and a bound
pose that fits inside the cone gives up none. `binding_free_energy` takes
the cone the windows ran under and adds `kT ln(4 pi / Omega)` back. Both
numbers are reported -- `delta_g_before_the_cone_kjmol` is what the curve
says and `delta_g_kjmol` is what it means -- because a reader checking the
arithmetic should see the step rather than the result of it.

The half of that derivation which can fail is the bound state, so it is
measured. Every window writes the wall's own bias beside its coordinate;
`wall_bias_where_the_bound_state_is` reads that column out of the windows
holding the bound state, discards what the study discards, and takes the
worst rather than the average -- one window pressed against the wall is
enough to have taken population out of the integral. Above a tenth of RT the
binding free energy is refused, with the number and the remedy. Where no
window recorded the column, the result says the check was not made rather
than reporting a zero that would read as a wall that stayed quiet.

The plan's `cone` block records what the correction will cost before it is
spent, and the windows' own records are read from the directory the runner
writes them to.

### Every refusal says which refusal it is

437 sites in this package raise, and every one of them now carries a code: a
stable name a program can act on rather than a sentence only a person can
read. `MissingPathError` is a path the user gave that is not there;
`MissingResultError` is an analysis looking for a phase that did not run.
Both were `FileNotFoundError` and indistinguishable, so a caller could only
guess -- and guessing wrong means editing a correct config, or re-running a
phase that was never the problem. `OutputExistsError` refuses to write over
a finished study, because the cost of being wrong is asymmetric: refusing
costs one argument, overwriting costs whatever the run took. Where a class
replaces a builtin it must still be an instance of it, and a test holds that
-- widening never breaks a handler, narrowing silently does, and only in the
path nobody exercises until it matters.

The disclosure rule -- what may be told to an automated caller -- is
enforced by the registry rather than at each raise site, so a semantic
refusal cannot leak a set of permitted values by accident.

Not enough data is a refusal too, and it says how much short.
`summarise()` has always withheld a mean it could not stand behind; the
withholding was prose, so nothing could tell "three frames here" from "the
correlation time is not resolved, run longer". Three conditions are now
separated -- `analysis.sampling.too_few_frames`,
`analysis.sampling.correlation_unresolved`,
`analysis.sampling.too_few_independent` -- and `sampling_shortfall()`
answers the question the refusal leaves open. The reason is a `str` subclass
carrying its code, so it prints, formats and compares exactly as the plain
string did and the report layer is untouched.

A new raise site that says nothing about itself now fails the suite. The
assertion is the property rather than a fraction of it: a floor of 0.99 let
through exactly what it was meant to prevent.

### Drift is told from scatter

Pooling combines estimates of one quantity. If the segments are not
measuring one quantity -- a system still moving across the whole run -- the
pooled mean is a confident number for a quantity that does not exist.

Segment means that disagree in no order say the per-segment errors are too
small: the mean stands and its error is a lower bound. Segment means that
climb or fall say the system had not settled at the scale of the whole run:
no mean, `analysis.sampling.drifting`, and the remedy is a longer run rather
than more pooling.

The ordering is tested by permuting the segments rather than by assuming a
distribution. The statistic is the weighted least-squares slope against
segment index and the null is that slope with the same means in random
order -- exact for any number of segments, which matters because a run is
often three or four and a t approximation on three points is a number rather
than a test. Heterogeneity is reported as Cochran's Q over its degrees of
freedom rather than as a p-value: a ratio of three is plainly too much and
needs no distribution to say so. Both conditions are required before calling
drift, because an ordering of segments that agree is a trend of nothing, and
on the p-value alone a settled eight-segment run would refuse one time in
twenty.

A study that passes still says what its windows did, and a window's remedy
is sized against the gate it actually broke.

### A study may be split, and says when it may not

A checkpoint restores positions and velocities. It does not restore the
biasing state, and for two methods that is the whole calculation. So the
first thing built here was the refusal.

Metadynamics split into segments begins the second piece from zero bias with
the system in a well the first piece already filled. The surface that comes
out is wrong and does not look wrong -- it is smooth, it plots, and the
depth is off. A steered pull is worse: the restraint is placed by absolute
step number, so a resumed piece pulls from an anchor the protein is not at,
and the work integral is taken along a path nothing walked. A hand-written
PLUMED script refuses too, because what state it keeps is not something this
software can read. Two are safe and say so: an unbiased run carries nothing
beyond positions and velocities, and an umbrella window's restraint is a
function of the collective variable and not of time. `Queue.submit` consults
this before the first segment's hours are spent.

The resume path is now run rather than read. Twenty argon atoms, a real
`System`, a real checkpoint: a resumed run reproduces the run it continued
to 1e-6, and a checkpoint from a different system is rejected with OpenMM's
own reason travelling out in the refusal -- "wrong number of particles"
sends somebody to the system they built, "could not be loaded" sends them to
the disk.

The third claim did not hold. A truncated checkpoint loads silently: at half
length it loads and gives the right positions, at a tenth it loads and gives
wrong ones, and at no point does OpenMM object. There is no length or
checksum in the format. So a finished run seals its checkpoint -- size and
digest, written beside it on completion -- which detects truncation and,
because it is written on completion rather than by the reporter, means the
segment reached the end. A run killed partway leaves a checkpoint and no
seal, and the next segment refuses.

A segment must run the same study, not a cheaper one. `plan_segments` zeroed
`npt_steps` after the first segment to skip equilibration; the runner gates
the barostat on `npt_steps > 0`, so zeroing it removed the barostat from
production. Segment 0 ran NPT and the rest ran NVT -- two ensembles in one
trajectory, joined and analysed as one, with nothing downstream able to tell.
A resumed segment now keeps one token NPT step where the study wants a
barostat and zero where it does not.

A joined run is read as joined, because reading it as one loses the joins.
The report finds the joins itself and stops when they say stop. What a join
costs is measured rather than assumed: the barostat's adaptive move size is
not in the checkpoint, and the magnitude at production size is what decides
whether that is a footnote or a rule about minimum segment length.

### The machine measures itself, and learns from what it has run

`measure_this_machine()` is a bootstrap: argon, no water, no PME, no
constraints. A machine that has run real studies knows more about itself
than that.

Every finished run now writes `cost.json` beside its output -- particles,
steps, seconds, platform, precision. The clock starts after the system is
built and before the first step, so it times integration rather than setup,
which does not scale with step count and would overstate short runs badly. A
resumed segment records its own steps. `calibrate_from_runs` fits across
them, on real system sizes, real force fields, PME rather than a plain
cutoff.

The part worth having is not the better constant. It is that a fit can say
something a single point cannot: whether the model holds here at all.
Seconds are taken to go as particles times steps, and a `fit.spread` past 3
refuses rather than averaging through a disagreement that wide. A queue's
budget is arithmetic over those numbers, and a study says how long it will
take on this machine and holds the line to it.

### Four measurements per frame

A coordination number, a residue-pair distance, a moment of inertia and an
end-to-end distance for an unstructured chain. None needed a new phase; each
is one number per frame and each goes in beside `rg`.

`coordination_number` counts one selection within a shell of another. The
cutoff *is* the measurement -- water round Mg2+ is six at 0.28 nm and eleven
at 0.35 -- so no number is assumed. The default reads the first minimum of
this run's own g(r), records that it did, and refuses where the curve has no
shell in it. Finding that minimum needed the scatter of g(r) taken at the
peak's own radius rather than in the bulk: counting noise grows as 1/r, so
measured against the bulk figure a structureless liquid reports a hydration
shell. On randomly placed points the scatter was 0.569 inside 0.3 nm against
0.071 beyond 1.5, a ratio of 8.0 against the 1/r prediction of 7.8.

`pair_distance` separates two selections by centre of mass or closest
approach, `end_to_end` measures a chain's extension, and `moments_of_inertia`
gives the three principal moments, which separate a rod from a disc where the
radius of gyration cannot. Three of the four carry a periodic hazard and say
so rather than leaving it in the curve: minimum image returns the shorter way
round, so a separation past half the box is reported as an approach and the
line folds back with nothing to show that it has.

`requires_naming` is new, and is why two of these are absent from the
automatic plan: every other analysis takes its subject from the structure,
and these take it from the user.

### A number the quantity cannot be is refused

pH 25, a negative duration and a temperature below absolute zero all
validated until now, and so did 150 in a field that means molar -- the
thousandfold slip the evaluation set exists to trap. Twelve settings gain
bounds where the bound is a fact rather than a preference. A 10 fs timestep
gets none, because it is unstable and not impossible.

### What nothing checked says so

A figure drawn by a phase that worked outside the schema carries a visible
`unvalidated` mark. Only the unchecked phases: a trajectory from a validated
simulation is sound even when the analysis over it was not, and a mark on
everything stops being read. No setting removes it; the way out is to rerun
inside the schema.

### Figures in colour, greyscale, or both

`analysis.figure_colours` takes `colour`, `greyscale` or `both`, declared
once as a schema field with `choices`, which is what generates
`--analyze-figure-colours`, the GUI control and the config validation.
`colour` uses the Okabe-Ito palette, chosen to stay distinguishable under
the common forms of colour vision deficiency; `greyscale` drops hue and
carries the same distinctions in value and hatching, which is what a print
journal wants; `both` writes the colour figure as `<name>.png` and a
greyscale copy as `<name>_greyscale.png` beside it, so the choice does not
have to be made before the journal has been. The primary keeps its name
whichever mode drew it, so the report, the dashboard and every reader that
knows about `figure_path` are unaffected. British and American spellings are
both accepted.

A figure now says what it shows, in one house style: what the number means,
the legend inside the axes, and which frames its mean came from. A time axis
is the run's clock, the frame number, or nothing.

### Equilibrated and converged, in the field's own words

"Settled" was this package's word for two different physical ideas, and it is
not the field's word for either. `statistics.py` cites Chodera's automated
equilibration detection and its function is called `detect_equilibration`,
while the class it returned was called `Settled` and about ninety
user-facing strings said "settled" -- on every figure, in every report.

An observable that stops drifting has **equilibrated**; that is Chodera's
own word, and the transient it discards is the equilibration period. A
metadynamics bias or a free-energy surface that stops changing has
**converged**. They are different claims. An independent sample is a whole
one, and the frames that are thrown away have one noun.

### The GUI is one frame

Three columns: the study in the sidebar, the work in the middle, a log-and-
files panel beside every page. Each column scrolls alone and the wordmark
stays put. One nav, in the order somebody works, with the citation in sight.
The builder asks four questions and the phases are tiles. The Report page
reads the report where the study ran, and works on a base install. The
Overview holds what the sidebar cannot. The structure sits above the charts,
the summary cards are two across, and their text wraps.

### The documentation is a set, not a pile of pages

The pages were individually well written and were not a documentation set.
Two of the six components had no page at all, one page held seven unrelated
subjects, and eleven claims described behaviour the code does not have.

The structure is now the six components in the order a study moves through
them: a Config specifies the study, four phases run it, a Manifest records
the result, and four interfaces write a Config -- the GUI, the CLI, the API,
and the Agent in place of a person.

Six new pages. `agent.md` leads with the claim the design turns on: what the
Agent writes goes through the same validator, by the same code, with the
same refusals, as a Config typed by hand. `manifest.md`, because
`manifest.json` was a table row in `results.md` and is half of what the
software is for. `config_reference.md` lists all 119 settings in the groups
the GUI form and `--help` already use. `analyses.md` is the per-analysis
catalogue, `developers.md` takes the contributor material that was loose in
user pages, and `validation.md` covers the corpus, cross-tool and environment
measurements.

`refusals.md` goes from 1,113 lines to 266 and covers refusals; its other six
subjects went where they belong. Renames: `cli_reference` to `cli`,
`configuration` to `config`, `getting_started` to `first_study`, `phases` to
`how_it_works`, `simulations` to `studies`, `usage_examples` to `examples`,
`remote` to `clusters`. Every correction is traced to source, and the numbers
in the pages are held by tests so they cannot quietly decay.

### Eight defects, and what finding them turned up

The resolved config named only what was typed. It now names every setting
every phase used -- per phase, exactly the dictionary the phase was handed,
not a reconstruction of it. A study that decided two settings wrote two; it
writes 108.

A study-level `agent:` never reached the manifest. `unvalidated` stamped no
figures. The Agent panel's "Load into the form" did nothing, and two GUI
sections collected settings and dropped them.

Then, from asking what a complete resolved config changes for its readers:
`cross_tool` took the ligand from a field that has a default, so every apo
protein's config would have claimed one and the comparison would have hunted
a residue that was never prepared. `join_segments` refuses to concatenate
segments from two studies and could not -- it read a path no run writes, so
every digest came back empty and three segments at two different pH values
joined without complaint. The CLI and script renderers turned a resolved
config into 108 settings, one of them a ligand for a protein without one.
And the GUI offered "autonomous -- draft it and run it" and drafted.

Two readers of one path, again, in two more places: an umbrella window's own
record was written under `simulation/` and looked for one directory up, and
a run's own answers were worked out and then thrown away.

The same-solvation guard read its evidence backwards. A segment after the
first runs with `include: ["simulation"]` and `setup_from` pointing at
segment 0, so it never runs setup and has no `setup_parameters.json` of its
own -- and the absence of that file is the evidence the two share a
solvation, not that they do not. The guard now refuses only when both counts
are known and differ, and says the two numbers when it does.

About twenty settings remain deferred decisions written as `null`, and those
follow whichever version replays them. The docs say so.

### Housekeeping

A `.mailmap` collapses nine committer identities on `main` to one. Log
capture takes the logger that emits, not the root. Water is not a ligand.
Two claims about the literature that could not be sourced are removed. The
V4 threshold is fixed before the result it judges exists. Tests that do not
check an error bar no longer pay to compute one. Windows paths are compared
as paths rather than as strings.

Read the Docs served `FastMDXplora 0.1.1.dev50+g52cfb8616` in the title of
every page while PyPI and conda-forge both carried 2.5.5: the build clones
shallow and without tags, so setuptools-scm found no tag to resolve against
and stamped its guess into every page. `post_checkout` now fetches the tags
and deepens the clone -- both are needed, since the version is the nearest
tag and the distance to it is counted in commits a shallow clone does not
have. The fallback changes from `0.1.0` to `0.0.0+unknown-version`, because
a number shaped exactly like a release made a build that failed to find its
version indistinguishable from one that found it.

The conda environment file installs what it says it installs. PLIP was cited
and not run, and the docs said otherwise. The seeder reads the spelling the
docs lead with. The README's link row is Quick start, Agent, Cite; the
conda-downloads and engine badges are gone. The software says what it is
rather than what its name stands for. Equilibration steps are not simulation
steps. Several windows can share one GPU, and this said they could not. A
window can be held at its own force constant, and one that could not be held
says what would have held it. Production resets the clock, so a held
window's COLVAR had two of them; COLVAR is production and the settling has
its own file. The recombination ran out of iterations before it ran out of
progress. A slope through two bins is not the slope of either. A study that
names a prepared system does not prepare another one. The shakedown refuses
an occupied output directory before measuring the machine rather than three
minutes in. A test that passes because the network is down is not a test, and
a hangup is not an incident.

## [2.5.5] — 2026-08-22

A correctness fix and eight repairs to what the software says while it
works. Ligand pose RMSD was wrong for any ligand that left the pocket;
everything else here is a message, a refusal, or a test that was not
measuring what it claimed.

### If you have quoted a ligand RMSD for an unbinding run, recompute

**Nothing followed the ligand across the periodic boundary.** The analysis
took raw Cartesian displacement after protein alignment, and `superposed`
discards the unit cell, so a ligand crossing a face jumped by a box repeat.
On the 20 ns T4-lysozyme unbound control the benzene gave 65 frame-to-frame
jumps above 2 nm, the largest clustered at 6.58-6.80 nm between frames 10 ps
apart, and the reported RMSD reached 9.49 nm in a box smaller than that. A
benzene does not travel 6.8 nm in 10 ps.

A bound ligand never crosses a face, so bound-state results are unchanged --
a regression test pins that. The affected quantity is the unbinding or
free-ligand case, where the reported path was the box rather than the
ligand.

The displacement is now unwrapped: each frame takes the image nearest its
*predecessor*, not the one nearest the receptor. Consecutive frames are a
saving interval apart, so a step of nearly a whole lattice vector is an
image swap and nothing else, which is what makes rounding exact here.

Two earlier attempts imaged per frame into the receptor's cell and are
recorded in the tests so the approach is not tried a fourth time. The first
rounded fractional coordinates, which is correct only in a near-orthogonal
cell; in the rhombic dodecahedron these runs use it picks a longer image
than the true minimum for 29% of random pairs, and it moved the control's
maximum from 9.49 nm to 9.22 nm. The second delegated the minimum image to
MDTraj, which handles triclinic correctly, and made the jumps worse --
59 to 84. Per-frame imaging answers "where is it now" and cannot make a
path continuous.

**The fix was also rejected twice on magnitudes that were asserted rather
than computed**, and the run's own data acquitted it. "A step above 0.3 nm
is not physical motion" presumed a diffusion coefficient near 0.1 nm²/ns.
The unwrapped series implies 1.60 nm²/ns from its own rms step -- where
benzene in TIP3P belongs, TIP3P over-diffusing by roughly twice -- and at
that value the series is Gaussian to counting noise: 188 steps above 0.3 nm
observed against 187 predicted, 14 above 0.5 nm against 10, a largest step
of 0.664 nm against an expected extreme of 0.698 for 1,999 draws, and none
at all above 1 nm where the wrapped series had 72.

### A refusal now reaches the person who can act on it

Three analyses refused correctly and said so only in the debug log.

`rdf` could not succeed on a default configuration at all: its default
pair is the solute against water oxygens, and a default run saves the
solute alone, so two defaults contradicted each other. It is now absent
from a default plan rather than failing in it -- `_build_plan` already
skipped analyses declaring `requires_water`, and `rdf` had simply never
declared it. An explicit `include: [rdf]` still runs it, and where the
trajectory holds no solvent the refusal names `save_selection: all`.

More generally, an analysis that refuses now prints its reason beneath its
row in the results table. The message was already carried on the result;
the row renderer had no parameter for it. A bare exception class name is
suppressed rather than printed, since it occupies the line an explanation
should hold.

And it is now said once. An analysis that raised also printed
``✗ ERROR Analysis 'rdf' failed`` several analyses before the table was
drawn -- a line that named no reason, in a place where nothing could yet
be done about it. It is suppressed on the console rather than demoted:
`fastmdxplora.log` still records it at ERROR with its traceback, because a
log that filed it at DEBUG would describe a run as clean when an analysis
failed in it. The console and the audit record want different things.

### Findings that were not findings

`rdf` reported a first peak from a flat curve. On a 0.1 ns Trp-cage run
g(r) spanned 0.000 to 1.074 and peaked at 1.782 nm -- the last bin before
half the box -- recorded as a hydration shell at 1.78 nm with no
qualification. Two criteria now separate a peak from the tallest bin of
noise, both read off the curve rather than set as thresholds: it must be a
local maximum away from the edge of the measurable range, and it must stand
above the bulk by more than the bulk's own scatter. Where neither holds the
finding says `not_a_measurement`, as the radius of gyration already did.

### Messages that described the wrong run

`--help` raised on every subcommand carrying a literal percent: `71% of a
cube` in `box_shape` was read as a format specification. The doubling now
happens at render, not in the schema, because `config/generate.py` and the
GUI print the same text verbatim and the interface-parity test requires
them to match byte for byte.

A ligand supplied by hand -- the offline route, where there is no network
and the SDF is handed over directly -- was reported as removed one line
before it was loaded and placed. And `analyze` printed a SIMULATION banner.

### A campaign says how far along it is

A member of a campaign has no terminal, so a three-member study printed
`0/3 done` for hours with no way to distinguish a healthy run from a
stalled one; the only recourse was watching a DCD grow. Nothing new is
recorded: `live_status.json` already carries `current_step` against
`total_planned_steps`, and the study's heartbeat now reads it back. Where
it cannot be read the heartbeat says less rather than failing.

### The restraint test asks a question its data can answer

Five versions of `test_restrained_atoms_move_less` have failed, each
replaced on a reading of the previous failure. The reading was wrong every
time, and the same wrongness underlies all five: the test asserted a
*trend* -- that the free arm climbs away and the restrained arm does not --
using a statistic built from two numbers.

`setRandomNumberSeed(7)` made that look safe and does not. OpenMM's CPU
platform sums forces in thread order, so the trajectory depends on the
thread count as much as on the seed. On one machine, one seed, unchanged
source: the free arm's final displacement is 0.224 nm at one thread and
0.520 nm at eight. Across three observations it spans 0.140, 0.224 and
0.520 nm -- a factor of 3.7 -- while the restrained arm sits at 0.035 and
0.036 nm over the same range. Two thread counts failed two *different*
assertions, which is what a wide distribution does to a statistic drawn
from two of its samples.

What a positional restraint controls is amplitude, so that is what the
test now asserts: every restrained block below every free block, first
block discarded on both arms. Replayed against all three observed
trajectories it passes with the distributions separated by 3.0, 3.4 and
4.1 times, where the old assertions failed two of the three. The thread
count is deliberately not pinned -- a test that passes only at a
particular thread count measures the platform rather than the physics.

### Python 3.14 is not supported, and this says why

`requires-python` remains `>=3.9, <3.14`. The ceiling was moved to 3.14 and
moved back within the hour, and the reason is worth publishing so nobody
repeats the check that missed it.

Every distribution in the dependency set publishes cp314 wheels, which was
verified first and was the wrong question. PROPKA is pure Python: it
installs anywhere and runs where its code runs. On 3.14 it raises
``'Parameters' object has no attribute '__annotations__'`` --
`propka/parameters.py` reads `self.__annotations__`, an instance lookup
that resolved to the class dict through 3.13 and does not under PEP 649,
where class annotations are computed lazily through `__annotate__`. The
current release is 3.5.1, it declares `>=3.8` with no upper bound, and no
fix has been published.

The full suite is otherwise green on 3.14: 3,171 passed, two failed, both
from that one call. PROPKA is a run dependency of the conda package, so
`requires-python` cannot claim 3.14 while pKa assignment raises there. The
bound moves when PROPKA ships a release that runs on it.

A related correction: the `publish.yml` version gate told a failing release
to bump `shim-package/pyproject.toml`, which has taken its version from the
tag through setuptools-scm since 2.5.4. It now names the causes that can
actually produce the mismatch.

### The 3D viewer keeps water, ions and the cell during playback

*Contributed by Tomato_Cultivator (@Paradoxicaly).*

Trajectory playback streams the solute-only trajectory, which is what the
run saves. Water, ions and the periodic cell live in the full solvated
topology, so switching a viewer into playback -- or changing which run it
showed -- dropped them, and the toggles for them went on claiming they were
on. The full topology is now mounted as a static environment overlay
alongside the played frames, aligned to them, with the toggle states
reasserted on every run change and a viewer generation counter so a slow
load for an abandoned run cannot repaint the current one. Where the
environment cannot be fetched the viewer says so rather than silently
showing less.

Two rendering changes come with it, both about a solvated box being mostly
solvent. Water is drawn as oxygen markers rather than every bond, since
rendering thousands of water hydrogens hides the solute the person is
looking at; the Hydrogens control still exposes the atom-level view. And
the periodic cell, which is physically larger than the protein, is drawn
thinner and at an opacity that depends on whether water is shown, so the
guide stays visible without overpowering a protein-only view.

On the server side, a run whose process exits non-zero now reports the
failure with the relevant line from its log rather than a bare return code,
and telemetry that predates the current process is recognised as stale
rather than shown as if it were live.

### Tests

A deposited `.dat` now names its own layout. The two conventions under one
extension -- comma-separated with a header, whitespace with none -- have
been recorded in `options.json` since 2.5.4, which serves a reader who
knows to look and still has the run directory. The whitespace form now
carries a `#` line saying how to read it, because a deposited file
outlives its directory. `np.loadtxt` skips such lines by default, so
nothing that reads these files changes.

Almost nothing. The cross-tool harness reads one of these back, and it took
its delimiter from the file's first line -- which is now that note, and the
note contains a comma. A whitespace file was split on commas, the column
names came back wider than the array, and the lookup raised `IndexError`.
It read correctly for `ligand_rmsd`, whose preferred column name happened
to match the first comma-split token, and failed for `sasa`. The delimiter
now comes from a data row instead, which also repairs a PLUMED
`#! FIELDS` header that the old path mis-parsed in 2.5.4.

A sweep whose runs share one system and one setup block is told, at plan
time, that its members will each solvate independently unless
`simulation.prepared_from` is set. Solvation does not place water the same
way twice: a three-member seed sweep gave 30,654 atoms against 30,803, so
anything called a replica sweep measured dynamics variance plus solvation
variance, and no recorded setting showed it. This names the setting rather
than changing the default, because sharing setup silently would make a
study half-run under the old behaviour incomparable with its own earlier
members.

Two tests written for this release listened to a logger that does not
exist. Both named `fastmdxplora.…`; the package logger is `fastmdx`. Both
also captured through the root, while `setup_console` sets
`propagate = False` on the package logger -- so a record need not reach
root at all. One failed on every Python 3.9 job; the other passed
throughout, asserting against a logger it had never configured. They now
attach to the logger under test.

The boundary fix arrived with seven tests that all exercised the helper
directly and none through the analysis. Mutation showed the cost: replacing
the call site in `compute` with `followed = None` -- routing straight back
to the pre-fix branch -- left the entire suite green. A helper can be
proven and unused. `test_the_analysis_actually_uses_it` closes it, and is
checked by that same mutation.

- Tests: 3,119 → 3,191 collected.


## [2.5.4] — 2026-08-17

A correctness fix. Interaction counts from any run that computed more than
one analysis were too high.

### If you have published interaction counts, recount

**One analysis was disturbing another.** MDTraj's `superpose` rotates
coordinates in place and returns the same object, and the analysis layer
handed every measure the same trajectory. A measure that aligned -- RMSF,
ligand RMSD, clustering, dimensionality reduction, order parameters,
B-factor comparison -- left every measure after it reading rotated
coordinates. What a measure reported depended on which other measures had
run before it, and nothing in the record could show it, because "which
other analyses ran" is not a setting.

Compounding it: rotation does not rotate the unit cell. Minimum-image
distances taken afterwards map atoms through a box that no longer
describes the frame, which does not fail. It answers, wrongly.

On a 20 ns trypsin--benzamidine run, `pl_interactions` reported 252
hydrophobic contacts when it ran after three aligning analyses and 10 when
it ran alone, on the same trajectory in the same container on the same
day. The same ligand--protein pair measured 1.64 nm before alignment and
1.83 nm after. **The smaller count is the correct one**: contacts counted
after superposition are counted through a box that does not match the
coordinates.

Any interaction table from a full pipeline run, or from any `analyze` that
included an aligning measure, over-reports. Re-run the analysis on this
release; the trajectories are unaffected.

Each analysis now receives its own copy of the trajectory, and every
superposition goes through a helper that aligns a copy and drops the box
that no longer describes it. A test asserts that a measure gives the same
answer alone as in company.

One existing test had accommodated the defect rather than catching it: it
compared its result against the caller's trajectory and passed only
because superposition had mutated it, with a comment explaining the
mutation as though it were behaviour.

### Provenance

The environment record consults distribution metadata where a module
carries no version attribute, so PDBFixer -- which decides every
protonation state in a run -- is named rather than reported as merely
loaded.

A ligand whose chemistry was inferred from coordinates now leaves that
chemistry in `setup/ligands/`, where the next analysis finds it instead of
inferring again. Chemistry read from a file was already on disk; the
inferred kind, which is the weaker of the two and the one this software
labels a guess, was the only kind leaving no record of what was guessed.


## [2.5.3] — 2026-08-16

Measurements the software can be checked against, and four numbers that
change.

### Read this before upgrading

**The fraction of native contacts is a different number.** Q's contact set
S is now over pairs of heavy atoms, which is the definition Best, Hummer
and Eaton published and the one MDTraj's reference implementation uses.
It was over residue pairs, taking one closest-heavy distance for each,
which is a coarser measure and not comparable with a Q quoted anywhere
else. On a peeling hairpin the two readings stand 0.263 apart on a scale
that runs zero to one. Any Q recorded before this release is the residue
reading; `scheme: residue-closest-heavy` still produces it, named so that
choosing it is deliberate. The defect survived because the test asserting
the published formula compared against a restatement of the
implementation's own choice: the code and its test agreed with each other
and neither agreed with the paper.

**A stated ligand charge is now a check rather than an override.** Where
`ligand_net_charge` disagrees with the formal charges in the chemistry
file, setup stops instead of warning and proceeding. The file is what gets
parameterised, so a configuration claiming +1 over a neutral amidine
produced a run whose charge record and whose chemistry disagreed. An
existing study that carried a mismatched charge will now refuse; the fix
is a file in the protonation state the study means, which for an amidine
or a carboxylate at physiological pH is not the Chemical Component
Dictionary's ideal form.

**Trajectories hold the solute.** `simulation.save_selection` defaults to
`not water`, taking a 20 ns run of a small protein from about 740 MB to
under 80. The topology that matches what was written is saved beside it as
`trajectory_topology.pdb`, and every reader prefers it. Anything outside
this software that loads `production.dcd` against the prepared system's
topology will now fail on an atom-count mismatch: load the file beside the
trajectory, or set `save_selection: all`. The water-site analysis needs
`all` and says so rather than reporting an empty result.

**Solvent-accessible surface area is of the protein.** Computed over the
whole box it was occluded by the very water whose access it measures,
which made it both wrong and slow.

### Measured against something outside the trajectory

Backbone N--H order parameters, in the Lipari--Szabo sense, for comparison
against NMR relaxation. Per-residue fluctuations against a deposited
structure's B-factors, reported as a correlation and not as an accuracy,
because a refined B carries static disorder and the lattice damps the loop
motion a solution trajectory is free to make. Density, energies and
temperature read back from the state record the simulation already wrote,
with density reported only from a constant-pressure run. The radial
distribution between two selections, stopped at half the smallest box
dimension, past which the minimum-image convention supplies only part of
each shell and the curve falls away for a reason belonging to the box.

Standard-state binding free energy from a one-dimensional potential of
mean force, refused where the run never reached bulk: beyond the
interaction the curve must fall as -2kT ln r, and windows that stopped
while the ligand was still held give a smooth curve and a plausible number
that is wrong by however much of the well was left outside.

### Sampling and campaigns

The fraction of native contacts is available as a collective variable as
well as an analysis, biased over exactly the contacts the analysis
measures, so the coordinate a surface is drawn along is the one the run
reports. Free-energy surfaces over two collective variables, judged one
dimension at a time on the free energy along each with the other
integrated out: a run can fill a torsion thoroughly while the distance it
was also biasing never left one basin, and a refusal names the dimension.
Periodicity is read per variable rather than once for the run.

Point mutations, written as `L99A` or `LEU-99-ALA`, with the original
residue checked against the structure. Numbering travels badly between
constructs, and applying a mutation to whatever sits at that position
produces results describing a protein nobody chose.

Campaign members are collected into one comparison. Where they differ only
by random seed they are repeats, and the spread of their means is set
against the error each run estimated for itself; where they differ by
system or parameter that spread is the result rather than an error, and
the record says which it decided and why.

### Provenance

The manifest records the versions of the packages that decide a number --
mdtraj, OpenMM, RDKit and the rest -- read from what was loaded rather
than probed, because what ran is what produced the numbers. A benchmark's
interaction table proved to differ between machines with identical
settings and identical perceived chemistry, and the record could not say
which environment produced which answer. Demonstrable and undiagnosable is
the worst combination, and this closes it for runs made from here.

A residue's interaction occupancy is exported as the union of its atom
pairs' frames, in `pl_interactions_by_residue.dat`. Read from the pair
table alone that union can only be bounded from both sides, and residues
touched through many atoms carried intervals wide enough to swallow the
difference under test.

### Validation, as a measurement

A guardrail corpus that reports both rates: thirteen studies with one
named defect each and seven ordinary ones where the right answer is
silence, with the expected response written down before execution.
Detection thirteen of thirteen; false refusals none of seven. Sensitivity
alone was never evidence, because a checker that refuses everything scores
perfectly on it.

Running the corpus found a metadynamics run whose coordinate never left
one basin passing the recrossing gate on movement within that basin -- on
a count whose own definition string said it measured whether the
coordinate moved rather than whether it changed state. The
two-dimensional path had been given a single-minimum reason and the
one-dimensional path had not.

The cross-tool comparison used for the benchmark now lives in the
repository, with its pre-registered thresholds under test, alongside a
comparison of one study run in several environments.


## [2.5.2] — 2026-08-13

Written after the fact, on 2026-08-16. This release went out without an
entry, which is how the conda recipe stayed on 2.5.1 through two releases:
the check that keeps the recipe level compares it against the changelog's
latest release, and both were equally out of date, so it passed.

The release that made the test suite run the physics it claims to test.
Optional scientific dependencies were installable and absent from
continuous integration, so whole families of chemistry and physics tests
skipped silently while the code read as tested -- the same defect found
three times over, in rdkit, then propka, then OpenMM itself. They are in
the test environment now, and the first all-green matrix followed.

Workers start from a clean interpreter rather than a forked one, which is
what let the spawn-based platforms run the suite at all, and a test that
hangs is bounded by a timeout rather than by a person noticing.

Documentation caught up with the code across nine files, and the README
was rewritten to say what the software is for before it says what it has.

Container images are attached to the release rather than parked as build
artifacts, after a mis-click left a 1.26 GB image expiring in ninety days
with no way to reach it from the release page.

## [2.5.1] — 2026-08-12

Everything a run tells you, told earlier and told better.

Five things worth knowing before a run starts now appear while the settings
are still choices, in the GUI beside the control they concern and in the run
before its first phase: a metal this force field will not hold in its site, a
box too small for the cutoff, a switching function the force field does not
want, a ligand with no chemistry, a density that will never be equilibrated.

A ligand supplied as a file is placed where the structure has it, not where
the file's own coordinates lie. An ideal component from the Chemical Component
Dictionary carries the chemistry a PDB cannot express and an arbitrary pose;
using that pose put a benzene seventeen Angstroms from the cavity it belonged
in, and the run succeeded. The same component appearing more than once -- a
cofactor in each half of a dimer -- takes successive copies rather than
declining.

A run lists the platforms OpenMM found and, where one is missing, what is
known about why. An installation whose plugin directory has moved offers only
the Reference platform and reports no failures, because it never looked: the
run is correct and about a hundred times slower, and nothing says so.

The nonbonded cutoff comes from the force field. CHARMM36 is developed at
1.2 nm with switching from 1.0; the AMBER force fields are developed with hard
truncation and are not switched at all.

Six messages that named a problem and stopped now say what to do about it: a
fetch with no route to the internet, a ligand's chemistry that cannot be
looked up, `ligand` given a residue name, `--include` used twice, an analysis
name no analysis has, and a structure that is not there.

An image carrying the whole stack -- including the OpenFF toolkit, which is
not on PyPI -- is attached to each release, for machines that cannot reach
conda-forge.


## [2.5.0] — 2026-08-10

**Recompute any metadynamics or umbrella result on a torsion or an angle.**
Separations on a periodic coordinate are now measured the short way round, in
the umbrella window stitching, the free energy surface, the bias each frame
felt, and the offset that corrects for it. Results on a distance or a radius
of gyration are unaffected, as are torsion studies confined to part of a turn.

Metadynamics recrossings are counted as travel between the two deepest basins
rather than past a fixed threshold, and the record names the basins.

The nonbonded cutoff comes from the force field: CHARMM36 at 1.2 nm with
switching from 1.0, AMBER hard-truncated at 1.0 with no switch. An explicit
setting still wins.

`workers` implies parallel execution and each worker takes a share of the
machine's cores. Capping groups survive setup and count as polymer.
`--force-overwrite` replaces `--force`, which still works. Analyses that
superpose are skipped where fewer than three atoms match. PLUMED's setup goes
to `simulation/plumed.log`. A study prints a progress bar.

Documentation leads with the config: one file describes a study, and the GUI,
the CLI and the Python API each build one and each run all four phases.


### Added
- **A metal in a protein site says the force field will not hold it there.**
  Standard force fields treat a metal ion as a point charge with
  Lennard-Jones terms and nothing else. That is enough for a carboxylate cage
  and often not for a metal held by histidines, so the ion drifts out of its
  site while the fold stays intact, the RMSD stays flat, and nothing else in
  the run reports it. Measured on thermolysin over 20 ns: all four calciums
  held their sites to within a tenth of an Angstrom, and the catalytic zinc
  lost His142 in the first production frame and never recovered it. Setup now
  measures which metals are actually coordinated -- rather than assuming from
  the residue name -- and names them, with the remedies. Salt stays silent.

- **The config is the study, said where somebody looks.** The README and the
  documentation index now lead with it: one file describes a study completely,
  and the GUI, the command line and the Python API each build one and each run
  all four phases. None is the primary interface and none is a subset -- they
  are generated from a single declaration, which 53 parity tests already
  enforced and nothing stated. The GUI page also understated itself: 159
  options across 15 analyses, when there are 200 across 19, and no mention of
  the 100 phase settings at all.

- **PLUMED's setup is kept beside the run.** Forty lines -- which atoms the
  collective variable is built from, the hill width, the pace, the bias
  factor -- written from C++ straight to the file descriptor, arriving in the
  middle of a progress bar. It goes to `simulation/plumed.log`: it is the only
  independent statement of what PLUMED actually did, and reading `TORSION
  between atoms 7 9 15 17` in it is how a psi selection was confirmed correct.

- **A study reads its own curve.** `pmf.json` carried a free energy and left
  the reading of it to whoever opened the file, which is how it got misread.
  The grid spans wherever the coordinate went, the windows covered a part of
  it, and a minimum taken across the whole grid references a region nothing
  visited -- a real study looked to have a 164 kJ/mol barrier that way,
  against 11 measured where the windows actually were. It now records the
  covered range and a summary: the barrier, where it sits, and every minimum
  sorted by depth. The `pmf` analysis reports them instead of only drawing a
  line.

- **A closed turn measures itself.** The two ends of a full period are the
  same place, so their free energies must agree. They arrive there from
  different windows through a chain of joins and nothing forces them to, which
  makes the difference a measurement rather than a fault: it is what the
  study's statistics are worth. Reported, not constrained -- forcing the ends
  together would make the number zero and take the information with it. A
  well-sampled synthetic profile closes to nothing on its own; a real study of
  0.2 ns windows closed to 2 kJ/mol, which is that study's uncertainty stated
  honestly. A partial turn carries `None` rather than zero, because "this was
  never a circle" and "this closed perfectly" are opposite claims.

- **The simulation phase now says why each stage exists.** `explain.py` opens
  by saying a pipeline that does all this silently "teaches nothing", and that
  held for setup and not for simulation: the entries for minimisation, NVT,
  NPT, production and the ensemble were written, and none of them could be
  reached. The runner announces through a callback carrying a message and
  nothing else, and the presenter could print an explanation from `step` but
  not from `info`, which is what stages use. Both ends existed and were not
  joined. A real run explained its protonation and its solvation, then went
  quiet for the twenty minutes in which the ensemble is chosen.

- **What NVT and NPT are for, and what choosing one does to production.**
  Both steps had an explanation and neither had a reference. They now cite
  the LiveCoMS best-practices guide and the Monte Carlo barostat OpenMM
  actually uses, and the NPT text carries the measured number rather than
  calling the error "usually a little wrong": solvation packs a box about ten
  per cent short of water, and only a barostat corrects it.

  A third explanation covers the question neither answered -- why you would
  run only one. NVT production is legitimate at a density you know is right,
  and the way you learn that density is to run NPT first and take the average
  box size from it. Going straight to NVT is a different thing entirely: it
  fixes the box at whatever solvation produced. That is the arrangement two
  runs here used, at 0.92 g/mL, and it is the one nobody intends.

- **The README says what a biased run's averages mean.** The front page
  listed the three enhanced sampling methods and what each output is and is
  not, and stopped there -- so the most distinctive thing the analyses now do,
  correcting those averages back to the ensemble somebody actually wanted, was
  not on it. The closing paragraph now names the harder case too: a run that
  does not support the thing it was for is told so in those terms.

- **The reweighting is documented.** What the analysis phase writes to
  `analysis/reweighted/`, the estimator it uses and why it carries the c(t)
  offset, which analyses are corrected and which are not, and why an umbrella
  window and a steered pull cannot be. The two details that are easy to get
  wrong are stated rather than left implicit: weighting by the final surface
  inflates early frames, and without c(t) the weights rank frames by when they
  were written instead of by where the system was. Pinned by tests, so an
  analysis that gains a correction and does not gain a sentence fails.

- **Each enhanced-sampling method reports the result it exists to produce.**
  A metadynamics run wrote its free energy surface to JSON and stopped there:
  no figure, no entry in the analysis manifest, no mention in the report.
  Sixteen analyses of the trajectory each produced a curve and a plot, and the
  one result the run was for did not. The same gap held for an umbrella
  study's potential of mean force and a steered pull's work.

  There are now three analyses -- `pmf`, `metad_surface` and `steered_work` --
  each reading what the biased run itself recorded rather than recomputing it,
  each drawing it, and each running only where such a run produced one. They
  are not analyses of the trajectory and do not need a ligand.

- **A biased trajectory says what it is.** The methods section described
  restraints well and said nothing at all about the three methods where
  biasing is the entire point. Ten analyses were reported beside the free
  energy with no distinction between them, and a reader would take a mean
  RMSD over a metadynamics run as a measurement of the system.

  The three are not alike, and one caveat covering all of them would be wrong
  about two. Metadynamics deposits a known bias, so the unbiased ensemble is
  recoverable by weighting. An umbrella window describes a system held where
  it was put, and what combines the windows is the free energy rather than an
  average across them. A steered pull is not an equilibrium ensemble at all.
  Each now gets the paragraph that is true of it.

- **The restraint ladder steps across equilibration rather than at its
  boundaries.** The strength was sampled at two points, before NVT and before
  NPT, so a four-rung ladder reached the first and third rungs and never the
  others. With `npt_steps: 0` the second sample sat inside a branch that never
  ran, and the restraint held at full strength through all of equilibration
  and dropped to zero at production -- the release all at once that a ladder
  exists to prevent, from a setting the user had written out in four steps.

- **The GUI cites the software.** The report, the slides and `fastmdx info`
  all print the citation and the GUI printed none, which made the interface
  the documentation sends a new user to first the one that never said how to
  cite the work. There is a Cite page with the reference, the DOI and the
  BibTeX entry, and the report's own dashboard carries it in its footer.

  Filled by the server from the same constants, because writing it into the
  page would have put a fourth copy beside the report's, the slides' and the
  CLI's. There were already two copies of the BibTeX entry; it is now declared
  once beside the citation it belongs to, and a test fails if it is written
  out anywhere else.

- **A progress bar during molecular dynamics.** The part of a run that takes
  the time announced how many steps it was about to take and then said nothing
  until the stage ended -- half an hour of a terminal that looked exactly like
  a hung one. Each stage now steps in chunks of a fiftieth and reports how far
  through it is, at what rate in ns/day, and how long is left, so the decision
  to wait or to stop can be made in the first few seconds rather than at the
  end.

  On a terminal it is one line, rewritten. Where the output is a file -- which
  is where a worker's output goes when several run at once -- a line of
  carriage returns is unreadable, so it prints at tenths instead.

- **A mean says how much of a run is behind it.** Ten analyses averaged over
  the whole production run without asking whether the system had settled by
  the time the averaging started, or how many *independent* observations the
  average rested on. Frames are not independent: a trajectory written every
  picosecond from a system decorrelating over a hundred has a hundred times
  fewer independent samples than frames, and an error computed as though each
  frame counted is understated -- six-fold on a test series, which is how a
  difference between two systems becomes significant on paper without being
  real.

  Both questions have one answer. The statistical inefficiency is the number
  of frames per independent sample, so it fixes where the relaxation ended
  (Chodera, J. Chem. Theory Comput. 2016, 12, 1799) and what the mean is
  worth. Below ten independent samples the mean describes the run rather than
  the system, and it is refused.

  Recorded by the base class for any analysis declaring that it produces one
  value per frame -- six of the sixteen -- so `findings` carries the mean, its
  error, the frames discarded and the independent samples behind it. Declared
  rather than inferred from the array's length: a per-atom result on a
  trajectory with as many frames as atoms would otherwise be summarised as a
  time series, and the numbers would look right.

  A run too short to measure its own correlation time is refused separately,
  because that failure flatters: the inefficiency comes back too small and the
  sample count too large. On a test series with a true inefficiency of 2000,
  four thousand frames gave 361 and an apparent eleven independent samples
  where the truth was two.

- **A metadynamics run produces a free energy surface, or refuses one.** It
  wrote PLUMED's HILLS and COLVAR and stopped: nothing read them back, so the
  module said "a run that has not converged has no free energy" while offering
  no free energy either way, and whoever wanted a surface summed the hills
  themselves and got one with nothing attached. Umbrella sampling went from a
  config block to a curve or a refusal; this went to a pair of files.

  Three conditions, each answerable from what the run already writes: the
  hills must have decayed, so the bias has flattened rather than still filling
  the landscape; the surface built from three quarters of the hills must match
  the one built from all of them; and the system must have crossed between the
  ends of the range more than once, because a barrier crossed once has been
  observed once. The evidence is written down beside the verdict either way,
  so a borderline run can be judged rather than only rejected.

- **A run records which code made it.** The manifest carried the version
  string and nothing else, and setuptools-scm writes that at install time --
  so an editable install carries whatever it was when `pip install -e .` was
  last run. A study of ours came back stamped `2.3.0` for seven windows that
  used shared setup, a feature `2.3.0` did not have: the manifest named a
  version in which the run could not have happened, and it is the number the
  report's reproducibility section prints.

  Where the package was imported from a checkout, the commit is recorded
  beside the version, and so is whether the tree had uncommitted changes. The
  checkout is found from the package rather than the working directory,
  because a run started inside another repository would otherwise record that
  repository's commit -- a precise claim about the wrong code, which is worse
  than recording none. A dirty tree is reported rather than refused: refusing
  would block the runs developers make all day, and a commit beside
  uncommitted changes does not describe what ran, so saying so is what keeps
  the commit from being decorative.

- **The banner says how to watch the run.** It computed the dashboard address
  and discarded it, on the reasoning that the GUI prints its own -- true while
  a server is running, and no help to somebody who has just started a run.
  Where none is running it prints the command that starts one, pointed at this
  run's output; where one is, the address.

### Changed
- **One page for one run.** Overview and Live Simulation showed the same run,
  and the top bar showed it a third time: three places to look for one answer,
  and neither page complete on its own. They are one page now, with what is
  happening at the top -- progress, charts, health, the structure -- and what
  has been recorded beneath it. The panels were moved rather than rebuilt, so
  every element the script reads is where it was. Two cards were both called
  progress; one is a bar for the running stage and the other a table of
  phases, and they now say so.

- **Watching a run is on by default.** The live dashboard could show nothing
  unless somebody knew to turn on a setting called telemetry -- a word that
  elsewhere means data sent to a vendor, and here means four files written
  into the run directory for the local page to read. It cost a tenth of a per
  cent, measured on a solvated system over 2,000 steps with and without, and
  the frame history is capped, so the only thing the default saved was the
  ability to watch.

- **The GUI asks to be cited where somebody will see it.** The citation page
  existed and was the last item in the sidebar, beneath Documentation and
  GitHub under a heading reading Tools -- so the one thing a scientific tool
  most needs its user to find sat after the two links that navigate away from
  it. It comes before them now, and the sidebar footer, which shows on every
  page, carries a line linking to it.

- **The interface is called the GUI.** Documentation and comments used "the
  browser" for both the interface and the thing it renders in, so the three
  interfaces read as "the command line, a config file, and the browser". A
  browser tab and the `--no-browser` flag are the literal thing and keep the
  word; everything else naming the interface says GUI.

### Removed
- **The `datasets` package.** It promised a Trp-cage trajectory that was never
  bundled: `TrpCage.traj` returned the path to a file that had never existed,
  from v0.1.0 through v2.4.0, so reading it gave a plausible string and
  passing it anywhere gave a file-not-found from inside a trajectory reader.
  Nothing replaces it. FastMDXplora fetches a deposited structure and
  simulates it, so a reference trajectory carried in the distribution would be
  megabytes of wheel for something one command produces --- and a reference
  system is a PDB identifier, not a file.

### Fixed
- **The reported free energy surface was summed the long way round.** The
  periodic separation was added to `surface_from_hills` and the only two
  places that call it were left on the default, so the number every run
  reported came from the arithmetic that had just been fixed. A converged
  10 ns metadynamics run on alanine dipeptide's psi reported 60.7 kJ/mol; the
  same hills give 25.8. Every report, dashboard, slide deck and bundle
  downstream carried the wrong figure, and the analysis phase does not
  recompute it -- it reads what the simulation phase wrote, so re-running
  analysis reproduced the error with a fresh timestamp.

- **A recrossing was counted past a line rather than between states.** The
  thresholds sat a quarter of the way in from the extremes of the hill range,
  which describes the grid and not the system. On a full turn that put them at
  -90 and +90 degrees, and a run whose minima were at -17 and 155 had one of
  them inside the dead band. The count is now travel between the two deepest
  basins, each frame assigned to the nearer one measured the short way round.
  The same run: 925 became 349, against a threshold of 4.

- **Two more analyses fitted a rotation to one point.** `cluster` and `dimred`
  superpose on `name CA` exactly as `rmsd` and `rmsf` do, and were not gated
  when those were. A capped alanine was clustered on identity rotations,
  sklearn found one distinct cluster where five were asked for, and the run
  reported `ok`. The test now asks the source which analyses superpose rather
  than trusting a list.

- **`precision` was reported as applied when the platform has no such
  setting.** OpenMM's CPU platform offers `Threads` and `DeterministicForces`
  and nothing else. The banner now says what was applied, and the diagnosis
  after a non-finite coordinate no longer suggests double precision there.

- **A path was handed back with its separators changed.** The directory the
  banner suggests watching was split with `pathlib` and rebuilt, which
  normalises separators, so a Windows caller who wrote `../runs/study` was
  given a correct path that was not the one they typed. Found by Windows CI,
  on a function three green macOS runs had passed.

- **Every force field got the same cutoff, and two of the four were wrong for
  it.** The default was 1.0 nm with switching from 0.9, applied to all of
  them. CHARMM36 is developed at 1.2 nm with switching from 1.0, so it was run
  0.2 nm short with the switch in the wrong place; the AMBER force fields are
  developed with hard truncation and were being switched, which moves a run
  away from the parameterisation rather than towards it.

  This is not a preference. A force field is fitted with a particular
  treatment of the truncation and the effects of that truncation are
  compensated in its other parameters, so the scheme belongs to the force
  field rather than beside it -- and CHARMM36 at AMBER's cutoff is a different
  force field from the one that was validated. Each registry entry now carries
  its own, an explicit `nonbonded_cutoff_nm` or `switch_distance_nm` still
  wins, and the run says which scheme it used and why.

  What OpenMM applies is the potential-based switching function; CHARMM's is
  force-based, and the toolkit does not have it. Lee et al. say so directly in
  the CHARMM-GUI Input Generator paper and prescribe this protocol for OpenMM
  regardless, having tested a range of cutoff schemes against CHARMM's own
  results. Naming it "CHARMM's switching" would claim a function that is not
  there.

- **A torsion study that covered the whole turn returned a ramp.** The bias
  on a window was `0.5 * k * (x - centre)**2`, a straight-line subtraction. On
  a circle that is wrong at the wrap: a sample at +170 degrees is ten degrees
  from a window held at -180, not three hundred and fifty. The code charged it
  933 kJ/mol instead of 0.76 -- a Boltzmann weight wrong by a factor of
  10^162 -- so that window's free energy was pushed up and every join after it
  inherited the error.

  Twelve windows tiling a full turn of alanine dipeptide's psi returned a
  monotonic slide of 180 kJ/mol with no minimum anywhere in it, and every
  check passed: twelve runs finished, no unsampled bins, every overlap above
  the threshold, no refusal. A tool that refuses when it cannot support a
  claim produced a confident wrong one. Corrected, the same windows give two
  minima at -15 and 159 degrees and two barriers between them, 10.4 kJ/mol one
  way round and 23.7 the other.

  The scope is worth stating precisely: a study confined to part of the turn
  is unaffected. The nine-window study before this one kept its windows away
  from the wrap and gave 11.0 kJ/mol before the fix and 11.5 after -- the
  difference being the periodic distance now applied throughout, where
  subtraction had been merely close. It also found the easier of the two
  barriers by luck of which arc it happened to cover.

- **Asking for workers did not ask for parallelism.** `mode` defaulted to
  sequential and `workers` was read separately, so `workers: 3` ran the runs
  one at a time and said nothing about it. Nobody sets a worker count wanting
  one at a time, and nobody lists GPUs to leave all but one idle. `mode`
  written out still wins.

- **Parallel workers competed for the whole machine.** OpenMM's CPU platform
  takes every core it can see, and a pool of workers each doing that
  oversubscribes by the worker count -- so `workers` could not be used well on
  CPU at all. Each worker is now given `cores // workers` threads, set in the
  worker process because the libraries underneath read it once at import.

- **Capping groups were stripped as heterogens.** ACE, NME and the rest
  terminate a chain and are part of the molecule. Removing them is right for
  buffer and cryoprotectant and wrong here: it left a bare alanine wearing
  atoms from its caps, and the run failed with "no template found for ALA",
  naming the residue that survived rather than the two that did not.

- **A rotation was fitted to one point.** RMSD and RMSF align on `name CA`,
  and a molecule with one alpha carbon has no superposition. MDTraj's C
  extension printed "UNCONVERGED ROTATION MATRIX. RETURNING IDENTITY" once per
  frame -- thousands of lines in a window's log -- and returned distances
  measured against no alignment at all, which look like results. Both now
  declare a minimum of three atoms and are skipped below it, the way a chain
  too short to have a fold already is.

- **`--force` did not say what it would force.** It is `--force-overwrite`
  now, with the old spelling kept as an alias so existing scripts still work.
  Three refusals still named the old one, which is worse than either name
  alone: it sends a reader to look for something the help no longer lists.
  A test scans for stragglers, because a rename is exactly the change that
  leaves messages behind.

- **A study printed a sentence a minute for an hour.** Nine windows on a
  laptop produced sixty near-identical heartbeat lines, burying the ones that
  mattered -- the windows finishing, and anything that went wrong. It draws a
  bar instead, redrawn in place where there is a terminal to redraw on and
  written out in full where the output is a file.

- **The banner said to watch a directory that would not change again.** An
  umbrella study prepares one system every window shares, and the preparation
  announced where it writes: accurate, and a second of setup finished before
  anybody could type the command. It points at the study now.

- **The NPT explanation gave one number where the effect is not one number.**
  It said solvation packs a box "roughly ten per cent short of water", from
  two runs that happened to sit at the same padding. Measured against OpenMM
  directly across box sizes, with and without a solute: pure water packs at
  0.96 to 0.99 whatever the size, a solute at 1.0 to 1.2 nm of padding brings
  it to about 0.90, and the same solvation at 2.0 nm reaches 0.96. The gap is
  the vacuum shell left around the solute, and it matters in proportion to
  how small the box is -- so it is described that way now, and the run
  reports its own number rather than a claim standing in for it.

- **The reconstructed bias was 11.1% too large on every well-tempered run.**
  PLUMED does not store the height it deposited. For a well-tempered run it
  stores that height multiplied by y/(y-1), so that summing HILLS gives the
  free energy directly -- which is the convention the free energy surface
  relies on and which stays. Reconstructing the *bias* needs it undone, and it
  was not: a run asking for 1.2 kJ/mol had 1.333 in HILLS, and every bias
  summed from that file was too large by the same ninth.

  It does not cancel. Both V and c(t) scale by the same factor, so their
  difference scales too, and the weights go as exp((V - c(t))/RT) -- a scaled
  exponent is an effective-temperature error, which sharpens the weights,
  understates the effective sample size and biases every average resting on
  them. Found by comparing against PLUMED's own record of the same quantity on
  a real 1L2Y run, which disagreed by 11.5% of the bias range while every unit
  test passed, because the test fixture wrote HILLS the convenient way rather
  than the way PLUMED writes it. The fixture now writes what PLUMED writes.

- **A hill was felt at the instant it was laid.** PLUMED prints the bias for
  a step before depositing that step's hill, and HILLS and COLVAR usually
  share a stride, so counting the hill as already felt was wrong on every
  row. On a real run it left the reconstruction out by 1.200 kJ/mol against
  PLUMED's own record -- exactly the configured hill height, which is what
  identified it once the larger height-convention error was removed. The
  frames and the c(t) checkpoints now take the same boundary, and the test
  fixture computes its reference bias the way PLUMED reports it rather than
  the way that was easier to write.

- **The collective variable was recorded as `cv`.** That is PLUMED's label for
  the column, not the name of what was biased, and it is what the provenance
  record carried. The configured name is used where it can be found, with the
  label kept beside it.

- **A run did not record its own box.** The setup record kept the atom count
  -- added because a methods section has to state it and it was only ever
  logged -- and not the periodic cell, which a methods section states for the
  same reason. Diagnosing a failed run meant reading CRYST1 out of
  `solvated.pdb` by hand to find out whether the box had ever been big enough
  for the cutoff. It now records the vectors, the perpendicular widths, the
  volume and the largest cutoff the box can carry. The widths matter rather
  than the edge lengths: for a rhombic dodecahedron the smallest width is the
  edge over root two, so reading the edge alone overstates the room by 40%.

- **The line announcing a bias said it was happening, not that it would.**
  All three methods print where their PLUMED script is written, which is
  before minimisation, in the present tense -- "Metadynamics biasing
  radius_of_gyration". Biasing is added just before production and
  equilibration runs unbiased, correctly, but the log read as though the bias
  were live from the first step. It sent the diagnosis of a real failed run
  off after the bias for a while when equilibration was the only thing that
  had run. All three now say which stage they apply to.

- **A NaN raised by the integrator never reached the diagnosis written for
  it.** The module that reads a failed state -- which atoms went non-finite,
  what residues they belong to, and what that points at -- was wired only to
  the check that runs at a stage boundary. OpenMM detects a non-finite
  coordinate during integration and throws, so that check never ran, and the
  common way a simulation dies fell through to the generic list of settings to
  try that the diagnosis exists to replace. A real 1L2Y run hit it after
  eighteen minutes and was told to lower the timestep, lower the temperature,
  raise the friction or turn off NPT, without anything knowing which applied.

  Both `step()` sites now recover the state from the context, which survives
  the exception and still holds the coordinates that went wrong. OpenMM's own
  message is kept beside the diagnosis rather than replaced by it: it says
  what the integrator noticed and links to its FAQ, while the diagnosis says
  which atoms it happened to.

- **An umbrella window and a steered pull reported their averages as though
  the run had been ordinary.** Metadynamics gained a reweighted column and an
  effective sample size beside every mean; the other two biased methods gained
  nothing, so their dashboard rows read `RMSD` exactly as a plain simulation's
  would. That is the worse case rather than the milder one: a window is held
  where it was put and a pull is not an equilibrium ensemble at all, so unlike
  metadynamics there is no corrected number to set beside the raw one.

  The analysis phase now records that a run was biased even where it cannot
  undo the bias, naming the method from the PLUMED script it wrote. The report
  leads with what the averages are not, the dashboard labels every metric as
  being of a biased ensemble, and neither invents a corrected column -- which
  would be the same numbers under a heading claiming otherwise. The wording is
  the methods section's own, so the two cannot drift apart.

- **Two config spellings with one meaning were refused.** A phase block
  present but empty -- `analysis:` with only a comment under it -- parsed as
  null and was rejected, while leaving the key out entirely was accepted:
  two spellings of the same intent behaving differently, when an option set to
  null is already read as "use the default". And `--include setup,simulation`
  reached the validator as a single string, because argparse takes a
  comma-separated list as one item. No phase or analysis name contains a
  comma, so neither reading is ambiguous. Both are now settled before
  validation, and a block of the wrong type -- a number, a list -- is still
  refused, because that is a real mistake rather than a spelling.

- **Metadynamics did not run at all.** The collective-variable plan was bound
  to the name the stage plan already used, so the next line to subscript it
  raised `'MetadynamicsPlan' object is not subscriptable`. The feature failed
  on the first line of use. Thirty-eight tests covered the module and
  twenty-three the surface; none of them ran the runner, which is where the
  two names met. A test now runs it.

- **A steered pull's work record was lost to a race.** PLUMED buffers its
  output and writes on teardown, and the record was built at the end of the
  simulation phase -- before the force was finalised. `COLVAR` existed, held
  no rows, and the record was silently not written; run by hand afterwards on
  the same file it worked. The pull now flushes as it goes.

- **A metadynamics refusal was cut mid-sentence.** Clipped at 160 characters,
  it lost the clause saying the output is still a usable snapshot of the
  filling. This is the one message whose only job is to explain why there is
  no surface, and a refusal that runs long is a refusal with something to say.

- **The surface's drift was judged where it means least.** Convergence was
  assessed over the whole grid, including regions far above the minimum that
  are visited rarely and move by several kJ/mol however long the run. Drift is
  now judged within 20 kJ/mol of the minimum, with the whole-grid figure still
  reported beside it.

- **A solvated tripeptide passed the fold gate.** The check counted every
  residue in the box -- 529 for a tripeptide in water -- so a chain far too
  short to have a fold was let through, and the Q-value analysis then sliced
  to the solute, found three residues, and failed. It counts the solute's
  residues now, and skips rather than errors.

- **Documentation that had gone stale without anything noticing.** The
  collective variables were listed as five in three places when there are
  eight, and the README said eight, so the two contradicted each other. The
  analysis count had outlived three additions. `pmf`, `metad_surface` and
  `steered_work` were filed under "Protein and ligand together", where a row
  lands if it is appended to the end of the file, implying they need a ligand.
  Each claim was true when written. They are now checked by tests, because a
  count is exactly the kind of thing that goes wrong quietly.

- **A page watching a run said every phase was "Not run".** It showed that
  run's energy, its temperature and its speed in ns/day above a table
  reporting nothing had happened. The table reads the manifest, and the
  manifest is written when a run finishes -- so "Not run", which is a claim
  that a phase did not happen, stood in for "has not finished". A phase in
  progress now names its stage, the ones before it are complete because a run
  that is simulating has finished preparing, and the ones after are pending.
  An empty directory still reads as not run, because that is a different
  thing.

- **The chart titles were drawn over the charts.** Absolutely positioned at
  the top-left of each canvas, each sat exactly where the chart draws its
  y-axis labels, and the two overlapped as soon as there was data to label.

- **The GUI re-read the whole trajectory on every mount, and said so.** Its
  terminal filled with `dcdplugin) detected standard 32-bit DCD file`, a pair
  of lines every few seconds for as long as it was open. The lines are VMD's
  DCD plugin, which MDTraj wraps, announcing every file it opens on the
  C-level file descriptor where Python's logging cannot reach it.

  The noise was the symptom. The viewer asked for the playback payload with
  `force=1` whenever the browser did not already hold one, so every mount and
  every reload bypassed the disk cache and re-streamed the trajectory to
  rebuild a file already sitting beside it. Force means the user asked for a
  rebuild. The reader is also silenced, so a legitimate read no longer writes
  to a server's terminal.

- **The Live Simulation page rendered an apparatus for reading data it did
  not have.** Opened on a run without telemetry it showed every field --
  current stage, current step, total planned steps, frames written, simulation
  time, elapsed time, checkpoint, last update -- reading "not available",
  twelve of them, above empty charts. It now says why, once, and shows nothing
  else.

  And it explained the absence by advising the reader to start the dashboard
  during a simulation, which is what somebody looking at that page has just
  done. Live telemetry is written only when a run asks for it and that is off
  by default, so the advice sent them to repeat what had already failed. The
  message names the setting and the flag that turn it on.

- **The reproducibility section implied a rerun would reproduce the study.**
  It gives an equivalent one. Solvation places water by a procedure that
  cannot be seeded -- OpenMM's `addSolvent` takes no seed -- so the same
  configuration run twice gave 37,251 atoms and then 37,763, with
  `random_seed` fixed both times: the seed fixes the dynamics, not the
  solvent. The section says so, and names what does repeat a study exactly,
  which is to simulate from the system it prepared.

- **The same observable was reported twice in one document with two different
  means.** A real study's RMSD read 0.08895 in the convergence table and
  0.09461 in its own results section, both labelled the mean, with nothing to
  say why. The table averaged the whole series; the per-analysis finding
  discarded the relaxation first, as the method requires. The table now takes
  the same settled part, so the two agree by construction, and it says how
  many frames were discarded. Drift is still measured over the whole series,
  since that is the question of whether the run was still relaxing.

- **The summary called a run settled that held six independent samples.**
  Settled and adequately sampled are different questions, and it reported only
  the first: a study whose RMSD held six independent samples and whose density
  held ten was described as having entirely settled. It now says both.

- **A study of one system printed one line and then nothing.** Each run's
  output goes to its own log so that several running at once do not interleave
  into an unreadable screen -- and that was applied to a single run too, which
  has nothing to interleave with. A config naming one system therefore said
  "Exploring 1 molecular system" and went quiet for as long as the run took.
  Output is redirected only where it would collide.

- **The reference conda recipe had fallen behind the feedstock.** It exists
  so a dependency added here reaches the package, and the traffic runs both
  ways: the feedstock learns what the conda-forge solver does and what its
  review asks for, and two such lessons had not come back. Its tests import
  the small-molecule stack, because declaring a dependency is not the same as
  it resolving and a broken solve should fail the build rather than somebody's
  first protein-ligand run. And its pip check is disabled, because
  openff-toolkit pulls in AmberTools components declaring numpy<2 against a
  numpy 2.x environment -- a reason recorded there and nowhere else, so a
  regeneration from this copy would have reintroduced a build failure already
  diagnosed. Both are back, with a test for each, and the recipe says
  graphical interface where it said browser.

- **A surface-area calculation that did not finish writing is computed
  again.** On Windows, MDTraj's `shrake_rupley` returns a final frame that was
  not fully written -- a partial value followed by zeros -- on the first call,
  and the same frame complete on a second call to the same trajectory. Frames
  other than the last come back bit-identical.

  The truncated answer identifies itself by how much of a frame reads exactly
  zero: the fault leaves a row like `[0.5354027, 0, 0, 0, 0]`, four fifths of
  it zero against none in the frames around it. Compared against the run's own
  median, because a real protein has buried residues whose surface area is
  exactly zero and which flicker between zero and a little above it as the
  structure breathes. Where a frame stands out the areas are computed again,
  up to five times, and the complete result used with the run recording that
  it had to be.

  Three things about the defect were assumed and all three were wrong. It is
  not confined to the final frame -- frames 0, 2 and 5 have been seen. A
  second call is not reliably clean. And the first attempt to recognise it
  asked whether a residue was exposed in some frames and exactly zero in
  others, which is true of every buried residue in every protein: it refused a
  solvated T4 lysozyme outright, five attempts and eighty seconds before
  giving up, on a run that was perfectly good. A test now measures the rate
  over twenty calls rather than assuming a shape, and its failure message
  carries what a fix would need.

  This is a workaround, not a rescue, and the distinction is the one this
  package draws elsewhere: a failed simulation is diagnosed and stopped
  because a salvaged trajectory looks like one that never needed salvaging.
  Here the wrong answer identifies itself and a correct one may be one call
  away.

  Without it, an unwritten frame pulls a six-frame mean down by a sixth, and
  the number reaching a report, with an error bar on it, looks like a
  measurement. Reported upstream; it affects anyone computing
  solvent-accessible surface area on Windows.

- **A SASA test was asserting a property of MDTraj.** Two routes to a
  per-residue average each called the surface-area calculation once and
  compared the results, so the test was also asserting that two identical
  calls agree. On Windows they did not: by eight per cent on one residue in
  one run, then by fifteen per cent on all five in the next, with the
  per-frame values matching every other platform and the direct route alone
  coming out low. Different Python versions failed each time -- 3.11, then 3.9
  and 3.12 -- which is nondeterminism rather than a version-specific bug.

  The two routes are now given the same areas, so the test asks about this
  package's arithmetic and nothing else, and a separate test calls the
  surface-area calculation ten times on one trajectory and requires the
  answers to be identical. If that one fails, the fault is upstream and the
  test is the reproduction to send there.

- **A mean over frames was accumulated in single precision.** `numpy.mean` on
  a float32 array sums in float32, so the average exposure of a residue lost
  digits over a long run that the per-frame route -- grouped by pandas in
  double -- did not. The two answers then differed in their last figures for
  no reason a reader could guess. Both are in double now.

- **A results section carried a figure and no number.** The mean, its
  uncertainty and the independent samples behind it are recorded for every
  per-frame analysis and were shown nowhere; each analysis now states what it
  measured, with the reason attached where the run cannot support it.

- **An analysis was described as running with default options beside a list of
  the options it ran with.** A `for ... else` runs its else when the loop
  finishes without a break, which is every time.

- **The shareable archive omitted the record of what produced it.** The bundle
  carried every output including the trajectory, and neither `manifest.json`
  nor `resolved_config.yml`: not by exclusion, but because both are written
  once every phase has finished and the bundle is built during the report
  phase, so they did not exist yet. A recipient got thirteen megabytes of
  results with no way to trace them and no file to rerun them from -- while
  the module's docstring said it contained the manifest. They are added once
  they exist.

- **The dashboard header showed the input path** where the report and the
  slides show the system's name. Its output-folder field and its
  `fastmdx gui --output ...` instruction keep their paths: that page is opened
  on the machine that produced the run, and both need one to be of any use.

- **The summary said nothing about the study.** The first section a reader
  reads was "This report was generated automatically by FastMDXplora from the
  outputs of an end-to-end molecular dynamics study" -- a statement about the
  software, in a document about their system. It now says what was simulated,
  for how long, at what temperature, and how much of the run can be
  interpreted, so the caveat arrives before the results rather than six pages
  after them.

- **The slides were a slideshow.** Twenty-one slides carrying twelve figures
  and not one number; three of them displaying filesystem paths from the
  machine that produced them; and the whole deck in 4:3, which shows on a
  modern projector with a black band down each side. The deck is 16:9, the
  path slides state how the system was built and how it was simulated, and a
  final slide gives every observable's mean with its uncertainty and the
  independent samples behind it -- from the same assessment the report uses,
  so the two cannot disagree.

- **The slide outline described a deck that no longer existed.** Three of its
  five sections read "See `setup_parameters.json`". It is built from the same
  content as the slides.

- **The methods section stated a ligand that was never there.** Run on a
  tri-alanine in water it read "The ligand LIG was parameterized with
  openff-2.2.1". Both values behind that sentence are defaults: LIG is the
  naming convention for a ligand if there is one, and the small-molecule force
  field is a property of the protein force field, present whether or not it
  was used. The sentence therefore appeared in every run. It is now written
  only where setup recorded that it prepared a ligand, which is the evidence
  rather than the setting. A methods section is the part of a report that gets
  published.

- **And stated the wrong provenance for the coordinates.** Whether they came
  from the Protein Data Bank was decided by the input being four characters
  long, so a local file named `abcd` was described as a deposited structure.
  Setup records the input form; it is read.

- **The report title carried the author's home directory.** "FastMDXplora
  Study --- /home/aaina/ala3.pdb", on the first line of a document meant to
  be sent to somebody. The title names the system: `ala3`, or `181L` where the
  input was a PDB entry.

- **A requested output that could not be produced left no record.** The
  terminal warned that WeasyPrint was absent and no PDF would be written, and
  the manifest did not: read from its files afterwards, the run showed four
  formats where five were asked for, with nothing to say the fifth had been
  attempted. `not_produced.json` records what was asked for, and why it is not
  there.

- **A message advertised an input the software does not accept.** Asked to
  classify something it did not recognise, setup replied that it expected "a
  PDB file path, a 4-character PDB ID, or a one-letter amino-acid sequence" --
  and a sequence, passed, is recognised and then refused two steps later,
  because building a structure from one needs a predictor this software does
  not carry. The message now says what works, says plainly that a sequence
  does not, and the refusal names what to do instead. Found while checking a
  manuscript's claims against the code, where it had already propagated.

- **One implementation of the correlation statistic, and one verdict from
  it.** The report layer already measured independent samples from the
  autocorrelation time; a second implementation was written for the per-frame
  analyses before that was noticed. They agreed to two decimal places on every
  correlated series and disagreed on a constant one, where the report layer
  was right -- a series that never changes is one observation however many
  frames of it there are. There is now one function.

  They also asked differently whether a series can measure its own correlation
  time. The report's rule was a tenth of the run, which is the usual working
  limit and lets the flattering case through: a series with a true correlation
  of 2000 measured 361 over 4000 frames, and 361 is under a tenth of 4000. Both
  layers now halve the series and ask whether the estimate moves, so one run
  cannot be measurable in the report and unresolved in the findings.

  It lives at the top level, beside the other cross-cutting modules, rather
  than inside the analysis package: nothing registers it, it produces no
  figure, and the report asks it the same question the analyses do.

## [2.4.0] — 2026-08-05

This release is about making a simulation go where an ordinary one will not.

Most of what is interesting in a molecular system happens too rarely to see.
A ligand unbinds once a second and a simulation runs for a microsecond, so
plain molecular dynamics watches the bound state fluctuate and learns nothing
about leaving it. Enhanced sampling pays for the rare event with a bias, and
the whole difficulty is knowing what the biased run entitles you to say.

Three methods arrive together, and each is explicit about what its output is
and is not: metadynamics gives a free-energy surface if the bias converged,
steered MD gives a pathway and the work along it and *not* a free energy, and
umbrella sampling gives a potential of mean force if the windows overlap. That
last one refuses more often than it produces -- which is the point. A seven-
window study of benzene leaving the T4 lysozyme cavity was refused here
because two adjacent windows shared no sampling at all, and a tool that
returned a curve for it would have drawn a line through a region nothing
visited.

### Added

- **Umbrella sampling, from a config block to a free energy.** One block
  expands into a run per window, which is the shape the batch machinery
  already runs, so scheduling, parallelism and per-GPU pinning come from the
  code that already does them. Each window writes its own PLUMED, the COLVARs
  are read back, the approach to each window is discarded, and the sampling is
  recombined. Verified end to end from files on disk: a barrier of
  10.1 kJ/mol against a known 10.0.

  Four refusals -- windows that do not overlap, a missing window, several
  systems at once, and two ways of biasing the same coordinate. A gap is told
  apart from its commonest cause: windows that never reached their centres,
  which want the opposite remedy from windows that simply do not touch. And
  nothing is concluded from histograms a run did not fill -- below
  `minimum_samples` the overlaps are reported and no free energy is offered,
  because an overlap from tens of points is noise given a decimal place.

  The block's own settings are checked, which nothing had done: a one-letter
  misspelling of `minimum_overlap` was accepted, ignored, and the study
  stitched at the three per cent default while its author believed it had
  demanded fifteen. Every refusal above could be switched off by a typo.

  How much overlap is enough is a judgement about how much evidence a joint
  needs, so `minimum_overlap` belongs to whoever is making the claim; three
  per cent is enough to stitch and it is thin. The refusal states the
  threshold it applied, so a genuine gap can be told from a strict setting.

- **One prepared system for a set of umbrella windows.** Seven windows of one
  study came out with 37,212, 37,254, 37,436 and 37,445 atoms: four different
  systems for one measurement. Solvation does not place water the same way
  twice, so preparing each window separately makes part of the difference
  between windows the solvent rather than the restraint. The system is
  prepared once into `shared_setup/` and every window simulates from it.
  `simulation.prepared_from` is an ordinary setting, useful on its own for a
  system prepared elsewhere.

- **Steered MD.** A spring attached to a collective variable, with the anchor
  moved, dragging the system whether or not it wants to go. It gives a pathway
  and the work along it, not a free energy: the work depends on how fast the
  anchor moves, and a single fast pull spends most of it pushing water aside
  rather than on the interactions of interest, overestimating the barrier.
  Jarzynski recovers a free energy from an ensemble of pulls dominated by rare
  low-work trajectories, so it needs many repeats. The rate and the work are
  reported and no free energy is claimed.

- **Metadynamics from a named collective variable**, rather than PLUMED input
  written by hand. Eight variables, each stating what it does *not* separate
  -- the failure mode of the method is biasing something that does not
  distinguish the states that matter, after which the surface converges and
  describes a different system. Well-tempered by default, and the hill width
  is refused rather than guessed.

  Coordination counts contacts through a switching function rather than a
  step, so it does not break when a ligand rotates. Membrane depth is measured
  against the bilayer's own centre, because a membrane drifts and depth
  against a fixed plane becomes depth against nothing.

- **Walls and funnels.** An unbounded ligand run is refused: biasing a
  ligand's distance pushes it into bulk solvent, where the landscape is flat
  and the bias fills a basin that is effectively infinite. Not wrong so much
  as unfinishable.

- **Restraints, released in stages.** A structure that has just been minimised
  is not at equilibrium, and heating it lets the solute move as well as the
  solvent. Position, distance, angle and torsion restraints hold it while the
  solvent settles, stepped down through equilibration and off before
  production -- a biased production run measures the bias. On a solvated
  peptide, heavy atoms move about a sixth as far restrained as free. The
  methods section reports what was held and how it was let go.

- **Membrane systems.** A protein embedded in a POPC, POPE, DLPC, DLPE, DMPC,
  DOPC or DPPC bilayer, packed by OpenMM so no external tool is needed. The
  orientation is checked rather than assumed: `addMembrane` puts the bilayer
  in the xy plane and a PDB entry is usually in a different frame, so a
  structure can be rotated onto its longest axis, and two refusals guard that
  -- whether there is a longest axis worth rotating onto, and whether the
  result has the hydrophobic belt a bilayer-spanning protein has.

  The barostat is chosen from the topology. An ordinary one scales x, y and z
  together, squeezing a bilayer that should change thickness independently of
  its area, and area per lipid is what membrane simulations are validated
  against: the run completes and is wrong.

- **Water sites.** Most water in a simulation is bulk, but some positions are
  held throughout -- a water wedged between a ligand and a backbone carbonyl,
  bridging a hydrogen bond neither could make alone -- and displacing one
  costs entropy or gains affinity. Clustering positions and reporting
  occupancy is standard; the distinction is not. A cluster occupied in every
  frame is either one molecule that stayed, which has a residence time and is
  a molecule to displace, or a position many waters passed through, which is
  geometry the protein favours. Both are reported, and only waters near the
  solute count: bulk clusters beautifully and means nothing.

- **A diagnosis when a run fails**, read from the state it failed in. Which
  atoms went non-finite tells a wrong ligand parameter from a bilayer packing
  problem from an integration failure, and those need different remedies --
  the message used to advise a smaller timestep for all of them, which cannot
  fix a wrong parameter. Nothing is retried: a rescue that produces a
  trajectory from a broken system is worse than a failure, because the failure
  is visible.

- **An explanation of each step, while it happens.** Molecular dynamics has a
  lot of steps that are obvious once you know them and opaque before that, and
  a pipeline that does all of it silently is faster to use and teaches
  nothing. Fifteen explanations, each saying *why* rather than repeating what
  the step already said, with a citation where there is one worth following.
  On by default; `--no-explain` turns them off. A reference must carry authors
  and a year or be absent, because inventing one to look thorough would be
  worse than having none.

- **Run options in the browser.** The form was built from phase settings only,
  so a top-level setting reached the command line and the config file and not
  the browser -- which was the one interface that could not turn explanations
  on or off. A guard checks that every top-level setting either reaches the
  form or is on a list of exclusions with a reason.

### Changed

- **The settings are grouped by what they decide.** Thirty-six for setup and
  thirty-seven for simulation arrived as one flat list each, in the order they
  happened to be declared: a pH sat beside a dispersion correction, and
  finding the one you wanted meant reading all of them. Each phase now opens
  into named groups that say what they are about, and `--help` reads in the
  same sections -- one grouping declared in the schema and seen twice, rather
  than two that agree until one is edited. A setting added without being
  placed fails a test.

- **A settings block can be written in the browser.** `umbrella`, `steered`
  and `metadynamics` are blocks of several settings, not one value each. They
  reached the form -- the schema declares them -- as single-line text boxes,
  and what was typed arrived as a string no phase could read, so the browser
  was the one interface where enhanced sampling could not be set up at all.
  Each now gets a box you write the block into, one setting per line, with an
  example of the right shape showing until you type.

- **The documentation was reordered around how somebody meets it**, the
  reference pages point at what generates them, and the README leads with what
  can be studied rather than with what the software is not. Two pages
  documented a command that does not exist and an API that was never written;
  both are gone.

### Removed

- **The `gentle` preset.** It dropped the temperature to 100 K along with
  shortening the run, which is not a smoke test but different physics: water
  is ice at 100 K, and a run that survives there says nothing about whether
  the system is stable at 300 K. The smoke campaign script defaulted to it, so
  every campaign was simulating at 100 K -- the case where it mattered most,
  since a campaign exists to find out whether real structures prepare and
  simulate.

### Fixed

- **The banner described a different run from the one about to happen.** It
  reconstructed the settings from `sys.argv`, so a study driven by a config
  file printed the defaults for the step counts, the timestep and the
  temperature while using the config's values -- showing a million production
  steps while running five thousand. A banner is read at a glance and
  believed, which makes that worse than showing nothing.

- **A per-system block replaced the top-level one rather than merging**, so an
  expansion giving each window a block of only its umbrella settings discarded
  everything else the study asked for: a run requesting five thousand
  production steps ran a million, and nothing said so.

- **The umbrella pieces were committed unreachable.** The expansion was called
  by nothing, and after that the recombination was called by nothing -- a
  config with an umbrella block would have been accepted, ignored, and run
  once. The loader expands it, so every route in gets it, and the batch
  explorer recombines once the windows have finished.

- **PLUMED printed its banner three times, interleaved.** Silencing ours did
  not reach it: it writes from C++ straight to the file descriptor, where
  redirecting `sys.stdout` does not go. The descriptor moves to the run's own
  log, so nothing is lost and a worker's output sits beside its results.

- **A hydration shell clustered into a water site.** On ubiquitin the first
  shell chained into one cluster -- forty-eight thousand positions and eight
  hundred and fifty-four distinct waters, reported as a site occupied in every
  frame and described as mostly one molecule. A site now has to be compact,
  and "mostly one molecule" requires that molecule to hold most of the
  observations.

- **A run too short to show residence said it had.** Water on a protein
  surface exchanges on ten to a hundred picoseconds, so a site fully occupied
  through ten of them shows that no water left, not that one is held -- and
  every site in such a run looks the same. Below a nanosecond the finding says
  what the run cannot distinguish.

- **A water analysis failed on a system with no water.** Refusing is right in
  itself and wrong as a phase failure: an implicit-solvent run, or a
  trajectory stripped of solvent to save space, has no water sites and that is
  not an error.

- **An automatic choice was recorded as the user's.** The record said the site
  selection was "given" when the setting was `auto` -- a truthiness check
  reading a value as an absence, so `options.json` claimed a decision was
  somebody's that was not.

- **Two more counts had drifted.** The README said the trajectory is analysed
  "fifteen ways" and the `include` help said "all ten"; there are sixteen
  analyses registered. The help now says which run rather than how many -- ten
  always, water sites where there is water, five more where there is a ligand
  -- and the opening paragraph does not count at all.

- **The metadynamics help named five collective variables where there are
  eight.** It is what the browser shows beside the box, what `--help` prints,
  and what the generated config template carries, so one stale sentence was
  stale in four places. There is a test that fails when a variable is added
  and the sentence is not.

- **The Windows job failed on a test, not on the code.** `ctypes.CDLL(None)`
  asks for the running program's own symbols, which Windows does not have.
  Writing the check portably exposed a real defect: the worker restored the
  file descriptors without flushing the C runtime's buffer first, so whatever
  PLUMED had not flushed arrived in the shared terminal after the redirect was
  undone -- the leak the guard exists to prevent, arriving late.

- **`pip install "fastmdxplora[ligand]"` could not succeed.** The extra named
  `openff-toolkit`, which has no PyPI distribution at all, so pip failed to
  resolve the whole command rather than installing what it could reach. The
  toolkit is out of the extra and named where it can be had: the conda recipe,
  and the error raised at the point of use. A command that cannot work is
  worse than one that installs part of the answer and says what is left.

- **The `plumed` extra had the same defect, and worse.** `openmm-plumed` has
  no PyPI distribution either, and it was absent from the conda recipe -- so
  metadynamics would have failed at the point of use on the primary channel.
  The existing guard missed it because it sat in a list that exempts a package
  from being checked at all.

- **The reference conda recipe carried the previous version too.** It is the
  copy a dependency is added to before the feedstock, so somebody reading it
  to see what this release requires was reading a file that said it was a
  different one. It gets the same check the alias has, against the same source
  of truth.

- **The `fastmdx` alias carried the previous version.** Its version is written
  in a file where the main package takes its own from the git tag, and a
  hand-written version beside a derived one drifts. The release workflow
  refused the tag, which is a check that fires too late to be comfortable, so
  the ordinary test run now checks it against the changelog -- the thing a
  release is cut from.

- **`fastmdx info` called a phase available that could not run.** It reported
  setup and simulation as "available" three lines above OpenMM and PDBFixer as
  "missing" -- "available" meant only that the module imported and had a
  callable `run`, which is true of a phase with nothing to run it on. One
  screen contradicted itself, and the block somebody reads first was the one
  that was wrong. A phase now says what it needs, or that it is ready, or what
  it will not be able to do: setup prepares a protein without the ligand
  stack, and the report is written without a PDF, so neither is reported
  unavailable for wanting them.

- **`fastmdx info` reported two backends where the software reaches for
  eight.** A PyPI install showed OpenMM and PDBFixer present and said nothing
  about the OpenFF toolkit, which a protein-ligand setup needs and which pip
  cannot install -- so somebody reading it would conclude their install was
  complete and find out otherwise three phases later. Every backend is listed
  now, grouped by what it is for, with a command that works for each. A
  backend that is installed and will not load is told apart from one that is
  absent, because they need different remedies -- and because importing
  WeasyPrint without Pango raises from the dynamic loader rather than as an
  ImportError, which crashed the command outright.


## [2.3.0] — 2026-08-05

This release is about knowing how far to trust a number.

Ten analyses were checked against the method each cites -- a paper, a
reference implementation, or the contract of the library beneath -- and eight
defects were found. Four of them change published numbers. The pattern behind
most of them was the same: software answering where the honest response is
that the question cannot be answered from what was given, and this release
teaches it to say so instead.

Protein-ligand interactions are typed rather than counted, with the chemistry
resolved before any geometry is measured and the route recorded. An occupancy
now carries the observation behind it. The report writes a methods paragraph
against the checklists journals apply, and a convergence assessment that says
what a run cannot support. The browser interface was rebuilt around a single
page that offers every setting the schema declares, and the command line now
does the same.


### Added
- **The report as a PDF**, alongside the Markdown. A Markdown file renders
  differently in every viewer and cannot be printed with the figures where the
  text put them. Needs the `pdf` extra on PyPI, where WeasyPrint's system
  libraries have to be found separately; the conda-forge package brings its
  own. Where they are absent the run says so and writes the other formats.

- **A methods section written against the published checklists.** A methods
  paragraph for a molecular dynamics study has to state a particular list of
  things, and that list is published -- in the Journal of Chemical Information
  and Modeling's reporting guidelines and in Communications Biology's
  reproducibility checklist. Every value was already recorded; what was
  missing was the assembly. Steps are given as time, a missing random seed is
  reported as missing, and anything the run did not record is named rather
  than filled in with what is usual.

- **A convergence assessment**, which says how much independent information a
  trajectory holds. Consecutive frames are nearly the same structure, so an
  error bar computed over them is too small by the square root of the
  correlation time -- measured at four and a half times on real data. Where a
  run is too short to measure its own correlation, the section says so rather
  than reporting the independence it merely failed to rule out.

- **A flag for every setting the schema declares.** The command line's option
  table was maintained by hand: twenty-four settings had no flag, and
  fifty-three of the sixty that did carried help text that had fallen behind
  the declaration. Flags, help, types and accepted values are now derived, so
  a flag cannot offer a value the software refuses.

### Changed
- **`contacts` is `pl_contacts`**, beside `pl_hbonds` and `pl_interactions`,
  because it measures protein-ligand contacts. The old name is gone rather
  than aliased: nothing has been released under it, and the alias made the
  orchestrator run the analysis twice into the same directory.

- **What an analysis works out is recorded apart from what it was told.** Both
  reach `options.json`; only the settings reach the report, which had been
  printing two pages of raw atom-index tuples. The findings appear there as a
  sentence each.

- **The banner describes the run rather than the software.** No frame, no
  heading, no list of output formats, no feature badges. What remains is the
  system, where it is going, and the settings for each phase that will run.

- **Settings that never applied are no longer offered.** Six analyses accepted
  a general atom selection and ignored it -- a protein-ligand measure works
  out both sides from the ligand's residue name. A measurement that looks
  restricted and is not is the same defect as a count that looks complete and
  is not.

### Fixed
- The solvated atom count and the pressure the barostat held were computed and
  discarded, so a methods section had to report as unrecorded two things the
  run had printed to the terminal.
- A template failure during solvation reached the user as OpenMM's raw
  message; the explanation was wired to system creation only, which solvation
  reaches first.
- HED, the oxidised dimer of mercaptoethanol, is listed as the crystallisation
  additive it is, beside BME which it comes from.
- The analysis manifest is written after `compute` as well as before it, so
  what an analysis learns about its own run is no longer thrown away.

- **What version 1 measured and this did not.** Every analysis was compared
  against its version 1 counterpart, option by option. Most differences were
  the same setting renamed -- ``atoms`` is ``selection``, ``beta_const`` is
  ``beta``, ``linkage_method`` is ``linkage`` -- but six were real.

  Omega dihedrals are measured again, with an ``angles`` option choosing which
  torsions to compute. Omega is the peptide bond itself, near 180 degrees in
  almost every residue, and the exceptions are the finding: a cis bond, most
  often before a proline.

  MDS is offered again beside PCA, t-SNE and UMAP. It answers a different
  question -- PCA finds the directions of largest variance in the coordinates,
  MDS an arrangement preserving the distances between frames, and that
  distance is RMSD.

  Hydrogen bonds take ``distance_cutoff``, ``angle_cutoff``, ``sidechain_only``
  and ``exclude_water``. The two cutoffs were not settable at all, and were
  written in two places, so a setting reaching only one would have left the
  per-frame count disagreeing with the bonds it counted.

  Clustering takes ``random_state`` and ``n_init``. The seed was fixed at 42
  inside a function, which made every run agree with every other and hid the
  question: k-means finds a local optimum, so a clustering that survives a
  change of seed is a finding and one that does not is an artefact of where
  the algorithm started.

  Not adopted: version 1 could shell out to an external ``mkdssp`` binary for
  secondary structure. MDTraj's implementation is used instead, because a
  system package is a poor dependency for an analysis that already works.

- **Protein-ligand interactions, typed.** Counting contacts says how much of
  the protein a ligand touches; this says what is holding it -- a salt bridge
  a charge change would destroy, a hydrophobic packing that tolerates one.
  Eight interaction types, each implemented against a published criterion
  named in its own docstring.

  The chemistry is resolved before the geometry is measured, and how it was
  resolved is recorded: the run's own setup phase, a supplied SDF, the
  Chemical Component Dictionary, or inference from the coordinates. Salt
  bridges and pi-cation interactions are refused where the ligand's charge was
  not determined, because they are claims about charge and a charge inferred
  from coordinates is ambiguous more often than not.

  Occupancy carries the observation behind it. A contact present in 450
  consecutive frames and one present in 450 alternating frames are both fifty
  per cent, and only the second has an error bar worth printing. Transitions
  between binding modes are counted always and given as probabilities only
  where enough were seen for a rate to mean anything.

  Where the published criteria disagree, the disagreement is recorded rather
  than quietly resolved. PLIP allows a hydrogen bond at 4.1 A and 100 degrees
  where the literature standard is 3.5 and 120, and counts fluorine as a
  halogen bond donor where the sigma-hole literature says organic C-F does
  not. Both of PLIP's choices remain reachable as settings.

**Read this before upgrading a study in progress.** Every analysis was checked
against the method it claims to implement -- a cited paper, a reference
implementation, or the contract of the library underneath. Eight of the ten
were wrong about something, and five of those change numbers a 2.2.0 run
produced: hydrogen-bond counts rise, the Q-value becomes the switching function
its cited paper defines, the radius of gyration becomes mass-weighted as the
documentation always said it was, contacts and hydrogen bonds measure across
the periodic boundary, and secondary-structure residue numbering was shifted
whenever a ligand was present.

Three more stop rather than report something meaningless: dimensionality
reduction on a structure that does not move, secondary structure where nothing
has a backbone, and protein-ligand hydrogen bonds where the ligand's
connectivity is missing. The `v1` analysis profile is gone.

### Changed
- **One page builds a run, and it does everything.** There were two -- one
  starting from a protein, one from a trajectory -- and they were the same
  thing: both wrote a config and ran it, differing only in which phases the
  config named. Built separately, one offered eleven of the eighty-three
  settings that exist and the other offered all of them. The page that
  replaces them asks what you have, what should happen to it, and what you
  want to change, and draws every control from the schema.

  It also takes a config you already have: checked for syntax and for settings
  that do not exist, then run exactly as it stands or opened into the form and
  changed. Opening never writes to it -- a change is saved as a new file,
  because the one on disk may be committed beside a paper.

- **A trajectory you already have is enough to start.** From GROMACS, from
  AMBER, from your own script. Point the browser at the folder, choose what to
  measure, and run it. The simulation does not have to be reproduced here
  first, which is what asking for it excluded.

- **Every field wanting a path can be browsed**, and only files of the kind
  being asked for are listed.

### Fixed
- **The banner describes the run it is printing.** Built from command-line
  flags, a run started with `--config` saw none of them: it announced a
  million production steps for a run that only analysed a trajectory, and the
  default pH for a config that said otherwise.
- **The timeline shows only the stages a run can reach.** An analysis of an
  existing trajectory reaches one of seven, and six greyed out forever reads
  exactly like a run that stalled.
- **The GUI opens on something to do.** `fastmdx gui` passed the working
  directory as an active run, so it opened on the overview of a run that did
  not exist.
- **Results land where you point them.** A results folder given as a path was
  flattened into a folder name, so `/Users/someone/work` became
  `Users_someone_work` inside the launch directory.
- **Clustering declares the methods it runs.** Its docstring named one where
  the code ran two, and its default was filled in from `None` afterwards, so
  nothing reading the signature could say what it would do.

### Removed
- **`--compat v1` and `--analyze-compat v1`.** The profile applied a table of
  analysis options described as version 1's. Three of them were this
  software's own defaults, one named a parameter version 1 does not have, and
  two were neither implementation's: the hydrogen-bond entry multiplied the
  per-frame count by two, and the clustering entry asked for six clusters
  where version 1 falls back to three.

  Neither version 1 nor MDTraj counts a hydrogen bond twice — version 1 takes
  `len(baker_hubbard(frame))`, one row per donor-hydrogen-acceptor triplet —
  so the multiplier modelled nothing. And the clustering entry could not have
  reproduced version 1 at any number, because the two compare frames in
  different spaces.

  Reproducing a published result means running the same method and finding the
  same number. A flag that supplies the number removes the thing being
  checked. State the settings instead: `--scope`, `--selection`, `--stride`,
  and the per-analysis options, all of which `fastmdx analyze --help` lists.

### Fixed
- **The Q-value is measured as the paper it cites defines it.** Best, Hummer &
  Eaton give Q through a switching function: each native contact is judged
  against the distance it had natively, and stops counting gradually rather
  than at a step. A single threshold was applied to every contact instead, so
  one formed at 0.20 nm was held to the same standard as one formed at 0.44 nm.
  On a hairpin coming apart, the threshold reached zero where the published
  measure still read 0.62, and crossed a half at frame 27 where the paper's
  crossed at 89. **Q values will differ from 2.2.0, and for a folding study
  that is the result rather than a detail.** `beta` and `lambda_factor` are
  exposed, at the paper's values.

  One consequence: Q at the reference frame is now slightly under 1 rather
  than exactly 1. That is inherent to a smooth measure, and the paper leaves
  it unnormalised, so rescaling it to 1 would misstate what was measured.
- **Secondary structure leaves out what has no backbone.** DSSP returns a
  column for every residue and marks the ones without a backbone `NA`. Those
  columns were kept, and since `NA` is not in the colour map they were drawn
  as coil; worse, the residue labels were taken from the protein residues
  alone, so the two lists came out different lengths and the numbering fell
  back to counting from zero. **A protein numbered 10 to 15 was relabelled 0
  to 6, with the ligand as the last row.** Scope defaults to protein and
  ligand, so this was the ordinary case for a protein-ligand study.

  Where nothing in the selection has a backbone -- a nucleic acid, a lone
  ligand, a coarse-grained model -- the analysis now says so instead of
  drawing an empty timeline.
- **Protein-ligand hydrogen bonds need the ligand's bonds.** A donor is found
  as a nitrogen or oxygen with a hydrogen bonded to it, so a ligand whose
  connectivity is missing from the topology can only be seen to accept, never
  to donate -- and the count reported one direction under a name promising
  both. The guard that supplied missing bonds fired only when the topology had
  none at all, which a PDB carrying the protein's connectivity but no CONECT
  records for its ligand does not. The analysis now says so instead of
  counting half.
- **Distances honour the periodic box.** Contacts and hydrogen bonds measured
  plain distances whatever the trajectory carried, while the Q-value measured
  with the box on the same frames -- two analyses answering "is this near
  that" differently in one run. A solvated trajectory is not always imaged,
  and a molecule split across the boundary looks far from everything it is
  actually touching: a bound ligand sitting across the wall reported **no
  contacts at all**. Both now use the unit cell when there is one, which
  changes nothing where there is not. `periodic=False` restores the previous
  measurement.
- **The radius of gyration is mass-weighted.** The docstring said it used
  masses from the topology; `mdtraj.compute_rg` weights every atom equally
  unless told otherwise, and when given masses still measures from the
  geometric centre rather than the centre of mass. So each hydrogen counted
  for as much as each carbon, a few per cent from what GROMACS's `gyrate`,
  cpptraj's `radgyr` and a published figure report. **Rg values will differ
  from 2.2.0**, in the direction the documentation already claimed. Pass
  `mass_weighted=False` for the unweighted quantity.
- **Dimensionality reduction says when there is nothing to decompose.** A
  structure that does not move has no variance, and PCA divides each
  component's variance by the total: the ratios came out NaN and the figure
  was labelled `PC 1 (nan%)` over a scatter of coincident points, which looks
  like a result and is not one. The only sign was a numpy warning about
  dividing by zero. All three methods now refuse, since there are no
  neighbourhoods to preserve among points that are all the same point.
- **An atom column is always a number.** `Atom.serial` is supplied by the file
  a topology came from; a trajectory built in memory has none, and `None`
  became NaN in the RMSF and ligand-RMSF atom column. The saved data carried
  the NaN, and the figure cast it to the most negative integer there is and
  used that as an axis label. Where the file gives no serial, the atom's
  position is used.
- **Hydrogen bonds are counted in every frame they occur in.** Baker-Hubbard
  proposes bonds above an occupancy threshold, and only proposed bonds were
  evaluated frame by frame — so a bond present in five per cent of frames
  contributed to none of them, including the frames it was in. The series is
  the number of hydrogen bonds per frame, and it was reporting the number of
  persistent ones. **Counts will be higher than 2.2.0 reported.** Raise
  `candidate_freq` to restrict the series again.
- **`freq` reports what it names.** Its only effect had been deciding which
  bonds were proposed; it now records how many are persistent at that
  threshold, alongside how many were found, in the analysis manifest.
- **An analysis option the software cannot apply stops the run.** Every
  analysis ends its signature with `**kwargs`, which the base class stores and
  nothing reads, so a misspelled setting was accepted, ignored, and the run
  reported success. Asking for `n_clusteres` clustered at the default and said
  nothing about it.
- **`--analyze-dimred-components` had never worked.** The flag became an option
  name by dropping the analysis prefix, which gives `components`, while the
  option is `n_components`. Nothing accepted it, so anyone who set it reduced
  to two components and was told the run succeeded.
- **A default is declared once.** Three phase tables restated ninety values the
  schema also declared, and four lists of accepted values existed in the
  command line, the browser, and beside the code validating them. `DEFAULT_PH`
  had already stopped agreeing: it sat unread in the setup package at 7.0
  after the pH default became 7.4. A default must now also be one of its own
  accepted values, which cannot be stated while the two live apart.

### Added
- **`--cluster-features`**, which states what clustering compares frames in.
  `rmsd` (the default, and what 2.2.0 did) superposes every pair optimally, so
  the distance cannot depend on where the molecule sits. `coordinates`
  superposes each frame onto the first and compares directly, scaled by
  1/sqrt(n_atoms) so a distance is still an RMSD in nm — the cheaper
  approximation, and the space `ward` and k-means were defined in.

  The choice changes the answer more than any parameter here does. Comparing
  coordinates without superposing, as version 1 does, makes the leading
  difference between frames where the molecule drifted and how it turned: on a
  trajectory with a hinge motion and rigid-body drift, it recovers the two
  conformations no better than chance, where pairwise RMSD recovers them
  completely.
- **`--setup-protonation-margin`**, so a setting the software tells you to
  narrow can be reached without editing a config file.
- Every analysis now describes the settings it accepts, read from its own
  constructor and docstring rather than restated anywhere. A setting nobody
  has explained fails a check, so the description cannot fall behind.

## [2.2.0] — 2026-08-03

Protein-ligand systems from a PDB identifier alone. A crystal structure
carries the ligand you care about alongside the buffer that kept the protein
soluble, and a PDB record says which is which only by name. FastMDXplora now
decides, retrieves the chemistry the structure omits, and refuses where the
structure does not determine the answer.

### Added
- **Automatic protein-ligand preparation.** `--setup-heterogens auto` inspects
  the structure, decides what each non-standard residue means, retrieves the
  chemistry for anything worth simulating, and prepares it. A bound ligand no
  longer has to be extracted and rebuilt by hand.
- **Ligand chemistry from the Protein Data Bank.** A PDB record carries no bond
  orders, formal charges, or hydrogens, and a force field needs all three.
  These are retrieved from RCSB at the crystallographic pose, completed with
  hydrogens, and cached, so a run that worked once works again offline.
- **Protonation decided in the complex.** A ligand's pKa in a binding site is a
  property of the complex, not of the molecule: a buried acid can sit several
  units from its solution value. PROPKA is asked about the bound state, in a
  structure repaired first so the electrostatics are those of the system that
  will be simulated. States decisively away from the pH are adopted and
  reported with their environment shift; poised ones are refused.
- **Several ligands, cofactors, or copies** may be parameterized together.
  Each is placed and clash-checked against everything already present, since
  two ligands can overlap each other as readily as they can the protein.
- **`--setup-heterogens`** with `auto` (the default), `drop`, and `keep`.

### Changed
- **The default force field is now chosen for you, and it has changed.**
  `forcefield` defaults to `auto`, which resolves to `amber-openff`: the
  ff14SB protein model with TIP3P water and the OpenFF Sage small-molecule
  force field. Previously the default was CHARMM36. **Runs that relied on the
  default will produce different numbers from this release onward.** Name
  `charmm36` explicitly to keep it.

  `auto` resolves to one stack whatever the structure contains, deliberately.
  Choosing per-system would give an apo run and its holo partner different
  protein force fields, and the comparison between them is usually the point.
  CHARMM36 remains protein-only here because its native small-molecule
  partner is CGenFF; pairing it with OpenFF would be an unvalidated mixture.
- **`heterogens` now defaults to `auto`.** Previously every non-standard
  residue was discarded. **A run that prepared an apo protein from a holo
  structure will now prepare the complex, or stop and say why.** Pass
  `--setup-heterogens drop` for the old behaviour.

  What decided it is not that `auto` is convenient. Under `drop`, rhodopsin
  prepares as opsin, haemoglobin as globin, and streptavidin without its
  biotin — each completing in silence and reading like an answer to the
  question that was asked. Across thirty ordinary structures `auto` now
  prepares the ligand in twelve, matches `drop` in eight, and stops in ten,
  every stop naming what the structure left undetermined. None fails without a
  reason.
- **The default pH is physiological.** `ph` moves from 7.0 to 7.4: blood is
  7.4, and a protein studied without a stated reason otherwise is studied
  there. Cytosol sits near 7.2 and a lysosome near 4.7, so a
  compartment-specific study should say which. **Titratable groups near the
  old value may settle differently.**
- **`protonation_margin` is now a setting.** How close a ligand's pKa may come
  to the pH before setup stops rather than choose. The default of one unit is
  about the uncertainty of the pKa calculation itself, so the band marks where
  the answer is unresolved rather than merely close; narrowing it is for a
  ligand whose protonation is already known.
- **Centre-of-mass motion is removed by default**, matching OpenMM's own
  default. The previous setting let the system drift as a whole.
- **Ambiguity is refused rather than resolved.** Where a structure does not
  determine what should be simulated, setup stops and says what must be
  decided: a covalently bonded adduct, an unidentified component, a cofactor
  needing parameters a small-molecule force field cannot supply, alternate
  conformations at indistinguishable occupancy, a metal coordinated in some
  copies and not others, or a sugar that may be a glycosylation site, a
  substrate, or a cryoprotectant. Producing a plausible trajectory from a
  guess is worse than producing none.
- Removing a heterogen is announced. A bound ligand and a buffer molecule are
  indistinguishable in a PDB file, and both used to be discarded silently, so
  a run could be apo while reading as holo.

### Fixed
- **A ligand is parameterized in the charge state the pocket implies.** The pKa
  settled in the complex was computed, logged, and then discarded: the file
  handed to the small-molecule force field carried whatever protonation the
  reference chemistry held. Retinoic acid was simulated as a neutral acid and
  4-hydroxytamoxifen as a neutral amine, in both cases losing the charge that
  binds them. Benzamidine revealed it only by crashing, on a stereocentre the
  amidinium it should have been does not have.
- **An amidine is no longer read as an amine.** Its cation is delocalised and
  forms on the sp2 nitrogen; built on the sp3 one it is a different molecule.
  Benzamidine is the ligand of half the trypsin structures in the PDB.
- **Each ionizable group is settled on its own pKa.** One answer for the whole
  ligand forced an amino acid onto one side rather than building the
  zwitterion it is at pH 7.4.
- **A pyridine-type nitrogen is read.** Quinazolines, pteridines and purines
  were silently untitratable.
- **A glycan is identified by what it is bonded to.** A LINK to an asparagine,
  serine or threonine says a sugar is a glycosylation site rather than leaving
  the question open; those are dropped and the protein prepared without them.
  A sugar bonded only to other sugars, as in lysozyme's substrate, still is a
  question.
- **A nucleotide chelating a magnesium is not a covalent adduct.** Both ends of
  a LINK record were marked bonded, so ATP and GTP complexes — among the most
  common structures there are — refused as though bound to the protein.
- **A group the pKa calculation reports but the molecule does not carry no
  longer stops a run.** Folate's N10 lies between a methylene and an aromatic
  ring; read as an aliphatic secondary amine, it was given that class's model
  pKa of 10.0, when an aniline is nearer 4.6.
- **A coordinated ion is prepared, not refused.** Connectivity records name
  coordination and covalency alike, and one tying an ion to its surroundings
  made it unmatchable against a force field whose ion templates allow no
  external bonds.
- **A residue removed as a heterogen is not rebuilt.** It remained in the
  deposited sequence, so the gap it left was read as unresolved polymer and
  scheduled for reconstruction from a template that no longer existed.
- **Setup artifacts are not nested inside a second setup directory.**
- **A failed phase reports failure.** Setup swallowed any failure resolving its
  input and returned success, so a mistyped PDB identifier produced an empty
  directory and exit code 0. An absent optional backend still degrades
  gracefully, because choosing not to install OpenMM is a choice rather than a
  failure.
- Single-phase commands report why they failed; only `explore` did.
- The run summary reflects the run. Options were read under their `explore`
  spelling only, so `fastmdx setup --ph 6.5` displayed the default instead,
  for all fifteen of them.
- `scipy` and `pillow` are declared rather than arriving through
  `scikit-learn` and `python-pptx`. A test now compares what the source
  imports against what is declared.
- `netcdf4` and `umap-learn` become optional extras, matching their guarded
  imports.

### Known limitations
- A coordinated metal ion kept by `--setup-heterogens auto` can fail the
  ligand clash check: a zinc sits about 2 A from its donor atom and closer to
  that residue's hydrogens, well inside the 1.5 A threshold. Lower
  `ligand_clash_threshold_nm`, or exclude the metal.
- Hydrogens added to a retrieved ligand are placed from its own geometry,
  without reference to the surrounding protein, so a tight pocket can produce
  an apparent clash.
- No named force field loads nucleic acid parameters, so DNA and RNA cannot
  be prepared.
- Non-standard residues such as modified cysteines are treated as components
  rather than being replaced with their standard equivalents.

### Packaging
- Available from conda-forge: `conda install -c conda-forge fastmdxplora`,
  including the small-molecule stack, so protein-ligand preparation works from
  a single install command.
- The conda recipe uses the v1 format.

## [2.1.0] — 2026-08-01

A graphical interface, publication-ready figures, and Python 3.13 support.
An exploration can now be designed, launched, watched, and reviewed from a
browser, and the figures it produces are drawn for print.

### Added
- **Graphical interface.** `fastmdx gui` opens a local browser interface with
  sections for building an exploration, starting it, watching it run, viewing
  the structure and trajectory, browsing analyses, and reaching the report.
- **Config files from the GUI.** The builder can save its selection as a
  FastMDXplora `.yml` file instead of starting a run, so an exploration
  designed in the browser can be submitted anywhere, including on a cluster
  where the GUI cannot run. The GUI guide documents the rsync pattern for
  watching a cluster job from a workstation.
- **Live telemetry.** A dependency-free localhost server that watches an
  output directory while an exploration is in progress: phase progress, live
  trajectory frames, an interactive 3D molecular viewer with playback, and
  per-analysis charts. It observes and starts explorations; it does not
  reimplement any phase science.
- **Report content in the GUI.** Summary cards, per-phase progress including
  phases that did not run, trajectory statistics with means and standard
  deviations, categorised analysis sections, and quick-action links. These are
  computed once and shown identically in the browser and in the report.
- **`v1` analysis compatibility profile** (`--compat v1`): reproduces the
  scope, selection, stride, analysis set, and per-analysis options of the
  published BPTI case study from FastMDXplora version 1.
- **PDB smoke campaign** (`scripts/run_pdb_smoke_campaign.py`) for exercising
  the pipeline across many structures.
- **Report additions:** region highlights and a single-figure run summary.
- **Python 3.13 support** across the whole supported range (3.9 to 3.13).
- **Documentation:** a beginner's guide, a CLI reference, a GUI guide, a
  production-run guide, region-highlight and smoke-campaign pages, and a
  substantially expanded installation guide.

### Changed
- **Publication-ready figures.** The palette is now Okabe-Ito, which stays
  distinguishable under common colour vision deficiencies and in greyscale;
  the previous palette's red and green converged in both. Axes are closed with
  inward major and minor ticks, and tick density adapts to each panel's
  physical size, so long trajectories no longer crowd their axes. Legends get
  a translucent backing and headroom so they stay readable over dense data.
- **One interface module.** All user-interface code now lives in
  `fastmdxplora.gui`: the localhost server, the browser application and its
  assets, and the static dashboard written into a report. Both surfaces share
  one set of design tokens and display the same figures.
- **Explorer nomenclature throughout.** An exploration is started rather than a
  job launched: `Start Exploration` in the interface, `/api/explore/*`
  endpoints, `fastmdxplora.gui.exploration` in the Python API, and exploration
  wording in the terminal.
- **Each analysis has its own section.** Sections used to be merged when they
  held three figures or fewer, so an analysis appeared under its own name or a
  catch-all depending on how many plots that run produced.
- The startup banner is one left-aligned block; `info` carries the version,
  authors, DOI, phase availability, and backend status.
- CI runs on Python 3.9 through 3.13 across Linux, macOS, and Windows, with
  actions updated to Node 24 compatible versions.
- `STRUCTURE.md` rewritten to match the current tree.

### Fixed
- Simulation robustness, with clearer diagnostics when an integration step
  produces a NaN.
- Batch early-stop behaviour when an exploration fails.
- Report-only invocation and phase validation.
- Windows: path comparison, server port reuse, and platform-specific commands.
- The CLI degrades cleanly when the chemistry backends are absent rather than
  failing mid-phase.
- The startup banner printed twice, and advertised a GUI address even when no
  GUI was running.
- Figures were listed twice, once for the PNG and once for the SVG of the same
  plot.
- CLI tests asserted the exit code of a machine without OpenMM, so they passed
  in CI and failed on a working install.
- Documentation examples are covered by drift tests so they cannot go stale.

### Removed
- The bundled installer and repository doctor. Installation now follows
  standard routes: pip for analysis and reporting, pip plus conda-forge for the
  full chemistry stack, or a clone with the bundled `environment.yml`.
- The `dashboard` subcommand; `fastmdx gui --output DIR` opens the same
  interface pointed at an existing run.
- The curated chart pipeline, which redrew 17 figures in a second style that
  nothing displayed once both surfaces settled on the analysis figures.
- The live pressure metric. OpenMM's `StateDataReporter` cannot supply it, so
  the card could only ever read zero; the barostat setpoint remains in the run
  summary.
- `driftmd_workbench`, a separate package that duplicated the analysis and
  report pipeline without using it.

## [2.0.0] — 2026-05-25

**FastMDXplora** — Fully Automated SysTem for Molecular Dynamics eXploration.
A single command takes a structure (and optional bound ligand) from input to
publication-quality deliverable across four phases: setup, simulation
(including enhanced sampling), analysis (protein and protein-ligand), and
reporting.

### Packaging
- Canonical package: **`fastmdxplora`** (`import fastmdxplora`). The CLI
  command is **`fastmdx`**.
- `fastmdx` remains available on PyPI as a short alias that installs and
  re-exports `fastmdxplora`.

### Features
- End-to-end MD orchestration across four phases (setup, simulation, analysis, report).
- Named force-field selector; OpenFF ligand/cofactor parameterization with a setup-time pose clash check.
- Protein-ligand analyses: ligand pose RMSD, protein-ligand contacts + binding-site fingerprint, protein-ligand H-bonds, ligand RMSF — auto-detected for complexes.
- Analysis scope (`solute`/`protein`/`ligand`/`all`) keeping analyses off solvent.
- PLUMED enhanced sampling on the production stage (`--simulate-plumed-script`).
- Cross-platform CI (Linux/macOS/Windows); parallel batch execution and cross-run comparison.

## [0.3.0] — 2026-05-25

Enhanced sampling. FastMDXplora can now drive PLUMED collective-variable
biasing (metadynamics, umbrella sampling, steered MD, …) on the production
stage of a run, with equilibration left unbiased per standard protocol.

### Added
- **PLUMED enhanced sampling** (optional): supply a PLUMED script via `simulation.plumed` (config: `{enabled: true, script: "<inline or path to .dat>"}`) or `--simulate-plumed-script PATH` (CLI) to add collective-variable biasing — metadynamics, umbrella sampling, steered MD, etc. — to the **production** stage. Equilibration (NVT/NPT) runs unbiased, matching standard enhanced-sampling protocol; the biasing force is added just before production and the context reinitialized. PLUMED output files (COLVAR, HILLS, …) are redirected into the run's output directory, and the resolved script is saved as `plumed.dat` for reproducibility. Requires the `plumed` extra (`openmm-plumed`, installed via `conda install -c conda-forge openmm-plumed`); absent, enabling PLUMED raises a clear, actionable error.

### Changed
- Renamed the `test_md_parity.py` test module to `test_md_engine_controls.py` to match its content (MD engine controls). Trimmed the README (removed the Status and Project-family sections).

## [0.2.0] — 2026-05-25

End-to-end protein-ligand molecular dynamics. FastMDXplora can now set up,
simulate, and analyze a protein-ligand complex from a feasible bound pose:
named force fields with an OpenFF small-molecule path, a setup-time pose
sanity check, and the standard protein-ligand analysis suite, all detected
and wired automatically.

### Added
- **Protein-ligand analyses** (run automatically when a ligand is detected; `include`/`exclude` apply): in addition to ligand pose RMSD, three more commonly-reported analyses now run on protein-ligand complexes:
  - `contacts` — protein-ligand contacts, reported two ways: a per-frame count of protein residues within a cutoff (default 0.4 nm) of the ligand (`contacts.dat`), and a per-residue contact-frequency "interaction fingerprint" identifying the binding-site residues (`contacts_per_residue.csv`, also shown as the figure)
  - `pl_hbonds` — hydrogen bonds formed specifically between protein and ligand (per frame), distinct from the general intra-solute `hbonds` analysis
  - `ligand_rmsf` — per-ligand-atom fluctuation after protein alignment: the ligand's internal flexibility in the pocket
- **Ligand pose RMSD** analysis (`ligand_rmsd`): the headline protein-ligand stability metric. Each frame is rigidly aligned onto the reference using the protein (Cα by default), then RMSD is measured on the ligand atoms of the aligned coordinates — i.e. how far the ligand has moved *relative to the protein frame*, which tells you whether it holds its binding pose or drifts/unbinds. This is distinct from the standard RMSD (which aligns and measures on the same atoms). It runs automatically when a ligand is detected (from `resolved_forcefield.ligand` in the setup manifest) and is skipped for protein-only runs; `include`/`exclude` still apply. Ligand-only analyses are marked with a `requires_ligand` flag on the analysis class, and the orchestrator supplies the detected ligand residue name automatically
- **Analysis scope** (`analysis.scope` / `--analyze-scope`): a single setting controls which atoms analyses operate on — `solute` (protein + ligand, the default), `protein`, `ligand`, or `all`. It resolves to a default atom selection applied to analyses that don't set their own (the solvent-blind ones: Rg, SASA, secondary structure, Q-value, hydrogen bonds), so they no longer run on solvent/ions by accident. Analyses with a meaningful own default (the Cα-based RMSD, RMSF, clustering, dimensionality reduction) keep it. An explicit per-analysis or orchestrator-wide `selection` still overrides the scope. When a ligand is present (detected from `resolved_forcefield.ligand` in the setup manifest), `solute` and `ligand` scopes include it automatically by residue name
- **Ligand / cofactor parameterization** (protein-ligand systems): supply a small-molecule ligand as an SDF or MOL2 file via `setup.ligand` (config) or `--setup-ligand` (CLI), parameterized with an OpenFF small-molecule force field through `openmmforcefields`' `SystemGenerator`. Selected with the ligand-capable `amber-openff` named force field (AMBER ff14SB protein + TIP3P water + OpenFF Sage 2.2.1 for the ligand). Net charge is inferred from the SDF formal charges unless set explicitly via `ligand_net_charge`; the ligand residue name (`ligand_name`, default `LIG`) and small-molecule force field (`ligand_forcefield`, e.g. `openff-2.2.1` or `gaff-2.2.20`) are configurable. The supplied ligand coordinates must be a feasible bound pose (from a co-crystal structure or docking); a setup-time clash check (`check_ligand_clashes`, `ligand_clash_threshold_nm`) fails with a clear message if the pose severely overlaps the protein, rather than letting it surface as a divergent simulation later. Incoherent combinations are rejected early with clear errors (a ligand with a non-ligand-capable force field, or with a raw XML list). The resolved ligand parameterization is recorded under `resolved_forcefield.ligand` in `setup_parameters.json`. Requires the `ligand` extra (`pip install 'fastmdxplora[ligand]'`); absent, the phase degrades with an actionable install message. Ligand input is list-shaped in config for future multi-ligand support; single-ligand parameterization is implemented now
- **Named force-field selector**: pick a force field by a short, documented name via `setup.forcefield` (config) or `--setup-forcefield` (CLI) — `charmm36` (default), `amber14`, `amber-fb15`, or `amber-openff` (ligand-capable) — instead of listing raw OpenMM XML filenames. Each name resolves to the correct protein/water XML set and default water model through a single registry (`setup/forcefields.py`). The raw `force_field` XML list remains as a power-user escape hatch; specifying both a named selector and a raw list is rejected with a clear error, as is an unknown force-field name (the message lists valid choices). The resolved force field (actual XMLs + water model) is recorded under `resolved_forcefield` in `setup_parameters.json` for reproducibility, regardless of which form the user chose

### Fixed
- The ligand residue in the prepared/solvated topology is now named with the configured ligand name (default `LIG`) instead of OpenFF's default `UNK`. Previously the written `topology.pdb` labelled the ligand `UNK` while the manifest recorded `LIG`, so resname-based selection silently failed — ligand-aware analyses found no ligand atoms, and the `solute`/`ligand` analysis scopes silently excluded the ligand. The name is now set on both the ligand topology and the merged topology so it survives `Modeller.add()` across OpenMM versions
- Clustering on a trajectory with fewer frames than the requested number of clusters now fails with a clear, actionable message ("Clustering needs at least n_clusters=N frames, but the trajectory has only M...") instead of an opaque scikit-learn internals error. k-means and hierarchical clustering are guarded; DBSCAN (which doesn't take a cluster count) is unaffected
- Analyses that operate on all atoms by default (Rg, SASA, secondary structure, Q-value, hydrogen bonds) now slice the trajectory to the resolved scope/selection *before* computing, rather than processing the full solvated system. Previously several of these passed the whole trajectory straight to the underlying calculation regardless of the selection — so on a solvated complex the Q-value analysis enumerated residue pairs across ~10k water residues (tens of millions of pairs) and effectively hung, and Rg/SASA were computed over water. With the new `solute` default scope and per-analysis slicing they operate on protein (+ ligand) only — a correctness fix for any solvated run and a large speedup
- **Named force-field selector**: pick a force field by a short, documented name via `setup.forcefield` (config) or `--setup-forcefield` (CLI) — `charmm36` (default), `amber14`, `amber-fb15`, or `amber-openff` (ligand-capable) — instead of listing raw OpenMM XML filenames. Each name resolves to the correct protein/water XML set and default water model through a single registry (`setup/forcefields.py`). The raw `force_field` XML list remains as a power-user escape hatch; specifying both a named selector and a raw list is rejected with a clear error, as is an unknown force-field name (the message lists valid choices). The resolved force field (actual XMLs + water model) is recorded under `resolved_forcefield` in `setup_parameters.json` for reproducibility, regardless of which form the user chose

## [0.1.0] — 2026-05-XX

Initial claim-staking release. Establishes the project-level orchestrator
scaffolding, the four-phase API (setup, simulation, analysis, report), and
the `fastmdx` CLI.

### Added
- **Robust auto platform selection**: when `platform=auto`, the simulation runner now verifies a GPU platform (CUDA/OpenCL) can actually create a Context before committing to it, and falls back to the next candidate (ultimately CPU) if not — instead of selecting a *registered-but-unusable* platform that then fails at Context construction with a confusing error. An explicit `platform=CUDA`/`OpenCL` request is still honored as-is (the user sees the real error if their choice is broken)
- **Clear periodic-box / cutoff guard**: `prepare_system` now raises an actionable error when the nonbonded cutoff exceeds half the smallest periodic box dimension (instead of OpenMM's cryptic `NonbondedForce` message), naming the cutoff, the box, and how to fix it (increase `solvent_padding_nm` or decrease `nonbonded_cutoff_nm`)
- **`environment.yml` + git install path** — clone the repo and `mamba env create -f environment.yml || conda env create -f environment.yml` then `pip install .` to get all four phases (the OpenMM/PDBFixer chemistry stack from conda-forge) without waiting on the conda-forge package. Plain `pip install fastmdxplora` still gives the analysis + report phases on their own

### Fixed
- **Parallel execution on Windows**: spawned worker processes now reconfigure their stdout/stderr to UTF-8 (as the CLI entry point does). Previously, because workers are spawned (not forked) on Windows and bypass the CLI entry, their streams stayed on the platform codec (cp1252) and crashed with `UnicodeEncodeError` the moment the presenter printed a status glyph (✓, ▸) — so every run in `mode: parallel` failed on Windows while sequential mode succeeded
- **Headless plotting**: the analysis package now forces matplotlib's non-interactive `Agg` backend before pyplot is imported (respecting an explicit `MPLBACKEND`). Previously the backend was only forced off-Windows with no `DISPLAY`, so analyses crashed on headless machines that didn't match that gate (notably headless Windows CI) with "Can't find a usable init.tcl". FastMDXplora always writes figures to files, so a non-interactive backend is always correct
- **Cross-platform paths in reports/manifests**: figure links in the Markdown report, zip archive entry names, and the relative artifact paths recorded in manifests now use forward slashes (`as_posix()`) on every OS. Previously, on Windows these were emitted with backslashes, breaking Markdown/HTML image links and producing non-portable manifests
- **UTF-8 file/stream encoding everywhere**: all text written by FastMDXplora (reports, comparison markdown, config templates, manifests, PDB/XML artifacts) now specifies `encoding="utf-8"` explicitly, and the CLI reconfigures stdout/stderr to UTF-8 at entry. Previously, on a machine whose default locale encoding was ASCII, writing the comparison report's `→` or the config template's `—` (or printing the banner) raised `UnicodeEncodeError`
- **FastMDXplora orchestrator class** (`fastmdxplora.FastMDXplora`) — project-level coordinator following a seven-phase orchestration pattern (Aina & Kwan, JCC 2026)
- **Four phases** under `fastmdxplora.setup`, `.simulation`, `.analysis`, `.report` — each with a `run(orchestrator, output_dir, **options)` entry point and a structured parameters manifest
- **`fastmdx` CLI** with subcommands `explore` (canonical), `xplore` (X-themed alias), `setup`, `simulate`, `analyze`, `report`, `info`, plus `--version` and `--cite` flags
- **Report phase artifacts**: Markdown study report, .pptx slide deck, self-contained .zip project bundle
- **YAML configuration files**: a single config captures an entire study — input is given as a canonical `systems:` list (always a list, even for one system), plus phase selection and all per-phase options; drives both the CLI (`--config` / `-c`) and the Python API (`FastMDXplora(config=...).explore()`); `fastmdx init-config` writes a fully-commented template; strict schema validation rejects typos with did-you-mean suggestions; command-line flags override file values; every run writes a re-runnable `resolved_config.yml` for reproducibility
- **Full MD engine controls**: integrator selection (`langevin_middle`, `langevin`, `brownian`, `verlet`, `variable_langevin`, `variable_verlet`); pressure in either `pressure_bar` or `pressure_atm` (auto-converted); GPU `device_index` selection; `checkpoint_interval_steps` writing a restart-ready `.chk`; `ForceField.createSystem` pass-throughs (`nonbonded_method`, `ewald_error_tolerance`, `use_switching_function`, `switch_distance_nm`, `dispersion_correction`, `remove_cm_motion`); and `fixed_pdb` to skip PDBFixer when a prepared structure is supplied
- **Many-system & parameter-sweep mode**: the `systems:` list can hold several systems, and an optional `sweep:` of parameter axes (dotted `phase.option` keys) runs the full cross-product (systems × sweep), each as a complete self-contained study. One run writes the flat output layout; multiple runs go in `runs/<id>/` indexed by a top-level `batch_manifest.json`. Per-system option overrides and swept values merge with correct precedence (base < per-system < sweep); typo'd sweep axes are rejected with the valid-option list. An optional `execution:` block runs studies in parallel (process pool) with round-robin GPU device pinning (one run per device)
- **Cross-run comparison report**: after a multi-run study, a `comparison/` report is built automatically at the batch root — per-frame **overlays** (RMSD, Rg, Q-value, total SASA across all runs on one axes), **trend** plots of each run's summary scalar against the swept parameter, a `comparison_summary.csv`, and a written `comparison_report.md` with a quantitative takeaway per property. Degrades gracefully (errored runs / missing analyses skipped); disable with `report: { comparison: false }`; (re)build via `FastMDXplora(...).compare()` (optionally `compare(output_dir=…)` for a batch that finished earlier)
- **Dry-run / plan-only mode**: `fastmdx explore --config … --dry-run` (or `explore(dry_run=True)`) prints every run, its system, swept values, target output directory, and the phases that would execute — then exits without running anything or writing to disk
- **Uniform return shape**: `FastMDXplora.explore()` always returns a `list[RunResult]` — a single study is a list of one, a sweep is a list of many. Each `RunResult` carries `run_id`, `system`, `status`, `output_dir`, `sweep_values`, and its per-phase `PhaseResult` list in `.phases` (with a `.phase(name)` lookup helper). The single user-facing entry point is always `FastMDXplora`; the batch machinery underneath is private
- **Reproducibility manifest** (`manifest.json`) written at the project root summarizing phases executed, parameters, and DOI. (It recorded this software's own version and not the versions of the libraries underneath, which is what 2.5.3 corrects.)
- **Datasets namespace** (`fastmdxplora.datasets`) with a TrpCage placeholder
- **CI**: matrix tests on ubuntu/macos/windows × Python 3.9–3.12 (GitHub Actions)
- **PyPI**: dual-name publishing — `fastmdxplora` is the primary package, `fastmdx` is a thin alias that depends on it

### Notes
- The analysis and report phases are self-contained (no heavy runtime dependencies); the setup and simulation phases require OpenMM + PDBFixer.
