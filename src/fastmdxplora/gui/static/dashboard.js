/* FastMDXplora Live Dashboard — application controller.
 *
 * Framework-free and fully offline.  This module owns navigation, API
 * polling, status/results/file rendering, and the shared event bus used by
 * charts.js and molecule-viewer.js.  Every API renderer is isolated so one
 * malformed payload can never prevent the other dashboard sections from
 * updating.
 */

(function () {
  "use strict";

  const $ = (selector, root) => (root || document).querySelector(selector);
  const $$ = (selector, root) => Array.from((root || document).querySelectorAll(selector));
  const byId = (id) => document.getElementById(id);

  const state = {
    outputDir: "",
    paused: false,
    pollIntervalMs: 3000,
    /* Whether `/api/stream` is open; the poll slows while it is. */
    streaming: false,
    changeStream: null,
    lastUpdateMs: 0,
    refreshTimer: null,
    /* The pages are the pages the document has. Naming them here as well
     * meant adding a section was not enough to make it reachable: navigation
     * fell back to the overview, silently, and the new page looked like it
     * had been wired to the wrong link. */
    pages: [],
    activePage: "overview",
    appState: {},
    initialRouteResolved: false,
    stages: [],
    phases: [],
    ligandResname: null,
    bindingPocketCutoff: 5,
    playbackAvailable: false,
    playbackFrames: 0,
    playbackTotalFrames: 0,
    playbackFrameTimes: [],
    playbackSignature: null,
    metrics: [],
    status: {},
    health: {},
    results: {},
    structureInfo: null,
    setupManifest: {},
    simManifest: {},
    runId: "",
    runTitle: "FastMDXplora Live",
    apiErrors: {},
  };

  let chartHistorySamples = 600;

  document.addEventListener("DOMContentLoaded", boot);

  function boot() {
    const configuredRefresh = Number(document.body?.dataset.refreshSeconds || 3);
    state.pollIntervalMs = clampInt(configuredRefresh * 1000, 1000, 60000, 3000);
    wireNavigation();
    wireTopBar();
    wireViewerToggle();
    wireInfoTabs();
    wireTrajectoryControls();
    wireSettings();
    navigate(location.hash.replace(/^#/, "") || "overview", {updateHash: false});
    startLoadingChecklist();
    schedulePoll(0);
    openChangeStream();
  }

  /* ------------------------------------------------------------------ */
  /* Navigation                                                          */
  /* ------------------------------------------------------------------ */
  function wireNavigation() {
    state.pages = $$('.page')
      .map((element) => element.getAttribute("data-page"))
      .filter(Boolean);
    // The demo study, copied into the folder the GUI was started in, then
    // opened as any study is.
    const demoButton = byId("open-demo");
    if (demoButton) {
      demoButton.addEventListener("click", async () => {
        demoButton.disabled = true;
        if (Number(state.appState?.demo_to_fetch || 0)) demoButton.textContent = "Fetching the demo study\u2026";
        try {
          const response = await fetch("/api/demo", {
            method: "POST", headers: {"content-type": "application/json"}, body: "{}",
          });
          const answer = await response.json();
          if (answer && answer.ok) {
            location.hash = "#overview";
            location.reload();
            return;
          }
          demoButton.textContent = (answer && answer.error) || "Could not open it.";
        } catch (error) {
          demoButton.textContent = "The server did not answer.";
        }
        demoButton.disabled = false;
      });
    }
    // The methods are there to be pasted into a manuscript.
    const methodsCopy = document.getElementById("overview-methods-copy");
    if (methodsCopy) {
      methodsCopy.addEventListener("click", async () => {
        const copied = await copyText(methodsPlain);
        window.FastMDXIcons.flash(methodsCopy, copied, copied ? "Copied" : "Select the text to copy it.");
      });
    }
    $$('[data-view-link]').forEach((element) => {
      element.addEventListener("click", (event) => {
        event.preventDefault();
        navigate(element.getAttribute("data-view-link"));
      });
    });
    window.addEventListener("hashchange", () => {
      const page = location.hash.replace(/^#/, "");
      if (state.pages.includes(page) || DIALOG_OF[page]) navigate(page, {updateHash: false});
    });
  }

  /* Preferences and the citation were pages and are dialogs over the page
   * shown; a link to either (#settings, #cite) opens it. */
  const DIALOG_OF = {settings: "prefs-dialog", preferences: "prefs-dialog", cite: "cite-dialog"};

  function navigate(page, options) {
    const opts = options || {};
    if (DIALOG_OF[page] && byId(DIALOG_OF[page])) {
      if (!state.activePage) navigate("overview");
      else history.replaceState(null, "", `#${state.activePage}`);
      window.FastMDXDialog?.open(DIALOG_OF[page]);
      return;
    }
    if (!state.pages.includes(page)) page = "overview";
    state.activePage = page;
    $$('.page').forEach((element) => {
      const hidden = element.getAttribute("data-page") !== page;
      element.hidden = hidden;
    });
    $$('[data-view-link]').forEach((element) => {
      element.classList.toggle(
        "active",
        element.getAttribute("data-view-link") === page
      );
    });
    document.documentElement.setAttribute("data-page", page);
    // A page opens at its top. The column's scroll position carried over
    // from the last page, so "open the report" from the foot of a long
    // thread landed at the foot of the report.
    const column = document.querySelector(".main");
    if (column) column.scrollTop = 0;
    if (opts.updateHash !== false && location.hash !== `#${page}`) {
      history.replaceState(null, "", `#${page}`);
    }
    requestAnimationFrame(() => {
      // The live panels moved onto the overview, so opening that is
      // what starts the polling they need. Keyed to a page that no
      // longer exists, nothing would have started.
      if (page === "overview") emit("live-page-opened", {});
      if (page === "viewer") emit("viewer-page-opened", {});
      if (page === "analysis" || page === "files") emit("results-page-opened", {});
      // Any page opened, for a page's own script to load what it shows:
      // the Report page listened for this and it was never sent.
      emit("navigate", {page});
    });
  }

  /* ------------------------------------------------------------------ */
  /* Loading screen                                                      */
  /* ------------------------------------------------------------------ */
  function startLoadingChecklist() {
    const checks = [
      ["telemetry", "/api/status"],
      ["structure", "/api/structure-info"],
      ["metrics", "/api/metrics"],
    ].map(([name, url]) => {
      setLoadingStep(name, "active");
      return fetchJSON(url)
        .then(() => setLoadingStep(name, "done"))
        .catch(() => setLoadingStep(name, "error"));
    });
    Promise.allSettled(checks).finally(() => {
      window.setTimeout(() => {
        document.body.classList.remove("state-loading");
        document.body.classList.add("state-ready");
        const loading = byId("loading-screen");
        if (loading) loading.setAttribute("aria-hidden", "true");
      }, 300);
    });
  }

  function setLoadingStep(name, status) {
    const element = $(`.loading-step[data-step="${name}"]`);
    if (element) element.setAttribute("data-state", status);
  }

  /* ------------------------------------------------------------------ */
  /* Controls                                                            */
  /* ------------------------------------------------------------------ */
  function wireTopBar() {
    byId("pause-toggle")?.addEventListener("click", () => {
      state.paused = !state.paused;
      byId("pause-toggle")?.setAttribute("aria-pressed", String(state.paused));
      setText("pause-label", state.paused ? "Resume" : "Pause");
      showToast(
        state.paused
          ? "Browser updates paused. The OpenMM simulation is still running."
          : "Browser updates resumed."
      );
      if (!state.paused) schedulePoll(0);
    });
    byId("refresh-now")?.addEventListener("click", () => schedulePoll(0));
    byId("study-folder")?.addEventListener("click", copyTheFolder);
    // Fitted again as the sidebar is dragged wider or narrower.
    if (window.ResizeObserver && byId("study-folder")) {
      new ResizeObserver(() => fitTheFolderName()).observe(byId("study-folder"));
    }
    // The run's card is pinned just above the foot, so the foot's height
    // is where it stops.
    const foot = document.querySelector(".sidebar-foot");
    if (window.ResizeObserver && foot) {
      new ResizeObserver(() => {
        foot.parentElement.style.setProperty("--sidebar-foot-height", `${foot.offsetHeight}px`);
      }).observe(foot, {box: "border-box"});
    }
    byId("open-output")?.addEventListener("click", async () => {
      try {
        const payload = await fetchJSON("/api/open-output");
        if (!payload.path) {
          showToast("No study is open, so there is no folder to open.", "warning");
        } else if (payload.opened) {
          showToast(`Opened output folder: ${payload.path}`);
        } else {
          await copyText(payload.path || state.outputDir || "");
          showToast("Could not open the folder automatically; its path was copied.", "warning");
        }
      } catch (error) {
        if (state.outputDir) await copyText(state.outputDir);
        showToast("Could not open the output folder; its path was copied.", "warning");
      }
    });
  }

  function wireViewerToggle() {
    byId("mini-preview-frame")?.addEventListener("click", () => navigate("viewer"));
  }

  /* The information beside the viewer is a set of tabs, as a screen reader
   * is told it is: the chosen tab selected and the only one in the Tab
   * order, the arrow keys, Home and End moving between them. */
  function wireInfoTabs() {
    const tabs = $$('.info-tab');
    function choose(tab, focus) {
      const name = tab.getAttribute("data-tab");
      tabs.forEach((item) => {
        const chosen = item === tab;
        item.classList.toggle("active", chosen);
        item.setAttribute("aria-selected", String(chosen));
        item.tabIndex = chosen ? 0 : -1;
      });
      $$('.info-pane').forEach((pane) => {
        const active = pane.getAttribute("data-tab") === name;
        pane.hidden = !active;
        pane.classList.toggle("active", active);
      });
      if (focus) tab.focus();
    }
    tabs.forEach((tab, index) => {
      tab.addEventListener("click", () => choose(tab, false));
      tab.addEventListener("keydown", (event) => {
        const to = {ArrowRight: index + 1, ArrowLeft: index - 1, Home: 0,
          End: tabs.length - 1}[event.key];
        if (to === undefined) return;
        event.preventDefault();
        choose(tabs[(to + tabs.length) % tabs.length], true);
      });
    });
  }

  function wireTrajectoryControls() {
    byId("traj-slider")?.addEventListener("input", (event) => {
      emit("trajectory-seek", {frame: parseInt(event.target.value, 10) || 0});
    });
    $$('[data-traj]').forEach((button) => {
      button.addEventListener("click", () => {
        emit("trajectory-action", {action: button.getAttribute("data-traj")});
      });
    });
  }

  function wireSettings() {
    // The Preferences dialog's fields, and the study's own words from the
    // study card's menu (preferences.js keeps both in the browser).
    const ids = [
      "setting-protein-rep", "setting-ground", "setting-show-water", "setting-show-ions",
      "setting-spin", "setting-preserve-camera", "setting-pocket-cutoff",
      "setting-chart-history", "setting-time-format", "setting-run-name",
      "setting-ligand-resname",
    ];
    ids.forEach((id) => byId(id)?.addEventListener("change", onSettingsChanged));
    readSettings();
    byId("pocket-cutoff")?.addEventListener("change", (event) => {
      const value = clampFloat(parseFloat(event.target.value), 3, 15, 5);
      state.bindingPocketCutoff = value;
      if (byId("setting-pocket-cutoff")) byId("setting-pocket-cutoff").value = String(value);
      onSettingsChanged();
    });
  }

  /* What the fields say, into the page's state; the Viewer asks for the
   * same with settings() as it starts, and is told of each change. */
  function readSettings() {
    state.bindingPocketCutoff = clampFloat(
      parseFloat(byId("setting-pocket-cutoff")?.value),
      3,
      15,
      5
    );
    const ligand = (byId("setting-ligand-resname")?.value || "").trim().toUpperCase();
    state.ligandResname = ligand || null;
    chartHistorySamples = clampInt(
      parseInt(byId("setting-chart-history")?.value, 10),
      60,
      5000,
      600
    );
    const customRunName = (byId("setting-run-name")?.value || "").trim();
    if (customRunName) state.runTitle = customRunName;
  }

  function currentSettings() {
    return {
      ligand: state.ligandResname,
      pocketCutoff: state.bindingPocketCutoff,
      chartHistory: chartHistorySamples,
      proteinRepresentation: byId("setting-protein-rep")?.value || "cartoon",
      ground: ["white", "dark"].includes(byId("setting-ground")?.value)
        ? byId("setting-ground").value : "scheme",
      showWater: !!byId("setting-show-water")?.checked,
      showIons: !!byId("setting-show-ions")?.checked,
      spin: !!byId("setting-spin")?.checked,
      preserveCamera: byId("setting-preserve-camera")?.checked !== false,
    };
  }

  function onSettingsChanged() {
    readSettings();
    renderTopBar(state.status, state.health);
    emit("settings-updated", currentSettings());
    schedulePoll(0);
  }

  /* ------------------------------------------------------------------ */
  /* Polling                                                             */
  /* ------------------------------------------------------------------ */
  async function fetchJSON(url) {
    const response = await fetch(url, {
      cache: "no-store",
      headers: {"X-FastMDX": "live"},
    });
    if (!response.ok) throw new Error(`HTTP ${response.status} on ${url}`);
    return response.json();
  }

  /* The server says when the study changes (`GET /api/stream`), and the
   * page asks for what it draws then, rather than every few seconds
   * whether or not anything did. The poll stays as a slow fall-back while
   * the stream is open, and as it was when the stream is not: an older
   * browser, or a network that cuts long connections. */
  const STREAMING_POLL_MS = 30000;
  let changeTimer = null;
  let polling = false;
  let changedWhilePolling = false;

  function openChangeStream() {
    if (typeof window.EventSource !== "function") return;
    let source;
    try {
      source = new EventSource("/api/stream");
    } catch (error) {
      return;
    }
    // Each connection's first event is sent at once, for a page that
    // reconnected having missed a change. A page that has asked since the
    // connection opened, or is asking now, has missed nothing.
    let first = true;
    let openedAt = Date.now();
    // Counted as streaming only once an event has arrived: a proxy that
    // holds a response until it ends would open the connection and deliver
    // nothing, and the page would then wait on its slow poll.
    source.addEventListener("open", () => {
      first = true;
      openedAt = Date.now();
    });
    source.addEventListener("error", () => { state.streaming = false; });
    source.addEventListener("change", () => {
      state.streaming = true;
      const wasFirst = first;
      first = false;
      if (state.paused) return;
      if (wasFirst && (polling || state.lastUpdateMs >= openedAt)) return;
      if (polling) {
        // Asked again once this answer is in, so a change made while it
        // was being read is not left to the slow poll.
        changedWhilePolling = true;
        return;
      }
      // Several files change together as a run writes; one request set.
      if (changeTimer) window.clearTimeout(changeTimer);
      changeTimer = window.setTimeout(() => {
        changeTimer = null;
        pollNow().catch((error) => console.warn("dashboard poll error", error));
      }, 150);
    });
    state.changeStream = source;
  }

  function nextPollMs() {
    return state.streaming ? Math.max(state.pollIntervalMs, STREAMING_POLL_MS) : state.pollIntervalMs;
  }

  function schedulePoll(delayMs) {
    if (state.refreshTimer) window.clearTimeout(state.refreshTimer);
    state.refreshTimer = window.setTimeout(async () => {
      if (!state.paused) {
        try {
          await pollNow();
        } catch (error) {
          console.warn("dashboard poll error", error);
        }
      }
      schedulePoll(nextPollMs());
    }, Math.max(0, delayMs));
  }

  async function pollNow() {
    polling = true;
    try {
      await pollOnce();
    } finally {
      polling = false;
      if (changedWhilePolling) {
        changedWhilePolling = false;
        window.setTimeout(() => {
          pollNow().catch((error) => console.warn("dashboard poll error", error));
        }, 0);
      }
    }
  }

  async function pollOnce() {
    const names = ["app", "status", "metrics", "events", "results", "structure", "playback"];
    const urls = [
      "/api/app-state", "/api/status", `/api/metrics?most=${chartHistorySamples}`,
      "/api/events", "/api/results",
      "/api/structure-info", "/api/frames-info",
    ];
    const settled = await Promise.allSettled(urls.map(fetchJSON));
    let successes = 0;

    settled.forEach((result, index) => {
      const name = names[index];
      if (result.status === "rejected") {
        state.apiErrors[name] = String(result.reason || "request failed");
        console.warn(`dashboard ${name} request failed`, result.reason);
        return;
      }
      delete state.apiErrors[name];
      successes += 1;
      const payload = result.value;
      if (name === "app") safeApply(name, () => applyAppState(payload));
      if (name === "status") safeApply(name, () => applyStatus(payload));
      if (name === "metrics") safeApply(name, () => applyMetrics(payload.metrics || []));
      if (name === "events") safeApply(name, () => renderEvents(payload.events || []));
      if (name === "results") safeApply(name, () => applyResults(payload));
      if (name === "structure") safeApply(name, () => applyStructure(payload));
      if (name === "playback") safeApply(name, () => applyPlayback(payload));
    });

    if (successes) {
      state.lastUpdateMs = Date.now();
      updateRefreshedAt();
    }
    updateConnectionState();
  }

  function applyAppState(payload) {
    const activeRun = payload?.active_run || "";
    const previousRun = state.appState?.active_run || "";
    const firstAppState = Object.keys(state.appState || {}).length === 0;
    const runChanged = firstAppState || previousRun !== activeRun;
    state.appState = payload || {};
    // The demo study, offered where this installation carries it.
    const demo = byId("open-demo");
    if (demo) {
      demo.hidden = !state.appState.demo_available;
      // Fetched the first time it is opened: said, with its weight.
      const weight = Number(state.appState.demo_to_fetch || 0);
      if (!demo.disabled) {
        demo.textContent = weight
          ? `Open the demo study (fetches ${Math.round(weight / 1e6)} MB once)`
          : "Open the demo study";
      }
    }
    if (runChanged) {
      resetRunDependentState();
      emit("run-changed", {previousRun, activeRun, first: firstAppState});
    }
    if (activeRun) state.outputDir = activeRun;
    // Why there is no live record follows the study's state, which changes
    // without a status arriving: a run of the study starting or ending.
    if (!state.status || Object.keys(state.status).length === 0) renderLiveProgress({});

    if (!state.initialRouteResolved) {
      state.initialRouteResolved = true;
      const requested = location.hash.replace(/^#/, "");
      if (requested && state.pages.includes(requested)) navigate(requested);
      else if (activeRun) navigate("overview");
      else navigate("run");
    }

    if (!activeRun) {
      setText("topbar-run-id", "workspace");
      setText("topbar-run-title", "No active study");
      setText("topbar-stage", "configure a simulation");
      setText("sidebar-run-name", "No active study");
      setText("sidebar-platform", "—");
      showTheStudyFolder();
    }
    // Sections that only have content once a run exists are dimmed until one
    // does, so a fresh workspace points at the builder instead of offering
    // five empty views.
    $$("[data-requires-run]").forEach((element) => {
      element.classList.toggle("nav-link-pending", !activeRun);
      // Named by its own word, to a screen reader and on hover: folded to
      // a strip of icons, its word is hidden, and once a study was open the
      // links of its pages were left with no name at all.
      const word = element.dataset.word
        || (element.dataset.word = (element.querySelector("span")?.textContent || "").trim());
      if (word) element.setAttribute("aria-label", word);
      element.setAttribute("title", activeRun ? word
        : `${word}: available once a study is open in this workspace`);
    });
    emit("app-state", payload || {});
  }

  /* The study's folder in the study menu, offered only where a study is
   * open: with none, the runtime's root is a name never created. */
  function showTheStudyFolder() {
    setTextWithTooltip("sidebar-output-folder", state.outputDir || "—");
    // Its folder under the card, by name, shortened in the middle to fit.
    const folder = byId("study-folder");
    if (folder) {
      folder.hidden = !state.outputDir;
      const name = String(state.outputDir || "").replace(/[\\/]+$/, "").split(/[\\/]/).pop() || "";
      folder.dataset.name = name;
      folder.title = state.outputDir ? `${state.outputDir}\nClick to copy the path` : "";
      fitTheFolderName();
    }
    ["sidebar-output-folder", "open-output"].forEach((id) => {
      const node = byId(id);
      if (!node) return;
      // The whole path is copied from beside the card, not printed here.
      node.hidden = id === "sidebar-output-folder" || !state.outputDir;
      // Its label, and the rule above that.
      const label = id === "sidebar-output-folder" ? node.previousElementSibling : null;
      if (label) label.hidden = !state.outputDir;
      const rule = label ? label.previousElementSibling : null;
      if (rule && rule.classList.contains("study-menu-divider")) rule.hidden = !state.outputDir;
    });
    // The study's own words (preferences.js) are a study's, so none either.
    $$("#study-menu [data-study-field]").forEach((item) => { item.hidden = !state.outputDir; });
  }

  /* A name in the width there is, with its middle given up first:
   * fastmdxplora_output_20260919_021313 and its siblings differ at their
   * ends. The most of it that fits is found by halving, a handful of
   * measurements rather than one per letter. */
  function fitInTheMiddle(shown, name, room) {
    shown.textContent = name;
    if (!(room > 0) || name.length < 4 || shown.scrollWidth <= room + 0.5) return;
    const cut = (keep) => {
      const head = Math.ceil(keep / 2);
      return `${name.slice(0, head)}\u2026${name.slice(name.length - (keep - head))}`;
    };
    let fits = 2, over = name.length;
    while (over - fits > 1) {
      const keep = Math.floor((fits + over) / 2);
      shown.textContent = cut(keep);
      if (shown.scrollWidth <= room + 0.5) fits = keep; else over = keep;
    }
    shown.textContent = cut(fits);
  }

  function fitTheFolderName() {
    const folder = byId("study-folder");
    const shown = byId("study-folder-name");
    if (!folder || !shown || folder.hidden) return;
    const room = shown.getBoundingClientRect().width || folder.clientWidth - 16;
    fitInTheMiddle(shown, folder.dataset.name || "", room);
  }

  /* Copied however the browser allows: the clipboard is offered only on
   * a secure page, and a GUI served to another machine over http is not.
   * Said either way. */
  async function copyTheFolder() {
    const shown = byId("study-folder-name");
    if (!state.outputDir || !shown) return;
    const copied = await copyText(state.outputDir);
    shown.textContent = copied ? "Path copied" : "Could not copy; the path is in the tooltip";
    setTimeout(fitTheFolderName, copied ? 1400 : 2600);
  }

  function resetRunDependentState() {
    state.outputDir = "";
    // Another study's cards can have the same paths as this one's.
    state.analysisSectionsKey = "";
    state.status = {};
    state.health = {};
    state.results = {};
    state.structureInfo = null;
    state.setupManifest = {};
    state.simManifest = {};
    state.metrics = [];
    state.stages = [];
    state.phases = [];
    state.playbackAvailable = false;
    state.playbackFrames = 0;
    state.playbackTotalFrames = 0;
    state.playbackFrameTimes = [];
    state.playbackSignature = null;
    if (window.FastMDXCharts) window.FastMDXCharts.update([]);
    renderLiveProgress({});
    renderStructureTab({valid: false, reason: "missing"});
    renderLigandTab({});
    renderSimulationTab({});
  }

  function safeApply(name, callback) {
    try {
      callback();
    } catch (error) {
      state.apiErrors[`render-${name}`] = String(error);
      console.error(`dashboard ${name} renderer failed`, error);
      showToast(`${humanise(name)} data could not be rendered. See the browser console.`, "warning");
    }
  }

  function updateConnectionState() {
    // The sidebar row is labelled Connection, and used to be filled with the
    // run's own status. So a server that had gone away left the last thing a
    // live run said about itself frozen on the page: killing `fastmdx gui`
    // and leaving it running looked identical. The run's state is reported
    // by the topbar and the health panel; this row answers a different
    // question -- whether the page is still being told anything.
    const ageSeconds = state.lastUpdateMs ? (Date.now() - state.lastUpdateMs) / 1000 : Infinity;
    const requestFailed = Object.keys(state.apiErrors).length > 0;
    const connection = !requestFailed
      ? "live"
      : (ageSeconds > 30 ? "lost" : "reconnecting");

    setText("sidebar-connection-state", connection);
    byId("sidebar-connection-state")?.setAttribute("data-connection", connection);
    setClassName(
      "sidebar-status-dot",
      `status-dot ${connection === "live" ? "status-dot-live" : "status-dot-stale"}`
    );
    // Added and removed, not added and left. A dot that never comes back
    // reports a fault that has passed.
    const topbarDot = byId("topbar-status-dot");
    if (topbarDot) topbarDot.classList.toggle("status-dot-stale", connection !== "live");
  }

  function updateRefreshedAt() {
    const date = new Date(state.lastUpdateMs);
    const use24 = byId("setting-time-format")?.value !== "12h";
    // A time is enough for "updated at": the page is open today.
    const text = use24
      ? date.toLocaleTimeString([], {hour12: false})
      : date.toLocaleTimeString();
    setText("refreshed-at", text);
  }

  /* ------------------------------------------------------------------ */
  /* Status                                                              */
  /* ------------------------------------------------------------------ */

  /* Panels that only mean something for a phase this run does not include.
   * An analysis of a trajectory that already exists has no simulation to be
   * healthy or unhealthy, and a card announcing that the simulation became
   * numerically unstable is not merely useless there -- it is alarming, and
   * it is about a simulation that was never asked for. */
  function applyPhaseVisibility() {
    const phases = state.phases;
    if (!phases || !phases.length) {
      // Nothing said, so nothing is hidden.
      $$('[data-needs-phase]').forEach((element) => { element.hidden = false; });
      return;
    }
    $$('[data-needs-phase]').forEach((element) => {
      const needed = element.getAttribute("data-needs-phase");
      element.hidden = !phases.includes(needed);
    });
  }

  function applyStatus(payload) {
    if (!state.appState?.active_run) {
      state.status = {};
      state.health = {};
      state.stages = [];
      state.phases = [];
      renderTopBar({}, {});
      renderHero({});
      renderHealth({});
      renderStageTimeline({});
      renderLiveProgress({});
      emit("status-updated", {status: {}, health: {}});
      return;
    }
    const status = payload.status || {};
    const health = payload.health || {};
    state.status = status;
    state.health = health;
    state.times = payload.times || {};
    state.stages = Array.isArray(payload.stages) ? payload.stages : [];
    state.phases = Array.isArray(payload.phases) ? payload.phases : [];
    applyPhaseVisibility();
    renderTopBar(status, health);
    renderHero(status);
    renderHealth(health);
    renderStageTimeline(status);
    renderLiveProgress(status);
    emit("status-updated", {status, health, times: state.times});
  }

  /* The Overview's blocks in the order they are read, in the page itself,
   * so Tab goes as the eye does (a CSS order left the keyboard on the old
   * one). The results and the charts move; the run's card, which holds the
   * molecule's canvas, stays where it is. */
  function leadWith(body, lead) {
    const results = byId("overview-results"), panels = byId("live-panels");
    const run = byId("overview-run"), charts = byId("overview-charts");
    if (results && panels && results.parentElement === body && panels.parentElement === body) {
      const resultsFirst = !!(results.compareDocumentPosition(panels) & Node.DOCUMENT_POSITION_FOLLOWING);
      if (lead === "health" && resultsFirst) body.insertBefore(results, panels.nextSibling);
      if (lead !== "health" && !resultsFirst) body.insertBefore(results, panels);
    }
    if (run && charts && run.parentElement === charts.parentElement) {
      const chartsFirst = !!(charts.compareDocumentPosition(run) & Node.DOCUMENT_POSITION_FOLLOWING);
      if (lead === "health" && !chartsFirst) run.parentElement.insertBefore(charts, run);
      if (lead !== "health" && chartsFirst) run.parentElement.insertBefore(charts, run.nextSibling);
    }
  }

  function renderTopBar(status, health) {
    if (!state.appState?.active_run) {
      setClassName("topbar-status-dot", "status-dot status-dot-waiting");
      setText("topbar-status-text", "Ready");
      byId("sidebar-progress")?.setAttribute("data-run", "");
      setText("topbar-stage", "configure a simulation");
      setText("topbar-step", "—");
      setText("topbar-total", "—");
      setText("topbar-progress", "—");
      setText("topbar-eta", "—");
      setText("sidebar-platform", "—");
      setText("sidebar-output-folder", "—");
      setText("sidebar-run-name", "No active study");
      setText("topbar-run-id", "workspace");
      setText("topbar-run-title", "No active study");
      const body = byId("overview-body");
      if (body) { body.dataset.run = ""; body.dataset.health = ""; body.dataset.lead = ""; }
      return;
    }
    let statusName = String(health.state || status.status || "waiting").toLowerCase();
    // A study with no live record (finished before it kept one, or a study
    // of several runs) is said from the phases it recorded.
    const quiet = ["unknown", "waiting", "stale", ""];
    if (quiet.includes(statusName) && quiet.includes(String(status.status || "").toLowerCase())) {
      const phases = (state.results?.phases || []).map((p) => String(p.status || "").toLowerCase());
      if (phases.some((s) => ["error", "failed"].includes(s))) statusName = "failed";
      else if (phases.some((s) => ["ok", "complete", "completed"].includes(s))
        && phases.every((s) => ["ok", "complete", "completed", "skipped", "not run", "not-run"].includes(s))) {
        statusName = "completed";
      }
    }
    // Pausing the browser's updates means something only while there is
    // something to update: a finished study offered "Pause" beside its
    // results. Kept while paused, so there is a way to resume.
    const pause = byId("pause-toggle");
    if (pause) {
      const live = ["running", "starting"].includes(String(status.status || "").toLowerCase())
        || !!state.appState?.process_running;
      pause.hidden = !live && !state.paused;
    }
    // The progress card is for a run going on, or one that stopped short:
    // where it is or where it stopped, and for the second, what would fix
    // it. A finished study's card above says it finished.
    const run = runState(status, statusName);
    // The card's word is where the run is (running, stopped, failed);
    // for a finished run, the health verdict's.
    const word = run === "running" ? String(status.status || "running") : (run || statusName);
    setText("topbar-status-text", statusWord(word));
    // Its dot: live while it runs, green once it finished well, red when
    // it failed, grey otherwise (stopped, waiting, no recent word).
    const finished = !run && ["ok", "completed", "complete"].includes(statusName);
    setClassName("topbar-status-dot", `status-dot ${run === "running" ? "status-dot-live"
      : run === "failed" ? "status-dot-error"
      : run === "stopped" || run === "interrupted" ? "status-dot-waiting"
      : finished ? "status-dot-completed" : stateDotClass(statusName)}`);
    byId("sidebar-progress")?.setAttribute("data-run", run);
    // The Overview leads with what it determined once the run has ended
    // well, and with its health and charts while it runs or once it has
    // stopped short: what happened first (overview.js, the CSS).
    const body = byId("overview-body");
    if (body) {
      body.dataset.run = run === "running" ? "running" : "ended";
      body.dataset.health = statusName;
      body.dataset.lead = run === "running" || run === "failed" || run === "stopped"
        || run === "interrupted" ? "health" : "results";
      leadWith(body, body.dataset.lead);
    }
    // A run that has not reported a stage has not reached one; it is
    // starting. It is not a run whose stage cannot be determined.
    const stage = stageWord(status.stage) || "Starting";
    // Said stage first, "Simulation failed", as the phases are said.
    setText("topbar-stage", run === "stopped" ? `${stage} stopped`
      : run === "failed" ? `${stage} failed`
      : run === "interrupted" ? (status.last_update_timestamp
        ? `${stage}, last update ${formatWhen(status.last_update_timestamp)}` : stage)
      : stage);
    setText("topbar-step", valueOrDash(status.current_step));
    setText("topbar-total", valueOrDash(status.total_planned_steps));
    const pct = progressPercent(status);
    setText("topbar-progress", pct != null ? `${pct.toFixed(1)}%` : "—");
    // A run that has ended has no time left, whatever its last step: a
    // stopped run kept saying "5m 2s".
    setText("topbar-eta", run === "running" ? computeETA(status) : "\u2014");

    const platform = status.platform || state.simManifest.platform || "";
    setText("sidebar-platform", platform || "\u2014");
    // No platform recorded: the card's line ends at the state.
    const said = byId("sidebar-platform");
    if (said) {
      said.hidden = !platform;
      if (said.previousElementSibling) said.previousElementSibling.hidden = !platform;
    }
    showTheStudyFolder();
    byId("open-output")?.setAttribute(
      "title", state.outputDir ? `Open ${state.outputDir}` : "Open output folder"
    );
    setTextWithTooltip("sidebar-run-name", state.runTitle);

    state.runId = status.system_id || state.results?.system?.system || state.runId;
    setTextWithTooltip("topbar-run-id", state.runId || "system");
    // The study's name is the system, not the folder it went into. The
    // server's run_title is the output folder's name, and once a run began
    // the title read fastmdxplora_output_20260919_021313 under the wordmark,
    // which says nothing a person did not already choose. A name set in
    // Display preferences still wins.
    const chosen = (byId("setting-run-name")?.value || "").trim();
    const name = chosen || studyName(state.runId) || state.runTitle;
    setTextWithTooltip("topbar-run-title", name);
    const title = byId("topbar-run-title");
    if (title && state.runId && name !== state.runId) title.title = state.runId;
  }

  /* The run's state in a word a person reads: the health verdict for a
   * finished run ("ok" read as a code), the status otherwise. */
  function statusWord(name) {
    const words = {
      ok: "Completed", completed: "Completed", complete: "Completed",
      running: "Running", starting: "Starting", paused: "Paused",
      waiting: "Waiting", stale: "No recent updates", warning: "Running, with a warning",
      failed: "Failed", error: "Failed", stopped: "Stopped", ready: "Ready",
      interrupted: "Interrupted",
    };
    const key = String(name || "").toLowerCase();
    return words[key] || (key ? key.charAt(0).toUpperCase() + key.slice(1) : "Ready");
  }

  /* Whether the progress card shows, and how: a run going on, one that
   * stopped, one that failed, or none (finished, or nothing open). */
  function runState(status, statusName) {
    const raw = String(status.status || "").toLowerCase();
    if (["failed", "error"].includes(raw) || ["failed", "error"].includes(statusName)) return "failed";
    if (raw === "stopped" || statusName === "stopped") return "stopped";
    // Ended without saying so (telemetry.status_as_it_stands): its record
    // still said it was going, weeks after the run had gone.
    if (raw === "interrupted" && !state.appState?.process_running) return "interrupted";
    if (["running", "starting", "paused"].includes(raw) || state.appState?.process_running) {
      return "running";
    }
    return "";
  }

  function stageWord(stage) {
    const words = {
      setup: "Setup", minimization: "Minimisation", minimisation: "Minimisation",
      nvt: "NVT", npt: "NPT", production: "Production", analysis: "Analysis",
      report: "Report", simulation: "Simulation",
    };
    const key = normaliseStage(stage);
    const said = stage ? String(stage) : "";
    return words[key] || said.charAt(0).toUpperCase() + said.slice(1);
  }

  /* A system given as a file is named by the file: the sidebar read
   * "/home/lab/studies/struct..." for a study of tri-ala.pdb. The whole path
   * stays on hover. */
  /* The system in four capital characters, as fastmdxplora.system_id
   * names it: a PDB entry as it is, otherwise the first four letters or
   * digits of its structure file's name. */
  function studyName(system) {
    const text = system == null ? "" : String(system).trim();
    if (!text) return "";
    if (/^[0-9][A-Za-z0-9]{3}$/.test(text)) return text.toUpperCase();
    if (/^\d+ systems$/.test(text)) return text;
    const file = text.split(/[\\/]/).filter(Boolean).pop() || text;
    const stem = file.replace(/(\.(pdb|ent|cif|mmcif|pdbx|bcif|gro|mol2|sdf|xml|prmtop|parm7|psf|top|xyz))?(\.(gz|bz2|xz|zip))?$/i, "") || file;
    const letters = stem.replace(/[^A-Za-z0-9]/g, "");
    return (letters || stem).slice(0, 4).toUpperCase();
  }

  function setTextWithTooltip(id, value) {
    // Long paths are truncated with an ellipsis, so keep the full value
    // reachable on hover rather than losing it.
    const element = byId(id);
    if (!element) return;
    const text = value == null ? "" : String(value);
    element.textContent = text;
    if (text) element.title = text; else element.removeAttribute("title");
  }

  function stateDotClass(value) {
    if (["ok", "live", "completed"].includes(value)) return "status-dot-live";
    if (["failed", "error", "critical"].includes(value)) return "status-dot-error";
    return "status-dot-stale";
  }

  function renderHero(status) {
    const card = byId("hero-card");
    if (card) card.setAttribute("data-state", String(status.status || "running").toLowerCase());
    setText("hero-status-text", humanise(status.status || status.stage || "waiting"));
    setText("hero-stage", status.stage || "\u2014");
    // Said of a run that is writing, and of one that has ended as it is.
    const writing = ["running", "starting"].includes(String(status.status || "").toLowerCase())
      || !!state.appState?.process_running;
    setText("overview-subtitle", writing
      ? "What the run is doing, and whether it is doing it well."
      : "What the run did, and how it went.");
    setText("overview-charts-title", writing ? "Live charts" : "Thermodynamics");
  }

  function renderHealth(health) {
    const stateName = String(health.state || "unknown").toLowerCase();
    byId("hero-health")?.setAttribute("data-state", stateName);
    // Large, the headline; the message beneath it at the size of prose. A
    // failure's message was the headline, and a paragraph with a path in it
    // was set in the card's largest type.
    setText("health-headline", health.headline || health.message || humanise(stateName));
    setProse("health-explanation", health.headline
      ? [health.message, health.explanation].filter(Boolean).join("\n\n")
      : (health.explanation || ""));
    setText("health-pill", stateName);
    byId("health-pill")?.setAttribute("data-state", stateName);

    const list = byId("health-list");
    if (list) {
      const items = Array.isArray(health.items) ? health.items.slice(0, 4) : [];
      list.innerHTML = items.map((item) => `
        <li data-state="${escapeAttr(item.severity || "ok")}">
          <strong>${escapeHTML(item.title || item.severity || "info")}</strong>
          <span class="muted small"> — ${escapeHTML(item.detail || "")}</span>
        </li>
      `).join("");
    }
  }

  /* Text as it was written: its paragraphs as paragraphs and its "  - "
   * lines as a list. A diagnosis's "What to try" list was set as one
   * paragraph, its items run together. Built as elements: what a run
   * recorded is data, never markup. */
  function setProse(id, text) {
    const host = byId(id);
    if (!host) return;
    const key = String(text || "");
    if (host.dataset.prose === key) return;
    host.dataset.prose = key;
    host.replaceChildren();
    key.split(/\n\s*\n/).forEach((block) => {
      const lines = block.split("\n").filter((line) => line.trim());
      if (!lines.length) return;
      let para = null;
      let list = null;
      lines.forEach((line) => {
        const item = /^\s*[-*]\s+(.*)$/.exec(line);
        if (item) {
          if (!list) { list = document.createElement("ul"); host.appendChild(list); }
          const li = document.createElement("li");
          li.textContent = item[1];
          list.appendChild(li);
          para = null;
        } else {
          list = null;
          if (!para) { para = document.createElement("p"); host.appendChild(para); }
          else para.appendChild(document.createTextNode(" "));
          para.appendChild(document.createTextNode(line.trim()));
        }
      });
    });
  }

  function renderStageTimeline(status) {
    const order = ["setup", "minimization", "nvt", "npt", "production", "analysis", "report"];
    const phaseMap = {};
    (state.results.phases || []).forEach((phase) => {
      phaseMap[String(phase.name || "").toLowerCase()] = String(phase.status || "").toLowerCase();
    });
    const liveStates = status?.stage_states && typeof status.stage_states === "object"
      ? status.stage_states : {};
    const current = normaliseStage(status.stage);
    const currentIndex = order.indexOf(current);
    const simulationDone = isPhaseDone(phaseMap.simulation);

    /* Only the stages this run can reach. An analysis of a trajectory that
     * already exists has no minimization to wait for, and a stage greyed out
     * forever reads exactly like a run that stalled. Where the server cannot
     * tell -- an older run, or one started outside the GUI -- every stage is
     * listed, because hiding one that turns out to run is the worse mistake. */
    const reachable = Array.isArray(state.stages) && state.stages.length
      ? state.stages : null;

    $$('.stage-step').forEach((element) => {
      const stage = element.getAttribute("data-stage");
      if (reachable && !reachable.includes(stage)) {
        element.hidden = true;
        return;
      }
      element.hidden = false;
      let stageState = phaseVisualState(liveStates[stage]);

      // Older/completed runs may not have the new live stage map, so retain
      // manifest-based fallback without overwriting a real current/failed state.
      if (stageState === "waiting") {
        if (stage === "setup") stageState = phaseVisualState(phaseMap.setup);
        if (["minimization", "nvt", "npt", "production"].includes(stage) && simulationDone) {
          stageState = "completed";
        }
        if (stage === "analysis") stageState = phaseVisualState(phaseMap.analysis);
        if (stage === "report") stageState = phaseVisualState(phaseMap.report);
      }

      if (currentIndex >= 0 && stageState === "waiting") {
        const index = order.indexOf(stage);
        if (index < currentIndex) stageState = "completed";
        if (index === currentIndex) stageState = "current";
      }
      if (stage === current && phaseVisualState(liveStates[stage]) === "current") {
        stageState = "current";
      }
      element.setAttribute("data-state", stageState);
    });
    // "Stage 5 of 7" in the progress card: the stage reached among those
    // this run can reach.
    const shown = $$('.sidebar-stages .stage-step').filter((element) => !element.hidden);
    const stateOf = (element) => element.getAttribute("data-state");
    let at = shown.findIndex((element) => ["current", "failed"].includes(stateOf(element)));
    if (at < 0) {
      shown.forEach((element, index) => { if (stateOf(element) === "completed") at = index; });
    }
    setText("sidebar-stage-count", at >= 0 && shown.length
      ? `Stage ${at + 1} of ${shown.length}` : "");
  }

  /* Why a study open here has no live record, said as what it is. Several
   * situations look the same to this page -- no status -- and they used to
   * get one message: a finished study of several runs, whose runs each keep
   * their own record and whose folder keeps none, was told "Waiting for the
   * simulation" and that setup was under way. With no study open, say so
   * and offer to start one. */
  function liveAbsence(app) {
    app = app || {};
    if (app.no_study_in) {
      const name = String(app.no_study_in).split(/[\\/]/).filter(Boolean).pop() || app.no_study_in;
      return {
        title: "No study here",
        body: `${name} holds no study: nothing in it was written by FastMDXplora. `
          + "Open one under All studies, or start one.",
        offerToStart: true,
      };
    }
    if (!app.active_run) {
      return {title: "Nothing running", body: "", offerToStart: true};
    }
    const runs = Array.isArray(app.runs) ? app.runs : null;
    if (runs) {
      const count = (s) => runs.filter((r) => r.state === s).length;
      const where = count("running")
        ? `${count("running")} running, ${count("completed")} of ${runs.length} completed.`
        : `${count("completed")} of ${runs.length} completed.`;
      return {
        title: `A study of ${runs.length} runs`,
        body: `${where} Each run keeps its own record of its simulation: view one from Runs in the sidebar.`,
        offerToStart: false,
      };
    }
    if (app.process_running) {
      return {
        title: "Waiting for the simulation",
        body: "Setup is under way. This page fills in once the simulation starts.",
        offerToStart: false,
      };
    }
    if (app.analysed_only) {
      return {
        title: "A trajectory analysed",
        body: "This study analysed a trajectory and ran no simulation, so there is no "
          + "record of a run to show. What its analyses determined is on the Analysis page.",
        offerToStart: false,
      };
    }
    return {
      title: "No live record",
      body: "This study kept no record of its simulation as it ran: it stopped before "
        + "the simulation began, or ran with simulation.live_telemetry off.",
      offerToStart: false,
    };
  }

  function renderLiveProgress(status) {
    // With no telemetry there is nothing for these panels to read, and
    // filling twelve fields with "not available" is an apparatus for reading
    // something that is not there. Say why once, and show nothing else.
    const nothing = !status || Object.keys(status).length === 0;
    const panels = document.getElementById("live-panels");
    const absent = document.getElementById("live-absent");
    if (panels) panels.hidden = nothing;
    if (absent) {
      absent.hidden = !nothing;
      const said = liveAbsence(state.appState);
      const title = document.getElementById("live-absent-title");
      const body = document.getElementById("live-absent-body");
      const actions = document.getElementById("live-absent-actions");
      if (title) title.textContent = said.title;
      if (body) body.textContent = said.body;
      if (actions) actions.hidden = !said.offerToStart;
    }
    if (nothing) return;

    const pct = progressPercent(status);
    setWidth("live-progress-fill", pct);
    setText("live-progress-pct", pct != null ? pct.toFixed(1) : "—");
    setText(
      "live-sim-time",
      status.simulation_time_completed_ns != null
        ? formatNumber(status.simulation_time_completed_ns, 3)
        : "—"
    );
    setText("live-stage-cell", status.stage || "starting");
    setText("live-step-cell", valueOrDash(status.current_step));
    setText("live-total-cell", valueOrDash(status.total_planned_steps));
    setText(
      "live-frames-cell",
      status.current_frame_count != null
        ? `${status.current_frame_count}${status.planned_frame_count != null ? ` / ${status.planned_frame_count}` : ""}`
        : "—"
    );
    const length = productionSaid(state.times || {}, status);
    setText("live-simtime-label", length.label);
    setText("live-simtime-cell", length.value);
    const note = byId("live-simtime-note");
    if (note) {
      note.textContent = length.note;
      note.hidden = !length.note;
    }
    /* While it runs, the time since it began; once it has stopped, its
     * phases' own times added, as the Wall time card gives them: counted
     * from the run's start, a study analysed again hours later read the
     * hours between. */
    const wall = state.times?.wall_s ?? status.elapsed_wall_time_s;
    setText("live-elapsed-cell", wall != null ? fmtDuration(wall) : "—");
    setText("live-eta-cell", runState(status, String(status.status || "").toLowerCase()) === "running"
      ? computeETA(status) : "\u2014");
    // Where the checkpoint is, from the study, and when the record was last
    // written, to the minute: the absolute path and the full date and time
    // were cut off in their cells. Both are kept whole in the cell's title.
    const checkpoint = status.current_checkpoint_path || "";
    setText("live-checkpoint-cell", checkpoint ? inTheStudy(checkpoint) : "\u2014");
    setTitle("live-checkpoint-cell", checkpoint);
    setText("live-lastupdate-cell", formatWhen(status.last_update_timestamp));
    setTitle("live-lastupdate-cell", formatTimestamp(status.last_update_timestamp));
    setText("live-card-step", valueOrDash(status.current_step));
  }

  /* ------------------------------------------------------------------ */
  /* Metrics and events                                                  */
  /* ------------------------------------------------------------------ */
  function applyMetrics(metrics) {
    state.metrics = thinned((Array.isArray(metrics) ? metrics : [])
      .map(normaliseMetricRow), chartHistorySamples);
    if (window.FastMDXCharts) window.FastMDXCharts.update(state.metrics);
    emit("metrics-updated", {metrics: state.metrics});
  }

  /* The record thinned evenly over the whole run to about `most` rows, its
   * first, its last and each change of stage kept, as the server thins it
   * (overview_view.thinned_metrics): the newest rows alone lost the
   * equilibration and the start of production. */
  function thinned(rows, most) {
    const n = rows.length;
    if (n <= most || most < 2) return rows;
    const keep = new Set();
    for (let i = 0; i < most; i += 1) keep.add(Math.round(i * (n - 1) / (most - 1)));
    for (let i = 1; i < n; i += 1) {
      if (String(rows[i].stage || "") !== String(rows[i - 1].stage || "")) {
        keep.add(i - 1);
        keep.add(i);
      }
    }
    return Array.from(keep).sort((a, b) => a - b).map((i) => rows[i]);
  }

  function normaliseMetricRow(row) {
    const output = Object.assign({}, row || {});
    const aliases = {
      potentialEnergy: "potential_energy",
      kineticEnergy: "kinetic_energy",
      totalEnergy: "total_energy",
      simulationSpeed: "speed",
      frame: "current_frame_count",
      frames: "current_frame_count",
    };
    Object.keys(aliases).forEach((key) => {
      if (output[aliases[key]] == null && output[key] != null) output[aliases[key]] = output[key];
    });
    return output;
  }

  function renderEvents(events) {
    const list = byId("events-list");
    if (!list) return;
    const items = (Array.isArray(events) ? events : []).slice(-50).reverse();
    list.innerHTML = items.length ? items.map((event) => `
      <li data-level="${escapeAttr(event.level || "info")}">
        <span class="level-badge">${escapeHTML(event.level || "info")}</span>
        <span class="event-message">${escapeHTML(event.message || "")}</span>
        <span class="event-time">${escapeHTML(formatEventTime(event.timestamp))}</span>
      </li>
    `).join("") : `
      <li data-level="info">
        <span class="level-badge">info</span>
        <span class="event-message">No telemetry events were recorded.</span>
        <span class="event-time"></span>
      </li>`;
  }

  /* ------------------------------------------------------------------ */
  /* Results / analyses / files                                          */
  /* ------------------------------------------------------------------ */
  function applyResults(payload) {
    state.results = payload || {};
    state.outputDir = payload.output_dir || state.outputDir;
    state.setupManifest = payload.setup || {};
    state.simManifest = payload.simulation || {};
    state.runTitle = (byId("setting-run-name")?.value || "").trim()
      || payload.run_title
      || state.runTitle;
    state.runId = payload.system?.system || state.runId;

    renderTopBar(state.status, state.health);
    renderStageTimeline(state.status);
    renderMethods(payload);
    renderAnalysisSections(payload);
    renderAnalysis(payload);
    renderSimulationTab(state.structureInfo || {});
    emit("results-updated", payload);
  }

  function renderAnalysisSections(payload) {
    // Categorised panels and quick actions, matching the generated report.
    // When the report phase has produced its curated charts these panels use
    // them; otherwise they fall back to each analysis's own figure.
    // No report links here. The markdown report, the slides, the bundle and
    // the analysis manifest are all deliverables of the report phase, and the
    // Report tab lists every one of them with its size and date. Repeating
    // four of them on this tab made this the place people looked, and the
    // Report tab the place they did not.
    const actionHost = byId("analysis-quick-actions");
    if (actionHost) {
      actionHost.innerHTML = "";
      actionHost.hidden = true;
    }

    const sections = Array.isArray(payload.analysis_sections) ? payload.analysis_sections : [];
    const host = byId("analysis-sections");
    const flatGrid = byId("analysis-grid");
    if (!host) return;
    state.figureProvenance = payload.figure_provenance || {};
    // Rendered again only when something in it changed. Every poll replaced
    // the cards, which reloaded each figure and would take a chart from
    // under the pointer reading it.
    const key = JSON.stringify([sections, state.figureProvenance]);
    if (key === state.analysisSectionsKey && host.childElementCount) return;
    state.analysisSectionsKey = key;
    window.FastMDXSeries?.forget();
    window.FastMDXFigures?.forget();
    const charted = new Set();
    const named = new Set();

    const sectionHtml = (section) => {
      const panels = Array.isArray(section.panels) ? section.panels : [];
      // Reuse the same card structure the flat grid uses so both routes
      // through this view look identical.
      const cards = panels.map((panel) => {
        // Show the figure the analysis produced. Those are drawn at
        // publication settings (6.5x4.2in, 300 dpi, 9-11pt type); the report's
        // compact restyled variant is smaller and would look different from
        // what opening the figure gives you.
        const figure = panel.original_href || panel.href;
        // An analysis's own figure (analysis/rmsd/rmsd.png) can also be
        // drawn from its numbers, once per analysis.
        const own = /(?:^|\/)analysis\/([a-z][a-z0-9_]*)\/\1\.png$/.exec(
          panel.original_source || panel.source || "");
        // A figure that is not a series (secondary structure, clusters,
        // a projection, the dihedrals) is plotted from its own numbers
        // where figure-chart.js knows it.
        const plotted = window.FastMDXFigures?.figureOf(panel.original_source || panel.source || "") || "";
        const series = own && !plotted && !charted.has(own[1]) ? own[1] : "";
        if (series) charted.add(series);
        // The first card of each analysis answers to its name, so an
        // Agent's answer that cites it can open it here.
        const folder = /(?:^|\/)analysis\/([a-z][a-z0-9_]*)\//.exec(
          panel.original_source || panel.source || "");
        const analysisName = folder && !named.has(folder[1]) ? folder[1] : "";
        if (analysisName) named.add(analysisName);
        const links = [
          `<a class="file-action" href="${escapeAttr(figure)}" target="_blank" rel="noopener">Open full size</a>`,
        ];
        if (series) {
          links.push('<a class="file-action" href="#" data-series-toggle hidden>Show the figure</a>');
        }
        if (plotted) {
          links.push('<a class="file-action" href="#" data-figure-toggle hidden>Show the figure</a>');
        }
        const made = folder ? provenanceChip(folder[1]) : "";
        return `
        <article class="analysis-card" data-state="complete"${analysisName ? ` data-analysis="${escapeAttr(analysisName)}"` : ""}>
          <div class="ac-header">
            <div class="ac-title">${escapeHTML(panel.title || "")}</div>
          </div>
          <div class="ac-frame"${series ? ` data-series="${escapeAttr(series)}"` : ""}${plotted ? ` data-figure="${escapeAttr(plotted)}"` : ""}><img src="${escapeAttr(figure)}" alt="${escapeAttr(panel.title || "")}" loading="lazy"></div>
          <div class="ac-body">${escapeHTML(panel.summary || "")}</div>
          <div class="ac-footer">${links.join("")}${made}</div>
          ${made ? '<div class="figure-provenance" hidden></div>' : ""}
        </article>`;
      }).join("");
      // What the page's filter matches against: the section's title and
      // theme, and its figures' titles and captions.
      const search = [section.title, section.theme,
        ...panels.map((panel) => `${panel.title || ""} ${panel.summary || ""}`)].join(" ");
      return `
        <section class="analysis-section" id="analysis-section-${escapeAttr(section.anchor || "")}"
                 data-search="${escapeAttr(search)}">
          <div class="analysis-section-heading">
            <h3 class="analysis-section-title">${escapeHTML(section.title || "")}</h3>
            <span class="analysis-section-count">${panels.length} figure${panels.length === 1 ? "" : "s"}</span>
          </div>
          <div class="analysis-grid">${cards}</div>
        </section>`;
    };
    // The sections under what they study (report_dashboard.ANALYSIS_THEMES),
    // in the order the server gives them, which is that table's order.
    const themes = [];
    sections.forEach((section) => {
      const theme = section.theme || "Other";
      const last = themes[themes.length - 1];
      if (last && last.theme === theme) last.sections.push(section);
      else themes.push({ theme, sections: [section] });
    });
    host.innerHTML = themes.map(({ theme, sections: members }) => `
      <div class="analysis-theme" data-theme="${escapeAttr(theme)}">
        <h2 class="analysis-theme-title">${escapeHTML(theme)}</h2>
        ${members.map(sectionHtml).join("")}
      </div>`).join("");

    window.FastMDXSeries?.hydrate(host);
    window.FastMDXFigures?.hydrate(host);
    listenForProvenance(host);
    window.FastMDXAnalysisPage?.sectionsRendered(host, sections);

    const haveSections = sections.length > 0;
    host.hidden = !haveSections;
    // The flat grid is the fallback for runs with no sectioned data; showing
    // both would list every figure twice.
    if (flatGrid) flatGrid.hidden = haveSections;
  }

  /* What made a figure: the release, when, from how many frames, with which
   * selection and options, and the command that draws it again. A chip on
   * each figure opens it; the numbers are the study's own records (the
   * analysis manifest, the analysis's options.json and the Manifest's
   * record of who produced the phase), gathered by figure_provenance.py. */
  function shortVersion(version) {
    return String(version || "").split("+")[0].replace(/\.dev\d+$/, "");
  }

  function provenanceChip(name, made = (state.figureProvenance || {})[name]) {
    if (!made) return "";
    const label = made.version ? `v${shortVersion(made.version)}` : "how it was made";
    return `<button type="button" class="figure-chip" data-provenance="${escapeAttr(name)}" aria-expanded="false" title="What made this figure, and how to make it again">${escapeHTML(label)}</button>`;
  }

  /* A comparison's overlay or trend: the runs it was plotted from, the
   * release that analysed each, and the release that plotted them, as the
   * comparison recorded them (comparison/figures.json). */
  function comparisonProvenanceHtml(made) {
    const code = (value) => `<code>${escapeHTML(value)}</code>`;
    const release = (version) => version
      ? `FastMDXplora ${code(version)}` : "a release that did not record itself";
    const rows = [];
    const row = (label, value) => rows.push(`<dt>${escapeHTML(label)}</dt><dd>${value}</dd>`);
    row("Plotted by", release(made.version) + (made.host ? ` on ${escapeHTML(made.host)}` : ""));
    if (made.made) row("When", escapeHTML(String(made.made).replace("T", " ").replace(/\+00:00$/, " UTC")));
    const unit = made.unit ? ` (${escapeHTML(made.unit)})` : "";
    row("Against", made.kind === "trend"
      ? `${code(made.against)}, each run's mean of ${escapeHTML(made.analysis)}${unit}`
      : `${made.against === "time" ? "time (ns)" : "frame"}, each run's ${escapeHTML(made.analysis)}${unit}`);
    const runs = Array.isArray(made.runs) ? made.runs : [];
    const items = runs.map((run) => {
      const what = made.kind === "trend"
        ? ` at ${escapeHTML(String(run.x))}: ${escapeHTML(run.said || String(run.mean))}, ${escapeHTML(run.over || "")}`
        : `: ${Number(run.frames || 0).toLocaleString("en-US")} frames`;
      return `<li>${escapeHTML(run.label || run.run_id || "a run")} ${code(run.path || "")}${what}; ` +
        `analysed by ${release(run.analysed_by)}</li>`;
    });
    const releases = new Set(runs.map((run) => run.analysed_by || ""));
    const mixed = releases.size > 1
      ? '<p class="figure-provenance-note">The runs were analysed by more than one release, ' +
        "so a difference between them may be a difference between releases.</p>"
      : "";
    return `<dl class="figure-provenance-rows">${rows.join("")}</dl>` +
      `<p class="figure-provenance-how">Plotted from ${runs.length} run${runs.length === 1 ? "" : "s"}:</p>` +
      `<ul class="figure-provenance-runs">${items.join("")}</ul>` + mixed;
  }

  function provenanceHtml(made) {
    if (made.kind === "overlay" || made.kind === "trend") return comparisonProvenanceHtml(made);
    const rows = [];
    const row = (label, value) => {
      if (value === null || value === undefined || value === "") return;
      rows.push(`<dt>${escapeHTML(label)}</dt><dd>${value}</dd>`);
    };
    const code = (value) => `<code>${escapeHTML(value)}</code>`;
    row("Made by", made.version
      ? `FastMDXplora ${code(made.version)}` + (made.host ? ` on ${escapeHTML(made.host)}` : "")
      : "a release that did not record itself");
    const packages = Object.entries(made.packages || {});
    if (packages.length) {
      row("With", packages.map(([name, version]) => `${escapeHTML(name)} ${code(version)}`).join(", "));
    }
    if (made.made) row("When", escapeHTML(String(made.made).replace("T", " ").replace(/\.\d+/, "")));
    const trajectory = Array.isArray(made.trajectory) ? made.trajectory.join(", ") : made.trajectory;
    if (trajectory) {
      const frames = made.frames ? `, ${Number(made.frames).toLocaleString("en-US")} frames` : "";
      const n = Number(made.stride);
      const nth = n % 10 === 1 && n % 100 !== 11 ? "st" : n % 10 === 2 && n % 100 !== 12 ? "nd"
        : n % 10 === 3 && n % 100 !== 13 ? "rd" : "th";
      const every = n > 1 ? `, every ${n}${nth} frame` : "";
      row("From", `${code(trajectory)}${escapeHTML(frames + every)}`);
    }
    if (made.selection) row("Selection", code(made.selection));
    const options = Object.entries(made.options || {});
    if (options.length) {
      row("Options", options.map(([key, value]) => code(`${key}: ${JSON.stringify(value)}`)).join(" "));
    }
    const WIDTH_SAID = { page: "the page (6.5 in)", single_column: "one column (89 mm)",
                         double_column: "two columns (183 mm)" };
    if (made.width) row("Plotted for", escapeHTML(WIDTH_SAID[made.width] || made.width));
    const again = made.command || made.config || "";
    const older = made.version && made.this_version && made.version !== made.this_version
      ? `<p class="figure-provenance-note">This is FastMDXplora ${code(made.this_version)}; ` +
        "run again here, the figure is plotted by this release rather than the one above.</p>"
      : "";
    const how = made.command
      ? "Plots it again, from the same frames and options, into a folder of its own:"
      : "A setting here has no flag, so this config plots it again (fastmdx explore --config):";
    // The same command at another width: a journal's column, or the page.
    const widths = Object.entries(made.at_widths || {});
    const choose = widths.length
      ? '<div class="figure-provenance-widths" role="group" aria-label="Plot it at">' +
        `<button type="button" class="file-action is-chosen" data-width="">as it is</button>` +
        widths.map(([width]) => `<button type="button" class="file-action" data-width="${escapeAttr(width)}">` +
          `${escapeHTML(WIDTH_SAID[width] || width)}</button>`).join("") + "</div>"
      : "";
    return `<dl class="figure-provenance-rows">${rows.join("")}</dl>` +
      (again
        ? `<p class="figure-provenance-how">${escapeHTML(how)}</p>` + choose +
          `<pre class="figure-provenance-command">${escapeHTML(again)}</pre>` +
          '<button type="button" class="line-btn figure-provenance-copy" aria-label="Copy the command" ' +
          'title="Copy the command">' + window.FastMDXIcons.svg("copy") + "</button>" + older
        : "");
  }

  /* A figure's chip and its panel sit together in an Analysis card, or,
   * on the Report page, in the block under the figure (report-page.js). */
  const FIGURE_HOLDER = ".analysis-card, .report-figure-made";

  function listenForProvenance(host, lookup = (name) => (state.figureProvenance || {})[name]) {
    if (!host || host.dataset.provenanceListens) return;
    host.dataset.provenanceListens = "1";
    host.addEventListener("click", (event) => {
      const chip = event.target.closest(".figure-chip");
      const copy = event.target.closest(".figure-provenance-copy");
      const width = event.target.closest(".figure-provenance-widths [data-width]");
      if (width) {
        const panel = width.closest(".figure-provenance");
        const card = width.closest(FIGURE_HOLDER);
        const name = card?.querySelector(".figure-chip")?.dataset.provenance;
        const made = lookup(name);
        const said = panel?.querySelector(".figure-provenance-command");
        if (!made || !said) return;
        said.textContent = width.dataset.width
          ? (made.at_widths || {})[width.dataset.width] || ""
          : (made.command || made.config || "");
        panel.querySelectorAll(".figure-provenance-widths [data-width]").forEach(
          (button) => button.classList.toggle("is-chosen", button === width));
        return;
      }
      if (chip) {
        const card = chip.closest(FIGURE_HOLDER);
        const panel = card && card.querySelector(".figure-provenance");
        const made = lookup(chip.dataset.provenance);
        if (!panel || !made) return;
        const open = panel.hidden;
        if (open) panel.innerHTML = provenanceHtml(made);
        panel.hidden = !open;
        chip.setAttribute("aria-expanded", String(open));
      } else if (copy) {
        const said = copy.parentElement.querySelector(".figure-provenance-command");
        if (!said) return;
        navigator.clipboard?.writeText(said.textContent).then(
          () => showToast("Copied."), () => showToast("Select the text to copy it."));
      }
    });
  }

  /* The methods paragraphs, asked for again only when what they are written
   * from may have changed: another study, a phase that moved, an analysis
   * that finished, a study that grew. The results arrive far more often. */
  let methodsKey = "";
  let methodsPlain = "";

  async function renderMethods(payload) {
    const card = byId("overview-methods-card");
    const host = byId("overview-methods-text");
    if (!card || !host) return;
    const time = (payload.summary || []).find((row) => row.label === "Production");
    const key = JSON.stringify([payload.output_dir || "", payload.phase_rows || [],
      (payload.analyses || []).length, time ? time.value : ""]);
    if (key === methodsKey) return;
    methodsKey = key;
    let said;
    try {
      said = await fetchJSON("/api/methods");
    } catch (error) {
      methodsKey = "";
      return;
    }
    if (key !== methodsKey) return;  // another study was opened meanwhile
    methodsPlain = said && said.ok ? String(said.plain || "") : "";
    // Rendered on the server by the report's own renderer, which keeps
    // raw markup as text.
    host.innerHTML = methodsPlain ? String(said.html || "") : "";
    card.hidden = !methodsPlain;
  }

  function renderAnalysis(payload) {
    const grid = byId("analysis-grid");
    const empty = byId("analysis-empty");
    if (!grid || !empty) return;

    const analyses = Array.isArray(payload.analyses) ? payload.analyses : [];
    const plots = Array.isArray(payload.plots) ? payload.plots : [];
    const cards = [];
    const usedPlotPaths = new Set();

    analyses.forEach((analysis) => {
      const plot = analysis.plot || null;
      if (plot?.path) usedPlotPaths.add(plot.path);
      cards.push(analysisCardHtml(analysis, plot));
    });
    plots.forEach((plot) => {
      if (usedPlotPaths.has(plot.path)) return;
      cards.push(analysisCardHtml({
        name: plot.title,
        title: plot.title,
        status: "complete",
        message: plot.category || "",
        artifacts: [plot],
      }, plot));
    });

    // Rendered again only when a card changed, as the sections are. Every poll
    // replaced the cards: each figure reloaded, the page's height changed
    // under the reader while it did, and a card an Agent's answer had just
    // scrolled to and marked lost its mark and could leave the view.
    const html = cards.join("");
    if (html !== state.analysisGridHtml || !grid.childElementCount) {
      grid.innerHTML = html;
      state.analysisGridHtml = html;
    }
    const svgBundle = byId("download-all-svg");
    const svgCount = Number(payload.svg_figure_count || 0);
    if (svgBundle) {
      svgBundle.hidden = svgCount < 1;
      svgBundle.href = payload.svg_bundle_href || "/analysis-figures-svg.zip";
      const fromReport = Number(payload.svg_report_count || 0);
      svgBundle.textContent = (svgCount === 1
        ? "Download SVG figure"
        : `Download all ${svgCount} SVG figures`)
        + (fromReport && fromReport < svgCount
          ? `, ${fromReport} of them the report's` : fromReport ? ", the report's" : "");
    }
    const count = analyses.length || plots.length;
    setText(
      "analysis-meta",
      count ? `${count} analysis output${count === 1 ? "" : "s"}` : "no analyses yet"
    );
    if (cards.length) empty.setAttribute("hidden", "");
    else empty.removeAttribute("hidden");
  }

  function analysisCardHtml(analysis, plot) {
    const status = String(analysis.status || "unknown").toLowerCase();
    const title = analysis.title || humanise(analysis.name || "Analysis");
    const artifacts = Array.isArray(analysis.artifacts) ? analysis.artifacts : [];
    const imageArtifact = artifacts.find((item) => /\.(png|jpe?g|gif)$/i.test(item?.path || item?.name || ""));
    const svgArtifact = artifacts.find((item) => /\.svg$/i.test(item?.path || item?.name || ""));
    const displayPlot = plot?.href ? plot : imageArtifact || svgArtifact || null;
    const vectorPlot = plot?.svg_href
      ? {href: plot.svg_href, download_href: plot.svg_download_href}
      : svgArtifact;
    const dataArtifact = artifacts.find((item) => !/\.(png|jpe?g|gif|svg)$/i.test(item?.path || item?.name || ""));
    const primary = dataArtifact || artifacts[0] || displayPlot;
    const image = displayPlot?.href
      ? `<div class="ac-frame"><img src="${escapeAttr(displayPlot.href)}" alt="${escapeAttr(title)}" loading="lazy"></div>`
      : `<div class="ac-frame ac-no-plot"><div class="muted">No saved figure file was found for this analysis. Re-run the analysis with this revised version to generate PNG and SVG figures.</div></div>`;
    const links = [];
    if (displayPlot?.href) {
      links.push(`<a class="file-action" href="${escapeAttr(displayPlot.href)}" target="_blank" rel="noopener">Open figure</a>`);
      if (!/\.svg(?:$|\?)/i.test(displayPlot.href)) {
        links.push(`<a class="file-action" href="${escapeAttr(displayPlot.download_href || `${displayPlot.href}${displayPlot.href.includes("?") ? "&" : "?"}download=1`)}" download>Download PNG</a>`);
      }
    }
    if (vectorPlot?.href) {
      links.push(`<a class="file-action" href="${escapeAttr(vectorPlot.download_href || vectorPlot.href)}" download>Download SVG</a>`);
    }
    if (primary?.href && primary?.href !== displayPlot?.href && primary?.href !== vectorPlot?.href) {
      links.push(`<a class="file-action" href="${escapeAttr(primary.href)}" target="_blank" rel="noopener">Open data</a>`);
    }
    return `
      <article class="analysis-card" data-state="${escapeAttr(status)}"${analysis.name ? ` data-analysis="${escapeAttr(analysis.name)}"` : ""}>
        <div class="ac-header">
          <div class="ac-title">${escapeHTML(title)}</div>
          <div class="ac-status">${escapeHTML(status)}</div>
        </div>
        ${image}
        <div class="ac-body">${escapeHTML(analysis.message || plot?.category || "")}</div>
        <div class="ac-footer">${links.join("") || '<span class="muted small">Artifacts will appear when available.</span>'}</div>
      </article>`;
  }

  /* The Files page is files-page.js's, rendered by the server
   * (gui/files_page.py): asked for when it is opened and when the study
   * changes while it is shown. */

  /* ------------------------------------------------------------------ */
  /* Structure and playback                                              */
  /* ------------------------------------------------------------------ */
  function applyStructure(info) {
    state.structureInfo = info || {};
    renderStructureTab(state.structureInfo);
    if (state.structureInfo.ligand_resnames?.length && !state.ligandResname) {
      state.ligandResname = state.structureInfo.ligand_resnames[0];
    }
    renderLigandTab(state.structureInfo);
    renderSimulationTab(state.structureInfo);
    emit("structure-updated", state.structureInfo);
  }

  function renderStructureTab(info) {
    const body = byId("structure-tab-tbody");
    if (!body) return;
    if (!info?.valid) {
      const reason = info?.reason ? ` (${humanise(info.reason)})` : "";
      body.innerHTML = `<tr><td colspan="2" class="muted">Structure metadata is not available${escapeHTML(reason)}.</td></tr>`;
      return;
    }
    body.innerHTML = `
      <tr><th>Protein chains</th><td>${escapeHTML(info.n_chains)}</td></tr>
      <tr><th>Protein residues</th><td>${escapeHTML(info.protein_residues)}</td></tr>
      <tr><th>Protein atoms</th><td>${escapeHTML(info.protein_atoms)}</td></tr>
      <tr><th>Ligands</th><td>${escapeHTML((info.ligand_resnames || []).join(", ") || "none")}</td></tr>
      <tr><th>Water</th><td>${escapeHTML(info.water_residues)}</td></tr>
      <tr><th>Ions</th><td>${escapeHTML(info.ions)}</td></tr>`;
  }

  function renderSimulationTab() {
    const body = byId("simulation-tab-tbody");
    if (!body) return;
    const setup = state.setupManifest || {};
    const simulation = state.simManifest || {};
    const status = state.status || {};
    body.innerHTML = `
      <tr><th>Force field</th><td>${escapeHTML(setup.force_field || status.force_field || "—")}</td></tr>
      <tr><th>Water model</th><td>${escapeHTML(setup.water_model || status.water_model || "—")}</td></tr>
      <tr><th>pH</th><td>${escapeHTML(setup.ph ?? "—")}</td></tr>
      <tr><th>Ion concentration</th><td>${escapeHTML(setup.ion_concentration_M != null ? `${setup.ion_concentration_M} M` : "—")}</td></tr>
      <tr><th>Temperature</th><td>${escapeHTML(firstPresent(status.target_temperature_K, simulation.temperature_K) != null ? `${firstPresent(status.target_temperature_K, simulation.temperature_K)} K` : "—")}</td></tr>
      <tr><th>Timestep</th><td>${escapeHTML(firstPresent(status.timestep_fs, simulation.timestep_fs) != null ? `${firstPresent(status.timestep_fs, simulation.timestep_fs)} fs` : "—")}</td></tr>
      <tr><th>Precision</th><td>${escapeHTML(precisionText(status, simulation))}</td></tr>
      <tr><th>Platform</th><td>${escapeHTML(status.platform || simulation.platform || "—")}</td></tr>`;
  }

  function renderLigandTab(info) {
    const tools = byId("ligand-tools");
    const meta = byId("ligand-meta");
    const body = byId("ligand-tab-tbody");
    if (!tools || !meta || !body) return;
    const instances = Array.isArray(info?.ligand_instances) ? info.ligand_instances : [];
    const instance = instances.find((item) => item.resname === state.ligandResname) || instances[0];
    if (!instance) {
      tools.hidden = true;
      meta.textContent = "—";
      body.innerHTML = '<tr><td colspan="2" class="muted">No ligand was detected.</td></tr>';
      return;
    }
    tools.hidden = false;
    const residueName = instance.resname || state.ligandResname;
    const atoms = info?.atoms_by_resname?.[residueName] ?? "—";
    state.ligandResname = residueName;
    meta.textContent = `${residueName} · chain ${instance.chain || "—"} · resi ${instance.resi || "—"} · ${atoms} atoms`;
    body.innerHTML = `
      <tr><th>Ligand</th><td>${escapeHTML(residueName)}</td></tr>
      <tr><th>Position in structure</th><td>${crystalPositionCell(info, residueName)}</td></tr>
      <tr><th>Position in simulation</th><td>${escapeHTML(
        `${instance.chain || "—"} ${instance.resi ?? "—"}`
      )}</td></tr>
      <tr><th>Atom count</th><td>${escapeHTML(atoms)}</td></tr>
      <tr><th>Contact residues</th><td>${contactResidueCell(info)}</td></tr>
      <tr><th>Pocket residues</th><td>Use “Show pocket residues” — everything within ${escapeHTML(state.bindingPocketCutoff)} Å</td></tr>
      <tr><th>H-bonds</th><td>${interactionCell(info, "hbonds")}</td></tr>
      <tr><th>Hydrophobic contacts</th><td>${interactionCell(info, "hydrophobic")}</td></tr>
      <tr><th>Salt bridges</th><td>${interactionCell(info, "salt_bridges")}</td></tr>`;
  }

  function crystalPositionCell(info, resname) {
    // From the structure the run started from, which is the numbering the
    // PDB entry uses and any other tool will expect. The row beneath gives
    // OpenMM's, which is what the viewer selects on -- both are true, of
    // different files, and which one you want depends on what you are doing.
    const positions = info?.crystal_positions?.[resname];
    if (!positions || !positions.length) return "—";
    return escapeHTML(positions.join(", "));
  }

  function contactResidueCell(info) {
    // Named, rather than an instruction to go and look. These are the
    // residues that met an interaction criterion, best observed first -- a
    // narrower set than the pocket button's, which takes every residue
    // within a distance cutoff whether or not anything was measured.
    const measured = info?.interactions;
    if (!measured?.analysed) return "Not analysed";
    const residues = measured.contact_residues || [];
    if (!residues.length) return "none observed";
    const shown = residues.slice(0, 5).join(", ");
    const rest = residues.length - 5;
    return escapeHTML(rest > 0 ? `${shown} +${rest} more` : shown);
  }

  function interactionCell(info, kind) {
    // Three different things, said differently: the analysis has not run, it
    // ran and found none of this kind, or it found some. All three used to
    // read "Requires analysis output" -- including on a run that had just
    // produced it.
    const measured = info?.interactions;
    if (!measured?.analysed) return "Not analysed";
    const entry = measured.kinds?.[kind];
    if (!entry || !entry.residues) return "none observed";
    // Residues, not atom pairs: a twelve-atom ligand against six residues
    // makes scores of pairs, and the pair count reads as though the ligand
    // were held by scores of separate things.
    const parts = [`${entry.residues} residue${entry.residues === 1 ? "" : "s"}`];
    if (entry.best_occupancy != null) {
      const pct = Math.round(entry.best_occupancy * 100);
      parts.push(entry.best_residue ? `best ${entry.best_residue} ${pct}%` : `best ${pct}%`);
    }
    if (entry.thinly_sampled) {
      // A residue whose every contact is thinly observed is a weaker claim
      // than the count alone suggests.
      parts.push(`${entry.thinly_sampled} thinly sampled`);
    }
    return escapeHTML(parts.join(" · "));
  }

  function applyPlayback(payload) {
    const previousSignature = state.playbackSignature;
    const signature = payload?.source_signature || payload?.compiled_at || null;
    const previousFrame = parseInt(byId("traj-slider")?.value || "0", 10) || 0;

    state.playbackAvailable = !!payload?.playback_available;
    state.playbackFrames = Number(payload?.n_frames_browser || 0);
    state.playbackTotalFrames = Number(payload?.n_frames_total || 0);
    state.playbackFrameTimes = Array.isArray(payload?.frame_times_ns) ? payload.frame_times_ns : [];
    state.playbackSignature = signature;

    const slider = byId("traj-slider");
    const row = byId("trajectory-row");
    if (!slider || !row) return;
    if (!state.playbackAvailable) {
      row.hidden = true;
      emit("playback-ready", payload || {});
      return;
    }

    const maxFrame = Math.max(0, state.playbackFrames - 1);
    const frame = Math.min(previousFrame, maxFrame);
    slider.min = "0";
    slider.max = String(maxFrame);
    // Do not reset the user's scrubber on every three-second API poll.
    slider.value = String(frame);
    setText("traj-current", String(frame));
    setText("traj-total", String(state.playbackFrames));
    setText(
      "traj-simtime",
      state.playbackFrameTimes[frame] != null
        ? formatNumber(state.playbackFrameTimes[frame], 3) : "—"
    );
    row.hidden = false;
    emit("playback-ready", Object.assign({}, payload, {
      source_changed: previousSignature !== null && previousSignature !== signature,
    }));
  }

  /* ------------------------------------------------------------------ */
  /* Helpers                                                             */
  /* ------------------------------------------------------------------ */
  function progressPercent(status) {
    if (Number.isFinite(Number(status.progress_percent))) {
      return clampFloat(Number(status.progress_percent), 0, 100, null);
    }
    if (status.current_step != null && status.total_planned_steps != null && Number(status.total_planned_steps) > 0) {
      return clampFloat(Number(status.current_step) / Number(status.total_planned_steps) * 100, 0, 100, null);
    }
    return null;
  }

  function fmtDuration(seconds) {
    const total = Math.max(0, Math.floor(Number(seconds) || 0));
    const hours = Math.floor(total / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    const secs = total % 60;
    return hours ? `${hours}h ${minutes}m` : `${minutes}m ${secs}s`;
  }

  /* Time left at the speed the run is stepping now: the steps left over
   * the steps a second of its recent samples. The run's whole time so far
   * includes building the system and the first, slow samples, so early in
   * a run it said 33 minutes with about six left. Past its steps (analysis,
   * report), or with no speed to go by, nothing is said. */
  function computeETA(status) {
    const step = Number(status.current_step);
    const total = Number(status.total_planned_steps);
    if (!(step > 0) || !(total > 0) || step >= total) return "\u2014";
    // Only from the steps the record saw taken: the run's whole time,
    // setup and minimisation in it, said 39 minutes where 5 were left.
    const rate = stepsPerSecond();
    return rate > 0 ? fmtDuration((total - step) / rate) : "\u2014";
  }

  function stepsPerSecond() {
    let samples = (state.metrics || []).map((row) => ({
      step: Number(row.step), at: Date.parse(row.timestamp || ""),
    })).filter((s) => s.step >= 0 && Number.isFinite(s.at));
    // From where the steps began (the last sample at step 0, minimisation's
    // end), and since the last long pause: a study carried on in pieces
    // has the time between them in its record, and its rate jumped.
    const began = samples.map((s) => s.step).lastIndexOf(0);
    if (began > 0) samples = samples.slice(began);
    let recent = samples.slice(-12);
    const gaps = recent.slice(1).map((s, i) => s.at - recent[i].at);
    const usual = gaps.slice().sort((a, b) => a - b)[Math.floor(gaps.length / 2)] || 0;
    const pause = gaps.reduce((at, gap, i) => (gap > Math.max(60000, 5 * usual) ? i + 1 : at), 0);
    recent = recent.slice(pause);
    if (recent.length < 2) return 0;
    const first = recent[0];
    const last = recent[recent.length - 1];
    const seconds = (last.at - first.at) / 1000;
    return seconds > 0 && last.step > first.step ? (last.step - first.step) / seconds : 0;
  }

  function normaliseStage(value) {
    const stage = String(value || "").toLowerCase();
    if (stage.includes("minim")) return "minimization";
    if (stage.includes("nvt")) return "nvt";
    if (stage.includes("npt")) return "npt";
    if (stage.includes("production")) return "production";
    if (stage.includes("analysis")) return "analysis";
    if (stage.includes("report")) return "report";
    if (stage.includes("setup") || stage.includes("loading")) return "setup";
    return stage;
  }

  function phaseVisualState(value) {
    const status = String(value || "").toLowerCase();
    if (["ok", "complete", "completed", "success", "succeeded"].includes(status)) return "completed";
    if (["error", "failed"].includes(status)) return "failed";
    if (["skipped", "not run"].includes(status)) return "skipped";
    if (["running", "active", "current"].includes(status)) return "current";
    return "waiting";
  }

  function isPhaseDone(value) {
    return ["ok", "complete", "completed", "success"].includes(String(value || "").toLowerCase());
  }

  /* A path inside the open study, from the study; any other as it is. The
   * run may have written it through another name for the same folder (on a
   * Mac, /var is /private/var), so the study's own folder name is looked for
   * too. */
  function inTheStudy(path) {
    const root = String(state.appState?.active_run || "").replace(/[\\/]+$/, "");
    const text = String(path).replace(/\\/g, "/");
    if (!root) return text;
    const whole = root.replace(/\\/g, "/");
    if (text.startsWith(whole + "/")) return text.slice(whole.length + 1);
    const name = whole.split("/").pop();
    const at = name ? text.lastIndexOf("/" + name + "/") : -1;
    return at >= 0 ? text.slice(at + name.length + 2) : text;
  }

  function setTitle(id, title) {
    const element = byId(id);
    if (!element) return;
    if (title) element.title = title; else element.removeAttribute("title");
  }

  /* A moment as short as it can be read: the time today, the day and time
   * this year, the date otherwise. */
  function formatWhen(value) {
    if (!value) return "\u2014";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    const now = new Date();
    const time = {hour: "2-digit", minute: "2-digit", hour12: !uses24Hours()};
    if (date.toDateString() === now.toDateString()) return date.toLocaleTimeString([], time);
    if (date.getFullYear() === now.getFullYear()) {
      return date.toLocaleString([], {day: "numeric", month: "short", ...time});
    }
    return date.toLocaleDateString([], {day: "numeric", month: "short", year: "numeric"});
  }

  function formatTimestamp(value) {
    if (!value) return "—";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
  }

  /* Settings' Time choice, which the log's times and the last update now
   * follow as the sidebar's refresh time did: they were 24-hour whatever
   * was chosen. */
  function uses24Hours() {
    return byId("setting-time-format")?.value !== "12h";
  }

  function formatEventTime(value) {
    if (!value) return "";
    const date = new Date(value);
    return Number.isNaN(date.getTime())
      ? String(value)
      : date.toLocaleTimeString([], {hour12: !uses24Hours()});
  }

  /* A simulated length in the unit it reads in: picoseconds under one
   * nanosecond, nanoseconds from there, as simulated_time.say_length. */
  function sayLength(ns) {
    const value = Number(ns);
    if (ns == null || !Number.isFinite(value)) return "—";
    if (Math.abs(value) < 1) {
      return `${String(Number((value * 1000).toFixed(1)))} ps`;
    }
    return `${String(Number(value.toFixed(4)))} ns`;
  }

  /* How long the study has sampled: its production, which the analyses
   * average, with its equilibration beside it. The live record's own time
   * has every stage in it ("0.11 ns" for 100 ps of production after 10 of
   * equilibration); a study recorded before the plan was kept says that
   * total, and that it includes equilibration. */
  function productionSaid(times, status) {
    const running = ["running", "starting", "paused"].includes(String(status.status || "").toLowerCase());
    const planned = times.production_planned_ns;
    const done = times.production_ns;
    if (done == null && !times.equilibrating) {
      const total = times.simulated_ns ?? status.simulation_time_completed_ns;
      return {label: "Simulated", value: sayLength(total),
        note: total != null ? "equilibration included" : ""};
    }
    if (times.equilibrating) {
      const stage = String(times.stage || "").toUpperCase();
      const where = ["NVT", "NPT"].includes(stage) ? `equilibrating (${stage})` : "equilibrating";
      const eq = times.equilibration_ns != null && times.equilibration_planned_ns
        ? `, ${sayLength(times.equilibration_ns)} of ${sayLength(times.equilibration_planned_ns)}` : "";
      return {label: "Production",
        value: planned != null ? `0 of ${sayLength(planned)}` : sayLength(0),
        note: where + eq};
    }
    if (times.ended_in) {
      // Ended before production began: how far its equilibration got, not
      // the equilibration it planned, said as done.
      const words = {setup: "setup", loading: "setup", minimization: "minimisation",
                     nvt: "NVT equilibration", npt: "NPT equilibration"};
      const eqPlanned = times.equilibration_planned_ns;
      return {label: "Production",
        value: planned != null ? `0 of ${sayLength(planned)}` : sayLength(0),
        note: `ended in ${words[times.ended_in] || "its preparation"}` + (eqPlanned
          ? `, ${sayLength(times.equilibration_ns || 0)} of the ${sayLength(eqPlanned)} of equilibration planned`
          : "")};
    }
    const value = running && planned != null && done < planned
      ? `${sayLength(done)} of ${sayLength(planned)}` : sayLength(done);
    const eqTotal = times.equilibration_planned_ns;
    let note = "";
    if ((times.pieces || 1) > 1) {
      note = `in ${times.pieces} pieces`;
    } else if (eqTotal) {
      const parts = [["nvt_ns", "NVT"], ["npt_ns", "NPT"]]
        .filter(([key]) => times[key]).map(([key, name]) => `${sayLength(times[key])} ${name}`);
      note = `after ${sayLength(eqTotal)} of equilibration` + (parts.length > 1 ? ` (${parts.join(", ")})` : "");
    }
    return {label: "Production", value, note};
  }

  function formatNumber(value, digits) {
    const number = Number(value);
    return Number.isFinite(number) ? number.toFixed(digits) : String(value ?? "—");
  }

  function firstPresent(...values) {
    return values.find((value) => value !== null && value !== undefined && value !== "");
  }

  function precisionText(status, simulation) {
    const value = firstPresent(status?.precision, simulation?.precision);
    if (value === undefined) return "—";
    // `Precision` is a property of the CUDA/OpenCL/HIP platforms only. Where
    // the platform has no such setting the requested value was carried but
    // never applied, and printing it on its own would claim otherwise.
    if (status?.precision_applied === false) {
      const platform = status?.platform || simulation?.platform;
      return platform ? `${value} (requested; unused on ${platform})` : `${value} (requested)`;
    }
    return String(value);
  }

  function valueOrDash(value) {
    return value !== null && value !== undefined && value !== "" ? String(value) : "—";
  }

  function setText(id, value) {
    const element = byId(id);
    if (element) element.textContent = value == null ? "" : String(value);
  }

  function setClassName(id, value) {
    const element = byId(id);
    if (element) element.className = value;
  }

  function setWidth(id, value) {
    const element = byId(id);
    if (element) element.style.width = value == null ? "0%" : `${Math.max(0, Math.min(100, value))}%`;
  }

  function humanise(value) {
    return String(value || "")
      .replace(/[_-]+/g, " ")
      .replace(/\b\w/g, (character) => character.toUpperCase());
  }

  async function copyText(text) {
    if (!text) return false;
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
        return true;
      }
      const textarea = document.createElement("textarea");
      textarea.value = text;
      textarea.style.position = "fixed";
      textarea.style.opacity = "0";
      document.body.appendChild(textarea);
      textarea.select();
      const ok = document.execCommand("copy");
      textarea.remove();
      return ok;
    } catch (error) {
      return false;
    }
  }

  function showToast(message, kind) {
    let toast = byId("dashboard-toast");
    if (!toast) {
      toast = document.createElement("div");
      toast.id = "dashboard-toast";
      toast.className = "dashboard-toast";
      toast.setAttribute("role", "status");
      document.body.appendChild(toast);
    }
    toast.textContent = message;
    toast.setAttribute("data-kind", kind || "ok");
    toast.classList.add("show");
    window.clearTimeout(showToast.timer);
    showToast.timer = window.setTimeout(() => toast.classList.remove("show"), 3500);
  }

  function escapeHTML(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, (character) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    })[character]);
  }

  function escapeAttr(value) {
    return escapeHTML(value);
  }

  function clampInt(value, low, high, fallback) {
    const number = parseInt(value, 10);
    if (!Number.isFinite(number)) return fallback;
    return Math.max(low, Math.min(high, number));
  }

  function clampFloat(value, low, high, fallback) {
    const number = Number(value);
    if (!Number.isFinite(number)) return fallback;
    return Math.max(low, Math.min(high, number));
  }

  /* Open the Analysis page on one analysis's figure: the one an Agent's
   * answer cited. Its cards may still be arriving: results are rendered
   * when every request of a poll has answered, which on a busy machine can
   * take longer than the six seconds this used to wait. The card is looked
   * for whenever results are rendered and every quarter second besides,
   * while the Analysis page stays open, for up to a minute. */
  const CITED_LOOK_MS = 60000;

  function showAnalysis(name) {
    navigate("analysis");
    const wanted = String(name || "");
    const started = Date.now();
    let timer = null;
    const finish = () => {
      clearInterval(timer);
      window.removeEventListener("dashboard:results-updated", look);
    };
    function look() {
      if (state.activePage !== "analysis" || Date.now() - started > CITED_LOOK_MS) {
        finish();
        return;
      }
      const card = $$(`.page[data-page="analysis"] .analysis-card[data-analysis]`)
        .find((element) => element.getAttribute("data-analysis") === wanted
          && element.offsetParent !== null);
      if (!card) return;
      finish();
      // Which figure the page was opened on, kept after the mark fades.
      const pageEl = card.closest(".page");
      pageEl.dataset.cited = wanted;
      card.scrollIntoView({block: "center", behavior: "smooth"});
      card.classList.add("is-cited");
      setTimeout(() => card.classList.remove("is-cited"), 2400);
      keepInView(card, pageEl);
    }
    window.addEventListener("dashboard:results-updated", look);
    timer = setInterval(look, 250);
    look();
  }

  /* The figures and charts around a card load after it is scrolled to,
   * and as they take their height the card moved: opened from an answer,
   * the RMSD card ended a whole card above the view. For a few seconds,
   * while the page's content changes size, it is brought back, unless the
   * reader has scrolled, clicked or pressed a key in the meantime. */
  const KEEP_IN_VIEW_MS = 5000;

  function keepInView(card, pageEl) {
    if (typeof ResizeObserver !== "function") return;
    const column = document.querySelector(".main");
    const reader = ["wheel", "touchstart", "keydown", "mousedown"];
    let done = false;
    const observer = new ResizeObserver(() => {
      if (done || !card.isConnected) return;
      card.scrollIntoView({block: "center"});
    });
    const stop = () => {
      if (done) return;
      done = true;
      observer.disconnect();
      reader.forEach((name) => window.removeEventListener(name, stop, true));
    };
    reader.forEach((name) => window.addEventListener(name, stop, true));
    observer.observe(pageEl);
    if (column) Array.from(column.children).forEach((child) => observer.observe(child));
    Array.from(pageEl.querySelectorAll(".analysis-card")).forEach((each) => observer.observe(each));
    setTimeout(stop, KEEP_IN_VIEW_MS);
  }

  function emit(name, detail) {
    window.dispatchEvent(new CustomEvent(`dashboard:${name}`, {detail}));
  }

  window.FastMDXDashboard = {
    get state() { return JSON.parse(JSON.stringify(state)); },
    navigate,
    showAnalysis,
    applyStatus,
    applyAppState,
    applyMetrics,
    applyStructure,
    applyPlayback,
    applyResults,
    on(eventName, handler) {
      window.addEventListener(`dashboard:${eventName}`, (event) => handler(event.detail));
    },
    /* A figure's chip, for the Report page's figures: the chip for a
     * record, and the listener that opens it, given how to find a record
     * by its analysis's name. */
    /* The settings as the fields say them, as "settings-updated" sends. */
    settings: () => currentSettings(),
    /* The page's notice, seen and read out (icons.js says a copy in it). */
    toast: (message, kind) => showToast(message, kind),
    figureChip: (name, made) => provenanceChip(name, made),
    listenForFigureChips: (host, lookup) => listenForProvenance(host, lookup),
    /* A name shortened in its middle to the width given (studies.js). */
    fitInTheMiddle,
    systemId: studyName,
  };
}());
