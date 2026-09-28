"""The cutoff scheme is part of the force field, not a setting beside it.

A force field is fitted with a particular treatment of the truncation, and the
effects of that truncation are compensated in its other parameters. So running
CHARMM36 at AMBER's cutoff is not a small inaccuracy; it is a different force
field from the one that was validated.

One cutoff and one switch served every entry in the registry: 1.0 nm with a
switch at 0.9. That is the wrong distance for CHARMM36, which is developed at
1.2 with switching from 1.0, and it is a switch AMBER should never have had --
AMBER is developed with hard truncation, and switching it moves the run away
from the parameterisation rather than towards it.

OpenMM offers the potential-based switch and not CHARMM's force-based one.
Lee et al. (CHARMM-GUI Input Generator, J Chem Theory Comput 2016) say so
plainly and prescribe this protocol for OpenMM regardless, having tested it
against CHARMM's own results.
"""

from __future__ import annotations

import pytest

from fastmdxplora.setup.forcefields import _REGISTRY, nonbonded_scheme


def _resolve(name, cutoff=None, switching=None, switch=None):
    return nonbonded_scheme(name, cutoff_nm=cutoff,
                            use_switching_function=switching,
                            switch_distance_nm=switch)


class TestEachForceFieldCarriesItsOwnScheme:
    def test_charmm36_is_developed_at_one_point_two(self) -> None:
        cutoff, switching, switch, _ = _resolve("charmm36")
        assert cutoff == pytest.approx(1.2)
        assert switching is True
        assert switch == pytest.approx(1.0)

    @pytest.mark.parametrize("name", ["amber14", "amber-fb15", "amber-openff"])
    def test_amber_is_truncated_hard(self, name: str) -> None:
        """Switching an AMBER force field is not a refinement of it."""
        cutoff, switching, switch, _ = _resolve(name)
        assert cutoff == pytest.approx(1.0)
        assert switching is False
        assert switch is None

    def test_every_entry_declares_one(self) -> None:
        for name, choice in _REGISTRY.items():
            cutoff, switch = choice.nonbonded
            assert cutoff > 0, name
            assert switch is None or 0 < switch < cutoff, name


class TestWhatSomebodyChoseIsKept:
    def test_a_named_cutoff_wins(self) -> None:
        cutoff, _, _, said = _resolve("charmm36", cutoff=1.5)
        assert cutoff == pytest.approx(1.5)
        # Only the switch, which nobody stated, was decided and is said.
        assert "cutoff" not in said.split("uses")[1] and "switching from 1" in said

    def test_a_named_switch_wins(self) -> None:
        _, switching, switch, said = _resolve("amber14", switch=0.85)
        assert switch == pytest.approx(0.85) and switching is True
        assert "switch" not in said.split("uses")[1]

    def test_an_unknown_force_field_is_left_alone(self) -> None:
        """A raw XML list is not in the registry and carries no scheme."""
        cutoff, switching, switch, said = _resolve("something-custom",
                                                   cutoff=1.1, switch=0.9)
        assert (cutoff, switching, switch, said) == (1.1, True, 0.9, None)


class TestItSaysWhatItDecided:
    def test_charmm_names_the_switch_it_is_not(self) -> None:
        """OpenMM's is the potential-based switch. Calling it CHARMM's would
        be claiming a function the toolkit does not have."""
        said = _resolve("charmm36")[3]
        assert "potential-based" in said and "force-based" in said
        assert "1.2" in said and "1" in said

    def test_amber_says_why_there_is_no_switch(self) -> None:
        said = _resolve("amber14")[3]
        assert "hard truncation" in said
        assert "away from the parameterisation" in said

    def test_nothing_is_said_when_nothing_was_decided(self) -> None:
        assert _resolve("charmm36", cutoff=1.4, switching=True)[3] is None


class TestThePreparationUsesIt:
    def test_the_resolved_values_replace_the_originals(self) -> None:
        """Rebinding the same names is what makes this safe: every later use
        -- the box check, `createSystem` -- picks up the resolved cutoff
        without having to be found and changed. CHARMM36 at 1.2 nm needs a
        bigger box than the same system at 1.0, and the padding check has to
        be comparing against the cutoff the run will actually use."""
        from pathlib import Path
        import fastmdxplora.setup.prepare as prepare

        source = Path(prepare.__file__).read_text(encoding="utf-8")
        assert "(nonbonded_cutoff_nm, use_switching_function," in source
        assert "switch_distance_nm, said) = nonbonded_scheme(" in source

    def test_the_helper_is_imported_there(self) -> None:
        import fastmdxplora.setup.prepare as prepare

        assert hasattr(prepare, "nonbonded_scheme")
