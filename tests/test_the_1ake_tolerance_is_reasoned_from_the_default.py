"""The 1AKE box question's tolerance is reasoned from the 2.0 nm default.

Its reason ("a twentieth of the width is a tenth of the default padding")
was written when the default padding was 1.0 nm; and two tests told that
history as "the defaults". The number the tolerance allows is checked here
against setup's own estimate for 1AKE's assembly: a twentieth of the width
at the default padding is under a quarter of that padding, and a width at
the 1.0 nm padding before it is told apart.
"""

from __future__ import annotations

import gzip
from pathlib import Path

HERE = Path(__file__).parent


def _width(structure: Path, padding: float | None) -> float:
    from fastmdxplora.gui.preview import preview_of_config

    config = {"systems": [{"system": str(structure)}]}
    if padding is not None:
        config["setup"] = {"solvent_padding_nm": padding}
    answer = preview_of_config(config)
    assert answer.get("ok"), answer
    return float(answer["estimate"]["width_nm"])


def test_the_reason_is_the_number_it_allows(tmp_path) -> None:
    from fastmdxplora.config.schema import SETUP
    from fastmdxplora.validation.agent_looks import QUESTIONS

    (question,) = [q for q in QUESTIONS if q.name == "box_1ake"]
    padding = SETUP.defaults()["solvent_padding_nm"]
    structure = tmp_path / "1AKE.pdb"
    structure.write_text(gzip.open(HERE / "data" / "assemblies" / "1AKE.pdb.gz", "rt").read())
    width, before = _width(structure, None), _width(structure, 1.0)
    allowed = question.tolerance * width
    assert allowed < padding / 4
    assert abs(before - width) / width > question.tolerance
    assert f"{padding:.1f} nm default padding" in question.because
    assert f"({width:.1f} nm) is {allowed:.2f} nm" in question.because
    assert f"1.0 nm padding before it ({before:.1f} nm)" in question.because


def test_the_old_padding_is_not_called_the_defaults() -> None:
    for name in ("test_a_small_solute_gets_a_box_for_its_cutoff.py",
                 "test_prepare_guardrails.py"):
        text = " ".join((HERE / name).read_text(encoding="utf-8").split())
        assert "The defaults (1.0 nm" not in text, name
        assert "The settings before 2.0 nm became the default (1.0 nm" in text, name
