"""A study described once is runnable in three languages, verbatim.

The GUI hands back a config; the same study also has a command-line language
and a Python language, and translating into either by hand is where a flag
gets the wrong prefix and the run that follows is not the study the form
described. The translators are derived rather than written -- the command
consults the live parser -- and the proof is a round trip: the rendered
command, parsed by the real parser, reproduces the config it came from.
"""

from __future__ import annotations

import shlex

import pytest

from fastmdxplora.config.languages import (
    UntranslatableSetting,
    cli_command,
    python_script,
)


def _parse(command: str):
    from fastmdxplora.cli.main import _build_parser

    parts = shlex.split(command)
    assert parts[:2] == ["fastmdx", "explore"]
    explore = _build_parser()._subparsers._group_actions[0].choices["explore"]
    return explore.parse_args(parts[2:])


class TestTheRoundTrip:
    """Rendered, then parsed by the parser it was rendered from."""

    def test_every_block_survives_the_trip(self) -> None:
        config = {
            "system": "181L.pdb",
            "output": "runs/t4",
            "include": ["setup", "simulation"],
            "setup": {"ph": 6.5, "ligand": "BNZ_ideal.sdf",
                      "ligand_pose": "file", "solvent_padding_nm": 1.2},
            "simulation": {"duration_ns": 20.0, "platform": "CUDA"},
            "analysis": {"include": ["pl_contacts", "rmsd"]},
        }
        got = _parse(cli_command(config))
        assert got.system == "181L.pdb"
        assert got.output_dir == "runs/t4"
        assert got.include == ["setup", "simulation"]
        assert got.setup__ph == 6.5
        assert got.setup__ligand_pose == "file"
        assert got.simulate__duration_ns == 20.0
        assert got.analyze__include == ["pl_contacts", "rmsd"]

    def test_the_hand_named_exception_is_honoured(self) -> None:
        """`analysis.include` is `--analyze-analyses` on the command line,
        because `--analyze-include` would collide with the phase selector.
        The renderer asks the parser instead of rederiving the rule, so the
        exception costs nothing here."""
        command = cli_command({"analysis": {"include": ["rmsd"]}})
        assert "--analyze-analyses rmsd" in command
        assert "--analyze-include" not in command

    def test_a_zero_argument_flag_renders_bare(self) -> None:
        command = cli_command({"setup": {"keep_heterogens": True}})
        assert command.endswith("--setup-keep-heterogens")
        assert _parse(command).setup__keep_heterogens is True

    def test_a_path_with_a_space_is_quoted(self) -> None:
        command = cli_command({"system": "my folder/protein.pdb"})
        assert "'my folder/protein.pdb'" in command
        assert _parse(command).system == "my folder/protein.pdb"

    def test_the_batch_config_shape_renders_its_one_system(self) -> None:
        """The GUI writes `systems: [entry]` so one protein and forty have
        the same file shape; the command line takes the one."""
        got = _parse(cli_command({"systems": [{"system": "1UBQ"}]}))
        assert got.system == "1UBQ"

    def test_a_new_schema_field_is_spelled_the_day_it_exists(self) -> None:
        """Everything the schema declares must render and round-trip, so a
        field added tomorrow cannot silently lack a command spelling."""
        from fastmdxplora.cli.main import _build_parser
        from fastmdxplora.config.schema import SETUP

        explore = _build_parser()._subparsers._group_actions[0].choices[
            "explore"]
        actions = {a.dest: a for a in explore._actions}

        for field in SETUP.fields:
            default = field.default
            action = actions.get(f"setup__{field.name}")
            choices = getattr(action, "choices", None) if action else None
            if choices:
                # A constrained field probes with a legal value that is not
                # the default, which is what a person would actually set.
                value = next((c for c in choices if c != default), None)
            else:
                value = {
                    bool: True, int: 3, float: 1.5, str: "x",
                }.get(field.type if not isinstance(field.type, tuple)
                      else field.type[0])
            if value is None or field.name in ("fixed_pdb",):
                continue
            if isinstance(default, bool):
                value = not default
            config = {"setup": {field.name: value}}
            try:
                command = cli_command(config)
            except UntranslatableSetting:
                continue  # a declared refusal is honest; silence is not
            parsed = _parse(command)
            assert getattr(parsed, f"setup__{field.name}") is not None


class TestAFullConfigTranslates:
    """A full config restates every default on purpose, so the file is
    complete. The command must not fail on the first default the CLI can
    only negate -- `replace_nonstandard_residues: true` is the default and
    has only a `--no-` flag, and a real study's full config was declared
    untranslatable for exactly that. A value the run takes anyway needs no
    flag at all."""

    def test_restated_defaults_need_no_flags(self) -> None:
        from fastmdxplora.gui.config_builder import config_yaml

        out = config_yaml({"system": "trp_cage.pdb",
                           "analysis": {"include": ["rg", "rmsd"]}},
                          full=True)
        assert out["ok"] and out["command"] is not None
        assert "replace-nonstandard" not in out["command"]
        assert "--analyze-analyses rg rmsd" in out["command"]
        assert out["command"] in out["yaml"]

    def test_a_default_negated_on_purpose_is_still_said(self) -> None:
        """Skipping applies to values *equal* to the default; a chosen
        departure still renders, through the negation flag it has."""
        from fastmdxplora.config.languages import cli_command

        command = cli_command(
            {"setup": {"replace_nonstandard_residues": False}})
        assert "no-replace-nonstandard-residues" in command


class TestAResolvedConfigTranslatesToWhatWasChosen:
    """The file a finished run leaves, put back through the other two.

    It names every setting the run used -- a hundred and eight of them for
    a study that decided three -- and both renderers are asked for the
    three. The command was carrying a ligand for a protein with none, and
    the script was a hundred lines of which four were chosen, with which
    four being the only thing a reader wants from it.
    """

    @staticmethod
    def _resolved(tmp_path, **options):
        from fastmdxplora.config import write_resolved_config
        from fastmdxplora.config.loader import (
            load_config_file, normalise_config,
        )

        path = write_resolved_config(
            {"system": "1UBQ", "options": options}, tmp_path)
        return normalise_config(load_config_file(path))

    def test_the_command_says_only_what_was_chosen(self, tmp_path) -> None:
        from fastmdxplora.config.languages import cli_command

        command = cli_command(self._resolved(
            tmp_path, setup={"ph": 6.5},
            simulation={"duration_ns": 50, "pressure_atm": 1.2}))
        assert "--setup-ph 6.5" in command
        assert "--simulate-duration-ns 50" in command
        assert "--simulate-pressure-atm 1.2" in command
        assert command.count(" --") == 4, f"too much said: {command}"

    def test_no_ligand_is_named_for_a_protein_without_one(
        self, tmp_path
    ) -> None:
        """`ligand_resname` declares no default and is nonetheless "LIG"
        in every normalised config, mirrored from `ligand_name`, which
        does. Compared against the raw schema default it looks chosen."""
        from fastmdxplora.config.languages import cli_command, python_script

        config = self._resolved(tmp_path, setup={"ph": 6.5})
        assert "ligand" not in cli_command(config)
        assert "ligand" not in python_script(config)

    def test_the_script_carries_the_decisions_and_not_the_defaults(
        self, tmp_path
    ) -> None:
        from fastmdxplora.config.languages import python_script

        script = python_script(self._resolved(
            tmp_path, setup={"ph": 6.5}, simulation={"duration_ns": 50}))
        assert "'ph': 6.5" in script
        assert "'duration_ns': 50" in script
        assert "None" not in script
        assert "'report'" not in script, "a phase that decided nothing"

    def test_the_script_still_runs_the_same_study(self, tmp_path) -> None:
        """Dropping a setting is safe exactly when the run settles on the
        same value without it. That is the rule both renderers apply, and
        this is it stated over a whole config rather than trusted.
        """
        import ast

        from fastmdxplora.config.languages import (
            _settled_defaults, python_script,
        )

        config = self._resolved(
            tmp_path, setup={"ph": 6.5, "box_shape": "cube"})
        script = python_script(config)
        tree = ast.parse(script)
        call = next(node for node in ast.walk(tree)
                    if isinstance(node, ast.Call)
                    and getattr(node.func, "attr", "") == "FastMDXplora")
        passed = next(ast.literal_eval(kw.value) for kw in call.keywords
                      if kw.arg == "options")

        settled = _settled_defaults()["setup"]
        assert {**settled, **passed["setup"]} == config["setup"]
        assert passed["setup"] == {"ph": 6.5, "box_shape": "cube"}

    def test_the_one_setting_dropped_by_the_mirror_is_reached_anyway(
        self
    ) -> None:
        """`ligand_resname` is left out of both renderings, so what reads
        it must fall back to `ligand_name` -- which carries the same value
        and is the spelling setup has always used."""
        import inspect

        from fastmdxplora.setup.pipeline import _explicit_ligand_resnames

        source = inspect.getsource(_explicit_ligand_resnames)
        assert 'params.get("ligand_name") or params.get("ligand_resname")' \
            in source
        assert _explicit_ligand_resnames({"ligand_name": "LIG"}) == ("LIG",)
        assert _explicit_ligand_resnames({"ligand_resname": "LIG"}) == ("LIG",)


class TestWhatCannotBeSaid:
    def test_many_systems_refuse_with_the_reason(self) -> None:
        with pytest.raises(UntranslatableSetting, match="config-file"):
            cli_command({"systems": [{"system": "1UBQ"}, {"system": "4HHB"}]})

    def test_the_refusal_names_the_setting(self) -> None:
        with pytest.raises(UntranslatableSetting, match="setup.nonsense"):
            cli_command({"setup": {"nonsense": 1}})


class TestThePythonSpelling:
    def test_the_script_is_valid_python_in_the_documented_shape(self) -> None:
        script = python_script({
            "system": "181L.pdb", "output": "runs/t4",
            "include": ["setup"],
            "setup": {"ligand_pose": "file"},
        })
        compile(script, "study.py", "exec")  # syntax is the contract
        assert "fastmdx.FastMDXplora(" in script
        assert "system='181L.pdb'" in script
        assert "output_dir='runs/t4'" in script
        assert "'ligand_pose': 'file'" in script
        assert "explore(include_phase=['setup'])" in script

    def test_the_batch_shape_yields_its_one_system(self) -> None:
        script = python_script({"systems": [{"system": "1UBQ"}]})
        assert "system='1UBQ'" in script

    def test_no_blocks_means_no_options_argument(self) -> None:
        script = python_script({"system": "1UBQ"})
        assert "options=" not in script


class TestTheGuiHandsAllThreeBack:
    def test_the_payload_carries_command_and_script(self) -> None:
        from fastmdxplora.gui.config_builder import config_yaml

        out = config_yaml({"system": "181L.pdb",
                           "setup": {"ligand_pose": "file"}})
        assert out["ok"]
        assert "--setup-ligand-pose file" in out["command"]
        compile(out["script"], "study.py", "exec")

    def test_the_command_is_written_into_the_config_file_itself(self) -> None:
        """The file is what travels; the command travels inside it, so a
        reader of the file six months on has both spellings in hand."""
        from fastmdxplora.gui.config_builder import config_yaml

        out = config_yaml({"system": "181L.pdb",
                           "setup": {"ligand_pose": "file"}})
        assert out["command"] in out["yaml"]
        assert "# Or, without this file:" in out["yaml"]

    def test_an_invalid_study_offers_no_spellings(self) -> None:
        from fastmdxplora.gui.config_builder import config_yaml

        out = config_yaml({"system": "1UBQ",
                           "analysis": {"include": ["contacts"]}})
        assert not out["ok"]
        assert out.get("command") is None

    def test_the_page_has_the_buttons_and_the_handlers(self) -> None:
        from pathlib import Path

        html = Path("src/fastmdxplora/gui/templates/dashboard.html").read_text(
            encoding="utf-8")
        js = Path("src/fastmdxplora/gui/static/run-builder.js").read_text(
            encoding="utf-8")
        for anchor in ("run-copy-command", "run-download-script"):
            assert anchor in html and anchor in js
        assert "fastmdxplora_study.py" in js
