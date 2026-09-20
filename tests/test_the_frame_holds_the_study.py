"""Three columns, one of them always the run.

The sidebar is the study: what is running and how far along. The main
column is the task. The side panel is what the command line prints and the
browser used to throw away -- the explain text, the refusals, the stage
transitions, as they happen -- and a Files tab for what the run has
written, opened in place.

None of the frame knows which page is in the middle. That is what lets a
person look at the molecule without losing the run.
"""

from __future__ import annotations

import json
import pathlib
import re
import unittest
from urllib.request import urlopen

import fastmdxplora.gui as gui

STATIC = pathlib.Path(gui.__file__).parent / "static"
TEMPLATE = pathlib.Path(gui.__file__).parent / "templates" / "dashboard.html"


def _page() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


def _script() -> str:
    return (STATIC / "frame.js").read_text(encoding="utf-8")


def _css() -> str:
    return (STATIC / "dashboard.css").read_text(encoding="utf-8")


class TestTheColumns(unittest.TestCase):

    def test_there_are_three_and_two_handles_between_them(self):
        page = _page()
        self.assertIn('class="sidebar"', page)
        self.assertIn('class="main"', page)
        self.assertIn('id="side-panel"', page)
        self.assertEqual(page.count('class="col-handle"'), 2)

    def test_the_handles_are_separators_a_screen_reader_can_name(self):
        page = _page()
        # Every separator is named, whatever the count: the two column
        # seams and the one between the file list and its preview.
        separators = re.findall(r'<div[^>]*role="separator"[^>]*>', page)
        self.assertGreaterEqual(len(separators), 3)
        for sep in separators:
            with self.subTest(sep=sep[:60]):
                self.assertIn("aria-label=", sep)
                self.assertIn("aria-orientation=", sep)
        self.assertIn('aria-label="Resize sidebar"', page)
        self.assertIn('aria-label="Resize side panel"', page)
        self.assertIn('aria-label="Resize the file list"', page)

    def test_widths_are_tokens_the_grid_reads(self):
        css = _css()
        self.assertIn("--sidebar-width", css)
        self.assertIn("--panel-width", css)
        grid = css[css.index(".app-shell {", css.index("The frame:")):]
        self.assertIn("var(--sidebar-width)", grid[:400])
        self.assertIn("var(--panel-width)", grid[:400])

    def test_dragging_is_bounded(self):
        # A sidebar dragged to zero, or a panel dragged across the whole
        # window, is a page nobody can use and a preference nobody meant.
        script = _script()
        self.assertIn("LIMITS", script)
        self.assertIn("Math.max(lim[0], Math.min(lim[1], px))", script)

    def test_a_double_click_restores_the_default(self):
        self.assertIn('handle.addEventListener("dblclick"', _script())

    def test_the_panel_collapses_and_comes_back(self):
        page = _page()
        self.assertIn('id="side-collapse"', page)
        self.assertIn('id="side-expand"', page)
        self.assertIn("body.panel-collapsed .app-shell", _css())


class TestTheSidePanel(unittest.TestCase):

    def test_it_has_a_log_and_a_files_tab(self):
        page = _page()
        self.assertIn('data-side-tab="log"', page)
        self.assertIn('data-side-tab="files"', page)
        self.assertIn('role="tablist"', page)

    def test_the_log_reads_the_events_the_run_already_writes(self):
        # Not a second stream. /api/events is what the overview page shows;
        # the panel is the same events with room to be read.
        self.assertIn('fetch("/api/events")', _script())

    def test_the_files_tab_reads_what_the_run_wrote(self):
        self.assertIn('fetch("/api/artifacts")', _script())

    def test_a_refusal_is_told_from_a_line(self):
        # The point of the column. A refusal in the middle of ordinary
        # output is what a person needed to see and could not.
        script = _script()
        self.assertIn('kind: "refused"', script)
        self.assertIn('data-kind="refused"', _css())

    def test_the_explain_text_is_a_block_not_a_line(self):
        # The "why" text has a citation on the end and reads as prose. On
        # one line it is a ribbon; in a block it is the paragraph the CLI
        # prints.
        script = _script()
        self.assertIn('kind: "why"', script)
        self.assertIn("et al", script)

    def test_the_log_can_be_filtered_to_what_matters(self):
        page = _page()
        for f in ("all", "refusals", "why"):
            with self.subTest(filter=f):
                self.assertIn(f'data-log-filter="{f}"', page)

    def test_follow_is_on_by_default_and_a_scroll_up_is_respected(self):
        page = _page()
        self.assertIn('id="side-follow" checked', page)
        # Only scroll to the bottom when following or already there.
        self.assertIn("wasAtBottom", _script())

    def test_a_file_opens_in_place_by_kind(self):
        # Images and PDFs are shown by the browser and named here; text is
        # asked of the server, which reads it over one list of types the
        # Agent's + shares, rather than a second list kept in the page.
        script = _script()
        self.assertIn("function preview(item)", script)
        for kind in ('"png"', '"pdf"'):
            with self.subTest(kind=kind):
                self.assertIn(kind, script)
        self.assertIn('fetch("/api/file-text?path="', script)
        from fastmdxplora.gui.agent_panel import ATTACHABLE_SUFFIXES

        self.assertIn(".yml", ATTACHABLE_SUFFIXES)

    def test_every_file_can_be_downloaded_or_opened(self):
        page = _page()
        self.assertIn('id="side-preview-download"', page)
        self.assertIn('id="side-preview-open"', page)

    def test_the_panel_is_not_load_bearing(self):
        # A run must not fail because the panel could not fetch. Every
        # fetch swallows its error.
        script = _script()
        self.assertGreaterEqual(script.count(".catch(function ()"), 3)


class TestTheSettingsPopup(unittest.TestCase):

    def test_the_trigger_is_at_the_foot_of_the_sidebar(self):
        page = _page()
        trigger = page.index('id="settings-open"')
        sidebar_close = page.index("</aside>", trigger)
        self.assertLess(trigger, sidebar_close)
        # Nothing after it inside the sidebar: it is the foot.
        between = page[trigger:sidebar_close]
        self.assertNotIn("<nav", between)

    def test_the_trigger_is_the_name_and_nothing_else(self):
        # The engine and mode under the name read as a standing
        # advertisement for somebody else's product. They are in the
        # popup, where somebody choosing them is looking.
        page = _page()
        trigger = page[page.index('id="settings-open"'):page.index("</button>", page.index('id="settings-open"'))]
        self.assertNotIn("account-detail", trigger)
        self.assertIn('id="account-name">FastMDXplora', trigger)
        self.assertNotIn('el("account-detail")', _script())

    def test_the_popup_is_a_dialog_with_a_theme_switch(self):
        page = _page()
        popup = page[page.index('id="settings-popup"'):]
        self.assertIn('role="dialog"', popup[:200])
        for theme in ("graphite", "ink", "paper"):
            with self.subTest(theme=theme):
                self.assertIn(f'data-theme="{theme}"', popup)

    def test_escape_and_a_click_outside_close_it(self):
        script = _script()
        self.assertIn('e.key === "Escape"', script)
        self.assertIn("!popup.contains(e.target)", script)

    def test_the_page_has_exactly_the_external_links_it_had(self):
        # test_dashboard_html_has_aai_branding counts external references
        # on purpose. The Tools group moved its two links into the popup;
        # the total is unchanged, and the DOI on the Cite page is the third.
        page = _page()
        self.assertEqual(page.count("https://"), 3)
        popup = page[page.index('id="settings-popup"'):page.index('<div class="app-shell">')]
        self.assertIn("readthedocs.io", popup)
        self.assertIn("github.com/aai-research-lab", popup)

    def test_cite_is_in_the_popup_with_the_version(self):
        # It was a line in the sidebar on every page. The owner moved it
        # into the settings popup, replacing About and keeping the version
        # beside it: the popup is on every page too, one click away, and
        # the sidebar had grown crowded. That is his call to make.
        page = _page()
        popup = page[page.index('id="settings-popup"'):page.index('<div class="app-shell">')]
        self.assertIn('data-view-link="cite">Cite FastMDXplora', popup)
        self.assertIn('id="settings-version"', popup)
        self.assertNotIn("About FastMDXplora", popup)
        sidebar = page[page.index('<aside class="sidebar"'):page.index("</aside>")]
        self.assertNotIn("footer-cite", sidebar)



class TestTheThemes(unittest.TestCase):

    def test_three_schemes_over_one_set_of_tokens(self):
        theme = (STATIC / "theme.css").read_text(encoding="utf-8")
        self.assertIn('body[data-theme="ink"]', theme)
        self.assertIn('body[data-theme="paper"]', theme)
        # Graphite is the default: the tokens themselves, no override.
        self.assertNotIn('body[data-theme="graphite"]', theme)

    def test_a_scheme_changes_the_ground_and_not_the_meaning_of_colour(self):
        # Green done, amber qualified, red refused are the same in all
        # three. A scheme that recoloured status would make a warning look
        # like one thing in Graphite and another in Paper.
        theme = (STATIC / "theme.css").read_text(encoding="utf-8")
        for name in ("ink", "paper"):
            block = theme[theme.index(f'body[data-theme="{name}"]'):]
            block = block[:block.index("}")]
            with self.subTest(theme=name):
                for token in ("--status-error", "--status-warning", "--status-success"):
                    self.assertNotIn(token, block)

    def test_the_choice_is_remembered(self):
        self.assertIn('store.set("theme", name)', _script())

    def test_the_wordmark_needs_no_network(self):
        # This GUI runs on machines with no route out -- a cluster login
        # node, a lab box behind a firewall. A web font is a font that
        # fails there, and the branding test refuses googleapis for that
        # reason.
        self.assertNotIn("googleapis", _page().lower())
        css = _css()
        mark = css[css.index(".brand-product {"):]
        mark = mark[:mark.index("}")]
        self.assertIn("Georgia", mark)
        self.assertNotIn("googleapis", mark)


class TestItServes(unittest.TestCase):

    def test_the_frame_is_in_the_page_a_browser_gets(self):
        import tempfile

        from fastmdxplora.gui.server import start_dashboard_session

        session = start_dashboard_session(
            output=tempfile.mkdtemp(), host="127.0.0.1", port=0)
        self.addCleanup(session.server.shutdown)
        base = f"http://127.0.0.1:{session.port}"
        page = urlopen(base + "/").read().decode("utf-8")
        self.assertIn('id="side-panel"', page)
        self.assertEqual(urlopen(base + "/static/frame.js").getcode(), 200)
        events = json.loads(urlopen(base + "/api/events").read())
        self.assertIn("events", events)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


class TestTheStudyIsInTheSidebar(unittest.TestCase):
    """The top bar became a sidebar block, and nothing the JS writes to moved
    out from under it."""

    def test_the_bar_is_gone(self):
        self.assertNotIn('class="top-bar"', _page())

    def test_every_id_the_dashboard_writes_to_is_still_there_once(self):
        page = _page()
        for run_id in ("topbar-run-id", "topbar-run-title", "topbar-status-dot",
                       "topbar-status-text", "topbar-stage", "topbar-step",
                       "topbar-total", "topbar-progress", "topbar-eta",
                       "pause-toggle", "refresh-now", "open-output",
                       "refreshed-at"):
            with self.subTest(id=run_id):
                self.assertEqual(page.count(f'id="{run_id}"'), 1)

    def test_they_are_inside_the_sidebar(self):
        page = _page()
        sidebar = page[page.index('<aside class="sidebar"'):page.index("</aside>")]
        for run_id in ("topbar-run-title", "topbar-eta", "pause-toggle"):
            with self.subTest(id=run_id):
                self.assertIn(f'id="{run_id}"', sidebar)

    def test_the_stages_reuse_the_class_the_dashboard_drives(self):
        # dashboard.js updates `.stage-step` by class selector, so the
        # sidebar list and the overview timeline are painted by the same
        # code and cannot disagree.
        page = _page()
        sidebar = page[page.index('<aside class="sidebar"'):page.index("</aside>")]
        for stage in ("setup", "nvt", "npt", "production", "analysis", "report"):
            with self.subTest(stage=stage):
                self.assertIn(f'class="stage-step" data-stage="{stage}"', sidebar)
        script = (STATIC / "dashboard.js").read_text(encoding="utf-8")
        self.assertIn("$$('.stage-step')", script)


class TestTheBuilderAsksFourQuestions(unittest.TestCase):
    """Four numbered cards, the phases as tiles, and the second question
    continued inside the first rather than opened as a card of its own."""

    def builder(self):
        page = _page()
        start = page.index('<section class="page" data-page="run">')
        # Anchored on the section, not on `data-page="overview"` alone: the
        # <html> element carries that attribute too, and matching it sent
        # an earlier edit slicing backwards through the file and
        # duplicating two hundred lines.
        end = page.index('<section class="page" data-page="overview"', start)
        self.assertGreater(end, start)
        return page[start:end]

    def test_four_cards_numbered_in_order(self):
        import re

        steps = re.findall(r'builder-step">(\d\d)<', self.builder())
        self.assertEqual(steps, ["01", "02", "03", "04"])
        self.assertEqual(self.builder().count('class="card builder-card"'), 4)

    def test_where_it_is_continues_the_first_card(self):
        builder = self.builder()
        self.assertNotIn("Where is it?", builder)
        self.assertIn('id="run-input-card" hidden', builder)
        # Inside the first card: before the phases card opens.
        self.assertLess(builder.index('id="run-input-card"'),
                        builder.index('id="run-phases-card"'))

    def test_every_field_the_js_drives_is_still_there_once(self):
        builder = self.builder()
        for field in ("run-start", "run-system", "run-trajectory",
                      "run-topology", "run-config-path", "run-output",
                      "run-phases", "run-start-button", "run-download"):
            with self.subTest(field=field):
                self.assertEqual(builder.count(f'id="{field}"'), 1)

    def test_the_phases_are_tiles(self):
        css = _css()
        rule = css[css.index(".run-phases {"):]
        rule = rule[:rule.index("}")]
        self.assertIn("grid-template-columns: 1fr 1fr", rule)
        # A chosen tile is bordered in the accent, not striped down one
        # edge, so the set that will run reads at a glance.
        self.assertIn('.run-phase[data-chosen="true"] { border-color: var(--accent-cyan)', css)

    def test_the_page_is_not_duplicated(self):
        # The regression the slicing bug produced: the run section's cards
        # appearing twice. Once, and once only.
        page = _page()
        self.assertEqual(page.count('<section class="page" data-page="run">'), 1)
        self.assertEqual(page.count('id="run-phases-card"'), 1)


class TestEachColumnScrollsAlone(unittest.TestCase):
    """The shell is the viewport; each column is its own scroll region; the
    wordmark and the settings trigger stay where they are however long the
    sidebar gets."""

    def test_the_body_does_not_scroll(self):
        css = _css()
        self.assertIn("body { overflow: hidden; }", css)
        self.assertIn(".app-shell { height: 100vh; min-height: 0; }", css)

    def test_the_columns_do(self):
        css = _css()
        block = css[css.index("Each column scrolls by itself"):]
        self.assertIn("overflow-y: auto;", block)
        self.assertIn(".sidebar, .main, .side-panel {", block)

    def test_the_wordmark_is_pinned(self):
        css = _css()
        block = css[css.index("Each column scrolls by itself"):]
        rule = block[block.index(".sidebar-brand {"):]
        rule = rule[:rule.index("}")]
        self.assertIn("position: sticky; top: 0;", rule)

    def test_the_settings_trigger_is_pinned_at_the_foot(self):
        css = _css()
        block = css[css.index("Each column scrolls by itself"):]
        self.assertIn(".sidebar-account { position: sticky; bottom: 0;", block)


class TestThePopupItemsAct(unittest.TestCase):

    def test_agent_settings_opens_the_agents_dialog_without_leaving(self):
        # Landing on the page and leaving somebody to find the button was
        # the same as not linking it. And navigating to the Agent page in
        # order to open a settings dialog was a detour: the dialog is a
        # fixed overlay and belongs at body level, where it opens over
        # whatever page is showing.
        script = _script()
        self.assertIn('el("settings-agent-link")', script)
        self.assertIn("window.FastMDXAgent.openSettings()", script)
        handler = script[script.index('el("settings-agent-link")'):script.index("The other popup items navigate")]
        self.assertNotIn('navigate("agent")', handler)
        page = _page()
        self.assertLess(page.index('id="agent-settings"'), page.index('<div class="app-shell">'))
        agent = (STATIC / "agent-panel.js").read_text(encoding="utf-8")
        self.assertIn("window.FastMDXAgent = { openSettings: openSettings", agent)

    def test_cite_in_the_popup_goes_to_the_cite_page(self):
        page = _page()
        popup = page[page.index('id="settings-popup"'):page.index('<div class="app-shell">')]
        item = popup[popup.index("Cite FastMDXplora") - 120:popup.index("Cite FastMDXplora")]
        self.assertIn('href="#cite"', item)

    def test_the_version_comes_from_the_cite_page(self):
        # One copy, filled in by the server, rather than a second
        # placeholder to keep in step.
        self.assertIn('el("cite-version")', _script())

    def test_the_settings_page_is_named_for_what_it_holds(self):
        # "Browser settings" read as configuring the browser. The page is
        # viewer and dashboard preferences.
        self.assertIn("Display preferences", _page())

    def test_pause_is_one_word(self):
        # "Pause updates" overlapped Refresh in a 232px sidebar.
        self.assertIn('<span id="pause-label">Pause</span>', _page())


class TestTheViewerFollowsTheRun(unittest.TestCase):
    """When new frames arrive during a run, show the newest -- unless a
    person has chosen a frame, in which case the run stops choosing for
    them."""

    def script(self):
        return (STATIC / "molecule-viewer.js").read_text(encoding="utf-8")

    def test_the_toggle_exists_and_is_on_by_default(self):
        page = _page()
        self.assertIn('id="traj-follow" checked', page)

    def test_new_frames_land_on_the_newest_when_following(self):
        script = self.script()
        self.assertIn('document.getElementById("traj-follow")?.checked', script)
        self.assertIn("STATE.playbackFrames - 1 : current", script)

    def test_scrubbing_or_stepping_stops_following(self):
        # A person looking at frame 40 is not dragged to frame 200 by the
        # next poll.
        script = self.script()
        self.assertIn("function stopFollowing()", script)
        seek = script[script.index('"dashboard:trajectory-seek"'):]
        self.assertIn("stopFollowing();", seek[:120])
        action = script[script.index('"dashboard:trajectory-action"'):]
        self.assertIn('action !== "last") stopFollowing()', action[:400])


class TestTheOverviewHoldsWhatTheSidebarCannot(unittest.TestCase):
    """The sidebar shows the stage, the step, the progress and the ETA on
    every page. The Overview used to show them again, in a bar and a
    nine-row table, beside a stage timeline the sidebar also has and a
    Recent events card the Log panel now is. What is left is what only
    this page can carry."""

    def overview(self):
        page = _page()
        start = page.index('<section class="page" data-page="overview" data-status')
        return page[start:page.index("</section>", start)]

    def test_nothing_repeated_from_the_sidebar(self):
        ov = self.overview()
        for gone in ("Simulation progress", "Stage timeline", "Recent events",
                     'id="hero-card"', 'id="events-list"'):
            with self.subTest(gone=gone):
                self.assertNotIn(gone, ov)
        # No stage list here: the sidebar's is the one.
        self.assertNotIn('class="stage-step"', ov)

    def test_what_only_this_page_can_carry(self):
        ov = self.overview()
        for kept in ('id="hero-health"', 'id="health-explanation"',
                     'id="chart-stack"', 'id="mini-preview-canvas"',
                     'id="live-simtime-cell"', 'id="live-frames-cell"',
                     'id="live-checkpoint-cell"'):
            with self.subTest(kept=kept):
                self.assertIn(kept, ov)

    def test_the_order_is_health_structure_charts(self):
        # Health first: the one thing that can say the run is going wrong
        # before the numbers do. Then the molecule, then what is happening
        # to it. The charts had come before the structure; the person
        # running it wanted to see the thing before its numbers.
        ov = self.overview()
        health = ov.index('id="hero-health"')
        structure = ov.index('id="mini-preview-canvas"')
        charts = ov.index('id="chart-stack"')
        self.assertLess(health, structure)
        self.assertLess(structure, charts)

    def test_the_empty_state_says_what_to_do(self):
        # "Nothing to show" said nothing. It points at the Agent now, and
        # at the builder as the second way in.
        ov = self.overview()
        absent = ov[ov.index('id="live-absent"'):ov.index('id="live-panels"')]
        self.assertIn('data-view-link="agent"', absent)
        self.assertIn('data-view-link="run"', absent)

    def test_the_ids_the_script_writes_still_land_somewhere(self):
        # setText guards on null, but a hidden landing spot keeps the
        # values reachable for anyone who inspects the page.
        ov = self.overview()
        for hidden_id in ("live-stage-cell", "live-step-cell", "live-total-cell",
                          "live-eta-cell", "live-progress-fill"):
            with self.subTest(id=hidden_id):
                self.assertIn(f'id="{hidden_id}" hidden', ov)


class TestThreeThingsSeenInTheBrowser(unittest.TestCase):

    def test_the_viewer_overlay_is_contained(self):
        # "LIVE · nvt · frame 123000 · age 1s" is position: absolute; with
        # no positioned parent it escaped to the viewport corner, over the
        # side panel's collapse button.
        css = _css()
        self.assertIn(".viewer-canvas-wrap { position: relative; }", css)

    def test_the_summary_cards_have_a_grid(self):
        # `metric-grid` was a class with no rule behind it, so the cards
        # had nothing to sit in and overlapped.
        page = _page()
        self.assertIn('class="grid overview-summary metric-cards" id="overview-summary-cards"', page)
        self.assertNotIn("metric-grid", page)
        css = _css()
        # Two across, and the value wraps rather than clips. Four across
        # put each card at a quarter of the page with its text cut off.
        self.assertIn(".overview-summary { grid-template-columns: repeat(2, minmax(0, 1fr))", css)
        self.assertIn(".overview-summary .metric-card-value { white-space: normal", css)

    def test_the_overview_is_one_column(self):
        # A structure card beside the charts made a second right-hand panel
        # inside the centre, next to the real one.
        page = _page()
        start = page.index('<section class="page" data-page="overview" data-status')
        ov = page[start:page.index("</section>", start)]
        self.assertIn('class="overview-stack"', ov)
        self.assertNotIn("overview-grid", ov)
        css = _css()
        self.assertIn(".overview-stack { display: flex; flex-direction: column;", css)
        self.assertIn("#live-panels .preview-frame { height: 360px; }", css)


class TestElevenThingsFromUsingIt(unittest.TestCase):

    def test_the_study_keeps_its_name_when_a_run_begins(self):
        script = (STATIC / "dashboard.js").read_text(encoding="utf-8")
        self.assertIn("The study's name is the system, not the folder it went into", script)
        self.assertIn('setTextWithTooltip("topbar-run-title", chosen || state.runId || state.runTitle);', script)

    def test_one_output_button_that_also_copies_the_path(self):
        page = _page()
        self.assertEqual(page.count('id="open-output"'), 1)
        self.assertNotIn('id="copy-output-path"', page)
        self.assertIn('openOut.textContent = "Path copied";', _script())

    def test_paper_leaves_no_token_dark(self):
        import re

        theme = (STATIC / "theme.css").read_text(encoding="utf-8")
        root = theme[theme.index(":root {"):theme.index("}", theme.index(":root {"))]
        paper = theme[theme.index('body[data-theme="paper"]'):]
        paper = paper[:paper.index("}")]
        overridden = set(re.findall(r"(--[a-z0-9-]+):", paper))
        for token, value in re.findall(r"(--[a-z0-9-]+):\s*([^;]+);", root):
            if re.search(r"#[01][0-9a-f]{5}\b|rgba\(0, 0, 0, 0\.[3-9]|rgba\(255, 255, 255", value):
                with self.subTest(token=token):
                    self.assertIn(token, overridden, f"{token} is still {value.strip()} under Paper")

    def test_the_sidebar_collapses_like_the_panel(self):
        page = _page()
        self.assertIn('id="sidebar-collapse"', page)
        self.assertIn('id="sidebar-expand"', page)
        script = _script()
        self.assertIn("function setSidebarCollapsed(yes)", script)
        self.assertIn("body.sidebar-collapsed .app-shell", _css())

    def test_the_explanations_reach_the_log(self):
        # The "why" filter had nothing to show: the explain text was
        # printed by the caller's hook and never written as an event.
        import inspect

        from fastmdxplora.simulation import runner

        source = inspect.getsource(runner.run_simulation)
        self.assertIn('telemetry.event(text, level="explain")', source)
        self.assertIn('if (level === "explain") return { kind: "why", level: "info" };', _script())

    def test_one_status_row_and_complete(self):
        page = _page()
        sidebar = page[page.index('<aside class="sidebar"'):page.index("</aside>")]
        self.assertNotIn('class="study-facts', sidebar)
        self.assertIn('<span id="topbar-stage" hidden></span>', sidebar)
        self.assertIn('<span class="metric-label">Complete</span>', sidebar)
        self.assertNotIn('<span class="metric-label">Progress</span>', sidebar)


class TestTheCentreSurvivesCollapse(unittest.TestCase):
    """A display:none grid item leaves auto-placement, so hiding the
    sidebar and its handle shifted .main a track to the left, into the
    collapsed sidebar's zero track, and it vanished. The collapsed columns
    are hidden by width and visibility now, kept in the grid flow, so each
    child stays mapped to its track and the centre keeps the 1fr."""

    def css(self):
        return (STATIC / "dashboard.css").read_text(encoding="utf-8")

    def test_collapsed_columns_are_not_display_none(self):
        css = self.css()
        # The rule that hides the panel keeps it in flow.
        block = css[css.index('body.panel-collapsed .side-panel,'):]
        block = block[:block.index("}")]
        self.assertNotIn("display: none", block)
        self.assertIn("width: 0", block)
        self.assertIn("visibility: hidden", block)
        sb = css[css.index('body.sidebar-collapsed .sidebar,'):]
        sb = sb[:sb.index("}")]
        self.assertNotIn("display: none", sb)
        self.assertIn("width: 0", sb)


try:
    from playwright.sync_api import sync_playwright  # noqa: F401
    _HAVE_PLAYWRIGHT = True
except ImportError:
    _HAVE_PLAYWRIGHT = False


@unittest.skipUnless(_HAVE_PLAYWRIGHT, "playwright not installed")
class TestTheCentreIsCentredInEveryStateRendered(unittest.TestCase):
    """Rendered, not read: the centre column keeps a positive width and
    its content stays centred with both side columns folded."""

    def test_rendered_widths(self):
        import sys
        sys.path.insert(0, "src")
        from playwright.sync_api import sync_playwright

        from fastmdxplora.gui.server import start_dashboard_session

        session = start_dashboard_session(output="/tmp/sh7/whole",
                                          host="127.0.0.1", port=0)
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch()
                page = browser.new_page(viewport={"width": 1400, "height": 900})
                page.goto(session.url, wait_until="domcontentloaded")
                page.wait_for_selector(".main", timeout=8000)

                def width(sel):
                    return page.eval_on_selector(
                        sel, "el => Math.round(el.getBoundingClientRect().width)")

                def left(sel):
                    return page.eval_on_selector(
                        sel, "el => Math.round(el.getBoundingClientRect().left)")

                page.evaluate("(c)=>{document.body.className=c;}",
                              "sidebar-collapsed panel-collapsed")
                page.wait_for_timeout(120)
                # The centre column spans the viewport and its content is
                # centred within it, not pinned to an edge.
                self.assertGreater(width(".main"), 1000)
                shell_left = left(".page-shell")
                self.assertGreater(shell_left, 100)
                self.assertEqual(width(".page-shell"), 900)
                browser.close()
        finally:
            session.server.shutdown()


class TestTheFilePreviewReadsWhatTheAgentCanRead(unittest.TestCase):
    """The preview had a shorter list of types than the Agent's + and a
    512 KB cliff with no explanation. One reader, one list, and a View
    and Code mode: Code is the bytes and the default for anything
    scientific, View is the convenience and means something different per
    type. The seam between the list and the preview drags, and either can
    take the whole panel."""

    def test_one_reader_over_one_list_of_types(self):
        import inspect

        from fastmdxplora.gui.agent_panel import ATTACHABLE_SUFFIXES, read_attachment, read_text_file

        # The attachment reader delegates, so the two cannot drift.
        self.assertIn("return read_text_file(path", inspect.getsource(read_attachment))
        self.assertIn("ATTACHABLE_SUFFIXES", inspect.getsource(read_text_file))
        for suffix in (".yml", ".json", ".log", ".md", ".csv", ".pdb", ".cif", ".py"):
            self.assertIn(suffix, ATTACHABLE_SUFFIXES)

    def test_a_read_is_confined_to_the_study(self):
        import tempfile
        from pathlib import Path

        from fastmdxplora.gui.agent_panel import read_text_file

        root = Path(tempfile.mkdtemp())
        (root / "run").mkdir()
        (root / "run" / "a.yml").write_text("a: 1", encoding="utf-8")
        (root / "secret.yml").write_text("not yours", encoding="utf-8")
        self.assertTrue(read_text_file(root / "run" / "a.yml", within=root / "run")["ok"])
        for escape in (root / "secret.yml", str(root / "run" / ".." / "secret.yml")):
            answer = read_text_file(escape, within=root / "run")
            self.assertFalse(answer["ok"])
            self.assertIn("outside this study", answer["error"])

    def test_a_long_file_keeps_its_head_and_tail_and_says_so(self):
        import tempfile
        from pathlib import Path

        from fastmdxplora.gui.agent_panel import PREVIEW_LIMIT_BYTES, read_text_file

        f = Path(tempfile.mkdtemp()) / "exploration.log"
        f.write_text("start\n" + "x" * (PREVIEW_LIMIT_BYTES + 50_000) + "\nERROR at the end\n",
                     encoding="utf-8")
        answer = read_text_file(f)
        self.assertTrue(answer["truncated"])
        self.assertTrue(answer["text"].startswith("start"))
        self.assertTrue(answer["text"].rstrip().endswith("ERROR at the end"))
        self.assertIn("not shown", answer["text"])

    def test_the_facts_make_it_a_record(self):
        import tempfile
        from pathlib import Path

        from fastmdxplora.gui.agent_panel import read_text_file

        f = Path(tempfile.mkdtemp()) / "resolved_config.yml"
        f.write_text("systems:\n- system: 1UAO\n", encoding="utf-8")
        answer = read_text_file(f)
        self.assertEqual(answer["size"], len("systems:\n- system: 1UAO\n"))
        self.assertEqual(len(answer["sha256"]), 12)
        self.assertEqual(answer["lines"], 3)
        self.assertEqual(answer["suffix"], "yml")

    def test_code_is_the_default_and_the_choice_is_remembered(self):
        script = _script()
        self.assertIn('store.get("previewMode", "code")', script)
        self.assertIn('store.set("previewMode", mode)', script)

    def test_view_means_something_different_per_type(self):
        script = _script()
        for kind in ("renderTable", "renderTree", "renderLog", "renderStructure", "renderDoc"):
            with self.subTest(kind=kind):
                self.assertIn(f"function {kind}(", script)
        # Markdown is escaped before it is rendered, so a file cannot put
        # markup in the page.
        doc = script[script.index("function renderDoc("):script.index("function showText(")]
        self.assertIn("escapeText(text)", doc)

    def test_the_seam_drags_resets_and_is_remembered(self):
        page = _page()
        self.assertIn('id="side-seam"', page)
        script = _script()
        self.assertIn('store.set("sideFilesHeight"', script)
        self.assertIn('seam.addEventListener("dblclick"', script)
        css = _css()
        self.assertIn("flex: 0 0 var(--side-files-height, 45%)", css)
        # Either side can take the whole panel: the bound is 0 to 100.
        self.assertIn("Math.max(0, Math.min(100, pct))", script)

    def test_the_preview_header_stays_put(self):
        css = _css()
        self.assertIn(".side-preview-head { position: sticky; top: 0;", css)


class TestAPdfFillsThePreview(unittest.TestCase):

    def test_the_iframe_has_a_height_that_resolves(self):
        # height: 100% against a flex parent with no definite height
        # resolves to nothing: the frame was there and zero pixels tall,
        # which reads as "the PDF does not render".
        css = _css()
        rule = css[css.index(".side-preview-body iframe {"):]
        rule = rule[:rule.index("}")]
        self.assertIn("flex: 1 1 auto", rule)
        self.assertIn("min-height: 320px", rule)
        self.assertNotIn("height: 100%", rule)
        body = css[css.index(".side-preview-body { "):]
        body = body[:body.index("}")]
        self.assertIn("min-height: 0", body)
