"""The GPU shakedown reports what happened, including when a run failed.

It threw away what explore() returned, so a study whose phases failed
printed its minutes like any other, the script went on, and the empty
results were explained as physics: no energy record read as "constant
volume". A later segment with no log was passed over in silence. And the
constant-volume control ran production at constant pressure. Each is
driven here through main(), with the studies stood in so the outcome of
each is set exactly.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from fastmdxplora.orchestrator import PhaseResult, RunResult

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "gpu_shakedown.py"


def _the_script():
    spec = importlib.util.spec_from_file_location("gpu_shakedown", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _energy(folder: Path, volumes=None) -> None:
    simulation = folder / "simulation"
    simulation.mkdir(parents=True, exist_ok=True)
    header = "step,Potential Energy (kJ/mole)" + (",Box Volume (nm^3)" if volumes else "")
    rows = [f"{i},-1.0" + (f",{v}" if volumes else "") for i, v in
            enumerate(volumes or [0.0] * 10)]
    simulation.joinpath("energy.csv").write_text("\n".join([header, *rows]) + "\n",
                                                encoding="utf-8")


def _drive(tmp_path, monkeypatch, capsys, *, outcome, argon=1e-9):
    """Run main() with each study replaced by `outcome(output_dir)`, which
    writes whatever that run should leave and returns what explore()
    would. Returns main's exit code and what it printed."""
    import fastmdxplora
    from fastmdxplora import cost
    from fastmdxplora.simulation import resume

    class Study:
        def __init__(self, *, config_data, output_dir):
            self.output = Path(output_dir)

        def explore(self):
            self.output.mkdir(parents=True, exist_ok=True)
            return outcome(self.output)

    monkeypatch.setattr(fastmdxplora, "FastMDXplora", Study)
    monkeypatch.setattr(cost, "measure_this_machine",
                        lambda **kw: SimpleNamespace(seconds_per_particle_step=argon))
    monkeypatch.setattr(cost, "calibrate_from_runs",
                        lambda *a, **kw: SimpleNamespace(seconds_per_particle_step=1e-9,
                                                         spread=1.1, runs=4))
    monkeypatch.setattr(resume, "segmentability",
                        lambda config: SimpleNamespace(allowed=True, method="checkpoint",
                                                       qualification=""))
    code = _the_script().main(["1UBQ", "--output", str(tmp_path / "shakedown"),
                               "--platform", "CPU", "--ns", "0.01", "--segments", "3",
                               "--nvt-steps", "200", "--npt-steps", "200"])
    return code, capsys.readouterr().out


def _ok(output: Path):
    return [RunResult(run_id="r", system="1UBQ", status="ok",
                      phases=[PhaseResult(name="simulation", status="ok")])]


def test_a_failed_study_stops_the_shakedown_and_says_where(tmp_path, monkeypatch, capsys) -> None:
    def setup_fails(output):
        return [RunResult(run_id="r", system="1UBQ", status="error", phases=[
            PhaseResult(name="setup", status="error",
                        message="The force field has no parameters for LIG.")])]

    code, said = _drive(tmp_path, monkeypatch, capsys, outcome=setup_fails)
    assert code == 1
    assert "FAILED in setup: The force field has no parameters for LIG." in said
    assert "3. The same study in" not in said and "5. What a join cost" not in said
    written = json.loads((tmp_path / "shakedown" / "shakedown.json").read_text(encoding="utf-8"))
    assert written["failed"]["phase"] == "setup"


def test_a_segment_with_no_log_is_said_to_be_unknown(tmp_path, monkeypatch, capsys) -> None:
    def segments(output):
        _energy(output, volumes=[130.0 + 0.01 * i for i in range(10)])
        if output.name == "seg1":
            (output / "fastmdxplora.log").write_text("Resumed from checkpoint.chk\n",
                                                     encoding="utf-8")
        return _ok(output)

    code, said = _drive(tmp_path, monkeypatch, capsys, outcome=segments)
    assert code == 0
    assert "segment 1: resumed" in said
    assert "segment 2: wrote no log, so whether it resumed is unknown" in said


@pytest.mark.parametrize("case, expected", [
    ("no energy record", "did not reach production"),
    ("no rows", "shorter than the state interval"),
    ("no volume column", "production at constant volume"),
    ("volume held constant", "held at 34.763 nm^3 on both sides"),
    ("box changed", "the box changed across the join"),
    ("one segment", "no join to measure"),
])
def test_the_join_says_why_it_was_not_measured(tmp_path, capsys, case, expected) -> None:
    script = _the_script()
    first, second = tmp_path / "seg0", tmp_path / "seg1"
    if case == "no energy record":
        _energy(first, volumes=[130.0] * 10)
        directories = [first, second]
    elif case == "no rows":
        # What a rehearsal wrote: segments shorter than the state interval
        # leave an energy record with no rows, which is not constant volume.
        _energy(first, volumes=[130.0] * 10)
        (second / "simulation").mkdir(parents=True)
        (second / "simulation" / "energy.csv").write_text("", encoding="utf-8")
        directories = [first, second]
    elif case in ("volume held constant", "box changed"):
        # What a constant-volume run writes: a volume column with one value.
        _energy(first, volumes=[34.763] * 10)
        _energy(second, volumes=[34.763 if case == "volume held constant" else 35.1] * 10)
        directories = [first, second]
    elif case == "no volume column":
        _energy(first)
        _energy(second)
        directories = [first, second]
    else:
        _energy(first, volumes=[130.0] * 10)
        directories = [first]
    findings: dict = {}
    script._report_the_join(tmp_path, directories, findings)
    said = capsys.readouterr().out
    assert expected in said
    assert "sd 1.00" not in said
    assert ("constant volume" in said) == (case in ("no volume column", "volume held constant"))


def test_the_state_interval_can_be_set() -> None:
    script = _the_script()
    args = script.parse(["1UBQ", "--state-interval", "100"])
    assert script.study(args, production_ns=1.0)["simulation"]["state_interval_steps"] == 100
    unset = script.study(script.parse(["1UBQ"]), production_ns=1.0)["simulation"]
    assert "state_interval_steps" not in unset


def test_the_control_runs_production_at_constant_volume() -> None:
    from fastmdxplora.simulation.runner import resolve_ensemble

    script = _the_script()
    for pressure, ensemble in (("0", "nvt"), ("1.0", "npt")):
        args = script.parse(["1UBQ", "--pressure-bar", pressure])
        simulation = script.study(args, production_ns=1.0)["simulation"]
        assert resolve_ensemble(simulation) == ensemble, pressure


def test_the_argon_miss_is_a_ratio_with_its_consequence(tmp_path, monkeypatch, capsys) -> None:
    # "Out by 62%" hid which way and how far: argon at 0.38 of what the runs
    # measured makes every estimate from it 2.6 times too low.
    def segments(output):
        _energy(output, volumes=[130.0] * 10)
        (output / "fastmdxplora.log").write_text("Resumed from x\n", encoding="utf-8")
        return _ok(output)

    code, said = _drive(tmp_path, monkeypatch, capsys, outcome=segments, argon=0.38e-9)
    assert code == 0
    assert "argon predicted 0.38x the cost these runs measured" in said
    assert "would be 2.6 times too low" in said


def test_every_join_is_measured(tmp_path, capsys) -> None:
    # Three segments are two joins. The second one settles for three frames;
    # reading only the first join would never see it.
    script = _the_script()
    steady = [130.0 + 0.01 * (i % 3) for i in range(20)]
    directories = [tmp_path / f"seg{i}" for i in range(3)]
    _energy(directories[0], volumes=steady)
    _energy(directories[1], volumes=steady)
    _energy(directories[2], volumes=[133.0, 132.5, 131.0] + steady[3:])
    findings: dict = {}
    script._report_the_join(tmp_path, directories, findings)
    said = capsys.readouterr().out
    assert "join 1 (seg0 to seg1)" in said and "join 2 (seg1 to seg2)" in said
    assert [j["frames_settling"] for j in findings["joins"]] == [0, 3]
