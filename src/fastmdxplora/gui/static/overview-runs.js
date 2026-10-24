/* The Overview of a study of several runs (runs_compared.py's numbers).
 *
 * It said "A study of 2 runs" and nothing else. Now: the runs as a table,
 * each with its state, how far its production has got, its speed and its
 * own means; for replicas, the mean of their means with the error the
 * spread between them gives it, beside each run's own; what is not
 * determined, and in which run; and one measure's series of every run
 * overlaid. A run opens from its row. It is read again every few seconds
 * while a run goes, less often while runs wait for their round, and once
 * otherwise; only while the Overview is shown.
 */
(function () {
  "use strict";

  var EVERY_MS = 5000;
  var SHOWN_MEASURES = 4;
  var DOT = { running: "status-dot-live", completed: "status-dot-completed",
              failed: "status-dot-error", stopped: "status-dot-waiting",
              interrupted: "status-dot-waiting", waiting: "status-dot-waiting" };
  var data = null;
  var study = "";
  var timer = null;
  var chosen = "";
  // Two runs chosen to compare (their folders), kept as the table is
  // drawn again while runs go.
  var picked = [];

  function byId(id) { return document.getElementById(id); }
  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  /* As the Analysis page's table says a number (runs-compared.js). */
  function figure(value) {
    if (value == null || !isFinite(value)) return "\u2014";
    var m = Math.abs(value);
    if (m === 0) return "0";
    if (m >= 1e5 || m < 1e-3) return value.toExponential(2);
    if (m >= 100) return value.toFixed(1);
    if (m >= 1) return value.toFixed(3);
    return value.toPrecision(3);
  }
  function withError(mean, error) {
    return figure(mean) + (error != null && isFinite(error) ? " ± " + figure(error) : "");
  }
  function ns(value) {
    if (value == null) return "\u2014";
    return value < 1 ? (value * 1000).toFixed(value * 1000 < 10 ? 1 : 0) + " ps"
      : value.toFixed(value < 10 ? 2 : 1) + " ns";
  }

  function stateSaid(run) {
    if (run.state === "running" && typeof run.fraction === "number") {
      return (100 * run.fraction).toFixed(0) + "%";
    }
    if (run.state === "waiting" && run.so_far) return "next round";
    return run.state;
  }

  /* The measures the table shows: those most runs recorded, in the order
   * the Analysis page lists them, at most four; all of them are there. */
  function shownMeasures() {
    return (data.measures || []).filter(function (m) {
      return m.cells.some(function (c) { return c.mean != null; });
    }).slice(0, SHOWN_MEASURES);
  }

  /* Through the place's way to another study, which asks first whether
   * the switch would lose work here (place.js). Beyond loopback a switch
   * is a post the server refuses: none is made. */
  function open(run) {
    if (beyondLoopback()) return;
    if (window.FastMDXPlace) window.FastMDXPlace.open(run.path, "overview");
  }

  function renderTable() {
    var table = byId("overview-runs-table");
    // The keyboard stays on the run it was on when the table is drawn again.
    var focused = table.contains(document.activeElement)
      ? (document.activeElement.closest(".overview-runs-row") || {}).dataset : null;
    var focusRun = focused ? focused.run : "";
    table.innerHTML = "";
    var measures = shownMeasures();
    var head = el("tr");
    ["Run", "State", "Production", "ns/day"].forEach(function (word, i) {
      head.appendChild(el("th", i > 1 ? "is-number" : "", word));
    });
    measures.forEach(function (m) {
      var th = el("th", "is-number", m.label + (m.unit ? " (" + m.unit + ")" : ""));
      th.title = m.label + ": each run's mean with its standard error";
      head.appendChild(th);
    });
    var thead = el("thead");
    thead.appendChild(head);
    table.appendChild(thead);
    var body = el("tbody");
    data.runs.forEach(function (run) {
      var row = el("tr", "overview-runs-row");
      row.dataset.run = run.run_id;
      row.dataset.state = run.state;
      var name = el("th", "overview-runs-name");
      name.scope = "row";
      // Beyond loopback opening a run is a switch of study, a post the
      // server refuses, and Compare is not answered (/api/compared):
      // the run is named as text, and nothing that posts is offered.
      var outside = beyondLoopback();
      var link = el(outside ? "span" : "button", outside ? "overview-runs-named" : "overview-runs-open");
      var dot = el("span", "status-dot " + (DOT[run.state] || "status-dot-stale"));
      dot.setAttribute("aria-hidden", "true");
      link.appendChild(dot);
      link.appendChild(el("span", "", run.label || run.run_id));
      if (outside) {
        // Laid out as the button is (dashboard.css), without its pointer.
        link.style.cssText = "display: inline-flex; align-items: center; gap: 8px;";
        dot.style.cssText = "width: 7px; height: 7px; margin: 0;";
        link.title = (run.label || run.run_id) + "\n" + run.run_id;
      } else {
        link.type = "button";
        link.title = "Open " + (run.label || run.run_id) + "\n" + run.run_id;
        link.addEventListener("click", function () { open(run); });
      }
      if (!outside) {
        var pick = el("input", "overview-runs-pick");
        pick.type = "checkbox";
        pick.checked = picked.indexOf(run.path) >= 0;
        pick.dataset.path = run.path;
        pick.setAttribute("aria-label", "Choose " + (run.label || run.run_id) + " to compare");
        pick.title = "Choose two runs to compare";
        pick.addEventListener("change", function () { choose(run.path, pick.checked); });
        name.appendChild(pick);
      }
      name.appendChild(link);
      row.appendChild(name);
      var state = el("td", "overview-runs-state");
      if (run.state === "running" && typeof run.fraction === "number") {
        var bar = el("span", "overview-runs-bar");
        var fill = el("span", "overview-runs-fill");
        fill.style.width = (100 * run.fraction).toFixed(1) + "%";
        bar.appendChild(fill);
        state.appendChild(bar);
      }
      state.appendChild(el("span", "", stateSaid(run)));
      row.appendChild(state);
      row.appendChild(el("td", "is-number mono", run.production_ns == null ? "\u2014"
        : ns(run.production_ns) + (run.production_planned_ns && run.production_planned_ns !== run.production_ns
          ? " of " + ns(run.production_planned_ns) : "")));
      row.appendChild(el("td", "is-number mono", run.speed_ns_per_day == null ? "\u2014"
        : figure(run.speed_ns_per_day)));
      measures.forEach(function (m) {
        var cell = m.cells.find(function (c) { return c.run_id === run.run_id; }) || {};
        var td = el("td", "is-number mono");
        if (cell.mean == null) {
          td.textContent = "\u2014";
          td.classList.add("is-missing");
          td.title = "Not recorded yet";
        } else if (cell.withheld) {
          td.textContent = figure(cell.mean);
          td.classList.add("is-withheld");
          td.title = "Not determined: " + cell.withheld;
        } else {
          td.textContent = withError(cell.mean, cell.error);
        }
        if (cell.mean != null && cell.noted) {
          // Its image note beside it, as the Analysis page says it.
          td.appendChild(document.createTextNode(" \u2020"));
          td.classList.add("is-noted");
          td.title = (td.title ? td.title + " " : "") + cell.noted.charAt(0).toUpperCase()
            + cell.noted.slice(1) + ".";
        }
        row.appendChild(td);
      });
      body.appendChild(row);
    });
    table.appendChild(body);
    if (data.replicas && measures.some(function (m) { return m.ensemble; })) {
      var foot = el("tfoot");
      var together = el("tr", "overview-runs-together");
      var label = el("th", "", "The runs together");
      label.scope = "row";
      label.title = "The mean of the replicas' means, with the error their spread gives it: "
        + "the spread over the square root of the number of runs";
      together.appendChild(label);
      together.appendChild(el("td", "", ""));
      together.appendChild(el("td", "", ""));
      together.appendChild(el("td", "", ""));
      measures.forEach(function (m) {
        var td = el("td", "is-number mono");
        // Of fewer runs than the study has (one not ended, not read, or
        // without this mean): said on the mean, not left to be taken as
        // every run's.
        var some = m.ensemble && m.ensemble.of ? " (" + m.ensemble.runs + " of " + m.ensemble.of + ")" : "";
        var from = m.ensemble && m.ensemble.of
          ? " From " + m.ensemble.runs + " of the study's " + m.ensemble.of + " runs." : "";
        if (m.ensemble && m.ensemble.withheld) {
          // Not determined: their spread is not yet an error, as All
          // studies and the written comparison say (runs_compared.py).
          td.textContent = figure(m.ensemble.mean) + some;
          td.classList.add("is-withheld");
          td.title = "Not determined: " + m.ensemble.withheld + " Their spread: "
            + figure(m.ensemble.spread) + "." + from;
        } else if (m.ensemble) {
          td.textContent = withError(m.ensemble.mean, m.ensemble.error) + some;
          td.title = "Mean of " + m.ensemble.runs + " runs' means ± the error between them (their "
            + "spread, " + figure(m.ensemble.spread) + ", over √" + m.ensemble.runs + ", from "
            + m.ensemble.runs + " runs)." + from;
        } else {
          td.textContent = "\u2014";
        }
        if (m.ensemble && m.ensemble.noted) {
          // Replicas share the box: their means' image notes are in the
          // mean of them (runs_compared.mean_of_replicas), said under the table.
          td.appendChild(document.createTextNode(" \u2020"));
          td.classList.add("is-noted");
          td.title += " " + m.ensemble.noted.charAt(0).toUpperCase() + m.ensemble.noted.slice(1) + ".";
        }
        together.appendChild(td);
      });
      foot.appendChild(together);
      table.appendChild(foot);
    }
    if (focusRun) {
      Array.prototype.some.call(table.querySelectorAll(".overview-runs-row"), function (row) {
        if (row.dataset.run !== focusRun) return false;
        var again = row.querySelector(".overview-runs-open");
        if (again) again.focus();
        return true;
      });
    }
    // Columns past its right edge are shaded there (runs-compared.js).
    if (window.FastMDXRunsCompared && window.FastMDXRunsCompared.markMore) {
      window.FastMDXRunsCompared.markMore(table.parentElement);
    }
  }

  /* Two runs chosen: a third drops the first, as in All studies. */
  function choose(path, on) {
    picked = picked.filter(function (p) { return p !== path; });
    if (on) picked.push(path);
    if (picked.length > 2) picked = picked.slice(-2);
    var table = byId("overview-runs-table");
    Array.prototype.forEach.call(table.querySelectorAll(".overview-runs-pick"), function (box) {
      box.checked = picked.indexOf(box.dataset.path) >= 0;
    });
    sayPicked();
  }

  function sayPicked() {
    var button = byId("overview-runs-compare");
    if (!button) return;
    var runs = (data && data.runs) || [];
    picked = picked.filter(function (p) { return runs.some(function (r) { return r.path === p; }); });
    button.hidden = beyondLoopback() || picked.length !== 2;
  }

  function compare() {
    if (beyondLoopback()) return;
    if (picked.length === 2 && window.FastMDXCompare) window.FastMDXCompare.open(picked[0], picked[1]);
  }

  /* A page bound beyond loopback (the server says so on the body). */
  function beyondLoopback() { return document.body.dataset.beyondLoopback === "true"; }

  function renderSaid() {
    var runs = data.runs || [];
    var count = function (s) { return runs.filter(function (r) { return r.state === s; }).length; };
    var parts = [count("completed") + " of " + runs.length + " runs completed"];
    if (count("running")) parts.push(count("running") + " running");
    var waiting = runs.filter(function (r) { return r.state === "waiting"; }).length;
    if (waiting) parts.push(waiting + " waiting");
    var axes = (data.axes || []).map(function (a) { return a.label.replace(/^random_/, ""); });
    // Replicas as every page says how they differ (batch/aggregate.py).
    parts.push(data.replicas ? "replicas, " + data.replicas_said
      : axes.length ? "differing by " + axes.join(" and ") + ": compared, not averaged" : "");
    byId("overview-runs-said").textContent = parts.filter(Boolean).join("; ") + ".";
    var more = (data.measures || []).length - shownMeasures().length;
    var note = byId("overview-runs-note");
    note.textContent = more > 0 ? more + " more measure" + (more === 1 ? "" : "s")
      + " on the Analysis page, compared run against run." : "";
    renderNoted(note);
  }

  /* What † in the table means, a line for each mean it marks, as the
   * Analysis page's runs compared say it (runs-compared.js): the note on a
   * cell's title alone was not reached by keyboard or touch. */
  function renderNoted(after) {
    var host = byId("overview-runs-noted");
    if (!host) {
      host = el("div", "overview-runs-noted");
      host.id = "overview-runs-noted";
      after.parentNode.insertBefore(host, after.nextSibling);
    }
    host.innerHTML = "";
    var labels = {};
    (data.runs || []).forEach(function (run) { labels[run.run_id] = run.label || run.run_id; });
    shownMeasures().forEach(function (m) {
      m.cells.forEach(function (c) {
        if (c.mean == null || !c.noted) return;
        host.appendChild(el("p", "muted small runs-compared-noted", "† " +
          (labels[c.run_id] || c.run_id) + ", " + m.label + ": " + c.noted + "."));
      });
      if (data.replicas && m.ensemble && m.ensemble.noted) {
        host.appendChild(el("p", "muted small runs-compared-noted", "† The runs together, " +
          m.label + ": " + m.ensemble.noted + "."));
      }
    });
    host.hidden = !host.childNodes.length;
  }

  /* What is not determined, and in which runs: the reason's first words
   * (the whole on hover), each run's independent samples, and the remedy
   * the reason names; a run opens on its Overview, where what would fix it
   * is priced. */
  function firstWords(reason) {
    var said = String(reason || "").split(/:\s|\.\s/)[0];
    return said.length > 140 ? said.slice(0, 139) + "\u2026" : said;
  }
  function listed(words) {
    return words.length < 2 ? words.join("") : words.slice(0, -1).join(", ") + " and " + words[words.length - 1];
  }
  /* Under one is said so, as `statistics.independent_samples_said` says it. */
  function samplesSaid(n) { return n < 1 ? "fewer than one" : n.toFixed(1); }
  function remedy(reason) {
    var found = /The remedy is ([^.]+)\./.exec(String(reason || ""));
    return found ? "The remedy is " + found[1] + "." : "";
  }
  function renderUndetermined() {
    var host = byId("overview-runs-undetermined");
    var list = byId("overview-runs-undetermined-list");
    list.innerHTML = "";
    var lines = 0;
    (data.measures || []).forEach(function (m) {
      var withheld = m.cells.filter(function (c) { return c.withheld; });
      if (!withheld.length) return;
      lines += 1;
      var li = el("li");
      li.appendChild(el("strong", "", m.label + ". "));
      var names = withheld.map(function (c) {
        var run = data.runs.find(function (r) { return r.run_id === c.run_id; }) || {};
        return run.label || c.run_id;
      });
      var samples = withheld.filter(function (c) { return c.effective_samples != null; })
        .map(function (c) { return samplesSaid(c.effective_samples); });
      var reason = withheld[0].withheld;
      li.appendChild(document.createTextNode("Not determined in " + listed(names)
        + (samples.length === withheld.length ? " (" + listed(samples) + " independent "
          + (samples.every(function (n) { return n === "fewer than one"; }) ? "sample" : "samples")
          + ")" : "")
        + ". " + firstWords(reason) + ". " + remedy(reason)));
      li.title = reason;
      list.appendChild(li);
    });
    host.hidden = lines === 0;
  }

  function renderOverlay() {
    var select = byId("overview-runs-measure");
    var measures = (data.measures || []).filter(function (m) {
      return m.cells.some(function (c) { return c.mean != null; });
    });
    byId("overview-runs-overlay").hidden = measures.length === 0;
    if (!measures.length) return;
    var key = measures.map(function (m) { return m.analysis; }).join(",");
    if (select.dataset.key !== key) {
      select.dataset.key = key;
      select.innerHTML = "";
      measures.forEach(function (m) {
        var option = el("option", "", m.label);
        option.value = m.analysis;
        select.appendChild(option);
      });
    }
    if (!chosen || !measures.some(function (m) { return m.analysis === chosen; })) chosen = measures[0].analysis;
    select.value = chosen;
    if (window.FastMDXRunsCompared) {
      window.FastMDXRunsCompared.overlay(data, chosen, byId("overview-runs-chart"),
                                         byId("overview-runs-legend"));
    }
  }

  var drawn = "";

  function render() {
    var host = byId("overview-runs");
    if (!host) return;
    host.hidden = !data || !data.ok;
    document.documentElement.toggleAttribute("data-overview-runs", !host.hidden);
    if (host.hidden) return;
    var key = JSON.stringify(data);
    if (key === drawn) return;
    drawn = key;
    renderSaid();
    renderTable();
    sayPicked();
    renderUndetermined();
    // The series again where a run's state changed, or half a minute on
    // while runs go: each is a fetch of every run's series.
    var states = JSON.stringify(data.runs.map(function (r) { return [r.run_id, r.state]; }));
    if (states !== overlaidStates || Date.now() - overlaidAt > 30000) {
      overlaidStates = states;
      overlaidAt = Date.now();
      renderOverlay();
    }
  }
  var overlaidStates = "";
  var overlaidAt = 0;

  function going() {
    return data && data.ok && data.runs.some(function (r) {
      return r.state === "running" || r.state === "waiting";
    });
  }
  /* Read again only while the Overview is shown; a run waiting for its
   * round can wait hours, so a study with none running is read less often. */
  function shown() { return document.documentElement.dataset.page === "overview"; }
  function every() {
    return data.runs.some(function (r) { return r.state === "running"; }) ? EVERY_MS : EVERY_MS * 6;
  }
  var stale = false;

  function load() {
    clearTimeout(timer);
    var asked = study;
    return fetch("/api/runs-compared", { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (answer) {
        if (asked !== study) return;
        data = answer;
        render();
        if (going()) timer = setTimeout(again, every());
      })
      .catch(function () { if (asked === study) timer = setTimeout(again, EVERY_MS * 2); });
  }
  function again() {
    if (shown()) load();
    else stale = true;
  }

  function start() {
    if (!byId("overview-runs") || !window.FastMDXDashboard) return;
    byId("overview-runs-compare").addEventListener("click", compare);
    byId("overview-runs-measure").addEventListener("change", function (e) {
      chosen = e.target.value;
      if (data && window.FastMDXRunsCompared) {
        window.FastMDXRunsCompared.overlay(data, chosen, byId("overview-runs-chart"),
                                           byId("overview-runs-legend"));
      }
    });
    // Back on the Overview: read now what was not read while away.
    window.FastMDXDashboard.on("navigate", function () {
      if (stale && shown() && study) { stale = false; load(); }
    });
    window.FastMDXDashboard.on("app-state", function (s) {
      var next = s && Array.isArray(s.runs) ? String(s.active_run || "") : "";
      if (next === study) return;
      study = next;
      data = null;
      picked = [];
      drawn = "";
      stale = false;
      overlaidStates = "";
      clearTimeout(timer);
      if (study) load();
      else render();
    });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();

  // `show` draws an answer as if just read.
  window.FastMDXOverviewRuns = { load: load, show: function (answer) { data = answer; render(); } };
}());
