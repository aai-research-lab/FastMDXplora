/* How far a run is, in the tab's title, and a notice when it ends.
 *
 * A run of hours is watched from another tab or another window. While it
 * runs, the tab's title begins with how far it is ("42% · 1L2Y"); once it
 * ends, the title is the page's own again. Where the person has asked for
 * it in Preferences (Notify when a run ends), the browser shows a notice as
 * the run completes, stops or fails: only a run seen running in this page,
 * so opening a finished study says nothing. The browser asks the person
 * once whether the page may; refused, the setting is unticked and says so.
 */
(function () {
  "use strict";

  var base = "";
  var last = null;

  function el(id) { return document.getElementById(id); }

  function text(id) {
    var node = el(id);
    return node ? node.textContent.trim() : "";
  }

  /* Where the run stands, as the sidebar says it. */
  function now() {
    var progress = el("sidebar-progress");
    return {
      run: progress ? progress.getAttribute("data-run") || "" : "",
      name: text("topbar-run-title"),
      word: text("topbar-status-text"),
      stage: text("topbar-stage"),
      percent: parseFloat(text("topbar-progress")),
    };
  }

  function wanted() {
    var box = el("setting-notify");
    return !!(box && box.checked);
  }

  function title(state) {
    if (state.run !== "running" || !state.name || state.name === "No active study") {
      document.title = base;
      return;
    }
    var far = isFinite(state.percent) ? Math.floor(state.percent) + "% · " : "";
    document.title = far + state.name + " · " + base;
  }

  function notify(state) {
    if (!wanted() || !("Notification" in window) || Notification.permission !== "granted") return;
    var run = state.run;
    if (!run && !state.said) {
      // No record says how it ended (its process ended before writing
      // one, or a study of several runs): the process's own end, as the
      // app state says it, failed or stopped.
      var app = window.FastMDXDashboard && window.FastMDXDashboard.state.appState;
      var word = app ? String(app.status || "") : "";
      if (word === "failed" || word === "stopped") run = word;
    }
    var ended = { failed: "failed", stopped: "stopped", interrupted: "was interrupted" }[run]
      || "completed";
    try {
      var notice = new Notification(state.name + " " + ended, {
        body: state.stage || state.word || "", tag: "fastmdx-run-" + state.name,
      });
      notice.onclick = function () { window.focus(); notice.close(); };
    } catch (e) { /* a browser that shows notices only from a service worker */ }
  }

  /* Whether a status after one seen running is that run's end. */
  function ended(was, state) {
    if (!was.study || was.study !== state.study) return false;
    // No record: the end of a run that had none either (a study of several
    // runs keeps its records in its runs, not at its top; a run's process
    // can end before it writes one), said as its process ends. A record
    // seen and then gone was cleared (a rerun forced in its folder).
    if (!state.said) return !was.said;
    if (!was.started || was.started === state.started) return true;
    var then = Date.parse(was.started), since = Date.parse(state.started);
    return isFinite(then) && isFinite(since) && since > then;
  }

  function said(message) {
    var note = el("setting-notify-note");
    if (!note) return;
    note.textContent = message;
    note.hidden = !message;
  }

  /* Asked for: the browser asks the person, once. */
  function askedFor() {
    var box = el("setting-notify");
    if (!box || !box.checked) { said(""); return; }
    if (!("Notification" in window)) {
      box.checked = false;
      said("This browser shows no notices.");
      box.dispatchEvent(new Event("change", { bubbles: true }));
      return;
    }
    if (Notification.permission === "granted") { said(""); return; }
    if (Notification.permission === "denied") {
      box.checked = false;
      said("The browser has notices from this page turned off; allow them in its site settings.");
      box.dispatchEvent(new Event("change", { bubbles: true }));
      return;
    }
    Promise.resolve(Notification.requestPermission()).then(function (answer) {
      if (answer !== "granted") {
        box.checked = false;
        said("Not allowed by the browser.");
        box.dispatchEvent(new Event("change", { bubbles: true }));
      } else {
        said("");
      }
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    base = document.title;
    var box = el("setting-notify");
    if (box) box.addEventListener("change", function (event) {
      if (event.isTrusted) askedFor();
    });
    // A run is the study's whose status says it, by the folder the server
    // read, which it names (a study opened elsewhere as a poll was answered
    // gives another folder's status); and by when its live record says it
    // started, which the folder's record keeps until a rerun clears it; not
    // by the name shown, which changes as the study's records arrive. Its
    // end is said where the record that ends it is the same run's, one seen
    // running with no start (its record not written yet, a study of several
    // runs, an older record), or the folder's run started again later (a
    // rerun forced in its folder, ended before the page saw it going). A
    // record seen and then gone says nothing ended.
    window.addEventListener("dashboard:status-updated", function (event) {
      var state = now();
      var detail = event.detail || {};
      var status = detail.status || {};
      state.started = status.run_started_at ? String(status.run_started_at) : "";
      state.said = status.status ? String(status.status) : "";
      state.study = detail.study ? String(detail.study) : "";
      title(state);
      if (last && last.run === "running" && state.run !== "running" && ended(last, state)) {
        notify(state);
      }
      last = state;
    });
    // The records named it: the title says the study's name at once.
    window.addEventListener("dashboard:results-updated", function () { title(now()); });
    window.addEventListener("dashboard:run-changed", function () { last = null; });
  });

  window.FastMDXRunNotice = { now: now };
}());
