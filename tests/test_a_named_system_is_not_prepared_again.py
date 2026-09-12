"""A study that names a prepared system does not prepare a second one.

Solvation does not place water the same way twice, so a second preparation
is a second set of atoms. A study that says `setup_from` and prepares anyway
gets frames from one system and a topology from another, and the failure --
if it fails at all -- is a particle count mismatch ten seconds later. A real
umbrella study stopped with "the prepared system has 36075 particles and the
pull's trajectory has 36087", having ignored the setting that would have
prevented it.
"""

from __future__ import annotations

import pytest


def _a_study(tmp_path, prepared):
    from fastmdxplora.batch.explorer import BatchExplorer

    config = tmp_path / "c.yml"
    config.write_text(
        "output: out\n"
        "systems:\n"
        "  - system: 181L\n"
        "simulation:\n"
        f"  setup_from: {prepared}\n"
        "  umbrella:\n"
        "    collective_variable: distance\n"
        '    selection_a: "name CA"\n'
        '    selection_b: "name CB"\n'
        "    from: 0.4\n"
        "    to: 1.0\n"
        "    n_windows: 4\n"
        "    force_constant: 3000\n",
        encoding="utf-8")
    return BatchExplorer(config=config, output_dir=str(tmp_path / "out"))


def _prepared_at(where):
    where.mkdir(parents=True, exist_ok=True)
    for name in ("system.xml", "state.xml", "topology.pdb"):
        where.joinpath(name).write_text("<x/>", encoding="utf-8")
    return where


class TestANamedSystemIsNotPreparedAgain:

    def test_the_setup_phase_is_skipped_rather_than_run(self, tmp_path, caplog):
        import logging

        prepared = _prepared_at(tmp_path / "earlier" / "setup")
        study = _a_study(tmp_path, prepared)

        with caplog.at_level(logging.INFO):
            # Nothing is prepared: the early return is the reuse path, which
            # is the one that seeds from what is already there.
            study._maybe_prepare_once(None, None)

        said = " ".join(record.getMessage() for record in caplog.records)
        assert "Preparing nothing" in said
        assert "water is not placed the same way twice" in said

    def test_a_name_pointing_at_nothing_is_refused(self, tmp_path):
        study = _a_study(tmp_path, tmp_path / "not-here")

        with pytest.raises(FileNotFoundError, match="no prepared system"):
            study._maybe_prepare_once(None, None)

    @pytest.mark.parametrize("shape", ["setup", "shared_setup/setup", ""])
    def test_the_three_shapes_a_finished_study_leaves(self, tmp_path, shape):
        """A run directory, a study's shared preparation, or the setup
        directory itself: a person points at what they have."""
        root = tmp_path / "earlier"
        _prepared_at(root / shape if shape else root)
        study = _a_study(tmp_path, root)

        study._maybe_prepare_once(None, None)     # does not raise

    def test_a_study_that_names_nothing_still_prepares(self, tmp_path):
        """The setting is what turns preparation off. Without it, a study
        that asked for setup gets setup."""
        from fastmdxplora.batch.explorer import BatchExplorer

        config = tmp_path / "c.yml"
        config.write_text(
            "output: out\n"
            "systems:\n"
            "  - system: 181L\n"
            "simulation:\n"
            "  umbrella:\n"
            "    collective_variable: distance\n"
            '    selection_a: "name CA"\n'
            '    selection_b: "name CB"\n'
            "    from: 0.4\n"
            "    to: 1.0\n"
            "    n_windows: 4\n"
            "    force_constant: 3000\n",
            encoding="utf-8")
        study = BatchExplorer(config=config, output_dir=str(tmp_path / "out"))

        # It gets as far as preparing, which on a config with no real
        # structure behind it is where it stops -- the point is that it tried
        # rather than returning early.
        with pytest.raises(Exception) as stopped:
            study._maybe_prepare_once(None, None)
        assert "no prepared system" not in str(stopped.value)
