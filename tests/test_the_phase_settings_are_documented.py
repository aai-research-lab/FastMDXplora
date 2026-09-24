"""What each phase method accepts can be found without running it.

The command line and the GUI are generated from the schema. The phase
methods took **kwargs under a one-line docstring, so help() named no
setting and a type checker could not tell `duraton_ns` from `duration_ns`
until the call ran. Their docstrings and a typed settings file now come from
the same schema, and the package says it is typed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fastmdxplora import FastMDXplora
from fastmdxplora.config.phase_settings import PHASES, TYPES_FILE, _schema, types_source


def test_the_typed_settings_are_the_schemas() -> None:
    assert TYPES_FILE.read_text(encoding="utf-8") == types_source(), (
        "phase_settings_types.py is out of date with the schema. Write it again with "
        "`python -m fastmdxplora.config.phase_settings`.")


@pytest.mark.parametrize("method, section", list(PHASES.items()))
def test_each_phase_method_names_every_setting(method, section) -> None:
    documented = getattr(FastMDXplora, method).__doc__
    missing = [f.name for f in _schema(section).fields if f"\n{f.name} : " not in documented]
    assert not missing, missing


def test_the_package_says_it_is_typed() -> None:
    # Without the marker a type checker skips the package, annotations and all.
    import fastmdxplora

    assert (Path(fastmdxplora.__file__).parent / "py.typed").is_file()


def test_a_type_checker_catches_what_the_validator_would(tmp_path) -> None:
    api = pytest.importorskip("mypy.api")
    uses = tmp_path / "uses.py"
    uses.write_text(
        "from fastmdxplora import FastMDXplora\n"
        "study = FastMDXplora(system='1UBQ')\n"
        "study.simulate(duration_ns=1.0, platform='CUDA')\n"
        "study.simulate(duraton_ns=1.0)\n"
        "study.simulate(temperature_K='warm')\n"
        "study.analyze(include=['rmsd', 'rmsff'])\n", encoding="utf-8")
    report, _, _ = api.run([str(uses), "--no-incremental", "--cache-dir", str(tmp_path / "c")])
    flagged = {line.split(":")[1] for line in report.splitlines() if ": error:" in line}
    assert flagged == {"4", "5", "6"}, report
