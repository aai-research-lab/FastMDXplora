# The FastMDXplora GUI

```bash
fastmdx gui
```

A browser tab opens, and everything FastMDXplora does is in it: designing a
study, starting it, watching it happen, and reading the results.

The GUI is not a cut-down version of the command line. It offers **every
setting the software has** (all 380 analysis options across 31 analyses, and
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
| **All studies** | Every study under the workspace (or a folder you choose), as a list first, a row a study: its molecule (the Cα trace of the latest frame its run wrote, else of the structure it was prepared from or is to start from, seen along its two widest axes, coloured from the N terminus (purple) to the C terminus, the nearer part stronger), its name (with its folder where two share one, and its tags wrapping under it), where it stands (a run going with how far it has got and the time it has left, as the status bar says them, and the ns of its production of the ns planned; a study of several runs counts every run, begun or not, its time left the sum of its runs one after another or, side by side, as its workers take them (those its runner recorded, else its Config's), and not known where neither says), its system, the ns it simulated (for several runs, in all, and each run's length, or the range where they differ, in one unit, or how many began, "1 of 3 runs begun, 18.5 ps", and how many of their lengths are known), every mean it recorded in one cell (a filled dot where it is determined with its error, a ring where it is not, the first two shown and **+N** the rest, each with why it is not determined (**why** where the two shown include one not determined); for replicas, read from their runs as the runs' table and the written comparison read them, the mean of their means with the error their spread gives it, the spread over the square root of their number, or the spread alone, and why, where any replica did not determine its own mean with an error of its own; a mean from fewer replicas than the study has says how many, "from 2 of 3 replicas"), and when it was last active. A trajectory analysed with no run of its own says **analysed**, as does a run analysed whose Manifest is missing; one that simulated with neither says its state is **not known**, and a length its records do not give is **not known**, not none. Each study is read as its own pages read it: a record linked out of it, or a file of it that is not one of its results (a hidden one), is not read, the row naming it under the study's name (**Not read:** and the first records, which open every one with why, by a click, a tap or the keyboard), and what it would have said (where the study stands, how long it ran, a mean, its molecule) is said **not read** rather than taken as absent; a structure it is to start from outside its folder is not rendered. A study's runs folder, or a run, linked out of it is not read when its runs are unfolded (nor when asked by its own path), and a run linked out offers no Open, Tag, Files or box; a folder linked into the workspace from outside it is not looked in, while a study linked in (one kept on another disk) is listed, marked a link to where it leads, and read held to itself (hosted, it is named and not read). A study's tags are changed held to the study too: where its `study_tags.json` is a link out of it, Tag is not offered and a change is refused, said as the row says it. Sorted by a column's head (**Means** by the mean chosen beside it); **Open**, **Tag** and **Files** at a row's end on hover. Where the column is narrower than the table wants, columns give way in turn: Active and System are said under the name, the molecule is left out, the name and the means are made narrow, and Open, Tag and Files come over the row's end; the table holds at 1440 px with the side panel open or folded and at about 1250 with the Agent beside. Views as tabs above it: **All**, **Running**, **Needs more sampling** (a mean not yet determined), **Not started**, and your own (**Save this view** keeps the search, the chips and the grouping under a name, kept on this computer for every browser tab the GUI opens in; Delete or a right-click removes one). Chips narrow by **State**, **System** and **Tags**; **Group by** study (replicas folded under their study, opened with ▸ and read as they open; folders where nothing ran gathered at the foot), system, tag, state, folder or nothing; **Comfortable** or **Compact**; **Columns** hides some. Keys: <kbd>/</kbd> search, <kbd>j</kbd> and <kbd>k</kbd> move, <kbd>Enter</kbd> opens, <kbd>x</kbd> chooses, <kbd>c</kbd> compares the two chosen, <kbd>t</kbd> tags the chosen, <kbd>Esc</kbd> clears them, <kbd>m</kbd> every mean. **Cards** shows the same studies as a gallery, the molecule as each card's picture, as many a row as fit; in a window under 1000 px, or where even so the table does not fit, each row is a few short lines, a running study's progress and time left on a line of their own, keeping the time left, when it was last active and Open, Tag and Files, with **Sort** and **All shown** beside the chips in place of the table's head. Search narrows them; **Open** makes one the study on screen; while any study is chosen (its box, <kbd>x</kbd> on its row, Shift and a click for every study from the one chosen last, or the box in the list's head for every study shown), one bar at the foot of the list says how many and offers **Compare** (with two: the two on the Compare page, below, Back returning to them chosen; <kbd>c</kbd>), **Tag** (each tag with how many of the chosen carry it: ticked, it goes on all of them, cleared, it comes off all of them, and a study's other tags and note stay as they were, a tag an AI app or another window gave it since among them; <kbd>t</kbd>), **Files** (with one: its Files page), **Archive** (the studies left out of All studies and every view but **Archived**, which shows them with **Unarchive**; kept in each study's `study_tags.json`, its tags untouched, nothing of the study moved or deleted; a run of a study is archived with its study, so not offered for one) and **Clear** (<kbd>Esc</kbd>); **Undo** follows Tag and Archive. The bar has room of its own under the last row, and stays in view at the window's foot while the list runs past it; on a phone it runs across the foot of the window. **Tags** are words of your own on a study ("wild type", "JCIM Fig. 4"), up to 20 a study and 40 characters each, with a one-line note: **Tag** (marked with a dot, and named **Tags**, once it has some) edits them on the row or card (what is put on and taken off is kept, on the tags as they are by then), offering the tags the studies here carry as you type; a tag clicked shows only the studies with it, and search reads tags and notes too. They are kept in the study's folder (`study_tags.json`), beside its records and never in them, so they go wherever the study goes (but not into a shared study). **Open a shared study** (the box with an arrow, beside **Look in another folder**, the folder with a magnifier) takes a shared study's DOI, its Zenodo address or the path of its zip on this computer, checks every file against its SHA-256 and opens it in a folder of its own where new studies go ([Sharing a study](sharing.md)). Under the studies, **Remote jobs** lists the jobs sent from here whose results come back into the workspace: **Ask how it is doing** (at most every 30 s), **Fetch the results** once it has said what it brings and its size, and **Stop it** once asked again ([Other machines](remote.md#from-the-gui)) |
| **Agent** | [The FastMDXplora Agent](agent.md): a conversation that writes, edits, runs and reads a study |
| **New study** | The [Config](config.md) builder, in five steps: **Start** (a structure, a trajectory or a config you have; the systems as rows, each with what its file holds; the results folder and the phases that run; **Start from an example**: a protein in water, a protein and its ligand, a membrane protein, a study run until a quantity is determined, and a free energy along a distance, each a complete Config of the [worked examples](examples.md) loaded into the form to change, or the Agent), **System**, **Protocol** (a fixed length or until it is determined, replicas, enhanced sampling as cards), **Analysis and report** (the analyses under the Analysis page's themes, each that this study leaves nothing for saying why) and **Review** (the plan with what is checked after, what is worth knowing, the reasons recorded, where it runs). Each group shows its essential settings and **Show more** the rest; each setting has its unit, its default, what it does when left unset, a dot and a revert once changed, and **Why this value?** to record its reason as `decisions`. **Find a setting** finds one by its name. All five steps are shown from the start: before a starting point is chosen, steps 2 to 5 are as a study from a structure has them, there to read and not to change, and **Run on this machine** is quiet until there is a study to run. Beside the steps, the study: whether its checks pass, **What it costs here** (as a compiler's diagnostics, each line a note, a warning or an error: the ns/day a run makes on this machine's own timing, the time every run takes, the disk its trajectories take against what is free where it is written, an error where it does not fit and a warning where it would leave the disk all but full, and the GPU memory a run needs, from the runs measured here, against what the GPUs have free, as a Run here chooses its GPU), what setup will build, what changed, the Config, the command and the script, and **Run on this machine**; where the page is narrower than 784 pixels with the sidebar folded (a small window with the Agent open beside it, which New study and **Or describe the study to the Agent** do), the study goes under the steps, with whether it will run and **Run on this machine** kept at the page's foot. The form is saved with the page, so a reload or a new browser tab loses nothing. Where a machine has been inspected at a terminal, **Plan the send** beside **Run on this machine** (or under a config file's own run) shows what a send of the study to it would do, every file that travels with its size, and sends it only with **Send to** the machine; a study built in the form is saved first in the folder new studies go in, and where that is the home folder, Plan the send says so beside it before anything is asked ([Other machines](remote.md#from-the-gui)) |
| **Overview** | A live run: health first, then the live charts, the structure as it is written, and once there are results, the recorded numbers. A study of several runs: its runs as a table (each one's state, how far its production has got, its speed in ns/day and its own means), for replicas the mean of their means with the error their spread gives it (the spread over the square root of their number, from the replicas that finished), what is not determined and in which run, and one measure of every run overlaid; a run opens from its row, two ticked open on the Compare page (no box to tick on a GUI bound beyond loopback, where Compare is not answered), and the table is read again as runs go. A run that ended leads with one sentence: how many of its means are determined and, where some are not, how much more production the analyses asked for and what that takes at the run's own speed (the largest ask, a floor where a run could not resolve its own correlation time; said as not in the records where they do not give it); each tile says Not determined quietly, with its reason in a line (the whole on hover); a mean noted for the solute's own periodic image keeps its verdict and says the note under its value, as the Analysis page's table does (the analysis's paragraph on hover, and a click away in the table); where the page's column is 1180 px wide or more (about 1400 px of window with the side panel folded) the means and the run sit side by side |
| **Viewer** | The molecule in 3D, live while running and played back afterwards; follows the run by default. The molecule is shown 4 wide to 3 high, as large as the window allows with the head of the sequence above it and the timeline under it in view, centred with the settings beside it; the sequence folds as the settings' sections do, closed until first opened, and stays as it was left; opening it leaves the molecule's size as it was, pushing what is under it down, and the column under the header scrolls to reach it. The **timeline** is always under the molecule while there are frames to play: the transport, a ruler of the frames on production's clock (by their numbers where the run recorded no clock) with the frame shown marked, the frames each analysis left out as equilibration shaded, each cluster's medoid (the frame its clustering points to as most like the rest of its cluster) and each judgement of a study run until it is determined marked, named on hover, and the study's series as tracks under it (RMSD, the radius of gyration and the next listed at first; one is added from the list and taken away by its ×, the choice kept for the next time); a click or a drag on the ruler or a track, or a click on a mark, shows that frame. The settings are a rail of tools on the right (View, Display, Selections, Ligand, Saved views, Information, ...), the one chosen a panel as tall as the rail, scrolled on its own. **Sequence**, above the molecule, gives each chain as a line of one-letter codes, the structure's own residue numbers written over every fifth residue (5, 10, 15 ...), a gap where numbers are missing and a bar under each residue in a helix or a strand in the frame shown; a click selects a residue, a drag a run, Shift extends and Ctrl or Cmd adds or removes one, and the selection is rendered in the structure as green sticks; an atom clicked in the structure selects its residue there (with Shift or Ctrl, adds it); a double click centres the camera on the selection; from the keyboard each line is a list, the arrows moving along it (with Shift, extending the selection), Up and Down changing chain, Enter selecting and Escape clearing. **Selections**, in the panel, are PyMOL's kind: one typed in MDTraj's language (`resSeq 10 to 25 and backbone`, as a Config writes it) is read by MDTraj in the very structure the Viewer renders, so its atoms are the ones shown; what is selected, typed or in the sequence or the structure (a ligand clicked too), is named with **Name it** and listed, each with its colour, painted over every representation of its atoms, an eye that hides its atoms wherever they are rendered, a representation of its own (sticks, spheres, lines, a surface or a cartoon), labels for its residues, and buttons to select it, centre on it and forget it; they are kept with the study (`viewer_selections.json`), and found again in whatever the Viewer renders, the structure with its solvent or without, or the frames: residues by their chains, numbers and names, a typed selection by MDTraj reading it again. It is rendered by Mol\* (5.12.0, bundled, so nothing is fetched from outside the GUI) from the trajectory sent as binary frames, each made whole as the analyses read it, as XTC (each coordinate to a hundredth of an angstrom, about a third of the size of a DCD) in pieces of up to two million atoms times frames, played from the first while the rest arrive, how many are here said beside the transport; while a study runs, each new frame's coordinates alone move the atoms already shown, so what is being measured stays measured; on each track of the timeline, a series over time is plotted with the frames its analysis left out as equilibration and the mean of the rest, a line marking the frame shown as it plays and its value on it; **Superposed** fits each frame, by MDTraj's least squares, on the protein's backbone or on the backbone of the residues within the pocket cutoff of the ligand in the first frame, **to** the first frame, to the structure the run started from (as setup prepared it) or to the deposited structure the study was given (`setup/input.pdb`, its backbone atoms matched to the frames' by chain, residue number and name, the first of alternate locations and the first model, and how many matched said), and **Smoothed** averages each atom's fitted position over 3, 5, 9 or 15 frames centred on the one shown (over fewer at the ends; frames shown as written are fitted on the backbone first, since an average of a molecule turning is a molecule shrunk, and an average shortens bonds a little, so measure on frames as written), at the frame shown, the measurements kept (any water shown is the first frame's, so it is not shown where the frames are fitted to another structure, and the periodic box is not shown, since each frame is turned out of its own); **Periodic box** shows the box of the frame shown, following an NPT run's changes, as the brick setup and the frames keep the water and ions in (a by b by c along x, y and z, which for a rhombic dodecahedron or a truncated octahedron is a brick of the same volume with the same periodic images), about the water shown or, without it, about the protein; **What holds the ligand** lists the contacts the interactions analysis found (`pl_interactions`), the twelve most often present, each a row marked where it was present over the frames played with its share of them, and shows those of the frame shown in the structure as dashed lines between their atoms, coloured by kind, kept apart from the ruler's (**In the structure** turns them off); **Between chains**, where the protein has more than one, lists the hydrogen bonds and salt bridges between its chains in the frames played, by the criteria the interactions analysis applies to a ligand (donor and acceptor within 3.5 Å with the angle at the hydrogen above 120°; charged groups' centres within 4.5 Å), the most often present first with the share of frames each was present in, and shows those of the frame shown in the structure as dashed lines, blue and orange, kept apart from the ligand's; **Views** saves the view with the study under a name (`viewer_views.json`): the camera, the frame, the representation and colouring, the parts shown, the superposition, the pocket cutoff and the publication look, each checked, and choosing it again shows it as it was; the box icon beside them writes the view as a **scene file** (MolViewSpec, `scenes/<name>.mvsx`, downloaded too): the atoms shown at the frame shown (superposed where the view is), the study's DSSP as the cartoon, the colouring (a result on the Viewer's scale), the parts shown, the selections named, the camera, the ground and the look, which opens as it is shown here in any viewer built on Mol\* (molstar.org among them), as `fastmdx scene` writes it from the command line and an AI app with `write_scene`; **Scenes** lists the scenes written with the study, shows the one chosen in this Viewer again (the eye: its frame, camera, representation, colouring, parts shown and superposition, the camera shown kept where the scene was written without one), and opens it on a page of its own where the Viewer's engine is given the scene and nothing else, as any viewer built on Mol\* shows it; **Movie** makes a movie of the frames as they are shown (from one frame to another, every so many, at 10 to 60 frames a second, as wide as the view is shown at 1920 pixels across or at 720p, 1080p or 4K, with the time of each frame in its corner, the colour bar of a result, and one turn about the screen's vertical if asked): each frame is rendered as a picture and encoded by ffmpeg on the computer the GUI runs on, as H.264 in an MP4 or, where that ffmpeg has no H.264 encoder, VP9 or VP8 in a WebM, its colours as BT.709; **In between** puts 1, 3 or 7 frames in between each two frames played, each atom moved in a straight line from its place in one to its place in the next, for a smoother movie: a display, not more simulation, which shortens a bond whose atoms swing far between two frames, so the frames are superposed on the backbone first where they are shown as written, and the movie's record says it was interpolated; the movie is kept with the study (`movies/<name>.mp4`) and downloaded, and the frame and the camera are put back; a study without frames (set up and not yet run) gives the structure shown turned once over six seconds; the page icon gives the **publication look**, a white ground with outlines and shading at the highest quality, for a figure; a terminal cap such as ACE or NME is rendered with its chain. **Over the frames** shows where the ligand and the water went over the frames played, as VMD's VolMap does: space cut into cubes half an angstrom wide and, for each, the fraction of frames in which a heavy atom of the ligand (within 1.5 Å) or a water's oxygen near it (within 1.4 Å, up to 500 frames, read from the trajectory, which needs `simulation.save_selection: all`) is there, each frame fitted on the backbone of the ligand's pocket to the first frame played; each map is rendered as a surface where that fraction reaches the level chosen (30% and 60% to begin with; bulk water reads about 32%), written beside the frames as OpenDX (`simulation/occupancy_<ligand or water>_<ligand>_<cutoff>.dx`); **Water sites** places the sites the `water_sites` analysis found as spheres, vermilion where one molecule held the site and sky blue where many passed through, each listed with its occupancy; the frames are superposed on the pocket to the first frame as these are shown, and they are hidden while the frames are fitted otherwise. **Main motions** shows the study's principal components, the collective motions it spends most of its fluctuation on: the `dimred` analysis keeps them (`analysis/dimred/dimred_pca_modes.npz`: each motion over its atoms, its variance and share, the mean structure), and a study analysed before has them found from the alpha carbons of the frames played, by the same steps, and is said to; **Play the motion** swings the motion's atoms as a backbone from two standard deviations of its projection one way to two the other, **Lines along it** shows a line from each atom to where two standard deviations take it, each placed on the first frame played, its share and how far it moves an atom said, and **Size** makes it three times larger to be seen, which is said too. **States** lists the states the `cluster` analysis found (k-means, hierarchical or DBSCAN, chosen where it ran more than one), each with its share of the frames analysed and a representative, its medoid among the frames played (the frame with the least summed RMSD to the state's others, on the atoms the analysis compared; each frame played takes the state of the analysed frame nearest it), which **Show** shows; **Compare** shows the second state's representative in grey beside the first's frame, fitted on the alpha carbons as the frames are shown, and colours the protein by how far each residue's alpha carbon moved between them, with the RMSD said; the grey structure is beside that frame only, and **Stop comparing** puts the colouring back. **Backbone angles** is a Ramachandran plot tied to the frame shown: behind, in grey, where every residue's φ and ψ fell over every frame played; over it, each residue in the frame shown as a dot (hollow for glycine), moving as the frames play; and the residue chosen (from the list, or by selecting it in the structure or the sequence) as its path over the frames, faint at the first frames and solid at the last, with a ring at the frame shown and its φ and ψ said, each mark shown beside what it is in a key under the plot; a click on the path shows that frame, and a click on a dot follows that residue and selects it. The angles are computed by MDTraj from the frames played as written (a dihedral is the same in a fitted frame) when the section is first opened, and kept beside the frames. **Contact map** gives each pair of the protein's residues the share of frames played they were in contact in, darker the more often: two residues are in contact when any heavy atoms of the two are within 4.5 Å, MDTraj's closest-heavy contact at its 0.45 nm cutoff, residues of one chain fewer than three apart left out as MDTraj leaves them out (the frames are made whole and centred on the protein, so no contact is looked for across the box); a line marks where a chain ends; pointing at a cell names the pair and how often; a click follows it, selecting both residues in the structure and joining their closest heavy atoms with a dashed line in the frame shown, as the frames play, with how far apart they are; where the cluster analysis found states, **State** B **vs** A and **Compare** colour each pair by how much more often it was in contact in one state than in the other (the share of B's frames played minus the share of A's), red where more often in B and blue where more often in A; a key under the map says which colour is which, and for the map of every frame, that darker is more often. **Pocket volume**, for a study with a ligand, plots how much room the protein leaves in the ligand's pocket in each frame played, counted as POVME counts it (Durrant et al., 2014): each frame fitted on the pocket's backbone to the first, the points of a grid half an angstrom apart within 4 Å of the ligand's heavy atoms in the first frame, each empty in a frame where it is beyond every protein atom's van der Waals radius (Bondi's), inside the hull of the pocket residues' heavy atoms (the open solvent at the pocket's mouth left out) and joined to the ligand's place; the ligand, water and ions are not counted as filling it. The plot gives the mean, a line at the frame shown and its volume, and a click shows that frame; the spread said is over the frames, not the error of the mean. **Show the pocket** renders the empty points of the frame shown as a green surface, following the frames, the frames superposed on the pocket to their first frame. **Compare study** plays another study's frames beside this one's, a wild type and its mutant or two related proteins, chosen from the studies of the workspace: their residues are paired by their one-letter sequences (difflib's matching, which pairs every residue of two forms of one protein but those inserted or deleted, and pairs a substituted one as a mutation, each listed), the other's frames fitted on the paired alpha carbons to this study's first frame, and played by simulation time, each frame beside the other's nearest it (by place in the run where a run's clock was not recorded), in orange, hidden while the frames played are not fitted on the backbone to their first; **Coloured by the difference in RMSF**, where both studies have an RMSF, colours this study's protein by the other's RMSF less its own over the paired residues, red where the other moves more and blue where less; the fitted frames are kept with this study (`viewer_beside/`). **Runs**, for a study of several runs, plays them together: the first run with a trajectory, in the order the study planned them, is played as any study's frames are (what is clicked, measured, selected, the pocket and a colouring by a result are its), and every other run of the same atoms is rendered beside it, protein and ligand, in the colour the Analysis page gives it, **Coloured by** set to **Run** so the run played is in its own; each other run is read at the source frames the first run's frames were taken from, so frame k of every run is the same step of its simulation, made whole the same way, and fitted by least squares on the protein's backbone to the first run's first frame, so every run is superposed on one shared structure; the frames played are superposed on the backbone to their first frame as they open, and the other runs are hidden while they are superposed otherwise or shown as written; a run with fewer frames is not shown past its last, each other run can be hidden with its box, and a run of other atoms (another system, or one prepared otherwise) or with nothing written yet is listed as not shown, with why; a run still running is played from the snapshots it has written, each frame beside the snapshot it had written last by that frame's time, fitted the same way and said to be running, and is rendered again as it writes more without the frames played being loaded again; while no run has finished, the first to have written snapshots is played; the fitted frames are kept at the study's root (`viewer_runs/`), written again when a run's trajectory changes; a scene written from such a study is of the run played alone, and says so. **Measure** (the ruler, or M): two atoms clicked give their distance, three the angle, four the dihedral, shown in the structure and listed in the Selection tab, following the frames; two atoms can be measured over every frame with the command it gives. A clicked histidine, aspartate, glutamate or lysine offers its protonation states: choosing one opens the Config page with this study's Config and that residue set in `setup.residue_states`, found as setup builds the structure. The camera button saves the view as a picture as wide as **Picture** says, whatever the window's size: 2,400 pixels across (a double-column figure at 300 dpi) unless 1,200 (one column), 3,600 or 4,800 is chosen, in the view's own shape, and on no ground at all where **Transparent** is ticked. **Coloured by** offers the study's per-residue results where its analyses wrote any: RMSF, mean SASA, contact with the ligand, the N-H order parameter S², and the RMSF implied by the deposited B-factors, read from the analyses' own files; for a study of replicas (runs that differ only by their seed, or start from different models of an ensemble), the mean over the runs of the same atoms as the run played ("RMSF, mean of 3 runs"), with each residue's standard error across them and each run's own value in the Selection tab, and for runs that differ otherwise, the run played's own, said to be. Blue is the low end of a result's range, red the high end (for S², the mobile end); RMSF and the RMSF from B-factors share one scale from zero. A bar over the canvas gives the range, and the saved picture carries it; a residue with no value, or one the structure shown cannot tell from another (an insertion code lost on the way), is grey, and the line under the controls says how many. The Selection tab gives a residue's value in each result and its secondary structure in the frame shown. The cartoon is DSSP, as the `ss` analysis computes it, for the structure, the live frame and every frame played, read from the trajectory's own coordinates for the frames; the Structure tab says so, or that the cartoon is Mol\*'s own DSSP and why (Mol\*'s is computed chain by chain, so a strand paired with one in another chain, as in a fibril, is rendered as coil) |
| **Analysis** | **What the analyses determined**, one table first: for each analysis, under what it studies, the mean it recorded over its equilibrated frames to the place its error allows (and any other quantity it recorded a mean of, such as the helix fraction or the asphericity), the frames it was averaged over and from when, the independent samples behind it, and **Determined** or **Not determined**, which opens to say why (a run too short against its correlation time, a molecule broken across the box, a biased run with no reweighted value); a failed analysis gives its reason; **Download CSV** gives the table with each standard error and unit. A name goes to its section. The sections follow, each analysis its own, grouped by what it studies, with an index of them and **Find an analysis**, which keeps the sections whose title or figures hold every word typed. Each section is a cell: under its heading, the values the table gives it, what it was run with (its frames, the atoms it measured and its options, as its record keeps them) and **Analyze again** with it alone ticked. Every plot zooms by a drag across it (on both axes where both are quantities, as on a landscape), moves with Shift held and shows all again with a double click or **Show all**; from the keyboard, on a plot with focus, + and - zoom, [ and ] move along (the arrows too on a figure, where they are not the plot's cursor) and 0 or Escape shows all; **SVG** (on the ground it is plotted on), **PNG** and **CSV** save what it shows. The overlay of a study's runs has the same. A time range dragged across a plot on the simulation's clock is the page's: every plot on that clock shows it, a line at the top of the page says it with **Show all**, and the Viewer's timeline marks it on its ruler and tracks. A map (secondary structure over time, a matrix, a landscape) is plotted as its cells, sharp at any size, and as a picture of them only past 8,000 runs of a colour, which it says; a run shorter than a nanosecond is read in picoseconds. Each figure's card links the data it is plotted from, and a series over time has **Convergence**: the running mean of its equilibrated frames with its standard error beside the mean the analysis recorded, the standard error from blocks of 1, 2, 4 and more frames (Flyvbjerg and Petersen's blocking, where it levels off once blocks are longer than the correlation), the autocorrelation with the integrated correlation time, and the distribution of the frames averaged beside the frames left out, all from the estimator the recorded error comes from (`statistics.convergence_of`), and said in a paragraph; where the recorded mean started where the study's replicas equilibrate together, it says so. For a study of several runs, the runs side by side: the settings that differ, the mean each run recorded with its error, a difference marked only where it exceeds twice the two runs' combined error (replicas are set against their own errors instead), and one measure from every finished run overlaid. **Analyze again** runs the study's analyses again in its folder, from its records and the settings it recorded, with the analyses chosen (those it ran last ticked, any other this release has to add); the report is written again too where the study has one, and what is replaced is kept in `previous/`. A study of several runs is analysed run by run and its comparison built again. Nothing is simulated; it asks first, and runs `fastmdx analyze --output <study> --rerun` (with `--include-phase analysis report` on `explore` where the report is written again too) |
| **Report** | The report itself, rendered as a document, with downloads for what was produced and a notice for what could not be. Beside it, its outline: each section numbered as the PDF numbers it (2, 2.1), the one being read marked, each a way to it; where the page is narrower, the outline folds above the report. A number the report states with a unit, an error, a percent or what it counts, or with a decimal point, opens on a click the record it was traced to: the file, the place in it and the value as recorded, and the value in the report's own units where they differ (nm and angstrom, fs to microseconds, K and degrees C). A value is taken where it rounds to the number as given and the record is plainly the number's: a record whose name or own field gives a unit of the number's kind (never one with no unit for a number said with one), one that counts what the number counts, or one its words or its table column name; an analysis's record only for a number in its section, row or words, and a run's only for that run. Where several records could hold it, each is named and none picked; where none is, the card says no record was traced (a threshold of the method, a citation, a number worked out from more than one record). A record a number was traced to when the report was first read, whose value has changed since, is named under the outline and on the number's card. **Mark the N not traced** marks each number no record or more than one holds (not those of a record not read). A number's card opens beside it and takes the keyboard's focus; Escape returns it to the number. **Write it again** runs the report phase alone on the study open (each run's, for a study of several), from its records and the report settings it recorded, after asking, the report before kept in `previous/`; nothing is simulated or analysed |
| **Files** | Everything the study wrote, in the order it ran. **What you came for** first: the trajectory with the topology it is read with (`trajectory_topology.pdb` where the run saved its atoms without the water), its frames, time and atoms said, a zip of the two and the `md.load` line that reads them; the report, the bundle, the system as simulated, the final state and the configuration. Then the space each phase takes, and the phases as sections, a row a file: what it is, where it is, its size and when it was written, **Preview** in the side panel, **Download**, and a menu that opens it in a tab, copies its full path, shows it in Finder (Explorer on Windows, its folder elsewhere; on the person's own computer only) or copies its SHA-256. Each analysis is a row under what it studies, with its figure, the mean it recorded where its error was determined, its files (a zip of them) and **Open in Analysis**. The run record, what `--rerun` set aside (`previous/`) and the scratch (the frames the Viewer was sent and what it computed from them, written again from the trajectory when needed, and the live view's snapshots of the run as it went) are folded; **Clear scratch…**, on the person's own computer, says how many files and bytes would go, and how many are snapshots, which nothing writes again, and removes them once asked, beside its button: never while the study runs, and each run's snapshots (the study's, each run's of several, each segment's) judged by its own record: kept while it runs, where it has no trajectory, and where it failed or ended without saying so, since its trajectory may be cut short and the Viewer plays the snapshots instead. A link is removed as a link. The trajectory, the analyses and the report are not touched. **Find a file** keeps the rows whose name or path holds every word typed, wherever they are folded; the filters keep one kind (structures, trajectories, data, figures, documents, logs, configs and records); the sort is by the study's order, name, size or time; **Folder view** lists the study's folders instead; a study of several runs has a run picker. **Prepare a data deposit** writes one zip for Zenodo, a journal's data repository or a colleague into the study's `deposit/` folder: the sets ticked (the inputs, the configuration, the trajectory with the topology it is read with, the final state, the analyses' numbers, the figures as SVG or as PNG (a figure with no twin of the kind chosen kept as it is), the report with the pictures it shows, and the records; the checkpoint, binary and read only by the OpenMM build that wrote it, a joined study's segments, which its joined trajectory is made of, and what was kept from the Viewer only when ticked), each set's size said, a `README.md` written from the study's records (what was done, as the report's methods say it, the software and its versions, how to read the trajectory, every file and what it is, how to check them and how to cite), shown before it is written, and `SHA256SUMS`, checked with `sha256sum -c SHA256SUMS`; never the scratch, what `--rerun` set aside, the project bundle or an earlier deposit; one at a time, and not while the study runs. Files are sent a megabyte at a time, a range on request, so a download that stops resumes |

Three things are on every page. The **sidebar**: the two ways to begin, a
new study and the Agent, the workspace's newest studies, the study on
screen and its pages, where a run stands while it runs, and at its foot the
person and the settings. The **side panel**:
a *Log* tab, which is what the command line prints, sorted so a refusal is a
red-edged block and the explain text a quiet one, with filters and a
scroll-to-newest toggle; a *Files* tab, which opens any of the run's
files in place; a *Problems* tab, the study's health in one list
(below); and a *Timeline* tab, what happened to the study, newest first,
from its own records: each phase as it last ran, one a run again replaced
or set aside (kept in `previous/`) and each piece an extended study was
carried on in; when, how long, the version and the computer that made it,
and what it changed where the records say: the means an analysis run again
moved (before and after, those the same, those not run again), the
settings a piece was carried on with. Who asked for a step is not
recorded, so it is not said. A record of the study not read (a link out of
it) is said not read at the top, and a mean whose record was not read is
said so, never as one added or not run again. A study of several runs
takes no steps of its own: its runs' steps are listed, each named by what
sets its run apart ("seed 1: Simulated"), with the runs that recorded none
said. A study run until what it asks is determined lists its rounds
(`stopping.json`): the production each judged and what was decided on it
("Round 1: each run extended by 500 ps", lengths in ps under 1 ns), a round
stopped said by what stopped it (an analysis that recorded no mean, Ctrl+C
or a signal, an error while the runs were extended, read from the round's
own `stopped_by`), and "not recorded" where its record does not say. The
last round of a record still saying the study runs is said "extending each
run by 99.5 ps; the record does not say it finished", never as done, since
a record cannot tell a study still running from one killed; a study resumed
and stopped before its rule was applied again says that as the rule's own
step, never as its last round's. A round's record gives no time: it is
placed by the recorded times of the steps around it, just before the
pieces it began or, beginning none, after the analyses and report it
judged, and says so. A run killed,
or ended by a signal without writing its end, is said "Stopped without
finishing" with the stage and step it had reached, at the time it last
wrote, never as running or as finished at its last step. And two **seams** between
the columns that drag, with a
double-click to reset; the sidebar folds to a strip of its icons under
FastMDXplora's mark, the side panel to a tab at its edge, and the centre stays
centred at a reading width whatever is folded. Pointing at the strip's
mark, or the panel's tab, shows the column over the page for as long as
the pointer stays on it or the column, with one button that keeps it; a
click on that button, or anywhere in the column with nothing of its own
under the pointer, keeps the column open. The strip's icons are used as they
are, without opening the sidebar; Recent's and Conversations' show their lists
beside the strip, Conversations' with **New conversation** first (**New chat**
where no study is open). **Conversations** in the sidebar is one list: the
open study's first, then the chats of no study, each part named where both
have some; its + begins one about the open study, or a chat where none is,
and **New chat, about no study** begins a chat of no study while one is open. Opening the Viewer folds both, and
another page brings them back as they were; a column kept open on the
Viewer stays open there where the window has room for it. The GUI fits the
window: where a page would have less than it needs (788 pixels for the
Viewer and its tools beside the molecule, 784 for the Config builder and
its summary beside the form, 480 for the others), the sidebar folds to its
strip, then the side panel, and only then do the Viewer's tools or the
builder's summary go under the page; where folding the side panel alone is
enough, the sidebar stays. So the tools and the summary stay beside the
page in a window of 900 pixels or more, or about 1250 with the Agent beside
(a little more where scroll bars take room, as on Windows). The Agent
opened beside a page with its own right panel (the Viewer, the Config
builder) folds the sidebar too, and closing it, or going to a page without
one, gives it back where the room allows; opened again by you while the
Agent is beside, it stays open until the Agent closes or the page changes.
A column you open or widen is not folded to fit until you change page, even
where the page then has less than it needs; a double-click on its edge puts
its width back. The builder's form is laid out as on a phone where it has
less than 680 pixels (380 beside the summary at the least). Every page's
header is one band across the centre column, and stays put while the page
scrolls. The three columns' top bars are one height, their rules on one
line: the product's name at the top of the sidebar, each page's title,
centred, and the side panel's tabs.

The **Problems** tab lists, from the open study's own records, each refusal
(its code, its words and the registry's remedy), those of its phases in
`manifest.json` and each report format not produced in
`report/not_produced.json`; a run interrupted or stopped, as the Overview
says it now (one whose record says it is going, but whose process is gone or
which has written nothing for a day, is interrupted; a stop being carried on,
or carried on to the end, is no longer a problem, and neither are the stop's
own warnings); the report's verdict that an observable has not equilibrated,
read from its convergence checks in `report/report.md` with the observables
still moving; each warning recorded in `simulation/live_events.log`, in a
piece's own narration, or in the study's log (`fastmdxplora.log`), each
once, but not one the log keeps from a phase a later run did again (each run
appends to the log; a phase run again replaces it and the phases after it);
and each mean not determined, with the quantity and why. Under **What
would fix it** is the remedy the records hold: for a mean withheld for want
of samples, its record's own words for the more production it asks for;
where the records hold none, it says no remedy is recorded. A quantity the
same in every frame is named as the Overview names it. The file icon opens a
problem's record in Files, at its first line; for a run that ended in the
piece carrying it on, that piece's own record. With no problems it says so
and names the records it read; a record missing or unreadable is named as
unavailable, and the headline then never says the study has no problems. A
running study's `manifest.json`, written when the run ends, is said as that,
not as unavailable; a folder with no records yet says so; a study whose
folder has gone is said to be gone. The tab only reads: it writes no file,
starts no run, and reads nothing outside the open run's folder. It reads the
open run alone, not its campaign or the runs beside it; for a campaign, open
each run. A record linked out of the study, or to a hidden file in it, is
refused, and the list is then unavailable and names the record by its place
in the study; of the hidden files, only the run's own process record
(`.fastmdxplora_run.json`) is read, to tell a run going from one that ended.
It is read when the study opens, when the tab is shown, with **Refresh**, and
again only when the run writes something new (its live record's update time
or its events log changes, as the page learns from its updates); nothing
reads it on a timer, and the status bar's count is that read's.
With the Agent beside a page, the Agent takes the side
panel's place, so Log, Files and Problems come back when the Agent closes.

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
opens the Agent beside whatever page is open, in the side panel's place
(from 340 to 440 pixels wide, by the window, and the whole width on a
phone; beside the page in a window of 932 pixels or more, over it in a
narrower one), and closes it again; beside a page or over it, its box
asks in shorter words. It is the Agent page's own conversation, so one
begun on either goes on in the other, and **Open as a page** takes it
there. A
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
computer by the study's folder, as the settings of You are. All studies says how
many studies the workspace holds.

**Where you are, at the head of every page.** A study's pages lead with
the study, the run, then the page: `villin › seed 1 › Analysis`. The
study's name opens its Overview; for a study of several runs, the run is a
menu of *All runs* (the study as a whole, its runs compared) and each run,
with its state and how far it has got, each opening on the page shown.
A run is named by what sets it apart from the others ("seed 1",
"temperature 310 K"), its folder on hover. Each page is named by the
sidebar's word for it.

**The status bar**, a strip under every page (not a panel: nothing in it
scrolls or opens), says this computer and its GPUs (as `nvidia-smi` reads
them, or the OpenMM platform a study here would run on); the run going
here, with its stage, which of the stages it can reach it is in, how far
it is and the time left at its speed so far, and **Stop** beside it while
it runs (asked once more first, as on its Overview), or where it stopped,
failed or was interrupted, with **What would fix it**, which opens the Overview
at that card (for an interrupted run, `fastmdx resume`, run from there only
where its process was seen to be gone); a run going in another study of
the workspace (from this GUI at most one study runs at a time), opening
it on the page shown (its Overview from a page of no study); each job on one of your machines as its last recorded status says it
(nothing is asked of the machine); the open study's recorded problems,
opening the Problems tab; **Notices**, with how many are new, opening every
notice the page gave (each saved as it is shown, with no path in it), each
run seen ending (with **Open its Overview**) and each refusal the Log reads
while the page is open, the last 200 saved on this computer, one of each
however many windows saw it, shown as All, Runs, or Refusals and warnings,
and **Clear the list** with Undo (Tab stays in the list, and on the Undo
of a notice shown beside it, until <kbd>Esc</kbd> or a choice); the Agent's
mode, opening its
settings; and
the page's own updates, paused (the run goes on) or refreshed now. The
Agent's mode is said by its name and its two words: **Seeker: built-in,
assisted** (it asks before anything is run) or **Ranger: built-in,
autonomous** (it runs within the ceiling set). The AI model is never said
in the strip; it is in the Agent's settings. A hosted service's Intel
level would be said beside the mode, but no levels file is read yet, so
the strip has no Intel item, and on your own install it never has. A
finished study can be opened and read
while another runs. A run is stopped from the strip, from its Overview,
beside how it is going, or from ⌘K, each asked once more first. On a phone
the strip is not shown.

**Asked once, said one way.** What cannot be undone (stopping a run here or
a job on one of your machines, clearing the scratch) is asked beside the
button that was pressed, in one form: what it does, what goes and what is
saved, **Not now** with the keyboard and <kbd>Esc</kbd> for it. What can be
undone (a selection forgotten, tags changed, a view saved or forgotten)
asks nothing and offers **Undo**. A page with nothing to show, still
reading, or not able to read says it in one block, with what to do: All
studies, Compare, Files (with **Try again** where the server did not
answer), the Problems tab and the Log.

**Go to anything, and the keys.** ⌘K (Ctrl+K) finds a page (with its key),
a study of the workspace, a file of the open study, an analysis, a tool of
the Viewer, Settings and each setting of its You, the scheme and the actions (a study
opens on the page shown, its Overview from a page of no study); ⌘P
(Ctrl+P) the open study's files alone. `@TRP23` (or `@A:23`) selects that
residue in the Viewer, `@` and part of a name or number lists the
residues it fits, and `:120` shows frame 120 of the trajectory there. ?
(anywhere but in a field) lists every key, the Viewer's with them:

| Key | |
|---|---|
| ⌘K | Go to anything |
| ⌘P | A file of the open study |
| ⌘J | The Agent beside the page |
| ⌘1 to ⌘5 | The study's Overview, Viewer, Analysis, Report and Files. In a browser tab ⌘1 to ⌘9 are the browser's own: there, ⌥1 to ⌥5 (Alt elsewhere) |
| ⌘B | Fold or show the sidebar |
| ⌘⌥B | Fold or show the side panel |
| ? | Every key |

Ctrl in place of ⌘ off a Mac.

**Compare** puts two studies, or two runs, side by side on one page:
each named (a run by its study and by what sets it apart, "chignolin,
seed 2") with its system and state and a way to open it, the settings in
which they differ, defaults filled in as `fastmdx diff` says them (a length
in ps under 1 ns), the
means each recorded (its analyses', then the thermodynamic means as the
run's Overview gives them) with the second's less the first's, marked
resolved only past twice their combined standard error, and each series
both recorded over time overlaid on production's clock, every chart on the
card in one unit (ps under 1 ns; not overlaid where one is on frame numbers
and the other on time), production's energies, temperature and density
among them. A thermodynamic quantity whose analysis ran and gave no mean is
said not determined, with why; where the analysis's record was not read,
its means are said not read, never taken from the energy file instead. Two
runs of a study of replicas (runs that differ only by seed, or started from
different models of an ensemble, `setup.model`, and said so) are repeats of
one measurement: each one's error is the spread of the replicas' means, or
the run's own where that is larger, as `batch/aggregate.py` gives a
replica's (`replica_error`); the columns give the error used, each
difference says whether it was the replicas' spread or the runs' own, and
the page says how many of the runs' means each spread was taken over ("from
2 of the 3 runs") where fewer than all were read. Where only the two
compared were read for a quantity, the spread of their two means is their
difference over the square root of 2, so the difference would equal its
error whatever they are: two repeats of one measurement are not said to
differ, and their spread is set against the errors the two estimated for
themselves as `batch/aggregate.py` sets replicas' (`spread_against_own`:
past twice those, the errors are too tight); the page says why, and says
"only these two runs' means were read" only where the study has more runs. Where the two also differ in a setting besides what makes them replicas,
the page names it from the Settings diff rather than saying they differ
only by that. A finished run its
study's manifest places outside the study is named and left out, not looked
for. Where one of the two did not finish, the spread is not its error: each
difference is set against the runs' own errors, and the page says why;
where the study's record of its runs was not read, whether they are
replicas is said not known. **Play them beside in the Viewer**
opens the first in the Viewer with the second played beside it (Compare
study, below). It opens from All studies (two chosen), from a study of
several runs (two of its runs ticked on its Overview) and from ⌘K
(**Compare with...**: the other runs of the study open, then the
workspace's studies). Its address names the two, so Back, Forward and a
reload keep them; **Swap** puts the second first. **Open** opens one as
every way to another study does, asked first whether the switch would lose
work here. Each of the two is read from its own records only: a record of
one linked out of it is not read and the page says so, naming it, and a
mean whose record was not read is said not read, never as one not recorded;
a series both recorded that was not read is named as not plotted, and a
run whose Manifest was not read is said in a state not known.

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
process in the folder (its number, the machine's boot and when the process
began, counted in the system's ticks since that boot); a stale record left
by a crash is checked against the live process before anything is believed,
and a process that is not FastMDXplora running that study is never adopted.
A process is the run only where its number and its start are the record's,
which a step of the clock does not change; a record written before the start
was recorded is judged by the arguments it recorded, and, where it has none,
a process begun since it was written is another one. Every page, the
workspace's list of runs going, adoption and Stop ask the one test, and Stop
asks it again before it signals: where the number has been given to another
process since, nothing is signalled and Stop says why.

**The settings**, from the gear at the sidebar's foot: the scheme
(System, which follows the computer, Light or Dark), the accent (Cyan, Blue,
Violet, Magenta or Graphite, saved on this computer; Graphite has no hue and
underlines its links), whose AI the Agent asks (its provider) and its mode
with **Agent settings**, where alone its AI model is said, **Settings**,
**Cite FastMDXplora**, the documentation and the repository. Agent settings
and Cite open as dialogs over the page shown; Escape, Close, Back or a click
outside closes them, and `#cite` in the address opens the citation. **Cite
FastMDXplora** gives the reference and its BibTeX, each copied with one
click.

**Settings** is one page for every setting (`#settings`, and `#preferences`,
its old address), each applied as it is changed, with no Save. One search
finds a setting by its words, and says beside each scope how many it found
there. Two scopes:

- **You** is this computer, saved in this browser's storage for the GUI's
  address: the scheme and the accent, as in the gear's menu; the **Display
  size**; how the Viewer opens (representation, a dark or white ground, water,
  ions, spin, the camera kept as frames change, the pocket cutoff), the time
  in 24 or 12 hours, how many samples a live chart keeps and a notice when a
  run ends; and the Agent's engine and mode, changed in its own settings
  (**Change the Agent's settings**; that dialog's **All settings** leads back
  here). The Agent's mode and GPU-hour ceiling are saved as they are chosen.
  **Restore defaults** puts the release's back for the Display size, the
  Viewer, time, charts and notices. Nothing in You changes a study or its
  files.
- **This folder** is the workspace's `fastmdx-defaults.yml` (the nearest in or
  above where new studies go, or a new one there), which every new study made
  here starts from, from the GUI, the command line and the Agent alike. Every
  setting it may hold is listed by its phase, FastMDXplora's own value beside
  it; a setting of one study (what it continues or is prepared from, the files
  it analyses, its structure, ligand, mutations, chains and model, or one that
  names its structure's parts: a ligand's charge, residues by number, a
  structure oriented by hand, a restraint or an analysis's option naming
  particular atoms, a bias chosen for one system, one study's title) is not
  one, and a file that sets one is refused. A value set is written into the file at
  once and checked as any defaults file is: one it refuses is said beside the
  setting, and the file is left as it was. **Use FastMDXplora's** takes it out
  again, a reason given is saved with it in the file's `decisions` (a study's
  report says it under **Why these settings**), and **Undo** puts back what
  was there. A setting no control draws (a stopping rule, the
  analyses' options), or a value its control cannot say (restraints
  written as blocks), is set only by hand in the file and shown under its
  words as the file holds it, and a file with comments is shown and changed
  by hand, as writing it again would lose them. Not offered when the GUI is served to others.

The **Display size** sizes everything at once, text included: Auto, 90%,
100%, 110%, 125% or 150%. Auto chooses from the screen as your browser
reports it, in its own pixels, so a system's own scaling is counted once:
100% up to about 1920 by 1080, 125% at 2560 by 1440 (or an ultrawide 3440 by
1440), 150% at 3840 by 2160. It chooses again when the window is moved to
another screen. Where your browser cannot size the page itself, Settings
says to use the browser's zoom. The page is laid out as a window of its
own width is: at 150%, a window 1500 pixels wide is laid out as one 1000
wide is at 100%. A Display size never makes the page narrower than a
phone's 390 pixels: the size applied is the largest not above the one
chosen that keeps it so, again as the window is resized (150% chosen is
125% in a window 500 wide and 100% in one 390 wide), and Settings says so
beside the select.

**Back and Forward** move between the GUI's pages, the analysis shown and
the Cite and Viewer help dialogs. The address names the open
study by a key, never its path (`?study=<key>`), so Back returns to the
study open before and a bookmark opens that study again; a folder not yet a
study is not named. An address naming a study not among those here says so,
and one followed from another site offers the study it names rather than
opening it. A tag, a note or a saved view changed says so in a notice with
**Undo**. In the Config Builder, Cmd+Z (Ctrl+Z) takes the form back one
change at a time and Shift+Cmd+Z (Ctrl+Y) forward again; typing in a field
is undone and redone in the field itself, as in any text field.

Green done, amber qualified and red refused mean the same in all three
schemes, and with every accent; a study running is shown in cyan with every
accent. `fastmdx gui --accent NAME` sets the accent the GUI opens with for
everyone it serves, and the reports of the studies it starts on this
machine take it; a person's own choice comes first.

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
  `fastmdxplora_config.yml`. Take it to a cluster and `fastmdx explore --config`
  runs the same study.
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
**check it** (syntax and every setting, without running anything, judged as
`fastmdx explore --config` judges it: a config with no systems does not run,
and your `fastmdx-defaults.yml` is filled in), **run it as-is**, or **open it
for editing**. It never rewrites the file you gave it.

In the Agent workspace, a Config version the Agent proposes lists each
changed setting, old value to new value, with **Accept** and **Reject**
beside **Use this version**. Additions, removals and changes to a
setting's recorded reason are listed too. Systems, sweeps and compound
settings such as a stopping rule are kept whole and said as the change
list says them. **Accept all** and **Reject all** fill the choices
without applying them. Equivalent names such as `prepared_from` and
`setup_from` share one choice and keep their reasons together. A version
with no changes, or one made from your own choices or from an earlier
version, offers no choices.

**Use this version** keeps the proposal as it is, as it does on an
earlier version: the conversation says "Use version N", and a new version
of the same Config says every change was accepted, with the labels and
values shown.
Once a change is rejected, **Use reviewed version** puts the chosen
combination through the existing Config validator and creates a new
version only when it passes; the changes not rejected stay as proposed.
A refused combination is shown with the changes it involves marked, and
the choices are kept so they can be corrected. While a rejected change
is not yet applied, use one of the two before sending another message or
running the study. The earlier versions stay intact, and the new version
holds only the settings reviewed, each in the spelling its version used.
The conversation records the choices with the labels and values shown.
Choices not yet applied are kept in the page's local storage, by conversation,
version and the changes themselves, and survive a reload; another
proposal given the same version number starts with no choices. They are
never saved as a Config. After
applying, Run, Download, Open in the builder and the next Agent message
use the reviewed version. Agent exports retain chosen settings and reasons
for inactive phases too; the full export adds the defaults for the phases
currently running.

**Open for editing** fills the page in from the file: every system with its
name and its own settings, `output`, the phases it runs, the four phase blocks,
the per-analysis options, the `sweep`, the `execution` block and the
`agent`/`agent_model`/`budget_hours`/`decisions` provenance. A config with no
systems yet opens too, to be given one. **Run it as-is** leaves the file
untouched.

With a `fastmdx-defaults.yml` where the study will run, each setting it gives
is marked **Your default** on its field, and the review's plan says the value
the run will take with it, marked as yours; where systems give their own
value, the plan names each with its systems ("310 K, 1 bar (plain); 320 K,
1 bar (warm)"). Every value typed into the form is kept in the file, yours
too, so the file run where there is no defaults file runs the study shown;
only FastMDXplora's own value, where yours say nothing else, goes unsaid.
The file is changed in **Settings**, under **This folder**. A file that is
refused (a setting of one study in it, or a value the schema refuses)
fills nothing: New study says why above the form, with the way to This
folder, and offers FastMDXplora's own values until it is put right. The
form keeps the defaults it was drawn with: where the file has changed
since (in This folder or put right), New study says so above the form,
where it is refused since, the refusal, and **Read the defaults again**
draws the form from the file as it is. Bound beyond loopback, the folder's
file is neither shown nor said, as This folder is not offered there: the
form offers FastMDXplora's own values, and the config handed back is drawn
from them. A full config gives your default only to the systems that take
it, and each run records your reason only where it took your value.

When you press **Run**, the Config is written into the output directory as
`exploration.yml` before anything starts (the full one where **Full config** is
ticked, your defaults named in it), so the run is repeatable from its own
output. **Run as-is** writes nothing and runs the file you named, unmodified,
into its own `output:` (a relative one read from the folder new studies go
in), or, where it names none, beside it as `<its name>_output`.

---

## Watching it happen

The strip across the main page keeps the studies opened in this browser
window, named as Recent names them: the system, and the folder too where
another study of the workspace, or another open one, has the same system.
The open study's tab is filled with a bar beneath it and kept in view; a
fade at an edge says more tabs lie past it. Select a study to return to its
last page or analysis section. **Open another study** opens All studies;
the existing folder picker can open a study elsewhere. The open list
survives reload for this workspace while browser session storage is
available; where the browser does not allow it, the strip says the open
studies are not kept. The strip is one stop for Tab: use Left/Right or
Home/End to move along it, Enter to open a study and Delete or its Close
button to close it. Long lists scroll within the strip, by a mouse wheel
too. Scrolled down, the strip and what it says stay at the top, with the
page's own header below it.

Closing an inactive study leaves the current one open. Closing the active
study opens its nearest neighbour; a neighbour found gone as it is tried is
marked so and the next is tried. With none left, All studies is shown, the
address no longer names the study, and a study found gone is named under
the strip. Closing stops no simulation and removes no files: a run in the
study closed goes on. If a switch is refused, the current study stays open
and the strip says why. Browser Back and Forward continue to work,
including reopening a closed study.

A study whose folder has gone is said so on its tab ("folder gone").
Choosing it asks again: it opens if its folder is back, and otherwise the
strip says its folder is no longer there. When the open study's folder
goes, its pages are not shown as an empty study: the page says the folder
is no longer there, and the address still names it.

Every way to another study the page offers (the strip, Recent, All
studies, Load study, a study's runs, the run going elsewhere, the demo, a
shared study, another study's conversation in the Agent's list, and Back
or Forward within the page) first saves the Builder's workspace draft. A
reply being written, a rejected Config change not applied (**Use reviewed
version**, or **Use this version** to keep the proposal) or a run launch
holds the switch, and so do an unsent Agent message or attachment, except
on Back or Forward; the strip says what needs attention. While one switch
is under way, the strip is busy and no second one is asked.

Unsent Agent words and attachments are kept for the study they were begun
in, in this window's session storage: the words, and of each attachment
its path, name, size and digest, read again when they are put back (a
file that cannot be read again, or changed since, is named beside the
box). Opened again by any way, Back included, that study's Agent box has
them again, after anything typed before the study was known; sending or
clearing them forgets them, and so does closing the study's tab or its
folder going. Where the browser does not keep them, the box says so, Back
within the page holds for them as the other ways do, and the browser asks
before the page is left (a Back to another page, a reload, an address
typed); so it does for a Config draft it could not keep.

The Agent shows and writes the conversation of the study the page is for:
read once the study the address names is open, and read again if the page
finds it showed another's. The server gives no conversation to a page for
another study than the one open (`GET /api/agent/conversation?study=`),
and refuses a write whose study is not the page's (`page_study`, 409).
When the study open is changed elsewhere (another window, an AI app), a
window says so, and unsent words typed there are kept for the study they
were typed for, not left in the new study's box. **Open it** on an address
from another site goes through the same hold as the other ways.

A Config draft this browser could not save holds the switch, and the
Builder's header says "Not saved in this browser": **Download config** or
**Reset** releases it while the form stays as downloaded or reset. Another
window's newer draft is kept: this window takes it where it changed nothing
since; otherwise the header says "Not saved: another window saved a newer
draft" and the switch is held until **Download config**, or a reload to
take the newer draft. Back or Forward to a page the browser loads for
another study is not held: the Agent words are kept as above, but this
window's Config changes held by another window's newer draft are not. The
Builder remains one draft for the workspace. The server's active study is
still shared by browser windows; these tabs do not provide independent
server sessions.

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
has run, and lists every round and what was decided on it. It says the study
running only where the study's process is known to be (its process record);
a record still saying it runs is otherwise "Not finished when this was
written", and a study whose run was killed "Stopped without finishing", with
every run killed named. The card reads the study's records and nothing else,
so it says what the report says.

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
learned from the studies here that completed with their runs one at a time on
the GPU chosen, read by what their own processes held, as on a workstation
([Other machines](remote.md#sending-a-study)); until one has, it is not known
and nothing is refused. Work on the CPU (a study on the CPU, an analysis or a
report written again, a fix), a study whose GPU is not chosen here (a
continuation, which runs on its study's GPU, or a config that names its GPUs in
a way not checked here), and every study on a computer whose GPUs `nvidia-smi`
does not read (a Mac), each waits for the other such work: the refusal names
the study running and who started it. No two runs ever write one folder: a
study whose folder a run is writing, in or around it, is refused, and so is a
continuation of a study still running, from any page
(`environment.workspace.run_going`). The rule is kept in the folder the GUI
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
| `--output DIR` | The run to watch or read; new studies go beside it. Without it, the GUI opens in "design a new study" mode, and the folder it was started in is where new studies go, where a relative path is read from and where your `fastmdx-defaults.yml` is looked for | current directory, home mode |
| `--host` | What to bind to | `127.0.0.1` |
| `--port` | Which port. If it is busy, the next free one is used and reported | `8765` |
| `--no-browser` | Print the URL instead of opening a tab | opens a tab |
| `--demo [DIR]` | Copy the demo study (trypsin with benzamidine, 3PTB, finished) into DIR and open it. It is not in the package: it is fetched the first time (about 11 MB), checked against the SHA-256 this release records, and kept in `~/.cache/fastmdxplora/demo` (or under `FASTMDXPLORA_CACHE_DIR`). **Open the demo study** on an empty Overview does the same. Not yet published for 2.6.0: `--demo` says so (`environment.demo.absent`) and the button is not shown | off |
| `--open SOURCE` | Open a shared study from its DOI, its Zenodo address or its zip, checked file by file before it opens; `--open-into DIR` says where it is unpacked and `--open-most-gb` how large an archive is allowed (2 GB). See [Sharing a study](sharing.md) | off |
| `--ligand-resname NAME` | Which residue the viewer treats as the ligand | auto-detected |
| `--binding-pocket-cutoff-A X` | How near counts as the pocket | `5.0` |
| `--accent NAME` | The accent for everyone it serves, and for the reports of the studies it starts on this machine: `cyan`, `blue`, `violet`, `magenta` or `graphite`. A person's own choice, in the settings, comes first | `FASTMDX_ACCENT` if set, else `cyan` |
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
| `/api/agent/memory`, read as well as written | It holds what the person told the Agent of themselves, and changes it; where hosted, it is the host's store for the one person served |

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
| `GET /api/studies?path=`, `/api/compared?a=&b=`, `/api/study-thumbnail?path=` | The studies under a folder as cards (each with its tags and note), the tags they carry, the most used first, two compared (for the Compare page, with each named and their series), and a card's figure; loopback only, as browsing folders is, and inside the workspace when hosted |
| `POST /api/study-tags` | A study's tags and note, `{"path", "tags", "note"}`, replacing those it had (without `note`, the note kept), kept in its `study_tags.json`; loopback only, and inside the workspace when hosted |
| `GET /api/residue-states?chain=A&resseq=57&resname=HIS` | A clicked residue's protonation states and this study's Config to start a new study from, the residue named as setup builds the structure |
| `GET /api/measure-over-frames?a=<selection>&b=<selection>` | The command that measures the distance between two atoms at every frame (`pair_distance`, into a folder of its own), each selection checked to name one atom |
| `GET /api/remote/machines`, `/api/remote/job?job=`, `/api/remote/fetch-sizes?job=` | Your machines as recorded at a terminal ([Other machines](remote.md)) and the jobs whose results come back into the workspace, nothing asked of any machine; a job's state, asked of its machine at most every 30 s; what a fetch of an ended job would bring, each part's size; loopback only, and not in a hosted GUI |
| `GET /api/stream` | Server-sent events: one `change` event each time the study's files or the run's state change, and nothing about what changed; the page then asks the routes here |
| `GET /api/stopping` | For a study run until it is determined: the rule, each quantity's error and mean after each round with the replicas' own means, where the error would reach the target at the rate it has fallen, and the piece now running with its time here |
| `GET /api/study-timeline` | What happened to the study on screen, newest first: each phase as it last ran, one replaced or set aside, each piece it was carried on in, with what each changed; loopback only |
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
| `POST /api/remote/plan` | What sending a Config in the workspace (`config`), or the Config Builder's study (`state`, saved first as `<results>.yml`), to one of your machines (`machine`) would do, sending nothing: what travels with each size, where it runs, the GPUs' room, the job script; with a token (`plan`) kept ten minutes. Only files in the Config's folder, inside the workspace, travel; loopback only, and not in a hosted GUI |
| `POST /api/remote/send` | Send the plan shown (`{"plan"}`), once, and only while what would travel is what was shown, each file's contents included; loopback only |
| `POST /api/remote/forget` | Let a plan go unsent (`{"plan"}`), taking back the config saved for it from the Config Builder where nothing else holds it and it is as written; loopback only |
| `POST /api/remote/fetch` | Bring an ended job's results (`job`, `with_trajectory`), only while the machine, asked again, says the size shown (`bringing`, in bytes); loopback only |
| `POST /api/remote/cancel` | Stop a job, as `fastmdx remote cancel` does; loopback only |
| `POST /api/report/write` | Write the report of the study open again, as `fastmdx report --output <study> --rerun` does; loopback only |
| `GET /api/again` | What can be run again on the study open and why not, the analyses it ran last and those this release has; loopback only |
| `POST /api/again` | Run the study open's analysis or report again (`phases`, `analyses`), by the phase command with `--rerun`; loopback only |
| `POST /api/agent/model` | Read or set the [Agent](agent.md)'s AI model choice. Never returns the key |
| `POST /api/agent/propose` | A sentence to a validated Config |
| `GET /api/agent/memory`, `POST /api/agent/memory` | What the [Agent remembers of you](agent.md#what-the-agent-remembers-of-you), and a change to it (`op`: `add`, `change`, `forget`, `undo`, `clear` or `switches`); `GET` with `reply` says what one reply changed, and whether it is still being read; loopback only, or the host's store where hosted |
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
