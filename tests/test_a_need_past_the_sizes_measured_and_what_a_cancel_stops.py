"""What a study needs where the sizes measured do not grow in order, and
what a job leaves going once it has ended.

Memory is never planned from a size measured larger than the next one up,
nor flattened by a larger size that held less. A workstation job's run
carries its send's own id, so whatever its run leaves going after the job
has ended is found, said, and stopped by a cancel, and nothing of another
send of the same name is.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

import pytest

from fastmdxplora.refusals import StudyError, refusal_of
from fastmdxplora.remote.send import cancel, prepare, send, status
from tests import test_a_study_travels_and_comes_back as travels
from tests import test_remote_from_every_interface as every
from tests.test_a_study_travels_and_comes_back import RELEASE, _send

machine = travels.machine
machine_path = travels.machine_path
app = every.app

pytestmark = pytest.mark.skipif(
    not Path("/proc/self/environ").exists(),
    reason="a job's processes are found by their environment, read from /proc")


# ---------------------------------------------------------------------------
# What a study needs
# ---------------------------------------------------------------------------
class TestTheSizesMeasured:
    @pytest.fixture(autouse=True)
    def settings(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", str(tmp_path / "settings"))

    @staticmethod
    def _learned(*runs: tuple[int, int], on: str = "box") -> None:
        from fastmdxplora.remote.gpu_room import learn

        for job, (particles, mb) in enumerate(runs):
            learn(on, job=str(job), particles=particles, peak_mb=mb, gpu="g")

    def test_a_dip_above_a_study_does_not_give_it_the_largest_run_s_memory(self):
        """A size measured above the next one that held less (another method,
        another card) gave a study just past the smallest the largest run's
        memory: 10,350 MB for 10,001 particles beside a 10,000 run of 1,500."""
        from fastmdxplora.remote.gpu_room import need_for

        self._learned((10_000, 1500), (12_000, 1600), (20_000, 1550), (40_000, 9000))
        # The next size up, 12,000, held 1,600: and 15% more.
        assert need_for("box", 10_001).mb == 1840
        # Never below what a run of that size or smaller held.
        assert need_for("box", 13_000).mb == 1840
        assert need_for("box", 20_000).mb == 1840
        assert need_for("box", 20_001).mb == 10350

    def test_highs_going_down_past_the_largest_do_not_flatten_what_a_larger_one_is_given(
            self):
        """Past the largest size measured, a last size that held less than
        the one before made the line nearly flat: 40,000 particles were given
        little more than what the 20,000 run held."""
        from fastmdxplora.remote.gpu_room import need_for

        self._learned((10_000, 2000), (20_000, 6000), (30_000, 5000))
        # The most held, 6,000 MB, carried on from the largest size as
        # steeply as it rose from 10,000: 0.4 MB a particle, 10,000 MB at
        # 40,000; and 15%.
        assert need_for("box", 40_000).mb == 11500
        assert need_for("box", 50_001).mb is None       # past as far again

    def test_the_most_held_by_the_smallest_does_not_drop_the_trend_of_the_rest(self):
        """The size holding the most was the smallest (another card): the
        plan fell back to the line, 3,450 MB for 150,000 particles where the
        sizes from 30,000 up grow 0.02 MB a particle to 3,900."""
        from fastmdxplora.remote.gpu_room import need_for

        self._learned((10_000, 3000), (30_000, 1500), (60_000, 2100), (90_000, 2700))
        assert need_for("box", 150_000).mb == 4485

    def test_two_sizes_close_together_do_not_give_any_slope(self):
        """A rise over a few particles, read with ordinary noise, planned
        1,270,865 MB for 61,000 particles."""
        from fastmdxplora.remote.gpu_room import need_for

        self._learned((10_000, 1000), (49_999, 5000), (50_000, 5100), (60_000, 5050))
        # The line through the four, 5,688 MB at 61,000, and 15%.
        assert need_for("box", 61_000).mb == 6542
        # Only sizes a tenth of the span smaller give a slope: 1,500 MB from
        # 10,000 to 100,000, from 2,500 MB at the largest; 3,333 MB at 150,000.
        self._learned((10_000, 1000), (95_000, 2000), (100_000, 2500), on="box2")
        assert need_for("box2", 150_000).mb == 3834

    def test_a_small_size_s_step_is_not_carried_over_the_whole_range(self):
        """The slope up to the size holding the most, two small sizes close
        together, was carried from that size over every particle past it:
        37.5 GB and 1 TB planned."""
        from fastmdxplora.remote.gpu_room import need_for

        self._learned((20_000, 1400), (21_000, 1900), (100_000, 1800))
        assert need_for("box", 150_000).mb == 2358
        self._learned((1000, 500), (1100, 6000), (10_000, 5100), on="box3")
        assert need_for("box3", 19_000).mb == 11155

    def test_a_next_size_that_held_less_does_not_lower_one_between(self):
        """The next size up held less than its neighbours (another method):
        a study between was given the most held below it, not carried on as
        that rose."""
        from fastmdxplora.remote.gpu_room import need_for

        self._learned((1000, 1000), (2000, 2000), (3000, 1500), (4000, 4000))
        # 2,000 MB at 2,000, rising 1 MB a particle from 1,000: 2,500 MB at
        # 2,500; and 15%.
        assert need_for("box", 2500).mb == 2875
        assert need_for("box", 2000).mb == 2300

    def test_what_is_carried_on_is_never_above_what_larger_runs_held(self):
        """Carried on across a size measured, a study of exactly that size
        was planned at 5,500 MB where a run of that size held 1,200 and none
        more than 1,500."""
        from fastmdxplora.remote.gpu_room import need_for

        self._learned((10_000, 1000), (20_000, 1500), (100_000, 1200))
        assert need_for("box", 100_000).mb == 1725
        assert need_for("box", 50_000).mb == 1725

    def test_highs_growing_to_the_end_carry_on_from_the_largest(self):
        from fastmdxplora.remote.gpu_room import need_for

        self._learned((10_000, 600), (110_000, 1600))
        # 10 MB per 1,000 particles from 1,600 MB at 110,000: as before.
        assert need_for("box", 160_000).mb == 2415


# ---------------------------------------------------------------------------
# What a job leaves going
# ---------------------------------------------------------------------------
#: A stand-in run that leaves a process going and ends: ``$FAKE_LEFT`` is
#: how the process starts (``setsid`` out of the job's group, ``nohup`` in
#: it), and its number is written to ``$HOME/left_pid``.
_LEAVES_ONE = '''
out=run; while [ $# -gt 0 ]; do [ "$1" = --output ] && out=$2; shift; done
mkdir -p "$out/analysis" "$out/simulation"
echo '{"version": "1.0"}' > "$out/manifest.json"
echo "working"
$FAKE_LEFT sleep 60 > /dev/null 2>&1 < /dev/null &
echo $! > "$HOME/left_pid"
exit "$FAKE_EXIT"'''


def _leaving_one(machine, how: str, exit_code: str = "1") -> None:
    travels._tool(machine.home.joinpath(*every._FASTMDX), _LEAVES_ONE)
    machine.env["FAKE_LEFT"] = how
    machine.env["FAKE_EXIT"] = exit_code


def _left_pid(machine) -> str:
    every._until_there(machine.home / "left_pid")
    deadline = time.monotonic() + 10
    while not (said := (machine.home / "left_pid").read_text().strip()):
        assert time.monotonic() < deadline
        time.sleep(0.05)
    return said


def _killed(pid: str) -> None:
    try:
        os.kill(int(pid), signal.SIGKILL)
    except (ProcessLookupError, ValueError):
        pass


def _gone(pid: str, seconds: float = 10) -> bool:
    deadline = time.monotonic() + seconds
    while every._process_there(pid):
        if time.monotonic() > deadline:
            return False
        time.sleep(0.05)
    return True


@pytest.mark.skipif(not shutil.which("setsid"), reason="starts a process out of a group")
class TestWhatAJobLeavesGoing:
    @pytest.mark.parametrize("how", ["setsid", "nohup"])
    def test_it_is_said_and_stopped_by_a_cancel(self, machine, how):
        """An explorer that died with its runs going wrote its exit code: the
        job read failed, `cancel` stopped nothing, and a forced send was
        refused while they ran, with no way to stop them from here."""
        _leaving_one(machine, how)
        job = _send(machine)
        left = _left_pid(machine)
        try:
            job = travels._until_finished(machine, job.name)
            assert job.state == "failed"
            assert job.detail.startswith("exit code 1; 1 process it started still works "
                                         "there (`fastmdx remote cancel trial` stops it)")
            again = prepare(machine.study, "box", output=str(machine.back), force=True,
                            code=RELEASE, transport=machine.transport())
            with pytest.raises(ValueError) as caught:
                send(again, transport=machine.transport(), local_runner=machine.local,
                     code=RELEASE)
            assert "`fastmdx remote cancel trial` stops them" in str(caught.value)
            stopped = cancel("trial", transport=machine.transport())
            # How it ended is kept; what it left going is stopped.
            assert stopped.state == "failed"
            assert stopped.detail.startswith("exit code 1; what it left going was asked "
                                             "to stop ")
            assert _gone(left)
            # Asked once more, nothing is left.
            assert "still work" not in status("trial", transport=machine.transport()).detail
        finally:
            _killed(left)

    def test_another_send_s_process_is_not_stopped(self, machine):
        """Keyed to the send, not the folder or the name: a process of a later
        send of that name (from another computer) carries another id."""
        _leaving_one(machine, "setsid")
        job = _send(machine)
        left = _left_pid(machine)
        other = subprocess.Popen(
            ["sleep", "60"], cwd=job.remote_dir, start_new_session=True,
            env={**os.environ, "FMDX_SEND_ID": "0123456789abcdef",
                 "FMDX_JOB_DIR": str(Path(job.remote_dir).resolve())})
        try:
            travels._until_finished(machine, job.name)
            cancel("trial", transport=machine.transport())
            assert _gone(left)
            assert every._process_there(str(other.pid))
        finally:
            _killed(left)
            other.kill()
            other.wait()

    def test_a_job_that_ended_with_nothing_left_is_let_be(self, machine):
        job = _send(machine)
        job = travels._until_finished(machine, job.name)
        assert job.state == "done" and job.detail == ""
        assert cancel("trial", transport=machine.transport()).state == "done"
        assert "left_stopped_at" not in status("trial",
                                               transport=machine.transport()).extra

    def test_its_send_id_is_the_run_s_alone(self, machine):
        """The job's script, and the GPU reader that sleeps past its end, do
        not carry it: a job just ended reads nothing left going."""
        job = _send(machine)
        script = (Path(job.remote_dir) / "job.sh").read_text()
        explorer = [line for line in script.splitlines() if "explore" in line]
        assert explorer and explorer[0].startswith(
            'FMDX_JOB_DIR="$(pwd -P)" FMDX_SEND_ID="${1:-}" ')
        assert "FMDX_SEND_ID" not in script.replace(explorer[0], "")
        # Given to the script as it starts, not through a file in the folder
        # a send racing in from another computer could write first.
        assert any(f"nohup sh job.sh {job.extra['send_id']} >" in c
                   for c in machine.commands)
        assert not (Path(job.remote_dir) / ".fmdx-send-id").exists()

    def test_a_sampler_sleeping_past_the_end_is_not_left_going(self, machine, monkeypatch):
        from fastmdxplora.remote import gpu_room

        monkeypatch.setattr(gpu_room, "SAMPLE_EVERY_S", 4)
        every._gpus(machine, (0, every.UUID_0, 24000, 23000))
        job = every._go(machine, every._plan(machine, machine.back.parent, name="trial"))
        job = travels._until_finished(machine, job.name)
        assert job.state == "done" and "still work" not in job.detail
        assert "left_going" not in job.extra


def _leaving_trapped(machine) -> None:
    """A stand-in run that ends and leaves, out of its group, a process
    that counts each TERM it is sent and stays until killed."""
    travels._tool(machine.home.joinpath(*every._FASTMDX), _LEAVES_ONE.replace(
        "$FAKE_LEFT sleep 60", "setsid sh -c 'trap \"echo t >> \\\"$HOME/terms_out"
        "\\\"\" TERM; while :; do sleep 0.1; done'"))
    machine.env["FAKE_EXIT"] = "1"


#: A stand-in run that counts each TERM it is sent, as does a process it
#: starts out of its group, and that stays until killed.
_COUNTS_ITS_STOPS = r'''
out=run; while [ $# -gt 0 ]; do [ "$1" = --output ] && out=$2; shift; done
mkdir -p "$out/analysis" "$out/simulation"
setsid sh -c 'trap "echo t >> \"$HOME/terms_out\"" TERM; echo $$ > "$HOME/left_pid"
  while :; do sleep 0.1; done' > /dev/null 2>&1 < /dev/null &
trap 'echo t >> "$HOME/terms_run"' TERM
while :; do sleep 0.1; done'''


def _terms(machine, which: str) -> int:
    path = machine.home / f"terms_{which}"
    return len(path.read_text().split()) if path.exists() else 0


@pytest.mark.skipif(not shutil.which("setsid"), reason="starts a process out of a group")
class TestEachIsAskedToStopOnce:
    def test_a_running_job_s_run_is_sent_one_term(self, machine):
        """The group's TERM, then a second ssh call sending TERM again to every
        process carrying the id: a run told twice more than 2 s apart stops at
        once, with no checkpoint and no record."""
        travels._tool(machine.home.joinpath(*every._FASTMDX), _COUNTS_ITS_STOPS)
        job = _send(machine)
        left = _left_pid(machine)
        try:
            time.sleep(0.5)
            cancel(job.name, transport=machine.transport())
            deadline = time.monotonic() + 10
            while _terms(machine, "out") < 1 or _terms(machine, "run") < 1:
                assert time.monotonic() < deadline, "a process was not asked to stop"
                time.sleep(0.05)
            time.sleep(0.5)
            assert (_terms(machine, "run"), _terms(machine, "out")) == (1, 1)
        finally:
            _killed(left)
            every._group_killed(job.handle)

    def test_what_an_ended_job_left_is_asked_once_until_it_has_had_time(self, machine):
        """A second cancel a few seconds after the first sent TERM again,
        stopping the run at once while saying it stops at its next frame."""
        _leaving_trapped(machine)
        job = _send(machine)
        left = _left_pid(machine)
        try:
            travels._until_finished(machine, job.name)
            cancel(job.name, transport=machine.transport())
            every._until_there(machine.home / "terms_out")
            said = status(job.name, transport=machine.transport())
            assert "asked to stop from here at its run's next frame" in said.detail
            assert "`fastmdx remote cancel" not in said.detail
            again = cancel(job.name, transport=machine.transport())
            time.sleep(0.5)
            assert _terms(machine, "out") == 1
            assert again.state == "failed"
            # A forced send says what is under way.
            sending = prepare(machine.study, "box", output=str(machine.back), force=True,
                              code=RELEASE, transport=machine.transport())
            with pytest.raises(ValueError) as caught:
                send(sending, transport=machine.transport(), local_runner=machine.local,
                     code=RELEASE)
            assert ("what trial left going was asked to stop from here, and stops at "
                    "its run's next frame") in str(caught.value)
        finally:
            _killed(left)


def _answer_lost(machine, when: str):
    """The machine's connection, which runs a command holding ``when`` and
    then is cut (ssh's 255): what was sent went, its answer did not come."""
    from fastmdxplora.remote.transport import Transport

    def runner(command, input=None, **kwargs):
        done = machine.ssh(command, input=input, **kwargs)
        said = (input.decode() if isinstance(input, bytes) else input or "") + command[-1]
        if when in said:
            return subprocess.CompletedProcess(done.args, 255, b"", b"Connection reset")
        return done
    return Transport("box", runner=runner, interactive=False)


@pytest.mark.skipif(not shutil.which("setsid"), reason="starts a process out of a group")
class TestAStopWhoseAnswerIsLost:
    def test_a_running_job_s_cancel_is_not_sent_twice(self, machine):
        travels._tool(machine.home.joinpath(*every._FASTMDX), _COUNTS_ITS_STOPS)
        job = _send(machine)
        left = _left_pid(machine)
        try:
            time.sleep(0.5)
            with pytest.raises(ValueError):
                cancel(job.name, transport=_answer_lost(machine, "kill -TERM -"))
            every._until_there(machine.home / "terms_out")
            again = cancel(job.name, transport=machine.transport())
            assert again.state == "abandoned"
            time.sleep(0.5)
            assert (_terms(machine, "run"), _terms(machine, "out")) == (1, 1)
        finally:
            _killed(left)
            every._group_killed(job.handle)

    def test_a_stop_that_never_ran_is_sent_as_the_first(self, machine):
        """The first cancel's connection failed before its command ran: a
        cancel again within ten minutes recorded the job cancelled, and sent
        nothing; its run went on unseen."""
        from fastmdxplora.remote.transport import Transport

        def never_ran(command, input=None, **kwargs):
            said = (input.decode() if isinstance(input, bytes) else input or "")
            if "kill -TERM -" in said:
                return subprocess.CompletedProcess(command, 255, b"", b"Connection reset")
            return machine.ssh(command, input=input, **kwargs)

        travels._tool(machine.home.joinpath(*every._FASTMDX), _COUNTS_ITS_STOPS)
        job = _send(machine)
        left = _left_pid(machine)
        try:
            time.sleep(0.5)
            with pytest.raises(ValueError):
                cancel(job.name, transport=Transport("box", runner=never_ran,
                                                     interactive=False))
            time.sleep(0.3)
            assert (_terms(machine, "run"), _terms(machine, "out")) == (0, 0)
            again = cancel(job.name, transport=machine.transport())
            assert again.state == "abandoned" and again.detail.startswith("cancelled")
            every._until_there(machine.home / "terms_out")
            time.sleep(0.5)
            assert (_terms(machine, "run"), _terms(machine, "out")) == (1, 1)
        finally:
            _killed(left)
            every._group_killed(job.handle)

    def test_a_running_cancel_s_stop_counts_for_what_it_left(self, machine):
        """A cancel's TERM landed and its answer was lost; the run then ended
        and left a process: a cancel two seconds later sent it a second TERM."""
        _leaving_trapped(machine)
        machine.env["FAKE_SLEEP"] = "2"
        travels._tool(machine.home.joinpath(*every._FASTMDX), _LEAVES_ONE.replace(
            "$FAKE_LEFT sleep 60", "setsid sh -c 'trap \"echo t >> \\\"$HOME/terms_out"
            "\\\"\" TERM; while :; do sleep 0.1; done'").replace(
            'exit "$FAKE_EXIT"', 'sleep "$FAKE_SLEEP"; exit "$FAKE_EXIT"'))
        job = _send(machine)
        left = _left_pid(machine)
        try:
            with pytest.raises(ValueError):
                cancel(job.name, transport=_answer_lost(machine, "kill -TERM -"))
            every._until_there(machine.home / "terms_out")
            from fastmdxplora.remote.jobs import load_job, save_job

            record = load_job(job.name)
            record.state, record.extra["left_going"] = "failed", 1
            save_job(record)
            done = cancel(job.name, transport=machine.transport())
            assert done.extra["last_stop"]["outcome"] == "stopping"
            time.sleep(0.5)
            assert _terms(machine, "out") == 1
        finally:
            _killed(left)
            every._group_killed(job.handle)

    def test_what_an_ended_job_left_is_not_asked_twice(self, machine):
        _leaving_trapped(machine)
        job = _send(machine)
        left = _left_pid(machine)
        try:
            travels._until_finished(machine, job.name)
            with pytest.raises(ValueError):
                cancel(job.name, transport=_answer_lost(machine, "kill -TERM $pids"))
            every._until_there(machine.home / "terms_out")
            cancel(job.name, transport=machine.transport())
            time.sleep(0.5)
            assert _terms(machine, "out") == 1
            assert "asked to stop from here at its run's next frame" in status(
                job.name, transport=machine.transport()).detail
        finally:
            _killed(left)


@pytest.mark.skipif(not shutil.which("setsid"), reason="starts a process out of a group")
class TestAStopLongAsked:
    def test_after_the_window_a_cancel_stops_them_at_once_and_says_so(self, machine):
        """Ten minutes after a stop asked, status said "cancel stops them",
        and a cancel said they stop at their next frame: a second TERM stops
        a run at once, without its checkpoint."""
        import re

        _leaving_trapped(machine)
        job = _send(machine)
        left = _left_pid(machine)
        try:
            travels._until_finished(machine, job.name)
            cancel(job.name, transport=machine.transport())
            every._until_there(machine.home / "terms_out")
            _aged(machine, job, 11 * 60)
            said = status(job.name, transport=machine.transport())
            assert re.search(r"stops (it|them) now, without a checkpoint", said.detail)
            stopped = cancel(job.name, transport=machine.transport())
            assert "what it left going was stopped at once" in stopped.detail
            deadline = time.monotonic() + 10
            while _terms(machine, "out") < 2:
                assert time.monotonic() < deadline
                time.sleep(0.05)
        finally:
            _killed(left)


def _aged(machine, job, seconds: float) -> None:
    """The stops asked of ``job``, on the machine and here, made ``seconds``
    older."""
    from fastmdxplora.remote.jobs import load_job, save_job

    for mark in Path(job.remote_dir).glob(".fmdx-stop-*"):
        then = mark.stat().st_mtime - seconds
        os.utime(mark, (then, then))
    record = load_job(job.name)
    record.extra["left_stopped_at"] = record.extra["left_stopped_at"] - seconds
    save_job(record)


@pytest.mark.skipif(not shutil.which("setsid"), reason="starts a process out of a group")
class TestACancelledJobsRunThatGoesOn:
    def test_a_cancel_after_the_window_stops_it_at_once(self, machine):
        """A cancelled job's record was final: a run that outlived its stop
        could not be stopped from here again."""
        travels._tool(machine.home.joinpath(*every._FASTMDX), _COUNTS_ITS_STOPS)
        job = _send(machine)
        left = _left_pid(machine)
        try:
            time.sleep(0.5)
            assert cancel(job.name, transport=machine.transport()).state == "abandoned"
            every._until_there(machine.home / "terms_out")
            # Within the window, nothing more.
            again = cancel(job.name, transport=machine.transport())
            assert again.extra["last_stop"]["outcome"] == "stopping"
            for mark in Path(job.remote_dir).glob(".fmdx-stop-*"):
                then = mark.stat().st_mtime - 11 * 60
                os.utime(mark, (then, then))
            later = cancel(job.name, transport=machine.transport())
            assert later.extra["last_stop"]["outcome"] == "now"
            assert later.detail.startswith("stopped at once")
            deadline = time.monotonic() + 10
            while _terms(machine, "out") < 2:
                assert time.monotonic() < deadline
                time.sleep(0.05)
        finally:
            _killed(left)
            every._group_killed(job.handle)


class TestACancelAskedAgain:
    def test_a_cancelled_job_whose_folder_is_gone_stays_cancelled(self, machine):
        """Asked again where nothing could be asked, a cancel of a cancelled
        job failed every time, saying to cancel it again."""
        machine.env["FAKE_SLEEP"] = "30"
        job = _send(machine)
        try:
            time.sleep(0.3)
            assert cancel(job.name, transport=machine.transport()).state == "abandoned"
            shutil.rmtree(job.remote_dir)
            assert cancel(job.name, transport=machine.transport()).state == "abandoned"
        finally:
            every._group_killed(job.handle)

    @pytest.mark.skipif(not shutil.which("setsid"), reason="starts a process out of a group")
    def test_a_stop_made_just_after_another_is_that_one(self, machine, tmp_path):
        """Two cancels at once from this computer: the second looked before
        the first made its stop, then made the next and sent a second TERM,
        said as a stop at once."""
        travels._tool(machine.home.joinpath(*every._FASTMDX), _COUNTS_ITS_STOPS)
        job = _send(machine)
        left = _left_pid(machine)
        try:
            time.sleep(0.5)
            cancel(job.name, transport=machine.transport())
            every._until_there(machine.home / "terms_out")
            # The second cancel's first look finds nothing, as one that
            # looked before the first made its stop would.
            shims = tmp_path / "racing-bin"
            shims.mkdir()
            real = shutil.which("find")
            travels._tool(shims / "find", f'''
case "$*" in *"-*"*) exit 0 ;; esac
exec {real} "$@"''')
            machine.env["PATH"] = f"{shims}:{machine.env['PATH']}"
            from fastmdxplora.remote.jobs import load_job, save_job

            record = load_job(job.name)
            record.state, record.extra["left_going"] = "failed", 1
            save_job(record)
            again = cancel(job.name, transport=machine.transport())
            assert again.extra["last_stop"]["outcome"] == "stopping"
            time.sleep(0.5)
            assert _terms(machine, "out") == 1
        finally:
            _killed(left)
            every._group_killed(job.handle)


class TestAnEndedRecord:
    def test_its_cancel_does_not_ask_the_machine_or_signal_its_old_number(self, machine):
        """A cancel of a job read ended asked the machine again; with its exit
        code gone (a restart) and its number now another process's, it read
        running and that process's group was sent TERM."""
        job = _send(machine)
        job = travels._until_finished(machine, job.name)
        assert job.state == "done"
        (Path(job.remote_dir) / "exit_code").unlink()
        stranger = subprocess.Popen(["sleep", "60"], start_new_session=True)
        try:
            from fastmdxplora.remote.jobs import load_job, save_job

            record = load_job(job.name)
            record.handle = str(stranger.pid)
            save_job(record)
            asked = len(machine.commands)
            assert cancel(job.name, transport=machine.transport()).state == "done"
            assert len(machine.commands) == asked
            time.sleep(0.3)
            assert every._process_there(str(stranger.pid))
        finally:
            stranger.kill()
            stranger.wait()

    def test_a_running_record_s_number_now_another_s_is_not_its_job(self, machine):
        """After a restart (no exit code written) the job's number may be
        another process's: it read running, and a cancel sent TERM to that
        process's group."""
        from fastmdxplora.remote.jobs import Job, save_job

        where = machine.home / "fastmdxplora-jobs" / "restarted"
        (where / "run").mkdir(parents=True)
        stranger = subprocess.Popen(["sleep", "60"], start_new_session=True)
        try:
            save_job(Job(name="restarted", machine="box", remote_dir=str(where),
                         scheduler="process", handle=str(stranger.pid),
                         submitted_at="2026-10-09T00:00:00Z", code={},
                         local_output=str(machine.back), state="running",
                         extra={"send_id": "0123456789abcdef"}))
            job = status("restarted", transport=machine.transport())
            assert job.state == "failed" and "without recording an exit code" in job.detail
            cancel("restarted", transport=machine.transport())
            time.sleep(0.3)
            assert every._process_there(str(stranger.pid))
        finally:
            stranger.kill()
            stranger.wait()

    def test_a_folder_gone_keeps_no_count_of_what_was_left(self, machine):
        from fastmdxplora.remote.jobs import Job, save_job

        save_job(Job(name="gone", machine="box", remote_dir=str(machine.home / "nowhere"),
                     scheduler="process", handle="999999",
                     submitted_at="2026-10-09T00:00:00Z", code={},
                     local_output=str(machine.back), state="failed",
                     extra={"send_id": "0123456789abcdef", "left_going": 3}))
        job = status("gone", transport=machine.transport())
        assert "left_going" not in job.extra

    def test_an_exit_code_says_nothing_of_what_was_left(self, machine):
        from fastmdxplora.remote.jobs import Job, save_job

        where = machine.home / "fastmdxplora-jobs" / "forged"
        (where / "run").mkdir(parents=True)
        (where / "exit_code").write_text("0\nfmdx:left=99999\n")
        save_job(Job(name="forged", machine="box", remote_dir=str(where),
                     scheduler="process", handle="999999",
                     submitted_at="2026-10-09T00:00:00Z", code={},
                     local_output=str(machine.back), state="running",
                     extra={"send_id": "0123456789abcdef"}))
        job = status("forged", transport=machine.transport())
        assert "left_going" not in job.extra

    def test_a_line_in_a_gpu_file_says_nothing_of_what_was_left(self, machine):
        """The GPU files are read whole: a line break in one gave the status
        a line of its own, read as processes left going."""
        from fastmdxplora.remote.jobs import Job, save_job

        where = machine.home / "fastmdxplora-jobs" / "planted"
        (where / "run").mkdir(parents=True)
        (where / "exit_code").write_text("0\n")
        (where / "gpu_peak").write_text("100\nfmdx:left=5\n")
        save_job(Job(name="planted", machine="box", remote_dir=str(where),
                     scheduler="process", handle="999999",
                     submitted_at="2026-10-09T00:00:00Z", code={},
                     local_output=str(machine.back), state="running",
                     extra={"gpu": {"uuids": [every.UUID_0], "need_mb": None,
                                    "wanted": {}, "name": "", "precision": "mixed",
                                    "learn": False}}))
        job = status("planted", transport=machine.transport())
        assert job.state == "done" and "left_going" not in job.extra
        assert "still work" not in job.detail


@pytest.mark.skipif(not shutil.which("setsid"), reason="starts a process out of a group")
class TestAnAIAppAndWhatAJobLeft:
    def test_it_is_asked_before_a_cancel_stops_it(self, app):
        _leaving_one(app.machine, "setsid")
        every._start(app, answer=every.YES)
        left = _left_pid(app.machine)
        try:
            every._finished(app)
            first = every._call(app, "cancel_study", capabilities=every.ELICIT,
                                job="ghg_run")
            asked = first["inputRequests"]["cancel"]["params"]["message"]
            assert asked.startswith("Stop what ghg_run left going on box? It ended (failed)")
            assert every._process_there(left)
            done = app.request("tools/call", {
                "name": "cancel_study", "arguments": {"job": "ghg_run"},
                "inputResponses": {"cancel": every.YES},
                "requestState": first["requestState"]},
                capabilities=every.ELICIT)["result"]
            assert every._text(done).startswith("Asked what ghg_run left going on box to "
                                                "stop")
            assert _gone(left)
        finally:
            _killed(left)

    def test_what_changed_while_the_person_was_asked_is_not_stopped(
            self, app, monkeypatch):
        """An AI app that asks and waits for the answer: a stop asked from
        elsewhere meanwhile, and the yes stopped them again, at once."""
        from fastmdxplora.mcp import remote_tools
        from fastmdxplora.remote import api

        _leaving_trapped(app.machine)
        every._start(app, answer=every.YES)
        left = _left_pid(app.machine)
        try:
            every._finished(app)

            def stopped_elsewhere_then_yes(ctx, key, message, bound_to):
                api.cancel("ghg_run")
                return True

            monkeypatch.setattr(remote_tools, "_went_ahead", stopped_elsewhere_then_yes)
            said = every._text(every._call(app, "cancel_study", job="ghg_run"))
            assert said.startswith("ghg_run changed while the person was asked")
            every._until_there(app.machine.home / "terms_out")
            time.sleep(0.5)
            assert _terms(app.machine, "out") == 1
        finally:
            _killed(left)

    def test_an_ended_job_with_nothing_left_asks_nothing(self, app):
        every._start(app, answer=every.YES)
        every._finished(app)
        said = every._call(app, "cancel_study", capabilities=every.ELICIT, job="ghg_run")
        assert every._text(said) == "ghg_run has ended already (done)."


# ---------------------------------------------------------------------------
# What works in a job's folder, and a cluster's queue
# ---------------------------------------------------------------------------
class TestWhatWorksInTheFolder:
    @pytest.mark.parametrize("command", [["tail", "-f", "job.sh"],
                                         ["sh", "-c", "sleep 30", "fastmdxplora-jobs"]])
    def test_a_reader_or_a_command_naming_the_script_does_not_refuse_a_send(
            self, machine, command):
        """A process only naming `job.sh` or `fastmdx` (a pager, a path naming
        `fastmdxplora-jobs`), working in an ended job's folder, refused every
        send there."""
        first = _send(machine)
        travels._until_finished(machine, first.name)
        reader = subprocess.Popen(command, cwd=first.remote_dir,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            again = prepare(machine.study, "box", output=str(machine.back), force=True,
                            code=RELEASE, transport=machine.transport())
            job = send(again, transport=machine.transport(), local_runner=machine.local,
                       code=RELEASE)
            travels._until_finished(machine, job.name)
        finally:
            reader.kill()
            reader.wait()

    def test_a_worker_of_a_job_sent_before_the_marker_refuses_a_send(self, machine):
        """A spawn worker names neither the script nor `fastmdx`; one working
        in the folder of a job sent before `FMDX_JOB_DIR` was not found."""
        first = _send(machine)
        travels._until_finished(machine, first.name)
        env = {k: v for k, v in os.environ.items()
               if k not in ("FMDX_JOB_DIR", "FMDX_SEND_ID")}
        worker = subprocess.Popen(
            ["sh", "-c", "sleep 30", "python", "--multiprocessing-fork"],
            cwd=first.remote_dir, env=env)
        try:
            again = prepare(machine.study, "box", output=str(machine.back), force=True,
                            code=RELEASE, transport=machine.transport())
            with pytest.raises(ValueError) as caught:
                send(again, transport=machine.transport(), local_runner=machine.local,
                     code=RELEASE)
            said = str(caught.value)
            assert refusal_of(caught.value).code == "environment.path.exists"
            assert "a shell left" not in said
            assert "(a run of a job sent there, from here or elsewhere)" in said
        finally:
            worker.kill()
            worker.wait()


class TestTheQueue:
    def test_a_folder_reached_through_a_link_is_the_queue_s_folder(self, machine, tmp_path):
        """sbatch keeps the folder as it resolves it (`/scratch` into Lustre):
        compared as text with a folder reached through a link, the job
        waiting there was never found."""
        sending = every._cluster_sending(machine, force=True)
        real = tmp_path / "lustre" / "trial"
        real.mkdir(parents=True)
        where = Path(sending.remote_dir)
        where.parent.mkdir(parents=True, exist_ok=True)
        where.symlink_to(real, target_is_directory=True)
        machine.env["QUEUED_IN"] = str(real.resolve())
        with pytest.raises(ValueError) as caught:
            send(sending, transport=machine.transport(), local_runner=machine.local,
                 code=RELEASE)
        assert refusal_of(caught.value).code == "environment.path.exists"
        # Its number named, to cancel it with.
        assert "(`scancel 4300`)" in str(caught.value)
        assert not (machine.home / "submitted").exists()


class TestAnAIAppIsToldToTryAgain:
    def test_a_silent_queue_s_refusal_keeps_its_minute(self):
        from fastmdxplora.mcp.remote_tools import _refuse_taken_folder
        from fastmdxplora.mcp.tools import ToolError

        silent = StudyError(
            "The queue of box did not answer, so whether a job called trial is waiting "
            "or running there is not known. Send it again in a minute, or give another "
            "--output.", code="environment.path.exists", path="trial")
        with pytest.raises(ToolError) as caught:
            _refuse_taken_folder(silent, "box")
        said = str(caught.value)
        assert said.endswith("Set `output` in the config to a new folder name, save it "
                             "and check it again, or start it again in a minute.")
        assert "--output" not in said

    def test_the_queue_s_job_is_named_with_its_number(self):
        from fastmdxplora.mcp.remote_tools import _refuse_taken_folder
        from fastmdxplora.mcp.tools import ToolError

        held = StudyError(
            "The queue of box holds a job called trial (pending) in its folder there, "
            "/scratch/fastmdxplora-jobs/trial; a job is sent there again once that one "
            "has ended. Cancel it there (`scancel 4300`), or give another --output.",
            code="environment.path.exists", path="trial")
        with pytest.raises(ToolError) as caught:
            _refuse_taken_folder(held, "box")
        said = str(caught.value)
        assert "Cancel it there (`scancel 4300`). Set `output` in the config" in said
        assert "--output" not in said
