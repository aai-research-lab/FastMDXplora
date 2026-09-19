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
        """Records what the endpoint hands to launch_from_config.

        Two earlier shapes of this stub each hid a bug: one read a key the
        real function never read; one read form state, which the endpoint
        then stopped sending. The endpoint hands over the config itself
        now -- one source of truth -- and this records exactly that.
        """

        def __init__(self):
            self.started = None

        def launch_from_config(self, state, dashboard_url=None, config=None):
            # The endpoint launches the config itself now -- one source of
            # truth -- so what arrives is the config, and `state` is None.
            self.started = dict(config) if config is not None else state
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
            {"config": {"agent": "autonomous", "systems": [{"system": "1UAO"}]}})
        self.assertEqual(answer["code"], "environment.budget.absent")
        self.assertIsNone(runtime.started, "it started anyway")

    def test_a_budget_of_zero_is_not_a_budget(self):
        # For autonomous, where one is required.
        for value in (0, "0", "", None, "not a number"):
            with self.subTest(budget=value):
                answer, runtime = self.start(
                    {"config": {"agent": "autonomous", "systems": [{"system": "1UAO"}]},
                     "budget_hours": value})
                self.assertFalse(answer["ok"])
                self.assertIsNone(runtime.started)

    def test_autonomous_with_a_budget_runs_and_carries_it(self):
        # On the config, so it reaches resolved_config.yml and the
        # manifest rather than living only in the request that started it.
        answer, runtime = self.start(
            {"config": {"agent": "autonomous", "systems": [{"system": "1UAO"}]},
             "budget_hours": 40})
        self.assertTrue(answer["ok"])
        self.assertEqual(runtime.started["budget_hours"], 40.0)

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
                    {"config": {"agent": mode, "systems": [{"system": "1UAO"}]}})
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
                    {"config": {"agent": mode, "systems": [{"system": "1UAO"}]},
                     "budget_hours": 40})
                self.assertTrue(answer["ok"])
                self.assertEqual(
                    runtime.started["budget_hours"], 40.0)

    def test_a_budget_is_not_invented_where_none_was_given(self):
        # Absent means absent. A default ceiling would be a number nobody
        # chose deciding when somebody's study stops.
        answer, runtime = self.start(
            {"config": {"agent": "assisted", "systems": [{"system": "1UAO"}]}})
        self.assertNotIn("budget_hours", runtime.started)

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
                    page.index("</section>", page.index('data-page="agent"'))]

    def dialog(self):
        # At body level, not inside the Agent section: a fixed overlay
        # that inherited the section's `hidden` could only open from the
        # Agent page, and the settings popup wanted to open it from any.
        page = self.page()
        start = page.index('id="agent-settings"')
        return page[start:page.index('id="settings-popup"', start)]

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
        settings = self.dialog()
        self.assertIn('role="dialog"', settings)
        self.assertIn("hidden", settings[:80])

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
        # The builder's own set, under the Agent: there is no need to go
        # to the builder from the Agent unless you want to change the
        # config, and that is a link, not the way out.
        panel = self.panel()
        # The send control is an arrow inside the box with a title, as every
        # assistant places it, rather than a labelled button beside it.
        self.assertIn('id="agent-propose"', panel)
        self.assertIn('title="Send (Enter). Shift+Enter for a new line."', panel)
        for action in ("Show the config", "Download config",
                       "Copy the command", "Download a script", "Run here",
                       "Write every setting"):
            with self.subTest(action=action):
                self.assertIn(action, panel)
        self.assertIn('id="agent-load"', panel)
        # "Draft" is not a word this page uses.
        self.assertNotIn("draft", panel.lower())

    def test_the_actions_are_the_builders_own(self):
        """One derivation, two doors.

        The file, the command and the script come from the builder's
        exported actions, reading the builder's state -- which the panel
        loads silently from the config it just wrote. So what the Agent
        hands over is exactly what the builder would, rather than a
        second rendering that could drift. The first version wrote the
        YAML from the browser without a round trip; that matched the
        screen but not the builder.
        """
        script = self.script()
        self.assertIn('post("/api/load-config", { config: data.config })', script)
        for action in ("download", "copyCommand", "downloadScript"):
            with self.subTest(action=action):
                self.assertIn(f'viaBuilder("{action}")', script)
        self.assertIn("window.FastMDXRun.fetchConfig()", script)
        import pathlib

        import fastmdxplora.gui as gui

        builder = (pathlib.Path(gui.__file__).parent / "static"
                   / "run-builder.js").read_text(encoding="utf-8")
        self.assertIn("fetchConfig, download, copyCommand, downloadScript,", builder)

    def test_run_here_still_goes_through_the_agents_door(self):
        # It checks the budget an autonomous run needs and records the
        # mode, which the builder's start does not.
        script = self.script()
        run = script[script.index("runBtn.onclick = function () {"):]
        run = run[:run.index("};", run.index("post("))]
        self.assertIn('post("/api/agent/run"', run)

    def test_opening_the_builder_is_a_link_not_the_way_out(self):
        panel = self.panel()
        load = panel[panel.index('id="agent-load"') - 40:panel.index('id="agent-load"') + 80]
        self.assertIn("<a href=", load)
        self.assertIn("to change it first", panel)

    def test_the_dialog_is_not_a_file_picker(self):
        # It borrows the shape and not the class: there is exactly one file
        # picker on the page, which is a reasonable thing to assert, and
        # this is not it.
        dialog = self.dialog()
        self.assertIn("agent-dialog", dialog)
        self.assertNotIn("analyse-picker", dialog)


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
                    page.index("</section>", page.index('data-page="agent"'))]

    def dialog(self):
        import pathlib

        import fastmdxplora.gui as gui

        page = (pathlib.Path(gui.__file__).parent / "templates"
                / "dashboard.html").read_text(encoding="utf-8")
        start = page.index('id="agent-settings"')
        return page[start:page.index('id="settings-popup"', start)]

    def test_only_the_study_is_on_the_page(self):
        panel = self.panel()
        self.assertIn('id="agent-request"', panel)
        self.assertIn('id="agent-propose"', panel)
        self.assertNotIn('id="agent-settings"', panel)

    def test_mode_and_the_ceiling_are_behind_settings(self):
        dialog = self.dialog()
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



class TestAConfigRemembersWhoWroteIt(unittest.TestCase):
    """The header names the author the config records.

    "Written by the FastMDXplora GUI" appeared on a study the Agent drafted,
    because build_config loaded `agent` and `agent_model` into the form
    state and then wrote none of it back. Opening an Agent-drafted config
    in the builder and saving it produced a file claiming a person wrote
    it -- the one thing those two fields exist to record.
    """

    def yaml_for(self, config):
        from fastmdxplora.gui.config_builder import config_yaml, state_from_config

        return config_yaml(state_from_config(config)["state"])["yaml"]

    def test_an_agent_drafted_config_says_so(self):
        text = self.yaml_for({"systems": [{"system": "1UAO"}],
                              "agent": "assisted",
                              "agent_model": "anthropic/claude-opus-5"})
        self.assertIn("Written by the FastMDXplora Agent", text)
        self.assertIn("agent: assisted", text)
        self.assertIn("agent_model: anthropic/claude-opus-5", text)

    def test_a_hand_written_config_says_the_gui(self):
        text = self.yaml_for({"systems": [{"system": "1UAO"}]})
        self.assertIn("Written by the FastMDXplora GUI", text)
        self.assertNotIn("agent:", text)

    def test_the_default_output_is_timestamped(self):
        # A fixed name collided on the second run and sent somebody off to
        # choose a folder for a study they had already described.
        import pathlib

        import fastmdxplora.gui as gui

        script = (pathlib.Path(gui.__file__).parent / "static"
                  / "run-builder.js").read_text(encoding="utf-8")
        self.assertIn("function defaultOutput()", script)
        # The browser does not name the folder; the server does, by one rule.
        self.assertNotIn('"fastmdxplora_output_"', script)
        self.assertIn('output: el("run-output").value.trim(),', script)


class TestTheAgentsButtonsBehaveLikeTheBuilders(unittest.TestCase):

    def script(self):
        import pathlib

        import fastmdxplora.gui as gui

        return (pathlib.Path(gui.__file__).parent / "static"
                / "agent-panel.js").read_text(encoding="utf-8")

    def page(self):
        import pathlib

        import fastmdxplora.gui as gui

        return (pathlib.Path(gui.__file__).parent / "templates"
                / "dashboard.html").read_text(encoding="utf-8")

    def test_show_becomes_hide(self):
        script = self.script()
        self.assertIn('showBtn.textContent = "Hide the config";', script)
        self.assertIn('showBtn.textContent = "Show the config";', script)

    def test_the_two_actions_carry_the_builders_descriptions(self):
        page = self.page()
        agent = page[page.index('id="agent-copy-command"'):page.index('id="agent-run"')]
        self.assertIn("The fastmdx invocation that runs this study", agent)
        self.assertIn("The same study as a Python script", agent)

    def test_a_note_lands_beside_the_button_not_above_the_config(self):
        # "Command copied" three lines above the config, where the
        # attempts are listed, was read as not having happened.
        page = self.page()
        self.assertIn('id="agent-action-note"', page)
        script = self.script()
        self.assertIn('noteEl.textContent = said ? said.textContent : "";', script)
        self.assertNotIn("note(box, said.textContent", script)

    def test_downloads_ask_where(self):
        import pathlib

        import fastmdxplora.gui as gui

        builder = (pathlib.Path(gui.__file__).parent / "static"
                   / "run-builder.js").read_text(encoding="utf-8")
        self.assertIn("async function saveAs(text, name, type)", builder)
        self.assertIn("window.showSaveFilePicker", builder)
        self.assertIn('saveAs(built.yaml, "fastmdxplora_config.yml"', builder)
        self.assertIn('saveAs(built.script, "fastmdxplora_study.py"', builder)

    def test_the_empty_state_no_longer_names_a_setting_that_is_on(self):
        import pathlib

        import fastmdxplora.gui as gui

        dash = (pathlib.Path(gui.__file__).parent / "static"
                / "dashboard.js").read_text(encoding="utf-8")
        self.assertNotIn("live_telemetry: true", dash)
        self.assertIn("Waiting for the simulation", dash)
        self.assertIn("Nothing running", dash)
        from fastmdxplora.config.schema import PHASE_SCHEMAS

        field = next(f for f in PHASE_SCHEMAS["simulation"].fields if f.name == "live_telemetry")
        self.assertTrue(field.default, "the message said to turn on something that is on")


class TestTheAgentIsAConversation(unittest.TestCase):
    """What you said, what came back, what it wrote, scrolling up; a
    composer pinned at the foot. A textarea on a page with a button under
    it was a form."""

    def page(self):
        import pathlib

        import fastmdxplora.gui as gui

        return (pathlib.Path(gui.__file__).parent / "templates"
                / "dashboard.html").read_text(encoding="utf-8")

    def script(self):
        import pathlib

        import fastmdxplora.gui as gui

        return (pathlib.Path(gui.__file__).parent / "static"
                / "agent-panel.js").read_text(encoding="utf-8")

    def test_a_thread_a_composer_and_a_template_for_each_reply(self):
        page = self.page()
        agent = page[page.index('data-page="agent"'):page.index("</section>", page.index('data-page="agent"'))]
        self.assertIn('id="agent-thread"', agent)
        self.assertIn('class="agent-composer"', agent)
        self.assertIn('<template id="agent-reply-template">', agent)

    def test_enter_sends_and_shift_enter_breaks(self):
        script = self.script()
        self.assertIn('e.key === "Enter" && !e.shiftKey', script)
        self.assertIn("e.preventDefault();\n        draft();", script)

    def test_the_composer_grows(self):
        script = self.script()
        self.assertIn("function autosize(area)", script)
        self.assertIn('area.addEventListener("input"', script)

    def test_each_reply_has_its_own_controls(self):
        # Two replies on one page cannot share an id. The template's ids
        # are stripped on clone and each part is found by data-role.
        script = self.script()
        self.assertIn('n.removeAttribute("id")', script)
        self.assertIn("node.querySelector('[data-role=\"' + role + '\"]')", script)

    def test_a_question_carries_the_request_into_the_answer(self):
        # The loop is stateless. "simulate chignolin for 2 ns" then "1UAO"
        # goes back as one request the loop can write a study from.
        script = self.script()
        self.assertIn("var request = pending ? pending + \"\\n\" + typed : typed;", script)
        self.assertIn("pending = request;", script)

    def test_run_here_runs_once(self):
        # A second press started it again into the same folder and was
        # refused for the folder being occupied -- the right refusal for
        # the wrong reason.
        script = self.script()
        self.assertIn('runBtn.textContent = "Running";', script)

    def test_the_servers_default_output_is_timestamped(self):
        import inspect

        from fastmdxplora.gui import exploration

        source = inspect.getsource(exploration.DashboardRuntime.launch_from_config)
        self.assertIn("default_output_name(system_of(dict(source)))", source)
        self.assertNotIn('requested = "analysis_output"', source)


class TestRunHereActuallyRuns(unittest.TestCase):
    """The button said Running and nothing ran.

    launch_from_config takes the builder's form state and builds a config
    from it. The endpoint handed it {"config": ...}, a key nothing reads,
    and it built an empty config: the study started, the CLI refused it
    for naming no system, and the button said Running. The tests passed
    because their stub runtime echoed the "config" key back -- they tested
    the assumption, not the function. These use the real runtime.
    """

    def runtime(self):
        import tempfile
        from pathlib import Path

        from fastmdxplora.gui.exploration import DashboardRuntime

        root = Path(tempfile.mkdtemp())
        return DashboardRuntime(root / "watch", root / "explore")

    def test_the_written_config_carries_the_study(self):
        from pathlib import Path

        from fastmdxplora.gui.agent_panel import run_endpoint

        answer = run_endpoint({"config": {
            "systems": [{"system": "1UAO"}],
            "simulation": {"duration_ns": 2},
            "agent": "assisted", "agent_model": "anthropic/x",
        }}, self.runtime())
        self.assertTrue(answer["ok"], answer.get("error"))
        written = Path(answer["config_path"]).read_text(encoding="utf-8")
        self.assertIn("systems:", written)
        self.assertIn("system: 1UAO", written)
        self.assertIn("agent: assisted", written)
        self.assertNotIn("\n{}\n", written)

    def test_the_budget_reaches_the_file_as_a_number(self):
        from pathlib import Path

        from fastmdxplora.gui.agent_panel import run_endpoint

        answer = run_endpoint({"config": {
            "systems": [{"system": "1UAO"}], "agent": "autonomous",
        }, "budget_hours": "40"}, self.runtime())
        self.assertTrue(answer["ok"], answer.get("error"))
        written = Path(answer["config_path"]).read_text(encoding="utf-8")
        self.assertIn("budget_hours: 40", written)

    def test_the_default_output_is_timestamped(self):
        from fastmdxplora.gui.agent_panel import run_endpoint

        answer = run_endpoint({"config": {"systems": [{"system": "1UAO"}]}},
                              self.runtime())
        self.assertTrue(answer["ok"])
        import pathlib

        self.assertRegex(pathlib.Path(answer["output"]).name, r"fastmdxplora_1UAO_study_\d{14}")


class TestTheBudgetIsAConfigKey(unittest.TestCase):
    """The CLI's `agent --autonomous` applied the budget in-process; a
    config the GUI hands to `explore --config` had no way to carry it. It
    is a top-level key now, with a floor, read by explore -- one reader
    for the ceiling, whichever door the study came through."""

    def test_it_validates_with_a_floor(self):
        from fastmdxplora.config.loader import ConfigError, validate_config

        validate_config({"systems": [{"system": "1UAO"}], "budget_hours": 40})
        with self.assertRaises(ConfigError):
            validate_config({"systems": [{"system": "1UAO"}], "budget_hours": -1})

    def test_it_is_carried_with_the_other_study_keys(self):
        from fastmdxplora.config.loader import STUDY_LEVEL_KEYS

        self.assertIn("budget_hours", STUDY_LEVEL_KEYS)

    def test_explore_routes_a_budgeted_config_through_the_staged_runner(self):
        import inspect

        from fastmdxplora.cli import main as cli

        module = inspect.getmodule(cli)
        source = inspect.getsource(module._cmd_explore)
        self.assertIn('budget = config.get("budget_hours")', source)
        self.assertIn("run_in_stages(config, output, budget_hours=float(budget))", source)

    def test_run_here_is_the_builders_primary_button(self):
        import pathlib

        import fastmdxplora.gui as gui

        page = (pathlib.Path(gui.__file__).parent / "templates"
                / "dashboard.html").read_text(encoding="utf-8")
        self.assertIn('<button class="primary-btn" type="button" data-role="run" id="agent-run">', page)


class TestTheThreadFollowsTheReply(unittest.TestCase):
    """The thread scrolls to the newest message after a reply.

    Three attempts. Setting scrollTop on the thread assumed the thread was
    the scroller; scrollIntoView after one frame ran before the reply had
    finished laying out -- the actions row and the config land after the
    first paint. Now both the thread and the column are scrolled, after
    the paint and again once layout has settled.
    """

    def script(self):
        import pathlib

        import fastmdxplora.gui as gui

        return (pathlib.Path(gui.__file__).parent / "static"
                / "agent-panel.js").read_text(encoding="utf-8")

    def test_both_containers_are_scrolled(self):
        script = self.script()
        block = script[script.index("function scrollToEnd()"):script.index("function autosize")]
        self.assertIn("thread.scrollTop = thread.scrollHeight;", block)
        self.assertIn("column.scrollTop = column.scrollHeight;", block)

    def test_after_the_paint_and_again_after_layout(self):
        script = self.script()
        block = script[script.index("function scrollToEnd()"):script.index("function autosize")]
        self.assertIn("requestAnimationFrame(toEnd);", block)
        self.assertIn("setTimeout(toEnd, 400);", block)

    def test_the_page_has_a_height_so_the_thread_can_scroll(self):
        import pathlib

        import fastmdxplora.gui as gui

        css = (pathlib.Path(gui.__file__).parent / "static"
               / "dashboard.css").read_text(encoding="utf-8")
        self.assertIn('.page[data-page="agent"] { display: flex; flex-direction: column; height: calc(100vh - 88px); min-height: 0; }', css)


class TestAMessageCanBeCopiedEditedAndRetried(unittest.TestCase):

    def script(self):
        import pathlib

        import fastmdxplora.gui as gui

        return (pathlib.Path(gui.__file__).parent / "static"
                / "agent-panel.js").read_text(encoding="utf-8")

    def test_a_user_message_has_all_three(self):
        script = self.script()
        say = script[script.index("function say(text, attached)"):script.index("function reply()")]
        for label in ('"Copy"', '"Edit"', '"Retry"'):
            with self.subTest(label=label):
                self.assertIn(label, say)

    def test_a_reply_can_be_copied(self):
        script = self.script()
        reply = script[script.index("function reply()"):script.index("function scrollToEnd")]
        self.assertIn('"Copy"', reply)

    def test_edit_and_retry_cut_the_thread_from_there(self):
        # Everything after the edited message goes, in the thread and in
        # the history the Agent sees, so the conversation continues from
        # that point rather than with a fork in it.
        script = self.script()
        self.assertIn("function cutFrom(msg)", script)
        cut = script[script.index("function cutFrom(msg)"):script.index("function say(text, attached)")]
        self.assertIn("history.length = i;", cut)
        self.assertIn("currentConfig = null;", cut)


class TestTheLaunchedConfigIsTheWrittenConfig(unittest.TestCase):
    """One source of truth: the Agent's config is the config that launches.

    It used to be translated into the builder's form state first, and the
    translation and the builder disagreed about where phase settings lived
    -- so every Agent-launched run ran with defaults. A 5 ns study ran for
    the default; a setup_from redirect was dropped and production looked
    in its own empty setup/. The first fix taught build_config to read
    both shapes, which was a patch over two sources. Now there is one
    renderer, render_config, that takes a config; the form reaches it
    through build_config, the Agent reaches it directly, and no form state
    stands between the Agent's config and the file the run reads.
    """

    def test_render_config_is_the_one_renderer(self):
        import inspect

        from fastmdxplora.gui import config_builder, run_from_config

        self.assertIn("return render_config(build_config(state, full=full)",
                      inspect.getsource(config_builder.config_yaml))
        self.assertIn("built = render_config(dict(config)",
                      inspect.getsource(run_from_config.prepare_run))

    def test_the_agent_does_not_go_through_form_state(self):
        import inspect

        from fastmdxplora.gui import agent_panel

        source = inspect.getsource(agent_panel.run_endpoint)
        self.assertNotIn("state_from_config", source)
        self.assertIn("launch_from_config(None, config=config", source)

    def test_build_config_reads_one_shape(self):
        # The both-shapes patch is gone: the browser's flat shape is the
        # only form state build_config ever sees.
        import inspect

        from fastmdxplora.gui import config_builder

        source = inspect.getsource(config_builder.build_config)
        self.assertNotIn('nested = state.get("phases")', source)

    def test_the_file_the_run_reads_carries_every_field(self):
        import tempfile
        from pathlib import Path

        from fastmdxplora.gui.agent_panel import run_endpoint
        from fastmdxplora.gui.exploration import DashboardRuntime

        root = Path(tempfile.mkdtemp())
        runtime = DashboardRuntime(root / "w", root / "e")
        answer = run_endpoint({"config": {
            "systems": [{"system": "1UAO"}],
            "simulation": {"duration_ns": 5, "setup_from": "/runs/first"},
            "setup": {"solvent_padding_nm": 1.5},
            "exclude": ["setup"],
            "agent": "assisted", "agent_model": "anthropic/x",
        }}, runtime)
        self.assertTrue(answer["ok"], answer.get("error"))
        written = Path(answer["config_path"]).read_text(encoding="utf-8")
        for line in ("duration_ns: 5", "setup_from: /runs/first",
                     "solvent_padding_nm: 1.5", "- setup", "agent: assisted",
                     "Written by the FastMDXplora Agent"):
            with self.subTest(line=line):
                self.assertIn(line, written)


class TestTheMessageToolsAndTheScrollbar(unittest.TestCase):

    def css(self):
        import pathlib

        import fastmdxplora.gui as gui

        return (pathlib.Path(gui.__file__).parent / "static"
                / "dashboard.css").read_text(encoding="utf-8")

    def test_the_tools_sit_below_in_fixed_widths(self):
        # Floated over the corner, the bar was wider than a short message
        # and hung off it.
        css = self.css()
        rule = css[css.index(".agent-msg-tools {"):]
        rule = rule[:rule.index("}")]
        self.assertNotIn("position: absolute", rule)
        self.assertIn("margin-top: 4px", rule)
        self.assertIn(".agent-msg-tools button { width: 40px;", css)

    def test_the_scrollbar_has_its_own_gutter(self):
        # It was painting over the messages.
        css = self.css()
        rule = css[css.index(".agent-thread {"):]
        rule = rule[:rule.index("}")]
        self.assertIn("scrollbar-gutter: stable", rule)
        self.assertIn("padding: 8px 16px 24px 0", rule)


class TestTheLayoutAndTheVoice(unittest.TestCase):

    def css(self):
        import pathlib

        import fastmdxplora.gui as gui

        return (pathlib.Path(gui.__file__).parent / "static"
                / "dashboard.css").read_text(encoding="utf-8")

    def test_the_centre_has_a_reading_width_except_the_viewer(self):
        css = self.css()
        self.assertIn(".page-shell { max-width: 900px; margin: 0 auto; width: 100%; }", css)
        self.assertIn('html[data-page="viewer"] .page-shell { max-width: none; }', css)

    def test_the_panel_starts_wide(self):
        import pathlib

        import fastmdxplora.gui as gui

        css = self.css()
        self.assertIn("--panel-width: 560px;", css)
        frame = (pathlib.Path(gui.__file__).parent / "static"
                 / "frame.js").read_text(encoding="utf-8")
        self.assertIn('store.get("panelWidth", "560")', frame)
        self.assertIn("panel: [280, 640]", frame)

    def test_the_agents_prose_is_a_serif_and_the_persons_is_not(self):
        css = self.css()
        self.assertIn(".agent-answer, .agent-msg-agent .agent-attempt {\n    font-family: Georgia", css)
        # No hosted font: this GUI runs without a route to the internet.
        self.assertNotIn("googleapis", css)

    def test_the_agent_is_told_to_write_plainly(self):
        from fastmdxplora.agent.propose import prompt_for

        prompt = prompt_for("x")
        self.assertIn("No em dashes and no en dashes", prompt)
        self.assertIn('No "I\'d be happy to", no "great question"', prompt)
        self.assertIn("Say the thing and stop.", prompt)


class TestTheAgentsProseAndTheSidebar(unittest.TestCase):

    def css(self):
        import pathlib

        import fastmdxplora.gui as gui

        return (pathlib.Path(gui.__file__).parent / "static"
                / "dashboard.css").read_text(encoding="utf-8")

    def script(self):
        import pathlib

        import fastmdxplora.gui as gui

        return (pathlib.Path(gui.__file__).parent / "static"
                / "agent-panel.js").read_text(encoding="utf-8")

    def test_the_prose_is_normal_weight_in_the_secondary_colour(self):
        # In the primary colour a serif at this size read as bold across
        # the whole reply, and left nowhere for emphasis to go.
        css = self.css()
        rule = css[css.index(".agent-answer, .agent-msg-agent .agent-attempt {"):]
        rule = rule[:rule.index("}")]
        self.assertIn("font-weight: 400", rule)
        self.assertIn("color: var(--text-secondary)", rule)
        self.assertIn(".agent-answer strong { color: var(--text-primary); font-weight: 600; }", css)

    def test_a_reply_renders_only_four_kinds_of_emphasis_and_escapes_the_rest(self):
        script = self.script()
        self.assertIn("function prose(text)", script)
        block = script[script.index("function prose(text)"):script.index("function note(box")]
        self.assertIn('replace(/&/g, "&amp;")', block)
        for tag in ("<code>", "<strong>", "<em>", "rel=\"noopener\""):
            with self.subTest(tag=tag):
                self.assertIn(tag, block)
        self.assertIn("p.innerHTML = prose(data.answer);", script)

    def test_the_agent_is_told_where_emphasis_belongs(self):
        from fastmdxplora.agent.propose import prompt_for

        prompt = prompt_for("x")
        self.assertIn("**bold** for the one thing to", prompt)
        self.assertIn("No headings, no bullet lists in an answer.", prompt)

    def test_the_sidebar_shows_the_study_not_its_folder(self):
        import pathlib

        import fastmdxplora.gui as gui

        css = self.css()
        self.assertIn(".study-path, .sidebar-study .study-label { display: none; }", css)
        page = (pathlib.Path(gui.__file__).parent / "templates"
                / "dashboard.html").read_text(encoding="utf-8")
        # The elements stay, hidden, because the JS writes to them.
        self.assertEqual(page.count('id="sidebar-output-folder"'), 1)
        self.assertEqual(page.count('id="topbar-run-id"'), 1)

    def test_output_is_one_button(self):
        # Four buttons in a 232px sidebar was too many. Output opens the
        # folder and copies the path.
        import pathlib

        import fastmdxplora.gui as gui

        page = (pathlib.Path(gui.__file__).parent / "templates"
                / "dashboard.html").read_text(encoding="utf-8")
        self.assertNotIn('id="copy-output-path"', page)
        self.assertIn('title="Open the output folder, and copy its path"', page)

    def test_the_follow_toggle_says_what_it_does(self):
        import pathlib

        import fastmdxplora.gui as gui

        page = (pathlib.Path(gui.__file__).parent / "templates"
                / "dashboard.html").read_text(encoding="utf-8")
        self.assertIn("scroll to newest", page)
        self.assertIn("Keep the newest line in view as the run writes", page)


class TestEditIsInPlace(unittest.TestCase):

    def test_the_bubble_becomes_editable(self):
        import pathlib

        import fastmdxplora.gui as gui

        script = (pathlib.Path(gui.__file__).parent / "static"
                  / "agent-panel.js").read_text(encoding="utf-8")
        edit = script[script.index('label: "Edit"'):script.index('label: "Retry"')]
        self.assertIn('body.contentEditable = "true";', edit)
        # Enter sends; Escape restores; blur restores.
        self.assertIn('e.key === "Enter" && !e.shiftKey', edit)
        self.assertIn('e.key === "Escape"', edit)
        self.assertIn("body.textContent = before;", edit)
        # Not the old detour through the composer.
        self.assertNotIn("Put this back in the composer", edit)


class TestSixMoreFromUsingIt(unittest.TestCase):

    def test_the_agent_sees_the_step_and_the_time_left(self):
        # It said "I do not have a step count or an elapsed time" while the
        # sidebar read 334,000 of 350,000 and three minutes left.
        import json
        import tempfile
        from pathlib import Path

        from fastmdxplora.gui.agent_panel import _run_status

        root = Path(tempfile.mkdtemp())
        (root / "simulation").mkdir()
        (root / "simulation" / "live_status.json").write_text(json.dumps({
            "stage": "production", "current_step": 334000,
            "total_planned_steps": 350000, "elapsed_wall_time_s": 4000,
            "simulation_time_completed_ns": 0.668}), encoding="utf-8")

        class Runtime:
            active_root = root

            def snapshot(self):
                return {"active_run": str(root), "status": "running"}

        status = _run_status(Runtime())
        self.assertIn("step: 334,000 of 350,000 (95.4% complete)", status)
        self.assertIn("left", status)
        self.assertIn("simulated so far, equilibration included: 0.668 ns", status)

    def test_thinking_not_writing(self):
        import pathlib

        import fastmdxplora.gui as gui

        script = (pathlib.Path(gui.__file__).parent / "static"
                  / "agent-panel.js").read_text(encoding="utf-8")
        self.assertIn('note(box, "Thinking\\u2026");', script)
        self.assertNotIn('"Writing\\u2026"', script)

    def test_send_is_inside_the_box(self):
        import pathlib

        import fastmdxplora.gui as gui

        page = (pathlib.Path(gui.__file__).parent / "templates"
                / "dashboard.html").read_text(encoding="utf-8")
        self.assertIn('<div class="agent-composer-box">', page)
        css = (pathlib.Path(gui.__file__).parent / "static"
               / "dashboard.css").read_text(encoding="utf-8")
        self.assertIn(".agent-composer-box .agent-send {\n    position: absolute; right: 8px; bottom: 8px;", css)

    def test_the_placeholder_is_as_general_as_the_agent(self):
        import pathlib

        import fastmdxplora.gui as gui

        page = (pathlib.Path(gui.__file__).parent / "templates"
                / "dashboard.html").read_text(encoding="utf-8")
        self.assertIn('placeholder="Describe a study, ask a question, or tell me what to do."', page)

    def test_a_page_opens_at_its_top(self):
        import pathlib

        import fastmdxplora.gui as gui

        script = (pathlib.Path(gui.__file__).parent / "static"
                  / "dashboard.js").read_text(encoding="utf-8")
        nav = script[script.index("function navigate(page, options)"):script.index("function startLoadingChecklist")]
        self.assertIn("column.scrollTop = 0;", nav)

    def test_one_sampling_bar(self):
        # convergence.py had its own, at five, while the report's prose said
        # ten. The Convergence table counted rg at 8.3 as adequately sampled
        # while the section above it said it was not.
        from fastmdxplora.report import convergence
        from fastmdxplora.statistics import MINIMUM_EFFECTIVE_SAMPLES

        self.assertEqual(convergence._ENOUGH_SAMPLES, MINIMUM_EFFECTIVE_SAMPLES)
        self.assertEqual(MINIMUM_EFFECTIVE_SAMPLES, 10.0)


class TestSixFromLaunchingIt(unittest.TestCase):

    def test_the_browser_opens_after_the_server_answers(self):
        # It was opened first, and on a completed-run folder reached the
        # port before it was listening: "unable to connect" until a refresh.
        import inspect

        from fastmdxplora.gui import server

        source = inspect.getsource(server.serve_dashboard)
        self.assertIn("on_ready", source)
        self.assertIn("urllib.request.urlopen(url", source)
        # The CLI hands serve_dashboard an on_ready rather than opening first.
        from fastmdxplora.cli import main as cli

        whole = inspect.getsource(inspect.getmodule(cli))
        self.assertIn("on_ready=on_ready", whole)

    def test_the_sidebar_collapse_leaves_the_centre(self):
        import pathlib

        import fastmdxplora.gui as gui

        css = (pathlib.Path(gui.__file__).parent / "static"
               / "dashboard.css").read_text(encoding="utf-8")
        # minmax(0, 1fr) for the centre when the sidebar is away: it takes
        # the room, it does not collapse with the sidebar.
        self.assertIn("body.sidebar-collapsed .app-shell {\n    grid-template-columns: 0 0 minmax(0, 1fr)", css)

    def test_the_widths_are_smaller(self):
        import pathlib

        import fastmdxplora.gui as gui

        css = (pathlib.Path(gui.__file__).parent / "static"
               / "dashboard.css").read_text(encoding="utf-8")
        self.assertIn("--panel-width: 560px;", css)
        self.assertIn(".page-shell { max-width: 900px;", css)
        frame = (pathlib.Path(gui.__file__).parent / "static"
                 / "frame.js").read_text(encoding="utf-8")
        self.assertIn("panel: [280, 640]", frame)

    def test_an_empty_report_parameter_is_dropped(self):
        # "state_csv: None" in the report said a file was not given, which
        # is noise, not a setting.
        import inspect

        from fastmdxplora.report import document

        source = inspect.getsource(document)
        self.assertIn("v is not None and v != \"\"", source)


class TestTheCentreStaysCentred(unittest.TestCase):
    """The centre column stays visible and centred whatever is folded.

    Collapsing the sidebar had pinned the page shell 24px from the left,
    so with both columns folded the content sat hard against the edge and
    read as gone. The shell centres in its column -- max-width and margin
    auto -- and the column is minmax(0, 1fr) in every collapse state, so
    it takes the freed width and the content sits in the middle of it.
    """

    def css(self):
        import pathlib

        import fastmdxplora.gui as gui

        return (pathlib.Path(gui.__file__).parent / "static"
                / "dashboard.css").read_text(encoding="utf-8")

    def test_no_left_pin_when_the_sidebar_folds(self):
        self.assertNotIn("margin-left: 24px", self.css())

    def test_the_shell_centres_in_its_column(self):
        self.assertIn(".page-shell { max-width: 900px; margin: 0 auto; width: 100%; }", self.css())

    def test_the_centre_track_is_a_fraction_in_every_state(self):
        css = self.css()
        for sel in ("body.panel-collapsed .app-shell {",
                    "body.sidebar-collapsed .app-shell {",
                    "body.sidebar-collapsed.panel-collapsed .app-shell {"):
            block = css[css.index(sel):css.index("}", css.index(sel))]
            with self.subTest(sel=sel):
                # The third track -- the centre -- is the fraction.
                self.assertIn("minmax(0, 1fr)", block)


class TestConversationsBelongToStudies(unittest.TestCase):
    """The model Claude's users know: a chat belongs to a project, and
    every chat in it sees the project's context. A study is the project.
    Conversations live inside the study folder so a copied study carries
    the conversations that made it; a conversation about no study lives
    at the workspace level; opening one from another study loads that
    study; a conversation that launches a run moves into the study it
    created."""

    def setUp(self):
        import json
        import tempfile
        from pathlib import Path

        self.ws = Path(tempfile.mkdtemp())

        def study(name, system):
            s = self.ws / name
            (s / "simulation").mkdir(parents=True)
            (s / "manifest.json").write_text(json.dumps({"system": {"system": system}}),
                                             encoding="utf-8")
            return s

        self.chig, self.trp = study("run_chig", "1UAO"), study("run_trp", "1L2Y")

        class Runtime:
            def __init__(rt, ws):
                rt.exploration_root = ws
                rt.active_root = None

            def switch_to(rt, path):
                rt.active_root = Path(path)
                return {"ok": True}

        self.rt = Runtime(self.ws)

    def test_no_study_loaded_means_the_workspace_scope(self):
        from fastmdxplora.gui.agent_panel import WORKSPACE_CONVERSATIONS_DIR, write_conversation

        write_conversation(self.rt, [{"role": "user", "text": "general question"}])
        self.assertTrue((self.ws / WORKSPACE_CONVERSATIONS_DIR).is_dir())

    def test_a_studys_conversations_live_inside_it(self):
        from fastmdxplora.gui.agent_panel import (CONVERSATIONS_SUBDIR, read_conversation,
                                                   write_conversation)

        self.rt.active_root = self.chig
        self.assertEqual(read_conversation(self.rt)["study_label"], "1UAO")
        write_conversation(self.rt, [{"role": "user", "text": "simulate chignolin"}])
        self.assertTrue((self.chig / CONVERSATIONS_SUBDIR).is_dir())
        # And not in the workspace's own store.
        from fastmdxplora.gui.agent_panel import WORKSPACE_CONVERSATIONS_DIR

        self.assertFalse((self.ws / WORKSPACE_CONVERSATIONS_DIR).exists())

    def test_loading_another_study_shows_its_own_thread(self):
        from fastmdxplora.gui.agent_panel import read_conversation, write_conversation

        self.rt.active_root = self.chig
        write_conversation(self.rt, [{"role": "user", "text": "about chignolin"}])
        self.rt.active_root = self.trp
        self.assertEqual(read_conversation(self.rt)["entries"], [])

    def test_a_launch_moves_the_whole_conversation_into_the_new_study(self):
        # The sequence a person takes: a thread in chignolin writes a
        # config; Run here launches trpcage, which switches the loaded
        # study BEFORE the move runs. The browser names the conversation
        # and where it was, so the move finds it. Without those the move
        # looked in the new study, found nothing, and the config exchange
        # was lost from the thread that landed there.
        from fastmdxplora.gui.agent_panel import (CONVERSATIONS_SUBDIR, attach_conversation,
                                                   read_conversation, write_conversation)

        self.rt.active_root = self.chig
        saved = write_conversation(self.rt, [
            {"role": "user", "text": "run trpcage with the same settings"},
            {"role": "agent", "kind": "config", "yaml": "x", "config": {}}])
        self.rt.active_root = self.trp  # the launch already switched
        moved = attach_conversation(self.rt, self.trp, saved["id"], saved["study"])
        self.assertTrue(moved["moved"])
        self.assertTrue((self.trp / CONVERSATIONS_SUBDIR / f"{moved['id']}.json").is_file())
        self.assertFalse((self.chig / CONVERSATIONS_SUBDIR / f"{moved['id']}.json").exists())
        kinds = [e.get("kind", "user") for e in read_conversation(self.rt)["entries"]]
        self.assertEqual(kinds, ["user", "config"])

    def test_the_old_lookup_would_have_moved_nothing(self):
        from fastmdxplora.gui.agent_panel import attach_conversation, write_conversation

        self.rt.active_root = self.chig
        write_conversation(self.rt, [{"role": "user", "text": "x"}])
        self.rt.active_root = self.trp
        self.assertFalse(attach_conversation(self.rt, self.trp)["moved"])

    def test_the_save_reports_its_scope(self):
        from fastmdxplora.gui.agent_panel import write_conversation

        self.rt.active_root = self.chig
        self.assertEqual(write_conversation(self.rt, [{"role": "user", "text": "x"}])["study"],
                         str(self.chig))

    def test_the_list_groups_by_study_loaded_first(self):
        from fastmdxplora.gui.agent_panel import list_conversations, write_conversation

        self.rt.active_root = self.chig
        write_conversation(self.rt, [{"role": "user", "text": "chig thread"}])
        self.rt.active_root = self.trp
        write_conversation(self.rt, [{"role": "user", "text": "trp thread"}])
        groups = list_conversations(self.rt)["groups"]
        self.assertEqual(groups[0]["label"], "1L2Y")
        self.assertTrue(groups[0]["loaded"])
        self.assertIn("1UAO", [g["label"] for g in groups])

    def test_opening_across_studies_loads_that_study(self):
        from fastmdxplora.gui.agent_panel import (open_conversation, read_conversation,
                                                   write_conversation)

        self.rt.active_root = self.chig
        write_conversation(self.rt, [{"role": "user", "text": "chig thread"}])
        cid = read_conversation(self.rt)["id"]
        self.rt.active_root = self.trp
        opened = open_conversation(self.rt, cid, str(self.chig))
        self.assertTrue(opened["ok"])
        self.assertTrue(opened["loaded_study"])
        self.assertEqual(self.rt.active_root, self.chig)
        self.assertEqual(opened["entries"][0]["text"], "chig thread")

    def test_bad_ids_and_studies_are_refused(self):
        from fastmdxplora.gui.agent_panel import delete_conversation, open_conversation

        self.assertFalse(open_conversation(self.rt, "../etc", str(self.chig))["ok"])
        self.assertFalse(open_conversation(self.rt, "conv-x", str(self.ws / "nope"))["ok"])
        self.assertFalse(delete_conversation(self.rt, "conv-nope", None)["ok"])

    def test_new_keeps_the_old_and_delete_is_one(self):
        from fastmdxplora.gui.agent_panel import (delete_conversation, list_conversations,
                                                   new_conversation, read_conversation,
                                                   write_conversation)

        self.rt.active_root = self.chig
        write_conversation(self.rt, [{"role": "user", "text": "first"}])
        first = read_conversation(self.rt)["id"]
        new_conversation(self.rt)
        write_conversation(self.rt, [{"role": "user", "text": "second"}])
        rows = list_conversations(self.rt)["groups"][0]["conversations"]
        self.assertEqual([r["title"] for r in rows][-1], "first")
        self.assertTrue(delete_conversation(self.rt, first, str(self.chig))["ok"])
        rows = list_conversations(self.rt)["groups"][0]["conversations"]
        self.assertEqual([r["title"] for r in rows], ["second"])

    def test_the_old_single_file_becomes_a_workspace_conversation(self):
        from fastmdxplora.gui.agent_panel import CONVERSATION_FILE, read_conversation

        (self.ws / CONVERSATION_FILE).write_text(
            '{"entries": [{"role": "user", "text": "from before"}]}', encoding="utf-8")
        self.assertEqual(read_conversation(self.rt)["entries"][0]["text"], "from before")
        self.assertFalse((self.ws / CONVERSATION_FILE).exists())

    def test_the_panel_groups_opens_across_and_moves_on_launch(self):
        import pathlib

        import fastmdxplora.gui as gui

        script = (pathlib.Path(gui.__file__).parent / "static"
                  / "agent-panel.js").read_text(encoding="utf-8")
        self.assertIn('post("/api/agent/conversation/open", { id: c.id, study: g.study })', script)
        self.assertIn("if (o.loaded_study && !g.loaded) {", script)
        self.assertIn('study: started.output, id: convId, from_study: fromStudy', script)
        # Saved before the launch, so the move carries the last exchange.
        run = script[script.index("runBtn.onclick = function () {"):]
        run = run[:run.index("\n  }\n", run.index("started.error"))]
        self.assertLess(run.index("persist().then("), run.index('post("/api/agent/run"'))
        self.assertIn("This conversation now belongs to the new study.", script)



class TestItHasAName(unittest.TestCase):

    def test_asked_who_it_is_it_says_the_fastmdxplora_agent(self):
        # It called itself "the assistant built into FastMDXplora". It has
        # a name, and it is not the model's or the vendor's.
        from fastmdxplora.agent.propose import prompt_for

        prompt = prompt_for("who are you?")
        self.assertIn("You are the FastMDXplora Agent.", prompt)
        # Asked the engine, it says: the one chosen in Settings, and names
        # it when the config shows it. Hiding it from the person who chose
        # it read as evasion, and the earlier "that is the whole answer"
        # came back three times in a row.
        self.assertIn("say it is the one chosen in Settings", prompt)
        self.assertIn("do not repeat a phrase across turns", prompt)
        self.assertNotIn("that is\nthe whole answer", prompt)


class TestTheAgentSeesTheRunsOwnConfig(unittest.TestCase):
    """"The same settings as the chignolin one" needs the chignolin
    config. The Agent lost it the moment it wrote a new one, and read
    "simulated so far: 0.7 ns" back as a production length of 0.7 when the
    config said 0.5. The active run's resolved config rides with the run
    status now, and the prompt says which number is which."""

    def test_the_resolved_config_rides_with_the_status(self):
        import tempfile
        from pathlib import Path

        from fastmdxplora.gui.agent_panel import _run_status

        root = Path(tempfile.mkdtemp())
        (root / "resolved_config.yml").write_text(
            "# header\nsystems:\n- system: 1UAO\nsimulation:\n  duration_ns: 0.5\n",
            encoding="utf-8")

        class Runtime:
            active_root = root

            def snapshot(self):
                return {"active_run": str(root), "status": "idle"}

        status = _run_status(Runtime())
        self.assertIn("the config this run used", status)
        self.assertIn("duration_ns: 0.5", status)
        self.assertNotIn("# header", status)

    def test_simulated_so_far_says_equilibration_included(self):
        import json
        import tempfile
        from pathlib import Path

        from fastmdxplora.gui.agent_panel import _run_status

        root = Path(tempfile.mkdtemp())
        (root / "simulation").mkdir()
        (root / "simulation" / "live_status.json").write_text(
            json.dumps({"stage": "production", "simulation_time_completed_ns": 0.7}),
            encoding="utf-8")

        class Runtime:
            active_root = root

            def snapshot(self):
                return {"active_run": str(root), "status": "running"}

        self.assertIn("equilibration included: 0.700 ns", _run_status(Runtime()))

    def test_the_prompt_says_where_the_same_settings_come_from(self):
        from fastmdxplora.agent.propose import prompt_for

        prompt = prompt_for("x")
        self.assertIn("the config the active run used", prompt)
        self.assertIn("is not\nthe production length", prompt)


class TestAStopIsRecordedWhenItHappens(unittest.TestCase):
    """A reloaded thread said "Did: stop" about a run that was only asked
    to stop and never confirmed, and the person's "yes" then went to the
    model as a new message. The ask is recorded as a question; the stop
    is recorded when it is confirmed; a reload restores the pending
    state if the ask was the last thing said."""

    def script(self):
        import pathlib

        import fastmdxplora.gui as gui

        return (pathlib.Path(gui.__file__).parent / "static"
                / "agent-panel.js").read_text(encoding="utf-8")

    def test_the_ask_is_a_question_not_an_action(self):
        script = self.script()
        block = script[script.index('if (data.action === "stop") {'):script.index("act(data.action, data.where")]
        self.assertIn('kind: "question"', block)
        self.assertIn("? Say yes.", block)

    def test_the_stop_is_recorded_on_confirmation(self):
        script = self.script()
        confirm = script[script.index("function confirmStop(typed, box)"):script.index("function wireActions")]
        self.assertIn('kind: "action", action: "stop"', confirm)
        self.assertIn('kind: "answer", text: "Not stopped."', confirm)

    def test_a_reload_keeps_a_pending_stop(self):
        script = self.script()
        replay = script[script.index("function replay(entries)"):script.index("document.addEventListener")]
        self.assertIn("stopPending = /^Stop the run.*\\? Say yes\\.$/.test(e.text", replay)
        self.assertIn("var last = entries[entries.length - 1];", replay)

    def test_replay_never_says_did_stop(self):
        script = self.script()
        self.assertIn('e.action === "stop" ? "Stopped the run." : "Did: " + e.action', script)


class TestAFileCanBeAttachedToAMessage(unittest.TestCase):
    """The + beside the composer. A file the person chose goes with that
    message as context: per message, explicit, and recorded by name, path,
    size and digest rather than by copying its bytes into the thread."""

    def setUp(self):
        import tempfile
        from pathlib import Path

        self.d = Path(tempfile.mkdtemp())

    def test_a_text_file_is_read_with_its_digest(self):
        from fastmdxplora.gui.agent_panel import read_attachment

        f = self.d / "setup_parameters.json"
        f.write_text('{"ligand_pose": "auto"}', encoding="utf-8")
        r = read_attachment(f)
        self.assertTrue(r["ok"])
        self.assertEqual(r["name"], "setup_parameters.json")
        self.assertEqual(r["size"], 23)
        self.assertEqual(len(r["sha256"]), 12)
        self.assertIn("ligand_pose", r["text"])
        self.assertFalse(r["truncated"])

    def test_a_binary_is_refused_with_a_sentence(self):
        from fastmdxplora.gui.agent_panel import read_attachment

        f = self.d / "traj.dcd"
        f.write_bytes(b"\\x00" * 64)
        r = read_attachment(f)
        self.assertFalse(r["ok"])
        self.assertIn("not a text file", r["error"])

    def test_a_long_log_keeps_its_head_and_tail(self):
        from fastmdxplora.gui.agent_panel import ATTACH_LIMIT_BYTES, read_attachment

        f = self.d / "exploration.log"
        f.write_text("start\n" + "x" * (ATTACH_LIMIT_BYTES + 50_000) + "\nERROR at the end\n",
                     encoding="utf-8")
        r = read_attachment(f)
        self.assertTrue(r["truncated"])
        self.assertTrue(r["text"].startswith("start"))
        self.assertTrue(r["text"].rstrip().endswith("ERROR at the end"))
        self.assertIn("not shown", r["text"])

    def test_missing_and_empty_are_refused(self):
        from fastmdxplora.gui.agent_panel import read_attachment

        self.assertFalse(read_attachment(self.d / "nope.yml")["ok"])
        self.assertFalse(read_attachment("")["ok"])

    def test_the_prompt_carries_the_file_under_its_name(self):
        from fastmdxplora.agent.propose import prompt_for

        prompt = prompt_for("why?", attachments=[
            {"name": "setup_parameters.json", "text": '{"ligand_pose": "auto"}', "truncated": True}])
        self.assertIn("## Files attached to this message", prompt)
        self.assertIn("### setup_parameters.json (head and tail; the middle was cut)", prompt)
        self.assertIn("ligand_pose", prompt)
        self.assertIn("A file attached to a message is there to be read", prompt)

    def test_the_endpoint_passes_attachments_through(self):
        import os
        import tempfile

        prior = os.environ.get("FASTMDXPLORA_CONFIG_DIR")
        os.environ["FASTMDXPLORA_CONFIG_DIR"] = tempfile.mkdtemp()
        self.addCleanup(lambda: (os.environ.__setitem__("FASTMDXPLORA_CONFIG_DIR", prior)
                                 if prior is not None
                                 else os.environ.pop("FASTMDXPLORA_CONFIG_DIR", None)))
        import fastmdxplora.agent as agent_mod
        from fastmdxplora.gui import agent_panel

        seen = {}
        before = agent_mod.completion_for
        agent_mod.completion_for = lambda *a, **k: (
            lambda prompt: (seen.__setitem__("prompt", prompt), "SAY: read it.")[1])
        try:
            agent_panel.propose_endpoint(
                {"request": "look", "attachments": [{"name": "a.log", "text": "the log says"}]}, None)
        finally:
            agent_mod.completion_for = before
        self.assertIn("### a.log", seen["prompt"])
        self.assertIn("the log says", seen["prompt"])

    def test_the_composer_has_the_plus_and_records_what_was_attached(self):
        import pathlib

        import fastmdxplora.gui as gui

        page = (pathlib.Path(gui.__file__).parent / "templates"
                / "dashboard.html").read_text(encoding="utf-8")
        self.assertIn('id="agent-attach"', page)
        self.assertIn('id="agent-attachments"', page)
        script = (pathlib.Path(gui.__file__).parent / "static"
                  / "agent-panel.js").read_text(encoding="utf-8")
        self.assertIn('post("/api/agent/attachment", { path: path })', script)
        # Recorded by name, path, size and digest; the bytes are not kept.
        self.assertIn("return { name: f.name, path: f.path, size: f.size, sha256: f.sha256, truncated: !!f.truncated };", script)
        self.assertIn('{ role: "user", text: typed, attachments: record }', script)
        # Sent with the text for the model.
        self.assertIn("attachments: files.map(function (f) { return { name: f.name, text: f.text, truncated: !!f.truncated }; })", script)


class TestThePickerServesTheAgentToo(unittest.TestCase):
    """The picker was built for the builder: find a trajectory or a
    structure and nothing else. Opened from the Agent to attach a log or
    a manifest, it showed folders and no way into them. It marks a study
    folder now, colours the three kinds, lists every readable file when
    no kind is asked for, and opens where the conversation lives."""

    def setUp(self):
        import tempfile
        from pathlib import Path

        self.ws = Path(tempfile.mkdtemp())
        self.study = self.ws / "fastmdxplora_1UAO_study_x"
        for d in ("setup", "simulation"):
            (self.study / d).mkdir(parents=True)
        (self.study / "manifest.json").write_text("{}", encoding="utf-8")
        (self.study / "exploration.log").write_text("x", encoding="utf-8")
        (self.study / "setup" / "system.pdb").write_text("ATOM", encoding="utf-8")
        (self.study / "setup" / "setup_parameters.json").write_text("{}", encoding="utf-8")
        (self.study / "simulation" / "production.dcd").write_bytes(b"0")

    def test_a_study_folder_is_marked(self):
        from fastmdxplora.gui.browse import browse

        entries = {e["name"]: e for e in browse(self.ws)["entries"]}
        self.assertTrue(entries["fastmdxplora_1UAO_study_x"]["study"])
        (self.ws / "notes").mkdir()
        entries = {e["name"]: e for e in browse(self.ws)["entries"]}
        self.assertFalse(entries["notes"]["study"])

    def test_no_kind_lists_what_the_agent_can_read(self):
        from fastmdxplora.gui.browse import browse

        self.assertEqual([f["name"] for f in browse(self.study)["files"]],
                         ["exploration.log", "manifest.json"])
        names = [f["name"] for f in browse(self.study / "setup")["files"]]
        self.assertEqual(names, ["setup_parameters.json", "system.pdb"])
        # A trajectory is not attachable and is not listed without a kind.
        self.assertEqual([f["name"] for f in browse(self.study / "simulation")["files"]], [])

    def test_a_kind_still_narrows_for_the_builder(self):
        from fastmdxplora.gui.browse import browse

        self.assertEqual([f["name"] for f in browse(self.study / "setup", kind="structure")["files"]],
                         ["system.pdb"])
        self.assertEqual([f["name"] for f in browse(self.study / "simulation", kind="trajectory")["files"]],
                         ["production.dcd"])

    def test_the_picker_colours_the_kinds_and_takes_a_start(self):
        import pathlib

        import fastmdxplora.gui as gui

        js = (pathlib.Path(gui.__file__).parent / "static"
              / "file-picker.js").read_text(encoding="utf-8")
        self.assertIn('const kind = entry.study ? "study"', js)
        self.assertIn('" badge-" + kind', js)
        # The caller's start, else the workspace the picker learned itself.
        self.assertIn('options.start || state.workspace || ""', js)
        self.assertIn('state.workspace = s.exploration_root', js)
        css = (pathlib.Path(gui.__file__).parent / "static"
               / "dashboard.css").read_text(encoding="utf-8")
        for k in ("badge-study", "badge-structure", "badge-trajectory"):
            with self.subTest(kind=k):
                self.assertIn(f".fastmdx-picker-row .{k}", css)

    def test_the_plus_opens_where_the_conversation_lives(self):
        import pathlib

        import fastmdxplora.gui as gui

        script = (pathlib.Path(gui.__file__).parent / "static"
                  / "agent-panel.js").read_text(encoding="utf-8")
        self.assertIn('start: convStudy || workspaceRoot || ""', script)

    def test_the_placeholder_clears_the_plus(self):
        import pathlib

        import fastmdxplora.gui as gui

        css = (pathlib.Path(gui.__file__).parent / "static"
               / "dashboard.css").read_text(encoding="utf-8")
        # The ID rule outranks the class rule, so the indent lives on it.
        self.assertIn("#agent-request { min-height: 44px; padding: 12px 52px 12px 46px; }", css)


class TestTheWordsAndTheRows(unittest.TestCase):

    def page(self):
        import pathlib

        import fastmdxplora.gui as gui

        return (pathlib.Path(gui.__file__).parent / "templates"
                / "dashboard.html").read_text(encoding="utf-8")

    def test_no_active_study(self):
        import pathlib

        import fastmdxplora.gui as gui

        for name in ("dashboard.js", "frame.js"):
            js = (pathlib.Path(gui.__file__).parent / "static" / name).read_text(encoding="utf-8")
            with self.subTest(file=name):
                self.assertNotIn("No active exploration", js)
        self.assertNotIn("No active exploration", self.page())

    def test_study_overview_title_and_overview_tab(self):
        page = self.page()
        self.assertIn('<h1 class="page-title">Study Overview</h1>', page)
        self.assertIn("<span>Overview</span>", page)

    def test_the_agent_header_is_a_title_row_and_the_controls_are_under_the_composer(self):
        page = self.page()
        agent = page[page.index('data-page="agent"'):page.index("</section>", page.index('data-page="agent"'))]
        header = agent[agent.index('<div class="page-header">'):agent.index("</div>\n          </div>", agent.index('<div class="page-header">'))]
        for gone in ("agent-settings-open", "agent-conversations", "agent-new"):
            with self.subTest(gone=gone):
                self.assertNotIn(gone, header)
        footer = agent[agent.index('<div class="agent-footer">'):]
        for here in ('id="agent-settings-open"', 'id="agent-footer-mode"',
                     'id="agent-conversations"', 'id="agent-new"'):
            with self.subTest(here=here):
                self.assertIn(here, footer)
        # The footer comes after the composer box.
        self.assertLess(agent.index('<div class="agent-composer-box">'), agent.index('<div class="agent-footer">'))

    def test_the_footer_shows_the_mode(self):
        import pathlib

        import fastmdxplora.gui as gui

        script = (pathlib.Path(gui.__file__).parent / "static"
                  / "agent-panel.js").read_text(encoding="utf-8")
        self.assertIn('var footer = el("agent-footer-mode");', script)

    def test_page_headers_are_one_row(self):
        import pathlib

        import fastmdxplora.gui as gui

        css = (pathlib.Path(gui.__file__).parent / "static"
               / "dashboard.css").read_text(encoding="utf-8")
        rule = css[css.index(".page-header {"):css.index("}", css.index(".page-header {"))]
        self.assertIn("align-items: center", rule)
        self.assertIn("min-height: 44px", rule)
        self.assertIn(".page-subtitle {\n    display: inline;", css)
