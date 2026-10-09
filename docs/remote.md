# Other machines

A study is designed on a laptop and is best run on a GPU somewhere else: a lab
workstation, a cluster. `fastmdx remote` is how FastMDXplora learns about those
machines. It reaches them with your own `ssh`, so anything `ssh <name>` reaches
from your terminal, it reaches too.

It finds out what a machine has and whether FastMDXplora is ready there,
installs it where you agree to, sends a study's Config to run, watches it, and
brings the results back:

```bash
fastmdx remote --machine gpu-box              # inspect: is it ready?
fastmdx remote install --machine gpu-box      # if not, and you agree
fastmdx remote send -c study.yml --machine gpu-box
fastmdx remote status
fastmdx remote fetch <job>
```

The machine runs the ordinary `fastmdx explore -c` on the Config, so a study
sent there is the same study as one run by hand as in
[Running FastMDXplora elsewhere](clusters.md).

---

## Inspecting a machine

Name the machine the way `ssh` knows it, as an alias from `~/.ssh/config` or as
`user@host`:

```bash
fastmdx remote --machine gpu-box
```

```
Inspecting gpu-box over ssh...

gpu-box   workstation
  host          aailab01 (Linux x86_64), 32 CPUs, 125 GB
  GPUs          1x NVIDIA L40S (driver 565.57.01, CUDA up to 12.7)
  conda         /home/me/miniforge3/bin/mamba
  apptainer     not found
  scheduler     none
  scratch       not found
  home          412 GB free
  internet      yes
  fastmdx       /home/me/miniforge3/envs/fastmdx-2.5.6: release 2.5.6
  this computer release 2.5.6

  ✓ Ready: /home/me/miniforge3/envs/fastmdx-2.5.6 holds this code and its backends load.
```

The inspection only reads. It creates nothing, installs nothing and leaves no
file behind on the machine. It looks for:

- the CPUs, memory and GPUs, and the newest CUDA the GPU driver supports;
- conda, mamba or micromamba, including where installers put them but a
  non-interactive shell does not see them;
- every conda environment holding a `fastmdx` command, whatever it is called,
  in the base's `envs`, in `~/.conda/envs` (where conda puts yours when the
  base is not yours to write, as with `/opt/conda`) and in any `envs_dirs` in
  `~/.condarc`; and which code each holds;
- Apptainer, and any `fastmdx-<version>.sif` release image;
- SLURM and its partitions, a scratch directory, free space, and whether
  conda-forge can be reached.

Where an installation holds the code running on this computer, it is asked what
it can load, with `fastmdx info --json`, the same answer `fastmdx info` gives a
person there.

**A machine is ready when an installation there holds exactly this computer's
code and loads OpenMM, PDBFixer and the ligand stack.** Exactly, because two
versions can resolve a study's defaults differently, and a run that resolved
differently from the Config it was given is not a run of that Config.

### Which code, for a source checkout

A release is known by its version. A source checkout is known by its **commit**,
because the version string of an editable install is written when it is
installed and stays that way as the checkout moves on: a checkout can report
`2.5.6.dev172+g64f17c43b` while it runs a commit two hundred later. So two
checkouts hold the same code when their commits match and neither has
uncommitted changes. With uncommitted changes the commit does not describe the
code, and nothing elsewhere can be shown to hold it; commit first.

### On a cluster

A login node usually has no GPU, so the inspection reports none there and lists
what the partitions offer instead. The driver's CUDA is known only once a job
runs on a GPU node.

### Signing in

Everything `~/.ssh/config` says applies: `ProxyJump` through a login node, keys,
an agent, certificates. Where a cluster asks for a password or a second factor,
you are asked once: the first connection is kept open for ten minutes and later
commands reuse it. From a script, an AI app or the Python API, with no
terminal to answer, a connection that needs a password fails at once instead of
waiting; sign in with `fastmdx remote --machine <name>` at a terminal first,
and they use the connection it keeps open for the next ten minutes.

---

## When it is not ready

If the machine holds the right version and cannot load something, the
inspection names what is missing and the conda command for each.

If it does not hold the right version, it prints an **install plan**: the exact
commands, and which computer each runs on. Run them yourself, then inspect the
machine again to check. Everything installs from conda-forge, where the
`fastmdxplora` package brings OpenMM, PDBFixer, the OpenFF toolkit, AmberTools,
PLUMED and the rest with it. The route depends on what the machine has:

| The machine has | The plan |
|---|---|
| conda, mamba or micromamba, and internet | A new environment, `fastmdx-<version>` |
| internet, and no conda | One micromamba binary under `~/.fastmdxplora`, then the same environment |
| Apptainer, and no internet | The release image, downloaded on this computer and copied across |
| neither internet nor Apptainer | No route yet; the inspection says so |

```
Install plan (conda, inside your account only):
  [on gpu-box]
    /home/me/miniforge3/bin/mamba create -y -n fastmdx-2.5.6 -c conda-forge "fastmdxplora=2.5.6" "cuda-version=12.6"
  then check it:
    /home/me/miniforge3/bin/mamba run -n fastmdx-2.5.6 fastmdx info --json
  cuda-version 12.6, because the driver supports CUDA up to 12.7, and a newer driver runs an older build.
```

**`cuda-version` is always pinned**, to 12.6 or to the driver's own ceiling if
that is lower. Left free, conda takes the newest CUDA, and a build newer than
the driver fails with `CUDA_ERROR_UNSUPPORTED_PTX_VERSION` when the first
kernel loads, after setup has already succeeded.

Nothing in a plan goes outside your own account: no `sudo`, no edits to your
shell's startup files, no shared environments. Each version gets its own
environment, so a newer one never replaces the one an older study ran on.

**A source checkout is brought to the same commit with git.** conda-forge
carries releases only, so where this computer runs a checkout, the plan finds a
checkout on the machine and moves it:

```
To bring it to this computer's commit:
  [on aailab01]
    git -C /home/me/FastMDXplora fetch origin
  [on aailab01]
    git -C /home/me/FastMDXplora merge --ff-only eae609edbbf9
  then check it:
    /home/me/.conda/envs/fastmdx-gpu/bin/fastmdx info --json
```

The commit has to be on `origin` for the fetch to find it, so push first.
`--ff-only` refuses rather than mix in commits of the machine's own. Where the
machine holds no checkout, the inspection says to clone one there.

---

## Installing, with your confirmation

```bash
fastmdx remote install --machine gpu-box
```

inspects the machine again, shows the same plan `--machine` prints, and asks
`Install now? [y/N]`. Nothing runs without a yes, there is no flag to skip the
question, and with no terminal to ask at, nothing runs at all. Each step is
printed as it runs; the first that fails stops the install and is named, and
what the steps before it did is left in place. At the end the machine is
inspected again and said to be ready or not.

Where there is no terminal, a program that shows the plan in a window of its
own (the Python API's `install_plan` and `install`) takes the person's yes to
that plan: bound to the machine and to every command in it, used once, and
gone after ten minutes. The machine is inspected again before anything runs,
and if the plan it gives is not the plan shown, nothing runs. An AI app and the
Agent are never offered an install; they name this command instead.

Where an installation already holds this computer's code and cannot load
something, the plan adds just that to the same environment:

```
To add them to that environment:
  [on gpu-box]
    /opt/conda/bin/mamba install -y -p /home/me/.conda/envs/fastmdx-gpu -c conda-forge openmm cuda-version=12.6
```

---

## Sending a study

```bash
fastmdx remote send -c study.yml --machine gpu-box
```

**The Config is checked here first,** by the same reading `explore -c` gives
it, so a refusal costs seconds rather than a copy and a wait. The machine is
probed again, and **nothing is sent to a machine that is not ready** for this
computer's code.

### What travels

**The files the Config names travel with it.** Every setting naming a file or
folder that exists, relative to the Config's own folder, is copied under
`inputs/` and the copy of the Config names it there: the structure, a ligand's
SDF, force field XMLs, a trajectory, a prepared study to continue from. A
structure given by PDB ID is fetched by the machine, which is refused where the
machine has no internet.

**Only files in the study's own folder travel,** the folder holding the
Config. A file named outside it is refused (`remote.input.outside`) and nothing
is sent, whoever wrote the Config: one written by an AI model naming
`~/.ssh/id_ed25519` would otherwise copy the key to the machine. Links are
resolved first, so a link in the folder leading out of it is outside too, and a
folder that travels is refused if a link in it, or in a folder one of its links
leads to, leads out of it or back into itself, since the copy follows links.
Copy the file into the study's folder and name it there. A Config in your home
folder, or at the top of the file system, is refused, since its folder is what
travels; and nothing in a place keys and credentials are kept (`.ssh`,
`.gnupg`, `.aws`, `.kube` and the like, and FastMDXplora's own settings) is
ever sent: a folder that travels is refused if one is anywhere in it, reached
directly or through a link. The link and key checks are made again as the copy
starts.

**Your defaults travel in the Config.** A `fastmdx-defaults.yml` beside the
Config or above it fills what the Config leaves unset here, as `explore -c`
would on this computer, and the plan says which settings it filled; the run
there is started with `--no-defaults`, so no defaults file on the machine (one
another user could have left up its folders) is laid over it.

The one exception is a prepared study named by `simulation.setup_from` or
`simulation.resume_from`, which usually sits beside the study's folder: it
travels from anywhere in the folder holding the study's folder when it holds a
`manifest.json` FastMDXplora wrote (for `setup_from` naming a study's `setup`
folder, the study's), with every link in it held to that study's folder. A
checkpoint file named by `resume_from` outside the folder is refused; name the
study instead.

`--dry-run` shows all of it, including the job script, and sends nothing:

```
Sending lysozyme to gpu-box
  runs in      /home/me/.conda/envs/fastmdx-gpu (checkout at 147781af14c2)
  folder       /home/me/fastmdxplora-jobs/lysozyme
  scheduler    a detached process
  results to   /Users/me/runs/lysozyme (with fetch)
  inputs       /Users/me/runs/181L.pdb as inputs/181L.pdb (238 kB)
  GPU 0 (NVIDIA GeForce RTX 4090): 23,900 MB free of 24,564 MB, 0% busy
  Runs on GPU 0.
  One run needs about 1,840 MB (from 3 runs in mixed precision measured on gpu-box, for about 31,412 particles).

job.sh:
  #!/bin/sh
  cd /home/me/fastmdxplora-jobs/lysozyme || exit 1
  export PATH=/home/me/.conda/envs/fastmdx-gpu/bin:"$PATH"
  export CUDA_VISIBLE_DEVICES=GPU-6f1c2e0a-93b4-4c1d-8e2f-5a7b9c0d1e2f
  free=$(nvidia-smi --query-gpu=memory.free ... -i GPU-6f1c2e0a-93b4-4c1d-8e2f-5a7b9c0d1e2f ...)
  ... (refused with exit 75 and a no_room file where less than 1,840 MB is free)
  fmdx_peak() { ... }      (reads the job's GPU memory every 15 s into gpu_peak)
  fmdx_peak $$ &
  /home/me/.conda/envs/fastmdx-gpu/bin/fastmdx explore -c study.yml --output run --no-defaults
  echo $? > exit_code
```

The job's **name** is its output folder's name, the same on both computers:
`--output`, else the Config's `output`, else a new study folder. Its folder on
the machine is `fastmdxplora-jobs/<name>` in scratch where there is one, else
in home, and the run is written to `run/` inside it.

**On a workstation** the job runs as a detached process in its own process
group, so it outlives the connection and can be stopped whole. **On a
cluster** it is an `sbatch` job asking for one GPU and not to be requeued (run
again after a preemption, it would find its own run folder and stop: it ends
instead, read as failed: `preempted` where the cluster still says so, else no
longer in its queue); `--partition` and `--time` are passed to it, and without
`--time` the partition's default applies. Either way the job writes its exit
code beside itself when it ends, so a finished run and a killed one are told
apart; a job sent again keeps the last one's until the cluster takes the new
one.

`--force-overwrite` replaces a job of the same name, here and on the machine,
once it has ended; one still waiting or running there is refused, on a
cluster too, so cancel it first.

**Studies share a workstation's GPUs, where they fit.** A send asks the
workstation's GPUs (`nvidia-smi`) how much memory each has free, and the
study goes to one it fits on: the one with the fewest studies from here,
then the most free memory. It is pinned there by the GPU's UUID
(`CUDA_VISIBLE_DEVICES`). The plan says each GPU's free memory and how busy
it is, and the jobs sent from here running there: a study sharing a GPU runs
slower than alone. A study that needs more memory than the GPU has free is
refused, with the numbers (`remote.machine.no_room`); `--dry-run` still
shows the plan, with a line saying so. The send asks again just before the
copy, holding a lock for the machine until the job is recorded, so two sends
at once do not both take the room for one; a GPU that no longer has room, or
GPUs that do not answer, refuse the send, naming another GPU with room where
there is one. The job script asks once more as the run starts, ending with
exit code 75 and a line saying why where the room has gone.

What a run needs is learned from the runs on that machine. Each job's script
reads its processes' GPU memory every 15 s, and once a job that ran one run
at a time on the GPU chosen for it has ended done, the most it held is kept
with the particles its runs had and their precision
(`gpu_memory/<machine>.json` in the settings folder; only the runs of that
send, never one an earlier send of the same name left). Much of a small
run's memory is CUDA's own and the same at any size, so a size is never
scaled from another by particles alone. For a new study, from runs in its
precision: no less than a run of its size or smaller held, and no more than
one of its size or larger held; from two sizes or more whose memory grows
with size, a straight line through them, followed past the largest size by
as far again as the sizes measured span; then 15% more. The particles are
those of the prepared system a run starts from (`setup_from`), else
estimated from each run's structure file and setup, as the builder's preview
estimates them, sweeps included. The need is not known where no run in that
precision has finished there, where the study is larger than the runs
measured can say (past the one size measured, or past the line's reach), or
where its size cannot be worked out here (a PDB identifier, a file other
than PDB, a membrane, more than 20 kinds of run): the plan says which, a GPU
is chosen as above, and nothing is refused. A study sent from here that does not yet
hold what it was expected to need (setup takes minutes) has the difference
kept back for it, on each GPU its share.

Runs side by side count: a study run in parallel needs room for as many runs
at once as the explorer starts (its `workers`, else one per device listed,
else the machine's cores, up to the number of runs), and is not learned
from; the runs on each GPU are counted as the explorer places them. A config
that names its own GPUs (`simulation.device_index` in the study, a system or
a sweep, or `execution.devices`) keeps them, nothing pinned, and each is
checked for room for the runs on it, by its number as `nvidia-smi` gives it;
where the machine's GPUs are not all alike, CUDA may number them otherwise,
so their memory is not checked and the plan says so. A continuation
(`simulation.resume_from`) runs on the GPU its study's record names, so none
is chosen or checked. A study that runs no simulation (setup and analysis
take no GPU), or runs it on the `CPU` or `HIP` platform, is not checked, nor
is a machine without `nvidia-smi`, which the plan says. A cluster's
scheduler gives each job its GPU, so nothing is asked there.

---

## Watching, fetching and stopping

```bash
fastmdx remote status            # every job not yet finished
fastmdx remote status lysozyme   # one job
```

asks the machine there and then and prints the state, with progress from the run's own live
status while it runs, and the last lines of its log. The states are the
queue's: `ready` (waiting for a GPU), `running`, `done`, `failed` with the exit
code or the reason, and `abandoned`. A cluster's queue that does not answer
leaves a job's state as it was; one that no longer knows the job, where the
cluster keeps no accounting to say how it ended, reads it as `failed`, its
log saying more. An AI app and the Python API are
answered from the last answer while it is under 30 s old, so asking in a loop
does not reach the machine each time.

```bash
fastmdx remote fetch lysozyme
```

copies a finished job's run folder back to the job's output folder here (a job
still waiting or running is refused, since it is still writing), with the last
MiB of the machine's job log as `remote_job.log`, ready to open with `fastmdx gui
--output`.
Trajectories and checkpoints stay on the machine unless `--with-trajectory` is
given; fetch says how many it left and where. It also reads the run's manifest
and says so if the code that ran is not the code that sent it. A link left in
the run on the machine is not copied, and anything else neither a file nor a
folder is taken out (said), each folder that came back is given its owner's
read, write and search, set-id bits are cleared and your file-creation mask
applied, and the run's own records of the process it ran as are left out (here
they would name a process on this computer), as is any `fastmdx-defaults.yml`
(said: here it would fill the settings of studies made below it). What was in the folder before the
fetch is left as it was, apart from entries of the same name as the run's. The
copy goes into a folder of its own inside the results folder
(`.fetching-<job>`, which only you can enter), and is moved into place only
once all of it has been looked over; only what differs from what is already
there is copied, and a copy that fails is kept there, so fetching again goes on
from it. So nothing fetch writes or reads here afterwards leads out of the
job's folder. Fetching from a machine you do not fully trust wants rsync 3.4.0
or later here, which closes the ways a hostile rsync at the other end could
read or write past the folder (CVE-2024-12084 to 12088). An AI app's fetch is said in bytes first, and a
file larger than the whole it said stays on the machine, named.

A study sent with a prepared system (`simulation.setup_from`) names it there as
`inputs/<name>`. Fetch writes `fetched.json` beside the results, recording the
folder here each input was sent from, so re-analysis and the report read the
prepared system's setup record from where it is on this computer. The run's
record carries the SHA-256 of that system's `system.xml`, so a folder prepared
again since is refused rather than read, and fetch names any run whose
prepared system it cannot find here.

```bash
fastmdx remote cancel lysozyme
```

stops the job, `scancel` on a cluster or the whole process group on a
workstation. Its folder on the machine is left as it is. A job the machine says
has ended is not signalled and keeps how it ended; a cancel the cluster does not
take is refused, and the job is asked about as before. A cluster's job last
read as failed whose queue does not answer now is neither signalled nor fetched
(`remote.job.cancel_not_taken`, `remote.job.unfinished`): nothing says whether
it is still going. A job waiting in the queue says why (its reason), a hold
above all, which ends only by hand.

---

## Machines you have inspected

```bash
fastmdx remote
```

lists every machine inspected so far and whether each is ready for this
computer's code, and every job sent from here. It connects to nothing: the list is as each machine was
last inspected, so inspect one again after changing it.

```bash
fastmdx remote forget gpu-box
```

removes the record. Nothing on the machine is touched.

---

## Where the records live

A study's Config never names a machine. `gpu-box` is an alias in your own
`~/.ssh/config` and means nothing in anyone else's, so a Config that named it
would stop being something that runs anywhere.

The records are kept with your other settings, one file per machine:
`~/.config/fastmdxplora/machines/<name>.json` (`%APPDATA%\fastmdxplora` on
Windows), or under `FASTMDXPLORA_CONFIG_DIR` where that is set. Beside them,
`gpu_memory/<name>.json` keeps the GPU memory the last 20 runs there held,
with their particles, which a study's need is worked out from.
