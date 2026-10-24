"""`fastmdx analyze`'s `--analyses` and `--exclude-analyses` reach the
analyses, not the phases.

`fastmdx analyze --exclude-analyses image_check cluster dimred` printed
"--include-phase and --exclude-phase are mutually exclusive" and stopped:
the flag's setting is `exclude`, and the phase's flags were copied onto
explore's of the same name, its `--exclude-phase`.
"""

from __future__ import annotations

import importlib

import pytest

main = importlib.import_module("fastmdxplora.cli.main")


@pytest.fixture
def explored(monkeypatch):
    seen = {}

    def caught(args):
        seen["args"] = args
        return 0

    monkeypatch.setattr(main, "_cmd_explore", caught)
    return seen


class TestTheFlagsReachTheAnalyses:
    def test_exclude_analyses_runs_the_analysis_phase(self, explored, tmp_path):
        assert main.main(["analyze", "--output", str(tmp_path), "--exclude-analyses",
                          "image_check", "cluster", "dimred"]) == 0
        args = explored["args"]
        assert args.include == ["analysis"]
        assert not args.exclude
        assert args.analyze__exclude == ["image_check", "cluster", "dimred"]
        assert main._harvest_phase_options(
            args, main._PHASE_SPEC["analyze"][0], dest_prefix="analyze"
        )["exclude"] == ["image_check", "cluster", "dimred"]

    def test_analyses_reach_the_analyses(self, explored, tmp_path):
        assert main.main(["analyze", "--output", str(tmp_path), "--analyses", "rg"]) == 0
        args = explored["args"]
        assert args.include == ["analysis"] and args.analyze__include == ["rg"]


class TestTheHelpSaysTheImageCheck:
    def _help(self, flag):
        options = {suffix: kwargs for suffix, _kwarg, kwargs in main._PHASE_SPEC["analyze"][0]}
        return options[flag]["help"]

    def test_a_listed_plan_has_it(self):
        said = self._help("analyses")
        assert "`image_check`" in said and "joins it and runs first" in said

    def test_excluding_it_says_what_is_left_unchecked(self):
        said = self._help("exclude-analyses")
        assert "image_check" in said and "leaves the solute unchecked" in said
        # Within the cutoff the solute sees its image; in contact only under 0.5 nm.
        assert "where it saw its image" in said and "met its copy" not in said
