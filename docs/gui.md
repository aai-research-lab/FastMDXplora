# The FastMDXplora GUI

```bash
fastmdx gui
```

A browser tab opens, and everything FastMDXplora does is in it: designing a
study, starting it, watching it happen, and reading the results.

The GUI is not a cut-down version of the command line. It offers **every
setting the software has** (all 366 analysis options across 30 analyses, and
all 127 phase and top-level settings), because the form is generated from the
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

Everyday controls are line icons, each named when you point at it (and to a
screen reader): open, browse, tag, close, copy, download, edit, remove, reset,
clear, compare, back and show. Under each of the Agent's replies, a thumb up
(**Useful**) and a thumb down (**Wrong**) mark the reply, filled while pressed;
the mark is kept with the conversation (in the study's `agent/conversations/`,
or the workspace's `.fastmdxplora_agent_conversations/` for a chat of no
study), for evaluating the Agent, and is sent nowhere. Beside them, a reply that
says what it took has a bars icon: pressed, it shows the tokens the reply
took (in, read from the cache and written to it, and out), hidden again by a
second press. Buttons that run or
write something (**Run on
this machine**, **Stop the run**, **Analyze again**, **Write it again**), a
dialog's answers, tabs, menus, the Viewer's show and hide switches and the
Agent's starting questions keep their words.

| | |
|---|---|
| **All studies** | Every study under the workspace (or a folder you choose), as cards: the structure, the kind of study, where it stands, when it began, the means it recorded with their errors, and a figure it plotted (before it has plotted one, its protein's backbone, rendered from its structure and coloured from the N terminus to the C terminus), two cards a row. Search narrows them; **Open** makes one the study on screen; once a second study is chosen, **Compare** and **Clear** come beside the pointer that chose it (beside its box, chosen from the keyboard), wherever on the page it was, out of the way of the comparison, of tags being changed and of the Agent, and Tab from the study chosen reaches them, then goes on from it; two chosen are **compared**: the settings in which they differ, defaults filled in as `fastmdx diff` says them, and the means both recorded, a difference marked resolved only past twice their combined standard error. **Tags** are words of your own on a card ("wild type", "JCIM Fig. 4"), up to 20 a study and 40 characters each, with a one-line note: **Tag** (marked with a dot, and named **Tags**, once it has some) edits them on the card, offering the tags the studies here carry as you type; a tag clicked shows only the studies with it, and search reads tags and notes too. They are kept in the study's folder (`study_tags.json`), beside its records and never in them, so they go wherever the study goes (but not into a shared study). **Open a shared study** (the box with an arrow, beside **Look in another folder**, the folder with a magnifier) takes a shared study's DOI, its Zenodo address or the path of its zip on this computer, checks every file against its SHA-256 and opens it in a folder of its own where new studies go ([Sharing a study](sharing.md)) |
| **Agent** | [The FastMDXplora Agent](agent.md): a conversation that writes, edits, runs and reads a study |
| **New study** | The [Config](config.md) builder, in five steps: **Start** (a structure, a trajectory or a config you have; the systems as rows, each with what its file holds; the results folder and the phases that run; **Start from an example**: a protein in water, a protein and its ligand, a membrane protein, a study run until a quantity is determined, and a free energy along a distance, each a complete Config of the [worked examples](examples.md) loaded into the form to change, or the Agent), **System**, **Protocol** (a fixed length or until it is determined, replicas, enhanced sampling as cards), **Analysis and report** (the analyses under the Analysis page's themes, each that this study leaves nothing for saying why) and **Review** (the plan with what is checked after, what is worth knowing, the reasons recorded, where it runs). Each group shows its essential settings and **Show more** the rest; each setting has its unit, its default, what it does when left unset, a dot and a revert once changed, and **Why this value?** to record its reason as `decisions`. **Find a setting** finds one by its name. Beside the steps, the study: whether its checks pass, what setup will build, what changed, the Config, the command and the script, and **Run on this machine**; where the page is narrow (the Agent open beside it, which New study and **Or describe the study to the Agent** do), the study goes under the steps, with whether it will run and **Run on this machine** kept at the page's foot. The form is saved with the page, so a reload or a new browser tab loses nothing |
| **Overview** | Study Overview. A live run: health first, then what the sidebar has no room for: the live charts, the structure as it is written, and once there are results, the recorded numbers |
| **Viewer** | The molecule in 3D, live while running and played back afterwards; follows the run by default. The molecule is shown 4 wide to 3 high, as large as the window allows with the heads of the sequence above it and the playback under it in view, centred with the settings beside it; the sequence and the playback fold as the settings' sections do, both closed until first opened (the keys play the frames either way), and stay as they were left; opening either leaves the molecule's size as it was, pushing what is under it down, and the column under the header scrolls to reach it. The settings are sections of a panel on the right (View, Display, Selections, Ligand, Saved views, Information), scrolled on their own. **Sequence**, above the molecule, gives each chain as a line of one-letter codes, the structure's own residue numbers written over every fifth residue (5, 10, 15 ...), a gap where numbers are missing and a bar under each residue in a helix or a strand in the frame shown; a click selects a residue, a drag a run, Shift extends and Ctrl or Cmd adds or removes one, and the selection is rendered in the structure as green sticks; an atom clicked in the structure selects its residue there (with Shift or Ctrl, adds it); a double click centres the camera on the selection; from the keyboard each line is a list, the arrows moving along it (with Shift, extending the selection), Up and Down changing chain, Enter selecting and Escape clearing. **Selections**, in the panel, are PyMOL's kind: one typed in MDTraj's language (`resSeq 10 to 25 and backbone`, as a Config writes it) is read by MDTraj in the very structure the Viewer renders, so its atoms are the ones shown; what is selected, typed or in the sequence or the structure (a ligand clicked too), is named with **Name it** and listed, each with its colour, painted over every representation of its atoms, an eye that hides its atoms wherever they are rendered, a representation of its own (sticks, spheres, lines, a surface or a cartoon), labels for its residues, and buttons to select it, centre on it and forget it; they are kept with the study (`viewer_selections.json`), and found again in whatever the Viewer renders, the structure with its solvent or without, or the frames: residues by their chains, numbers and names, a typed selection by MDTraj reading it again. It is rendered by Mol\* (5.12.0, bundled, so nothing is fetched from outside the GUI) from the trajectory sent as binary frames, each made whole as the analyses read it, as XTC (each coordinate to a hundredth of an angstrom, about a third of the size of a DCD) in pieces of up to two million atoms times frames, played from the first while the rest arrive, how many are here said beside the transport; while a study runs, each new frame's coordinates alone move the atoms already shown, so what is being measured stays measured; under the transport, one of the study's series over time (RMSD, radius of gyration, or another, chosen from a list) is plotted with the frames its analysis left out as equilibration and the mean of the rest, a line marking the frame shown as it plays and its value beside it, and a click or a drag along the series shows that frame; **Superposed** fits each frame, by MDTraj's least squares, on the protein's backbone or on the backbone of the residues within the pocket cutoff of the ligand in the first frame, **to** the first frame, to the structure the run started from (as setup prepared it) or to the deposited structure the study was given (`setup/input.pdb`, its backbone atoms matched to the frames' by chain, residue number and name, the first of alternate locations and the first model, and how many matched said), and **Smoothed** averages each atom's fitted position over 3, 5, 9 or 15 frames centred on the one shown (over fewer at the ends; frames shown as written are fitted on the backbone first, since an average of a molecule turning is a molecule shrunk, and an average shortens bonds a little, so measure on frames as written), at the frame shown, the measurements kept (any water shown is the first frame's, so it is not shown where the frames are fitted to another structure, and the periodic box is not shown, since each frame is turned out of its own); **Periodic box** shows the box of the frame shown, following an NPT run's changes, as the brick setup and the frames keep the water and ions in (a by b by c along x, y and z, which for a rhombic dodecahedron or a truncated octahedron is a brick of the same volume with the same periodic images), about the water shown or, without it, about the protein; **What holds the ligand** lists the contacts the interactions analysis found (`pl_interactions`), the twelve most often present, each a row marked where it was present over the frames played with its share of them, and shows those of the frame shown in the structure as dashed lines between their atoms, coloured by kind, kept apart from the ruler's (**In the structure** turns them off); **Between chains**, where the protein has more than one, lists the hydrogen bonds and salt bridges between its chains in the frames played, by the criteria the interactions analysis applies to a ligand (donor and acceptor within 3.5 Å with the angle at the hydrogen above 120°; charged groups' centres within 4.5 Å), the most often present first with the share of frames each was present in, and shows those of the frame shown in the structure as dashed lines, blue and orange, kept apart from the ligand's; **Views** saves the view with the study under a name (`viewer_views.json`): the camera, the frame, the representation and colouring, the parts shown, the superposition, the pocket cutoff and the publication look, each checked, and choosing it again shows it as it was; the box icon beside them writes the view as a **scene file** (MolViewSpec, `scenes/<name>.mvsx`, downloaded too): the atoms shown at the frame shown (superposed where the view is), the study's DSSP as the cartoon, the colouring (a result on the Viewer's scale), the parts shown, the selections named, the camera, the ground and the look, which opens as it is shown here in any viewer built on Mol\* (molstar.org among them), as `fastmdx scene` writes it from the command line and an AI app with `write_scene`; **Scenes** lists the scenes written with the study, shows the one chosen in this Viewer again (the eye: its frame, camera, representation, colouring, parts shown and superposition, the camera shown kept where the scene was written without one), and opens it on a page of its own where the Viewer's engine is given the scene and nothing else, as any viewer built on Mol\* shows it; **Movie** makes a movie of the frames as they are shown (from one frame to another, every so many, at 10 to 60 frames a second, as wide as the view is shown at 1920 pixels across or at 720p, 1080p or 4K, with the time of each frame in its corner, the colour bar of a result, and one turn about the screen's vertical if asked): each frame is rendered as a picture and encoded by ffmpeg on the computer the GUI runs on, as H.264 in an MP4 or, where that ffmpeg has no H.264 encoder, VP9 or VP8 in a WebM, its colours as BT.709; **In between** puts 1, 3 or 7 frames in between each two frames played, each atom moved in a straight line from its place in one to its place in the next, for a smoother movie: a display, not more simulation, which shortens a bond whose atoms swing far between two frames, so the frames are superposed on the backbone first where they are shown as written, and the movie's record says it was interpolated; the movie is kept with the study (`movies/<name>.mp4`) and downloaded, and the frame and the camera are put back; a study without frames (set up and not yet run) gives the structure shown turned once over six seconds; the page icon gives the **publication look**, a white ground with outlines and shading at the highest quality, for a figure; a terminal cap such as ACE or NME is rendered with its chain. **Over the frames** shows where the ligand and the water went over the frames played, as VMD's VolMap does: space cut into cubes half an angstrom wide and, for each, the fraction of frames in which a heavy atom of the ligand (within 1.5 Å) or a water's oxygen near it (within 1.4 Å, up to 500 frames, read from the trajectory, which needs `simulation.save_selection: all`) is there, each frame fitted on the backbone of the ligand's pocket to the first frame played; each map is rendered as a surface where that fraction reaches the level chosen (30% and 60% to begin with; bulk water reads about 32%), written beside the frames as OpenDX (`simulation/occupancy_<ligand or water>_<ligand>_<cutoff>.dx`); **Water sites** places the sites the `water_sites` analysis found as spheres, vermilion where one molecule held the site and sky blue where many passed through, each listed with its occupancy; the frames are superposed on the pocket to the first frame as these are shown, and they are hidden while the frames are fitted otherwise. **Main motions** shows the study's principal components, the collective motions it spends most of its fluctuation on: the `dimred` analysis keeps them (`analysis/dimred/dimred_pca_modes.npz`: each motion over its atoms, its variance and share, the mean structure), and a study analysed before has them found from the alpha carbons of the frames played, by the same steps, and is said to; **Play the motion** swings the motion's atoms as a backbone from two standard deviations of its projection one way to two the other, **Lines along it** shows a line from each atom to where two standard deviations take it, each placed on the first frame played, its share and how far it moves an atom said, and **Size** makes it three times larger to be seen, which is said too. **States** lists the states the `cluster` analysis found (k-means, hierarchical or DBSCAN, chosen where it ran more than one), each with its share of the frames analysed and a representative, its medoid among the frames played (the frame with the least summed RMSD to the state's others, on the atoms the analysis compared; each frame played takes the state of the analysed frame nearest it), which **Show** shows; **Compare** shows the second state's representative in grey beside the first's frame, fitted on the alpha carbons as the frames are shown, and colours the protein by how far each residue's alpha carbon moved between them, with the RMSD said; the grey structure is beside that frame only, and **Stop comparing** puts the colouring back. **Backbone angles** is a Ramachandran plot tied to the frame shown: behind, in grey, where every residue's φ and ψ fell over every frame played; over it, each residue in the frame shown as a dot (hollow for glycine), moving as the frames play; and the residue chosen (from the list, or by selecting it in the structure or the sequence) as its path over the frames, faint at the first frames and solid at the last, with a ring at the frame shown and its φ and ψ said, each mark shown beside what it is in a key under the plot; a click on the path shows that frame, and a click on a dot follows that residue and selects it. The angles are computed by MDTraj from the frames played as written (a dihedral is the same in a fitted frame) when the section is first opened, and kept beside the frames. **Contact map** gives each pair of the protein's residues the share of frames played they were in contact in, darker the more often: two residues are in contact when any heavy atoms of the two are within 4.5 Å, MDTraj's closest-heavy contact at its 0.45 nm cutoff, residues of one chain fewer than three apart left out as MDTraj leaves them out (the frames are made whole and centred on the protein, so no contact is looked for across the box); a line marks where a chain ends; pointing at a cell names the pair and how often; a click follows it, selecting both residues in the structure and joining their closest heavy atoms with a dashed line in the frame shown, as the frames play, with how far apart they are; where the cluster analysis found states, **State** B **vs** A and **Compare** colour each pair by how much more often it was in contact in one state than in the other (the share of B's frames played minus the share of A's), red where more often in B and blue where more often in A; a key under the map says which colour is which, and for the map of every frame, that darker is more often. **Pocket volume**, for a study with a ligand, plots how much room the protein leaves in the ligand's pocket in each frame played, counted as POVME counts it (Durrant et al., 2014): each frame fitted on the pocket's backbone to the first, the points of a grid half an angstrom apart within 4 Å of the ligand's heavy atoms in the first frame, each empty in a frame where it is beyond every protein atom's van der Waals radius (Bondi's), inside the hull of the pocket residues' heavy atoms (the open solvent at the pocket's mouth left out) and joined to the ligand's place; the ligand, water and ions are not counted as filling it. The plot gives the mean, a line at the frame shown and its volume, and a click shows that frame; the spread said is over the frames, not the error of the mean. **Show the pocket** renders the empty points of the frame shown as a green surface, following the frames, the frames superposed on the pocket to their first frame. **Compare study** plays another study's frames beside this one's, a wild type and its mutant or two related proteins, chosen from the studies of the workspace: their residues are paired by their one-letter sequences (difflib's matching, which pairs every residue of two forms of one protein but those inserted or deleted, and pairs a substituted one as a mutation, each listed), the other's frames fitted on the paired alpha carbons to this study's first frame, and played by simulation time, each frame beside the other's nearest it (by place in the run where a run's clock was not recorded), in orange, hidden while the frames played are not fitted on the backbone to their first; **Coloured by the difference in RMSF**, where both studies have an RMSF, colours this study's protein by the other's RMSF less its own over the paired residues, red where the other moves more and blue where less; the fitted frames are kept with this study (`viewer_beside/`). **Runs**, for a study of several runs, plays them together: the first run with a trajectory, in the order the study planned them, is played as any study's frames are (what is clicked, measured, selected, the pocket and a colouring by a result are its), and every other run of the same atoms is rendered beside it, protein and ligand, in the colour the Analysis page gives it, **Coloured by** set to **Run** so the run played is in its own; each other run is read at the source frames the first run's frames were taken from, so frame k of every run is the same step of its simulation, made whole the same way, and fitted by least squares on the protein's backbone to the first run's first frame, so every run is superposed on one shared structure; the frames played are superposed on the backbone to their first frame as they open, and the other runs are hidden while they are superposed otherwise or shown as written; a run with fewer frames is not shown past its last, each other run can be hidden with its box, and a run of other atoms (another system, or one prepared otherwise) or with nothing written yet is listed as not shown, with why; a run still running is played from the snapshots it has written, each frame beside the snapshot it had written last by that frame's time, fitted the same way and said to be running, and is rendered again as it writes more without the frames played being loaded again; while no run has finished, the first to have written snapshots is played; the fitted frames are kept at the study's root (`viewer_runs/`), written again when a run's trajectory changes; a scene written from such a study is of the run played alone, and says so. **Measure** (the ruler, or M): two atoms clicked give their distance, three the angle, four the dihedral, shown in the structure and listed in the Selection tab, following the frames; two atoms can be measured over every frame with the command it gives. A clicked histidine, aspartate, glutamate or lysine offers its protonation states: choosing one opens the Config page with this study's Config and that residue set in `setup.residue_states`, found as setup builds the structure. The camera button saves the view as a picture as wide as **Picture** says, whatever the window's size: 2,400 pixels across (a double-column figure at 300 dpi) unless 1,200 (one column), 3,600 or 4,800 is chosen, in the view's own shape, and on no ground at all where **Transparent** is ticked. **Coloured by** offers the study's per-residue results where its analyses wrote any: RMSF, mean SASA, contact with the ligand, the N-H order parameter S², and the RMSF implied by the deposited B-factors, read from the analyses' own files; for a study of replicas (runs that differ only by their seed), the mean over the runs of the same atoms as the run played ("RMSF, mean of 3 runs"), with each residue's standard error across them and each run's own value in the Selection tab, and for runs that differ otherwise, the run played's own, said to be. Blue is the low end of a result's range, red the high end (for S², the mobile end); RMSF and the RMSF from B-factors share one scale from zero. A bar over the canvas gives the range, and the saved picture carries it; a residue with no value, or one the structure shown cannot tell from another (an insertion code lost on the way), is grey, and the line under the controls says how many. The Selection tab gives a residue's value in each result and its secondary structure in the frame shown. The cartoon is DSSP, as the `ss` analysis computes it, for the structure, the live frame and every frame played, read from the trajectory's own coordinates for the frames; the Structure tab says so, or that the cartoon is Mol\*'s own DSSP and why (Mol\*'s is computed chain by chain, so a strand paired with one in another chain, as in a fibril, is rendered as coil) |
| **Analysis** | **What the analyses determined**, one table first: for each analysis, under what it studies, the mean it recorded over its equilibrated frames to the place its error allows (and any other quantity it recorded a mean of, such as the helix fraction or the asphericity), the frames it was averaged over and from when, the independent samples behind it, and **Determined** or **Not determined**, which opens to say why (a run too short against its correlation time, a molecule broken across the box, a biased run with no reweighted value); a failed analysis gives its reason; **Download CSV** gives the table with each standard error and unit. A name goes to its section. The sections follow, each analysis its own, grouped by what it studies, with an index of them and **Find an analysis**, which keeps the sections whose title or figures hold every word typed. Each figure's card links the data it is plotted from, and a series over time has **Convergence**: the running mean of its equilibrated frames with its standard error beside the mean the analysis recorded, the standard error from blocks of 1, 2, 4 and more frames (Flyvbjerg and Petersen's blocking, where it levels off once blocks are longer than the correlation), the autocorrelation with the integrated correlation time, and the distribution of the frames averaged beside the frames left out, all from the estimator the recorded error comes from (`statistics.convergence_of`), and said in a paragraph; where the recorded mean started where the study's replicas equilibrate together, it says so. For a study of several runs, the runs side by side: the settings that differ, the mean each run recorded with its error, a difference marked only where it exceeds twice the two runs' combined error (replicas are set against their own errors instead), and one measure from every finished run overlaid. **Analyze again** runs the study's analyses again in its folder, from its records and the settings it recorded, with the analyses chosen (those it ran last ticked, any other this release has to add); the report is written again too where the study has one, and what is replaced is kept in `previous/`. A study of several runs is analysed run by run and its comparison built again. Nothing is simulated; it asks first, and runs `fastmdx analyze --output <study> --rerun` (with `--include-phase analysis report` on `explore` where the report is written again too) |
| **Report** | The report itself, rendered as a document, with downloads for what was produced and a notice for what could not be. **Write it again** runs the report phase alone on the study open (each run's, for a study of several), from its records and the report settings it recorded, after asking, the report before kept in `previous/`; nothing is simulated or analysed |
| **Files** | Everything the study wrote, in the order it ran. **What you came for** first: the trajectory with the topology it is read with (`trajectory_topology.pdb` where the run saved its atoms without the water), its frames, time and atoms said, a zip of the two and the `md.load` line that reads them; the report, the bundle, the system as simulated, the final state and the configuration. Then the space each phase takes, and the phases as sections, a row a file: what it is, where it is, its size and when it was written, **Preview** in the side panel, **Download**, and a menu that opens it in a tab, copies its full path, shows it in Finder (Explorer on Windows, its folder elsewhere; on the person's own computer only) or copies its SHA-256. Each analysis is a row under what it studies, with its figure, the mean it recorded where its error was determined, its files (a zip of them) and **Open in Analysis**. The run record, what `--rerun` set aside (`previous/`) and the scratch (the frames the Viewer was sent and what it computed from them, written again from the trajectory when needed, and the live view's snapshots of the run as it went) are folded; **Clear scratch…**, on the person's own computer, says how many files and bytes would go, and how many are snapshots, which nothing writes again, and removes them once asked: never while the study runs, and each run's snapshots (the study's, each run's of several, each segment's) judged by its own record: kept while it runs, where it has no trajectory, and where it failed or ended without saying so, since its trajectory may be cut short and the Viewer plays the snapshots instead. A link is removed as a link. The trajectory, the analyses and the report are not touched. **Find a file** keeps the rows whose name or path holds every word typed, wherever they are folded; the filters keep one kind (structures, trajectories, data, figures, documents, logs, configs and records); the sort is by the study's order, name, size or time; **Folder view** lists the study's folders instead; a study of several runs has a run picker. **Prepare a data deposit** writes one zip for Zenodo, a journal's data repository or a colleague into the study's `deposit/` folder: the sets ticked (the inputs, the configuration, the trajectory with the topology it is read with, the final state, the analyses' numbers, the figures as SVG or as PNG (a figure with no twin of the kind chosen kept as it is), the report with the pictures it shows, and the records; the checkpoint, binary and read only by the OpenMM build that wrote it, a joined study's segments, which its joined trajectory is made of, and what was kept from the Viewer only when ticked), each set's size said, a `README.md` written from the study's records (what was done, as the report's methods say it, the software and its versions, how to read the trajectory, every file and what it is, how to check them and how to cite), shown before it is written, and `SHA256SUMS`, checked with `sha256sum -c SHA256SUMS`; never the scratch, what `--rerun` set aside, the project bundle or an earlier deposit; one at a time, and not while the study runs. Files are sent a megabyte at a time, a range on request, so a download that stops resumes |

Three things are on every page. The **sidebar**: the two ways to begin, a
new study and the Agent, the workspace's newest studies, the study on
screen and its pages, where a run stands while it runs, and at its foot the
person and the settings. The **side panel**:
a *Log* tab, which is what the command line prints, sorted so a refusal is a
red-edged block and the explain text a quiet one, with filters and a
scroll-to-newest toggle; and a *Files* tab, which opens any of the run's
files in place. And two **seams** between the columns that drag, with a
double-click to reset; the sidebar folds to a strip of its icons under
FastMDXplora's mark, the side panel to a tab at its edge, and the centre stays
centred at a reading width whatever is folded. Pointing at the strip's
mark, or the panel's tab, shows the column over the page for as long as
the pointer stays on it or the column, with one button that keeps it; a
click on that button, or anywhere in the column with nothing of its own
under the pointer, keeps the column open. The strip's icons are used as they
are, without opening the sidebar; Recent's and Chats' show their lists
beside the strip, Chats' with **New chat** first. Opening the Viewer folds both, and
another page brings them back as they were; a column kept open on the
Viewer stays open there. Every page's header is one band across the
centre column, and stays put while the page scrolls. The three columns'
top bars are one height, their rules on one line: the product's name at
the top of the sidebar, each page's title, centred, and the side panel's
tabs.

**The sidebar** reads top-down:

```
FastMDXplora                           [‹]
＋ New study
✦ Agent
RECENT ⌄
  1UAO                            running
  1UBQ                          completed
▦ All studies                         14
ACTIVE STUDY
┌───────────────────────────────────────┐
│ 1UAO                               ⌄  │
│ ● Running · CUDA                      │
└───────────────────────────────────────┘
  fastmdxplora_out…0919_021313
RUNNING  ● 1UBQ           50.0%  [View]
◔ Overview   ⚛ Viewer   ⌁ Analysis   ▤ Report   ▭ Files

┌ Production                     62.0% ┐
│ ▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬░░░░░░░░░░░░    │
│ Stage 5 of 7               1 h 12 min │
│ Step       1240000 / 2000000          │
│ [‖] [↻]                updated 14:02  │
└───────────────────────────────────────┘
(AA) Adekunle Aina                    ⚙
```

**New study** opens the Config builder and **Agent** the Agent; both stay
at the top as the rest scrolls. The button beside Agent, or ⌘J (Ctrl+J),
opens the Agent beside whatever page is open, in the side panel's place,
and closes it again; it is the Agent page's own conversation, so one begun
on either goes on in the other, and **Open as a page** takes it there. A
suggestion under the Agent is sent as it is pressed. **Recent** lists the workspace's newest
studies, the open one marked, each opened with a click; folded, it stays
folded. The study card says the study's name (its system), large, and one
line: where it stands (running, completed, stopped, failed), the platform,
and, when the page has stopped hearing from the server, that too. Under it
is the study's folder by name, shortened in the middle to fit; a click
copies its whole path. Pressed, the card opens **All studies**, **Open
another folder** (a picker on any folder), and the study's own folder with
**Open the folder** (where it cannot be opened from here, its path is
copied), **System ID for display** (the one to four letters or digits the
GUI shows the study under here; its folder and records keep theirs. A
study is otherwise shown by its system's ID: its PDB ID, or the first four
letters or digits of its structure file's name, in capitals) and **Ligand residue** (the
ligand the Viewer centres on and finds the pocket around, for a structure
with more than one; the analyses are not changed). Both are kept on this
computer by the study's folder, as Preferences are. The *Running* line appears only when a run is going in a study
other than the one on screen (from this GUI at most one study runs at a
time), with its fraction complete read from its own telemetry and a *View*
button back to it; a study of several runs lists them there, and a run of
one names its study and the way back. All studies says how many studies
the workspace holds.
The progress card is there while a run goes on:
its stage and how far it is, a bar of the stages it can reach (each named
when pointed at), which of them it is in, the time left at its speed so
far, its step, and buttons to pause the page's updates (the run goes on)
and to refresh now. A run that stopped, failed or was interrupted keeps
the card, saying where, with **What would fix it**, which opens the
Overview at that card (for an interrupted run, `fastmdx resume`, run from
there only where its process was seen to be gone).
A finished study has no card: its own card says it completed. A finished
study can be opened and read while another runs; Stop still stops the
running one from any page.

**A study's folder** is named for what it holds:
`fastmdxplora_<system>_study_<UTC timestamp>` — the first system in the
config, then `study`, then a timestamp, so two runs never collide and a
folder says what it is. One rule, in `fastmdxplora/naming.py`, that the
CLI, the API and the GUI all ask.

**The run outlives the server.** A study started from the GUI is its own
process. Close the browser tab and it runs on; Ctrl-C the server and it runs
on, and the server says where it is and how to stop it. A server opened
later on that folder, or brought to it from the study card, adopts
the run: it shows as running, and Stop reaches it. The run records its own
process in the folder; a stale record left by a crash is checked against
the live process before anything is believed, and a process that is not
FastMDXplora running that study is never adopted.

**The settings**, from the gear at the sidebar's foot: the scheme
(System, which follows the computer, Light or Dark), the Agent's AI model and mode with **Agent
settings**, **Preferences**, **Cite FastMDXplora**, the documentation and
the repository. Each of the three opens as a dialog over the page shown;
Escape, Close or a click outside closes it, and `#settings` or `#cite` in
the address opens it. **Preferences** are how the Viewer opens
(representation, a dark or white ground, water, ions, spin, the camera kept
as frames change, the pocket cutoff), the time in 24 or 12 hours, and how
many samples a live chart keeps; they apply as they change and are kept on
this computer, in the browser tab's storage for the GUI's address
(**Restore defaults** puts the release's back). Nothing in them
changes a study or its files. **Cite FastMDXplora** gives the reference and
its BibTeX, each copied with one click.

Green done, amber qualified and red refused mean the same in all three
schemes.

Every page, in every scheme, is held to WCAG 2.1 AA's contrast: 4.5 to 1 for
text, 3 to 1 for large text, measured against what is behind it by a test
that opens every page. The information beside the viewer is a set of tabs a screen reader is
told of, moved between with the arrow keys, Home and End; the viewer's
icons are named, with their keys, and the preview and the viewer are named
pictures.

---

## Designing a study

One page builds any run, and it asks three questions.

**What have you got?** A structure, a trajectory, or a Config you already have.
Choosing a trajectory greys out setup and simulation, because there is nothing
to prepare or run. Every path field has a **Browse** button, so there is no
typing a path and finding out later that it was wrong.

**What should happen?** Which phases, and which analyses. The analyses are
grouped — shape and size, flexibility, conformations, folding, the ligand,
protein and ligand together — and each explains what it computes, taken from
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

**How long it runs** can be a question rather than a number. Under
`stop when`, choose a quantity (only the analyses that record one mean are
offered), the error it must reach in its own unit or as a percentage of its
mean, and the most production any run may reach; the study then runs until
each is known as asked. Replicas must agree unless you untick it, and where the
study has none, one button sweeps the seed over three values. See
[Running until it is determined](production.md#running-until-it-is-determined).

`plumed` is the exception, because it is one script with an on-switch rather
than a mapping of settings, and writing a working `.dat` file inside YAML
block-scalar indentation is where a stray tab changes a PLUMED input. It gets a
control of its own: the switch, a path with the same **Browse** button, and a
place to write the PLUMED input directly. Text written there wins over a path —
the rule is stated on the control the moment both are filled — and the first
content to arrive turns the switch on.

**What setup will build** is said under the structure while the settings are
still open, and said again as they change: the particles, the box (its shape,
how wide it is from face to face, and the padding setup will grow it to where
the cutoff needs more), the solute's residues, atoms and charge, the ligands
kept, the water and the ions; and it is rendered beside them, the chains setup
keeps (copies from the assembly's symmetry included) inside the periodic cell
it builds, to scale: a cube, a rhombic dodecahedron or a truncated
octahedron. It is worked out from the structure and the
settings by OpenMM's own rules, the chains and copies of the biological
assembly setup will build included, and on the structures it was checked
against it comes within a few per cent of what setup builds; setup's own
numbers replace it once it has run. Where this machine has been timed (the
first study given a budget times it), it says how long the study will take
here, every run of it. What is worth knowing about the structure under these
settings, a metal in a site the force field will not hold, say, is said
beside it, with a button that opens the setting it is about. A membrane
system is not estimated: its box is the bilayer's.

**A residue's protonation state** is chosen under `residue states` in Setup,
from the structure itself: once the structure is read, every histidine,
aspartate, glutamate and lysine it holds is offered by kind, each with the
states it takes and what each is (HID, neutral with its hydrogen on ND1;
HIP, charged). Where a structural metal is within 3 Å of a residue's side
chain, which atom and how far is said beside it, since a histidine holds a
metal by a nitrogen with no hydrogen on it; which state follows is yours to
decide, and nothing is chosen for you. Each histidine is marked in the
picture of the system, and a click on one adds it to the rows and asks its
state; a residue given a state is marked there with it. A row reaches the
config, as `setup.residue_states`, once its state is chosen.

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

Each of the four puts the Config through the command line's validator first.
A setting it refuses is said **on that setting's own field**: its section is
opened, the field is outlined and focused, and under it are the refusal and
what would fix it, within what the refusal may say (the values a setting
takes where the schema holds them all, the setting alone where the value is
a judgement). Where the schema holds a spelling near what was given, a
button offers it, and nothing is changed until it is pressed. Changing the
field takes the refusal away. See
[What would fix it](refusals.md#what-would-fix-it-and-what-it-costs).

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

**The molecule, in 3D.** The structure is rendered as it is simulated, and once
frames exist the trajectory plays back. For a protein–ligand system the ligand
and its binding pocket are picked out, so what the ligand is doing is visible
without loading anything into another program.

**Whether the answer is determined yet.** A study run until it is determined
(`simulation.stop_when`, see
[Running until it is determined](production.md#running-until-it-is-determined)) has a
card of its own on the Overview. For each quantity it plots the standard error
after each round against the error asked for, where the error would reach it
if it keeps falling as one over the root of the frames (the estimate the next
piece is sized by), and the mean after each round with every replica's own
mean beside it, so replicas that disagree are seen as well as said. It says
what the piece now running adds and how long that takes at the speed the study
has run, and lists every round and what was decided on it. The card reads the
study's record and nothing else, so it says what the report says.

**The methods, to paste into a manuscript.** Once a phase has run, the
Overview carries the study's methods paragraphs as the report gives them:
preparation, protocol, how each mean and its error were determined, any rule
the study ran until, an AI model's part, and the software. They are
written from the records as they stand, so they are there before the report
is and follow an extension. **Copy** takes them as plain text, without the
Markdown. `GET /api/methods` gives the same.

The page is told when the study changes. The server looks at the study's files
twice a second and sends one event over `GET /api/stream` when any of them, or
the state of the run, is different, and the page then asks for what it shows;
so what the run writes appears within about a second, and an idle page asks
for nothing. The page still asks every thirty seconds while the stream is open,
and every three (the dashboard's refresh setting) where it cannot be opened, as
behind a network that cuts long connections. The data comes
from files the running simulation writes (`simulation/live_status.json`,
`live_metrics.csv`, `live_events.log` and a capped history of live frames) —
`simulation.live_telemetry` turns those on and off, and nothing leaves the
machine.

Live frames are a read-only side channel: they never feed values back into
OpenMM, and failures there are swallowed, so visualisation cannot stop or
change a scientific simulation.

Each live frame is written with its CRYST1 the box of that moment (recorded
too in `live_frame_index.json` as `cell`), and is sent to the Viewer made
whole and centred on the protein, as the frames played are: a snapshot
wraps each molecule into the box on its own, so a bound ligand could
otherwise be shown a box length from its pocket. A snapshot written before
its box was recorded names the box the run started with, so it is imaged
in the box of the trajectory's last frame.

One run per window. Starting a second from the same window returns *"A
FastMDXplora workflow is already running."* Beside the studies started in the
same workspace by another window or by an AI app ([FastMDXplora from your AI
app](mcp.md)), found from the workspace's list of runs, a study that simulates
on a GPU of this computer starts where it fits: on the GPU with the fewest
studies from here and then the most free memory, read with `nvidia-smi`, and
given that GPU. One that does not fit is refused with the memory it needs and
what is free (`environment.workspace.no_room`). The memory one run needs is
learned from the runs here that ran alone on their GPU to the end, as on a
workstation ([Other machines](remote.md#sending-a-study)); until one has, it is
not known and nothing is refused. Work on the CPU (a study on the CPU, an
analysis or a report written again, a fix), and every study on a computer whose
GPUs `nvidia-smi` does not read (a Mac), runs one at a time: the refusal names
the study running and who started it. The rule is kept in the folder the GUI
was started in and the folder it puts new studies in, never your home folder.

---

## When there is nothing to show

A page with no run behind it says so rather than plotting an empty chart. If
setup has not finished there is no structure to render; if production has not
started there are no frames to play. Each says which, and what would produce
it.

That is deliberate. An empty axis and a missing value look the same on
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

**What would fix a study that stopped** is a card on the Overview: each
thing that stopped it, what would fix it, the command and what it costs at the
study's own speed. A fix that is this software's own command (`fastmdx
resume`, windows run again with `--rerun-window`) has a **Run it** button;
pressing it asks once more with the price in view, and the run is followed on
the Overview as it goes. A fix waiting on a choice only you can make, a
setting to change and an install command are said, never run. See
[What would fix it](refusals.md#what-would-fix-it-and-what-it-costs).

**Each figure says what made it.** The chip at the foot of a figure on the
Analysis page names the release that plotted it; opened, it says the packages
whose versions decide its numbers (MDTraj, NumPy, SciPy, Matplotlib), when
it was made, the trajectory and how many frames it rests on, the selection
and the options, and the command that plots it again: the one analysis,
over the same frames with the same selection and options, into a folder of
its own beside the study, so nothing of the study is overwritten. The
command is the command line's own rendering, and run, it writes the same
numbers; where a setting has no flag, a config is given instead. Two buttons
give the same command plotting it at a journal's column width instead, one
column (89 mm) or two (183 mm), with its type sized for that width
(`analysis.figure_width`). A figure made by another release says so, since a
rerun here is plotted by this one. The same figures in the report carry the
same chip on the Report page, under each figure.

**A study of several runs is compared on its Report page.** Once every run
has finished, the batch layer writes the comparison to `comparison/` at the
study's root, and the Report page shows it with its figures; before then it
shows the means the finished runs determined. Under each overlay and trend a
chip names the runs it was plotted from, where each is, the release that
analysed each and, for a trend, the mean and error plotted for each run. It
is recorded in `comparison/figures.json` as the figures are plotted, so a
comparison plotted by an earlier release has no chip; runs analysed by more
than one release are said to be. See
[Reading the results](results.md#comparing-runs).

---

## `fastmdx gui` in full

| Flag | What it does | Default |
|---|---|---|
| `--output DIR` | The run to watch or read. Without it, the GUI opens in "design a new study" mode | current directory, home mode |
| `--host` | What to bind to | `127.0.0.1` |
| `--port` | Which port. If it is busy, the next free one is used and reported | `8765` |
| `--no-browser` | Print the URL instead of opening a tab | opens a tab |
| `--demo [DIR]` | Copy the demo study (trypsin with benzamidine, 3PTB, finished) into DIR and open it. It is not in the package: it is fetched the first time (about 11 MB), checked against the SHA-256 this release records, and kept in `~/.cache/fastmdxplora/demo` (or under `FASTMDXPLORA_CACHE_DIR`). **Open the demo study** on an empty Overview does the same | off |
| `--open SOURCE` | Open a shared study from its DOI, its Zenodo address or its zip, checked file by file before it opens; `--open-into DIR` says where it is unpacked and `--open-most-gb` how large an archive is allowed (2 GB). See [Sharing a study](sharing.md) | off |
| `--ligand-resname NAME` | Which residue the viewer treats as the ligand | auto-detected |
| `--binding-pocket-cutoff-A X` | How near counts as the pocket | `5.0` |
| `--hosted` | Serve it to someone else, through a proxy that signs them in; see [Serving the GUI to other people](hosting.md) | off |
| `--workspace DIR` | With `--hosted`: the one folder it reads and writes | current directory |
| `--allowed-host NAME` | With `--hosted`: a name the proxy serves it under; repeat for more | none; required |
| `--account-url PATH` | With `--hosted`: the service's page for the person, first in the menu at the foot of the sidebar | none; no link |
| `--product-name NAME` | With `--hosted`: the service's name, shown in place of FastMDXplora's at the top of the sidebar and in the title | FastMDXplora |
| `--product-tagline TEXT` | With `--hosted`: the line under that name | none with `--product-name`; FastMDXplora's without |
| `--runs-url PATH` | With `--hosted`: the service's page that runs a study on its compute; adds **Run on a GPU** to the builder | none; not offered |

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
model.** A service that runs the GUI for other people puts a sign-in in front
of it and starts it with `--hosted`, which is described in
[Serving the GUI to other people](hosting.md).

By default that is `127.0.0.1`, the loopback interface. A browser tab on the
same machine can reach it; nothing else on the network can.

### Binding somewhere else

`--host 0.0.0.0` opens it to the network, and FastMDXplora reduces what it will
do. Off loopback every POST returns **403** except `/api/config`, which builds a
config from the form and reads no file. Every GET returns **403** except those
watching a run needs: the page and its assets, the run's status, results and
structures, and its files. In both cases a route added later is refused until
somebody decides otherwise. Among the routes refused off loopback:

| Refused off loopback | Why |
|---|---|
| `/api/browse`, `/api/inspect-directory` | They walk the filesystem for the folder picker |
| `/api/load-config`, `/api/check-config` | They read a file the caller names and quote the line a parse error came from, which is a file-content oracle. A planted token and an AWS key were both recovered this way |
| `/api/run`, `/api/run-config`, `/api/explore/stop` | They start and stop work on this machine |
| `/api/explore/switch` | It changes which folder is served, and so which files `/artifacts/` hands out |
| `/api/open-output` | It opens a folder on the machine running the server |
| `/api/files/reveal` | It opens a file manager on the machine running the server |
| `/api/files/clear-scratch` | It deletes files on the machine running the server |
| `/api/files/deposit` | It writes a zip of the study into it, read through as large as the trajectory |
| `/api/files/sha256`, `/api/files/zip` | They read files through, as large as a trajectory, on the server's time |
| `/api/agent/model`, `/api/agent/propose` | One stores an API key, the other spends it |
| `/api/agent/conversation`, `/api/agent/conversations`, read as well as written | They hold what was asked of the agent and the content of files attached to it |

Files are served off loopback only from the run being watched, and only when
that folder is one FastMDXplora wrote; `--output` naming any other folder, a
home folder say, is served as no run at all. Inside the run, hidden files and
the agent's conversations (`agent/conversations/`) are neither listed nor
served. A viewer off loopback cannot ask for the preview or the trajectory's
frames to be rebuilt.

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

### Other websites open beside it

A browser tab on any website can send requests to `127.0.0.1`, so on loopback
"only this machine" would include every page open while the GUI runs. On
loopback the server therefore answers only to a loopback name (`localhost`,
`127.0.0.1`, `[::1]`), which defeats a name made to resolve to your machine.
Anywhere, it refuses a POST whose `Origin` is not its own, and an API request
that a browser tab marks as coming from another site. A script or `curl` sends
no `Origin` and is answered as before. A proxy in front of the GUI must pass
it a loopback `Host`, unless it is started with `--hosted`.

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
knowing if you want to drive it from a script. Requests are capped at 1 MB,
a movie's frames at 64 MB.

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
| `GET /api/live-frame-index`, `/api/live-coordinates` | The run's latest frame, as it writes them |
| `GET /api/frames-info`, `/structure/frames-topology.pdb`, `/structure/frames.dcd` | The trajectory the viewer plays, as binary frames: the source's own topology lines for the atoms shown (solvent stripped), and a DCD of evenly spaced frames, made whole and centred on the protein, each frame's time (`frame_times_ns`) as the analyses plot it, frame k at k + 1 saving intervals, the interval read from the analysis's record or the run's, under ten million atoms times frames and at most 2,000 frames (`--dashboard-max-playback-frames`); `?of=frames` gives their DSSP; `GET /api/frames-pieces` and `/structure/frames-piece.xtc?k=0` the same frames as XTC, in the pieces the Viewer plays from the first, and `?as=xtc` any of the frames (superposed or not) as XTC; `&span=FROM:TO:EVERY&between=N` gives a movie's frames with 1, 3 or 7 frames in between each two, interpolated in straight lines and written once beside the frames. For a study of several runs, the first run with a trajectory, with `runs_together`: each run's label, colour and frames, those not shown and why, and what they were fitted on; `/structure/frames.dcd?run=N` is the Nth run's frames fitted to the first run's first frame |
| `GET /api/frames-superposed?on=backbone`, `?on=pocket&ligand=LIG&cutoff=5` | The frames played, each fitted to the first on the backbone or on the pocket's backbone (`/structure/frames.dcd?superposed=...`), written once beside the frames, with what they were fitted on |
| `GET /api/chain-contacts` | The hydrogen bonds and salt bridges between the protein's chains in the frames played, by the interactions analysis's criteria, the most often present first (at most 40), each with its runs of frames and its two atoms among the frames played |
| `GET /api/interactions-over-frames` | The contacts the interactions analysis found, the most often present first (at most 40), each with its runs of frames on the trajectory's clock and its two atoms among the frames played, checked by name |
| `GET /api/views`, `POST /api/views` | The views of the Viewer saved with the study; `{"action": "save", "name", "view"}` or `{"action": "delete", "name"}`, saving on loopback only |
| `GET /api/occupancy?of=ligand&ligand=LIG&cutoff=5`, `?of=water`, `GET /structure/occupancy.dx?...` | Where the ligand or the water was over the frames played, fitted on the ligand's pocket to the first frame: written once beside the frames as OpenDX, with what it is, how many frames and the level bulk water reads; the file itself, named by the request's words |
| `GET /api/motion?mode=1&scale=1` | One of the study's main motions, placed on the first frame played: its atoms as a PDB of 40 models swinging along it and a PDB of lines to where it takes them, its share and standard deviation, and whether it was read from the analysis or found from the frames |
| `GET /api/backbone-angles` | Each protein residue's φ and ψ in each frame played, computed by MDTraj from the frames as written: the residues as the frames name them, and the angles in tenths of a degree as 16-bit integers in base 64, frame by frame, -32768 where a residue has none (a chain's ends); written once beside the frames |
| `GET /api/pocket-volume?ligand=LIG&cutoff=5`, `/structure/pocket.dx?ligand=LIG&cutoff=5&frame=N` | The volume of the ligand's pocket in each frame played, in Å³, with its mean, its standard deviation over the frames and the region looked in, written once beside the frames; the empty points of the pocket in one frame as OpenDX (1 at an empty point), placed as the frames are fitted on the pocket |
| `GET /api/contact-map`, `?first=0&second=1&method=kmeans`; `GET /api/contact-pair?a=10&b=120` | Each pair of protein residues in contact in any frame played (heavy atoms within 4.5 Å, residues of one chain fewer than three apart left out), by their places in the list of residues, with the share of frames it was in contact in, written once beside the frames; with two states, the share in the second's frames less the first's; one pair's closest heavy atoms in each frame, as atoms of the frames, and how far apart they are |
| `GET /api/states?method=kmeans`, `GET /api/state-difference?a=3&b=40&on=backbone` | The states the cluster analysis found, each with its share and representative frame played, and the state of each frame played; two frames compared, the second fitted onto the first as the frames are shown, as a PDB, with each residue's displacement |
| `GET /api/beside?path=...`, `GET /structure/beside.pdb?key=...`, `/structure/beside.dcd?key=...` | Another study's frames beside this one's, paired by sequence, fitted and timed, with the residues that differ and the difference in RMSF; on loopback only, since it reads a study the request names |
| `GET /api/water-sites` | The sites the `water_sites` analysis found, placed on the first frame played by fitting the site's atoms, or why they cannot be |
| `GET /api/scenes`, `POST /api/scenes`, `GET /scenes/<name>/<file>` | The scenes written with the study; `{"name", "view"}` writes one (`scenes/<name>.mvsx`) on loopback only; a file of one, `view` for its page |
| `GET /api/movies` | What a movie is made with on this computer (its ffmpeg, the encoder and the format) or why one cannot be, and the movies made; loopback only |
| `GET /api/residue-values` | Each per-residue result the analyses wrote, as `[chain, number, insertion code, value]`, with its unit, range and a sentence saying what it is |
| `GET /api/secondary-structure?of=structure\|live\|frames` | DSSP for each residue of what the viewer was sent, one string of `H`, `E` and `C` per frame, or why there is none |
| `GET /api/protein-preview` | The cached preview image |
| `GET /api/series?analysis=NAME` | An analysis's series as numbers, for the chart plotted from them; `&run=ID` for one run of a study of several; with neither, the study's series over time, each with its label and unit |
| `GET /api/selection?chain=A&resseq=189&resname=ASP&atom=CA` | The selection for a residue and one of its atoms, by `resSeq` and MDTraj's chain index, each checked against the topology the analyses read; `&frames_atom=N`, the atom's index in the frames played, names it exactly where they were written from that topology (a chain letter two chains share, or a residue number two residues share, by `resid`) |
| `GET /api/studies?path=`, `/api/studies-compared?a=&b=`, `/api/study-thumbnail?path=` | The studies under a folder as cards (each with its tags and note), the tags they carry, the most used first, two compared, and a card's figure; loopback only, as browsing folders is, and inside the workspace when hosted |
| `POST /api/study-tags` | A study's tags and note, `{"path", "tags", "note"}`, replacing those it had (without `note`, the note kept), kept in its `study_tags.json`; loopback only, and inside the workspace when hosted |
| `GET /api/residue-states?chain=A&resseq=57&resname=HIS` | A clicked residue's protonation states and this study's Config to start a new study from, the residue named as setup builds the structure |
| `GET /api/measure-over-frames?a=<selection>&b=<selection>` | The command that measures the distance between two atoms at every frame (`pair_distance`, into a folder of its own), each selection checked to name one atom |
| `GET /api/remote/machines`, `/api/remote/job?job=`, `/api/remote/fetch-sizes?job=` | Your machines as recorded at a terminal ([Other machines](remote.md)) and the jobs whose results come back into the workspace, nothing asked of any machine; a job's state, asked of its machine at most every 30 s; what a fetch of an ended job would bring, each part's size; loopback only, and not in a hosted GUI |
| `GET /api/stream` | Server-sent events: one `change` event each time the study's files or the run's state change, and nothing about what changed; the page then asks the routes here |
| `GET /api/stopping` | For a study run until it is determined: the rule, each quantity's error and mean after each round with the replicas' own means, where the error would reach the target at the rate it has fallen, and the piece now running with its time here |
| `GET /api/runs-compared` | For a study of several runs: each run, the settings that differ, and each quantity's recorded mean with its error and whether it differs from the first run's by more than twice their combined error |
| `GET /artifacts/<path>` | Any file under the run root, `?download=1` to attach |
| `POST /api/open-shared` | A shared study, `{"source": DOI, Zenodo address or zip path}`, checked file by file and opened; refused on a hosted GUI |
| `GET /structure/topology.pdb`, `/structure/live-frame.pdb` | Structures for the viewer |
| `GET /structure/live-frame.dcd` | The live frame's coordinates alone, as a one-frame DCD, with its atom count (`X-FastMDX-Atoms`) and the fingerprint its DSSP is matched by (`X-FastMDX-Fingerprint`): the viewer moves the atoms it shows rather than loading each frame as a new structure |
| `GET /analysis-figures-svg.zip` | Every analysis figure, zipped |

**Writing**

| Endpoint | What it does |
|---|---|
| `POST /api/config` | Build, validate and return the YAML for what is on screen, plus the equivalent CLI command and Python script. `"full": true` in the body restates every default |
| `POST /api/check-config` | Validate a Config file without running it |
| `POST /api/preview-system` | What setup will build from the form's structure and settings, how long the study will take here, and what is worth knowing about it. Writes nothing |
| `POST /api/load-config` | Read a Config into form state. Never writes the file |
| `POST /api/run` | Start a run from form state |
| `POST /api/run-config` | Start a run from a Config file, unmodified |
| `POST /api/explore/stop` | Terminate the running workflow |
| `POST /api/remote/plan` | What sending a Config in the workspace (`config`) to one of your machines (`machine`) would do, sending nothing: what travels with each size, where it runs, the GPUs' room, the job script; with a token (`plan`) kept ten minutes. Only files in the Config's folder, inside the workspace, travel; loopback only, and not in a hosted GUI |
| `POST /api/remote/send` | Send the plan shown (`{"plan"}`), once, and only while what would travel is what was shown; loopback only |
| `POST /api/remote/fetch` | Bring an ended job's results (`job`, `with_trajectory`), only at the size shown (`bringing`, in bytes); loopback only |
| `POST /api/remote/cancel` | Stop a job, as `fastmdx remote cancel` does; loopback only |
| `POST /api/report/write` | Write the report of the study open again, as `fastmdx report --output <study> --rerun` does; loopback only |
| `GET /api/again` | What can be run again on the study open and why not, the analyses it ran last and those this release has; loopback only |
| `POST /api/again` | Run the study open's analysis or report again (`phases`, `analyses`), by the phase command with `--rerun`; loopback only |
| `POST /api/agent/model` | Read or set the [Agent](agent.md)'s AI model choice. Never returns the key |
| `POST /api/agent/propose` | A sentence to a validated Config |
| `POST /api/agent/run-summary` | What a run the Agent started found once it has ended (`{"study"}`), from the study's records; `ended` false while it runs; loopback only |
| `POST /api/movies`, `/api/movies/<id>/frame`, `/api/movies/<id>/finish`, `/api/movies/<id>/cancel` | A movie: started with `{"name", "fps", "width", "height", "about"}`, given its frames as PNGs of its size, one a request, and finished into `movies/` or cancelled with nothing left; one not given a frame for five minutes is given up |

`GET /api/schema` is the one worth knowing about: it is the same declaration
the CLI builds its flags from, so anything reading it stays in step with the
software automatically.

---

## Not the same thing: `report/dashboard.html`

The report phase writes a **static** `dashboard.html` into the run directory.
It is laid out as the GUI is: the same sidebar and settings menu (with the
citation's dialog), the Overview, Analysis, Report and Files pages, the
three schemes, and each
series plotted from its numbers as the Analysis page plots it. It is the
study as the report phase found it, and does not update. It opens in a
browser tab with no server running, and travels inside `project_bundle.zip`:
it is the thing to send somebody. What needs the server (the Viewer, the
Agent, the Config builder, live charts) is in `fastmdx gui`, the live
interface.
