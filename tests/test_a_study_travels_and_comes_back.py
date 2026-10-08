"""A study is sent, watched, brought back, and stopped.

Everything here runs for real except the network. The "machine" is this
computer: each ssh command is handed to a local ``sh`` with its own home
directory, and each rsync runs locally with the machine's side as a plain
path. So the job script, the detached process, the exit-code file, the
status script, the find that lists what fetch leaves behind and the kill
that stops a process group are the ones a real machine receives.

The installation is a stand-in ``fastmdx`` that writes a run folder the
way explore does, and sleeps or fails when told to.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import time
from pathlib import Path

import pytest

from fastmdxplora.refusals import refusal_of
from fastmdxplora.remote import CodeIdentity, Environment, Inspection, Machine
from fastmdxplora.remote.identity import same_code
from fastmdxplora.remote.inputs import gather_inputs
from fastmdxplora.remote.installer import install
from fastmdxplora.remote.machines import REQUIRED_BACKENDS, save_machine
from fastmdxplora.remote.plan import backends_plan
from fastmdxplora.remote.send import cancel, fetch, job_script, prepare, send, status
from fastmdxplora.remote.transport import Transport
from fastmdxplora.simulation.pipeline import setup_records_of

pytestmark = pytest.mark.skipif(
    os.name == "nt" or not shutil.which("sh") or not shutil.which("rsync"),
    reason="needs sh and rsync, as sending does")

RELEASE = CodeIdentity("1.0")
# The machine's installation answers for every backend a study needs, read
# from the list the readiness check itself uses: a copy kept here fell
# behind when AM1-BCC charges joined it, and every send was refused.
REQUIRED = REQUIRED_BACKENDS


def _tool(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


#: What a workstation in these tests does not have, whatever this computer
#: has installed: a scheduler, a GPU, conda, a container runtime, or a
#: FastMDXplora of its own. With sbatch on PATH the stand-in would be a
#: cluster, and a test runner with SLURM installed would fail these.
#: `sha256sum` too: macOS has none, and a stand-in using it passed on Linux
#: and failed only on the macOS runners.
_NOT_ON_THE_MACHINE = frozenset({
    "sbatch", "sinfo", "squeue", "sacct", "scancel", "nvidia-smi",
    "conda", "mamba", "micromamba", "apptainer", "singularity", "fastmdx",
    "sha256sum",
})


@pytest.fixture(scope="session")
def machine_path(tmp_path_factory) -> str:
    """A PATH holding this computer's tools, less the ones above."""
    shims = tmp_path_factory.mktemp("machine-bin")
    for folder in ("/usr/local/bin", "/usr/bin", "/bin"):
        if not Path(folder).is_dir():
            continue
        for tool in Path(folder).iterdir():
            target = shims / tool.name
            if tool.name in _NOT_ON_THE_MACHINE or target.exists():
                continue
            if os.access(tool, os.X_OK) and not tool.is_dir():
                target.symlink_to(tool)
    return str(shims)


class Here:
    """This computer standing in for a machine reached over ssh."""

    def __init__(self, root: Path, path: str, **env: str) -> None:
        self.home = root / "machine-home"
        self.home.mkdir()
        self.env = {"HOME": str(self.home), "PATH": path, "LC_ALL": "C", **env}
        self.commands: list[str] = []

    def ssh(self, command, input=None, **kwargs):
        remote = command[-1]
        self.commands.append(remote)
        # As ssh is run: bytes in and out, decoded by the caller.
        return subprocess.run(["sh", "-c", remote], input=input,
                              text=kwargs.get("text", False),
                              capture_output=kwargs.get("capture_output", True),
                              env=self.env, timeout=60, check=False)

    def local(self, command, **kwargs):
        if command[0] == "rsync":
            # Drop the ssh transport and the machine's name: both sides are here.
            args = [a for i, a in enumerate(command)
                    if a != "-e" and (i == 0 or command[i - 1] != "-e")]
            args = [a.split(":", 1)[1] if a.startswith("box:") else a for a in args]
            return subprocess.run(args, text=True, check=False,
                                  capture_output=True)
        return subprocess.run(command, text=True, check=False,
                              capture_output=True, env=self.env)

    def transport(self) -> Transport:
        return Transport("box", runner=self.ssh, interactive=False)


@pytest.fixture
def machine(tmp_path, monkeypatch, machine_path):
    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", str(tmp_path / "settings"))
    here = Here(tmp_path, machine_path, FAKE_SLEEP="0", FAKE_EXIT="0")
    env = here.home / ".conda" / "envs" / "fastmdx-1.0"
    _tool(env / "bin" / "python", "echo '1.0|||'")
    # Writes a run folder the way explore does, then sleeps or fails on cue.
    _tool(env / "bin" / "fastmdx", '''
out=run; while [ $# -gt 0 ]; do [ "$1" = --output ] && out=$2; shift; done
mkdir -p "$out/analysis" "$out/simulation"
echo '{"version": "1.0"}' > "$out/manifest.json"
cp "$HOME/live_status.json" "$out/simulation/live_status.json"
: > "$out/simulation/production.dcd"
echo "working"
sleep "$FAKE_SLEEP"
exit "$FAKE_EXIT"''')
    # The live status a real run writes, from the writer a real run uses.
    from fastmdxplora.gui.telemetry import TelemetryWriter

    TelemetryWriter(simulation_dir=str(here.home), total_steps=1000
                    ).write_status(stage="npt", current_step=400)
    info = {"backends": {n: {"name": n, "state": "installed"} for n in REQUIRED}}
    save_machine(Machine("box", "2026-09-23T00:00:00Z",
                         Inspection(home=str(here.home)),
                         info={str(env): info}))
    study = tmp_path / "study"
    study.mkdir()
    (study / "top.pdb").write_text("ATOM\n", encoding="utf-8")
    (study / "study.yml").write_text(
        "systems:\n  - system: top.pdb\ninclude_phase: [analysis]\n"
        "analysis:\n  topology: top.pdb\n", encoding="utf-8")
    here.study = study / "study.yml"
    here.back = tmp_path / "back" / "trial"
    return here


def _send(here: Here):
    sending = prepare(here.study, "box", output=str(here.back),
                      code=RELEASE, transport=here.transport())
    return send(sending, transport=here.transport(), local_runner=here.local,
                code=RELEASE)


def _until_finished(here: Here, name: str):
    for _ in range(100):
        job = status(name, transport=here.transport())
        if job.state not in ("ready", "running"):
            return job
        time.sleep(0.1)
    raise AssertionError("the job never finished")


def test_a_study_is_run_there_and_its_results_come_back(machine):
    job = _send(machine)
    assert job.state == "running" and job.handle.isdigit()
    sent = Path(job.remote_dir)
    assert (sent / "inputs" / "top.pdb").is_file()
    assert "system: inputs/top.pdb" in (sent / "study.yml").read_text()

    job = _until_finished(machine, "trial")
    assert job.state == "done"

    job, warnings = fetch("trial", transport=machine.transport(),
                          local_runner=machine.local, code=RELEASE)
    assert (machine.back / "manifest.json").is_file()
    assert (machine.back / "remote_job.log").read_text().strip() == "working"
    # The trajectory stays, and fetch says exactly that and no more.
    assert not (machine.back / "simulation" / "production.dcd").exists()
    assert len(warnings) == 1 and "1 trajectory" in warnings[0]


def test_a_run_that_fails_says_so_with_its_exit_code(machine):
    machine.env["FAKE_EXIT"] = "3"
    _send(machine)
    job = _until_finished(machine, "trial")
    assert job.state == "failed" and job.detail == "exit code 3"


def test_a_running_job_reports_progress_and_can_be_stopped(machine):
    machine.env["FAKE_SLEEP"] = "30"
    job = _send(machine)
    time.sleep(0.5)
    job = status("trial", transport=machine.transport())
    assert job.state == "running"
    assert job.detail == "npt, 40% of all steps"
    with pytest.raises(ValueError) as caught:
        fetch("trial", transport=machine.transport(), local_runner=machine.local)
    assert refusal_of(caught.value).code == "remote.job.unfinished"

    job = cancel("trial", transport=machine.transport())
    assert job.state == "abandoned"
    time.sleep(0.5)
    assert not _alive(job.handle), "the job's process group was not stopped"


def _alive(pid: str) -> bool:
    """Whether anything in the process group is still running.

    A stopped process nobody has reaped yet is a zombie, which ``kill -0``
    still finds; in a container whose first process does not reap, it stays
    one. What matters is that nothing in the group runs.
    """
    listed = subprocess.run(["ps", "-A", "-o", "pgid=,stat="],
                            capture_output=True, text=True)
    rows = [line.split() for line in listed.stdout.splitlines()]
    return any(len(row) == 2 and row[0] == pid and not row[1].startswith("Z")
               for row in rows)


def test_a_second_send_of_the_same_name_is_refused(machine):
    _send(machine)
    # Finished first: one still running is refused as the machine's one study.
    _until_finished(machine, "trial")
    with pytest.raises(ValueError) as caught:
        _send(machine)
    assert refusal_of(caught.value).code == "environment.path.exists"


def test_a_machine_not_holding_this_code_is_sent_nothing(machine):
    with pytest.raises(ValueError) as caught:
        prepare(machine.study, "box", output=str(machine.back),
                code=CodeIdentity("2.0"), transport=machine.transport())
    assert refusal_of(caught.value).code == "remote.machine.not_ready"
    assert not any("job.sh" in c for c in machine.commands)


def test_the_log_shown_is_what_the_run_said_not_the_banner():
    from fastmdxplora.remote.send import _telling

    banner = ["", "   ___             _    __  __", "  | _| / _` |(_-< | __||",
              "  Fully   Automated   SysTem   for   Molecular  Dynamics  eXploration",
              "      NPT equilibration:  20.0%  1064.93 ns/day  1m04s left"]
    assert _telling(banner) == [banner[-1]]


# ---------------------------------------------------------------------------
# What travels
# ---------------------------------------------------------------------------
def test_files_a_config_names_travel_and_the_rest_stays(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "lig.sdf").write_text("x")
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "lig.sdf").write_text("y")
    (tmp_path / "prepared").mkdir()
    config = {"output": "prepared",
              "systems": [{"system": "1L2Y"}],
              "setup": {"ligand": ["a/lig.sdf", "b/lig.sdf"], "ph": 7.4,
                        "forcefield": "amber"},
              "simulation": {"setup_from": "prepared"}}
    found = gather_inputs(config, tmp_path)
    assert found.config["output"] == "prepared"        # where results go
    assert found.config["setup"]["ligand"] == ["inputs/lig.sdf",
                                               "inputs/2-lig.sdf"]
    assert found.config["simulation"]["setup_from"] == "inputs/prepared"
    assert found.config["setup"]["forcefield"] == "amber"
    assert found.fetched == ["1L2Y"]


def test_the_job_script_asks_slurm_for_a_gpu():
    env = Environment("/e/fastmdx-1.0", "1.0")
    script = job_script(remote_dir="/scratch/me/j", job_name="j", env=env,
                        container="", scheduler="slurm", force=False,
                        partition="gpu", time_limit="24:00:00")
    assert "#SBATCH --gres=gpu:1" in script
    assert "#SBATCH --partition=gpu" in script
    assert "#SBATCH --time=24:00:00" in script
    assert "/e/fastmdx-1.0/bin/fastmdx explore -c study.yml --output run" in script
    assert script.rstrip().endswith("echo $? > exit_code")


def test_an_image_runs_with_the_gpu_passed_through():
    env = Environment("/s/fastmdx-1.0.sif", "1.0")
    script = job_script(remote_dir="/s/j", job_name="j", env=env,
                        container="/usr/bin/apptainer", scheduler="process",
                        force=True)
    assert ("/usr/bin/apptainer exec --nv /s/fastmdx-1.0.sif fastmdx explore "
            "-c study.yml --output run --force-overwrite") in script


# ---------------------------------------------------------------------------
# Installing
# ---------------------------------------------------------------------------
def test_missing_backends_are_added_to_the_environment_that_needs_them():
    plan = backends_plan(Inspection(conda={"mamba": "/m/bin/mamba"},
                                    internet="yes", cuda_driver="13.2"),
                         "/e/fastmdx-gpu", ["openmm", "openff.toolkit"], "box")
    assert plan.route == "backends"
    assert plan.steps[0].command == (
        '/m/bin/mamba install -y -p /e/fastmdx-gpu -c conda-forge '
        'openmm openff-toolkit cuda-version=12.6')


def test_nothing_is_installed_without_a_yes(machine, tmp_path):
    env = machine.home / ".conda" / "envs" / "fastmdx-1.0"
    _tool(env / "bin" / "python",
          f"echo '1.0.dev1|aaaaaaaaaaaa|no|{tmp_path}/nowhere'")
    wanted = CodeIdentity("1.0.dev2", commit="bbbbbbbbbbbb", dirty=False)
    with pytest.raises(ValueError) as caught:
        install("box", confirm=lambda plan: False,
                transport=machine.transport(), code=wanted)
    assert refusal_of(caught.value).code == "remote.install.unconfirmed"
    assert not any(c.startswith("sh -c 'git") or "git -C" in c
                   for c in machine.commands)


def test_an_install_stops_at_the_first_step_that_fails(machine, tmp_path):
    env = machine.home / ".conda" / "envs" / "fastmdx-1.0"
    _tool(env / "bin" / "python",
          f"echo '1.0.dev1|aaaaaaaaaaaa|no|{tmp_path}/nowhere'")
    wanted = CodeIdentity("1.0.dev2", commit="bbbbbbbbbbbb", dirty=False)
    outcome = install("box", confirm=lambda plan: True,
                      transport=machine.transport(), code=wanted)
    assert outcome.plan.route == "checkout"
    assert outcome.ran == 0
    assert outcome.failed_step == f"git -C {tmp_path}/nowhere fetch origin"


def test_the_code_that_ran_is_checked_against_the_code_that_sent_it(machine):
    _send(machine)
    _until_finished(machine, "trial")
    Path(machine.back).mkdir(parents=True, exist_ok=True)
    job, warnings = fetch("trial", transport=machine.transport(),
                          local_runner=machine.local, code=RELEASE)
    manifest = json.loads((machine.back / "manifest.json").read_text())
    assert same_code(RELEASE, CodeIdentity(manifest["version"]))[0]
    assert not any("does not name the code" in w for w in warnings)


# ---------------------------------------------------------------------------
# A fetched study finds the prepared system it was sent with
# ---------------------------------------------------------------------------
# A study given `setup_from` travels with the prepared system under
# `inputs/<name>`, and its run records that name. Fetched back, the name meant
# nothing on this computer: re-analysis and the report found no setup record
# for the run, and `fetch` said nothing. The fetch now records where each
# input was sent from (`fetched.json`), the run's own record decides whether
# what is there is still it, and `fetch` says which runs cannot find theirs.
#: The stand-in installation, recording the prepared system it was given as
#: the simulation phase does: as named there, resolved there, relative to
#: the run and by the SHA-256 of its `system.xml`.
RUNS_FROM_A_PREPARED_SYSTEM = '''
out=run; while [ $# -gt 0 ]; do [ "$1" = --output ] && out=$2; shift; done
mkdir -p "$out/simulation"
echo '{"version": "1.0"}' > "$out/manifest.json"
digest=$(shasum -a 256 inputs/prepared/setup/system.xml | cut -d' ' -f1)
there=$(cd inputs/prepared/setup && pwd -P)
printf '{"prepared_system": {"given": "inputs/prepared", "resolved": "%s", "relative_to_run": "../inputs/prepared/setup", "system_xml_sha256": "%s"}}' "$there" "$digest" > "$out/simulation/simulation_parameters.json"
echo "working"'''


@pytest.fixture
def sent(machine):
    """A study naming a prepared system on this computer, run there."""
    _tool(machine.home / ".conda" / "envs" / "fastmdx-1.0" / "bin" / "fastmdx",
          RUNS_FROM_A_PREPARED_SYSTEM)
    prepared = machine.study.parent / "prepared" / "setup"
    prepared.mkdir(parents=True)
    for name in ("system.xml", "state.xml", "topology.pdb"):
        (prepared / name).write_text(f"<{name}/>", encoding="utf-8")
    (prepared / "setup_parameters.json").write_text("{}", encoding="utf-8")
    machine.study.write_text(
        "systems:\n  - system: top.pdb\ninclude_phase: [simulation]\n"
        "simulation:\n  setup_from: prepared\n", encoding="utf-8")
    job = _send(machine)
    assert job.extra["inputs"]["prepared"] == str(prepared.parent.resolve())
    assert _until_finished(machine, "trial").state == "done"
    # The machine's folders are not on this computer; here they would be,
    # since the machine is this computer, and would be found by their path.
    shutil.rmtree(Path(job.remote_dir) / "inputs")
    machine.prepared = prepared
    return machine


def _fetch(here):
    return fetch("trial", transport=here.transport(), local_runner=here.local,
                 code=RELEASE)


def test_it_finds_the_prepared_system_it_was_sent_from(sent) -> None:
    _, warnings = _fetch(sent)
    assert not warnings
    record = json.loads((sent.back / "fetched.json").read_text(encoding="utf-8"))
    assert record["inputs"]["prepared"] == str(sent.prepared.parent.resolve())
    assert setup_records_of(sent.back) == sent.prepared.resolve()


def test_one_prepared_again_since_is_said_and_not_read(sent) -> None:
    (sent.prepared / "system.xml").write_text("<another/>", encoding="utf-8")
    _, warnings = _fetch(sent)
    assert len(warnings) == 1 and "different system" in warnings[0]


def test_one_gone_since_is_said(sent) -> None:
    shutil.rmtree(sent.prepared.parent)
    _, warnings = _fetch(sent)
    assert len(warnings) == 1
    assert "'inputs/prepared'" in warnings[0] and "not on this computer" in warnings[0]
    assert setup_records_of(sent.back) is None


def test_a_study_sent_without_one_is_fetched_as_before(machine) -> None:
    _send(machine)
    _until_finished(machine, "trial")
    _, warnings = _fetch(machine)
    assert len(warnings) == 1 and "1 trajectory" in warnings[0]
    assert json.loads((Path(machine.back) / "fetched.json").read_text())["inputs"] == {
        "top.pdb": str((machine.study.parent / "top.pdb").resolve())}
