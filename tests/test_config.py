"""Tests for the YAML configuration system.

Covers:
  - Schema registry integrity
  - Loading + strict validation (unknown keys, did-you-mean, type errors)
  - include/exclude mutual exclusion
  - Override precedence (flags/kwargs beat file beat defaults)
  - Template generation (comprehensive + minimal), and that the generated
    template is itself valid
  - Resolved-config dump round-trips
  - End-to-end through FastMDXplora(config=...) and the CLI
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from fastmdxplora import FastMDXplora
from fastmdxplora.config import (
    ConfigError,
    PHASE_SCHEMAS,
    generate_template,
    load_config_file,
    phase_options,
    validate_config,
    write_resolved_config,
)
from fastmdxplora.config.schema import all_schemas
from fastmdxplora.cli.main import main as cli_main


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------
def _write_yaml(tmp_path: Path, text: str, name: str = "study.yml") -> Path:
    p = tmp_path / name
    p.write_text(text)
    return p


# A minimal valid `systems:` list for validation tests (systems is the
# canonical, required input form).
SYS = [{"id": "a", "system": "x.pdb"}]


@pytest.fixture
def stub_pdb(tmp_path: Path) -> Path:
    p = tmp_path / "protein.pdb"
    p.write_text(
        # A tripeptide, not a lone residue: one amino acid is simultaneously
        # N- and C-terminal, and AMBER has no template for that. CHARMM36
        # tolerated it, so this fixture survived until the default changed.
        "ATOM      1  N   ALA A   1       0.000   0.000   0.000  1.00  0.00           N\n"
        "ATOM      2  CA  ALA A   1       1.458   0.000   0.000  1.00  0.00           C\n"
        "ATOM      3  C   ALA A   1       2.009   1.420   0.000  1.00  0.00           C\n"
        "ATOM      4  O   ALA A   1       1.251   2.390   0.000  1.00  0.00           O\n"
        "ATOM      5  CB  ALA A   1       1.988  -0.773  -1.199  1.00  0.00           C\n"
        "ATOM      6  N   GLY A   2       3.332   1.549   0.000  1.00  0.00           N\n"
        "ATOM      7  CA  GLY A   2       3.972   2.849   0.000  1.00  0.00           C\n"
        "ATOM      8  C   GLY A   2       5.486   2.705   0.000  1.00  0.00           C\n"
        "ATOM      9  O   GLY A   2       6.008   1.593   0.000  1.00  0.00           O\n"
        "ATOM     10  N   ALA A   3       6.171   3.845   0.000  1.00  0.00           N\n"
        "ATOM     11  CA  ALA A   3       7.623   3.845   0.000  1.00  0.00           C\n"
        "ATOM     12  C   ALA A   3       8.174   5.265   0.000  1.00  0.00           C\n"
        "ATOM     13  O   ALA A   3       7.416   6.235   0.000  1.00  0.00           O\n"
        "ATOM     14  CB  ALA A   3       8.153   3.072  -1.199  1.00  0.00           C\n"
        "ATOM     15  OXT ALA A   3       9.400   5.400   0.000  1.00  0.00           O\n"
        "END\n"
    )
    return p


# ===========================================================================
# Schema integrity
# ===========================================================================
class TestSchema:
    def test_all_phases_present(self):
        assert set(PHASE_SCHEMAS) == {"setup", "simulation", "analysis", "report"}

    def test_each_field_has_help(self):
        for schema in all_schemas().values():
            for fld in schema.fields:
                assert fld.help, f"{schema.name}.{fld.name} missing help text"

    def test_field_names_unique_within_phase(self):
        for schema in all_schemas().values():
            names = [f.name for f in schema.fields]
            assert len(names) == len(set(names)), f"dup in {schema.name}"

    def test_setup_defaults_match_phase(self):
        """Schema defaults should match the setup phase's DEFAULTS where set."""
        from fastmdxplora.setup.pipeline import DEFAULTS as SETUP_DEFAULTS
        setup = PHASE_SCHEMAS["setup"]
        # Spot-check a few non-None defaults
        assert setup.get("ph").default == SETUP_DEFAULTS["ph"]
        assert setup.get("ion_concentration_M").default == SETUP_DEFAULTS["ion_concentration_M"]
        assert setup.get("box_shape").default == SETUP_DEFAULTS["box_shape"]


# ===========================================================================
# Loading
# ===========================================================================
class TestLoading:
    def test_load_valid(self, tmp_path):
        p = _write_yaml(tmp_path, "system: x.pdb\noutput: ./out\n")
        data = load_config_file(p)
        assert data["system"] == "x.pdb"
        assert data["output"] == "./out"

    def test_load_missing_file_raises(self, tmp_path):
        with pytest.raises(ConfigError, match="not found"):
            load_config_file(tmp_path / "nope.yml")

    def test_load_empty_file_is_empty_dict(self, tmp_path):
        p = _write_yaml(tmp_path, "")
        assert load_config_file(p) == {}

    def test_load_non_mapping_raises(self, tmp_path):
        p = _write_yaml(tmp_path, "- just\n- a\n- list\n")
        with pytest.raises(ConfigError, match="mapping at the top level"):
            load_config_file(p)

    def test_load_bad_yaml_raises(self, tmp_path):
        p = _write_yaml(tmp_path, "system: [unclosed\n")
        with pytest.raises(ConfigError, match="Failed to parse YAML"):
            load_config_file(p)


# ===========================================================================
# Validation
# ===========================================================================
class TestValidation:
    def test_valid_config_passes(self):
        validate_config({
            "systems": SYS,
            "setup": {"ph": 7.4},
            "simulation": {"duration_ns": 100.0},
        })

    def test_unknown_top_level_key_raises(self):
        with pytest.raises(ConfigError, match="Unknown top-level key 'boguskey'"):
            validate_config({"systems": SYS, "boguskey": 1})

    def test_unknown_top_level_suggests(self):
        with pytest.raises(ConfigError, match="did you mean 'systems'"):
            validate_config({"systms": SYS})

    def test_unknown_phase_block_suggests(self):
        with pytest.raises(ConfigError, match="did you mean 'simulation'"):
            validate_config({"systems": SYS, "simulaton": {"duration_ns": 1}})

    def test_unknown_phase_option_raises_with_suggestion(self):
        with pytest.raises(ConfigError, match="did you mean 'ph'"):
            validate_config({"systems": SYS, "setup": {"pH": 7.4}})

    def test_type_error_raises(self):
        with pytest.raises(ConfigError, match="should be number"):
            validate_config({"systems": SYS, "setup": {"ph": "high"}})

    def test_bool_not_accepted_for_numeric(self):
        """ph: true must not slip through as an int (bool is int subclass)."""
        with pytest.raises(ConfigError, match="should be number"):
            validate_config({"systems": SYS, "setup": {"ph": True}})

    def test_int_accepted_for_float_field(self):
        # temperature_K declared (int, float) — an int must pass
        validate_config({"systems": SYS, "simulation": {"temperature_K": 300}})

    def test_include_exclude_mutually_exclusive_top(self):
        with pytest.raises(ConfigError, match="mutually exclusive"):
            validate_config({
                "systems": SYS,
                "include": ["setup"],
                "exclude": ["report"],
            })

    def test_top_level_include_rejects_unknown_phase(self):
        with pytest.raises(ConfigError, match="unknown phase"):
            validate_config({"systems": SYS, "include": ["setupp"]})

    def test_top_level_exclude_rejects_unknown_phase(self):
        with pytest.raises(ConfigError, match="unknown phase"):
            validate_config({"systems": SYS, "exclude": ["reports"]})

    def test_analysis_include_exclude_mutually_exclusive(self):
        with pytest.raises(ConfigError, match="mutually exclusive"):
            validate_config({
                "systems": SYS,
                "analysis": {"include": ["rmsd"], "exclude": ["rg"]},
            })

    def test_phase_block_must_be_mapping(self):
        with pytest.raises(ConfigError, match="must be a mapping"):
            validate_config({"systems": SYS, "setup": "not a dict"})

    def test_null_value_allowed(self):
        # Explicit null means "use default" — should not type-error
        validate_config({"systems": SYS, "setup": {"water_model": None}})

    def test_systems_required_when_asked(self):
        with pytest.raises(ConfigError, match="must define a `systems:` list"):
            validate_config({"setup": {"ph": 7.0}}, require_systems=True)

    def test_systems_not_required_by_default(self):
        # Fragment validation (no require_systems) tolerates a missing list
        validate_config({"setup": {"ph": 7.0}})

    def test_execution_block_validated(self):
        validate_config({
            "systems": SYS,
            "execution": {"mode": "parallel", "workers": 2, "devices": [0, 1]},
        })

    def test_execution_bad_mode_rejected(self):
        """The refusal is unchanged; the sentence now comes from the schema.

        `mode` was checked here against a hand-written ("sequential",
        "parallel") pair, beside a schema that named the same pair in prose
        and declared no `choices` -- so the parser could not offer them and
        the GUI could not either. It has the tuple now and is refused on the
        same terms as the other nine choice-bearing settings, which is why
        the wording moved. Asserted on the value and the accepted list rather
        than on a phrase, so the next such consolidation does not fail a test
        that is not about it.
        """
        with pytest.raises(ConfigError) as refusal:
            validate_config({"systems": SYS, "execution": {"mode": "turbo"}})
        message = str(refusal.value)
        assert "mode" in message and "'turbo'" in message
        assert "sequential" in message and "parallel" in message


# ===========================================================================
# phase_options (per-phase option extraction)
# ===========================================================================
class TestPhaseOptions:
    def test_extracts_phase_blocks(self):
        opts = phase_options({
            "systems": SYS,
            "setup": {"ph": 7.4},
            "simulation": {"duration_ns": 100.0},
        })
        assert opts["setup"] == {"ph": 7.4}
        assert opts["simulation"] == {"duration_ns": 100.0}

    def test_drops_none_values(self):
        opts = phase_options({"setup": {"ph": 7.4, "water_model": None}})
        assert "water_model" not in opts["setup"]
        assert opts["setup"]["ph"] == 7.4

    def test_ignores_non_phase_keys(self):
        opts = phase_options({"systems": SYS, "output": "./o", "verbose": True})
        assert "systems" not in opts
        assert "output" not in opts

    def test_empty_phase_block_omitted(self):
        opts = phase_options({"setup": {"ph": None}})
        assert "setup" not in opts


# ===========================================================================
# Template generation
# ===========================================================================
class TestTemplate:
    def test_comprehensive_template_is_valid_yaml(self):
        text = generate_template()
        parsed = yaml.safe_load(text)
        # The active (uncommented) part should have a systems list
        assert "systems" in parsed
        assert parsed["systems"][0]["system"] == "protein.pdb"

    def test_comprehensive_template_validates(self):
        """The generated template must pass our own validator."""
        text = generate_template()
        parsed = yaml.safe_load(text)
        validate_config(parsed, require_systems=True)  # should not raise

    def test_minimal_template_is_valid(self):
        text = generate_template(minimal=True)
        parsed = yaml.safe_load(text)
        validate_config(parsed, require_systems=True)
        assert parsed["systems"][0]["system"] == "protein.pdb"

    def test_template_mentions_every_phase(self):
        text = generate_template()
        for phase in ("setup", "simulation", "analysis", "report"):
            assert phase in text

    def test_template_includes_help_comments(self):
        text = generate_template()
        # A few representative help strings should appear as comments
        assert "pH for hydrogen placement" in text
        assert "Production length in ns" in text


# ===========================================================================
# Resolved-config dump
# ===========================================================================
class TestResolvedConfig:
    def test_writes_file(self, tmp_path):
        merged = {
            "system": "x.pdb", "output": str(tmp_path), "verbose": False,
            "include": ["setup", "analysis"], "exclude": None,
            "options": {"setup": {"ph": 7.4}},
        }
        path = write_resolved_config(merged, tmp_path)
        assert path.exists()
        assert path.name == "resolved_config.yml"

    def test_resolved_round_trips(self, tmp_path):
        """The dump must be a valid config (canonical systems:) that re-validates."""
        merged = {
            "system": "x.pdb", "system_id": "trpcage1",
            "output": str(tmp_path), "verbose": True,
            "include": ["setup"], "exclude": None,
            "options": {"setup": {"ph": 6.5}, "simulation": {"duration_ns": 100.0}},
        }
        path = write_resolved_config(merged, tmp_path)
        reparsed = load_config_file(path)
        validate_config(reparsed, require_systems=True)
        assert reparsed["systems"][0]["system"] == "x.pdb"
        assert reparsed["systems"][0]["id"] == "trpcage1"
        assert reparsed["setup"]["ph"] == 6.5

    def test_omits_none_values(self, tmp_path):
        merged = {
            "system": "x.pdb", "output": str(tmp_path), "verbose": False,
            "include": None, "exclude": None, "options": {},
        }
        path = write_resolved_config(merged, tmp_path)
        reparsed = load_config_file(path)
        assert "include" not in reparsed
        assert "exclude" not in reparsed
        assert "verbose" not in reparsed  # False is omitted


# ===========================================================================
# Single-study orchestrator (explicit args; configs go via BatchExplorer)
# ===========================================================================
class TestOrchestrator:
    def test_explicit_system_and_options(self, tmp_path, stub_pdb):
        fmdx = FastMDXplora(
            system=str(stub_pdb),
            output_dir=str(tmp_path / "run"),
            options={"setup": {"ph": 6.8}, "analysis": {"include": ["rmsd", "rg"]}},
            include=["setup", "analysis"],
        )
        assert fmdx.system == str(stub_pdb)
        assert fmdx._config_include == ["setup", "analysis"]
        assert fmdx.options["setup"]["ph"] == 6.8

    def test_missing_system_raises(self):
        with pytest.raises(ValueError, match="requires either a `system`"):
            FastMDXplora()

    def test_writes_resolved_config_after_run(self, tmp_path, stub_pdb):
        fmdx = FastMDXplora(
            system=str(stub_pdb),
            output_dir=str(tmp_path / "run"),
            options={"setup": {"ph": 6.5}},
            include=["setup"],
        )
        fmdx.explore()
        resolved = fmdx.output_dir / "resolved_config.yml"
        assert resolved.exists()
        reparsed = load_config_file(resolved)
        validate_config(reparsed, require_systems=True)
        assert reparsed["setup"]["ph"] == 6.5
        assert reparsed["systems"][0]["system"] == str(stub_pdb)


# ===========================================================================
# End-to-end via the CLI
# ===========================================================================
class TestCLIConfig:
    def test_init_config_writes_template(self, tmp_path):
        out = tmp_path / "template.yml"
        rc = cli_main(["init-config", "-o", str(out)])
        assert rc == 0
        assert out.exists()
        # And it validates (systems is present and required)
        validate_config(load_config_file(out), require_systems=True)

    def test_init_config_minimal(self, tmp_path):
        out = tmp_path / "min.yml"
        rc = cli_main(["init-config", "-o", str(out), "--minimal"])
        assert rc == 0
        assert "duration_ns" in out.read_text(encoding="utf-8")
        assert "systems:" in out.read_text(encoding="utf-8")

    def test_init_config_refuses_overwrite(self, tmp_path):
        out = tmp_path / "exists.yml"
        out.write_text("systems: []\n")
        rc = cli_main(["init-config", "-o", str(out)])
        assert rc == 2  # refuses without --force

    def test_init_config_force_overwrites(self, tmp_path):
        out = tmp_path / "exists.yml"
        out.write_text("old content\n")
        rc = cli_main(["init-config", "-o", str(out), "--force"])
        assert rc == 0
        assert "FastMDXplora configuration" in out.read_text(encoding="utf-8")

    def test_explore_with_config(self, tmp_path, stub_pdb):
        cfg = _write_yaml(tmp_path, f"""
output: {tmp_path / 'run'}
include: [setup, report]
systems:
  - {{id: a, system: {stub_pdb}}}
""")
        rc = cli_main(["explore", "--config", str(cfg)])
        assert rc == 0
        # Single run -> flat output layout, resolved_config at root
        assert (tmp_path / "run" / "resolved_config.yml").exists()

    def test_config_short_flags(self, tmp_path, stub_pdb):
        """-c / -config / --config all work on explore."""
        # Numbered, because `strip("-")` gave `-config` and `--config` the
        # same directory -- so the third run wrote into the second's output.
        for index, flag in enumerate(("-c", "-config", "--config")):
            out = tmp_path / f"out_{index}_{flag.strip('-')}"
            cfg2 = _write_yaml(
                tmp_path,
                f"output: {out}\ninclude: [setup]\nsystems:\n  - {{id: a, system: {stub_pdb}}}\n",
                name=f"c_{index}_{flag.strip('-')}.yml",
            )
            rc = cli_main(["explore", flag, str(cfg2)])
            assert rc == 0, f"{flag} failed"

    def test_flag_overrides_config_value(self, tmp_path, stub_pdb):
        """--setup-ph on the command line beats the config file's ph."""
        cfg = _write_yaml(tmp_path, f"""
output: {tmp_path / 'run'}
include: [setup]
systems:
  - {{id: a, system: {stub_pdb}}}
setup:
  ph: 7.0
""")
        rc = cli_main([
            "explore", "--config", str(cfg),
            "--setup-ph", "6.0",   # override
        ])
        assert rc == 0
        manifest = json.loads(
            (tmp_path / "run" / "setup" / "setup_parameters.json").read_text(encoding="utf-8")
        )
        # Flag wins: ph should be 6.0, not the file's 7.0
        assert manifest["parameters"]["ph"] == 6.0

    def test_cli_config_error_returns_2(self, tmp_path, stub_pdb):
        cfg = _write_yaml(tmp_path, f"systems:\n  - {{id: a, system: {stub_pdb}}}\nsetup:\n  pH: 7.4\n")
        rc = cli_main(["explore", "--config", str(cfg)])
        assert rc == 2  # ConfigError -> exit code 2


# ===========================================================================
# FastMDXplora(config=...) is THE user-facing entry point (one or many)
# ===========================================================================
class TestFastMDXploraConfigEntry:
    def test_single_system_config_flat_output(self, tmp_path, stub_pdb):
        cfg = _write_yaml(tmp_path, f"""
output: {tmp_path / 'one'}
include: [setup]
systems:
  - {{id: trpcage, system: {stub_pdb}}}
""")
        fmdx = FastMDXplora(config=str(cfg))
        results = fmdx.explore()
        assert len(results) == 1
        assert results[0].status == "ok"
        # Flat layout for a single run — no runs/ wrapper
        assert (tmp_path / "one" / "setup").is_dir()
        assert not (tmp_path / "one" / "runs").exists()

    def test_many_system_config_runs_layout(self, tmp_path, stub_pdb):
        cfg = _write_yaml(tmp_path, f"""
output: {tmp_path / 'many'}
include: [setup]
systems:
  - {{id: a, system: {stub_pdb}}}
sweep:
  setup.temperature_K: [300, 310]
""")
        fmdx = FastMDXplora(config=str(cfg))
        results = fmdx.explore()
        assert len(results) == 2
        assert (tmp_path / "many" / "runs").is_dir()
        assert (tmp_path / "many" / "batch_manifest.json").exists()

    def test_config_explore_respects_include_override(self, tmp_path, stub_pdb):
        cfg = _write_yaml(tmp_path, f"""
output: {tmp_path / 'ov'}
systems:
  - {{id: a, system: {stub_pdb}}}
""")
        fmdx = FastMDXplora(config=str(cfg))
        fmdx.explore(include=["setup"])
        # Only setup ran -> setup dir exists, simulation dir does not
        assert (tmp_path / "ov" / "setup").is_dir()
        assert not (tmp_path / "ov" / "simulation").exists()

    def test_system_and_config_conflict_raises(self, tmp_path, stub_pdb):
        cfg = _write_yaml(tmp_path, f"systems:\n  - {{id: a, system: {stub_pdb}}}\n")
        with pytest.raises(ValueError, match="not both"):
            FastMDXplora(system=str(stub_pdb), config=str(cfg))


class TestSettingsAreGrouped:
    """Thirty-six settings for setup and thirty-seven for simulation arrived
    as one flat list each, in the order they happened to be declared: a pH sat
    beside a dispersion correction.

    Finding the one you wanted meant reading all of them, which is a list to
    read rather than a form to fill in. The schema is the one place that knows
    what a setting is, so it is the place that says what it is about -- and
    the browser and `--help` are two views of that one grouping rather than
    two groupings that agree until one is edited.
    """

    def test_every_setting_is_in_exactly_one_group(self) -> None:
        """A setting added and not placed would appear under 'Other' in every
        interface at once. This is what makes that a failure rather than
        something somebody notices later."""
        from fastmdxplora.config.schema import PHASE_SCHEMAS, SETTING_GROUPS

        for phase, schema in PHASE_SCHEMAS.items():
            declared = {field.name for field in schema.fields}
            placed: list[str] = []
            for _title, _why, names in SETTING_GROUPS[phase]:
                placed.extend(names)

            assert not (declared - set(placed)), (
                f"{phase}: not in any group: {sorted(declared - set(placed))}")
            assert not (set(placed) - declared), (
                f"{phase}: grouped but not declared: "
                f"{sorted(set(placed) - declared)}")
            assert len(placed) == len(set(placed)), (
                f"{phase}: named in two groups: "
                f"{sorted({n for n in placed if placed.count(n) > 1})}")

    def test_a_group_says_what_it_is_about(self) -> None:
        """A heading that only repeats the settings under it earns nothing."""
        from fastmdxplora.config.schema import SETTING_GROUPS

        for phase, groups in SETTING_GROUPS.items():
            for title, why, names in groups:
                assert title and not title.endswith(":"), (phase, title)
                assert why and why.endswith("."), (phase, title)
                assert names, (phase, title)

    def test_the_groups_come_back_with_the_fields_in_them(self) -> None:
        """In the declared order, which is the order a decision is met."""
        from fastmdxplora.config.schema import (
            PHASE_SCHEMAS,
            SETTING_GROUPS,
            grouped_fields,
        )

        for phase, schema in PHASE_SCHEMAS.items():
            groups = grouped_fields(phase)
            returned = [f.name for _t, _w, fields in groups for f in fields]
            declared = [name for _t, _w, names in SETTING_GROUPS[phase]
                        for name in names]

            assert returned == declared
            assert len(returned) == len(schema.fields)
            assert not any(title == "Other" for title, _w, _f in groups)

    def test_the_browser_is_given_them(self) -> None:
        from fastmdxplora.gui.schema_payload import schema_payload

        simulation = schema_payload()["phases"]["simulation"]
        titles = [group["title"] for group in simulation["groups"]]
        assert "Enhanced sampling" in titles
        assert "How long it runs" in titles
        drawn = sum(len(group["fields"]) for group in simulation["groups"])
        assert drawn == len(simulation["fields"])

    def test_and_help_reads_as_sections(self) -> None:
        """Thirty-seven flags in declaration order is the same problem in a
        terminal."""
        from fastmdxplora.cli.main import _build_parser

        parser = _build_parser()
        simulate = parser._subparsers._group_actions[0].choices["simulate"]
        titles = [group.title for group in simulate._action_groups]

        assert "simulate: enhanced sampling" in titles
        assert "simulate: how long it runs" in titles
        assert "simulate options" not in titles, (
            "every flag is an option; the word carries nothing")

    def test_the_two_views_read_in_the_same_order(self) -> None:
        """They are one grouping seen twice, so a reader moving between them
        is not learning a second arrangement."""
        from fastmdxplora.cli.main import _build_parser
        from fastmdxplora.gui.schema_payload import schema_payload

        page = [group["title"].lower()
                for group in schema_payload()["phases"]["simulation"]["groups"]]

        parser = _build_parser()
        simulate = parser._subparsers._group_actions[0].choices["simulate"]
        terminal = [group.title.removeprefix("simulate: ")
                    for group in simulate._action_groups
                    if group.title and group.title.startswith("simulate: ")
                    and group.title != "simulate: other"]

        assert terminal == page


class TestTheResolvedConfigNamesEverySettingTheRunUsed:
    """The point of the file, and what it did not do.

    It recorded only what was explicitly set, so a phase that ran entirely
    on its defaults left no block at all. That reproduces a study on the
    version that wrote it and on no other: a default moving in a later
    release silently changes what the file meant, which is the failure the
    file exists to prevent.
    """

    @staticmethod
    def _written(options, tmp_path):
        import yaml

        from fastmdxplora.config import write_resolved_config

        path = write_resolved_config(
            {"system": "1UBQ", "output": str(tmp_path), "options": options},
            tmp_path,
        )
        return yaml.safe_load(path.read_text(encoding="utf-8"))

    def test_every_field_of_every_phase_is_named(self, tmp_path):
        from fastmdxplora.config.schema import PHASE_SCHEMAS

        doc = self._written({"setup": {"ph": 6.5}}, tmp_path)

        for phase, schema in PHASE_SCHEMAS.items():
            assert set(doc[phase]) == schema.field_names(), phase

    def test_a_phase_that_ran_on_defaults_still_appears(self, tmp_path):
        doc = self._written({"setup": {"ph": 6.5}}, tmp_path)

        assert doc["report"]["title"] is None
        assert doc["analysis"]["scope"] == "solute"

    def test_what_was_set_still_wins_over_the_default(self, tmp_path):
        doc = self._written({"setup": {"ph": 6.5}}, tmp_path)

        assert doc["setup"]["ph"] == 6.5

    def test_the_values_are_phase_values_not_declared_defaults(self, tmp_path):
        """`simulation.pressure_bar` declares 1.0 and starts a phase at None,
        so that `pressure_atm` stays detectable. Writing the declared default
        would make bar always present, and bar wins."""
        from fastmdxplora.config.schema import PHASE_SCHEMAS

        doc = self._written({}, tmp_path)

        for phase, schema in PHASE_SCHEMAS.items():
            for field in schema.fields:
                assert doc[phase][field.name] == field.phase_value, (
                    f"{phase}.{field.name}")

    def test_a_pressure_in_atm_survives_the_round_trip(self, tmp_path):
        """The regression writing `Field.default` would have caused: a run
        at 1.2 atm replaying at 1.0 bar, silently."""
        from fastmdxplora.config import load_config_file, phase_options

        doc = self._written({"simulation": {"pressure_atm": 1.2}}, tmp_path)
        assert doc["simulation"]["pressure_bar"] is None

        replayed = phase_options(
            load_config_file(tmp_path / "resolved_config.yml"))["simulation"]
        assert replayed["pressure_atm"] == 1.2
        assert "pressure_bar" not in replayed

    def test_a_duration_is_not_overridden_by_a_step_count(self, tmp_path):
        """Step counts override durations, so a step count written beside
        the duration it was derived from would change the run on replay.
        A field resolved at run time is written null, which the loader
        reads as "use the default"."""
        from fastmdxplora.config import load_config_file, phase_options

        doc = self._written({"simulation": {"nvt_duration_ns": 2}}, tmp_path)
        assert doc["simulation"]["nvt_steps"] is None

        replayed = phase_options(
            load_config_file(tmp_path / "resolved_config.yml"))["simulation"]
        assert replayed["nvt_duration_ns"] == 2
        assert "nvt_steps" not in replayed

    def test_it_round_trips_to_the_same_run(self, tmp_path):
        """The whole claim: what a phase gets from the written file is what
        it got from the config that produced it."""
        from fastmdxplora.config import load_config_file, phase_options

        original = {"setup": {"ph": 6.5, "forcefield": "amber14"},
                    "simulation": {"duration_ns": 10}}
        self._written(original, tmp_path)

        replayed = phase_options(
            load_config_file(tmp_path / "resolved_config.yml"))
        for phase, block in original.items():
            for key, value in block.items():
                assert replayed[phase][key] == value

    def test_the_written_file_still_validates(self, tmp_path):
        from fastmdxplora.config import load_config_file, validate_config

        self._written({"setup": {"ph": 6.5}}, tmp_path)
        validate_config(
            load_config_file(tmp_path / "resolved_config.yml"),
            require_systems=True,
        )

    def test_the_short_form_is_still_available(self, tmp_path):
        import yaml

        from fastmdxplora.config import write_resolved_config

        path = write_resolved_config(
            {"system": "1UBQ", "options": {"setup": {"ph": 6.5}}},
            tmp_path, full=False,
        )
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert doc["setup"] == {"ph": 6.5}
        assert "report" not in doc
