"""What the loop does when told no, and what it declines to keep trying.

The easy property is that a valid config comes back. The ones worth
asserting are the restraints:

  - a structural refusal is retried, because the schema can answer it;
  - a semantic one is not, because the system does not determine an
    answer and a retry would be the model guessing at what the software
    declined to guess at;
  - the cap holds, because cheap validation invites thrashing and a
    config that validates on the fortieth mutation validates for reasons
    nobody chose;
  - and a refused proposal returns no config at all, rather than the last
    thing that nearly worked with a warning attached.

No model is called. A stub returning scripted replies is not a weaker
test here -- the thing under test is the loop's control flow, and a real
model would make it non-deterministic without making it more thorough.
"""

from __future__ import annotations

import unittest

from fastmdxplora.agent import Proposal, propose_config, repair_prompt_for
from fastmdxplora.refusals import Kind, Refusal


def scripted(*replies: str):
    """A model that says these things in order, then repeats the last."""
    box = list(replies)

    def complete(prompt: str) -> str:
        return box.pop(0) if len(box) > 1 else box[0]

    return complete


VALID = "setup:\n  ph: 7.4\nsystems:\n  - {id: a, system: 1UBQ}\n"


class TestTheLoopConverges(unittest.TestCase):

    def test_a_config_that_validates_comes_back_first_time(self):
        result = propose_config("ubiquitin at pH 7.4", scripted(VALID))
        self.assertTrue(result.accepted)
        self.assertEqual(result.cycles, 1)
        self.assertEqual(result.config["setup"]["ph"], 7.4)

    def test_a_typo_is_repaired_and_the_cycles_are_counted(self):
        result = propose_config(
            "ubiquitin", scripted(
                "setup:\n  pH: 7.4\nsystems:\n  - {id: a, system: 1UBQ}\n",
                VALID,
            ))
        self.assertTrue(result.accepted)
        self.assertEqual(result.cycles, 2)
        self.assertEqual(
            [a.refusal.code for a in result.attempts if a.refusal],
            ["config.option.unknown"],
        )

    def test_a_reply_that_is_not_yaml_is_refused_and_retried(self):
        result = propose_config(
            "ubiquitin", scripted("Sure! Here is a config for you.", VALID))
        self.assertTrue(result.accepted)
        self.assertEqual(result.attempts[0].refusal.code,
                         "config.file.unparseable")

    def test_code_fences_are_tolerated(self):
        # Models add them whether or not they are asked to. Refusing over
        # punctuation would spend a cycle on nothing.
        result = propose_config(
            "ubiquitin", scripted(f"```yaml\n{VALID}```"))
        self.assertTrue(result.accepted)
        self.assertEqual(result.cycles, 1)


class TestTheLoopStops(unittest.TestCase):

    def test_the_cap_holds_and_exhausting_it_is_a_refusal(self):
        never = "setup:\n  not_a_setting: 1\nsystems:\n  - {id: a, system: x}\n"
        result = propose_config("ubiquitin", scripted(never), max_cycles=3)
        self.assertFalse(result.accepted)
        self.assertEqual(result.cycles, 3)
        self.assertIsNotNone(result.refusal)

    def test_a_refused_proposal_carries_no_config(self):
        # Not the last thing that nearly worked, with a caveat. A config
        # that has not passed the validator is not one this package will
        # run, and handing one back would move the decision to the caller
        # -- which is the decision the validator exists to take away.
        never = "setup:\n  not_a_setting: 1\nsystems:\n  - {id: a, system: x}\n"
        result = propose_config("ubiquitin", scripted(never), max_cycles=2)
        self.assertIsNone(result.config)
        self.assertTrue(all(a.refusal for a in result.attempts))

    def test_a_semantic_refusal_is_not_retried(self):
        # The restraint this design turns on. A structural refusal says the
        # config does not match the schema, which reading can fix. A
        # semantic one says the system does not determine what to do, and
        # no rewording changes that.
        calls: list[str] = []

        def complete(prompt: str) -> str:
            calls.append(prompt)
            return VALID

        def refuse_semantically(config):
            from fastmdxplora.config.loader import ConfigError
            raise ConfigError(
                "LIG carries groups whose protonation depends on pH.",
                code="setup.chemistry.protonation_undetermined",
                resname="LIG", ph=7.4,
            )

        import fastmdxplora.agent.propose as module
        original = module.validate_config
        module.validate_config = refuse_semantically
        try:
            result = propose_config("a ligand at pH 7.4", complete,
                                    max_cycles=5)
        finally:
            module.validate_config = original

        self.assertFalse(result.accepted)
        self.assertEqual(result.cycles, 1, "a semantic refusal was retried")
        self.assertEqual(len(calls), 1)
        self.assertEqual(result.refusal.kind, Kind.SEMANTIC)


class TestTheRepairPromptWithholds(unittest.TestCase):
    """It says what the schema permits. It never says what chemistry needs."""

    def test_a_structural_refusal_offers_the_permitted_set(self):
        refusal = Refusal(
            code="config.option.not_permitted",
            message="box_shape does not accept 'dodecahedran'.",
            details={"option": "box_shape",
                     "permitted": ["cube", "dodecahedron", "octahedron"],
                     "suggestion": "dodecahedron"},
        )
        prompt = repair_prompt_for("setup: {box_shape: dodecahedran}", refusal)
        self.assertIn("'dodecahedron'", prompt)
        self.assertIn("box_shape", prompt)

    def test_a_semantic_refusal_offers_nothing_to_choose_from(self):
        # Even though the raise site here passed a set. The gate reads the
        # registry, so being wrong at a raise site cannot widen what a
        # model is told.
        refusal = Refusal(
            code="setup.chemistry.protonation_undetermined",
            message="LIG carries groups whose protonation depends on pH.",
            details={"resname": "LIG",
                     "permitted": ["protonated", "deprotonated"]},
        )
        prompt = repair_prompt_for("setup: {ligand: lig.sdf}", refusal)
        self.assertNotIn("Permitted values", prompt)
        self.assertNotIn("deprotonated", prompt)
        self.assertIn("protonation depends on pH", prompt)

    def test_no_remedy_is_volunteered_beyond_the_schema(self):
        # A validator that hands over the fix turns every rejection into a
        # well-specified task, which flatters the measurement and moves the
        # domain reasoning out of the part being measured.
        refusal = Refusal(
            code="setup.structure.undetermined",
            message="This structure does not determine what should be simulated.",
            details={"components": ["HEM", "SO4"]},
        )
        prompt = repair_prompt_for("systems: [{system: 1ABC}]", refusal)
        for word in ("try", "instead", "you should", "recommend"):
            self.assertNotIn(word, prompt.lower())


class TestTheRecordIsUsable(unittest.TestCase):

    def test_it_reports_cycles_and_the_codes_seen(self):
        # Cycles-to-valid is a direct reading of domain competence,
        # comparable across models and free to collect.
        result = propose_config(
            "ubiquitin", scripted(
                "setup:\n  pH: 7.4\nsystems:\n  - {id: a, system: 1UBQ}\n",
                VALID))
        record = result.as_record()
        self.assertTrue(record["accepted"])
        self.assertEqual(record["cycles"], 2)
        self.assertEqual(record["codes"], ["config.option.unknown"])

    def test_it_is_json_serialisable(self):
        import json
        json.dumps(propose_config("x", scripted(VALID)).as_record())


class TestTheBoundaryHolds(unittest.TestCase):

    def test_core_does_not_depend_on_the_agent(self):
        """Core must run with the agent absent.

        The rule this started as -- no file outside `agent/` mentions it --
        was right until the CLI gained `fastmdx agent`, which has to
        dispatch somewhere. The property it was protecting is narrower
        than the rule was: core must not *depend* on the agent, so that
        `fastmdxplora` installed without the extra is a complete
        simulation package and the safety cannot be said to come from a
        layer a caller could remove.

        A module-level import creates that dependency. An import inside
        the one function that handles `fastmdx agent` does not: nothing is
        loaded until somebody asks for it, and asking without the extra
        installed fails at that line with an ImportError naming what to
        install.

        So the rule is now about where the import sits, which is the thing
        that was actually meant.
        """
        import ast
        import pathlib

        import fastmdxplora
        root = pathlib.Path(fastmdxplora.__file__).parent
        offenders = []
        for path in root.rglob("*.py"):
            if "agent" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                elif isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                if any("fastmdxplora.agent" in n for n in names):
                    # col_offset 0 is module level; anything indented is
                    # inside a function and only runs when called.
                    if node.col_offset == 0:
                        offenders.append(f"{path.name}:{node.lineno}")
        self.assertEqual(
            offenders, [],
            f"core imports the agent at module level: {offenders}. "
            "Import it inside the function that needs it, so the package "
            "still works when the extra is not installed.")

    def test_the_package_imports_without_the_agent(self):
        # The property the rule above protects, asserted directly. If
        # `agent` were unimportable, everything else must still work.
        import importlib
        import pkgutil

        import fastmdxplora
        failures = []
        for info in pkgutil.walk_packages(fastmdxplora.__path__,
                                          prefix="fastmdxplora."):
            if ".agent" in info.name:
                continue
            try:
                importlib.import_module(info.name)
            except ImportError as exc:  # pragma: no cover
                failures.append(f"{info.name}: {exc}")
        self.assertEqual(failures, [])

    def test_the_proposing_loop_knows_nothing_about_providers(self):
        """The loop is provider-agnostic, and that has not changed.

        This began as a ban on the strings `api_key` and `anthropic`
        anywhere in `agent/`, which was right while the agent only ever
        took a caller-supplied function. `fastmdx agent set` changed that:
        somebody has to hold a key, and `models.py` is where.

        The property worth keeping is narrower. `propose_config` and its
        repair loop still take a callable and know nothing about what
        answers it -- so a caller can plug in anything, and `models.py` is
        one convenience built on the same hook rather than a dependency
        baked into the loop.
        """
        import pathlib

        import fastmdxplora.agent.propose as propose

        source = pathlib.Path(propose.__file__).read_text(encoding="utf-8")
        for banned in ("api_key", "API_KEY", "anthropic", "openai",
                       "import requests", "import httpx"):
            with self.subTest(banned=banned):
                self.assertNotIn(banned, source)

    def test_no_client_library_anywhere_in_the_agent(self):
        # One HTTPS request through the standard library. A package that
        # should not gain a dependency should not gain one for this.
        import pathlib

        import fastmdxplora.agent as agent

        root = pathlib.Path(agent.__file__).parent
        for path in root.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for banned in ("import openai", "import anthropic",
                           "import langchain", "import requests",
                           "import httpx"):
                with self.subTest(path=path.name, banned=banned):
                    self.assertNotIn(banned, text)

    def test_the_key_never_reaches_a_study(self):
        # The one that would actually hurt somebody. Configs and manifests
        # get shared, pasted into issues and committed.
        import tempfile
        from pathlib import Path

        from fastmdxplora.agent.models import ModelChoice, load_choice, save_choice

        where = Path(tempfile.mkdtemp()) / "model.json"
        save_choice(ModelChoice("openai", "gpt-5"), key="sk-secret",
                    path=where)
        record = load_choice(where).as_record()
        self.assertNotIn("sk-secret", str(record))
        self.assertNotIn("api_key", record)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
