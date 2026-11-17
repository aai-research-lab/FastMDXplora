"""A config is refused for what it says, before anything is built.

Found by running configs written to break things. A file that was not text,
or YAML nested past what the parser follows, was answered with Python's own
words ("'utf-8' codec can't decode byte 0xed", "maximum recursion depth
exceeded"). And `nonbonded_method: NoCutoff` with the default NPT stage was
prepared, solvated and minimised before OpenMM said "A barostat cannot be
used with a non-periodic system".
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fastmdxplora.config.loader import ConfigError, load_config_file, validate_config


def test_a_file_that_is_not_text_is_said_as_that(tmp_path: Path) -> None:
    path = tmp_path / "config.yml"
    path.write_bytes(b"\xed\xa0\x80 not text")
    with pytest.raises(ConfigError, match="is not a text file"):
        load_config_file(path)


def test_yaml_nested_past_the_parser_is_said_as_that(tmp_path: Path) -> None:
    path = tmp_path / "config.yml"
    path.write_text("x: " + "[" * 5000 + "]" * 5000, encoding="utf-8")
    with pytest.raises(ConfigError, match="nested too deeply"):
        load_config_file(path)


@pytest.mark.parametrize("method", ["NoCutoff", "CutoffNonPeriodic"])
def test_no_box_with_a_barostat_is_refused(method: str) -> None:
    with pytest.raises(ConfigError, match="no periodic box") as caught:
        validate_config({"systems": [{"system": "1UBQ"}],
                         "setup": {"nonbonded_method": method}})
    assert caught.value.refusal.code == "config.option.conflicting"
    # Shown as written in the GUI's Check verdict, where code marks are not set.
    said = str(caught.value)
    assert said.startswith("OpenMM cannot pair a barostat") and "`" not in said


def test_an_ensemble_of_nvt_still_equilibrates_under_pressure() -> None:
    """`ensemble` is production's: the NPT stage before it is still there,
    and still has a barostat, unless its length is zero."""
    with pytest.raises(ConfigError, match="no periodic box"):
        validate_config({"systems": [{"system": "1UBQ"}],
                         "setup": {"nonbonded_method": "NoCutoff"},
                         "simulation": {"ensemble": "nvt"}})


def test_constant_volume_throughout_runs() -> None:
    validate_config({"systems": [{"system": "1UBQ"}],
                     "setup": {"nonbonded_method": "NoCutoff"},
                     "simulation": {"ensemble": "nvt", "npt_duration_ns": 0}})


def test_a_swept_method_names_its_run() -> None:
    with pytest.raises(ConfigError, match="its own settings"):
        validate_config({"systems": [{"system": "1UBQ"}],
                         "sweep": {"setup.nonbonded_method": ["PME", "NoCutoff"]}})


def test_without_a_simulation_there_is_no_barostat() -> None:
    validate_config({"systems": [{"system": "1UBQ"}],
                     "setup": {"nonbonded_method": "NoCutoff"},
                     "include_phase": ["setup"]})


def test_setup_run_alone_adds_no_barostat(tmp_path: Path) -> None:
    """`.setup(nonbonded_method="NoCutoff")` was refused, as if the default
    NPT simulation ran with it."""
    from fastmdxplora import FastMDXplora

    study = FastMDXplora("1UBQ", output_dir=str(tmp_path / "study"))
    assert study._validated("setup", {"nonbonded_method": "NoCutoff"}) == {
        "nonbonded_method": "NoCutoff"}


@pytest.mark.parametrize("selection", [{"include_phase": ["setup"]}, {"include": ["setup"]},
                                       {"exclude_phase": ["simulation"]}])
def test_a_study_made_to_run_no_simulation_adds_no_barostat(tmp_path: Path, selection) -> None:
    """The constructor checked its options without the phases it was told
    to run, and refused a setup with no box as if the default NPT followed."""
    from fastmdxplora import FastMDXplora

    FastMDXplora("1UBQ", output_dir=str(tmp_path / "study"),
                 options={"setup": {"nonbonded_method": "NoCutoff"}}, **selection)
    with pytest.raises(ConfigError, match="no periodic box"):
        FastMDXplora("1UBQ", output_dir=str(tmp_path / "other"),
                     options={"setup": {"nonbonded_method": "NoCutoff"}})


def test_a_simulation_on_a_system_with_no_box_is_still_refused(tmp_path: Path) -> None:
    from fastmdxplora import FastMDXplora

    study = FastMDXplora("1UBQ", output_dir=str(tmp_path / "study"),
                         options={"setup": {"nonbonded_method": "NoCutoff"},
                                  "simulation": {"ensemble": "nvt", "npt_duration_ns": 0}})
    with pytest.raises(ConfigError, match="no periodic box"):
        study._validated("simulation", {"npt_duration_ns": 1})
    with pytest.raises(ConfigError, match="no periodic box"):
        study._validated("simulation", {})
    study._validated("simulation", {"ensemble": "nvt", "npt_duration_ns": 0})


def test_the_cli_still_refuses_a_study_with_no_box_and_a_barostat(tmp_path: Path, capsys) -> None:
    from fastmdxplora.cli.main import main

    try:
        code = main(["explore", "--system", "1UBQ", "--output", str(tmp_path / "study"),
                     "--setup-nonbonded-method", "NoCutoff", "--dry-run"])
    except ConfigError as refused:
        said = str(refused)
    else:
        assert code != 0
        printed = capsys.readouterr()
        said = printed.out + printed.err
    assert "no periodic box" in said


def test_runs_that_cannot_be_expanded_are_judged_as_the_study_s_own() -> None:
    """The batch check says what is wrong with them; this one reads the
    study's own settings."""
    from fastmdxplora.config.loader import _check_barostat_has_a_box

    with pytest.raises(ConfigError, match="no periodic box"):
        _check_barostat_has_a_box({"systems": "not a list",
                                   "setup": {"nonbonded_method": "NoCutoff"}})


def test_a_length_of_the_wrong_type_is_left_to_its_own_check() -> None:
    from fastmdxplora.config.loader import _check_barostat_has_a_box

    _check_barostat_has_a_box({"systems": [{"system": "1UBQ"}],
                               "setup": {"nonbonded_method": "NoCutoff"},
                               "simulation": {"npt_duration_ns": "long"}})


def test_the_status_bar_names_the_platform_a_study_runs_on(monkeypatch) -> None:
    """A Mac's OpenCL is registered and computes in single precision only, so
    `auto` passes it by and runs on the CPU; the status bar named OpenCL."""
    import sys
    from types import SimpleNamespace

    from fastmdxplora.gui import status_bar
    from fastmdxplora.simulation import runner

    registered = ["OpenCL", "CPU"]
    platforms = SimpleNamespace(
        getNumPlatforms=lambda: len(registered),
        getPlatform=lambda i: SimpleNamespace(getName=lambda: registered[i]),
        getPlatformByName=lambda name: name)
    fake = SimpleNamespace(Platform=platforms)
    asked = []

    def probe(omm, platform, name, props):
        asked.append((name, dict(props)))
        return name not in ("CUDA", "OpenCL")

    monkeypatch.setitem(sys.modules, "openmm", fake)
    monkeypatch.setattr(runner, "_import_openmm", lambda: {"openmm": fake})
    monkeypatch.setattr(runner, "_probe_platform", probe)
    status_bar.forget()
    try:
        named = status_bar._fastest_platform()
    finally:
        status_bar.forget()
    assert named == "CPU"
    assert ("OpenCL", {"Precision": "mixed"}) in asked


def test_the_status_bar_names_no_platform_without_openmm(monkeypatch) -> None:
    from fastmdxplora.gui import status_bar
    from fastmdxplora.simulation import runner

    def missing():
        raise ImportError("no OpenMM")

    monkeypatch.setattr(runner, "_import_openmm", missing)
    status_bar.forget()
    try:
        assert status_bar._fastest_platform() == ""
    finally:
        status_bar.forget()


def test_a_checkout_that_cannot_know_its_version_does_not_name_one() -> None:
    """As the build's `fallback_version`: `0.1.0` was printed in the banner
    of every run from a source checkout, and offered by Cite."""
    import ast

    from packaging.version import Version

    import fastmdxplora

    tree = ast.parse(Path(fastmdxplora.__file__).read_text(encoding="utf-8"))
    fallback = next(node.body[0].value.value for node in ast.walk(tree)
                    if isinstance(node, ast.ExceptHandler) and node.body
                    and isinstance(node.body[0], ast.Assign)
                    and getattr(node.body[0].targets[0], "id", "") == "__version__")
    assert Version(fallback).local
