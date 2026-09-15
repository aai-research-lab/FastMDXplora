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
