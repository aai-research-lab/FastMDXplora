"""The documentation says things about the code that the code can check.

Four claims in the published docs had gone stale without anything noticing:
the number of analyses, the number of collective variables in three separate
places, which analyses need a ligand, and which section three enhanced
sampling analyses belonged under. Each was correct when written. Each became
wrong when something was added, and a reader had no way to tell.

Prose has to be written by hand. A count does not, and a count is what goes
wrong, so the counts are pinned here.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import fastmdxplora.analysis.analyze  # noqa: F401  -- registers the analyses
from fastmdxplora.analysis.orchestrator import _REGISTRY
from fastmdxplora.simulation.metadynamics import COLLECTIVE_VARIABLES

DOCS = Path(__file__).resolve().parents[2] / "docs"
ROOT = Path(__file__).resolve().parents[2]

#: The three that read a biased run's own result rather than the trajectory.
METHOD_GATED = ("pmf", "metad_surface", "steered_work")


def _analysis_section() -> str:
    """The catalogue, from the top of the page down to the detail section.

    It moved out of `phases.md` when the documentation was reorganised around
    the Config and the Manifest: the four phases are an overview page now, and
    the per-analysis catalogue is a reference of its own.
    """
    text = (DOCS / "analyses.md").read_text(encoding="utf-8")
    return text[:text.index("## What the measures actually compute")]


class TestEveryAnalysisIsDocumented:
    def test_the_table_lists_exactly_what_is_registered(self) -> None:
        documented = set(re.findall(r"^\| `([a-z_]+)` \|", _analysis_section(),
                                    re.M))
        assert documented == set(_REGISTRY), (
            "docs/analyses.md and the analysis registry disagree; "
            f"undocumented={sorted(set(_REGISTRY) - documented)}, "
            f"documented but absent={sorted(documented - set(_REGISTRY))}")

    def test_the_stated_count_is_the_real_one(self) -> None:
        """'Fifteen analyses' outlived three additions."""
        section = _analysis_section()
        trajectory = len(_REGISTRY) - len(METHOD_GATED)
        words = {9: "Nine", 10: "Ten", 14: "Fourteen", 15: "Fifteen",
                 16: "Sixteen", 17: "Seventeen", 18: "Eighteen",
                 19: "Nineteen", 20: "Twenty", 21: "Twenty-one",
                 22: "Twenty-two", 23: "Twenty-three", 24: "Twenty-four",
                 25: "Twenty-five", 26: "Twenty-six"}
        assert f"{words[trajectory]} analyses" in section, (
            f"there are {trajectory} analyses of the trajectory; "
            "the sentence introducing them says otherwise")

    def test_the_ligand_gated_count_is_right(self) -> None:
        needs_ligand = [n for n, c in _REGISTRY.items()
                        if getattr(c, "requires_ligand", False)]
        assert len(needs_ligand) == 5, (
            "the docs say five ligand analyses run automatically; "
            f"the registry has {len(needs_ligand)}: {sorted(needs_ligand)}")
        assert "five ligand analyses" in _analysis_section()


class TestTheEnhancedSamplingThreeAreNotLigandAnalyses:
    """They were filed under 'Protein and ligand together', which is where a
    row lands if it is appended to the end of the file. None of them needs a
    ligand and none runs on an ordinary trajectory."""

    def test_none_of_them_requires_a_ligand(self) -> None:
        for name in METHOD_GATED:
            assert not getattr(_REGISTRY[name], "requires_ligand", False), name

    def test_each_runs_only_after_its_own_method(self) -> None:
        gates = ("requires_umbrella", "requires_metadynamics",
                 "requires_steered")
        for name in METHOD_GATED:
            assert any(getattr(_REGISTRY[name], g, False) for g in gates), name

    def test_they_sit_under_their_own_heading(self) -> None:
        section = _analysis_section()
        heading = section.index("### Enhanced sampling")
        ligand = section.index("### Protein and ligand together")
        for name in METHOD_GATED:
            assert section.index(f"| `{name}` |") > heading, (
                f"{name} is documented above the Enhanced sampling heading")
        assert ligand < heading


class TestTheCollectiveVariablesAreAllNamed:
    """The count was wrong in three places at once, and the README disagreed
    with the page it links to."""

    @pytest.mark.parametrize("document", ["studies.md", "examples.md"])
    def test_no_document_understates_them(self, document: str) -> None:
        text = (DOCS / document).read_text(encoding="utf-8")
        assert "five variables" not in text
        assert "on five variables" not in text

    def test_every_variable_is_named_where_they_are_listed(self) -> None:
        text = (DOCS / "studies.md").read_text(encoding="utf-8")
        missing = [c for c in COLLECTIVE_VARIABLES if f"`{c}`" not in text]
        assert not missing, f"collective variables never named in docs: {missing}"

    def test_the_readme_and_the_page_agree(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        words = {5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine",
                 10: "ten"}
        word = words[len(COLLECTIVE_VARIABLES)]
        assert word in readme.lower()
        assert word in (DOCS / "studies.md").read_text(
            encoding="utf-8").lower()


class TestTheCountsInTheReadmeHold:
    def test_the_bilayers(self) -> None:
        from fastmdxplora.setup.membrane import LIPIDS
        assert len(LIPIDS) == 7, "the README says seven bilayers"
        text = (DOCS / "studies.md").read_text(encoding="utf-8")
        for lipid in LIPIDS:
            assert lipid in text, f"{lipid} is offered and never documented"


class TestTheReweightingIsDescribedAsBuilt:
    """The estimator and its limits are claims about the code, and the code
    can check the ones that are structural."""

    @staticmethod
    def _section() -> str:
        """The section with its line wrapping flattened.

        Prose wraps where the column runs out, so "radius of gyration" spans
        a newline. A test that fails when a paragraph is rewrapped is a test
        that gets deleted rather than fixed."""
        import re
        text = (DOCS / "analyses.md").read_text(encoding="utf-8")
        start = text.index("## Averages on a biased run")
        return re.sub(r"\s+", " ", text[start:text.index("## Adding your own",
                                                         start)])

    def test_the_estimator_is_named_with_its_offset(self) -> None:
        """Without c(t) the weights rank frames by when they were written.
        Documenting exp(V/RT) alone would describe a different estimator."""
        section = self._section()
        assert "c(t)" in section
        assert "Tiwary" in section

    def test_every_reweighted_analysis_is_named(self) -> None:
        section = self._section().lower()
        for name, cls in _REGISTRY.items():
            if not getattr(cls, "reweightable", None):
                continue
            label = cls.reweightable[1].split(" (")[0].lower()
            assert label in section, (
                f"{name} is reweighted and the docs never say so")

    def test_what_is_not_corrected_is_said(self) -> None:
        section = self._section()
        assert "dimensionality reduction is not corrected" in section
        # Populations are corrected; which clusters exist is not.
        assert "which clusters exist" in section

    def test_the_two_methods_without_weights_are_explained(self) -> None:
        section = self._section()
        assert "umbrella window" in section.lower()
        assert "steered pull" in section.lower()
        assert "potential of mean force" in section

    def test_the_output_directory_is_documented(self) -> None:
        text = (DOCS / "analyses.md").read_text(encoding="utf-8")
        assert "analysis/reweighted/" in text
        assert "reweighted_averages.json" in text


class TestTheGuiPageCountsWhatThereIs:
    """The page said 159 options across 15 analyses; there are 200 across 19.
    A count in a document nobody recomputes is a claim that decays, and this
    one is load-bearing: it is the sentence that says the browser is not a
    cut-down command line."""

    @staticmethod
    def _page() -> str:
        return (DOCS / "gui.md").read_text(encoding="utf-8")

    def test_the_analysis_count_is_the_real_one(self) -> None:
        assert f"{len(_REGISTRY)} analyses" in self._page()

    def test_the_option_count_is_the_real_one(self) -> None:
        from fastmdxplora.analysis.describe import describe_analysis

        total = sum(len(describe_analysis(name)) for name in _REGISTRY)
        assert f"{total} analysis options" in self._page()

    def test_the_settings_count_is_the_real_one(self) -> None:
        from fastmdxplora.config.schema import PHASE_SCHEMAS, TOP_LEVEL

        total = len(TOP_LEVEL.fields) + sum(
            len(group.fields) for group in PHASE_SCHEMAS.values())
        assert f"{total} phase and top-level settings" in self._page()

    def test_it_says_the_browser_builds_and_the_cli_runs(self) -> None:
        """The claim external summaries keep getting wrong: this is not a
        command-line tool with a dashboard attached."""
        page = self._page()
        assert "generated from the" in page
        assert "any system FastMDXplora can study can be built here" in page


class TestTheConfigIsPresentedAsTheStudy:
    """A config is the whole description of a study, and every interface
    builds one and runs all four phases. External summaries kept reporting
    this as a command-line tool with a dashboard attached, which is what the
    documentation implied by describing the GUI as somewhere to watch a run.
    """

    @staticmethod
    def _readme() -> str:
        return (ROOT / "README.md").read_text(encoding="utf-8")

    def test_the_readme_leads_with_the_config(self) -> None:
        assert "## The config is the study" in self._readme()

    @pytest.mark.parametrize("interface", ["GUI", "CLI", "Python API"])
    def test_each_interface_is_named(self, interface: str) -> None:
        assert interface in self._readme()

    def test_none_is_claimed_as_primary(self) -> None:
        readme = self._readme()
        assert "None of these is the primary interface" in readme

    def test_the_api_really_takes_a_config(self) -> None:
        """The claim is checkable: the constructor accepts one."""
        import inspect

        from fastmdxplora.orchestrator import FastMDXplora

        assert "config" in inspect.signature(FastMDXplora.__init__).parameters

    def test_a_run_records_its_own_complete_config(self) -> None:
        """What makes a config the study rather than one input format."""
        from fastmdxplora.config import write_resolved_config  # noqa: F401

        assert "resolved_config.yml" in self._readme()


class TestTheResolvedConfigIsDescribedAsItIs:
    """Two numbers and one distinction, all of which decay silently.

    The page claims the file names every setting the run used, gives the
    size of a dumped file, and separates the settings it pins across
    versions from the ones it defers. Each is checkable, and the last is
    the one worth checking: a claim to reproduce a study independently of
    the version would be too strong, and softening it to nothing would
    lose the point of the file.
    """

    @staticmethod
    def _page() -> str:
        """As one line, because prose wraps and a claim should not depend
        on where it happened to break."""
        import re

        return re.sub(
            r"\s+", " ", (DOCS / "config.md").read_text(encoding="utf-8"))

    def test_the_dumped_size_is_the_real_one(self) -> None:
        import tempfile
        from pathlib import Path as _Path

        import yaml

        from fastmdxplora.config import write_resolved_config

        out = _Path(tempfile.mkdtemp())
        path = write_resolved_config(
            {"system": "1UBQ", "output": str(out),
             "options": {"setup": {"ph": 6.5},
                         "simulation": {"pressure_atm": 1.2}}},
            out)
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        total = sum(len(doc[phase])
                    for phase in ("setup", "simulation", "analysis", "report"))
        assert f"{total} settings for a study that set two" in self._page()

    @pytest.mark.parametrize("recorded,phase", [
        ("production_steps", "simulation"),
        ("trajectory_interval_steps", "simulation"),
        ("pressure_bar", "simulation"),
        ("switch_distance_nm", "setup"),
        ("force_field", "setup"),
        ("water_model", "setup"),
        ("include", "analysis"),
    ])
    def test_each_setting_shown_as_recorded_really_is(
        self, recorded: str, phase: str
    ) -> None:
        """The page shows a resolved config carrying these. Each is a
        setting of its phase, each has no fixed default -- so it genuinely
        had nothing to write before -- and each is one its phase now
        records."""
        from fastmdxplora.config.schema import PHASE_SCHEMAS

        field = PHASE_SCHEMAS[phase].get(recorded)
        assert field is not None, f"{phase}.{recorded} is shown in config.md"
        assert field.phase_value is None, (
            f"{phase}.{recorded} has a fixed default and needs no record")
        assert recorded in self._page()

    def test_every_phase_shown_recording_has_somewhere_to_record(
        self
    ) -> None:
        """The claim rests on the records existing and being read."""
        from fastmdxplora.config.generate import _PHASE_RECORDS

        for phase in ("setup", "simulation", "analysis"):
            assert phase in _PHASE_RECORDS

    def test_the_one_derived_setting_not_recorded_is_named(self) -> None:
        """`report.title` is the only value a run derives without writing
        down, and the page says so by name.

        It is placed with the settings that are genuinely unset rather
        than with the ones a replay has to decide again, because the test
        for that is whether losing it changes the science. It does not: a
        replay produces the same trajectory and the same numbers under a
        heading that may be worded differently. The note is wrong the
        moment report starts recording it, or the moment something else
        stops being recorded.
        """
        from fastmdxplora.config.generate import _PHASE_RECORDS
        from fastmdxplora.config.schema import PHASE_SCHEMAS

        assert PHASE_SCHEMAS["report"].get("title").phase_value is None
        assert "report" not in _PHASE_RECORDS, (
            "report records its title now; config.md says it does not")
        assert "`report.title`" in self._page()

    @pytest.mark.parametrize("phase,setting", [
        ("analysis", "include"),
        ("simulation", "production_steps"),
    ])
    def test_the_settings_it_contrasts_the_title_with_are_recorded(
        self, phase: str, setting: str
    ) -> None:
        """The note draws a line: these decide the science and are written
        down, a title is not. The line only holds while they are."""
        from fastmdxplora.config.generate import _PHASE_RECORDS

        assert phase in _PHASE_RECORDS
        assert setting in self._page()

    def test_the_page_does_not_call_the_title_an_outstanding_gap(
        self
    ) -> None:
        """It said "what is still left to the replaying version", which
        frames a heading as a hole in reproducibility. Every setting that
        decides what a run does is recorded; a title is not one."""
        page = self._page()
        assert "still left to the replaying version" not in page

    def test_the_round_trip_trap_is_still_explained(self) -> None:
        """Writing a derived value beside its source is safe only because
        the value is the run's own answer. Losing that sentence loses the
        reason the rule is not general."""
        page = self._page()
        assert "only safe because the number written is the run's own answer" \
            in page
        assert "production_steps" in page and "duration_ns" in page


class TestEveryMethodIsShownAndNotJustOne:
    """Metadynamics had a worked example and umbrella sampling and steered
    dynamics did not, so two of the three things this software exists for
    were documented only as settings in a reference table."""

    @staticmethod
    def _examples() -> str:
        return (DOCS / "examples.md").read_text(encoding="utf-8")

    def test_each_of_the_three_has_a_section(self) -> None:
        page = self._examples()
        for heading in ("## Metadynamics", "## Umbrella sampling",
                        "## Steered molecular dynamics"):
            assert heading in page

    def test_the_umbrella_example_says_windows_must_overlap(self) -> None:
        """The one thing that decides whether a study produces anything."""
        assert "sqrt(kT/k)" in self._examples()

    def test_it_warns_that_a_torsion_is_a_circle(self) -> None:
        """Windows covering part of a turn measure one of the two paths and
        say nothing about the other, which may be the higher."""
        page = self._examples()
        assert "a circle" in page and "tiles the full turn" in page

    def test_the_steered_example_says_where_a_pull_starts(self) -> None:
        page = self._examples()
        assert "refused rather than guessed" in page


class TestThereIsSomewhereElseToRun:
    """A laptop is where a study is designed and rarely where it should run.
    The documentation had one rsync line about that."""

    @staticmethod
    def _page() -> str:
        return (DOCS / "clusters.md").read_text(encoding="utf-8")

    def test_the_page_exists_and_is_in_the_index(self) -> None:
        assert self._page()
        assert "clusters" in (DOCS / "index.md").read_text(encoding="utf-8")

    def test_it_covers_installing_without_a_network(self) -> None:
        page = self._page()
        assert "--no-index" in page
        assert "--system-site-packages" in page

    def test_it_says_how_to_tell_the_gpu_is_being_used(self) -> None:
        """A run on the Reference platform finishes and is correct, so
        nothing else will say it took a hundred times longer than it should."""
        page = self._page()
        assert "Reference" in page and "OPENMM_PLUGIN_DIR" in page

    def test_it_covers_structures_offline(self) -> None:
        """A four-character identifier is fetched from RCSB, which needs a
        network that a cluster may not have."""
        assert "files.rcsb.org" in self._page()


class TestTheDocumentationIsShapedLikeTheSoftware:
    """Three things the structure was saying wrongly.

    The recommended installation was headed "The short version", which reads
    as the hurried option rather than the one to take -- and conda-forge is
    not a preference here: two dependencies have no PyPI distribution at all.

    The Config sat among the things that drive it, when it is the thing they
    all produce. And the Python API was filed under "Reference", which makes
    it look like an appendix rather than one of the three interfaces.
    """

    @staticmethod
    def _index() -> str:
        return (DOCS / "index.md").read_text(encoding="utf-8")

    def test_the_recommendation_is_headed_as_one(self) -> None:
        page = (DOCS / "installation.md").read_text(encoding="utf-8")
        assert "## Recommended: conda-forge" in page
        assert "## The short version" not in page

    def test_the_config_has_its_own_section(self) -> None:
        assert ":caption: The FastMDXplora Config" in self._index()

    def test_the_interfaces_are_listed_together(self) -> None:
        """None of them is primary, so none is filed apart from the others.

        The Agent is the fourth: it writes a Config in place of a person, and
        it is reachable through all three of the others.
        """
        index = self._index()
        rest = index[index.index(":caption: Writing a Config"):]
        block = rest[:rest.index(chr(96) * 3)]
        for page in ("gui", "cli", "api", "agent"):
            assert page in block

    def test_the_config_page_is_titled_as_the_thing(self) -> None:
        """Not "Configuration", and not a YAML file: YAML is the format it is
        written in, which is not what it is."""
        page = (DOCS / "config.md").read_text(encoding="utf-8")
        assert page.startswith("# The FastMDXplora Config")
        assert "format rather than the thing" in page

    def test_the_manifest_has_a_page_of_its_own(self) -> None:
        """The other half of the pair. A Config specifies a study; a Manifest
        records the result of one, and it had no page at all."""
        page = (DOCS / "manifest.md").read_text(encoding="utf-8")
        assert page.startswith("# The FastMDXplora Manifest")
        assert "manifest" in self._index().lower()

    def test_the_agent_has_a_page_of_its_own(self) -> None:
        """It was documented inside the refusals page, under headings nobody
        would search for."""
        page = (DOCS / "agent.md").read_text(encoding="utf-8")
        assert page.startswith("# The FastMDXplora Agent")
        assert "agent" in self._index().lower()

    def test_the_agent_page_says_it_has_no_privileges(self) -> None:
        """The load-bearing claim: what the Agent writes is validated by the
        same code as anything a person writes."""
        page = (DOCS / "agent.md").read_text(encoding="utf-8")
        assert "validate_config" in page
        assert "no privileges" in page.lower()

    def test_developer_material_is_filed_apart(self) -> None:
        """A user-facing page should not carry a post-mortem of a fixed bug."""
        assert ":caption: For developers" in self._index()
        assert (DOCS / "developers.md").is_file()

    def test_nothing_calls_it_a_yaml_file(self) -> None:
        pages = [ROOT / "README.md"] + sorted(DOCS.glob("*.md"))
        named = [p.name for p in pages
                 if p.is_file() and "YAML file" in p.read_text(encoding="utf-8")]
        assert not named, f"these name the format where they mean the Config: {named}"


class TestTheApiReferenceIsComplete:
    """A reference that lists most of the code is worse than none.

    It reads as complete, so a reader who does not find something concludes
    it does not exist rather than that the page is behind. The registry is
    the source of truth, and the page is checked against it.
    """

    @staticmethod
    def _directives() -> set[str]:
        import re
        from pathlib import Path

        docs = Path(__file__).resolve().parents[2] / "docs"
        text = "".join(
            (docs / page).read_text(encoding="utf-8")
            for page in ("api.md", "analyses.md"))
        return set(re.findall(r"\.\. auto(?:module|class):: ([\w\.]+)", text))

    def test_every_registered_analysis_is_documented(self) -> None:
        import fastmdxplora.analysis  # noqa: F401
        from fastmdxplora.analysis.orchestrator import _REGISTRY

        documented = self._directives()
        missing = sorted(
            name for name, cls in _REGISTRY.items()
            if cls.__module__ not in documented
        )
        assert not missing, (
            f"these analyses are registered but absent from the generated "
            f"reference in docs/api.md or docs/analyses.md: {missing}")

    def test_every_directive_resolves(self) -> None:
        """A misspelled target builds an empty section without failing."""
        import importlib

        unresolved = []
        for name in sorted(self._directives()):
            try:
                importlib.import_module(name)
            except ImportError:
                module, _, attribute = name.rpartition(".")
                try:
                    assert hasattr(importlib.import_module(module), attribute)
                except Exception:  # noqa: BLE001
                    unresolved.append(name)
        assert not unresolved, (
            f"the API reference names things that do not exist: {unresolved}")

    def test_the_public_entry_points_are_there(self) -> None:
        import fastmdxplora

        documented = self._directives()
        for name in ("FastMDXplora", "AnalysisOrchestrator"):
            assert name in fastmdxplora.__all__
            assert f"fastmdxplora.{name}" in documented


class TestThePagesThatEnumerateTheSchemaAreCurrent:
    """Four counts and one list that had drifted by 2026-09-25.

    Each was written true and went stale as the schema grew, because
    nothing recomputed it. `docs/config.md` said fourteen top-level keys
    against fifteen accepted and did not document `budget_hours` at all,
    while claiming nothing else is accepted; it said simulation carries 41
    settings against 44; `docs/developers.md` said fifteen harness
    requests against fourteen; `docs/gui.md` listed two endpoints that no
    longer exist.

    The endpoint one is the sharpest: the page is the reference for what
    the server refuses off loopback, so a name in that table that the
    server does not have reads as a promise about a route nobody can call.
    """

    @staticmethod
    def _page(name: str) -> str:
        import re

        return re.sub(r"\s+", " ", (DOCS / name).read_text(encoding="utf-8"))

    def test_the_top_level_key_count_is_the_real_one(self) -> None:
        from fastmdxplora.config.loader import TOP_LEVEL_KEYS

        words = {14: "fourteen", 15: "fifteen", 16: "sixteen",
                 17: "seventeen", 18: "eighteen"}
        spelled = words.get(len(TOP_LEVEL_KEYS))
        assert spelled, f"no word for {len(TOP_LEVEL_KEYS)}; extend the map"
        assert f"The {spelled} top-level keys" in self._page("config.md")

    def test_every_top_level_key_is_in_a_table_on_that_page(self) -> None:
        """The page says nothing else is accepted, so a key it omits is a
        key a reader cannot find. `budget_hours` was missing."""
        from fastmdxplora.config.loader import TOP_LEVEL_KEYS

        page = self._page("config.md")
        missing = [key for key in sorted(TOP_LEVEL_KEYS)
                   if f"`{key}`" not in page]
        assert not missing, f"undocumented top-level keys: {missing}"

    @pytest.mark.parametrize("phase", ["setup", "simulation", "analysis",
                                       "report"])
    def test_each_phase_setting_count_is_the_real_one(self, phase: str) -> None:
        from fastmdxplora.config.schema import PHASE_SCHEMAS

        total = len(PHASE_SCHEMAS[phase].fields)
        assert f"| `{phase}` | {total} settings" in self._page("config.md")

    def test_the_harness_request_count_is_the_real_one(self) -> None:
        from fastmdxplora.agent.evaluate import REQUESTS

        words = {13: "Thirteen", 14: "Fourteen", 15: "Fifteen",
                 16: "Sixteen", 17: "Seventeen"}
        spelled = words.get(len(REQUESTS))
        assert spelled, f"no word for {len(REQUESTS)}; extend the map"
        assert f"{spelled} requests across three tiers" in \
            self._page("developers.md")

    def test_every_endpoint_the_gui_page_names_exists(self) -> None:
        """Read from the server rather than from a list kept beside it."""
        import re

        import fastmdxplora.gui as gui

        server = (Path(gui.__file__).parent / "server.py").read_text(
            encoding="utf-8")
        served = set(re.findall(r'"(/api/[a-z0-9/_-]+)"', server))
        named = set(re.findall(r"`(/api/[a-z0-9/_-]+)`",
                               (DOCS / "gui.md").read_text(encoding="utf-8")))
        assert named, "the page names no endpoints; the regex has drifted"
        gone = sorted(named - served)
        assert not gone, f"docs/gui.md names endpoints the server lacks: {gone}"
