# Other machines

A study is designed on a laptop and is best run on a GPU somewhere else: a lab
workstation, a cluster. `fastmdx remote` is how FastMDXplora learns about those
machines. It reaches them with your own `ssh`, so anything `ssh <name>` reaches
from your terminal, it reaches too.

What it does today is the first half of running a study elsewhere: finding out
what a machine has, and whether FastMDXplora is ready there or how it would be
installed. Sending a study, watching it and bringing the results back come
next. Until then, [Running FastMDXplora elsewhere](clusters.md) shows the same
thing done by hand.

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
  fastmdx       2.5.6 in /home/me/miniforge3/envs/fastmdx-2.5.6
  this computer 2.5.6

  ✓ Ready: 2.5.6, backends load.
```

The inspection only reads. It creates nothing, installs nothing and leaves no
file behind on the machine. It looks for:

- the CPUs, memory and GPUs, and the newest CUDA the GPU driver supports;
- conda, mamba or micromamba, including where installers put them but a
  non-interactive shell does not see them;
- environments named `fastmdx-*`, and which FastMDXplora version each holds;
- Apptainer, and any `fastmdx-<version>.sif` release image;
- SLURM and its partitions, a scratch directory, free space, and whether
  conda-forge can be reached.

Where it finds the version running on this computer, it asks that installation
what it can load, with `fastmdx info --json`, the same answer `fastmdx info`
gives a person there.

**A machine is ready when it holds exactly this computer's version and that
installation loads OpenMM, PDBFixer and the ligand stack.** Exactly, because two
versions can resolve a study's defaults differently, and a run that resolved
differently from the Config it was given is not a run of that Config.

### On a cluster

A login node usually has no GPU, so the inspection reports none there and lists
what the partitions offer instead. The driver's CUDA is known only once a job
runs on a GPU node.

### Signing in

Everything `~/.ssh/config` says applies: `ProxyJump` through a login node, keys,
an agent, certificates. Where a cluster asks for a password or a second factor,
you are asked once: the first connection is kept open for ten minutes and later
commands reuse it. From a script, with no terminal to answer, a connection that
needs a password fails at once instead of waiting.

---

## When it is not ready

If the machine holds the right version and cannot load something, the
inspection names what is missing and the conda command for each.

If it does not hold the right version, it prints an **install plan**: the exact
commands, and which computer each runs on. Run them yourself, then inspect the
machine again to check. Everything installs from conda-forge, where the
`fastmdxplora` package brings OpenMM, PDBFixer, the OpenFF toolkit, PLUMED and
the rest with it. The route depends on what the machine has:

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

**A development build has no plan.** conda-forge carries releases only, so if
this computer runs something like `2.5.7.dev121+gc07cf6190`, install the same
commit on the machine by hand.

---

## Machines you have inspected

```bash
fastmdx remote
```

lists every machine inspected so far and whether each is ready for this
computer's version. It connects to nothing: the list is as each machine was
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
Windows), or under `FASTMDXPLORA_CONFIG_DIR` where that is set.
