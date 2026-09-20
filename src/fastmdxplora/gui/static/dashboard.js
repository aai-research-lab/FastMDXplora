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
  }

  /* ------------------------------------------------------------------ */
  /* Navigation                                                          */
  /* ------------------------------------------------------------------ */
  function wireNavigation() {
    state.pages = $$('.page')
      .map((element) => element.getAttribute("data-page"))
      .filter(Boolean);
    // The BibTeX entry is there to be taken, so make taking it one click.
    const citeCopy = document.getElementById("cite-copy");
    if (citeCopy) {
      citeCopy.addEventListener("click", async () => {
        const entry = document.getElementById("cite-bibtex");
        if (!entry) return;
        try {
          await navigator.clipboard.writeText(entry.textContent.trim());
          citeCopy.textContent = "Copied";
        } catch (err) {
          // Clipboard access needs a secure context, which http://127.0.0.1
          // is but a remote http:// host is not. Say so rather than
          // pretending it worked.
          citeCopy.textContent = "Select and copy";
        }
        setTimeout(() => { citeCopy.textContent = "Copy"; }, 2000);
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
      if (state.pages.includes(page)) navigate(page, {updateHash: false});
    });
  }

  function navigate(page, options) {
    const opts = options || {};
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
    byId("open-output")?.addEventListener("click", async () => {
      try {
        const payload = await fetchJSON("/api/open-output");
        if (payload.opened) {
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

  function wireInfoTabs() {
    $$('.info-tab').forEach((tab) => {
      tab.addEventListener("click", () => {
        const name = tab.getAttribute("data-tab");
        $$('.info-tab').forEach((item) => item.classList.toggle("active", item === tab));
        $$('.info-pane').forEach((pane) => {
          const active = pane.getAttribute("data-tab") === name;
          pane.hidden = !active;
          pane.classList.toggle("active", active);
        });
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
    const ids = [
      "setting-protein-rep", "setting-ligand-rep", "setting-background",
      "setting-show-water", "setting-show-ions", "setting-spin", "setting-fog",
      "setting-preserve-camera", "setting-refresh-seconds", "setting-chart-history",
      "setting-compact", "setting-reduced-motion", "setting-advanced-metrics",
      "setting-time-format", "setting-run-name", "setting-ligand-resname",
      "setting-pocket-cutoff", "setting-scinote",
    ];
    ids.forEach((id) => byId(id)?.addEventListener("change", onSettingsChanged));
    byId("pocket-cutoff")?.addEventListener("change", (event) => {
      const value = clampFloat(parseFloat(event.target.value), 3, 15, 5);
      state.bindingPocketCutoff = value;
      if (byId("setting-pocket-cutoff")) byId("setting-pocket-cutoff").value = String(value);
      onSettingsChanged();
    });
  }

  function onSettingsChanged() {
    state.pollIntervalMs = clampInt(
      parseFloat(byId("setting-refresh-seconds")?.value) * 1000,
      1000,
      60000,
      3000
    );
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

    document.body.classList.toggle("compact-mode", !!byId("setting-compact")?.checked);
    document.body.classList.toggle("reduced-motion", !!byId("setting-reduced-motion")?.checked);
    document.body.classList.toggle(
      "advanced-metrics",
      !!byId("setting-advanced-metrics")?.checked
    );

    renderTopBar(state.status, state.health);
    emit("settings-updated", {
      ligand: state.ligandResname,
      pocketCutoff: state.bindingPocketCutoff,
      chartHistory: chartHistorySamples,
      proteinRepresentation: byId("setting-protein-rep")?.value || "cartoon",
      ligandRepresentation: byId("setting-ligand-rep")?.value || "sticks",
      background: byId("setting-background")?.value || "matte-black",
      showWater: !!byId("setting-show-water")?.checked,
      showIons: !!byId("setting-show-ions")?.checked,
      spin: !!byId("setting-spin")?.checked,
      fog: !!byId("setting-fog")?.checked,
      preserveCamera: byId("setting-preserve-camera")?.checked !== false,
    });
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
      schedulePoll(state.pollIntervalMs);
    }, Math.max(0, delayMs));
  }

  async function pollNow() {
    const names = ["app", "status", "metrics", "events", "results", "structure", "playback"];
    const urls = [
      "/api/app-state", "/api/status", "/api/metrics", "/api/events", "/api/results",
      "/api/structure-info", "/api/playback-info",
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
    if (runChanged) {
      resetRunDependentState();
      emit("run-changed", {previousRun, activeRun});
    }
    if (activeRun) state.outputDir = activeRun;

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
    }
    // Sections that only have content once a run exists are dimmed until one
    // does, so a fresh workspace points at the builder instead of offering
    // five empty views.
    $$("[data-requires-run]").forEach((element) => {
      element.classList.toggle("nav-link-pending", !activeRun);
      if (activeRun) {
        element.removeAttribute("title");
      } else {
        element.setAttribute("title", "Available once an exploration exists in this workspace");
      }
    });
    emit("app-state", payload || {});
  }

  function resetRunDependentState() {
    state.outputDir = "";
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
    renderRunSummary({});
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
    const text = use24
      ? `${date.toLocaleDateString()} ${date.toLocaleTimeString([], {hour12: false})}`
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
    state.stages = Array.isArray(payload.stages) ? payload.stages : [];
    state.phases = Array.isArray(payload.phases) ? payload.phases : [];
    applyPhaseVisibility();
    renderTopBar(status, health);
    renderHero(status);
    renderHealth(health);
    renderStageTimeline(status);
    renderLiveProgress(status);
    emit("status-updated", {status, health});
  }

  function renderTopBar(status, health) {
    if (!state.appState?.active_run) {
      setClassName("topbar-status-dot", "status-dot status-dot-waiting");
      setText("topbar-status-text", "ready");
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
      return;
    }
    const statusName = String(health.state || status.status || "waiting").toLowerCase();
    const dotClass = stateDotClass(statusName);
    setClassName("topbar-status-dot", `status-dot ${dotClass}`);
    setText("topbar-status-text", statusName);
    // A run that has not reported a stage has not reached one; it is
    // starting. It is not a run whose stage cannot be determined.
    setText("topbar-stage", status.stage || "starting");
    setText("topbar-step", valueOrDash(status.current_step));
    setText("topbar-total", valueOrDash(status.total_planned_steps));
    const pct = progressPercent(status);
    setText("topbar-progress", pct != null ? `${pct.toFixed(1)}%` : "—");
    setText("topbar-eta", computeETA(status));

    setText("sidebar-platform", status.platform || state.simManifest.platform || "—");
    setTextWithTooltip("sidebar-output-folder", state.outputDir || "—");
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
    setTextWithTooltip("topbar-run-title", chosen || state.runId || state.runTitle);
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
    setText("hero-stage", status.stage || "—");
  }

  function renderHealth(health) {
    const stateName = String(health.state || "unknown").toLowerCase();
    byId("hero-health")?.setAttribute("data-state", stateName);
    setText("health-headline", health.message || humanise(stateName));
    setText("health-explanation", health.explanation || "");
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
      /* Two different situations look the same to this page -- no
       * status yet -- and used to get one message, about a setting that
       * is on by default. With a run under way the simulation has not
       * started: say so, and hide the "start one" buttons. With no run,
       * say that, and show them. */
      const hasRun = Boolean(state.appState && state.appState.active_run);
      const title = document.getElementById("live-absent-title");
      const body = document.getElementById("live-absent-body");
      const actions = document.getElementById("live-absent-actions");
      if (title) title.textContent = hasRun ? "Waiting for the simulation" : "Nothing running";
      if (body) body.textContent = hasRun
        ? "Setup is under way. This page fills in once the simulation starts."
        : "";
      if (actions) actions.hidden = hasRun;
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
    setText(
      "live-simtime-cell",
      status.simulation_time_completed_ns != null
        ? `${formatNumber(status.simulation_time_completed_ns, 6)} ns`
        : "—"
    );
    setText(
      "live-elapsed-cell",
      status.elapsed_wall_time_s != null ? fmtDuration(status.elapsed_wall_time_s) : "—"
    );
    setText("live-eta-cell", computeETA(status));
    setText("live-checkpoint-cell", status.current_checkpoint_path || "—");
    setText("live-lastupdate-cell", formatTimestamp(status.last_update_timestamp));
    setText("live-card-step", valueOrDash(status.current_step));
  }

  /* ------------------------------------------------------------------ */
  /* Metrics and events                                                  */
  /* ------------------------------------------------------------------ */
  function applyMetrics(metrics) {
    state.metrics = (Array.isArray(metrics) ? metrics : [])
      .map(normaliseMetricRow)
      .slice(-chartHistorySamples);
    if (window.FastMDXCharts) window.FastMDXCharts.update(state.metrics);
    emit("metrics-updated", {metrics: state.metrics});
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
    renderReportPanels(payload);
    renderAnalysisSections(payload);
    renderAnalysis(payload);
    renderFiles(payload);
    renderRunSummary(payload);
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

    host.innerHTML = sections.map((section) => {
      const panels = Array.isArray(section.panels) ? section.panels : [];
      // Reuse the same card structure the flat grid uses so both routes
      // through this view look identical.
      const cards = panels.map((panel) => {
        // Show the figure the analysis produced. Those are drawn at
        // publication settings (6.5x4.2in, 300 dpi, 9-11pt type); the report's
        // compact restyled variant is smaller and would look different from
        // what opening the figure gives you.
        const figure = panel.original_href || panel.href;
        const links = [
          `<a class="file-action" href="${escapeAttr(figure)}" target="_blank" rel="noopener">Open full size</a>`,
        ];
        return `
        <article class="analysis-card" data-state="complete">
          <div class="ac-header">
            <div class="ac-title">${escapeHTML(panel.title || "")}</div>
            <div class="ac-status">${escapeHTML(section.title || "")}</div>
          </div>
          <div class="ac-frame"><img src="${escapeAttr(figure)}" alt="${escapeAttr(panel.title || "")}" loading="lazy"></div>
          <div class="ac-body">${escapeHTML(panel.summary || "")}</div>
          <div class="ac-footer">${links.join("")}</div>
        </article>`;
      }).join("");
      return `
        <section class="analysis-section">
          <div class="analysis-section-heading">
            <h2 class="analysis-section-title">${escapeHTML(section.title || "")}</h2>
            <span class="analysis-section-count">${panels.length} figure${panels.length === 1 ? "" : "s"}</span>
          </div>
          <div class="analysis-grid">${cards}</div>
        </section>`;
    }).join("");

    const haveSections = sections.length > 0;
    host.hidden = !haveSections;
    // The flat grid is the fallback for runs with no sectioned data; showing
    // both would list every figure twice.
    if (flatGrid) flatGrid.hidden = haveSections;
  }

  function renderReportPanels(payload) {
    // Summary cards, phase progress, and trajectory statistics come from the
    // same computation the generated report uses, so both surfaces agree.
    const cards = Array.isArray(payload.summary_cards) ? payload.summary_cards : [];
    const cardHost = byId("overview-summary-cards");
    if (cardHost) {
      cardHost.innerHTML = cards.map((card) => {
        const value = card.value || "—";
        // Paths get monospace at a smaller size; everything else stays large.
        const kind = /[\\/]/.test(value) ? "path" : "text";
        return `
        <div class="metric-card">
          <div class="metric-card-label">${escapeHTML(card.label || "")}</div>
          <div class="metric-card-value mono" data-kind="${kind}" title="${escapeAttr(value)}">${escapeHTML(value)}</div>
          <div class="metric-card-unit" title="${escapeAttr(card.detail || "")}">${escapeHTML(card.detail || "")}</div>
        </div>`;
      }).join("");
      cardHost.hidden = cards.length === 0;
    }

    const phases = Array.isArray(payload.phase_rows) ? payload.phase_rows : [];
    const phaseHost = byId("overview-phase-rows");
    const phaseCard = byId("overview-phase-card");
    if (phaseHost) {
      phaseHost.innerHTML = phases.map((row) => `
        <tr>
          <td>${escapeHTML(row.name || "")}</td>
          <td><span class="stage-pill">${escapeHTML(row.status || "")}</span></td>
          <td class="muted">${escapeHTML(row.detail || "")}</td>
        </tr>`).join("");
    }
    if (phaseCard) phaseCard.hidden = phases.length === 0;

    const stats = Array.isArray(payload.metric_rows) ? payload.metric_rows : [];
    const statHost = byId("overview-stat-rows");
    const statCard = byId("overview-stats-card");
    if (statHost) {
      statHost.innerHTML = stats.map((row) => `
        <tr>
          <td>${escapeHTML(row.metric || "")}</td>
          <td class="mono">${escapeHTML(row.average || "—")}</td>
          <td class="mono">${escapeHTML(row.stddev || "—")}</td>
          <td class="muted">${escapeHTML(row.unit || "")}</td>
        </tr>`).join("");
    }
    if (statCard) statCard.hidden = stats.length === 0;
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

    grid.innerHTML = cards.join("");
    const svgBundle = byId("download-all-svg");
    const svgCount = Number(payload.svg_figure_count || 0);
    if (svgBundle) {
      svgBundle.hidden = svgCount < 1;
      svgBundle.href = payload.svg_bundle_href || "/analysis-figures-svg.zip";
      svgBundle.textContent = svgCount === 1
        ? "Download SVG figure"
        : `Download all ${svgCount} SVG figures`;
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
      <article class="analysis-card" data-state="${escapeAttr(status)}">
        <div class="ac-header">
          <div class="ac-title">${escapeHTML(title)}</div>
          <div class="ac-status">${escapeHTML(status)}</div>
        </div>
        ${image}
        <div class="ac-body">${escapeHTML(analysis.message || plot?.category || "")}</div>
        <div class="ac-footer">${links.join("") || '<span class="muted small">Artifacts will appear when available.</span>'}</div>
      </article>`;
  }

  const FILE_GROUPS = [
    ["deliverables", "Reports and deliverables"],
    ["simulation", "Simulation data"],
    ["analysis", "Analysis data"],
    ["figures", "Figures"],
    ["record", "Run record"],
  ];

  function renderFiles(payload) {
    const artifacts = Array.isArray(payload.artifacts) ? payload.artifacts : [];
    const host = byId("file-groups");
    if (!host) return;

    const cards = FILE_GROUPS.map(([key, title]) => {
      const files = artifacts.filter((item) => (item.group || "record") === key);
      if (!files.length) return "";
      // The run record is mostly scratch and manifests -- worth keeping, not
      // worth putting between somebody and the trajectory.
      const folded = key === "record";
      const bytes = files.reduce((total, item) => total + (parseInt(item.size, 10) || 0), 0);
      const rows = files.map(fileRowHtml).join("");
      const body = folded
        ? `<details class="file-fold"><summary>${files.length} files, ${escapeHTML(humanSize(bytes))}</summary>${rows}</details>`
        : rows;
      return `
        <div class="card">
          <div class="card-header">
            <h2 class="card-title">${escapeHTML(title)}</h2>
            <span class="muted small mono">${files.length} · ${escapeHTML(humanSize(bytes))}</span>
          </div>
          <div class="files-list">${body}</div>
        </div>`;
    }).filter(Boolean);

    host.innerHTML = cards.length
      ? cards.join("")
      : '<div class="card"><div class="muted small">This run has not written any files yet.</div></div>';
    wireCopyActions();
  }

  function fileRowHtml(file) {
    // What the file is, above where it lives. A reader deciding whether to
    // download 85 MB should not have to know that `production.dcd` is the
    // trajectory, or that of sixteen files called `options.json` this one
    // belongs to rmsd.
    const within = String(file.path || "").split("/").slice(1).join("/");
    const title = file.label || within || file.name || "Artifact";
    const subtitle = within && within !== title ? within : "";
    const size = file.size != null ? humanSize(parseInt(file.size, 10)) : "—";
    const mtime = file.mtime != null
      ? new Date(parseFloat(file.mtime) * 1000).toLocaleString()
      : "—";
    const href = file.href || `/artifacts/${encodeURI(file.path || "")}`;
    const downloadHref = file.download_href || `${href}${href.includes("?") ? "&" : "?"}download=1`;
    return `
      <div class="file-row" data-path="${escapeAttr(file.absolute_path || file.path || "")}">
        <div class="file-title" title="${escapeAttr(file.path || "")}">${escapeHTML(title)}</div>
        ${subtitle ? `<div class="file-subtitle mono">${escapeHTML(subtitle)}</div>` : ""}
        <div class="file-meta">
          <span>${escapeHTML(size)}</span>
          <span class="muted">${escapeHTML(mtime)}</span>
          <div class="file-actions">
            <a class="file-action" href="${escapeAttr(href)}" target="_blank" rel="noopener">Open</a>
            <a class="file-action" href="${escapeAttr(downloadHref)}" download>Download</a>
            <button class="file-action" type="button" data-copy-path>Copy path</button>
          </div>
        </div>
      </div>`;
  }

  function wireCopyActions() {
    $$('[data-copy-path]').forEach((button) => {
      button.addEventListener("click", async () => {
        const row = button.closest(".file-row");
        const path = row?.getAttribute("data-path") || "";
        const copied = await copyText(path);
        showToast(copied ? "File path copied." : "Could not copy the file path.", copied ? "ok" : "warning");
      });
    });
  }

  function renderRunSummary(payload) {
    const card = byId("summary-card");
    if (!card) return;
    const analyses = Array.isArray(payload.analyses) ? payload.analyses : [];
    const plots = Array.isArray(payload.plots) ? payload.plots : [];
    const reports = Array.isArray(payload.reports) ? payload.reports : [];
    const hasResults = payload.has_report || payload.has_analysis || reports.length || analyses.length;
    card.hidden = !hasResults;
    if (!hasResults) return;
    const stats = [
      {label: "Analyses", value: String(analyses.length)},
      {label: "Figures", value: String(plots.length)},
      {label: "Report formats", value: String(reports.length)},
      {label: "Result bundle", value: reports.some((item) => item.path?.endsWith(".zip")) ? "ready" : "—"},
    ];
    const grid = byId("summary-grid");
    if (grid) grid.innerHTML = stats.map((item) => `
      <div class="summary-stat">
        <span class="summary-stat-label">${escapeHTML(item.label)}</span>
        <span class="summary-stat-value">${escapeHTML(item.value)}</span>
      </div>`).join("");
  }

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

  function computeETA(status) {
    const step = Number(status.current_step);
    const total = Number(status.total_planned_steps);
    const elapsed = Number(status.elapsed_wall_time_s);
    if (!(step > 0) || !(total > 0) || !(elapsed >= 0) || step >= total) return step >= total ? "0m 0s" : "—";
    return fmtDuration(elapsed * (total / step - 1));
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

  function formatTimestamp(value) {
    if (!value) return "—";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
  }

  function formatEventTime(value) {
    if (!value) return "";
    const date = new Date(value);
    return Number.isNaN(date.getTime())
      ? String(value)
      : date.toLocaleTimeString([], {hour12: false});
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

  function humanSize(bytes) {
    if (!Number.isFinite(bytes)) return "—";
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
    if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(2)} MB`;
    return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
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

  function emit(name, detail) {
    window.dispatchEvent(new CustomEvent(`dashboard:${name}`, {detail}));
  }

  window.FastMDXDashboard = {
    get state() { return JSON.parse(JSON.stringify(state)); },
    navigate,
    applyStatus,
    applyAppState,
    applyMetrics,
    applyStructure,
    applyPlayback,
    applyResults,
    on(eventName, handler) {
      window.addEventListener(`dashboard:${eventName}`, (event) => handler(event.detail));
    },
  };
}());
