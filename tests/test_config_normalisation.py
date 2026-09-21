"""Two spellings a config can take that mean exactly one thing.

Both were found by writing a config by hand and having it refused, which is
the only way this class of friction gets found: the author of a validator
writes configs the validator's shape already fits.

Neither of these guesses at intent. A phase block present but empty says the
same thing as leaving the key out, and a comma-separated list is how most
command-line tools take a list. Refusing a spelling whose meaning is plain is
a puzzle rather than a safeguard -- and the puzzle lands on somebody three
minutes into their first run, not on the person who wrote the check.
"""

from __future__ import annotations

import pytest

from fastmdxplora.config.loader import ConfigError, normalise_config, validate_config


class TestAnEmptyBlockMeansDefaults:
    """`analysis:` with nothing under it parses as None, and that was refused
    while omitting the key entirely was accepted -- two spellings of one
    intent behaving differently."""

    @pytest.mark.parametrize("phase", ["setup", "simulation", "analysis", "report"])
    def test_an_empty_block_is_accepted(self, phase: str) -> None:
        data = {"systems": [{"system": "1UBQ"}], phase: None}
        validate_config(data)
        assert data[phase] == {}

    def test_it_means_the_same_as_leaving_it_out(self) -> None:
        with_block = {"systems": [{"system": "1UBQ"}], "analysis": None}
        without = {"systems": [{"system": "1UBQ"}]}
        validate_config(with_block)
        validate_config(without)
        assert with_block["analysis"] == {}

    def test_a_block_of_the_wrong_type_is_still_refused(self) -> None:
        """Absence of a value is unambiguous. A number is a real mistake and
        saying so is the point of the check."""
        for wrong in (5, "rmsd", ["rmsd"]):
            with pytest.raises(ConfigError, match="must be a mapping"):
                validate_config({"systems": [{"system": "1UBQ"}],
                                 "analysis": wrong})


class TestACommaSeparatedListIsAList:
    """argparse with `nargs="+"` takes `--include a,b,c` as a single string.
    No phase or analysis name contains a comma, so there is nothing to
    resolve."""

    def test_phases_given_as_one_comma_string(self) -> None:
        data = {"systems": [{"system": "1UBQ"}],
                "include": ["setup,simulation,analysis,report"]}
        validate_config(data)
        assert data["include_phase"] == ["setup", "simulation", "analysis", "report"]

    def test_a_bare_string_is_taken_as_one_item_list(self) -> None:
        assert normalise_config({"include": "setup,report"})["include"] == [
            "setup", "report"]

    def test_spaces_around_the_commas_are_ignored(self) -> None:
        assert normalise_config(
            {"include": ["setup, simulation , report"]})["include"] == [
            "setup", "simulation", "report"]

    def test_analysis_names_split_the_same_way(self) -> None:
        data = {"systems": [{"system": "1UBQ"}],
                "analysis": {"include": ["rmsd,rmsf,rg"]}}
        validate_config(data)
        assert data["analysis"]["include"] == ["rmsd", "rmsf", "rg"]

    def test_exclude_is_treated_the_same(self) -> None:
        data = {"systems": [{"system": "1UBQ"}], "exclude": ["analysis,report"]}
        validate_config(data)
        assert data["exclude_phase"] == ["analysis", "report"]

    def test_a_proper_list_is_left_alone(self) -> None:
        data = {"include": ["setup", "simulation"]}
        assert normalise_config(data)["include"] == ["setup", "simulation"]

    def test_an_unknown_phase_is_still_refused_after_splitting(self) -> None:
        """Splitting must not become a way of smuggling a typo past the
        check: each part is validated as though it had been written out."""
        with pytest.raises(ConfigError, match="unknown phase"):
            validate_config({"systems": [{"system": "1UBQ"}],
                             "include": ["setup,simulatoin"]})

    def test_an_unknown_analysis_survives_splitting_and_is_refused(
            self) -> None:
        """Splitting hands the typo on intact rather than swallowing it, and
        validation then refuses it by name.

        This used to be left to the orchestrator, which dropped an unknown
        name silently: a config asking for `contacts` rather than
        `pl_contacts` validated, ran, and produced a report missing the
        measurement it asked for. Refusing here means the split is still
        checkable -- the error names the typo, not the string it came from.
        """
        data = {"systems": [{"system": "1UBQ"}],
                "analysis": {"include": ["rmsd,vibes"]}}
        with pytest.raises(ConfigError, match="'vibes'"):
            validate_config(data)
        assert data["analysis"]["include"] == ["rmsd", "vibes"]


class TestNormalisingChangesNothingElse:
    def test_a_config_needing_neither_is_untouched(self) -> None:
        data = {"systems": [{"system": "1UBQ"}],
                "simulation": {"duration_ns": 2},
                "include": ["setup"]}
        assert normalise_config(dict(data)) == data

    def test_the_mutual_exclusion_still_holds(self) -> None:
        with pytest.raises(ConfigError, match="mutually exclusive"):
            validate_config({"systems": [{"system": "1UBQ"}],
                             "include": ["setup"], "exclude": ["report"]})
