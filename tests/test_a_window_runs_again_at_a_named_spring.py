"""A window runs again at a force constant the person names.

`--rerun-window N` ran a window again with whatever the config gave it, so
holding one window harder meant rewriting `force_constant` as a list of
thirty by hand, with the other twenty-nine as they ran. `--rerun-force-
constant K` does that: the windows named are held at K and every other
window keeps its own, the study's config records the list, and the next
recombination, or the next window run again from that config, reads the
same springs the windows ran with.
"""

from __future__ import annotations

import json

import pytest

from fastmdxplora.batch import explorer as explorer_module
from fastmdxplora.batch.explorer import BatchExplorer
from fastmdxplora.refusals import StudyError
from fastmdxplora.simulation.umbrella import windows_held_at
from tests.test_a_window_runs_again_in_place import _config, _stand_in, _window


def _umbrella(**block) -> dict:
    return {"systems": [{"system": "181L"}],
            "simulation": {"duration_ns": 1, "umbrella": {
                "collective_variable": "ligand_distance", **block}}}


class TestTheConfig:
    def test_the_windows_named_and_no_others(self):
        config = _umbrella(**{"from": 0.3, "to": 0.5, "n_windows": 5, "force_constant": 2000})
        held = windows_held_at(config, [1, 3], 6000)
        assert held["simulation"]["umbrella"]["force_constant"] == [2000, 6000, 2000, 6000, 2000]
        assert held["simulation"]["duration_ns"] == 1
        # The config given is left as it was.
        assert config["simulation"]["umbrella"]["force_constant"] == 2000

    def test_a_list_keeps_each_windows_own(self):
        config = _umbrella(centres=[0.3, 0.4, 0.5], force_constant=[1000, 2000, 3000])
        held = windows_held_at(config, [2], 5000)
        assert held["simulation"]["umbrella"]["force_constant"] == [1000, 2000, 5000]

    @pytest.mark.parametrize("windows,k,said", [
        ([7], 3000, "no window 7"),
        ([1], 0, "force constant of 0"),
        ([1], -5, "force constant of -5"),
        ([1], float("inf"), "finite number"),
        ([1], float("nan"), "finite number"),
    ])
    def test_what_is_refused(self, windows, k, said):
        config = _umbrella(centres=[0.3, 0.4, 0.5], force_constant=2000)
        with pytest.raises(StudyError, match=said):
            windows_held_at(config, windows, k)

    def test_not_an_umbrella_study(self):
        with pytest.raises(StudyError, match="no `simulation.umbrella`") as refused:
            windows_held_at({"systems": [{"system": "1UBQ"}]}, [0], 2000)
        assert refused.value.code == "config.option.inapplicable"


@pytest.fixture
def study(tmp_path, monkeypatch):
    ran: list[int] = []
    monkeypatch.setattr(explorer_module, "_execute_run", _stand_in(ran))
    monkeypatch.setattr(BatchExplorer, "_maybe_build_comparison", lambda self: None)
    out = tmp_path / "out"
    path = _config(tmp_path, [2000] * 5)
    BatchExplorer(config=path, output_dir=str(out)).run()
    ran.clear()
    return path, out, ran


def test_the_command_line_holds_the_window_named(study) -> None:
    from fastmdxplora.cli.main import main

    path, out, ran = study
    assert main(["explore", "-c", str(path), "--output", str(out),
                 "--rerun-window", "2", "--rerun-force-constant", "6000"]) == 0
    assert ran == [2]
    record = json.loads((_window(out, 2) / "simulation" / "umbrella_window.json")
                        .read_text(encoding="utf-8"))
    assert record["force_constant"] == 6000
    pmf = json.loads((out / "pmf.json").read_text(encoding="utf-8"))
    assert pmf["plan"]["force_constants"] == [2000, 2000, 6000, 2000, 2000]
    assert pmf.get("pmf") and not pmf.get("refused")

    # The study's own config says what each window ran with, so another
    # window run again from it keeps window 2 at its new spring.
    ran.clear()
    assert main(["explore", "-c", str(out / "resolved_config.yml"), "--output", str(out),
                 "--rerun-window", "4"]) == 0
    assert ran == [4]
    pmf = json.loads((out / "pmf.json").read_text(encoding="utf-8"))
    assert pmf["plan"]["force_constants"] == [2000, 2000, 6000, 2000, 2000]


def test_the_config_as_first_written_is_refused_after(study, capsys) -> None:
    """The first config gives window 2 its old spring, so it is refused, not
    recombined with the wrong one."""
    from fastmdxplora.cli.main import main

    path, out, ran = study
    assert main(["explore", "-c", str(path), "--output", str(out),
                 "--rerun-window", "2", "--rerun-force-constant", "6000"]) == 0
    ran.clear()
    assert main(["explore", "-c", str(path), "--output", str(out),
                 "--rerun-window", "4"]) == 1
    assert "window 2 ran at 0.4 nm with 6000" in capsys.readouterr().err
    assert ran == []


def test_without_the_windows_named_it_is_refused(study, capsys) -> None:
    from fastmdxplora.cli.main import main

    path, out, ran = study
    assert main(["explore", "-c", str(path), "--output", str(out),
                 "--rerun-force-constant", "6000"]) == 1
    assert "name them with --rerun-window" in capsys.readouterr().err
    assert ran == []
