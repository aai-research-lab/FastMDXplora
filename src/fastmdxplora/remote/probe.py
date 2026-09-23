"""What a machine has, found by looking rather than by asking the user.

The inspection is one POSIX shell script sent over SSH and run with
``sh -s``. It reads and never writes: no directory is created, no package
installed, nothing left behind. Every answer is one line,
``fmdx:<key>=<value>``, so whatever else the shell prints (a banner, a
message of the day, a profile that echoes) is ignored rather than parsed.

Three things about how a machine is reached shape what the script does.

**A non-interactive shell does not see conda.** ``ssh host cmd`` starts a
shell that reads no startup files, and conda's ``init`` block lives in
``.bashrc``. So a machine with a working conda reports ``command not found``.
The script looks in the places installers put conda as well as on ``PATH``.

**Conda keeps environments in more than one place.** Where its base is
not the user's to write -- ``/opt/conda``, a site install -- ``conda create``
puts a new environment in ``~/.conda/envs`` instead, and ``~/.condarc`` may
name others. All of them are searched, and an environment counts because
it holds a ``fastmdx`` command, not because of what it is called.

**A version string does not say which code a checkout holds.** An editable
install keeps the string it was installed with. So each installation also
reports its commit, where it is a checkout, read the way the manifest reads
it.

**A cluster's login node usually has no GPU.** ``nvidia-smi`` is absent
there even when every compute node carries four. The partitions say what
the nodes hold, and the driver is known only once a job runs on one; the
record says so instead of reporting a machine without GPUs.

**The answer can be cut short.** A dropped connection or a killed shell
leaves a partial answer, so the script ends with a marker, and an answer
without it is refused rather than half-believed.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from fastmdxplora.remote.identity import CodeIdentity, same_code

__all__ = [
    "PROBE_SCRIPT",
    "Environment",
    "Gpu",
    "Inspection",
    "parse_inspection",
]

#: Where installers commonly put a conda. Checked because a non-interactive
#: shell does not read the ``.bashrc`` that puts conda on ``PATH``.
_CONDA_ROOTS = (
    "$HOME/miniforge3", "$HOME/mambaforge", "$HOME/miniconda3",
    "$HOME/anaconda3", "/opt/conda", "/opt/miniforge3",
)

#: Where a micromamba placed by ``fastmdx remote`` lives on the host.
HOST_HOME = "$HOME/.fastmdxplora"

#: The run's own marker lines. A prefix rather than bare ``key=value`` so a
#: profile that prints ``PATH=...`` cannot be read as an answer.
_PREFIX = "fmdx:"

PROBE_SCRIPT = r"""
say() { printf 'fmdx:%s=%s\n' "$1" "$2"; }
have() { command -v "$1" >/dev/null 2>&1; }

say probe 1
say host "$(hostname 2>/dev/null)"
say os "$(uname -s 2>/dev/null)"
say arch "$(uname -m 2>/dev/null)"
say home "$HOME"
say user "$(id -un 2>/dev/null)"
say cpus "$(getconf _NPROCESSORS_ONLN 2>/dev/null || nproc 2>/dev/null)"
if [ -r /proc/meminfo ]; then
  say memory_kb "$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)"
fi

if have nvidia-smi; then
  nvidia-smi --query-gpu=name,memory.total,driver_version \
      --format=csv,noheader,nounits 2>/dev/null |
    while IFS= read -r line; do say gpu "$line"; done
  say cuda_driver "$(nvidia-smi 2>/dev/null |
    sed -n 's/.*CUDA Version: *\([0-9][0-9.]*\).*/\1/p' | head -n 1)"
fi

for tool in mamba conda micromamba; do
  found=$(command -v "$tool" 2>/dev/null) && say "$tool" "$found"
done
[ -n "$CONDA_EXE" ] && [ -x "$CONDA_EXE" ] && say conda "$CONDA_EXE"
[ -x "__HOST_HOME__/bin/micromamba" ] && say micromamba "__HOST_HOME__/bin/micromamba"
for root in __CONDA_ROOTS__; do
  [ -x "$root/bin/conda" ] && say conda_root "$root"
done
[ -d "__HOST_HOME__/mamba" ] && say conda_root "__HOST_HOME__/mamba"

ident() {
  "$1" -c '
import fastmdxplora
version = getattr(fastmdxplora, "__version__", "")
commit = dirty = checkout = ""
try:
    from fastmdxplora.provenance import source_checkout, source_provenance
    record = source_provenance() or {}
    commit = record.get("commit") or ""
    if commit:
        dirty = {True: "yes", False: "no"}.get(record.get("dirty"), "unknown")
        checkout = str(source_checkout() or "")
except Exception:
    pass
print("|".join((version, commit, dirty, checkout)))
' 2>/dev/null
}

roots="__CONDA_ROOTS__ $HOME/micromamba __HOST_HOME__/mamba"
for tool in mamba conda micromamba; do
  found=$(command -v "$tool" 2>/dev/null) && roots="$roots ${found%/bin/*}"
done
[ -n "$CONDA_EXE" ] && roots="$roots ${CONDA_EXE%/bin/*}"
[ -n "$MAMBA_ROOT_PREFIX" ] && roots="$roots $MAMBA_ROOT_PREFIX"
places="$HOME/.conda/envs"
for root in $roots; do places="$places $root/envs"; done
if [ -r "$HOME/.condarc" ]; then
  for dir in $(awk '/^envs_dirs:/ {on = 1; next}
      on && /^[ \t]*-/ {sub(/^[ \t]*-[ \t]*/, ""); gsub(/"/, ""); print; next}
      on && /^[^ \t]/ {on = 0}' "$HOME/.condarc"); do
    case "$dir" in "~"*) dir="$HOME${dir#"~"}" ;; esac
    places="$places $dir"
  done
fi
[ -n "$CONDA_ENVS_PATH" ] && places="$places $(printf '%s' "$CONDA_ENVS_PATH" | tr ':' ' ')"
for place in $places; do
  for env in "$place"/*; do
    [ -x "$env/bin/fastmdx" ] && [ -x "$env/bin/python" ] || continue
    say environment "$env|$(ident "$env/bin/python")"
  done
done
found=$(command -v fastmdx 2>/dev/null) && {
  python=$(sed -n '1s/^#![ \t]*//p' "$found" 2>/dev/null | awk '{print $1}')
  if [ -n "$python" ] && [ -x "$python" ]; then
    say on_path "$found|$(ident "$python")"
  else
    say on_path "$found|$(fastmdx --version 2>/dev/null | awk '{print $2}')|||"
  fi
}

for tool in apptainer singularity; do
  found=$(command -v "$tool" 2>/dev/null) &&
    say container "$found|$("$tool" --version 2>/dev/null | head -n 1)"
done
for image in "__HOST_HOME__"/images/fastmdx-*.sif "$HOME"/fastmdx-*.sif \
             "$HOME"/*/fastmdx-*.sif "${SCRATCH:-/nonexistent}"/fastmdx-*.sif; do
  [ -f "$image" ] && say image "$image"
done

if have sbatch; then
  say sbatch "$(command -v sbatch)"
  have sinfo && sinfo -h -o '%P|%l|%G' 2>/dev/null |
    while IFS= read -r line; do say partition "$line"; done
fi

me=$(id -un 2>/dev/null)
for dir in "$SCRATCH" "/scratch/$me" "/scratch/users/$me"; do
  if [ -n "$dir" ] && [ -d "$dir" ] && [ -w "$dir" ]; then
    say scratch "$dir"
    say scratch_free_kb "$(df -Pk "$dir" 2>/dev/null | awk 'NR == 2 {print $4}')"
    break
  fi
done
say home_free_kb "$(df -Pk "$HOME" 2>/dev/null | awk 'NR == 2 {print $4}')"

url=https://conda.anaconda.org/conda-forge/noarch/repodata.json
if have curl; then
  if curl -sSfI -m 8 "$url" >/dev/null 2>&1; then say internet yes; else say internet no; fi
elif have wget; then
  if wget -q --spider -T 8 "$url" >/dev/null 2>&1; then say internet yes; else say internet no; fi
else
  say internet unknown
fi
have bzip2 && say bzip2 yes

say probe_end 1
""".replace("__CONDA_ROOTS__", " ".join(_CONDA_ROOTS)).replace(
    "__HOST_HOME__", HOST_HOME)


@dataclass(frozen=True)
class Gpu:
    """One GPU, as ``nvidia-smi`` names it."""

    name: str
    memory_mb: int | None
    driver: str


@dataclass(frozen=True)
class Environment:
    """An installation of FastMDXplora on the machine, and what it holds.

    ``path`` is a conda environment, the ``fastmdx`` command on PATH, or a
    release image. ``commit``, ``dirty`` and ``checkout`` are filled where the
    installation is a source checkout.
    """

    path: str
    version: str
    commit: str = ""
    dirty: str = ""  # "yes", "no" or "unknown"; empty for a release
    checkout: str = ""

    @property
    def identity(self) -> CodeIdentity:
        return CodeIdentity(
            version=self.version, commit=self.commit,
            dirty={"no": False, "yes": True}.get(self.dirty),
            checkout=self.checkout)


@dataclass
class Inspection:
    """What one machine was found to have."""

    host: str = ""
    os: str = ""
    arch: str = ""
    home: str = ""
    user: str = ""
    cpus: int | None = None
    memory_kb: int | None = None
    gpus: list[Gpu] = field(default_factory=list)
    cuda_driver: str = ""
    conda: dict[str, str] = field(default_factory=dict)
    conda_roots: list[str] = field(default_factory=list)
    environments: list[Environment] = field(default_factory=list)
    on_path: Environment | None = None
    container: str = ""
    container_version: str = ""
    images: list[str] = field(default_factory=list)
    sbatch: str = ""
    partitions: list[dict[str, str]] = field(default_factory=list)
    scratch: str = ""
    scratch_free_kb: int | None = None
    home_free_kb: int | None = None
    internet: str = "unknown"
    bzip2: bool = False

    @property
    def kind(self) -> str:
        """``slurm`` where jobs go through a scheduler, else ``workstation``."""
        return "slurm" if self.sbatch else "workstation"

    def installations(self) -> list[Environment]:
        """Every installation found: environments, PATH, release images.

        A release image is named for its version and built from a tag, so
        its name is its identity.
        """
        found = list(self.environments)
        if self.on_path is not None and all(
                env.path != self.on_path.path for env in found):
            found.append(self.on_path)
        for image in self.images:
            name = image.rsplit("/", 1)[-1]
            version = name[len("fastmdx-"):-len(".sif")]
            found.append(Environment(path=image, version=version))
        return found

    def holding(self, code: CodeIdentity) -> list[Environment]:
        """The installations holding exactly ``code``."""
        return [env for env in self.installations()
                if same_code(code, env.identity)[0]]

    def as_record(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> Inspection:
        fields = dict(record)
        fields["gpus"] = [Gpu(**g) for g in fields.get("gpus") or ()]
        fields["environments"] = [Environment(**e)
                                  for e in fields.get("environments") or ()]
        if fields.get("on_path"):
            fields["on_path"] = Environment(**fields["on_path"])
        known = cls.__dataclass_fields__
        return cls(**{k: v for k, v in fields.items() if k in known})


def _integer(text: str) -> int | None:
    try:
        return int(text.strip())
    except ValueError:
        return None


def _installation(value: str) -> Environment:
    """``path|version|commit|dirty|checkout``, with the tail optional."""
    parts = (value.split("|") + [""] * 5)[:5]
    return Environment(path=parts[0], version=parts[1], commit=parts[2],
                       dirty=parts[3], checkout=parts[4])


def parse_inspection(text: str) -> Inspection | None:
    """The inspection an answer describes, or ``None`` if it was cut short.

    Only lines carrying the probe's prefix are read, and an answer missing
    either the opening or the closing marker is not an answer.
    """
    pairs: list[tuple[str, str]] = []
    for line in text.splitlines():
        if not line.startswith(_PREFIX):
            continue
        key, sep, value = line[len(_PREFIX):].partition("=")
        if sep:
            pairs.append((key, value.strip()))
    keys = {key for key, _ in pairs}
    if "probe" not in keys or "probe_end" not in keys:
        return None

    found = Inspection()
    for key, value in pairs:
        if key in ("host", "os", "arch", "home", "user", "cuda_driver",
                   "scratch", "sbatch", "internet"):
            setattr(found, key, value)
        elif key in ("cpus", "memory_kb", "scratch_free_kb", "home_free_kb"):
            setattr(found, key, _integer(value))
        elif key == "gpu":
            parts = [p.strip() for p in value.split(",")]
            if parts and parts[0]:
                found.gpus.append(Gpu(
                    name=parts[0],
                    memory_mb=_integer(parts[1]) if len(parts) > 1 else None,
                    driver=parts[2] if len(parts) > 2 else ""))
        elif key in ("mamba", "conda", "micromamba"):
            found.conda.setdefault(key, value)
        elif key == "conda_root":
            if value not in found.conda_roots:
                found.conda_roots.append(value)
        elif key == "environment":
            env = _installation(value)
            if all(env.path != known.path for known in found.environments):
                found.environments.append(env)
        elif key == "on_path":
            found.on_path = _installation(value)
        elif key == "container":
            path, _, version = value.partition("|")
            if not found.container:
                found.container, found.container_version = path, version
        elif key == "image":
            if value not in found.images:
                found.images.append(value)
        elif key == "partition":
            name, _, rest = value.partition("|")
            limit, _, gres = rest.partition("|")
            found.partitions.append(
                {"name": name, "time_limit": limit, "gres": gres})
        elif key == "bzip2":
            found.bzip2 = value == "yes"
    if found.internet not in ("yes", "no"):
        found.internet = "unknown"
    return found
