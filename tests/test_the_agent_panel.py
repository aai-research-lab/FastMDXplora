"""The agent in the GUI: two endpoints and a panel.

The GUI is one more door onto `propose_config` — the same function the CLI
calls and a notebook imports. Nothing in this layer decides whether a
config is acceptable; the validator does that, as it does for a config
somebody typed by hand. So what is worth testing here is the door, not the
decision.

The key is the part that needs care. It is typed in the browser, sent
once, and stored server-side, because a browser cannot hold a secret —
anything the page keeps is readable by anything else the page runs. It is
never sent back, which is the property asserted below: a page that never
receives a key cannot leak one.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from fastmdxplora.gui.agent_panel import model_endpoint, propose_endpoint


class TestChoosingAModel(unittest.TestCase):

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.before = os.environ.get("FASTMDXPLORA_CONFIG_DIR")
        os.environ["FASTMDXPLORA_CONFIG_DIR"] = str(self.root)

    def tearDown(self):
        if self.before is None:
            os.environ.pop("FASTMDXPLORA_CONFIG_DIR", None)
        else:
            os.environ["FASTMDXPLORA_CONFIG_DIR"] = self.before

    def test_it_offers_the_same_providers_the_cli_does(self):
        from fastmdxplora.agent.models import PROVIDERS

        answer = model_endpoint({})
        self.assertEqual([p["id"] for p in answer["providers"]],
                         list(PROVIDERS))

    def test_nothing_chosen_says_so_rather_than_guessing(self):
        self.assertIsNone(model_endpoint({})["current"])

    def test_a_choice_is_stored_and_reported_back(self):
        model_endpoint({"provider": "anthropic", "model": "claude-sonnet-4-6"})
        self.assertEqual(model_endpoint({})["current"]["provider"],
                         "anthropic")

    def test_the_key_never_comes_back(self):
        # The property that matters. A page that never receives a key
        # cannot leak one, to a screenshot, an extension or a bug report.
        model_endpoint({"provider": "openai", "model": "gpt-5",
                        "api_key": "sk-secret"})
        self.assertNotIn("sk-secret", json.dumps(model_endpoint({})))

    def test_a_compatible_server_without_a_url_is_refused(self):
        # Somebody picking it wants DeepSeek or a local model, and the
        # refusal should say which kind of thing is missing.
        answer = model_endpoint({"provider": "compatible", "model": "x"})
        self.assertFalse(answer["ok"])
        self.assertIn("base URL", answer["error"])
        self.assertIn("Ollama", answer["error"])

    def test_it_shows_what_the_compatible_option_is_for(self):
        # A list of servers rather than a list of vendors: the protocol is
        # what is supported, and a vendor list goes stale.
        compatible = next(p for p in model_endpoint({})["providers"]
                          if p["id"] == "compatible")
        labels = {e["label"] for e in compatible["examples"]}
        self.assertIn("Ollama (local)", labels)
        self.assertTrue(compatible["needs_url"])

    def test_an_unknown_provider_is_refused(self):
        answer = model_endpoint({"provider": "gemini"})
        self.assertFalse(answer["ok"])
        self.assertEqual(answer["code"], "config.option.not_permitted")


class TestProposing(unittest.TestCase):

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.before = os.environ.get("FASTMDXPLORA_CONFIG_DIR")
        os.environ["FASTMDXPLORA_CONFIG_DIR"] = str(self.root)
        model_endpoint({"provider": "anthropic",
                        "model": "claude-sonnet-4-6", "api_key": "x"})

    def tearDown(self):
        if self.before is None:
            os.environ.pop("FASTMDXPLORA_CONFIG_DIR", None)
        else:
            os.environ["FASTMDXPLORA_CONFIG_DIR"] = self.before

    def answering(self, reply):
        import fastmdxplora.agent as agent

        original = agent.completion_for
        agent.completion_for = lambda *a, **k: (lambda prompt: reply)
        return original

    def test_an_empty_request_is_refused(self):
        self.assertFalse(propose_endpoint({})["ok"])

    def test_a_sentence_becomes_a_config(self):
        import fastmdxplora.agent as agent

        original = self.answering(
            "systems:\n  - {id: a, system: 1UBQ}\n"
            "setup:\n  ph: 6.5\nsimulation:\n  duration_ns: 50\n")
        try:
            answer = propose_endpoint({"request": "ubiquitin at pH 6.5"})
        finally:
            agent.completion_for = original
        self.assertTrue(answer["ok"])
        self.assertEqual(answer["config"]["setup"]["ph"], 6.5)
        self.assertIn("duration_ns: 50", answer["yaml"])

    def test_the_mode_travels_with_the_config(self):
        # Beside it would vanish the moment the config was shared. In it,
        # the mode reaches resolved_config.yml and the manifest.
        import fastmdxplora.agent as agent

        original = self.answering("systems:\n  - {id: a, system: 1UBQ}\n")
        try:
            answer = propose_endpoint({"request": "x", "agent": "assisted"})
        finally:
            agent.completion_for = original
        self.assertEqual(answer["config"]["agent"], "assisted")

    def test_the_attempts_come_back_whole_rather_than_counted(self):
        # They are the only visible sign that anything checked the config,
        # and somebody watching a model correct itself learns the config
        # language while they wait.
        import fastmdxplora.agent as agent

        original = self.answering(
            "setup:\n  pH: 6.5\nsystems:\n  - {id: a, system: 1UBQ}\n")
        try:
            answer = propose_endpoint({"request": "x", "attempts": 2})
        finally:
            agent.completion_for = original
        self.assertFalse(answer["ok"])
        refusals = [a["refusal"] for a in answer["attempts"] if a["refusal"]]
        self.assertTrue(refusals)
        self.assertEqual(refusals[0]["code"], "config.option.unknown")
        self.assertIn("pH", refusals[0]["message"])

    def test_unvalidated_is_accepted_here_too(self):
        # Same answer as the CLI gives. A mode that behaved differently in
        # one door than the other would be two pieces of software wearing
        # one name.
        import fastmdxplora.agent as agent

        original = self.answering(
            "systems:\n  - {id: a, system: 1UBQ}\n"
            "setup:\n  ph: 6.5\nsimulation:\n  duration_ns: 50\n")
        try:
            answer = propose_endpoint({"request": "x",
                                       "agent": "unvalidated"})
        finally:
            agent.completion_for = original
        self.assertTrue(answer["ok"])
        self.assertEqual(answer["config"]["agent"], "unvalidated")

    def test_with_no_model_chosen_it_says_what_to_do(self):
        os.environ["FASTMDXPLORA_CONFIG_DIR"] = str(Path(tempfile.mkdtemp()))
        answer = propose_endpoint({"request": "x"})
        self.assertFalse(answer["ok"])
        self.assertEqual(answer["code"], "environment.model.unset")


class TestThePageCarriesIt(unittest.TestCase):

    def page(self) -> str:
        import fastmdxplora.gui as gui

        return (Path(gui.__file__).parent / "templates"
                / "dashboard.html").read_text(encoding="utf-8")

    def test_the_panel_and_its_nav_link_are_there(self):
        page = self.page()
        self.assertIn('data-page="agent"', page)
        self.assertIn('data-view-link="agent"', page)

    def test_the_script_is_included_and_shipped(self):
        import fastmdxplora.gui as gui

        self.assertIn("agent-panel.js", self.page())
        script = (Path(gui.__file__).parent / "static" / "agent-panel.js")
        self.assertTrue(script.is_file())

    def test_the_script_calls_the_endpoints_that_exist(self):
        import fastmdxplora.gui as gui

        script = (Path(gui.__file__).parent / "static"
                  / "agent-panel.js").read_text(encoding="utf-8")
        server = (Path(gui.__file__).parent
                  / "server.py").read_text(encoding="utf-8")
        for path in ("/api/agent/model", "/api/agent/propose"):
            with self.subTest(path=path):
                self.assertIn(path, script)
                self.assertIn(f'"{path}"', server)

    def test_the_panel_lands_by_url_fragment(self):
        # One browser and one server: `fastmdx agent` with no request opens
        # the same page at #agent rather than starting a second app.
        import inspect

        from fastmdxplora.cli.main import _cmd_gui

        self.assertIn("panel", inspect.signature(_cmd_gui).parameters)
        self.assertIn("fragment", inspect.getsource(_cmd_gui))

    def test_no_mode_promises_a_run_the_panel_does_not_start(self):
        """`autonomous` offered "draft it and run it" and drafted.

        `propose_endpoint` returns a config in every mode and starts
        nothing, and the panel has no budget field -- which is the one
        thing `--autonomous` refuses to run without, because approving an
        unknown duration is not a decision. A dropdown promising a run is
        the interface saying something the code does not do.
        """
        import inspect

        from fastmdxplora.gui import agent_panel

        source = inspect.getsource(agent_panel.propose_endpoint)
        self.assertNotIn("explore", source)
        self.assertNotIn("FastMDXplora(", source)

        page = self.page()
        start = page.index('<select id="agent-mode">')
        options = page[start:page.index("</select>", start)]
        self.assertNotIn("run it", options)
        for mode in ("assisted", "autonomous", "unvalidated"):
            with self.subTest(mode=mode):
                self.assertIn(f'value="{mode}"', options)

    def test_the_panel_says_a_run_is_started_by_hand(self):
        """Removing the false promise leaves the reader needing the true
        one, or the mode names alone imply it."""
        # The note sits beside the mode field rather than inside its
        # <label>. It describes the panel, not that one control, and the
        # dashboard's own pattern puts a note in `builder-card-note` at
        # card level -- the first version nested it in the label, which is
        # why help text rendered at body size and the fields ran together.
        # The promise moved from a paragraph beside the dropdown to a line
        # per mode in the script, shown for the mode actually chosen. A
        # paragraph covering all three was a paragraph nobody read.
        import pathlib

        import fastmdxplora.gui as gui

        script = (pathlib.Path(gui.__file__).parent / "static"
                  / "agent-panel.js").read_text(encoding="utf-8")
        notes = script[script.index("MODE_NOTES"):script.index("KEY_HELP")]
        self.assertIn("Nothing runs until you say so", notes)
        self.assertIn("ceiling is", notes)
        self.assertIn("stamped", notes)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


class TestTheEndpointsNeedTheMachinesTrust(unittest.TestCase):
    """Bound beyond loopback, the agent endpoints refuse.

    The GUI already gates the endpoints that read the filesystem or start a
    run behind `allow_control`, which is true only for a loopback bind —
    there is no login, so the bind address is the whole of the trust model.

    I added two endpoints and did not put them on that list. One stores an
    API key and the other spends it. Over a network, unauthenticated, that
    is somebody else deciding where this machine's requests go, or burning
    the credit on the key already stored. Neither is a study, so neither
    reads as dangerous at a glance — which is why they belong on a list
    rather than in somebody's judgement.
    """

    def serve(self, host):
        """A dashboard that stops when the test does.

        `serve_dashboard` serves until interrupted, so a test calling it in
        a daemon thread leaves it running for the rest of the session. Four
        of those accumulated and the suite stopped making progress
        somewhere unrelated -- a hang rather than a failure, which says
        nothing about what is wrong. `start_dashboard_session` hands back
        something that can be shut down, and `addCleanup` does.
        """
        import tempfile

        from fastmdxplora.gui.server import start_dashboard_session

        session = start_dashboard_session(
            output=tempfile.mkdtemp(), host=host, port=0)
        self.addCleanup(session.server.shutdown)
        return session.port

    def post(self, port, path, body=None):
        import urllib.error
        import urllib.request

        request = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}", method="POST",
            data=json.dumps(body or {}).encode(),
            headers={"content-type": "application/json"})
        try:
            return urllib.request.urlopen(request).getcode()
        except urllib.error.HTTPError as exc:
            return exc.code

    def test_they_are_on_the_same_list_as_the_rest(self):
        # Asserted from the source as well as by serving, because the list
        # is the thing somebody adding the next endpoint will read.
        import pathlib

        import fastmdxplora.gui as gui

        source = (pathlib.Path(gui.__file__).parent
                  / "server.py").read_text(encoding="utf-8")
        gated = source[source.index("if not allow_control and path in {"):]
        gated = gated[:gated.index("}:")]
        for path in ("/api/agent/model", "/api/agent/propose"):
            with self.subTest(path=path):
                self.assertIn(path, gated)

    def test_bound_beyond_loopback_they_refuse(self):
        port = self.serve("0.0.0.0")
        for path in ("/api/agent/model", "/api/agent/propose"):
            with self.subTest(path=path):
                self.assertEqual(self.post(port, path), 403)

    def test_and_so_do_the_endpoints_that_were_already_gated(self):
        # The comparison that makes the first assertion mean something: if
        # these stopped being gated, the agent ones passing would prove
        # nothing.
        port = self.serve("0.0.0.0")
        self.assertEqual(self.post(port, "/api/explore/start"), 403)

    def test_on_loopback_they_work(self):
        # The gate must not be a way of switching the feature off.
        port = self.serve("127.0.0.1")
        self.assertEqual(self.post(port, "/api/agent/model"), 200)


class TestTheBindAddressIsSaidOutLoud(unittest.TestCase):
    """A network door should announce itself.

    There is no login, so the bind address is the whole trust model, and
    `allow_control` already turns off browsing, config reading and run
    control when it is not loopback. What that cannot do is tell anybody
    it happened — and software that halts on an ambiguous protonation
    state should not open a port in silence.
    """

    def warnings_when_binding(self, host, port):
        import logging
        import tempfile

        from fastmdxplora.gui.server import start_dashboard_session

        said = []
        # "fastmdxplora.gui.server", not "fastmdx.gui.server". Most of the
        # package logs through `utils.logging.get_logger`, which namespaces
        # under "fastmdx"; the GUI server uses a plain getLogger(__name__)
        # and lands under the distribution name instead. Watching the wrong
        # one shows an empty list while the warning prints beside it.
        logger = logging.getLogger("fastmdxplora.gui.server")
        handler = logging.Handler()
        handler.emit = lambda record: said.append(record.getMessage())
        level = logger.level
        logger.addHandler(handler)
        logger.setLevel(logging.WARNING)
        try:
            session = start_dashboard_session(
                output=tempfile.mkdtemp(), host=host, port=0)
            self.addCleanup(session.server.shutdown)
        finally:
            logger.removeHandler(handler)
            logger.setLevel(level)
        return said

    def test_a_non_loopback_bind_warns(self):
        said = " ".join(self.warnings_when_binding("0.0.0.0", 8797))
        self.assertIn("not loopback", said)
        self.assertIn("no login", said)
        # And says what to do instead, because a warning with no remedy is
        # a warning people learn to scroll past.
        self.assertIn("ssh -L", said)

    def test_loopback_says_nothing(self):
        # The ordinary case is silent. A warning that fires every time is
        # a warning nobody reads when it matters.
        said = " ".join(self.warnings_when_binding("127.0.0.1", 8798))
        self.assertNotIn("not loopback", said)

    def test_the_docs_say_the_thing_the_gate_cannot(self):
        # On a shared host, loopback means every logged-in user, and
        # allow_control is true because the bind address is loopback. That
        # is the one exposure the code cannot detect, so it has to be
        # written down.
        import pathlib

        import fastmdxplora
        docs = (pathlib.Path(fastmdxplora.__file__).parents[2]
                / "docs" / "gui.md")
        if not docs.is_file():  # installed without the docs tree
            self.skipTest("docs not present in this layout")
        page = docs.read_text(encoding="utf-8")
        self.assertIn("loopback is not private", page)
        self.assertIn("login node", page)


class TestLoadIntoTheFormActuallyLoads(unittest.TestCase):
    """The button called a global that has never existed.

    `agent-panel.js` asked for `window.FastMDX.loadConfigObject`. The
    globals this package defines are `FastMDXDashboard`, `FastMDXRun`,
    `FastMDXPicker`, `FastMDXCharts` and `FastMDXMoleculeViewer` -- there is
    no `FastMDX`. The truthiness guard around it turned a TypeError into a
    silent no-op, which is why the button looked like it worked.
    """

    @staticmethod
    def _static(name: str) -> str:
        from pathlib import Path

        import fastmdxplora.gui as gui

        return (Path(gui.__file__).parent / "static" / name).read_text(
            encoding="utf-8")

    def test_every_global_a_script_uses_is_one_a_script_defines(self):
        # The test that would have caught it, and catches the next one.
        import re
        from pathlib import Path

        import fastmdxplora.gui as gui

        static = Path(gui.__file__).parent / "static"
        defined, used = set(), set()
        for path in sorted(static.glob("*.js")):
            text = path.read_text(encoding="utf-8")
            defined |= set(re.findall(r"window\.(FastMDX\w*)\s*=", text))
            used |= set(re.findall(r"window\.(FastMDX\w*)\b", text))
        self.assertTrue(
            used <= defined,
            f"these globals are used and never defined: {sorted(used - defined)}")

    def test_the_panel_reaches_the_run_builder_by_its_real_name(self):
        panel = self._static("agent-panel.js")
        self.assertIn("window.FastMDXRun", panel)
        self.assertNotIn("window.FastMDX.", panel)

    def test_the_run_builder_exports_what_the_panel_calls(self):
        self.assertIn("applyLoadedState", self._static("run-builder.js"))
        self.assertIn("applyLoadedState", self._static("agent-panel.js"))

    def test_the_panel_sends_the_config_to_an_endpoint_that_takes_one(self):
        # It holds a mapping, never a path, so the endpoint had to learn to
        # take one rather than the browser learning the mapping.
        self.assertIn("/api/load-config", self._static("agent-panel.js"))


class TestAConfigThatWasNeverOnDiskCanBeOpened(unittest.TestCase):

    def test_state_comes_back_for_a_mapping(self):
        from fastmdxplora.gui.config_builder import state_from_config

        loaded = state_from_config({
            "systems": [{"id": "wt", "system": "1UBQ"}],
            "setup": {"ph": 6.5},
            "analysis": {"include": ["rmsd", "rg"]},
        })

        self.assertTrue(loaded["ok"])
        self.assertEqual(loaded["state"]["system"], "1UBQ")
        self.assertEqual(loaded["state"]["analyses"], ["rmsd", "rg"])
        self.assertEqual(loaded["state"]["phases"]["setup"], {"ph": 6.5})

    def test_an_invalid_config_comes_back_refused(self):
        from fastmdxplora.gui.config_builder import state_from_config

        loaded = state_from_config({
            "systems": [{"id": "wt", "system": "1UBQ"}],
            "setup": {"pH": 6.5},
        })
        self.assertFalse(loaded["ok"])
        self.assertIn("pH", loaded["error"])

    def test_how_the_study_was_written_survives_the_round_trip(self):
        """Dropped before: only the phase blocks were copied, so opening an
        agent-written config in the form and saving it produced one claiming
        a person wrote it."""
        from fastmdxplora.gui.config_builder import (
            build_config,
            state_from_config,
        )

        loaded = state_from_config({
            "systems": [{"id": "wt", "system": "1UBQ"}],
            "agent": "unvalidated",
            "agent_model": "anthropic/x",
            "setup": {"ph": 6.5},
        })
        self.assertEqual(
            loaded["state"]["study"],
            {"agent": "unvalidated", "agent_model": "anthropic/x"})

        # And back out again, the way the form sends it.
        rebuilt = build_config({
            "system": loaded["state"]["system"],
            "include": loaded["state"]["include"],
            "__run__": loaded["state"]["study"],
            **{"setup": loaded["state"]["phases"]["setup"]},
        })
        self.assertEqual(rebuilt["agent"], "unvalidated")
        self.assertEqual(rebuilt["agent_model"], "anthropic/x")


class TestTheOtherTwoModesRunHere(unittest.TestCase):
    """The GUI runs on the user's own machine, so a mode that runs can run.

    `assisted` still loads into the form, because the point of that mode is
    that a person reads the config first. `autonomous` and `unvalidated`
    start from the panel — making them command-line-only was a limitation
    of where the wiring stopped, not a property of the modes.

    Through `launch_from_config`, which is the GUI's own door for running
    what a config describes rather than what a form was wired for. Nothing
    here is a second way of starting a study.
    """

    class Runtime:
        def __init__(self):
            self.started = None

        def launch_from_config(self, state, dashboard_url=None):
            self.started = state
            return {"ok": True, "launched": True}

    def start(self, payload):
        """Not `run`: that is TestCase.run, the method unittest calls to
        execute the test, and overriding it replaces the runner."""
        from fastmdxplora.gui.agent_panel import run_endpoint

        runtime = self.Runtime()
        return run_endpoint(payload, runtime), runtime

    def test_nothing_to_run_is_refused(self):
        answer, runtime = self.start({})
        self.assertFalse(answer["ok"])
        self.assertIsNone(runtime.started)

    def test_autonomous_without_a_budget_is_refused(self):
        # The same reason the CLI refuses: it runs without being shown to
        # anybody, so a ceiling is the only thing left that can stop it.
        answer, runtime = self.start(
            {"config": {"agent": "autonomous", "systems": []}})
        self.assertEqual(answer["code"], "environment.budget.absent")
        self.assertIsNone(runtime.started, "it started anyway")

    def test_a_budget_of_zero_is_not_a_budget(self):
        # For autonomous, where one is required.
        for value in (0, "0", "", None, "not a number"):
            with self.subTest(budget=value):
                answer, runtime = self.start(
                    {"config": {"agent": "autonomous", "systems": []},
                     "budget_hours": value})
                self.assertFalse(answer["ok"])
                self.assertIsNone(runtime.started)

    def test_autonomous_with_a_budget_runs_and_carries_it(self):
        # On the config, so it reaches resolved_config.yml and the
        # manifest rather than living only in the request that started it.
        answer, runtime = self.start(
            {"config": {"agent": "autonomous", "systems": []},
             "budget_hours": 40})
        self.assertTrue(answer["ok"])
        self.assertEqual(runtime.started["config"]["budget_hours"], 40.0)

    def test_only_autonomous_requires_one(self):
        """A budget stands in for a human, so the mode with nobody watching
        must have one. The others have something else standing between them
        and a bad study: `assisted` has the person reading the config,
        `unvalidated` has the mark on every figure -- its risk is an
        unchecked method rather than an unbounded spend.
        """
        for mode in ("assisted", "unvalidated"):
            with self.subTest(mode=mode):
                answer, runtime = self.start(
                    {"config": {"agent": mode, "systems": []}})
                self.assertTrue(answer["ok"])
                self.assertIsNotNone(runtime.started)

    def test_but_every_mode_may_have_one(self):
        """Offered everywhere, demanded in one place.

        The first version hid the field outside `autonomous`, which drew
        the line on the wrong axis: a ceiling is never the wrong thing to
        have on a study that will run for days, and somebody who reads a
        config carefully and then leaves it running overnight wants one as
        much as anybody.
        """
        for mode in ("assisted", "unvalidated", "autonomous"):
            with self.subTest(mode=mode):
                answer, runtime = self.start(
                    {"config": {"agent": mode, "systems": []},
                     "budget_hours": 40})
                self.assertTrue(answer["ok"])
                self.assertEqual(
                    runtime.started["config"]["budget_hours"], 40.0)

    def test_a_budget_is_not_invented_where_none_was_given(self):
        # Absent means absent. A default ceiling would be a number nobody
        # chose deciding when somebody's study stops.
        answer, runtime = self.start(
            {"config": {"agent": "assisted", "systems": []}})
        self.assertNotIn("budget_hours", runtime.started["config"])

    def test_the_run_endpoint_needs_the_machine_s_trust(self):
        # It starts work on this machine, so it belongs with the endpoints
        # that refuse on a non-loopback bind.
        import pathlib

        import fastmdxplora.gui as gui

        source = (pathlib.Path(gui.__file__).parent
                  / "server.py").read_text(encoding="utf-8")
        gated = source[source.index("if not allow_control and path in {"):]
        self.assertIn("/api/agent/run", gated[:gated.index("}:")])

    def test_the_panel_offers_a_budget_in_every_mode(self):
        import pathlib

        import fastmdxplora.gui as gui

        page = (pathlib.Path(gui.__file__).parent / "templates"
                / "dashboard.html").read_text(encoding="utf-8")
        # Present and not hidden: offered in every mode. Its note says
        # which mode demands it, written by the script as the mode changes,
        # rather than a placeholder that has to cover every case at once.
        field = page[page.index('id="agent-budget"'):]
        self.assertNotIn("hidden", field[:field.index(">")])
        script = (pathlib.Path(gui.__file__).parent / "static"
                  / "agent-panel.js").read_text(encoding="utf-8")
        self.assertIn("Required in this mode", script)
        self.assertIn('mode === "autonomous"', script)


class TestThePageIsATextareaAndButtons(unittest.TestCase):
    """What the page says and what it does not.

    Earlier versions explained the mechanism in the subtitle, reported
    which model was configured in a status line, put the engine form on the
    page beside the study, and told a GUI user to run a command. Each was
    a thing a reader had to get past to do what they came for.
    """

    def page(self):
        import pathlib

        import fastmdxplora.gui as gui

        return (pathlib.Path(gui.__file__).parent / "templates"
                / "dashboard.html").read_text(encoding="utf-8")

    def panel(self):
        page = self.page()
        return page[page.index('data-page="agent"'):
                    page.index("</section>", page.index("agent-save-model"))]

    def script(self):
        import pathlib

        import fastmdxplora.gui as gui

        return (pathlib.Path(gui.__file__).parent / "static"
                / "agent-panel.js").read_text(encoding="utf-8")

    def test_the_subtitle_says_what_to_do_and_not_how_it_works(self):
        panel = self.panel()
        self.assertIn("Describe a study in natural language", panel)
        for mechanism in ("corrects itself", "same rules as one you wrote",
                          "cannot ask for a study"):
            with self.subTest(phrase=mechanism):
                self.assertNotIn(mechanism, panel)

    def test_the_engine_is_behind_a_button(self):
        # Not a card competing with the study for attention. It is set once
        # and then irrelevant.
        panel = self.panel()
        self.assertIn('id="agent-settings-open"', panel)
        settings = panel[panel.index('id="agent-settings"'):]
        self.assertIn('role="dialog"', settings)
        self.assertIn("hidden", panel[panel.index('id="agent-settings"') - 60:
                                      panel.index('id="agent-settings"') + 60])

    def test_no_status_line_about_the_engine_on_the_page(self):
        # "Ready. Drafting with <model>." was a status line about an engine
        # on a page about a study. The model is named in the dialog, where
        # somebody is choosing, and recorded in `agent_model` where a
        # reader needs it.
        self.assertNotIn("Drafting with", self.script().replace(
            "* the page itself, as \"Ready. Drafting with <model>.\" -- a status line", ""))

    def test_the_gui_never_tells_you_to_run_a_command(self):
        """A GUI that says "run `fastmdx agent set`" has given up.

        The refusals are written for a terminal, which is right there. This
        surface has a Settings button, so it says that instead.
        """
        script = self.script()
        mapping = script[script.index("IN_THE_GUI"):script.index("function el(")]
        self.assertIn("environment.model.unset", mapping)
        self.assertIn("environment.credentials.absent", mapping)
        self.assertIn("Open Settings", mapping)

    def test_the_key_field_says_where_to_get_one(self):
        script = self.script()
        help_text = script[script.index("KEY_HELP"):script.index("IN_THE_GUI")]
        self.assertIn("platform.claude.com", help_text)
        self.assertIn("platform.openai.com", help_text)
        # And that a subscription is not an API account, which is the thing
        # people get wrong.
        self.assertIn("not the same thing", help_text)

    def test_the_actions_are_the_ones_asked_for(self):
        panel = self.panel()
        for action in ("Write config", "Download", "Explore"):
            with self.subTest(action=action):
                self.assertIn(action, panel)
        # "Draft" is not a word this page uses.
        self.assertNotIn("draft", panel.lower())

    def test_the_download_needs_no_round_trip(self):
        # The config is already in the browser. Asking the server for it
        # again would be a second copy that could differ from the one on
        # screen.
        script = self.script()
        self.assertIn("function download(yaml)", script)
        self.assertIn("study.yml", script)

    def test_the_dialog_is_not_a_file_picker(self):
        # It borrows the shape and not the class: there is exactly one file
        # picker on the page, which is a reasonable thing to assert, and
        # this is not it.
        panel = self.panel()
        self.assertIn("agent-dialog", panel)
        self.assertNotIn("analyse-picker", panel)


class TestTheModelFollowsTheProvider(unittest.TestCase):
    """Choosing a provider chooses among its models, not any string.

    It was a free-text field. Somebody had to know how a provider spells
    its model, and switching provider left the previous one's model in
    place — OpenAI selected with claude-sonnet-4-6 still showing, which is
    a configuration that cannot work and looked fine.

    Not a closed list. A model released next month will not be on it, so
    there is an "Other…" option and a field to name one. Typing a model a
    provider does not have fails at the first request with that provider's
    own message, which is clearer than anything this could say about a
    list it cannot keep current.
    """

    def setUp(self):
        import os
        import tempfile
        from pathlib import Path

        self.before = os.environ.get("FASTMDXPLORA_CONFIG_DIR")
        os.environ["FASTMDXPLORA_CONFIG_DIR"] = str(Path(tempfile.mkdtemp()))

    def tearDown(self):
        import os

        if self.before is None:
            os.environ.pop("FASTMDXPLORA_CONFIG_DIR", None)
        else:
            os.environ["FASTMDXPLORA_CONFIG_DIR"] = self.before

    def script(self):
        import pathlib

        import fastmdxplora.gui as gui

        return (pathlib.Path(gui.__file__).parent / "static"
                / "agent-panel.js").read_text(encoding="utf-8")

    def test_each_hosted_provider_offers_models(self):
        from fastmdxplora.gui.agent_panel import model_endpoint

        for provider in model_endpoint({})["providers"]:
            with self.subTest(provider=provider["id"]):
                if provider["needs_url"]:
                    # Whatever the local server happens to serve. A list
                    # here would be a guess about somebody else's machine.
                    self.assertEqual(provider["models"], [])
                else:
                    self.assertIn(provider["default_model"],
                                  provider["models"])

    def test_a_model_not_on_the_list_still_saves(self):
        # The list is a convenience, not a gate.
        from fastmdxplora.gui.agent_panel import model_endpoint

        model_endpoint({"provider": "openai", "model": "gpt-6-unreleased"})
        self.assertEqual(model_endpoint({})["current"]["model"],
                         "gpt-6-unreleased")

    def test_the_model_is_a_dropdown(self):
        import pathlib

        import fastmdxplora.gui as gui

        page = (pathlib.Path(gui.__file__).parent / "templates"
                / "dashboard.html").read_text(encoding="utf-8")
        self.assertIn('<select id="agent-model">', page)
        self.assertIn('id="agent-model-other"', page)

    def test_switching_provider_refills_the_models(self):
        # The bug: it filled the model only when the field was empty, so
        # the previous provider's model stayed.
        script = self.script()
        self.assertIn("fillModels(spec,", script)
        self.assertNotIn('if (!el("agent-model").value)', script)

    def test_what_is_saved_is_what_is_shown(self):
        # Whichever of the two controls is in use.
        script = self.script()
        self.assertIn("function chosenModel()", script)
        self.assertIn("model: chosenModel(),", script)


class TestWhatBelongsInSettings(unittest.TestCase):
    """The page is a textarea and a button; everything set once is behind
    Settings. Mode and the ceiling are set once and then left alone, so
    they sit with the provider rather than beside the study."""

    def panel(self):
        import pathlib

        import fastmdxplora.gui as gui

        page = (pathlib.Path(gui.__file__).parent / "templates"
                / "dashboard.html").read_text(encoding="utf-8")
        return page[page.index('data-page="agent"'):
                    page.index("</section>", page.index("agent-save-model"))]

    def test_only_the_study_is_on_the_page(self):
        panel = self.panel()
        page = panel[:panel.index('id="agent-settings"')]
        self.assertIn('id="agent-request"', page)
        self.assertIn('id="agent-propose"', page)

    def test_mode_and_the_ceiling_are_behind_settings(self):
        panel = self.panel()
        dialog = panel[panel.index('id="agent-settings"'):]
        for control in ('id="agent-mode"', 'id="agent-budget"',
                        'id="agent-provider"', 'id="agent-key"'):
            with self.subTest(control=control):
                self.assertIn(control, dialog)


class TestTheModelsComeFromTheProvider(unittest.TestCase):
    """A list of model names written into this repository is stale the week
    after it is written, and a model released next month would be missing
    with nothing to explain why. Both hosted providers publish one at
    `/v1/models`, and an OpenAI-compatible server serves the same path
    relative to its base URL -- so a local Ollama answers it too.

    The written list is what to show before there is a key to ask with,
    and when the provider cannot be reached. Being offline should leave
    somebody with a usable dropdown rather than an empty one.
    """

    def setUp(self):
        import os
        import tempfile
        from pathlib import Path

        self.before = os.environ.get("FASTMDXPLORA_CONFIG_DIR")
        os.environ["FASTMDXPLORA_CONFIG_DIR"] = str(Path(tempfile.mkdtemp()))

    def tearDown(self):
        import os

        if self.before is None:
            os.environ.pop("FASTMDXPLORA_CONFIG_DIR", None)
        else:
            os.environ["FASTMDXPLORA_CONFIG_DIR"] = self.before

    def test_it_asks_the_provider(self):
        import json
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        from fastmdxplora.agent import ModelChoice, save_choice
        from fastmdxplora.agent.models import list_models

        asked = {}

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 - http.server's spelling
                asked["path"] = self.path
                body = json.dumps({"data": [{"id": "llama3.1"},
                                            {"id": "qwen2.5-coder"}]}).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        port = server.server_address[1]

        save_choice(ModelChoice("compatible", "llama3.1",
                                f"http://127.0.0.1:{port}/v1"), key="none")
        self.assertEqual(list_models(), ("llama3.1", "qwen2.5-coder"))
        self.assertEqual(asked["path"], "/v1/models")

    def test_an_unreachable_provider_falls_back_rather_than_emptying(self):
        from fastmdxplora.agent import ModelChoice
        from fastmdxplora.agent.models import PROVIDERS, list_models

        found = list_models(ModelChoice("anthropic", "claude-sonnet-4-6"))
        self.assertEqual(found, PROVIDERS["anthropic"]["models"])

    def test_a_provider_with_no_way_to_ask_gives_nothing(self):
        from fastmdxplora.agent import ModelChoice
        from fastmdxplora.agent.models import list_models

        self.assertEqual(list_models(ModelChoice("nope", "x")), ())

    def test_saving_returns_what_the_provider_has(self):
        # So the dropdown fills from the provider the moment there is a key
        # to ask with, without a second round trip the panel has to know to
        # make.
        from fastmdxplora.gui.agent_panel import model_endpoint

        answer = model_endpoint({"provider": "anthropic",
                                 "model": "claude-sonnet-4-6",
                                 "api_key": "sk-not-real"})
        self.assertTrue(answer["ok"])
        self.assertTrue(answer["models"])

    def test_the_gui_says_what_to_do_about_a_missing_key(self):
        import pathlib

        import fastmdxplora.gui as gui

        script = (pathlib.Path(gui.__file__).parent / "static"
                  / "agent-panel.js").read_text(encoding="utf-8")
        self.assertIn("API key required. Open Settings and paste one.",
                      script)


class TestAFailedRunSaysWhy(unittest.TestCase):
    """The reason, not the exit code, and not the last line of the log.

    Reported from the browser: a setup failure showed "FastMDXplora
    exploration failed" and nothing else, while the log held "No structure
    at 'protein.pdb'. The path is read from where fastmdx was run" four
    lines above the end. The panel took the log's last line, which by then
    was a DEBUG about writing the resolved config -- a run keeps logging
    after it fails, so the final line is usually housekeeping.
    """

    def test_the_last_error_line_is_preferred_over_the_last_line(self):
        import inspect

        from fastmdxplora.gui import exploration

        source = inspect.getsource(exploration.DashboardRuntime
                                   ._process_failure_message)
        self.assertIn(' - ERROR - ', source)
        # And the reason leads, because "exited with code 1" is true of
        # every failure and says nothing about this one.
        self.assertIn("exit code", source)

    def test_stop_follows_the_process_and_not_the_directory(self):
        """`active_run` means there is a run to look at, which stays true
        after one fails. Stop the run was still offered for a run that had
        already stopped."""
        import pathlib

        import fastmdxplora.gui as gui

        script = (pathlib.Path(gui.__file__).parent / "static"
                  / "run-builder.js").read_text(encoding="utf-8")
        watcher = script[script.index("function watchForARun"):
                         script.index("async function start()")]
        self.assertIn("detail.process_running", watcher)
        self.assertNotIn("Boolean(detail && detail.active_run)", watcher)

    def test_the_runtime_reports_both_separately(self):
        # The fix only works because the payload distinguishes them.
        import inspect

        from fastmdxplora.gui import exploration

        source = inspect.getsource(exploration.DashboardRuntime.snapshot)
        self.assertIn('"process_running"', source)
        self.assertIn('"active_run"', source)


class TestTheModelListIsAskedForRatherThanGuessed(unittest.TestCase):
    """A guessed name in a dropdown is worse than no dropdown.

    The fallback held `claude-opus-4-1`, invented from a pattern rather
    than read from anywhere. Choosing it returned 404 from the provider —
    and it looked chosen rather than typed, so the obvious reading was that
    the software was broken.

    The fallback is now one string per provider that is known to work.
    Everything else comes from `/v1/models`, asked when Settings opens
    rather than only when a choice is saved: opening the dialog used to
    show the written list, and a model picked from it could fail.
    """

    def setUp(self):
        import os
        import tempfile
        from pathlib import Path

        self.before = os.environ.get("FASTMDXPLORA_CONFIG_DIR")
        os.environ["FASTMDXPLORA_CONFIG_DIR"] = str(Path(tempfile.mkdtemp()))

    def tearDown(self):
        import os

        if self.before is None:
            os.environ.pop("FASTMDXPLORA_CONFIG_DIR", None)
        else:
            os.environ["FASTMDXPLORA_CONFIG_DIR"] = self.before

    def test_the_fallback_is_one_string_not_a_guessed_list(self):
        from fastmdxplora.agent.models import PROVIDERS

        for name, spec in PROVIDERS.items():
            with self.subTest(provider=name):
                offered = spec.get("models") or ()
                self.assertLessEqual(
                    len(offered), 1,
                    "a list written here is a list of guesses")
                if offered:
                    self.assertEqual(offered[0], spec["default_model"])

    def test_opening_settings_asks_the_provider(self):
        import json
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        from fastmdxplora.agent import ModelChoice, save_choice
        from fastmdxplora.gui.agent_panel import model_endpoint

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 - http.server's spelling
                body = json.dumps({"data": [{"id": "one"}, {"id": "two"}]})
                raw = body.encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *a):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        port = server.server_address[1]
        save_choice(ModelChoice("compatible", "one",
                                f"http://127.0.0.1:{port}/v1"), key="k")

        offered = next(p for p in model_endpoint({})["providers"]
                       if p["id"] == "compatible")["models"]
        self.assertEqual(offered, ["one", "two"])

    def test_a_model_the_list_does_not_have_still_saves(self):
        from fastmdxplora.gui.agent_panel import model_endpoint

        answer = model_endpoint({"provider": "anthropic",
                                 "model": "claude-fable-5-1"})
        self.assertTrue(answer["ok"])
        self.assertEqual(answer["current"]["model"], "claude-fable-5-1")

    def test_typing_one_is_offered_in_words(self):
        # It said "Other…", and was reported as "I still can't choose any
        # model, only prelisted ones" -- by somebody with that option in
        # the dropdown in front of them.
        import pathlib

        import fastmdxplora.gui as gui

        script = (pathlib.Path(gui.__file__).parent / "static"
                  / "agent-panel.js").read_text(encoding="utf-8")
        self.assertIn("Type a model name", script)
        self.assertNotIn('? "Other\\u2026"', script)


class TestOneReasonForAFailure(unittest.TestCase):
    """A failed run gets one explanation, and it is the right one.

    Reported from the browser: a run started with Simulate and not Setup,
    failed a minute later with "setup outputs are missing", and the health
    panel attached a paragraph about timesteps, clashes and temperature to
    it. Two reasons on screen for one failure, one of them about a
    simulation that had never taken a step.
    """

    def test_a_missing_setup_is_not_called_numerical_instability(self):
        from fastmdxplora.gui.telemetry import analyze_health

        health = analyze_health(
            {"status": "failed",
             "latest_error": "Simulation cannot start because setup outputs "
                             "are missing in /x/setup. Run the setup phase "
                             "first."}, [])
        self.assertEqual(health["state"], "failed")
        self.assertEqual(health["explanation"], "")

    def test_a_blow_up_still_gets_the_explanation(self):
        from fastmdxplora.gui.telemetry import NUMERIC_EXPLANATION, analyze_health

        for said in ("Particle coordinate is NaN",
                     "The integration became unstable"):
            with self.subTest(error=said):
                health = analyze_health(
                    {"status": "failed", "latest_error": said}, [])
                self.assertEqual(health["explanation"], NUMERIC_EXPLANATION)

    def test_the_builder_refuses_simulate_without_setup_first(self):
        # The pipeline refuses it; the button should refuse it before the
        # pipeline is reached. Starting from a structure, Setup is what
        # turns it into a system.
        import pathlib

        import fastmdxplora.gui as gui

        script = (pathlib.Path(gui.__file__).parent / "static"
                  / "run-builder.js").read_text(encoding="utf-8")
        gate = script[script.index("function whyNotReady"):
                      script.index("function ready()")]
        self.assertIn('state.phases.has("simulation") && '
                      '!state.phases.has("setup")', gate)
        self.assertIn("Simulate needs Setup first", gate)

    def test_an_id_is_optional_and_the_refusal_says_so(self):
        # The accepted config had `- system: 1UAO` and no id, which is
        # fine -- ids are numbered if absent -- while the refusal said each
        # entry needs one. A message that contradicts the validator it
        # speaks for is a message that teaches the wrong rule.
        from fastmdxplora.config.loader import ConfigError, validate_config

        validate_config({"systems": [{"system": "1UAO"}]},
                        require_systems=True)
        with self.assertRaises(ConfigError) as caught:
            validate_config({"simulation": {}}, require_systems=True)
        self.assertIn("optional", str(caught.exception))


class TestWhichPhasesRunIsReadFromTheConfig(unittest.TestCase):
    """`include`/`exclude`, defaulting to all — not which blocks exist.

    Reported from the browser: the Agent wrote a config the validator
    accepts and `fastmdx explore` runs, with a `simulation:` block and no
    `setup:` block. The builder loaded it with only Simulate ticked, so the
    Run button was off and the config looked broken. An absent `setup:`
    block means setup with its defaults, not no setup.

    Two questions were sharing one answer — "does this phase run" and
    "does this phase have custom settings" — which is the shape of the
    npt_steps bug in a different place.
    """

    def load(self, config):
        import tempfile
        from pathlib import Path

        import yaml

        from fastmdxplora.gui.config_builder import load_config_into_state

        path = Path(tempfile.mkdtemp()) / "s.yml"
        path.write_text(yaml.safe_dump(config), encoding="utf-8")
        return load_config_into_state(str(path))["state"]

    def test_no_include_means_every_phase(self):
        state = self.load({"systems": [{"system": "1UAO"}],
                           "simulation": {"duration_ns": 2}})
        self.assertEqual(list(state["phases"]),
                         ["setup", "simulation", "analysis", "report"])
        # `include` too. The browser ticks its boxes from that field, and
        # the first fix corrected `phases` alone -- so this test passed
        # while the form still showed only Simulate. A test that checks
        # the field the code fills rather than the field the consumer
        # reads is a test of the wrong thing.
        self.assertEqual(state["include"],
                         ["setup", "simulation", "analysis", "report"])

    def test_the_two_fields_agree_in_every_case(self):
        for config in (
                {"systems": [{"system": "1UAO"}], "simulation": {}},
                {"systems": [{"system": "1UAO"}], "include": ["setup"]},
                {"systems": [{"system": "1UAO"}], "exclude": ["report"]},
                {"systems": [{"system": "x"}],
                 "analysis": {"trajectory": "t.dcd", "topology": "t.pdb"}}):
            with self.subTest(config=config):
                state = self.load(config)
                self.assertEqual(state["include"], list(state["phases"]))

    def test_the_browser_ticks_from_include(self):
        # Which is why both fields have to say the same thing.
        import pathlib

        import fastmdxplora.gui as gui

        script = (pathlib.Path(gui.__file__).parent / "static"
                  / "run-builder.js").read_text(encoding="utf-8")
        self.assertIn("state.phases = new Set(from.include)", script)

    def test_a_phase_with_no_block_still_runs_with_defaults(self):
        state = self.load({"systems": [{"system": "1UAO"}],
                           "simulation": {"duration_ns": 2}})
        self.assertEqual(state["phases"]["setup"], {})
        self.assertEqual(state["phases"]["simulation"], {"duration_ns": 2})

    def test_include_is_honoured(self):
        state = self.load({"systems": [{"system": "1UAO"}],
                           "include": ["setup", "simulation"]})
        self.assertEqual(list(state["phases"]), ["setup", "simulation"])

    def test_exclude_is_honoured(self):
        state = self.load({"systems": [{"system": "1UAO"}],
                           "exclude": ["report"]})
        self.assertNotIn("report", state["phases"])
        self.assertIn("setup", state["phases"])

    def test_a_trajectory_study_does_not_tick_setup(self):
        # Setup and simulation have nothing to do with a trajectory that
        # already exists, even when nothing excludes them.
        state = self.load({"systems": [{"system": "x"}],
                           "analysis": {"trajectory": "t.dcd",
                                        "topology": "t.pdb"}})
        self.assertNotIn("setup", state["phases"])
        self.assertNotIn("simulation", state["phases"])
        self.assertIn("analysis", state["phases"])


class TestARaceWithACorrectOutcomeIsNotAnError(unittest.TestCase):
    """Two dashboard polls build the playback at once; one wins the rename.

    Seen in the browser as "dashboard route failed: No such file or
    directory: playback.pdb.tmp". The second request's `.tmp` was the one
    the first had just renamed into place, so its own rename found nothing
    -- while the destination sat there, complete and correct. An error for
    a race whose outcome was right.
    """

    def test_the_losing_request_yields_when_the_file_is_there(self):
        import tempfile
        from pathlib import Path

        from fastmdxplora.gui.trajectory_playback import _replace_or_yield

        root = Path(tempfile.mkdtemp())
        destination = root / "playback.pdb"
        destination.write_text("the other request's work")
        _replace_or_yield(root / "playback.pdb.tmp", destination)
        self.assertEqual(destination.read_text(),
                         "the other request's work")

    def test_a_genuine_miss_still_raises(self):
        # No tmp and no destination is not a race, it is a failure, and
        # swallowing it would hide the next real bug behind this fix.
        import tempfile
        from pathlib import Path

        from fastmdxplora.gui.trajectory_playback import _replace_or_yield

        root = Path(tempfile.mkdtemp())
        with self.assertRaises(FileNotFoundError):
            _replace_or_yield(root / "x.tmp", root / "x")

    def test_the_companion_pdb_makes_its_directory(self):
        # `_atomic_text` did; the PDB writer beside it did not, and a
        # playback built for a run whose simulation/ did not yet exist
        # failed on the write rather than the rename.
        import inspect

        from fastmdxplora.gui import trajectory_playback

        source = inspect.getsource(trajectory_playback)
        writer = source[source.index('tmp = companion_pdb.with_suffix'):]
        before = source[:source.index('tmp = companion_pdb.with_suffix')]
        self.assertIn("companion_pdb.parent.mkdir", before[-200:])
        self.assertIn("_replace_or_yield(tmp, companion_pdb)", writer[:200])
