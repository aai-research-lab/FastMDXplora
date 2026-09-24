"""An option a user can set must be settable from every interface.

FastMDXplora offers four routes to the same workflow: the command line, a
configuration file, the Python API, and the browser. An option reachable from
only some of them is a feature that exists for some users and not others, and
`setup.heterogens` shipped that way: the automatic protein-ligand preparation
it controls could not be reached from the GUI at all.

These tests check the routes agree, so the gap fails here rather than
appearing as a missing control someone eventually notices.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fastmdxplora.config.schema import all_schemas

GUI = Path(__file__).resolve().parents[1] / "src" / "fastmdxplora" / "gui"

#: Options deliberately absent from the browser, with the reason.
GUI_EXEMPT = {
    # Paths and file handles: the browser cannot meaningfully offer a
    # filesystem path on a machine it may not be running on.
    "fixed_pdb", "ligand", "trajectory", "topology",
    # Expert force-field escape hatches, set through a config file.
    "force_field", "ligand_forcefield", "ligand_net_charge", "ligand_name",
    # Batch and reporting concerns outside the builder's scope.
    "region_highlights", "comparison", "options", "include", "exclude",
}



def _parse(argv):
    """Parse a command line the way the CLI does, without running anything."""
    from fastmdxplora.cli.main import _build_parser

    return _build_parser().parse_args(argv)



class TestDefaultsAgree:
    """One default, however the option is reached.

    A phase used to keep its own table of these, and checking three options by
    name was all that stood between them. That is how the pH default came to be
    7.4 in the schema and 7.0 in a constant beside it. A phase now reads the
    schema, so agreement is structural rather than asserted.
    """

    @pytest.mark.parametrize("phase,module", [
        ("setup", "fastmdxplora.setup.pipeline"),
        ("simulation", "fastmdxplora.simulation.pipeline"),
    ])
    def test_the_phase_table_is_the_schema_s(self, phase, module) -> None:
        import importlib

        table = importlib.import_module(module).DEFAULTS
        assert table == all_schemas()[phase].defaults()

    def test_no_phase_writes_a_table_of_its_own(self) -> None:
        """Deriving it is only worth anything while nothing restates it."""
        import pathlib
        import re

        src = pathlib.Path(__file__).resolve().parents[1] / "src" / "fastmdxplora"
        offenders = [
            str(path.relative_to(src))
            for path in src.rglob("*.py")
            if re.search(r"^DEFAULTS[^=]*= *\{", path.read_text(encoding="utf-8"), re.M)
        ]
        assert not offenders, (
            "these declare a defaults table instead of reading the schema, "
            f"which is a second place for a default to live: {offenders}"
        )

    def test_a_documented_default_and_a_phase_sentinel_can_differ(self) -> None:
        """pressure_bar is the case the distinction exists for.

        A user is told the default is 1 bar, which is true. The phase must
        start from None so that pressure_atm is recognised as the setting they
        actually gave -- bar wins when both are present, so a phase holding 1.0
        would override an explicit pressure_atm without saying so.
        """
        from fastmdxplora.simulation.runner import DEFAULT_PRESSURE_BAR

        group = all_schemas()["simulation"]
        field = next(f for f in group.fields if f.name == "pressure_bar")
        assert field.default == DEFAULT_PRESSURE_BAR, "what the user is told"
        assert group.defaults()["pressure_bar"] is None, "what the phase starts from"

    def test_a_field_without_a_sentinel_uses_its_default(self) -> None:
        group = all_schemas()["setup"]
        field = next(f for f in group.fields if f.name == "ph")
        assert field.phase_value == field.default == 7.4

    def test_centre_of_mass_motion_is_removed_by_default(self) -> None:
        """OpenMM removes it by default; silently differing would surprise."""
        from fastmdxplora.setup.pipeline import DEFAULTS

        assert DEFAULTS["remove_cm_motion"] is True
        schema = {f.name: f.default for f in all_schemas()["setup"].fields}
        assert schema["remove_cm_motion"] is True



class TestAutoResolvesConsistently:
    """`auto` must mean one stack, not a choice made per structure."""

    def test_auto_is_ligand_capable(self) -> None:
        """Otherwise the default combination refuses on any bound ligand."""
        from fastmdxplora.setup.forcefields import resolve_forcefield

        assert resolve_forcefield("auto").supports_ligand is True

    def test_auto_does_not_depend_on_the_structure(self) -> None:
        """An apo run and its holo partner must share a protein force field.

        Choosing per-system would give 4W51 CHARMM36 and 4W52 AMBER, and the
        comparison between them, which is usually the whole point, would be
        meaningless.
        """
        from fastmdxplora.setup.forcefields import AUTO_FORCEFIELD, resolve_forcefield

        assert resolve_forcefield("auto").name == AUTO_FORCEFIELD
        assert resolve_forcefield(None).name == AUTO_FORCEFIELD

    def test_every_interface_accepts_auto(self) -> None:
        from fastmdxplora.gui.exploration import _FORCEFIELDS

        assert "auto" in _FORCEFIELDS

        parsed = _parse(["explore", "--system", "X", "--setup-forcefield", "auto"])
        assert parsed.setup__forcefield == "auto"

    def test_naming_a_force_field_still_overrides(self) -> None:
        from fastmdxplora.setup.forcefields import resolve_forcefield

        assert resolve_forcefield("charmm36").name == "charmm36"

    def test_the_defaults_work_together(self) -> None:
        """heterogens=auto needs a force field that can take a ligand."""
        from fastmdxplora.setup.forcefields import resolve_forcefield
        from fastmdxplora.setup.pipeline import DEFAULTS

        # This was opt-in while the classifier kept coordinated metals that
        # later stages then rejected, which would have broken structures that
        # prepared cleanly. That is fixed, and a comparison across thirty
        # ordinary structures now shows nothing failing except by a refusal
        # the pipeline states a reason for. What decided it is the other side:
        # under 'drop' a bound ligand is discarded in silence, so rhodopsin
        # prepares as opsin and haemoglobin as globin, and each run completes
        # looking like an answer to the question that was asked.
        assert DEFAULTS["heterogens"] == "auto"
        assert resolve_forcefield(DEFAULTS["forcefield"]).supports_ligand, (
            "--setup-heterogens auto must work without also naming a force "
            "field, or the feature needs two flags instead of one"
        )


class TestArtifactLayout:
    """Ligands prepared from a structure sit with the other setup artifacts."""

    def test_ligands_are_not_written_to_a_nested_setup_directory(self) -> None:
        """output_dir is already the setup directory.

        Appending "setup" again buried them in setup/setup/ligands, where
        nothing else looks and no other artifact lives.
        """
        import inspect

        from fastmdxplora.setup import pipeline

        source = inspect.getsource(pipeline._auto_ligands)
        assert 'Path(setup_dir) / "ligands"' in source
        assert '"setup" / "ligands"' not in source


class TestFilteredStructuresStayConsistent:
    """Removing atoms must remove the records that referred to them.

    CONECT records name atoms by serial number and OpenMM builds bonds from
    them. Left pointing at atoms that are no longer present, they bond
    whatever now holds those serials: a filtered insulin structure gained a
    bond between two zincs 9.6 A apart, and the force field refused it with a
    message about templates that gave no hint of the real cause.
    """

    @staticmethod
    def _source(tmp_path):
        path = tmp_path / "in.pdb"
        path.write_text(
            "ATOM      1  N   ALA A   1       1.000   2.000   3.000  1.00  0.00           N\n"
            "ATOM      2  SG  CYS A   6       4.000   2.000   3.000  1.00  0.00           S\n"
            "ATOM      3  SG  CYS A  11       6.000   2.000   3.000  1.00  0.00           S\n"
            "HETATM   50 ZN    ZN A 112      11.000  12.000  13.000  1.00  0.00          ZN\n"
            "HETATM   60  C1  GOL A 300      21.000  22.000  23.000  1.00  0.00           C\n"
            "CONECT    2    3\n"
            "CONECT   60   50\n"
            "END\n",
            encoding="utf-8",
        )
        return path

    class _Decision:
        """Every copy of one component in the file, as a decision holds
        them: what is retained is those copies, not the name."""

        def __init__(self, resname, source):
            from fastmdxplora.setup.heterogens import (
                group_heterogens, parse_structure,
            )

            _, atoms, linked = parse_structure(source)
            self.resname = resname
            self.instances = tuple(h for h in group_heterogens(atoms, linked)
                                   if h.resname == resname)

    def test_records_naming_removed_atoms_are_dropped(self, tmp_path) -> None:
        from fastmdxplora.setup.pipeline import _retain_in_structure

        source = self._source(tmp_path)
        out = _retain_in_structure(
            source, tmp_path, [self._Decision("ZN", source)]
        )
        text = out.read_text(encoding="utf-8")

        assert "CONECT   60" not in text, "a record naming the removed glycerol"
        assert "CONECT    2    3" in text, "a record naming only retained atoms"

    def test_the_retained_ion_is_unchanged(self, tmp_path) -> None:
        from fastmdxplora.setup.pipeline import _retain_in_structure

        source = self._source(tmp_path)
        out = _retain_in_structure(
            source, tmp_path, [self._Decision("ZN", source)]
        )
        zinc = next(l for l in out.read_text().splitlines()
                    if l.startswith("HETATM"))
        assert zinc[17:20].strip() == "ZN"
        assert "11.000  12.000  13.000" in zinc


class TestIonConnectivityIsNotCovalent:
    """A CONECT naming a retained ion states a bond that is not there.

    The legacy PDB format writes coordination and covalency with the same
    record type; only mmCIF separates them, through struct_conn.conn_type_id.
    OpenMM's ion templates permit no external bonds, so such a record makes the
    ion unmatchable and setup fails reporting a missing template for a residue
    whose atoms and bonds do in fact match. 1ZNI reaches this, with two zincs
    and three chlorides named in CONECT records.
    """

    _Decision = TestFilteredStructuresStayConsistent._Decision

    @staticmethod
    def _write(tmp_path, body):
        path = tmp_path / "in.pdb"
        path.write_text(body, encoding="utf-8")
        return path

    def test_ion_to_ion_records_are_dropped(self, tmp_path) -> None:
        from fastmdxplora.setup.pipeline import _retain_in_structure

        source = self._write(tmp_path, (
            "HETATM 1569 ZN    ZN B  31      10.000  10.000  10.000  1.00  0.00          ZN\n"
            "HETATM 1571 CL    CL B  33      12.200  10.000  10.000  1.00  0.00          CL\n"
            "CONECT 1569 1571\n"
            "CONECT 1571 1569\n"
            "END\n"
        ))
        out = _retain_in_structure(
            source, tmp_path,
            [self._Decision("ZN", source), self._Decision("CL", source)]
        )
        text = out.read_text(encoding="utf-8")

        assert "CONECT" not in text
        # Only the asserted bond goes; the ions themselves are retained and
        # parameterized from the force field's non-bonded ion model.
        assert "ZN    ZN B  31" in text
        assert "CL    CL B  33" in text

    def test_ion_to_protein_records_are_dropped(self, tmp_path) -> None:
        """Coordination to a histidine is the common case, and the same rule."""
        from fastmdxplora.setup.pipeline import _retain_in_structure

        source = self._write(tmp_path, (
            "ATOM    880  NE2 HIS B  10       9.000  10.000  10.000  1.00  0.00           N\n"
            "HETATM 1569 ZN    ZN B  31      10.000  10.000  10.000  1.00  0.00          ZN\n"
            "CONECT  880 1569\n"
            "END\n"
        ))
        out = _retain_in_structure(source, tmp_path,
                                   [self._Decision("ZN", source)])
        text = out.read_text(encoding="utf-8")

        assert "CONECT" not in text
        assert "NE2 HIS B  10" in text
        assert "ZN    ZN B  31" in text


class TestHelpTextDoesNotStateItsOwnDefaults:
    """A default written into help text drifts from the one in force.

    The help said pH 7.0 after it had moved to 7.4, and named charmm36 after
    the force field became auto. Someone reading --help has no way to tell and
    no reason to doubt it, so the value is rendered from the schema instead.
    """

    def test_no_option_table_spells_out_a_default(self) -> None:
        import re
        from fastmdxplora.cli.main import (
            _ANALYSIS_OPTIONS, _REPORT_OPTIONS, _SETUP_OPTIONS,
            _SIMULATION_OPTIONS,
        )

        from fastmdxplora.config.schema import PHASE_SCHEMAS

        # Where the schema default is None or a flag, nothing is rendered and
        # a sentence explaining the behaviour is the only way to convey it --
        # "adaptive, about 2000 frames" is not a value. The rule is narrower:
        # do not write out a default the schema would print for you.
        tables = (
            (_SETUP_OPTIONS, "setup"), (_SIMULATION_OPTIONS, "simulation"),
            (_ANALYSIS_OPTIONS, "analysis"), (_REPORT_OPTIONS, "report"),
        )
        offenders = []
        for table, phase in tables:
            schema = {f.name: f.default for f in PHASE_SCHEMAS[phase].fields}
            for _flag, kwarg, kwargs in table:
                help_text = kwargs.get("help", "")
                default = schema.get(kwarg)
                rendered = (kwarg in schema and default is not None
                            and not isinstance(default, bool))
                if rendered and re.search(r"default", help_text, re.I):
                    offenders.append(f"--{kwarg} ({default!r}): {help_text}")
        assert not offenders, (
            "these write out a default the schema already renders: "
            + "; ".join(offenders)
        )

    def test_the_rendered_help_carries_the_schema_default(self) -> None:
        from fastmdxplora.cli.main import _build_parser
        from fastmdxplora.config.schema import PHASE_SCHEMAS

        parser = _build_parser()
        setup = parser._subparsers._group_actions[0].choices["setup"]
        rendered = {a.dest: (a.help or "") for a in setup._actions}
        schema = {f.name: f.default for f in PHASE_SCHEMAS["setup"].fields}

        for option in ("ph", "heterogens", "forcefield", "protonation_margin"):
            assert f"Default: {schema[option]}." in rendered[option], (
                f"--{option} help does not carry its schema default "
                f"{schema[option]!r}: {rendered[option]!r}"
            )

    def test_every_cli_phase_name_maps_to_a_schema_group(self) -> None:
        """The CLI names a phase for the verb, the schema for the noun."""
        from fastmdxplora.cli.main import _PHASE_SPEC, _SCHEMA_KEY
        from fastmdxplora.config.schema import PHASE_SCHEMAS

        for phase in _PHASE_SPEC:
            assert _SCHEMA_KEY.get(phase) in PHASE_SCHEMAS, (
                f"CLI phase {phase!r} has no schema group, so its defaults "
                "would silently stop being rendered"
            )


class TestSettableThingsAreReachable:
    """A setting the software tells you to change must be changeable.

    protonation_margin was added to the schema and to the phase, and a refusal
    message pointed at it, but no flag existed: the only way to reach it was a
    config file.
    """

    def test_protonation_margin_has_a_flag(self) -> None:
        from fastmdxplora.cli.main import _SETUP_OPTIONS

        assert any(kwarg == "protonation_margin"
                   for _flag, kwarg, _kwargs in _SETUP_OPTIONS)

    def test_every_setup_flag_names_a_real_schema_field(self) -> None:
        from fastmdxplora.cli.main import _SETUP_OPTIONS
        from fastmdxplora.config.schema import PHASE_SCHEMAS

        known = set(PHASE_SCHEMAS["setup"].field_names())
        unknown = {kwarg for _f, kwarg, _k in _SETUP_OPTIONS} - known
        assert not unknown, f"flags with no schema field: {unknown}"


class TestTheBrowserHasNoDefaultsOfItsOwn:
    """A control that falls back to a stale default is a silent disagreement.

    The builder offered "Remove them (default)" and fell back to drop after the
    default had become auto, and to pH 7 after it had become 7.4. Nothing fails
    when that drifts: the browser simply sends a different study from the one
    the same settings would produce anywhere else.

    Three tests used to hold those particular fallbacks to the schema. They are
    replaced by one that says why they are no longer needed: the page has no
    fallbacks, because it has no settings of its own. Every control is drawn
    from what the schema reports, so there is nothing left to drift.
    """

    @staticmethod
    def _browser_scripts():
        """The scripts that build the form.

        Not every file under static/: the viewer and the results dashboard
        contain the words "first", "last" and "title" for reasons that have
        nothing to do with settings, and a check that cannot tell those apart
        would be noise rather than a guard.
        """
        return {
            name: (GUI / "static" / name).read_text(encoding="utf-8")
            for name in ("run-builder.js", "file-picker.js")
        }

    def test_no_script_writes_out_a_setting_the_schema_declares(self) -> None:
        supplied = {"trajectory", "topology", "system", "output", "include",
                    "exclude", "scope"}
        named: list[str] = []
        for filename, script in self._browser_scripts().items():
            for phase, group in all_schemas().items():
                for field in group.fields:
                    if field.name in supplied:
                        continue
                    if f'"{field.name}"' in script or f"'{field.name}'" in script:
                        named.append(f"{filename}: {phase}.{field.name}")
        assert not named, f"settings written into the browser: {named}"

    def test_no_script_writes_out_a_list_of_accepted_values(self) -> None:
        """The schema declares those too, and a control offering something the
        command line rejects looks like the tool disagreeing with itself."""
        offenders: list[str] = []
        for filename, script in self._browser_scripts().items():
            for phase, group in all_schemas().items():
                for field in group.fields:
                    if not field.choices or len(field.choices) < 2:
                        continue
                    if all(f'"{choice}"' in script for choice in field.choices):
                        offenders.append(f"{filename}: {phase}.{field.name}")
        assert not offenders, f"choice lists written into the browser: {offenders}"

    def test_the_page_asks_the_schema_instead(self) -> None:
        scripts = self._browser_scripts()
        assert any("/api/schema" in script for script in scripts.values()), (
            "nothing asks the schema, so the controls come from somewhere else"
        )

class TestDefaultConstantsAgreeWithTheSchema:
    """A module constant naming a default is a second place for it to live.

    DEFAULT_PH sat in the setup package, read by nothing, and stayed at 7.0
    when the pH default became 7.4. Nothing failed, because nothing used it --
    but the next person to reach for it would have got the old value with no
    sign that it was stale. These constants are read by the code that applies
    them, so where one restates a schema field, the two must agree.
    """

    #: Constant -> the schema field it restates. Written out because the names
    #: do not correspond mechanically, and a constant with no field here is one
    #: nothing in the schema describes, which is fine.
    RESTATED = [
        ("setup", "solvent_padding_nm", "fastmdxplora.setup.prepare", "DEFAULT_PADDING_NM"),
        ("setup", "ion_concentration_M", "fastmdxplora.setup.prepare", "DEFAULT_IONIC_STRENGTH_M"),
        ("setup", "forcefield", "fastmdxplora.setup.forcefields", "DEFAULT_FORCEFIELD"),
        ("simulation", "timestep_fs", "fastmdxplora.simulation.runner", "DEFAULT_TIMESTEP_FS"),
        ("simulation", "temperature_K", "fastmdxplora.simulation.runner", "DEFAULT_TEMPERATURE_K"),
        ("simulation", "friction_per_ps", "fastmdxplora.simulation.runner", "DEFAULT_FRICTION_PER_PS"),
        ("simulation", "pressure_bar", "fastmdxplora.simulation.runner", "DEFAULT_PRESSURE_BAR"),
        ("simulation", "barostat_frequency", "fastmdxplora.simulation.runner", "DEFAULT_BAROSTAT_FREQUENCY"),
        ("simulation", "integrator", "fastmdxplora.simulation.runner", "DEFAULT_INTEGRATOR"),
        ("simulation", "integrator_error_tolerance", "fastmdxplora.simulation.runner",
         "DEFAULT_INTEGRATOR_ERROR_TOLERANCE"),
        ("simulation", "minimize_max_iterations", "fastmdxplora.simulation.runner",
         "DEFAULT_MINIMIZE_MAX_ITERATIONS"),
        ("simulation", "minimize_tolerance_kjmol_per_nm", "fastmdxplora.simulation.runner",
         "DEFAULT_MINIMIZE_TOLERANCE_KJMOL_PER_NM"),
        ("simulation", "checkpoint_interval_steps", "fastmdxplora.simulation.runner",
         "DEFAULT_CHECKPOINT_INTERVAL_STEPS"),
        ("simulation", "state_interval_steps", "fastmdxplora.simulation.runner",
         "DEFAULT_STATE_INTERVAL_STEPS"),
    ]

    @pytest.mark.parametrize(
        "phase,field,module,constant", RESTATED,
        ids=[f"{c}" for _p, _f, _m, c in RESTATED],
    )
    def test_the_constant_matches_the_field(self, phase, field, module, constant) -> None:
        import importlib

        schema = {f.name: f.default for f in all_schemas()[phase].fields}
        value = getattr(importlib.import_module(module), constant)
        assert value == schema[field], (
            f"{constant} is {value!r} but the schema says {phase}.{field} "
            f"is {schema[field]!r}"
        )

    def test_no_default_constant_is_unread(self) -> None:
        """An unread constant cannot drift loudly, so it must not exist."""
        import pathlib
        import re

        # Only the package is scanned. Counting the tests too would let this
        # test's own prose name a constant and thereby report it as used, which
        # is exactly what happened the first time it was written.
        src = pathlib.Path(__file__).resolve().parents[1] / "src" / "fastmdxplora"
        declared: set[str] = set()
        mentions: dict[str, int] = {}
        for path in src.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            declared.update(re.findall(r"^(DEFAULT_[A-Z0-9_]+) *=", text, re.M))
            # Comments are stripped before counting. A note explaining why a
            # constant was removed names it, and naming it was enough to make
            # this test report it as used -- which it did, twice, before the
            # comments were taken out.
            code = re.sub(r"#[^\n]*", "", text)
            for name in re.findall(r"\bDEFAULT_[A-Z0-9_]+\b", code):
                mentions[name] = mentions.get(name, 0) + 1

        # One mention is the declaration and nothing else.
        orphans = sorted(n for n in declared if mentions.get(n, 0) <= 1)
        assert not orphans, (
            "these state a default nothing reads, so nothing notices when they "
            f"fall out of date: {orphans}"
        )


class TestAcceptedValuesAreDeclaredOnce:
    """One list of accepted values, wherever an option is offered.

    The force-field names, the analysis scopes, the integrators, and the
    precisions each existed in two or more places: the CLI's option table, the
    browser's controls, and beside the code that validates them. They agreed,
    but only because nobody had touched them. A control offering something the
    CLI rejects is worse than either list being wrong -- it looks like the tool
    disagreeing with itself.
    """

    def test_every_default_is_one_of_its_own_choices(self) -> None:
        """The invariant that makes a choices list worth having."""
        broken = []
        for phase, group in all_schemas().items():
            for f in group.fields:
                if f.choices and f.default is not None and f.default not in f.choices:
                    broken.append(f"{phase}.{f.name}: {f.default!r} not in {f.choices}")
        assert not broken, broken

    def test_no_cli_table_restates_a_schema_list(self) -> None:
        from fastmdxplora.cli.main import (
            _ANALYSIS_OPTIONS, _REPORT_OPTIONS, _SETUP_OPTIONS,
            _SIMULATION_OPTIONS,
        )

        tables = (
            (_SETUP_OPTIONS, "setup"), (_SIMULATION_OPTIONS, "simulation"),
            (_ANALYSIS_OPTIONS, "analysis"), (_REPORT_OPTIONS, "report"),
        )
        offenders = []
        for table, phase in tables:
            described = {f.name for f in all_schemas()[phase].fields if f.choices}
            for _flag, kwarg, kwargs in table:
                if kwarg in described and "choices" in kwargs:
                    offenders.append(f"--{kwarg}")
        assert not offenders, (
            "these carry their own list of accepted values while the schema "
            f"also declares one: {offenders}"
        )

    def test_the_parser_offers_what_the_schema_declares(self) -> None:
        from fastmdxplora.cli.main import _build_parser

        parser = _build_parser()
        subcommands = parser._subparsers._group_actions[0].choices
        for phase, sub in (("setup", "setup"), ("simulation", "simulate"),
                           ("analysis", "analyze")):
            declared = {f.name: list(f.choices)
                        for f in all_schemas()[phase].fields if f.choices}
            offered = {a.dest: list(a.choices)
                       for a in subcommands[sub]._actions if a.choices}
            for name, values in declared.items():
                assert offered.get(name) == values, (
                    f"--{name} offers {offered.get(name)}, schema says {values}"
                )

    def test_the_browser_reads_the_same_lists(self) -> None:
        from fastmdxplora.gui.exploration import _INTEGRATORS, _PRECISIONS

        declared = {f.name: f.choices for f in all_schemas()["simulation"].fields}
        assert _INTEGRATORS == declared["integrator"]
        assert _PRECISIONS == declared["precision"]

    def test_the_force_field_names_match_the_registry(self) -> None:
        """The schema restates these because it cannot import the registry.

        Reaching setup.forcefields runs the setup package's __init__, which
        imports the phase, which imports the schema. So the names are written
        twice and held level here.
        """
        from fastmdxplora.setup.forcefields import available_forcefields

        declared = next(
            f.choices for f in all_schemas()["setup"].fields if f.name == "forcefield"
        )
        assert sorted(declared) == sorted(("auto",) + available_forcefields())


class TestTheCommandLineOffersWhatTheSchemaDeclares:
    """The flag table was maintained by hand beside a schema declaring the
    same settings, and it drifted: twenty-four settings had no flag of their
    own, including two added the same day the drift was found.

    A setting reachable from a config file and not from the command line is a
    setting somebody will conclude does not exist. This is the fourth
    hand-maintained list found beside a generated one -- after the GUI's
    fields, the analysis registry and the orchestrator's taxonomy -- and it
    failed the same way each time.
    """

    def test_every_setting_can_be_given_on_the_command_line(self) -> None:
        from fastmdxplora.cli.main import _NO_FLAG_OF_ITS_OWN, _PHASE_SPEC
        from fastmdxplora.config.schema import PHASE_SCHEMAS

        schema_key = {"setup": "setup", "simulate": "simulation",
                      "analyze": "analysis", "report": "report"}
        missing: dict[str, list[str]] = {}
        for phase, (table, _prefix) in _PHASE_SPEC.items():
            exposed = {dest for _flag, dest, _kw in table}
            declared = {f.name for f in PHASE_SCHEMAS[schema_key[phase]].fields}
            unreachable = sorted(
                declared - exposed - set(_NO_FLAG_OF_ITS_OWN))
            if unreachable:
                missing[phase] = unreachable
        assert not missing, (
            f"these settings have no flag and no recorded reason: {missing}"
        )

    def test_every_exclusion_says_why(self) -> None:
        """So the list does not become a place to hide a setting nobody
        wired up."""
        from fastmdxplora.cli.main import _NO_FLAG_OF_ITS_OWN

        assert _NO_FLAG_OF_ITS_OWN
        for name, reason in _NO_FLAG_OF_ITS_OWN.items():
            assert reason and len(reason) > 15, name

    def test_a_setting_added_to_the_schema_gets_a_flag(self) -> None:
        """The two that prompted this were `report.pdf` and
        `simulation.state_interval_steps`."""
        from fastmdxplora.cli.main import _PHASE_SPEC

        for phase, name in (("report", "pdf"),
                            ("simulate", "state_interval_steps"),
                            ("setup", "nonbonded_cutoff_nm"),
                            ("simulate", "barostat_frequency")):
            table, _prefix = _PHASE_SPEC[phase]
            assert name in {dest for _f, dest, _k in table}, f"{phase}.{name}"

    def test_a_yes_or_no_setting_can_be_turned_both_ways(self) -> None:
        """One defaulting to true needs a way off and one defaulting to false
        needs a way on, and which it is can change without the flag having
        to."""
        from fastmdxplora.cli.main import _PHASE_SPEC

        table, _prefix = _PHASE_SPEC["report"]
        flags = {flag for flag, dest, _kw in table if dest == "pdf"}
        assert "pdf" in flags and "no-pdf" in flags

    def test_the_flags_reach_the_phase_that_uses_them(self) -> None:
        from fastmdxplora.cli.main import (
            _PHASE_SPEC,
            _build_parser,
            _harvest_phase_options,
        )

        args = _build_parser().parse_args([
            "explore", "--system", "x.pdb",
            "--simulate-state-interval-steps", "20",
            "--setup-nonbonded-cutoff-nm", "1.2",
            "--report-no-pdf",
        ])
        for phase, name, expected in (("simulate", "state_interval_steps", 20),
                                      ("setup", "nonbonded_cutoff_nm", 1.2),
                                      ("report", "pdf", False)):
            table, prefix = _PHASE_SPEC[phase]
            harvested = _harvest_phase_options(args, table, dest_prefix=prefix)
            assert harvested.get(name) == expected, f"{phase}.{name}"

    def test_the_hand_written_wording_is_kept(self) -> None:
        """Its help text is better than anything generated, and several of its
        flags are deliberate aliases, so it stays and the generation fills
        what it does not cover."""
        from fastmdxplora.cli.main import _SETUP_OPTIONS, _PHASE_SPEC

        table, _prefix = _PHASE_SPEC["setup"]
        written = {flag for flag, _d, _k in _SETUP_OPTIONS}
        assert written <= {flag for flag, _d, _k in table}


class TestTheCommandLineTakesItsWordsFromTheSchema:
    """The table listing flags carried its own help text, and fifty-three of
    its sixty entries had fallen behind the declaration.

    ``--setup-ph`` said "pH for hydrogen placement" where the schema explains
    that it sets protonation states. Keeping the wording was the argument for
    the table, and the wording was the thing that had rotted -- which is what
    a second copy of a sentence does.
    """

    def test_no_flag_describes_itself_differently(self) -> None:
        from fastmdxplora.cli.main import _PHASE_SPEC
        from fastmdxplora.config.schema import PHASE_SCHEMAS

        schema_key = {"setup": "setup", "simulate": "simulation",
                      "analyze": "analysis", "report": "report"}
        stale = []
        for phase, (table, _prefix) in _PHASE_SPEC.items():
            group = PHASE_SCHEMAS[schema_key[phase]]
            for flag, dest, options in table:
                field = group.get(dest)
                if field is None or not field.help:
                    continue
                if options.get("action") == "store_false":
                    # Phrased as the negation, from the same sentence.
                    assert field.help[1:] in options.get("help", ""), flag
                    continue
                if (options.get("help") or "").strip() != field.help.strip():
                    stale.append(f"{phase}.{dest}")
        assert not stale, f"these describe themselves twice, differently: {stale}"

    def test_the_accepted_values_come_from_the_schema(self) -> None:
        """So a flag cannot offer a value the software refuses."""
        from fastmdxplora.cli.main import _PHASE_SPEC
        from fastmdxplora.config.schema import PHASE_SCHEMAS

        table, _prefix = _PHASE_SPEC["setup"]
        heterogens = next(o for f, d, o in table if d == "heterogens")
        declared = PHASE_SCHEMAS["setup"].get("heterogens").choices
        assert declared
        assert set(heterogens.get("choices") or declared) == set(declared)

    def test_what_the_table_still_holds_is_only_what_it_must(self) -> None:
        """A flag deliberately named something other than its setting, and a
        convenience flag with no setting of its own. Everything else is
        derived, so a new setting needs no decision here."""
        from fastmdxplora.cli.main import (
            _ANALYSIS_OPTIONS,
            _REPORT_OPTIONS,
            _SETUP_OPTIONS,
            _SIMULATION_OPTIONS,
        )
        from fastmdxplora.config.schema import PHASE_SCHEMAS

        irreducible = 0
        for phase, table in (("setup", _SETUP_OPTIONS),
                             ("simulation", _SIMULATION_OPTIONS),
                             ("analysis", _ANALYSIS_OPTIONS),
                             ("report", _REPORT_OPTIONS)):
            group = PHASE_SCHEMAS[phase]
            for flag, dest, _options in table:
                if group.get(dest) is None or flag != dest.replace("_", "-"):
                    irreducible += 1
        assert irreducible >= 16, (
            "the aliases and convenience flags should still be declared"
        )
