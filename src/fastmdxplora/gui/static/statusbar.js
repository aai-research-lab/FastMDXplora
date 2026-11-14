/* The status bar: a strip under every page, as an editor's (VS Code's).
 *
 * Left to right: this computer and its GPUs; the run going here, with its
 * stage, percent and time left (the ids dashboard.js has always written
 * them to), or where it stopped and what would fix it; a run going in
 * another study of this workspace; each job on another machine as its last
 * recorded status says it (nothing is asked of the machine); then the open
 * study's problems, the Agent's mode, and the page's own updates. Each
 * item opens its page. The AI model is never said here: it is in the
 * Agent's settings (user, 10-18). A hosted service's Intel level would be
 * said beside the mode; FastMDXplora has no levels file yet, so no Intel
 * item is shown, and the model is never put in its place. It is a strip, not a panel: nothing in it scrolls,
 * resizes or takes tabs (the rule of 10-12 against panels under the page).
 *
 * Read from: /api/status-bar (this computer; status_bar.py) once a minute,
 * the Agent's mode as chosen in its settings (#agent-mode), /api/remote/machines (the jobs, as recorded) every half minute,
 * the app's state and status as the page polls them, and the Problems
 * tab's count (problems.js). A route refused (a service's guests, a page
 * served beyond this computer) leaves its item out.
 */
(function () {
  "use strict";

  var MACHINE_EVERY_MS = 60000;
  var JOBS_EVERY_MS = 30000;
  var app = null;
  var machineInfo = null;
  var hasRecordedJobs = false;

  function byId(id) { return document.getElementById(id); }
  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }
  function icon(name) {
    return window.FastMDXIcons ? window.FastMDXIcons.svg(name, "sb-icon") : "";
  }
  function setItem(node, iconName, text, title) {
    if (!node) return;
    var html = icon(iconName) + '<span class="sb-text"></span>';
    if (node.dataset.icon !== iconName) {
      node.innerHTML = html;
      node.dataset.icon = iconName;
    }
    var span = node.querySelector(".sb-text");
    if (span.textContent !== text) span.textContent = text;
    if (title !== undefined && node.title !== title) node.title = title;
  }
  function getJSON(path) {
    return fetch(path, { cache: "no-store" }).then(function (r) {
      if (!r.ok) throw new Error(String(r.status));
      return r.json();
    });
  }
  function gb(mb) { return (mb / 1024).toFixed(mb >= 10240 ? 0 : 1) + " GB"; }

  /* ------------------------------------------------------------------ */
  /* This computer                                                       */
  /* ------------------------------------------------------------------ */

  function machineSaid(m) {
    var gpus = m.gpus || [];
    if (gpus.length) {
      var first = gpus[0].name.replace(/^NVIDIA\s+/, "");
      return m.host + " · " + first + (gpus.length > 1 ? " +" + (gpus.length - 1) : "");
    }
    return m.host + " · " + (m.platform && m.platform !== "CPU" ? m.platform : "CPU");
  }

  function machineTitle(m) {
    var lines = ["This computer: " + m.host + (m.system ? " (" + m.system + ")" : "")
      + (m.cpus ? ", " + m.cpus + " CPU cores" : "")];
    (m.gpus || []).forEach(function (g) {
      lines.push("GPU " + g.index + ": " + g.name + ", " + gb(g.free_mb) + " free of "
        + gb(g.total_mb) + (g.busy_pct != null ? ", " + g.busy_pct + "% busy" : ""));
    });
    if (!(m.gpus || []).length) {
      lines.push(m.gpus_read ? "No NVIDIA GPU read here." : "No nvidia-smi here, so no GPU read.");
    }
    if (m.platform) lines.push("A study here runs on OpenMM's " + m.platform + " platform.");
    if (hasRecordedJobs) lines.push("Open Remote jobs");
    return lines.join("\n");
  }

  function loadMachine() {
    getJSON("/api/status-bar").then(function (data) {
      var machine = byId("sb-machine");
      if (!data || !data.ok) {
        machineInfo = null;
        if (machine) machine.hidden = true;
        return;
      }
      if (machine && data.machine) {
        machineInfo = data.machine;
        setItem(machine, "machine", machineSaid(data.machine), machineTitle(data.machine));
        machine.hidden = false;
      }
    }).catch(function () {
      machineInfo = null;
      var machine = byId("sb-machine");
      if (machine) machine.hidden = true;
    });
  }

  /* ------------------------------------------------------------------ */
  /* The Agent's mode                                                    */
  /* ------------------------------------------------------------------ */

  /* The mode by its name and its two words, as its choice says it
   * ("Seeker: built-in, assisted"); never the AI model. */
  function showMode() {
    var item = byId("sb-agent");
    var select = byId("agent-mode");
    if (!item || !select || select.selectedIndex < 0) return;
    var said = select.options[select.selectedIndex].text;
    setItem(item, "agent", "Agent · " + said,
      "The Agent's mode: " + said + "\nOpen the Agent's settings");
    item.hidden = false;
  }

  /* ------------------------------------------------------------------ */
  /* Jobs on other machines, as last recorded                            */
  /* ------------------------------------------------------------------ */

  var GOING = { running: true, ready: true };

  function loadJobs() {
    getJSON("/api/remote/machines").then(function (data) {
      var allJobs = data && data.ok ? (data.jobs || []) : [];
      hasRecordedJobs = allJobs.length > 0;
      if (machineInfo) {
        var machine = byId("sb-machine");
        setItem(machine, "machine", machineSaid(machineInfo), machineTitle(machineInfo));
      }
      var host = byId("sb-remote");
      if (!host) return;
      var jobs = allJobs.filter(function (j) { return GOING[j.state]; });
      var key = JSON.stringify(jobs.map(function (j) { return [j.name, j.machine, j.state, j.detail]; }));
      if (host.dataset.key === key) return;
      host.dataset.key = key;
      host.innerHTML = "";
      jobs.forEach(function (job) {
        var item = el("button", "sb-item sb-job");
        item.type = "button";
        item.dataset.job = job.name;
        setItem(item, "external", job.machine + " · " + (job.detail || job.state),
          job.name + " on " + job.machine + ": " + (job.detail || job.state)
          + "\nAs last recorded; open Remote jobs");
        item.addEventListener("click", openRemote);
        host.appendChild(item);
      });
    }).catch(function () { /* no remote routes here: no jobs said */ });
  }

  function openRemote() {
    if (!hasRecordedJobs || !window.FastMDXDashboard) return;
    window.FastMDXDashboard.navigate("studies");
    setTimeout(function () {
      var jobs = byId("remote-jobs");
      if (jobs && !jobs.hidden) jobs.scrollIntoView({ block: "start" });
    }, 300);
  }

  /* ------------------------------------------------------------------ */
  /* The run going here, and one going in another study                   */
  /* ------------------------------------------------------------------ */

  var DOT = { running: "status-dot-live", failed: "status-dot-error",
              stopped: "status-dot-waiting", interrupted: "status-dot-waiting" };

  function showRun() {
    var run = byId("status-run");
    if (!run) return;
    var state = run.getAttribute("data-run") || "";
    var dot = byId("sb-run-dot");
    if (dot) dot.className = "status-dot " + (DOT[state] || "status-dot-stale");
    var title = byId("topbar-run-title");
    var name = byId("sb-run-name");
    var said = title ? title.textContent.trim() : "";
    if (name && name.textContent !== said) name.textContent = said;
    var stage = (byId("topbar-stage") || {}).textContent || "";
    var count = (byId("status-stage-count") || {}).textContent || "";
    var step = (byId("topbar-step") || {}).textContent || "";
    var total = (byId("topbar-total") || {}).textContent || "";
    run.title = said + ": " + stage + (count ? " (" + count.toLowerCase() + ")" : "")
      + (step && step !== "\u2014" ? ", step " + step + " of " + total : "")
      + "\nOpen its Overview";
  }

  function showElsewhere() {
    var host = byId("sb-elsewhere");
    if (!host) return;
    var where = app && app.running_elsewhere;
    host.hidden = !where;
    if (!where) return;
    var folder = String(where).split(/[\\/]/).filter(Boolean).pop() || where;
    var m = /^fastmdxplora_(.+)_study_\d+$/.exec(folder);
    var prog = app.running_elsewhere_progress || {};
    var far = typeof prog.percent === "number" ? prog.percent.toFixed(1) + "%"
      : (prog.stage ? String(prog.stage) : "");
    setItem(host, "play", (m ? m[1] : folder) + (far ? " · " + far : ""),
      "Running in another study: " + where + "\nOpen it");
  }

  /* Through the place's way to another study, which asks first whether
   * the switch would lose work here, on the page shown (Overview from a
   * page of no study), as the sidebar's way to it did (place.js). */
  function openElsewhere() {
    var where = app && app.running_elsewhere;
    if (where && window.FastMDXPlace) window.FastMDXPlace.open(where);
  }

  /* ------------------------------------------------------------------ */
  /* The open study's problems                                            */
  /* ------------------------------------------------------------------ */

  function showProblems(detail) {
    var item = byId("sb-problems");
    if (!item) return;
    var count = detail && typeof detail.count === "number" ? detail.count : null;
    item.hidden = count === null || !(app && app.active_run);
    if (item.hidden) return;
    setItem(item, "problem", count === 0 ? "No problems" : count + (count === 1 ? " problem" : " problems"),
      (count === 0 ? "No problems listed in the open study's records"
        : count + " recorded in the open study"
          + (detail.partial ? "; some of its records could not be read" : ""))
        + "\nOpen the Problems tab");
    item.dataset.count = String(count);
  }

  function openProblems() {
    if (!window.FastMDXFrame) return;
    if (document.body.classList.contains("panel-collapsed")) window.FastMDXFrame.setCollapsed(false);
    window.FastMDXFrame.showTab("problems");
    var tab = byId("problems-tab");
    if (tab) tab.click();
  }

  /* ------------------------------------------------------------------ */

  function start() {
    var bar = byId("status-bar");
    if (!bar || !window.FastMDXDashboard) return;
    var elsewhere = el("button", "sb-item sb-elsewhere");
    elsewhere.type = "button";
    elsewhere.id = "sb-elsewhere";
    elsewhere.hidden = true;
    elsewhere.addEventListener("click", openElsewhere);
    var remote = byId("sb-remote");
    bar.insertBefore(elsewhere, remote);
    var machine = byId("sb-machine");
    if (machine) machine.addEventListener("click", openRemote);
    var agent = byId("sb-agent");
    if (agent) {
      agent.addEventListener("click", function () {
        if (window.FastMDXAgent && window.FastMDXAgent.openSettings) window.FastMDXAgent.openSettings();
      });
    }
    var mode = byId("agent-mode");
    if (mode) mode.addEventListener("change", showMode);
    showMode();
    var problems = byId("sb-problems");
    if (problems) problems.addEventListener("click", openProblems);
    window.FastMDXDashboard.on("app-state", function (s) {
      app = s;
      document.body.classList.toggle("has-study", !!(s && s.active_run));
      showElsewhere();
      showRun();
      if (!(s && s.active_run)) showProblems(null);
    });
    window.FastMDXDashboard.on("status-updated", showRun);
    window.FastMDXDashboard.on("problems-counted", showProblems);
    loadMachine();
    loadJobs();
    setInterval(loadMachine, MACHINE_EVERY_MS);
    setInterval(loadJobs, JOBS_EVERY_MS);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();

  window.FastMDXStatusBar = { reload: function () { loadMachine(); loadJobs(); } };
}());
