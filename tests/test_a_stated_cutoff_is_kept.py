"""A cutoff or switch somebody wrote is kept, even at the force field's value.

The schema handed over 1.0 nm and a switching function whether or not anybody
wrote them, so setup could not tell a stated 1.0 nm from silence: a CHARMM36
study that asked for 1.0 nm ran at 1.2, and a stated `use_switching_function`
was replaced by the force field's either way, each with a log line. The
record then said 1.0 nm of a run cut off at 1.2, and the methods paragraph
repeated it. Unstated is now `None`, the force field decides only then, and
what it decided is recorded.
"""

from __future__ import annotations

import pytest

from fastmdxplora.config.schema import SETUP
from fastmdxplora.setup.forcefields import nonbonded_scheme


def _scheme(name, **stated):
    return nonbonded_scheme(name, cutoff_nm=stated.get("cutoff"),
                            use_switching_function=stated.get("switching"),
                            switch_distance_nm=stated.get("switch"))


def test_the_schema_hands_over_nothing() -> None:
    defaults = SETUP.defaults()
    assert defaults["nonbonded_cutoff_nm"] is None
    assert defaults["use_switching_function"] is None


class TestAStatedValueIsKept:

    def test_a_stated_one_nanometre_on_charmm36(self) -> None:
        cutoff, switching, switch, _ = _scheme("charmm36", cutoff=1.0)
        assert cutoff == pytest.approx(1.0)
        # Its switch at 1.0 would be the cutoff itself, so none is imposed.
        assert switching is True and switch is None

    def test_no_switch_stated_on_charmm36(self) -> None:
        cutoff, switching, _, _ = _scheme("charmm36", switching=False)
        assert switching is False and cutoff == pytest.approx(1.2)

    def test_a_switch_stated_on_amber(self) -> None:
        _, switching, _, _ = _scheme("amber14", switching=True)
        assert switching is True

    @pytest.mark.parametrize("name", ["amber14", "charmm36"])
    def test_unstated_is_still_the_force_fields(self, name) -> None:
        cutoff, switching, switch, said = _scheme(name)
        assert (cutoff, switching, switch) == ((1.2, True, 1.0) if name == "charmm36"
                                               else (1.0, False, None))
        assert "with nothing stated" in said

    def test_an_xml_list_falls_back_as_before(self) -> None:
        assert _scheme(None)[:3] == (1.0, True, None)


class TestTheRecordSaysWhatWasUsed:

    def _methods(self, tmp_path, setup):
        from fastmdxplora.report.methods import methods_paragraphs

        return methods_paragraphs(tmp_path, setup, {})

    def test_the_force_fields_cutoff_is_what_the_methods_say(self, tmp_path) -> None:
        setup = {"parameters": {"forcefield": "charmm36", "nonbonded_cutoff_nm": None},
                 "resolved": {"nonbonded_cutoff_nm": 1.2, "use_switching_function": True}}
        assert "real-space cutoff of 1.20 nm" in self._methods(tmp_path, setup)

    def test_a_record_written_before_this_reads_as_before(self, tmp_path) -> None:
        setup = {"parameters": {"forcefield": "amber14", "nonbonded_cutoff_nm": 1.0}}
        assert "real-space cutoff of 1.00 nm" in self._methods(tmp_path, setup)


class TestTheAdvice:

    def test_the_box_is_judged_against_the_force_fields_cutoff(self) -> None:
        from fastmdxplora.advisories import advise

        structure = {"extents_angstrom": [20.0, 20.0, 20.0]}
        # 1.2 nm of padding: enough for AMBER's 1.0 nm, not CHARMM36's 1.2.
        settings = {"solvent_padding_nm": 1.2, "nonbonded_cutoff_nm": None}
        said = {a.setting for a in advise(structure, {**settings, "forcefield": "charmm36"})}
        assert "solvent_padding_nm" in said
        said = {a.setting for a in advise(structure, {**settings, "forcefield": "amber14"})}
        assert "solvent_padding_nm" not in said

    def test_a_switch_is_questioned_only_where_it_was_stated(self) -> None:
        from fastmdxplora.advisories import advise

        stated = {a.setting for a in advise({}, {"forcefield": "amber14",
                                                 "use_switching_function": True})}
        unstated = {a.setting for a in advise({}, {"forcefield": "amber14",
                                                   "use_switching_function": None})}
        assert "use_switching_function" in stated
        assert "use_switching_function" not in unstated
