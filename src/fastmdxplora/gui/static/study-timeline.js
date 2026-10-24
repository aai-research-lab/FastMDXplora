/* The study's Timeline, in the side panel (study_timeline.py).
 *
 * What happened to the study, newest first, as an editor's Timeline of a
 * file: each phase as it last ran, one a run again replaced or set aside,
 * each piece an extended study was carried on in; when, how long, which
 * version on which computer, and what it changed where the records say (the
 * means an analysis run again moved, the settings a piece was carried on
 * with). Read when its tab is shown, and again as the study changes while
 * it is. Everything is built as elements: what a study recorded is data.
 */
(function () {
  "use strict";

  var shownFor = "";
  var asking = false;
  /* Asked again while a read was out (the study switched, or its results
   * changed): read once more when it answers. */
  var again = false;

  function byId(id) { return document.getElementById(id); }

  function make(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  function pane() { return document.querySelector('.side-pane[data-side-pane="timeline"]'); }
  function open() { var p = pane(); return !!p && !p.hidden; }

  /* Settings' Time choice, as every time on the page follows it. */
  function uses24Hours() {
    var choice = byId("setting-time-format");
    return !choice || choice.value !== "12h";
  }

  /* When, as a day and a time on this computer's clock, as the Overview
   * says it ("17 Oct, 21:10"). A step whose record gives no time says
   * none: `new Date(null)` is 1970's first instant. */
  function whenSaid(iso) {
    if (!iso) return "";
    var date = new Date(iso);
    if (isNaN(date.getTime())) return "";
    return date.toLocaleString([], { day: "numeric", month: "short", hour: "2-digit",
                                     minute: "2-digit", hour12: !uses24Hours() });
  }

  /* How long, as the Overview's phases say it ("1m 15s"). */
  function tookSaid(seconds) {
    if (seconds == null || !isFinite(seconds)) return "";
    if (seconds < 60) return Math.round(seconds) + " s";
    var m = Math.floor(seconds / 60), h = Math.floor(m / 60);
    if (h) return h + "h " + (m % 60) + "m";
    return m + "m " + Math.round(seconds % 60) + "s";
  }

  function figure(value) {
    if (value == null || !isFinite(value)) return "\u2014";
    var m = Math.abs(value);
    return m !== 0 && (m >= 1e5 || m < 1e-3) ? value.toExponential(3) : String(+value.toPrecision(4));
  }

  function meanSaid(side, unit) {
    if (!side || side.mean == null) return "not recorded";
    var said = figure(side.mean) + (side.error != null ? " \u00b1 " + figure(side.error) : "");
    return said + (unit ? " " + unit : "") + (side.error == null ? ", not determined" : "");
  }

  function changes(event) {
    var c = event.changes;
    if (!c) return null;
    var parts = [];
    if (c.moved.length) parts.push(c.moved.length + " mean" + (c.moved.length === 1 ? "" : "s") + " moved");
    if (c.kept) parts.push(c.kept + " the same");
    if (c.added.length) parts.push(c.added.length + " added");
    if (c.dropped.length) parts.push(c.dropped.length + " not run again");
    var unread = c.not_read || [];
    if (unread.length) parts.push(unread.length + " not read");
    var box = make("details", "tl-changes");
    box.appendChild(make("summary", "", parts.join(", ") || "Nothing changed"));
    if (c.moved.length) {
      var table = make("table", "kv-table tl-table");
      var head = make("tr");
      ["Analysis", "Before", "After"].forEach(function (w) { head.appendChild(make("th", "", w)); });
      table.appendChild(head);
      c.moved.forEach(function (m) {
        var row = make("tr");
        row.dataset.analysis = m.analysis;
        row.appendChild(make("td", "", m.analysis));
        row.appendChild(make("td", "mono", meanSaid(m.before, m.unit)));
        row.appendChild(make("td", "mono", meanSaid(m.after, m.unit)));
        table.appendChild(row);
      });
      box.appendChild(table);
    }
    if (c.added.length) box.appendChild(make("p", "muted small", "Added: " + c.added.join(", ") + "."));
    if (c.dropped.length) {
      box.appendChild(make("p", "muted small", "Not run again, kept in previous/analysis: " +
                                              c.dropped.join(", ") + "."));
    }
    if (unread.length) {
      // Its record not read, before or after: not said moved, kept, added
      // or dropped (study_timeline.py).
      box.appendChild(make("p", "muted small", "Not read, so not compared: " +
                                              unread.join(", ") + "."));
    }
    return box;
  }

  function settings(event) {
    if (event.settings_not_read) {
      return make("p", "muted small tl-not-read",
                  "Its settings, or the study's, were not read: the settings it was carried on with are not said.");
    }
    if (!event.settings || !event.settings.length) return null;
    var box = make("details", "tl-changes");
    box.appendChild(make("summary", "", event.settings.length + " setting" +
                                         (event.settings.length === 1 ? " differs" : "s differ") +
                                         " from the study's"));
    var table = make("table", "kv-table tl-table");
    var head = make("tr");
    ["Setting", "The study", "This piece"].forEach(function (w) { head.appendChild(make("th", "", w)); });
    table.appendChild(head);
    function value(d, side) {
      if (d[side + "_said"]) return d[side + "_said"];
      return d[side] == null ? "not set" : JSON.stringify(d[side]);
    }
    event.settings.forEach(function (d) {
      var row = make("tr");
      row.appendChild(make("td", "mono", d.setting));
      row.appendChild(make("td", "mono", value(d, "first")));
      row.appendChild(make("td", "mono", value(d, "second")));
      table.appendChild(row);
    });
    box.appendChild(table);
    return box;
  }

  /* A round of a study run until what it asks is determined: the
   * production it judged, and where it falls in time, which its record
   * does not give (study_timeline.py places it by the steps around it). */
  function roundFacts(event) {
    var placed = event.placed;
    return [
      event.production_said ? "judged after " + event.production_said + " of production" : "",
      "its time not recorded" + (!placed ? ""
        : placed.before ? ": placed just before the pieces it began"
        : ": placed after the last step before it"),
      event.where || "", event.detail || ""];
  }

  /* A study of several runs takes no steps of its own: its runs' steps
   * are listed, each named by its run, and a run that recorded none is
   * said, never the study as one that recorded nothing. */
  function ofItsRuns(runs, events) {
    var n = runs.length;
    var of = n === 1 ? "its run" : "its " + n + " runs";
    if (!events.length) {
      return "A study of " + n + " run" + (n === 1 ? "" : "s") + ": " +
        (n === 1 ? "it has" : "none has") + " recorded a step yet. Choose a run to see its own.";
    }
    var none = runs.filter(function (run) { return !run.steps; }).map(function (run) { return run.label; });
    return "The steps of " + of + ", each named by the run that took it; choose a run to see " +
      "its own." + (none.length ? " Not yet recorded a step: " + none.join(", ") + "." : "");
  }

  function render(data) {
    var list = byId("study-timeline-list");
    var said = byId("study-timeline-said");
    if (!list) return;
    list.replaceChildren();
    var events = data && data.ok ? data.events || [] : [];
    // A record of the study not read is said so first (server._confined),
    // never as a step it did not take.
    var notRead = data && data.not_read ? data.not_read : "";
    said.textContent = data && data.ok
      ? (notRead || (data.runs ? ofItsRuns(data.runs, events)
                     : events.length ? "" : "Nothing recorded yet: a study's steps are listed as it takes them."))
      : (data && (data.reason || data.error)) || "The Timeline could not be read.";
    events.forEach(function (event) {
      var item = make("li", "tl-event");
      item.dataset.kind = event.kind;
      item.dataset.phase = event.phase;
      if (event.replaced) item.dataset.replaced = "true";
      var head = make("div", "tl-head");
      head.appendChild(make("span", "tl-dot"));
      // A step of one of a study's runs, named by the run that took it.
      head.appendChild(make("span", "tl-said", (event.run ? event.run + ": " : "") +
                                               event.said + (event.again ? " again" : "")));
      var at = whenSaid(event.at);
      if (at) {
        var when = make("time", "tl-when", at);
        when.setAttribute("datetime", event.at);
        head.appendChild(when);
      }
      item.appendChild(head);
      var facts = event.kind === "round" ? roundFacts(event) : [
        tookSaid(event.seconds),
        event.version ? event.version + (event.host ? " on " + event.host : "") : "",
        event.where || "", event.detail || ""];
      facts = facts.filter(Boolean);
      if (event.stopped_by) item.dataset.stoppedBy = event.stopped_by;
      if (facts.length) item.appendChild(make("div", "tl-facts", facts.join(" \u00b7 ")));
      [changes(event), settings(event)].forEach(function (box) { if (box) item.appendChild(box); });
      list.appendChild(item);
    });
  }

  function studyOpen() {
    var app = window.FastMDXDashboard ? window.FastMDXDashboard.state.appState : null;
    return app && app.active_run ? app.active_run : "";
  }

  function load(force) {
    var study = studyOpen();
    if (!open()) return;
    if (asking) {
      // The answer coming may be the study before a switch, or its results
      // before they changed: read again when it comes.
      again = true;
      return;
    }
    if (!force && study === shownFor) return;
    asking = true;
    again = false;
    fetch("/api/study-timeline", { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .catch(function () { return { ok: false, reason: "The server did not answer." }; })
      .then(function (data) {
        asking = false;
        var stale = studyOpen() !== study;
        // An answer for a study no longer open is not drawn.
        if (!stale) {
          shownFor = study;
          render(data);
        }
        if (stale || again) load(true);
      });
  }

  function wire() {
    var p = pane();
    if (!p) return;
    if (document.body.dataset.beyondLoopback === "true") {
      // Beyond loopback /api/study-timeline is refused: the tab is not
      // offered, and a panel left on it shows the Log instead.
      if (open()) {
        var log = document.querySelector('.side-tab[data-side-tab="log"]');
        if (log) log.click();
      }
      var tab = byId("timeline-tab");
      if (tab) tab.remove();
      p.remove();
      return;
    }
    // Shown by its tab, or as the panel was left: read then.
    new MutationObserver(function () { if (open()) load(true); })
      .observe(p, { attributes: true, attributeFilter: ["hidden"] });
    window.addEventListener("dashboard:run-changed", function () { shownFor = ""; load(true); });
    window.addEventListener("dashboard:results-updated", function () { load(true); });
    if (open()) load(true);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", wire);
  else wire();

  window.FastMDXStudyTimeline = { load: load };
}());
