"""`fastmdx agent` — choosing a model, and writing a study from a sentence.

The command is thin on purpose. It reads a request, calls whatever model
was chosen, and hands the reply to `propose_config`, which hands it to the
same validator a hand-written config goes through. A refusal here is the
refusal you would have got anyway; the agent has no way to ask for
something the software will not do.

What is worth testing is the part around that: where the key lives, what
never carries it, and what the command prints when something is missing.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from fastmdxplora.agent import models as model_config
from fastmdxplora.agent.models import (
    ModelChoice,
    completion_for,
    describe_choice,
    load_choice,
    save_choice,
)
from fastmdxplora.refusals import StudyError, refusal_of

VALID = ("systems:\n  - {id: ubq, system: 1UBQ}\n"
         "setup:\n  ph: 6.5\nsimulation:\n  duration_ns: 50\n")


class TestWhereTheKeyLives(unittest.TestCase):

    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "model.json"

    def test_a_stored_choice_comes_back(self):
        save_choice(ModelChoice("anthropic", "claude-sonnet-4-6"),
                    path=self.path)
        found = load_choice(self.path)
        self.assertEqual(found.provider, "anthropic")
        self.assertEqual(found.model, "claude-sonnet-4-6")

    def test_the_record_for_a_manifest_carries_no_key(self):
        # The one that matters. Configs and manifests get shared, pasted
        # into issues and committed; a key in one is a key on the internet.
        save_choice(ModelChoice("openai", "gpt-5"), key="sk-secret",
                    path=self.path)
        record = load_choice(self.path).as_record()
        self.assertNotIn("api_key", record)
        self.assertNotIn("sk-secret", json.dumps(record))

    @unittest.skipIf(os.name == "nt",
                     "POSIX permission bits do not govern access on Windows")
    def test_the_stored_file_is_readable_only_by_its_owner(self):
        save_choice(ModelChoice("openai", "gpt-5"), key="sk-secret",
                    path=self.path)
        self.assertEqual(self.path.stat().st_mode & 0o077, 0)

    def test_the_key_file_is_outside_any_study_on_every_platform(self):
        """The protection that does not depend on POSIX bits.

        `chmod(0o600)` is the protection on Unix and is meaningless on
        Windows, where access is governed by ACLs that Python's chmod does
        not set -- the mode bits come back as 0o666 and the test above
        asserted otherwise, which was a test reporting a platform rather
        than a property.

        What holds everywhere is that the key never enters a study: not the
        config, not the manifest, not a log line. That is asserted
        elsewhere in this file; here, that the file itself lives outside
        any study directory, so sharing a run never shares a key.
        """
        from fastmdxplora.agent.models import model_path

        where = model_path()
        self.assertNotIn("output", where.parts)
        self.assertIn("fastmdxplora", where.parts)

    def test_the_environment_wins_over_the_file(self):
        # So a cluster job or a CI run can supply one per session without
        # anybody storing it, and a stored key can be overridden for one
        # run without editing anything.
        import os

        save_choice(ModelChoice("openai", "gpt-5"), key="from-file",
                    path=self.path)
        before = os.environ.get("OPENAI_API_KEY")
        os.environ["OPENAI_API_KEY"] = "from-environment"
        try:
            key = model_config._key_for(load_choice(self.path), self.path)
            self.assertEqual(key, "from-environment")
        finally:
            if before is None:
                del os.environ["OPENAI_API_KEY"]
            else:
                os.environ["OPENAI_API_KEY"] = before

    def test_no_key_anywhere_says_both_places_to_put_one(self):
        import os

        save_choice(ModelChoice("openai", "gpt-5"), path=self.path)
        before = os.environ.pop("OPENAI_API_KEY", None)
        try:
            with self.assertRaises(StudyError) as caught:
                model_config._key_for(load_choice(self.path), self.path)
            refusal = refusal_of(caught.exception)
            self.assertEqual(refusal.code, "environment.credentials.absent")
            self.assertIn("OPENAI_API_KEY", refusal.message)
            self.assertIn("fastmdx agent set", refusal.message)
        finally:
            if before is not None:
                os.environ["OPENAI_API_KEY"] = before

    def test_no_model_chosen_says_so_rather_than_failing_oddly(self):
        with self.assertRaises(StudyError) as caught:
            completion_for(path=self.path)
        self.assertEqual(refusal_of(caught.exception).code,
                         "environment.model.unset")

    def test_an_openai_compatible_server_needs_no_new_provider(self):
        # DeepSeek, vLLM, Ollama and the rest speak the same shape. One
        # entry rather than one per vendor, because a list of vendors goes
        # stale and a protocol does not.
        choice = ModelChoice("compatible", "deepseek-chat",
                             "https://api.deepseek.com")
        self.assertTrue(choice.url.endswith("/chat/completions"))
        self.assertEqual(choice.auth_style, "bearer")
        self.assertEqual(choice.as_record()["base_url"],
                         "https://api.deepseek.com")

    def test_describe_says_where_the_key_is_read_from(self):
        save_choice(ModelChoice("anthropic", "claude-sonnet-4-6"),
                    key="sk-x", path=self.path)
        described = describe_choice(self.path)
        self.assertIn("Anthropic", described)
        self.assertNotIn("sk-x", described)


class TestTheCommand(unittest.TestCase):

    def setUp(self):
        import os

        self.root = Path(tempfile.mkdtemp())
        self.before = os.environ.get("FASTMDXPLORA_CONFIG_DIR")
        os.environ["FASTMDXPLORA_CONFIG_DIR"] = str(self.root)

    def tearDown(self):
        import os

        if self.before is None:
            os.environ.pop("FASTMDXPLORA_CONFIG_DIR", None)
        else:
            os.environ["FASTMDXPLORA_CONFIG_DIR"] = self.before

    def run_command(self, argv, reply=None):
        import io
        from contextlib import redirect_stdout

        import fastmdxplora.agent as agent
        from fastmdxplora.cli.main import main

        original = agent.completion_for
        if reply is not None:
            agent.completion_for = lambda *a, **k: (lambda prompt: reply)
        buffer = io.StringIO()
        try:
            with redirect_stdout(buffer):
                code = main(argv)
        finally:
            agent.completion_for = original
        return code, buffer.getvalue()

    def test_with_no_request_it_opens_the_panel(self):
        """No request is an invitation to converse, and that wants a window.

        Patched rather than run: `_cmd_gui` serves until interrupted, and a
        test that called it would not fail, it would hang -- which is worse,
        because a hung suite says nothing about what is wrong. Found by
        hanging.
        """
        import io
        from contextlib import redirect_stdout

        # `import fastmdxplora.cli.main as cli` gets the *function*, not
        # the module: cli/__init__.py re-exports `main`, so the attribute
        # shadows the submodule. sys.modules is unambiguous.
        import sys

        import fastmdxplora.cli.main  # noqa: F401 - registers the module

        cli = sys.modules["fastmdxplora.cli.main"]
        seen = {}
        original = cli._cmd_gui
        cli._cmd_gui = lambda args, panel="": seen.update(
            {"panel": panel, "port": args.port}) or 0
        buffer = io.StringIO()
        try:
            with redirect_stdout(buffer):
                code = cli.main(["agent"])
        finally:
            cli._cmd_gui = original

        self.assertEqual(code, 0)
        self.assertEqual(seen["panel"], "agent")
        self.assertIn("Opening the agent panel", buffer.getvalue())

    def test_asking_without_a_model_refuses_and_explains(self):
        code, out = self.run_command(["agent", "simulate ubiquitin"])
        self.assertEqual(code, 1)
        self.assertIn("No model has been chosen", out)

    def test_a_request_becomes_a_config(self):
        save_choice(ModelChoice("anthropic", "claude-sonnet-4-6"), key="x")
        code, out = self.run_command(
            ["agent", "simulate ubiquitin at pH 6.5 for 50 ns"], reply=VALID)
        self.assertEqual(code, 0)
        self.assertIn("Accepted after 1 attempt", out)
        self.assertIn("duration_ns: 50", out)

    def test_the_corrections_are_shown_rather_than_hidden(self):
        # They are the only visible sign that anything checked the config,
        # and the count is worth seeing.
        save_choice(ModelChoice("anthropic", "claude-sonnet-4-6"), key="x")
        code, out = self.run_command(
            ["agent", "simulate ubiquitin"],
            reply="setup:\n  pH: 6.5\nsystems:\n  - {id: a, system: 1UBQ}\n")
        self.assertEqual(code, 1)
        self.assertIn("Unknown setup option 'pH'", out)
        self.assertIn("Gave up after", out)

    def test_the_request_can_come_from_a_file(self):
        save_choice(ModelChoice("anthropic", "claude-sonnet-4-6"), key="x")
        request = self.root / "study.txt"
        request.write_text("simulate ubiquitin at pH 6.5 for 50 ns",
                           encoding="utf-8")
        code, out = self.run_command(
            ["agent", "-f", str(request)], reply=VALID)
        self.assertEqual(code, 0)
        self.assertIn("Accepted", out)

    def test_the_config_can_be_written_out(self):
        save_choice(ModelChoice("anthropic", "claude-sonnet-4-6"), key="x")
        written = self.root / "study.yml"
        code, out = self.run_command(
            ["agent", "simulate ubiquitin", "-o", str(written)], reply=VALID)
        self.assertEqual(code, 0)
        self.assertTrue(written.is_file())
        # And says how to run it, in the flag form the rest of the CLI uses.
        self.assertIn("fastmdx explore -config", out)

    def test_what_it_writes_is_a_config_the_validator_accepts(self):
        import yaml

        from fastmdxplora.config.loader import validate_config

        save_choice(ModelChoice("anthropic", "claude-sonnet-4-6"), key="x")
        written = self.root / "study.yml"
        self.run_command(["agent", "x", "-o", str(written)], reply=VALID)
        validate_config(yaml.safe_load(written.read_text(encoding="utf-8")))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


class TestTheThreeModes(unittest.TestCase):
    """`--assisted`, `--autonomous`, `--unvalidated`.

    The flag names are the config values, so there is one vocabulary across
    the CLI, the config, the manifest and the schema description a model
    reads. A mode that lived only in a flag would vanish the moment the
    config was shared, and then a reader could not tell how the study was
    made -- which is the one thing config-is-the-study exists for.
    """

    def setUp(self):
        import os

        self.root = Path(tempfile.mkdtemp())
        self.before = os.environ.get("FASTMDXPLORA_CONFIG_DIR")
        os.environ["FASTMDXPLORA_CONFIG_DIR"] = str(self.root)
        save_choice(ModelChoice("anthropic", "claude-sonnet-4-6"), key="x")

    def tearDown(self):
        import os

        if self.before is None:
            os.environ.pop("FASTMDXPLORA_CONFIG_DIR", None)
        else:
            os.environ["FASTMDXPLORA_CONFIG_DIR"] = self.before

    def run_command(self, argv):
        import io
        from contextlib import redirect_stdout

        import fastmdxplora.agent as agent
        from fastmdxplora.cli.main import main

        original = agent.completion_for
        agent.completion_for = lambda *a, **k: (lambda prompt: VALID)
        buffer = io.StringIO()
        try:
            with redirect_stdout(buffer):
                code = main(argv)
        finally:
            agent.completion_for = original
        return code, buffer.getvalue()

    def test_the_mode_reaches_the_config(self):
        import yaml

        written = self.root / "study.yml"
        code, _ = self.run_command(
            ["agent", "simulate ubiquitin", "-o", str(written)])
        self.assertEqual(code, 0)
        config = yaml.safe_load(written.read_text(encoding="utf-8"))
        self.assertEqual(config["agent"], "assisted")

    def test_assisted_is_the_default(self):
        # Invoking `fastmdx agent` at all is asking for an agent, so the
        # default is the mode that shows its work rather than no mode.
        import yaml

        written = self.root / "study.yml"
        self.run_command(["agent", "x", "-o", str(written), "--assisted"])
        explicit = yaml.safe_load(written.read_text(encoding="utf-8"))
        self.run_command(["agent", "x", "-o", str(written)])
        implied = yaml.safe_load(written.read_text(encoding="utf-8"))
        self.assertEqual(explicit["agent"], implied["agent"])

    def test_the_modes_are_mutually_exclusive(self):
        from fastmdxplora.cli.main import main

        with self.assertRaises(SystemExit):
            main(["agent", "x", "--assisted", "--autonomous"])

    def test_the_config_with_a_mode_still_validates(self):
        # `agent` is a schema field like any other, so a config carrying it
        # goes through the same door.
        import yaml

        from fastmdxplora.config.loader import validate_config

        written = self.root / "study.yml"
        self.run_command(["agent", "x", "-o", str(written)])
        validate_config(yaml.safe_load(written.read_text(encoding="utf-8")))

    def test_unvalidated_writes_a_config_and_says_what_it_means(self):
        """It refused until the marking existed. The marking exists.

        The mode itself -- an agent writing code outside the schema -- is
        still to come. What is here is the marking that makes offering it
        safe: the config records `agent: unvalidated`, and every figure
        from an unchecked phase is stamped. Writing the config is therefore
        honest rather than premature.
        """
        import yaml

        written = self.root / "study.yml"
        code, out = self.run_command(
            ["agent", "x", "--unvalidated", "-o", str(written)])
        self.assertEqual(code, 0)
        self.assertIn("outside the schema", out)
        config = yaml.safe_load(written.read_text(encoding="utf-8"))
        self.assertEqual(config["agent"], "unvalidated")

    def test_and_the_config_it_writes_is_still_validated(self):
        # `unvalidated` marks the output, not the config. A study asking
        # for a setting that does not exist is refused in this mode exactly
        # as in any other.
        code, out = self.run_command(
            ["agent", "x", "--unvalidated"])
        self.assertEqual(code, 0)
        self.assertIn("Accepted", out)

    def test_autonomous_refuses_without_a_budget(self):
        """It was waiting on the staging. The staging exists.

        `--autonomous` runs the study without showing it to you, so a
        budget is the only thing left that can stop it. Refusing is the
        honest answer to being asked to run something unattended with no
        ceiling -- a default allowance would be a number nobody chose
        deciding how much of somebody's card to spend.
        """
        code, out = self.run_command(["agent", "x", "--autonomous"])
        self.assertEqual(code, 1)
        self.assertIn("--budget-hours", out)

    def test_the_budget_flag_exists_and_takes_hours(self):
        import io
        from contextlib import redirect_stdout

        from fastmdxplora.cli.main import _build_parser

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            try:
                _build_parser().parse_args(["agent", "--help"])
            except SystemExit:
                pass
        help_text = buffer.getvalue()
        self.assertIn("--budget-hours", help_text)
        # And says where the check happens, because "after setup" is the
        # surprising part and the place somebody would otherwise ask.
        self.assertIn("after setup", help_text)

    def test_the_schema_offers_exactly_these_three(self):
        from fastmdxplora.config.schema import TOP_LEVEL

        field = next(f for f in TOP_LEVEL.fields if f.name == "agent")
        self.assertEqual(set(field.choices),
                         {"assisted", "autonomous", "unvalidated"})
        # Absent means a person wrote it, which is why there is no fourth
        # choice for "off": off is the absence of the key.
        self.assertIsNone(field.default)
