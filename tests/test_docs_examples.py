"""Lightweight checks for documented command examples.

These tests keep README/docs snippets from drifting away from the CLI surface.
They intentionally parse commands rather than executing MD-heavy workflows.
"""

from __future__ import annotations

import shlex
import subprocess
import sys

import pytest

from fastmdxplora.cli.main import _build_parser
from fastmdxplora.config import validate_config
from scripts.run_pdb_smoke_campaign import build_parser as build_campaign_parser


@pytest.mark.parametrize(
    "command",
    [
        "explore --system protein.pdb --dry-run",
        "xplore -s 1L2Y --dry-run",
        (
            "explore -s protein.pdb --setup-ph 7.4 "
            "--simulate-duration-ns 100 --simulate-platform CUDA --dry-run"
        ),
        (
            "explore -s protein.pdb --include setup simulation "
            "--simulate-production-steps 5000 --simulate-platform CPU --dry-run"
        ),
        (
            "explore --system local_pdbs/1L2Y.pdb "
            "--output local_runs/trpcage_live_full "
            "--include setup simulation analysis report "
            "--simulate-production-steps 5000 --dashboard"
        ),
        "setup --system protein.pdb --ph 6.5 --box-shape octahedron",
        (
            "simulate --system protein.pdb --output ./trpcage_study "
            "--duration-ns 50.0 --platform CUDA --dashboard"
        ),
        "analyze --output ./trpcage_study --analyses rmsd rg --selection 'name CA'",
        "report --output ./trpcage_study --no-slides --dashboard",
        "gui --output ./trpcage_study --port 8765",
        "init-config --minimal -o study.yml",
        "info",
    ],
)
def test_documented_fastmdx_commands_parse(command: str) -> None:
    parser = _build_parser()
    parser.parse_args(shlex.split(command))


def test_documented_campaign_command_parses() -> None:
    parser = build_campaign_parser()
    args = parser.parse_args(
        shlex.split(
            "--input-list examples/pdb_list.txt "
            "--output-root runs/pdb_smoke_starter "
            "--production-steps 5000 --continue-on-error"
        )
    )

    assert args.production_steps == 5000
    assert args.continue_on_error is True


def test_documented_module_entrypoint_fallback_runs() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "fastmdxplora.cli.main", "--version"],
        check=True,
        text=True,
        capture_output=True,
    )

    assert "fastmdx" in result.stdout


def test_documented_configuration_shape_validates() -> None:
    validate_config(
        {
            "systems": [{"id": "trpcage", "system": "1L2Y"}],
            "setup": {"ph": 7.0, "forcefield": "charmm36"},
            "simulation": {"duration_ns": 10},
            "analysis": {"scope": "solute", "include": ["rmsd", "rmsf", "rg"]},
            "report": {
                "title": "My study",
                "slides": True,
                "comparison": True,
                "region_highlights": [
                    {
                        "label": "example flexible loop",
                        "start": 3,
                        "end": 7,
                        "color": "#4E79A7",
                    }
                ],
            },
        },
        require_systems=True,
    )


def test_the_metadynamics_examples_in_the_docs_actually_plan() -> None:
    """A documented example the software refuses is worse than none.

    `docs/simulations.md` was written before walls existed and showed a
    ligand_distance block with no bound -- which the software then began
    refusing, correctly, leaving the page telling people to run something that
    stops. The examples are parsed and planned here so that cannot happen
    quietly again.
    """
    import re
    from pathlib import Path

    import pytest

    pytest.importorskip("yaml")
    import mdtraj as md
    import yaml

    from fastmdxplora.simulation.metadynamics import plan_from_config

    page = Path(__file__).resolve().parents[1] / "docs" / "simulations.md"
    if not page.is_file():  # pragma: no cover - the page is optional
        return

    top = md.Topology()
    chain = top.add_chain()
    for index in range(240):
        residue = top.add_residue("ALA", chain, resSeq=index + 1)
        for name in ("N", "CA", "C", "O"):
            top.add_atom(name, md.element.carbon, residue)
    ligand_chain = top.add_chain()
    ligand = top.add_residue("BNZ", ligand_chain, resSeq=900)
    for index in range(6):
        top.add_atom(f"C{index}", md.element.carbon, ligand)

    blocks = re.findall(r"```yaml\n(simulation:\n  metadynamics:.*?)```",
                        page.read_text(encoding="utf-8"), re.S)
    assert blocks, "the page should show at least one worked example"

    for block in blocks:
        spec = yaml.safe_load(block)["simulation"]["metadynamics"]
        spec.setdefault("ligand_resname", "BNZ")
        # Raises if the example is one the software would refuse.
        plan_from_config(spec, top)


def test_every_page_is_in_the_documentation_navigation() -> None:
    """A page nobody can reach from the contents is a page nobody reads.

    The navigation was one flat list of thirteen entries under a single
    caption, with the production guide ahead of the phases it describes and a
    design note missing from it entirely. It is grouped now, in the order a
    new user meets things.
    """
    from pathlib import Path

    docs = Path(__file__).resolve().parents[1] / "docs"
    if not docs.is_dir():  # pragma: no cover - docs are optional
        return

    index = (docs / "index.md").read_text(encoding="utf-8")
    pages = {p.stem for p in docs.glob("*.md")} - {"index"}
    missing = sorted(page for page in pages if page not in index)
    assert not missing, f"these pages are in no toctree: {missing}"


def test_the_navigation_is_grouped() -> None:
    """Thirteen entries under one caption is a list, not an order."""
    from pathlib import Path

    index = (Path(__file__).resolve().parents[1] / "docs" / "index.md")
    if not index.is_file():  # pragma: no cover
        return
    captions = [line for line in index.read_text(encoding="utf-8").splitlines()
                if line.startswith(":caption:")]
    assert len(captions) >= 4, (
        f"the documentation should be grouped; found {len(captions)} section(s)"
    )


def test_the_readme_points_at_every_important_page() -> None:
    """The README's documentation section is the map. A capability documented
    on a page nothing links to is one nobody finds."""
    from pathlib import Path

    repo = Path(__file__).resolve().parents[1]
    readme = (repo / "README.md").read_text(encoding="utf-8")
    for page in ("installation", "getting_started", "phases", "simulations",
                 "interactions", "cli_reference", "configuration", "gui"):
        assert f"{page}.html" in readme, f"the README does not link {page}"


def test_the_gui_is_called_the_gui() -> None:
    """It is the FastMDXplora GUI, not "a browser". A browser is what you open
    it in, and the two are worth telling apart in a document somebody reads to
    learn what the software is called."""
    from pathlib import Path

    repo = Path(__file__).resolve().parents[1]
    pages = [repo / "README.md"] + sorted((repo / "docs").glob("*.md"))

    wrong = []
    for page in pages:
        if not page.is_file():
            continue
        text = page.read_text(encoding="utf-8")
        for phrase in ("browser interface", "the browser dashboard",
                       "watch one in a browser"):
            if phrase in text:
                wrong.append(f"{page.name}: {phrase!r}")
    assert not wrong, f"these call the GUI something else: {wrong}"


def test_every_command_the_docs_name_exists() -> None:
    """`fastmdx health --no-fix` was documented in three pages and had never
    existed. A documented command that is not there sends somebody to a usage
    error on their first attempt at diagnosing something.
    """
    import re
    from pathlib import Path

    from fastmdxplora.cli.main import _build_parser

    parser = _build_parser()
    commands = set(parser._subparsers._group_actions[0].choices)

    repo = Path(__file__).resolve().parents[1]
    pages = [repo / "README.md"] + sorted((repo / "docs").glob("*.md"))

    missing = {}
    for page in pages:
        if not page.is_file():
            continue
        for match in re.finditer(r"^\s*(?:\$ )?fastmdx\s+([a-z][a-z-]+)",
                                 page.read_text(encoding="utf-8"), re.M):
            name = match.group(1)
            if name not in commands and not name.startswith("-"):
                missing.setdefault(page.name, set()).add(name)
    assert not missing, f"these commands are documented and do not exist: {missing}"


def test_every_fastmdx_flag_the_docs_name_exists() -> None:
    """`fastmdx gui --open-browser` was documented; the flag is
    `--no-browser`, and the default is the opposite of what the page implied.
    """
    import re
    from pathlib import Path

    from fastmdxplora.cli.main import _build_parser

    known = set()

    def collect(parser):
        for action in parser._actions:
            known.update(action.option_strings)
            if hasattr(action, "choices") and isinstance(action.choices, dict):
                for child in action.choices.values():
                    collect(child)

    collect(_build_parser())

    repo = Path(__file__).resolve().parents[1]
    pages = [repo / "README.md"] + sorted((repo / "docs").glob("*.md"))

    #: Flags belonging to other tools, which the docs legitimately mention.
    OTHERS = {
        "--upgrade", "--no-deps", "--format", "--query-gpu", "--delete",
        "--input-list", "--output-root", "--continue-on-error", "--no-cache-dir",
        "--force-reinstall", "--dry-run", "--editable", "--user", "--prefix",
        "--version", "--help", "--extra-index-url", "--index-url",
        # Installing where there is no network, and submitting to a
        # scheduler: pip's, venv's and SBATCH's, named on lines that also
        # mention fastmdx.
        "--no-index", "--find-links", "--only-binary", "--python-version",
        "--platform", "--system-site-packages", "--job-name", "--gres",
        "--cpus-per-task", "--time", "--nv", "--fakeroot",
        "--download-only", "--offline",
    }

    missing = {}
    for page in pages:
        if not page.is_file():
            continue
        text = page.read_text(encoding="utf-8")
        # Only flags on a line that mentions fastmdx, so pip's and rsync's
        # are not counted as ours.
        for line in text.splitlines():
            if "fastmdx" not in line and not line.strip().startswith("--"):
                continue
            for match in re.finditer(r"(?<![\w-])(--[a-z][a-z0-9-]{3,})(?![\w-])",
                                     line):
                flag = match.group(1)
                if flag not in known and flag not in OTHERS:
                    missing.setdefault(page.name, set()).add(flag)
    assert not missing, f"these flags are documented and do not exist: {missing}"


def test_the_phases_page_lists_every_analysis() -> None:
    """It is the page everything else links to for what gets measured, and it
    fell four features behind: omega dihedrals, MDS, the per-residue SASA
    average and the clustering seed were all added without it changing.

    A reference that is missing what was added is worse than one that is
    obviously old, because nothing about it looks wrong.
    """
    from pathlib import Path

    import fastmdxplora.analysis  # noqa: F401
    from fastmdxplora.analysis.describe import describe_all

    page = Path(__file__).resolve().parents[1] / "docs" / "phases.md"
    if not page.is_file():  # pragma: no cover
        return
    text = page.read_text(encoding="utf-8")

    missing = sorted(name for name in describe_all() if f"`{name}`" not in text)
    assert not missing, f"these analyses are not in the phases page: {missing}"


def test_an_analysis_describes_what_it_currently_does() -> None:
    """The summary comes from the module docstring and reaches the GUI, the
    report and the docs. Three of them still said what the analysis did before
    it was extended -- dihedrals claimed phi/psi after omega was added, dimred
    claimed three methods after MDS made four.
    """
    import fastmdxplora.analysis  # noqa: F401
    from fastmdxplora.analysis import dihedrals, dimred, sasa
    from fastmdxplora.analysis.describe import explain_analysis

    dihedral_text = (explain_analysis("dihedrals").get("detail") or "").lower()
    assert "omega" in dihedral_text, (
        "dihedrals computes omega and its description should say so"
    )

    dimred_text = (explain_analysis("dimred").get("detail") or "").lower()
    assert "mds" in dimred_text

    sasa_text = (explain_analysis("sasa").get("detail") or "").lower()
    assert "mean" in sasa_text or "average" in sasa_text

    # And the constants they describe.
    assert "omega" in dihedrals.VALID_ANGLES
    assert "mds" in dimred.VALID_METHODS
    assert "average_residue" in sasa.VALID_MODES


def test_region_highlights_are_documented_where_they_are_produced() -> None:
    """They had a page of their own; they are one figure option, so they live
    in the report phase's section now.

    The first rewrite of that page dropped a whole capability -- the regions
    are drawn on the structure with PyMOL as well as on the RMSF trace -- so
    what the feature produces is checked rather than described from memory.
    """
    from pathlib import Path

    page = Path(__file__).resolve().parents[1] / "docs" / "phases.md"
    text = page.read_text(encoding="utf-8")

    for artifact in ("rmsf_region_highlights.png",
                     "structure_region_highlights.png",
                     ".pml"):
        assert artifact in text, f"the report phase does not mention {artifact}"
    assert "pymol-open-source" in text, (
        "the structure rendering needs PyMOL and the page should say how to "
        "get it"
    )


def test_the_smoke_campaign_is_documented_for_contributors() -> None:
    """It is a maintainer's script for finding what breaks before a release,
    not something a user of FastMDXplora runs, so it is in CONTRIBUTING rather
    than the user documentation.

    The statuses are the point of it: software that refuses cleanly and
    software that breaks look the same in a pass/fail count.
    """
    from pathlib import Path

    repo = Path(__file__).resolve().parents[1]
    guide = (repo / "CONTRIBUTING.md").read_text(encoding="utf-8")
    script = (repo / "scripts" / "run_pdb_smoke_campaign.py").read_text(
        encoding="utf-8")

    assert "Smoke campaigns" in guide

    for status in ("ok", "expected_limitation", "validation_failed", "failed",
                   "skipped"):
        assert f'"{status}"' in script, f"{status} is not a status the script uses"
        assert status in guide, f"CONTRIBUTING does not explain {status}"

    for artifact in ("campaign_summary.csv", "campaign_summary.json"):
        assert artifact in script and artifact in guide


def test_it_is_not_in_the_user_documentation() -> None:
    """A user landing on a contributor's release workflow learns nothing they
    can use."""
    from pathlib import Path

    docs = Path(__file__).resolve().parents[1] / "docs"
    stray = [p.name for p in docs.glob("*.md")
             if "run_pdb_smoke_campaign.py" in p.read_text(encoding="utf-8")]
    assert not stray, f"the campaign is documented for users in: {stray}"


def test_the_smoke_campaign_flags_exist() -> None:
    """Its flags are the script's, not the CLI's, so the general flag guard
    does not cover them."""
    import re
    from pathlib import Path

    repo = Path(__file__).resolve().parents[1]
    guide = (repo / "CONTRIBUTING.md").read_text(encoding="utf-8")
    script = (repo / "scripts" / "run_pdb_smoke_campaign.py").read_text(
        encoding="utf-8")

    section = guide[guide.index("## Smoke campaigns"):]
    section = section[:section.index("\n## ", 10)] if "\n## " in section[10:] else section
    documented = {
        match.group(1)
        for match in re.finditer(r"(?<![\w-])(--[a-z][a-z0-9-]{3,})(?![\w-])",
                                 section)
    }
    missing = sorted(flag for flag in documented if f'"{flag}"' not in script)
    assert not missing, f"these campaign flags do not exist: {missing}"


def test_every_documentation_link_resolves() -> None:
    """A link to a page that no longer exists.

    Two pages moved -- the smoke campaign into CONTRIBUTING, region highlights
    into the report phase -- and the README went on linking one of them. The
    guards that covered those pages did not notice, because each returned
    early when its page was absent: they went quiet rather than failing, which
    is the worst way for a check to react to the thing it checks disappearing.
    """
    import re
    from pathlib import Path

    repo = Path(__file__).resolve().parents[1]
    docs = repo / "docs"
    pages = {p.stem for p in docs.glob("*.md")}

    broken = []

    # Relative links between documentation pages.
    for page in docs.glob("*.md"):
        for match in re.finditer(r"\]\(([a-z_]+)\.md(?:#[^)]*)?\)",
                                 page.read_text(encoding="utf-8")):
            if match.group(1) not in pages:
                broken.append(f"{page.name} -> {match.group(1)}.md")

    # And the README's links into the published site.
    readme = (repo / "README.md").read_text(encoding="utf-8")
    for match in re.finditer(
            r"readthedocs\.io/en/latest/([a-z_]+)\.html", readme):
        if match.group(1) not in pages:
            broken.append(f"README.md -> {match.group(1)}.html")

    assert not broken, f"these links point at pages that do not exist: {broken}"


def test_the_yaml_examples_use_keys_the_software_reads() -> None:
    """Every documented `systems:` block said `label:`, which nothing reads.

    The loader accepts it -- it does not validate the keys inside a system
    entry -- so the example ran and quietly did nothing. The key is `id`, and
    it names the run's directory and its label in the comparison report.

    This checks the examples against `normalize_systems`, which is what
    actually consumes them.
    """
    import re
    from pathlib import Path

    import pytest

    pytest.importorskip("yaml")
    import yaml

    from fastmdxplora.batch.sweep import normalize_systems

    repo = Path(__file__).resolve().parents[1]
    pages = [repo / "README.md"] + sorted((repo / "docs").glob("*.md"))

    checked = 0
    for page in pages:
        if not page.is_file():
            continue
        for block in re.findall(r"```yaml\n(.*?)```",
                                page.read_text(encoding="utf-8"), re.S):
            try:
                parsed = yaml.safe_load(block)
            except yaml.YAMLError:
                continue
            if not isinstance(parsed, dict) or "systems" not in parsed:
                continue
            # Raises if an entry is malformed; ids must be unique.
            normalized = normalize_systems(parsed["systems"])
            checked += 1

            for original, _result in zip(parsed["systems"], normalized):
                stray = set(original) - {"system", "id"} - {
                    "setup", "simulation", "analysis", "report"}
                assert not stray, (
                    f"{page.name}: a system entry uses {sorted(stray)}, which "
                    "nothing reads. The key is `id`; phase names are "
                    "per-system overrides."
                )
    assert checked, "no systems example was found to check"


def test_the_documentation_names_the_software() -> None:
    """"We" and "here" leave a reader working out who is speaking and where
    "here" is -- in a page that may have arrived from a search engine with no
    surrounding context.

    The implementation note was written as a working document and said "we
    know" and "not here". It says FastMDXplora, which is unambiguous wherever
    the page is read.
    """
    import re
    from pathlib import Path

    repo = Path(__file__).resolve().parents[1]
    pages = [repo / "README.md"] + sorted((repo / "docs").glob("*.md"))

    vague = {}
    for page in pages:
        if not page.is_file():
            continue
        text = page.read_text(encoding="utf-8")
        # Outside code blocks, where "we" may appear in a quoted message.
        prose = re.sub(r"```.*?```", "", text, flags=re.S)
        found = set()
        for match in re.finditer(r"(?<![\w-])(we|our|We|Our)(?![\w-])", prose):
            found.add(match.group(1).lower())
        if found:
            vague[page.name] = sorted(found)

    assert not vague, (
        f"these pages speak as 'we' rather than naming the software: {vague}"
    )


def test_the_readme_headline_example_needs_no_flags() -> None:
    """The README opens with `fastmdx explore --system 181L`, which is a
    protein-ligand structure.

    That works only because the default force field resolves to a
    ligand-capable one. If the default ever became a protein-only field, the
    first command anybody runs from the README would fail on the ligand.
    """
    from pathlib import Path

    from fastmdxplora.config.schema import PHASE_SCHEMAS
    from fastmdxplora.setup.forcefields import AUTO_FORCEFIELD

    assert PHASE_SCHEMAS["setup"].get("forcefield").default == "auto"
    assert "openff" in AUTO_FORCEFIELD, (
        "auto must resolve to a ligand-capable force field, or the README's "
        "first example fails on 181L's benzene"
    )

    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(
        encoding="utf-8")
    assert "fastmdx explore --system 181L" in readme


class TestTheDocumentationQuotesWhatTheSoftwareSays:
    """A page that quotes a message the software does not print is worse than
    one that describes it: a reader checks the quote against their terminal
    and finds it different.

    The first draft of the explanations section paraphrased from memory and
    got three clauses wrong.
    """

    def test_the_explanation_the_page_shows_is_the_one_that_is_printed(
        self
    ) -> None:
        import pathlib
        import re

        from fastmdxplora.explain import EXPLANATIONS

        page = (pathlib.Path(__file__).resolve().parents[1]
                / "docs" / "getting_started.md").read_text(encoding="utf-8")
        block = page[page.index("▸ Minimizing energy"):]
        quoted = block[:block.index("```")]
        # The page wraps and uses an em dash where the terminal writes two
        # hyphens; neither changes the words.
        flattened = re.sub(r"\s+", " ", quoted).replace("—", "--").strip()
        flattened = flattened.removeprefix("▸ Minimizing energy").strip()

        assert flattened == EXPLANATIONS["minimize"].why

    def test_the_count_the_page_gives_is_the_count_there_is(self) -> None:
        import pathlib

        from fastmdxplora.explain import EXPLANATIONS

        page = (pathlib.Path(__file__).resolve().parents[1]
                / "docs" / "getting_started.md").read_text(encoding="utf-8")
        words = {14: "Fourteen", 15: "Fifteen", 16: "Sixteen",
                 17: "Seventeen", 18: "Eighteen"}
        assert words[len(EXPLANATIONS)] in page, (
            f"there are {len(EXPLANATIONS)} explanations; the page says "
            "otherwise. Say the new number there too.")

    def test_the_variables_the_page_tabulates_are_the_variables_there_are(
        self
    ) -> None:
        """The table listed five when there were eight -- the same drift as
        the schema help, in a second place."""
        import pathlib

        from fastmdxplora.simulation.metadynamics import COLLECTIVE_VARIABLES

        page = (pathlib.Path(__file__).resolve().parents[1]
                / "docs" / "simulations.md").read_text(encoding="utf-8")
        for variable in COLLECTIVE_VARIABLES:
            assert f"`{variable}`" in page, variable


class TestNobodyCountsTheAnalysesTwice:
    """The README said the trajectory is analysed 'fifteen ways' and the
    `include` help said 'all ten'. There are sixteen registered.

    A number in prose is a copy of something the code knows, and every copy
    goes stale. These fail when one is added, which is when the sentences can
    still be fixed cheaply.
    """

    def test_the_help_does_not_name_a_number_that_will_drift(self) -> None:
        """It says which analyses run rather than how many, because 'ten
        always, water sites where there is water, five more where there is a
        ligand' stays true as the registry grows in the ligand-aware part."""
        from fastmdxplora.analysis import available_analyses
        from fastmdxplora.config.schema import PHASE_SCHEMAS

        described = PHASE_SCHEMAS["analysis"].get("include").help
        assert "all ten" not in described
        assert "nine always" in described

        # The split, not the total. Ten unconditional, water sites where
        # there is water, five where there is a ligand, and the potential of
        # mean force where an umbrella study produced one -- so the registry
        # is those groups and nothing else. Counting to a number instead
        # meant the test failed on any addition and said only that the
        # number had moved, when what needs updating is the sentence.
        from fastmdxplora.analysis.orchestrator import _REGISTRY

        # Derived rather than listed. Naming each gate meant a new one --
        # `requires_metadynamics` -- was silently not counted, and the test
        # failed saying the total had moved rather than that a condition had
        # been added.
        conditional = {
            name for name, cls in _REGISTRY.items()
            if any(getattr(cls, attribute, False)
                   for attribute in dir(cls)
                   if attribute.startswith("requires_"))
        }
        assert len(available_analyses()) - len(conditional) == 9, (
            "the help says nine analyses run unconditionally; if that has "
            "changed, say the new split there too")
        assert "where there is a ligand" in described
        assert "umbrella" in described

    def test_the_readme_does_not_count_them_either(self) -> None:
        import pathlib
        import re

        readme = (pathlib.Path(__file__).resolve().parents[1]
                  / "README.md").read_text(encoding="utf-8")
        assert not re.search(
            r"analys\w+ the\s+trajectory\s+\w+\s+ways", readme, re.I), (
            "a count in the opening paragraph is a copy that goes stale")


class TestTheInterfaceIsCalledTheGUI:
    """The GUI is the interface; a browser is where it happens to render.

    Documentation and comments used "the browser" for both, so the product and
    its rendering surface had the same name -- and the three interfaces read
    as "the command line, a config file, and the browser".
    """

    def test_the_docs_name_the_interface_consistently(self) -> None:
        import pathlib

        root = pathlib.Path(__file__).resolve().parents[1]
        for page in ("docs/gui.md", "docs/simulations.md", "README.md"):
            text = (root / page).read_text(encoding="utf-8")
            for line in text.splitlines():
                if "browser" not in line:
                    continue
                # A browser tab, and the flag that suppresses it, are the
                # literal thing and keep the word.
                assert ("browser tab" in line or "--no-browser" in line), (
                    f"{page}: {line.strip()[:80]}")


def test_the_source_names_the_software_too():
    """The same rule the documentation is held to, in comments and docstrings.

    `test_the_documentation_names_the_software` covers `docs/` and caught a
    page saying "our handler". The habit is not confined to pages: source
    comments said "we", "our" and "ours" in fifty-odd places, and a reader
    opening one of those files has exactly the same question -- whose?

    Worth a rule because the register is invisible to whoever is writing
    in it. Worth a test because the rule is invisible to whoever writes
    next.

    Only comments and docstrings. Identifiers, string data and anything a
    user typed are none of this test's business.
    """
    import ast
    import pathlib
    import re

    import fastmdxplora

    root = pathlib.Path(fastmdxplora.__file__).parent
    speaking_as_we = re.compile(r"\b(we|our|ours|us)\b", re.IGNORECASE)
    offenders: dict[str, list[str]] = {}

    for path in sorted(root.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        found: list[str] = []

        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
                doc = ast.get_docstring(node)
                if doc:
                    found += [m.group(0) for m in speaking_as_we.finditer(doc)]

        for line in source.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                found += [m.group(0) for m in speaking_as_we.finditer(stripped)]

        if found:
            offenders[path.relative_to(root).as_posix()] = sorted(set(found))

    assert not offenders, (
        "these speak as 'we' rather than naming FastMDXplora: "
        f"{offenders}. A reader opening the file does not know who 'we' is, "
        "and the docs are already held to this.")
