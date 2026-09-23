"""How to put FastMDXplora on a machine that does not have it yet.

The plan is written once and used two ways: printed, for someone who would
rather run it themselves, and (in a later step) run by ``fastmdx remote
install`` after they approve it. One function writes both, so the commands a
person copies are the commands the software would run.

Everything installs from conda-forge, because that is the distribution that
carries the whole stack: OpenMM, PDBFixer, the OpenFF toolkit, PLUMED and
WeasyPrint arrive as dependencies of ``fastmdxplora`` there, and several of
them exist nowhere else. The routes differ only in how conda-forge reaches
the machine:

``conda``
    A conda, mamba or micromamba is already there, and so is the internet.
``micromamba``
    Neither is there, the internet is. One micromamba binary is placed
    under ``~/.fastmdxplora`` and makes the same environment.
``image``
    No internet, and Apptainer is there. The release's image, which is the
    same conda-forge stack solved once into one file, is fetched on this
    computer and copied across.
``checkout``
    This computer runs a source checkout rather than a release. conda-forge
    cannot supply that, so the plan brings a checkout already on the
    machine to the same commit, with git, and nothing else.

A machine offline without Apptainer has no route yet, and the plan says so
rather than improvising one.

Nothing is installed outside the user's own space: no ``sudo``, no shell
startup files (micromamba is never ``shell init``-ed), no shared
environments, no module files.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fastmdxplora.remote.identity import CodeIdentity
from fastmdxplora.remote.probe import HOST_HOME, Inspection

__all__ = [
    "DEFAULT_CUDA_VERSION",
    "InstallPlan",
    "Step",
    "cuda_pin",
    "environment_name",
    "install_plan",
    "is_release",
]

#: The CUDA every conda-forge package in the solve is built against, unless
#: the driver supports less. The same default as ``container/fastmdx.def``
#: and the ``container`` workflow, and for the reason written there: left
#: free, the solver takes the newest CUDA, and a build newer than the driver
#: fails with CUDA_ERROR_UNSUPPORTED_PTX_VERSION when the first kernel loads,
#: after setup has succeeded. Pinned low, because a newer driver runs an
#: older build and the reverse is what fails.
DEFAULT_CUDA_VERSION = "12.6"

#: Where the release images are attached, one per tagged version.
RELEASE_IMAGE_URL = ("https://github.com/aai-research-lab/FastMDXplora/"
                     "releases/download/v{version}/fastmdx-{version}.sif")

#: Where micromamba is fetched from, by conda platform.
MICROMAMBA_URL = "https://micro.mamba.pm/api/micromamba/{platform}/latest"

#: Rough space an install needs, in GB, for a warning rather than a refusal.
#: A solved environment with OpenMM, the CUDA libraries and the OpenFF stack
#: runs to several gigabytes; the release image is about 1.3.
ENVIRONMENT_GB = 8
IMAGE_GB = 2

#: The directory ``remote`` places things in on a machine, and the root its
#: own micromamba keeps environments in, as they end a resolved path.
_OWN_HOME = "/.fastmdxplora"
_OWN_ROOT = "/.fastmdxplora/mamba"

_CONDA_PLATFORM = {
    ("Linux", "x86_64"): "linux-64",
    ("Linux", "aarch64"): "linux-aarch64",
    ("Darwin", "arm64"): "osx-arm64",
    ("Darwin", "x86_64"): "osx-64",
}


@dataclass(frozen=True)
class Step:
    """One command, and which computer it runs on."""

    where: str  # "here" (this computer) or "host"
    command: str


@dataclass
class InstallPlan:
    """What would put ``version`` on the machine, or why nothing can."""

    version: str
    route: str = ""
    steps: list[Step] = field(default_factory=list)
    check: Step | None = None
    notes: list[str] = field(default_factory=list)
    blocked: str = ""

    @property
    def possible(self) -> bool:
        return bool(self.steps) and not self.blocked


def is_release(version: str) -> bool:
    """Whether conda-forge can have this version: a tag, not a dev build."""
    return bool(version) and "dev" not in version and "+" not in version


def environment_name(version: str) -> str:
    """One environment per version, so a study's version stays reachable."""
    return f"fastmdx-{version}"


def _version_tuple(text: str) -> tuple[int, ...]:
    parts: list[int] = []
    for piece in text.strip().split("."):
        if not piece.isdigit():
            break
        parts.append(int(piece))
    return tuple(parts)


def cuda_pin(inspection: Inspection) -> tuple[str, str]:
    """The ``cuda-version`` to pin, and why that one.

    The default, unless the driver the machine reports supports less, in
    which case the driver's own ceiling. Where no driver is visible -- a
    login node, a machine without a GPU -- the default stands and the
    reason says it could not be checked here.
    """
    default = _version_tuple(DEFAULT_CUDA_VERSION)
    driver = _version_tuple(inspection.cuda_driver)
    if not driver:
        if inspection.kind == "slurm":
            why = ("the GPUs are on the compute nodes, so the driver is not "
                   "visible from here")
        else:
            why = "no NVIDIA driver was found on this machine"
        return DEFAULT_CUDA_VERSION, why
    if driver[:2] < default[:2]:
        pinned = ".".join(str(n) for n in driver[:2])
        return pinned, (f"the driver supports CUDA up to "
                        f"{inspection.cuda_driver}")
    return DEFAULT_CUDA_VERSION, (f"the driver supports CUDA up to "
                                  f"{inspection.cuda_driver}, and a newer "
                                  f"driver runs an older build")


def _space_note(free_kb: int | None, needed_gb: int, where: str) -> str:
    if free_kb is None or free_kb >= needed_gb * 1024 * 1024:
        return ""
    return (f"{where} has {free_kb / 1024 / 1024:.1f} GB free and this "
            f"needs about {needed_gb} GB.")


def _conda_executable(inspection: Inspection) -> str:
    """The conda-like command to create environments with, best first."""
    for tool in ("mamba", "micromamba", "conda"):
        if tool in inspection.conda:
            return inspection.conda[tool]
    for root in inspection.conda_roots:
        if not root.endswith(_OWN_ROOT):
            return f"{root}/bin/conda"
    return ""


def _checkout_plan(inspection: Inspection, code: CodeIdentity,
                   machine: str) -> InstallPlan:
    """Bring a checkout on the machine to this computer's commit."""
    plan = InstallPlan(version=code.commit)
    if code.dirty is not False:
        plan.blocked = (
            f"This computer's checkout at {code.commit} has uncommitted "
            "changes, so no installation elsewhere can be shown to hold the "
            "same code. Commit them, then inspect again.")
        return plan
    checkouts = [env for env in inspection.installations()
                 if env.commit and env.checkout]
    if not checkouts:
        plan.blocked = (
            f"This computer runs a source checkout at {code.commit}, and "
            f"conda-forge carries releases only. {machine} holds no checkout "
            "to bring to that commit. Clone the repository there, install it "
            "into a conda environment that has the release's stack, and "
            "inspect again.")
        return plan
    env = checkouts[0]
    plan.route = "checkout"
    plan.steps += [
        Step("host", f"git -C {env.checkout} fetch origin"),
        Step("host", f"git -C {env.checkout} merge --ff-only {code.commit}"),
    ]
    plan.check = Step("host", f"{env.path}/bin/fastmdx info --json")
    plan.notes.append(
        f"{env.path} is installed from the checkout at {env.checkout}, "
        f"now at {env.commit}. The fetch finds {code.commit} only once it "
        "is on origin, so push it from this computer first if it is not.")
    if env.dirty != "no":
        plan.notes.append(
            f"{env.checkout} has uncommitted changes. Commit or discard "
            "them: until then its commit does not describe its code.")
    plan.notes.append(
        "--ff-only refuses rather than mixing in commits of its own. If "
        "the dependencies changed between the two commits, update the "
        "environment as well.")
    return plan


def install_plan(inspection: Inspection, code: CodeIdentity,
                 machine: str) -> InstallPlan:
    """The plan that would put exactly ``code`` on ``machine``."""
    if code.is_checkout:
        return _checkout_plan(inspection, code, machine)

    version = code.version
    plan = InstallPlan(version=version)
    name = environment_name(version)
    if not is_release(version):
        plan.blocked = (
            f"This computer runs {version}, which is not a release, and "
            "conda-forge carries releases only.")
        return plan

    pin, why = cuda_pin(inspection)
    spec = f'"fastmdxplora={version}" "cuda-version={pin}"'
    plan.notes.append(f"cuda-version {pin}, because {why}.")
    online = inspection.internet == "yes"

    conda = _conda_executable(inspection)
    if conda and online:
        plan.route = "conda"
        # The micromamba this places keeps its environments in its own root,
        # which is where the inspection looks for them.
        root = (f" -r {conda[:-len('/bin/micromamba')]}/mamba"
                if conda.endswith(f"{_OWN_HOME}/bin/micromamba") else "")
        plan.steps.append(Step(
            "host", f"{conda} create -y{root} -n {name} -c conda-forge {spec}"))
        plan.check = Step("host", f"{conda} run{root} -n {name} "
                                  "fastmdx info --json")
        note = _space_note(inspection.home_free_kb, ENVIRONMENT_GB,
                           "The home directory")
        if note:
            plan.notes.append(note)
        return plan

    if online:
        platform = _CONDA_PLATFORM.get((inspection.os, inspection.arch))
        if platform is None:
            plan.blocked = (f"No micromamba is published for "
                            f"{inspection.os} {inspection.arch}.")
            return plan
        if not inspection.bzip2:
            plan.blocked = ("The machine has no bzip2, which unpacking "
                            "micromamba needs. Install conda there, or bzip2.")
            return plan
        plan.route = "micromamba"
        binary = f"{HOST_HOME}/bin/micromamba"
        root = f"{HOST_HOME}/mamba"
        url = MICROMAMBA_URL.format(platform=platform)
        plan.steps += [
            Step("host", f"mkdir -p {HOST_HOME}"),
            Step("host", f'curl -Ls {url} | tar -xj -C {HOST_HOME} bin/micromamba'),
            Step("host", f"{binary} create -y -r {root} -n {name} "
                         f"-c conda-forge {spec}"),
        ]
        plan.check = Step("host", f"{binary} run -r {root} -n {name} "
                                  "fastmdx info --json")
        plan.notes.append("micromamba is placed under ~/.fastmdxplora and "
                          "not added to your shell's startup files.")
        note = _space_note(inspection.home_free_kb, ENVIRONMENT_GB,
                           "The home directory")
        if note:
            plan.notes.append(note)
        return plan

    if inspection.container:
        driver = _version_tuple(inspection.cuda_driver)
        if driver and driver[:2] < _version_tuple(DEFAULT_CUDA_VERSION)[:2]:
            plan.blocked = (
                f"The release image is built against CUDA "
                f"{DEFAULT_CUDA_VERSION} and this driver supports up to "
                f"{inspection.cuda_driver}, so it would run on the CPU. "
                "Build one against the driver's CUDA with the container "
                "definition's CUDA_VERSION setting.")
            return plan
        plan.route = "image"
        image = f"fastmdx-{version}.sif"
        folder = (f"{inspection.scratch}/fastmdxplora" if inspection.scratch
                  else f"{HOST_HOME}/images")
        tool = inspection.container.rsplit("/", 1)[-1]
        url = RELEASE_IMAGE_URL.format(version=version)
        plan.steps += [
            Step("here", f"curl -fL -o {image} {url}"),
            Step("host", f"mkdir -p {folder}"),
            Step("here", f"rsync -P {image} {machine}:{folder}/"),
            Step("host", f"{tool} test {folder}/{image}"),
        ]
        plan.check = Step("host", f"{tool} exec {folder}/{image} "
                                  "fastmdx info --json")
        free = (inspection.scratch_free_kb if inspection.scratch
                else inspection.home_free_kb)
        note = _space_note(free, IMAGE_GB, "The destination")
        if note:
            plan.notes.append(note)
        return plan

    if inspection.internet == "no":
        plan.blocked = (
            "The machine has no internet and no Apptainer, so neither conda "
            "nor the release image can reach it. Ask for Apptainer there, "
            "or see Installing where there is no network in the cluster "
            "guide.")
    else:
        plan.blocked = (
            "Whether the machine can reach conda-forge could not be told "
            "(it has neither curl nor wget), and it has no Apptainer.")
    return plan
