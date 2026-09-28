"""One umbrella window runs again in place, and the rest are kept.

A campaign that refuses because one window slid off its centre could only be
run again whole (`--force-overwrite` redoes all thirty) or as a subset
config, which renumbered from `window_00` and so could not land in the
window it was meant to replace. `--rerun-window N` runs the windows named
with whatever the config now gives them, keeps the others, and recombines.

Kept windows must have run with what the config gives them, since each is
unbiased with the spring it is given: that is checked before anything moves,
and before every recombination.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from fastmdxplora.batch import explorer as explorer_module
from fastmdxplora.batch.explorer import BatchExplorer
from fastmdxplora.orchestrator import RunResult
from fastmdxplora.refusals import StudyError

CENTRES = [0.3, 0.35, 0.4, 0.45, 0.5]


def _config(tmp_path: Path, forces: "list[float]") -> Path:
    prepared = tmp_path / "prepared"
    prepared.mkdir(exist_ok=True)
    for name in ("system.xml", "state.xml", "topology.pdb"):
        (prepared / name).write_text("x", encoding="utf-8")
    path = tmp_path / "study.yml"
    path.write_text(
        "systems:\n  - system: 181L\n"
        "simulation:\n"
        f"  setup_from: {prepared}\n"
        "  umbrella:\n"
        "    collective_variable: ligand_distance\n"
        '    site_selection: "resid 84 to 121 and name CA"\n'
        f"    centres: {CENTRES}\n"
        f"    force_constant: {forces}\n"
        "report:\n  comparison: false\n",
        encoding="utf-8")
    return path


def _stand_in(ran: list[int]):
    """A window as a real run leaves it: its COLVAR, its record, its config.
    A window being resumed that has a config is left as it is."""
    def run(spec, run_out, include, exclude, verbose, device, quiet=True,
            force=False, resume=False):
        out = Path(run_out)
        block = spec["options"]["simulation"]["umbrella"]
        if resume and (out / "resolved_config.yml").is_file():
            return RunResult(run_id=spec["run_id"], system="181L", status="ok",
                             output_dir=out, message="resumed: nothing")
        ran.append(int(block["index"]))
        simulation = out / "simulation"
        simulation.mkdir(parents=True, exist_ok=True)
        rng = np.random.default_rng(int(block["index"]))
        spread = (2.494 / float(block["force_constant"])) ** 0.5
        values = rng.normal(float(block["centre"]), spread, 1500)
        (simulation / "COLVAR").write_text(
            "#! FIELDS time cv restraint.bias\n"
            + "\n".join(f"{i * 0.1:.1f} {v:.6f} 0.0" for i, v in enumerate(values)),
            encoding="utf-8")
        (simulation / "umbrella_window.json").write_text(json.dumps({
            "index": block["index"], "centre": block["centre"],
            "force_constant": block["force_constant"]}), encoding="utf-8")
        (out / "resolved_config.yml").write_text("x: 1\n", encoding="utf-8")
        return RunResult(run_id=spec["run_id"], system="181L", status="ok",
                         output_dir=out)
    return run


@pytest.fixture
def study(tmp_path, monkeypatch):
    """A study whose five windows have all run once."""
    ran: list[int] = []
    monkeypatch.setattr(explorer_module, "_execute_run", _stand_in(ran))
    monkeypatch.setattr(BatchExplorer, "_maybe_build_comparison", lambda self: None)
    out = tmp_path / "out"
    BatchExplorer(config=_config(tmp_path, [2000] * 5), output_dir=str(out)).run()
    assert sorted(ran) == [0, 1, 2, 3, 4]
    ran.clear()
    return SimpleNamespace(root=tmp_path, out=out, ran=ran)


def _window(out: Path, index: int) -> Path:
    return next((out / "runs").glob(f"window?{index:02d}"))


class TestOneWindowRunsAgain:

    def test_only_the_window_named_runs_and_the_free_energy_uses_it(self, study) -> None:
        stiffer = [2000, 2000, 6000, 2000, 2000]
        BatchExplorer(config=_config(study.root, stiffer), output_dir=str(study.out),
                      rerun_windows=[2]).run()
        assert study.ran == [2]
        record = json.loads((_window(study.out, 2) / "simulation"
                             / "umbrella_window.json").read_text(encoding="utf-8"))
        assert record["force_constant"] == 6000
        pmf = json.loads((study.out / "pmf.json").read_text(encoding="utf-8"))
        assert pmf["plan"]["force_constants"] == stiffer
        assert pmf.get("pmf") and not pmf.get("refused")

    def test_the_earlier_run_is_kept_aside(self, study) -> None:
        BatchExplorer(config=_config(study.root, [2000] * 5), output_dir=str(study.out),
                      rerun_windows=[2]).run()
        aside = list((study.out / "superseded").iterdir())
        assert len(aside) == 1 and aside[0].name.startswith(_window(study.out, 2).name)
        assert (aside[0] / "simulation" / "COLVAR").is_file()


class TestWhatIsRefusedBeforeAnythingMoves:

    def _refused(self, study, forces, windows, **kwargs) -> StudyError:
        with pytest.raises(StudyError) as refused:
            BatchExplorer(config=_config(study.root, forces), output_dir=str(study.out),
                          rerun_windows=windows, **kwargs).run()
        assert not (study.out / "superseded").exists()
        assert study.ran == []
        return refused.value

    def test_a_kept_window_the_config_now_describes_otherwise(self, study) -> None:
        refused = self._refused(study, [2000, 2000, 6000, 3000, 2000], [2])
        assert "window 3 ran at 0.45 nm with 2000" in str(refused)

    def test_a_window_the_study_does_not_have(self, study) -> None:
        refused = self._refused(study, [2000] * 5, [7])
        assert "no window 7" in str(refused) and "0 to 4" in str(refused)

    def test_a_kept_window_that_never_finished(self, study) -> None:
        (_window(study.out, 4) / "simulation" / "COLVAR").unlink()
        refused = self._refused(study, [2000] * 5, [2])
        assert "Windows 4 have no production" in str(refused)

    def test_force_as_well(self, study) -> None:
        with pytest.raises(StudyError, match="Choose one"):
            BatchExplorer(config=_config(study.root, [2000] * 5),
                          output_dir=str(study.out), rerun_windows=[2], force=True)


def test_a_study_without_windows_has_none_to_run_again(tmp_path) -> None:
    path = tmp_path / "plain.yml"
    path.write_text("systems:\n  - system: 181L\n  - system: 1UBQ\n", encoding="utf-8")
    with pytest.raises(StudyError, match="has none"):
        BatchExplorer(config=path, output_dir=str(tmp_path / "out"),
                      rerun_windows=[0]).run()


def test_a_config_edited_after_the_windows_ran_is_not_recombined(study) -> None:
    """Without --rerun-window as well: the recombination checks each window's
    own record against the config."""
    explorer = BatchExplorer(config=_config(study.root, [2000, 2000, 2000, 2000, 5000]),
                             output_dir=str(study.out))
    explorer._maybe_build_pmf(bootstrap_resamples=0)
    pmf = json.loads((study.out / "pmf.json").read_text(encoding="utf-8"))
    assert pmf["pmf"] is None
    assert "window 4 ran at 0.5 nm with 2000" in pmf["refused"]


def test_the_command_line_takes_it(tmp_path) -> None:
    from fastmdxplora.cli.main import _build_parser

    args = _build_parser().parse_args(
        ["explore", "--config", "study.yml", "--rerun-window", "3", "--rerun-window", "5", "6"])
    assert args.rerun_windows == [3, 5, 6]


def test_a_window_still_running_is_not_moved(study, monkeypatch) -> None:
    from fastmdxplora.simulation import resume

    monkeypatch.setattr(resume, "_still_running", lambda where: where.name.endswith("03"))
    with pytest.raises(StudyError, match="window 3 is still running"):
        BatchExplorer(config=_config(study.root, [2000] * 5), output_dir=str(study.out),
                      rerun_windows=[2]).run()
    assert not (study.out / "superseded").exists()


def test_a_window_that_never_ran_is_run(study) -> None:
    import shutil

    shutil.rmtree(_window(study.out, 2))
    BatchExplorer(config=_config(study.root, [2000] * 5), output_dir=str(study.out),
                  rerun_windows=[2]).run()
    assert study.ran == [2] and not (study.out / "superseded").exists()


def test_a_record_that_cannot_be_read_is_not_judged(tmp_path) -> None:
    from fastmdxplora.simulation.umbrella import UmbrellaPlan, Window, windows_run_otherwise

    (tmp_path / "simulation").mkdir()
    (tmp_path / "simulation" / "umbrella_window.json").write_text("{", encoding="utf-8")
    plan = UmbrellaPlan(windows=(Window(0, 0.3, 1000.0), Window(1, 0.4, 1000.0)),
                        collective_variable="ligand_distance")
    assert windows_run_otherwise({0: tmp_path}, plan) == []


def test_a_study_of_one_system_has_no_windows(tmp_path) -> None:
    from fastmdxplora import FastMDXplora

    with pytest.raises(StudyError, match="has none"):
        FastMDXplora(system=str(tmp_path / "x.pdb"),
                     output_dir=str(tmp_path / "out")).explore(rerun_windows=[1])
