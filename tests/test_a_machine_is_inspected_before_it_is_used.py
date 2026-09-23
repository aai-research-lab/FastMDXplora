"""A machine is inspected before anything is sent to it.

The inspection script is run for real here, by this computer's own ``sh``,
against a home directory laid out the way installers leave one and a PATH
holding stand-ins for ``nvidia-smi``, ``sbatch`` and ``curl``. What is
checked is what the script finds, not what a hand-written answer says it
would have found.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from fastmdxplora.refusals import refusal_of
from fastmdxplora.remote import (
    DEFAULT_CUDA_VERSION,
    CodeIdentity,
    PROBE_SCRIPT,
    Environment,
    Gpu,
    Inspection,
    Machine,
    Transport,
    UnknownMachine,
    cuda_pin,
    forget_machine,
    inspect_machine,
    install_plan,
    load_machine,
    machine_names,
    parse_inspection,
    readiness,
    same_code,
    save_machine,
)
from fastmdxplora.remote.describe import describe_unloadable, overview

RELEASE = CodeIdentity("2.5.6")
CHECKOUT = CodeIdentity("2.5.6.dev172+g64f17c43b", commit="eae609edbbf9",
                        dirty=False, checkout="/src/FastMDXplora")

REPO = Path(__file__).resolve().parents[1]
SH = shutil.which("sh")
needs_sh = pytest.mark.skipif(SH is None or os.name == "nt",
                              reason="the probe is a POSIX sh script")


@pytest.fixture
def settings(monkeypatch, tmp_path):
    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", str(tmp_path / "settings"))
    return tmp_path / "settings"


def _tool(folder: Path, name: str, body: str) -> None:
    path = folder / name
    path.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _laid_out_machine(tmp_path: Path) -> tuple[Path, Path]:
    """A home with a miniforge holding one FastMDXplora environment, and a
    PATH where a GPU, a scheduler and a failing curl answer."""
    home = tmp_path / "home"
    env = home / "miniforge3" / "envs" / "fastmdx-9.9.9"
    (env / "bin").mkdir(parents=True)
    (home / "miniforge3" / "bin").mkdir(parents=True)
    _tool(home / "miniforge3" / "bin", "conda", "exit 0")
    _tool(env / "bin", "python", "echo '9.9.9|||'")
    _tool(env / "bin", "fastmdx", "exit 0")
    # Where conda puts an environment when its base is not the user's to
    # write, as on a workstation with conda in /opt: found by what it holds,
    # whatever it is called.
    own = home / ".conda" / "envs" / "working"
    (own / "bin").mkdir(parents=True)
    _tool(own / "bin", "python",
          f"echo '9.9.9.dev1+gold|eae609edbbf9|no|{home}/src/FastMDXplora'")
    _tool(own / "bin", "fastmdx", "exit 0")
    # An environment without FastMDXplora is not one.
    (home / ".conda" / "envs" / "other" / "bin").mkdir(parents=True)
    _tool(home / ".conda" / "envs" / "other" / "bin", "python", "echo nope")
    (home / "images").mkdir()
    (home / "images" / "fastmdx-9.9.9.sif").write_text("", encoding="utf-8")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _tool(bin_dir, "nvidia-smi", r'''
case "$*" in
  *query-gpu*) printf 'NVIDIA L40S, 46068, 565.57.01\nNVIDIA L40S, 46068, 565.57.01\n' ;;
  *) echo "| NVIDIA-SMI 565.57.01    Driver Version: 565.57.01    CUDA Version: 12.7 |" ;;
esac''')
    _tool(bin_dir, "sbatch", "exit 0")
    _tool(bin_dir, "sinfo", "echo 'gpu|2-00:00:00|gpu:l40s:2'")
    _tool(bin_dir, "curl", "exit 7")
    return home, bin_dir


def _run_probe(home: Path, bin_dir: Path) -> str:
    system_path = "/usr/bin:/bin"
    done = subprocess.run(
        [SH, "-s"], input=PROBE_SCRIPT, capture_output=True, text=True,
        env={"HOME": str(home), "PATH": f"{bin_dir}:{system_path}"},
        timeout=60, check=False)
    return done.stdout


# ---------------------------------------------------------------------------
# The probe, run for real
# ---------------------------------------------------------------------------
@needs_sh
def test_the_probe_finds_what_an_installer_left(tmp_path):
    home, bin_dir = _laid_out_machine(tmp_path)
    found = parse_inspection(_run_probe(home, bin_dir))

    assert found is not None
    assert found.os == os.uname().sysname
    assert found.cpus and found.cpus > 0
    assert found.gpus == [Gpu("NVIDIA L40S", 46068, "565.57.01")] * 2
    assert found.cuda_driver == "12.7"
    # Found where installers put it, though nothing put it on PATH.
    assert f"{home}/miniforge3" in found.conda_roots
    assert sorted(found.environments, key=lambda e: e.path) == sorted([
        Environment(f"{home}/.conda/envs/working", "9.9.9.dev1+gold",
                    "eae609edbbf9", "no", f"{home}/src/FastMDXplora"),
        Environment(f"{home}/miniforge3/envs/fastmdx-9.9.9", "9.9.9"),
    ], key=lambda e: e.path)
    # The release environment and the release image hold the same code.
    assert [e.path for e in found.holding(CodeIdentity("9.9.9"))] == [
        f"{home}/miniforge3/envs/fastmdx-9.9.9",
        f"{home}/images/fastmdx-9.9.9.sif"]
    assert [e.path for e in found.holding(CHECKOUT)] == [
        f"{home}/.conda/envs/working"]
    assert found.kind == "slurm"
    assert found.partitions == [
        {"name": "gpu", "time_limit": "2-00:00:00", "gres": "gpu:l40s:2"}]
    assert found.internet == "no"
    assert found.home_free_kb is not None


@needs_sh
def test_the_probe_changes_nothing_on_the_machine(tmp_path):
    home, bin_dir = _laid_out_machine(tmp_path)
    before = sorted(str(p) for p in home.rglob("*"))
    _run_probe(home, bin_dir)
    assert sorted(str(p) for p in home.rglob("*")) == before


def test_an_answer_cut_short_is_not_an_answer():
    whole = "fmdx:probe=1\nfmdx:os=Linux\nfmdx:probe_end=1\n"
    assert parse_inspection(whole) is not None
    assert parse_inspection("fmdx:probe=1\nfmdx:os=Linux\n") is None


def test_what_a_profile_prints_is_not_read_as_an_answer():
    noisy = ("Welcome to the cluster\nos=Windows\nPATH=/nowhere\n"
             "fmdx:probe=1\nfmdx:os=Linux\nfmdx:probe_end=1\n")
    assert parse_inspection(noisy).os == "Linux"


# ---------------------------------------------------------------------------
# The install plan
# ---------------------------------------------------------------------------
def _machine(**fields) -> Inspection:
    base = dict(os="Linux", arch="x86_64", internet="yes", bzip2=True,
                home_free_kb=50 * 1024 * 1024)
    base.update(fields)
    return Inspection(**base)


def test_a_conda_that_is_there_is_used():
    plan = install_plan(_machine(conda={"mamba": "/opt/mf/bin/mamba"},
                                 cuda_driver="13.2"), RELEASE, "box")
    assert plan.route == "conda"
    assert [s.where for s in plan.steps] == ["host"]
    assert plan.steps[0].command == (
        '/opt/mf/bin/mamba create -y -n fastmdx-2.5.6 -c conda-forge '
        '"fastmdxplora=2.5.6" "cuda-version=12.6"')
    assert plan.check.command.endswith("fastmdx info --json")


def test_the_package_brings_its_own_stack():
    # conda-forge's fastmdxplora declares OpenFF, openmmforcefields, PLUMED
    # and the rest; naming them again is a second list that can drift.
    plan = install_plan(_machine(conda={"conda": "/c/bin/conda"}), RELEASE, "b")
    command = plan.steps[0].command
    for extra in ("openff", "openmmforcefields", "plumed", "weasyprint"):
        assert extra not in command


def test_without_conda_micromamba_is_placed_in_the_users_space():
    plan = install_plan(_machine(), RELEASE, "box")
    assert plan.route == "micromamba"
    commands = "\n".join(s.command for s in plan.steps)
    assert "linux-64" in commands
    assert "$HOME/.fastmdxplora" in commands
    assert "shell init" not in commands and "sudo" not in commands


def test_offline_with_apptainer_the_release_image_is_carried_over():
    plan = install_plan(_machine(internet="no", container="/usr/bin/apptainer",
                                 scratch="/scratch/me"), RELEASE, "lab-hpc")
    assert plan.route == "image"
    where = [s.where for s in plan.steps]
    assert where[0] == "here"      # fetched where there is a network
    assert "lab-hpc:/scratch/me/fastmdxplora/" in plan.steps[2].command
    assert plan.steps[-1].command == (
        "apptainer test /scratch/me/fastmdxplora/fastmdx-2.5.6.sif")


def test_offline_without_apptainer_there_is_no_route():
    plan = install_plan(_machine(internet="no"), RELEASE, "box")
    assert not plan.possible and "no internet" in plan.blocked


def test_a_checkout_is_not_offered_from_conda_forge():
    plan = install_plan(_machine(conda={"conda": "/c/bin/conda"}),
                        CHECKOUT, "box")
    assert not plan.possible
    assert "eae609edbbf9" in plan.blocked and "releases only" in plan.blocked


def test_a_checkout_there_is_brought_to_this_commit_with_git():
    there = Environment("/home/me/.conda/envs/fastmdx-gpu", "2.5.7.dev105",
                        "13aa8d71c0de", "no", "/home/me/FastMDXplora")
    plan = install_plan(_machine(environments=[there]), CHECKOUT, "aailab01")
    assert plan.route == "checkout"
    assert [s.command for s in plan.steps] == [
        "git -C /home/me/FastMDXplora fetch origin",
        "git -C /home/me/FastMDXplora merge --ff-only eae609edbbf9",
    ]
    assert plan.check.command == (
        "/home/me/.conda/envs/fastmdx-gpu/bin/fastmdx info --json")
    assert any("push it" in note for note in plan.notes)


def test_uncommitted_changes_here_have_no_plan():
    dirty = CodeIdentity("x", commit="eae609edbbf9", dirty=True)
    plan = install_plan(_machine(), dirty, "box")
    assert not plan.possible and "uncommitted" in plan.blocked


def test_the_cuda_pin_never_exceeds_the_driver():
    assert cuda_pin(_machine(cuda_driver="13.2"))[0] == DEFAULT_CUDA_VERSION
    assert cuda_pin(_machine(cuda_driver="12.2"))[0] == "12.2"
    pin, why = cuda_pin(_machine(sbatch="/usr/bin/sbatch"))
    assert pin == DEFAULT_CUDA_VERSION and "compute nodes" in why


def test_an_image_newer_than_the_driver_is_refused():
    plan = install_plan(_machine(internet="no", container="/usr/bin/apptainer",
                                 cuda_driver="12.2"), RELEASE, "box")
    assert not plan.possible and "CPU" in plan.blocked


def test_the_cuda_default_is_the_containers():
    definition = (REPO / "container" / "fastmdx.def").read_text(encoding="utf-8")
    workflow = (REPO / ".github" / "workflows" / "container.yml").read_text(
        encoding="utf-8")
    assert f"CUDA_VERSION:-{DEFAULT_CUDA_VERSION}" in definition
    assert f"|| '{DEFAULT_CUDA_VERSION}'" in workflow


# ---------------------------------------------------------------------------
# Which code an installation holds
# ---------------------------------------------------------------------------
def test_a_checkout_is_known_by_its_commit_not_its_version_string():
    # The Mac that found this reported 64f17c4 in its version string while
    # running eae609e: an editable install keeps the string it was given.
    stale = CodeIdentity("2.5.6.dev172+g64f17c43b", commit="eae609edbbf9")
    fresh = CodeIdentity("2.5.7.dev121+gc07cf6190", commit="eae609edbbf9")
    assert same_code(stale, fresh)[0]
    moved = CodeIdentity("2.5.6.dev172+g64f17c43b", commit="64f17c43b000")
    assert not same_code(stale, moved)[0]


def test_uncommitted_changes_are_never_the_same_code():
    clean = CodeIdentity("x", commit="eae609edbbf9", dirty=False)
    for state in (True, None):
        other = CodeIdentity("x", commit="eae609edbbf9", dirty=state)
        assert not same_code(clean, other)[0]
        assert not same_code(other, clean)[0]


def test_a_release_and_a_checkout_are_not_the_same_code():
    assert same_code(RELEASE, CodeIdentity("2.5.6"))[0]
    assert not same_code(RELEASE, CodeIdentity("2.5.6", commit="eae609edbbf9"))[0]
    assert not same_code(RELEASE, CodeIdentity("2.5.4"))[0]


def test_this_computer_is_identified_the_same_way(monkeypatch):
    from fastmdxplora import provenance
    from fastmdxplora.remote import identity

    monkeypatch.setattr(provenance, "source_provenance",
                        lambda: {"commit": "eae609edbbf9", "dirty": False})
    monkeypatch.setattr(provenance, "source_checkout", lambda: Path("/src"))
    assert identity.this_code().commit == "eae609edbbf9"
    monkeypatch.setattr(provenance, "source_provenance", lambda: None)
    assert not identity.this_code().is_checkout


# ---------------------------------------------------------------------------
# Reaching a machine
# ---------------------------------------------------------------------------
def test_a_name_that_ssh_would_read_as_an_option_is_refused():
    with pytest.raises(ValueError) as caught:
        Transport("-oProxyCommand=touch /tmp/x")
    assert refusal_of(caught.value).code == "remote.machine.unusable_name"


def test_the_destination_follows_the_end_of_options():
    command = Transport("gpu-box", interactive=False).ssh_command(["sh", "-s"])
    assert command[0] == "ssh"
    assert command[command.index("--") + 1] == "gpu-box"
    assert "BatchMode=yes" in command
    assert command[-1] == "sh -s"


def test_a_person_at_a_terminal_can_answer_a_prompt():
    command = Transport("gpu-box", interactive=True).ssh_command(["true"])
    assert "BatchMode=yes" not in command


def test_no_ssh_says_what_is_missing():
    def absent(*args, **kwargs):
        raise FileNotFoundError("ssh")

    with pytest.raises(ValueError) as caught:
        Transport("box", runner=absent, interactive=False).run(["true"])
    assert refusal_of(caught.value).code == "environment.backend.missing"


def test_an_unreachable_machine_is_refused_with_sshs_reason():
    def refused(command, **kwargs):
        return subprocess.CompletedProcess(
            command, 255, "", "ssh: connect to host box port 22: Connection refused\n")

    with pytest.raises(ValueError) as caught:
        Transport("box", runner=refused, interactive=False).run(["true"])
    refusal = refusal_of(caught.value)
    assert refusal.code == "environment.service.machine_unreachable"
    assert refusal.retryable
    assert "Connection refused" in str(caught.value)


# ---------------------------------------------------------------------------
# Records, readiness, and the inspection end to end
# ---------------------------------------------------------------------------
def _ready_info() -> dict:
    names = ("openmm", "pdbfixer", "openff.toolkit", "openmmforcefields",
             "rdkit", "propka")
    return {"backends": {n: {"name": n, "state": "installed"} for n in names}}


@needs_sh
def test_an_inspection_records_the_installation_and_what_it_loads(
        tmp_path, settings):
    home, bin_dir = _laid_out_machine(tmp_path)
    asked = []

    def runner(command, input=None, **kwargs):
        remote = command[-1]
        asked.append(remote)
        if remote == "sh -s":
            return subprocess.CompletedProcess(
                command, 0, _run_probe(home, bin_dir), "")
        return subprocess.CompletedProcess(
            command, 0, "a banner\n" + json.dumps(_ready_info()), "")

    machine = inspect_machine("box", code=CodeIdentity("9.9.9"),
                              transport=Transport("box", runner=runner,
                                                  interactive=False))
    # Only the installation holding this code is asked what it loads.
    assert asked[1:] == [
        f"{home}/miniforge3/envs/fastmdx-9.9.9/bin/fastmdx info --json"]
    assert readiness(machine, CodeIdentity("9.9.9")).ready
    assert not readiness(machine, CodeIdentity("9.9.10")).ready
    assert not readiness(machine, CHECKOUT).ready
    assert load_machine("box").inspection == machine.inspection
    assert machine_names() == ["box"]


def test_an_installation_that_cannot_load_openmm_is_not_ready(settings):
    info = _ready_info()
    info["backends"]["openmm"] = {"name": "OpenMM", "state": "missing",
                                  "install": "conda install -c conda-forge openmm"}
    env = Environment("/e/fastmdx-1.0", "1.0")
    machine = Machine(
        name="box", inspected_at="2026-09-23T00:00:00Z",
        inspection=_machine(environments=[env]), info={env.path: info})
    verdict = readiness(machine, CodeIdentity("1.0"))
    assert not verdict.ready and "OpenMM" in verdict.summary
    advice = "\n".join(describe_unloadable(machine, verdict))
    assert "conda install -c conda-forge openmm" in advice


def test_a_dirty_checkout_here_is_never_ready_anywhere(settings):
    env = Environment("/e/w", "x", "eae609edbbf9", "no", "/src")
    machine = Machine("box", "2026-09-23T00:00:00Z",
                      _machine(environments=[env]), {env.path: _ready_info()})
    assert readiness(machine, CHECKOUT).ready
    dirty = CodeIdentity("x", commit="eae609edbbf9", dirty=True)
    verdict = readiness(machine, dirty)
    assert not verdict.ready and "uncommitted" in verdict.summary


def test_forgetting_removes_the_record_and_nothing_else(settings):
    save_machine(Machine("box", "2026-09-23T00:00:00Z", _machine()))
    forget_machine("box")
    assert machine_names() == []
    with pytest.raises(UnknownMachine) as caught:
        load_machine("box")
    refusal = refusal_of(caught.value)
    assert refusal.code == "remote.machine.unknown"
    assert refusal.permitted == ()


def test_the_overview_connects_to_nothing(settings):
    save_machine(Machine("box", "2026-09-23T10:00:00Z", _machine()))
    lines = overview([load_machine("box")], CodeIdentity("1.0"))
    assert lines[0] == "Machines"
    assert "box" in lines[1] and "not ready" in lines[1]


# ---------------------------------------------------------------------------
# The command
# ---------------------------------------------------------------------------
def test_with_nothing_inspected_the_command_says_how_to_start(settings, capsys):
    from fastmdxplora.cli.main import main

    assert main(["remote"]) == 0
    assert "fastmdx remote --machine <alias>" in capsys.readouterr().out


def test_forgetting_an_unknown_machine_is_refused(settings, capsys):
    from fastmdxplora.cli.main import main

    assert main(["remote", "forget", "nowhere"]) == 1
    assert "No machine called 'nowhere'" in capsys.readouterr().err


def test_info_speaks_json_without_a_banner(capsys):
    from fastmdxplora.cli.main import main

    assert main(["info", "--json"]) == 0
    record = json.loads(capsys.readouterr().out)
    assert set(record["phases"]) == {"setup", "simulation", "analysis", "report"}
    assert record["backends"]["openmm"]["state"] in ("installed", "missing",
                                                     "broken")
