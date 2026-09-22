"""One declaration, three interfaces, checked field by field.

The README says the form, the flags and the API are generated from one
declaration and none is a subset of another. Three findings said otherwise
and all three were one registry lookup deep:

  - the config loader read a field's *name* and its *type* and never its
    `choices`, so nine settings the parser rejects the file accepted, and
    `box_shape: dodecahedran` died inside OpenMM's `addSolvent` several
    minutes later;
  - `execution` was in neither `PHASE_SCHEMAS` nor `all_schemas()`, so
    `mode`, `workers`, `devices` and `continue_on_error` had no flag and no
    control and were reachable only by writing a file;
  - the umbrella expansion lived in `load_config_file`, under a comment
    saying the point was that every route gets it, so `config_data=` -- the
    CLI's own path -- got one unexpanded run.

The existing parity suite could not see any of them. It iterates
`all_schemas()`, which excluded `execution`; and its accepted-values test
names setup, simulation and analysis by hand, so a fourth block with
`choices` would be walked past. These tests take the registry as given and
name nothing.
"""

from __future__ import annotations

import pytest

from fastmdxplora.config.loader import ConfigError, validate_config
from fastmdxplora.config.schema import all_schemas

#: Blocks that appear in a config file as a mapping under their own key.
#: `(top-level)` is the scalars, which sit at the root instead.
BLOCKS = [name for name in all_schemas() if name != "(top-level)"]

#: Every (block, field) the registry declares with a list of accepted values.
CHOICE_FIELDS = [
    (block, field)
    for block, group in all_schemas().items()
    for field in group.fields
    if field.choices
]


def _verb_for(block: str) -> str:
    """The CLI verb a schema block's flags are prefixed with.

    The command line says `simulate` and `analyze`; the schema says
    `simulation` and `analysis`. `_SCHEMA_KEY` maps one to the other and this
    reads it backwards, rather than restating the pairs -- a second copy of
    that mapping is how the flag table drifted from the schema in the first
    place.
    """
    import importlib

    main = importlib.import_module("fastmdxplora.cli.main")
    for verb, schema_name in main._SCHEMA_KEY.items():
        if schema_name == block:
            return verb
    return block


def _config(block: str, key: str, value):
    body = {"systems": [{"system": "1UBQ"}]}
    if block == "(top-level)":
        body[key] = value
    else:
        body[block] = {key: value}
    return body


class TestTheLoaderAcceptsExactlyWhatTheSchemaDeclares:
    """The file is one of the three interfaces, and it was the lax one."""

    @pytest.mark.parametrize(
        "block,field",
        CHOICE_FIELDS,
        ids=[f"{b}.{f.name}" for b, f in CHOICE_FIELDS],
    )
    def test_every_declared_value_is_accepted(self, block, field) -> None:
        for value in field.choices:
            given = [value] if field.type is list else value
            validate_config(_config(block, field.name, given))

    @pytest.mark.parametrize(
        "block,field",
        CHOICE_FIELDS,
        ids=[f"{b}.{f.name}" for b, f in CHOICE_FIELDS],
    )
    def test_a_value_it_does_not_declare_is_refused(self, block, field) -> None:
        undeclared = "not-a-declared-value"
        given = [undeclared] if field.type is list else undeclared
        with pytest.raises(ConfigError) as refusal:
            validate_config(_config(block, field.name, given))

        # The refusal has to name the setting and say what is accepted --
        # the old failure was a message about neither.
        message = str(refusal.value)
        assert field.name in message
        for value in field.choices:
            assert str(value) in message

    def test_a_typo_gets_a_suggestion(self) -> None:
        """The nearest declared value, as an unknown *key* already gets."""
        with pytest.raises(ConfigError) as refusal:
            validate_config(_config("setup", "box_shape", "dodecahedran"))
        assert "did you mean 'dodecahedron'" in str(refusal.value)

    def test_one_bad_element_in_a_list_is_named(self) -> None:
        with pytest.raises(ConfigError) as refusal:
            validate_config(_config(
                "analysis", "include", ["rmsd", "rgyr", "sasa"]))
        assert "'rgyr'" in str(refusal.value)


class TestThePledgeThatNoInterfaceIsASubset:

    @pytest.mark.parametrize("block", BLOCKS)
    def test_every_field_has_a_flag_or_a_stated_reason(self, block) -> None:
        """A field reaches the command line, or `_NO_FLAG_OF_ITS_OWN` says
        why not. A mapping or a list of blocks cannot be spelled on a command
        line and that is a real answer; being forgotten is not, and the two
        looked identical from outside."""
        import importlib

        main = importlib.import_module("fastmdxplora.cli.main")
        parser = main._build_parser()
        explore = parser._subparsers._group_actions[0].choices["explore"]
        dests = {a.dest for a in explore._actions}
        verb = _verb_for(block)

        missing = [
            f.name for f in all_schemas()[block].fields
            if f"{verb}__{f.name}" not in dests
            and f.name not in dests
            and f.name not in main._NO_FLAG_OF_ITS_OWN
        ]
        assert not missing, (
            f"{block}: no flag reaches {missing}, and nothing says why. "
            f"Writing a config file is the only way to set them, which is "
            f"the subset the README says does not exist. Either generate a "
            f"flag or add the field to _NO_FLAG_OF_ITS_OWN with a reason."
        )

    @pytest.mark.parametrize("block", BLOCKS)
    def test_every_field_reaches_the_form(self, block) -> None:
        from fastmdxplora.gui.schema_payload import schema_payload

        payload = schema_payload()
        offered: set[str] = set()
        for entry in payload["phases"].values():
            for group in entry["groups"]:
                offered.update(f["name"] for f in group["fields"])
        for key in ("run_options", "execution_options"):
            offered.update(f["name"] for f in payload.get(key, ()))

        missing = [f.name for f in all_schemas()[block].fields
                   if f.name not in offered]
        assert not missing, (
            f"{block}: the browser cannot set {missing}"
        )

    @pytest.mark.parametrize(
        "block,field",
        CHOICE_FIELDS,
        ids=[f"{b}.{f.name}" for b, f in CHOICE_FIELDS],
    )
    def test_the_parser_offers_the_same_values(self, block, field) -> None:
        """Named nothing, so a block acquiring `choices` is covered the day
        it does. The existing version lists setup, simulation and analysis by
        hand and would have walked past `execution.mode`."""
        from fastmdxplora.cli.main import _build_parser

        parser = _build_parser()
        explore = parser._subparsers._group_actions[0].choices["explore"]
        offered = {a.dest: list(a.choices)
                   for a in explore._actions if a.choices}
        dest = (field.name if block == "(top-level)"
                else f"{_verb_for(block)}__{field.name}")

        if dest not in offered:
            pytest.skip(f"{dest} takes no value on the command line")
        assert offered[dest] == list(field.choices)

    @pytest.mark.parametrize("block", BLOCKS)
    def test_the_form_shows_the_same_values(self, block) -> None:
        from fastmdxplora.gui.schema_payload import schema_payload

        payload = schema_payload()
        seen: dict[str, list] = {}
        for entry in payload["phases"].values():
            for group in entry["groups"]:
                for f in group["fields"]:
                    if f.get("choices"):
                        seen[f["name"]] = list(f["choices"])
        for key in ("run_options", "execution_options"):
            for f in payload.get(key, ()):
                if f.get("choices"):
                    seen[f["name"]] = list(f["choices"])

        for field in all_schemas()[block].fields:
            if field.choices and field.name in seen:
                assert seen[field.name] == list(field.choices), (
                    f"{block}.{field.name}: the form offers {seen[field.name]}, "
                    f"the schema declares {list(field.choices)}"
                )


class TestTheSchedulingBlockIsReachable:
    """`execution` specifically, because it was reachable from one interface
    and the parity suite was iterating a registry that left it out."""

    def test_it_is_in_the_registry(self) -> None:
        assert "execution" in all_schemas()

    def test_it_is_not_a_phase(self) -> None:
        """It says how the runs a plan produces are scheduled; it is not one
        of them, and putting it in `PHASE_SCHEMAS` to make it reachable would
        have made it includable and excludable."""
        from fastmdxplora.config.schema import PHASE_SCHEMAS

        assert "execution" not in PHASE_SCHEMAS

    def test_it_has_a_group_so_the_form_can_lay_it_out(self) -> None:
        from fastmdxplora.config.schema import EXECUTION, grouped_fields

        grouped = grouped_fields("execution")
        assert grouped
        placed = {f.name for _t, _w, fields in grouped for f in fields}
        assert placed == {f.name for f in EXECUTION.fields}

    def test_the_flags_reach_the_config(self) -> None:
        import importlib

        main = importlib.import_module("fastmdxplora.cli.main")
        args = main._build_parser().parse_args([
            "explore", "--system", "1UBQ",
            "--execution-mode", "parallel",
            "--execution-workers", "3",
            "--execution-devices", "0", "1",
            "--execution-no-continue-on-error",
        ])
        harvested = main._harvest_phase_options(
            args, main._EXECUTION_OPTIONS, dest_prefix="execution")
        # Devices as numbers: the flag reads each item as the config file
        # would. As text before, which is what this test had pinned.
        assert harvested == {
            "mode": "parallel", "workers": 3,
            "devices": [0, 1], "continue_on_error": False,
        }

    def test_the_form_reaches_the_config(self) -> None:
        from fastmdxplora.gui.config_builder import build_config

        built = build_config({
            "system": "1UBQ",
            "__execution__": {"mode": "parallel", "workers": 4},
        })
        assert built.get("execution") == {"mode": "parallel", "workers": 4}


class TestOneRouteInIsTheSameAsAnother:
    """The umbrella expansion, which every route was said to get."""

    STUDY = {
        "systems": [{"system": "1L2Y", "name": "w"}],
        "simulation": {"umbrella": {
            "collective_variable": "distance",
            "selection_a": "resid 1", "selection_b": "resid 10",
            "from": 0.3, "to": 0.9, "n_windows": 5,
            "force_constant": 1000,
        }},
        "execution": {"continue_on_error": False},
    }

    def _from_file(self, tmp_path):
        import yaml

        from fastmdxplora.config import load_config_file

        path = tmp_path / "study.yml"
        path.write_text(yaml.safe_dump(self.STUDY), encoding="utf-8")
        data = load_config_file(path)
        validate_config(data, require_systems=True)
        return data

    def _from_a_dict(self):
        import copy

        data = copy.deepcopy(self.STUDY)
        validate_config(data, require_systems=True)
        return data

    def test_the_two_routes_agree(self, tmp_path) -> None:
        assert self._from_file(tmp_path) == self._from_a_dict()

    def test_both_expand_the_windows(self, tmp_path) -> None:
        for name, data in (("file", self._from_file(tmp_path)),
                           ("dict", self._from_a_dict())):
            assert len(data["systems"]) == 5, (
                f"the {name} route produced {len(data['systems'])} runs from a "
                f"five-window umbrella block"
            )

    def test_the_explorer_agrees_whichever_way_it_was_given(
        self, tmp_path
    ) -> None:
        """Where the asymmetry actually bit: `config_data=` is the CLI's own
        path, and it produced one run with the block unexpanded and
        `continue_on_error` flipped from False to True."""
        import copy

        import yaml

        from fastmdxplora.batch.explorer import BatchExplorer

        path = tmp_path / "study.yml"
        path.write_text(yaml.safe_dump(self.STUDY), encoding="utf-8")

        from_file = BatchExplorer(
            config=path, output_dir=tmp_path / "a")
        from_dict = BatchExplorer(
            config_data=copy.deepcopy(self.STUDY), output_dir=tmp_path / "b")

        assert len(from_dict.run_specs) == len(from_file.run_specs) == 5
        assert from_dict._is_umbrella is from_file._is_umbrella is True
        assert from_dict.continue_on_error is from_file.continue_on_error is False
