/* Compare: two studies, or two runs, side by side (compare_view.py).
 *
 * One view for what was in three places: the settings in which the two
 * differ, the means each recorded with the difference marked only where it
 * is resolved, each series both recorded over time overlaid, and the Viewer
 * playing the second beside the first. Opened from All studies (two
 * chosen), from a study of several runs (two of its runs chosen on its
 * Overview) and from the palette (Compare with...). Its address names the
 * two, so Back, Forward and a reload keep them: #compare/<first>|<second>.
 */
(function () {
  "use strict";

  /* The settings shown before "Show the other N", as All studies did. */
  var SHOWN_FIRST = 20;

  var shown = null;

  function byId(id) { return document.getElementById(id); }

  function make(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  /* A mean with its error, to the error's second significant figure. */
  function withError(mean, error, unit) {
    if (mean === null || mean === undefined) return "not recorded";
    var said;
    if (error !== null && error !== undefined && error > 0) {
      var places = Math.max(0, 1 - Math.floor(Math.log10(error)));
      if (places > 6) {
        // Past six places a fixed figure reads 0.000000: the two in the
        // error's own powers of ten instead.
        var power = Math.floor(Math.log10(error));
        var scale = Math.pow(10, power);
        said = "(" + (mean / scale).toFixed(1) + " \u00b1 " + (error / scale).toFixed(1) +
          ")e" + power;
      } else {
        said = mean.toFixed(places) + " \u00b1 " + error.toFixed(places);
      }
    } else {
      said = Number(mean.toPrecision(4)).toString();
    }
    return said + (unit ? " " + unit : "");
  }

  /* A mean as the comparison sets it: with the error the difference is
   * set against where that is not its own (two replicas: the replicas'
   * spread). */
  function meanSaid(record, error) {
    if (!record) return "not recorded";
    // Its record was not read (compare_view.py): said so, never as a study
    // that recorded no mean.
    if (record.not_read) return "not read";
    // Its analysis ran and gave no mean, and said why (below the table).
    if (record.mean == null && record.withheld) return "not determined";
    if (record.withheld) return withError(record.mean, null, record.unit) + ", not determined";
    return withError(record.mean, error != null ? error : record.error, record.unit);
  }

  /* What the error of a difference between two replicas is, by which
   * error each of the two has (compare_view.py). */
  var ERROR_OF = {
    replicas: " (replicas' spread)",
    own: " (each run's own error, larger than the replicas' spread)",
    mixed: " (the replicas' spread, or a run's own error where larger)",
  };

  /* Two replicas, the only two read: their spread against the errors they
   * estimated for themselves (`spread_against_own`), past its factor the
   * errors too tight. */
  function spreadSaid(check) {
    if (!check) return ", not set against their own errors: neither gave one above zero";
    var times = Number(check.ratio.toPrecision(2));
    return ", their spread " + times + " times their own errors" +
      (check.too_tight ? ": those errors are too tight"
        : ", within the factor of " + check.factor);
  }

  /* A cell of a table, with the column it is in for a phone, where the
   * table is laid out one quantity under another. */
  function cell(cls, text, label) {
    var node = make("td", cls, text);
    if (label) node.dataset.label = label;
    return node;
  }

  /* A setting's name, a line allowed to break after each dot and
   * underscore ("setup.random_seed"), never inside a word. */
  function settingName(name) {
    var cell = make("td", "mono compare-setting");
    String(name).split(/([._])/).forEach(function (part, i) {
      if (!part) return;
      cell.appendChild(document.createTextNode(part));
      if (i % 2) cell.appendChild(document.createElement("wbr"));
    });
    return cell;
  }

  /* A setting's value; a length in ns as the pages say one, in ps under
   * 1 ns, where the server said it so (compare_view.py). */
  function settingSaid(d, side) {
    if (d[side + "_said"]) return d[side + "_said"];
    var value = d[side];
    return value == null ? "not set" : JSON.stringify(value);
  }

  /* The two the address names, or none. */
  function pairOf(hash) {
    var place = String(hash || "").replace(/^#/, "").split(/\/(.*)/)[1] || "";
    var parts = place.split("|");
    if (parts.length !== 2 || !parts[0] || !parts[1]) return null;
    try {
      return [decodeURIComponent(parts[0]), decodeURIComponent(parts[1])];
    } catch (error) {
      return null;
    }
  }

  function placeOf(first, second) {
    return encodeURIComponent(first) + "|" + encodeURIComponent(second);
  }

  /* Open the page on two folders (a step of its own). */
  function open(first, second) {
    if (!first || !second || !window.FastMDXDashboard) return;
    window.FastMDXDashboard.navigate("compare", { place: placeOf(first, second) });
    // What opened it is on a page now hidden: the keyboard and a screen
    // reader go on from the page's heading, as Back takes them there.
    var heading = document.querySelector('.page[data-page="compare"] .page-title');
    if (heading) {
      if (!heading.hasAttribute("tabindex")) heading.setAttribute("tabindex", "-1");
      heading.focus({ preventScroll: true });
    }
  }

  /* Nothing to compare, comparing, or not compared: one block, as every
   * page says it (page-states.js). */
  function say(state) {
    var empty = byId("compare-empty");
    if (!empty) return;
    if (!state) { empty.hidden = true; empty.replaceChildren(); return; }
    if (window.FastMDXPageState) window.FastMDXPageState.show(empty, state);
    else { empty.hidden = false; empty.textContent = state.title + (state.detail ? " " + state.detail : ""); }
  }

  /* The records of the two that were not read, each with why. */
  function sayNotRead(text) {
    var line = byId("compare-not-read");
    if (!line) return;
    line.hidden = !text;
    line.textContent = text || "";
  }

  function clear() {
    sayNotRead("");
    ["compare-heads", "compare-settings", "compare-means", "compare-series"].forEach(function (id) {
      var node = byId(id);
      if (node) { node.replaceChildren(); node.hidden = true; }
    });
    byId("compare-swap").hidden = true;
    byId("compare-beside").hidden = true;
    var page = document.querySelector('.page[data-page="compare"]');
    if (page) page.removeAttribute("data-compared");
  }

  var asking = "";

  function load() {
    var pair = pairOf(location.hash);
    // The page opened again on the two it shows, or is asking for: nothing
    // to read again.
    if (pair && (asking === location.hash || (shown && shown.hash === location.hash))) {
      return Promise.resolve(shown && shown.data);
    }
    clear();
    shown = null;
    if (!pair) {
      byId("compare-said").textContent = "Two studies, or two runs, side by side.";
      say({ kind: "empty", title: "Nothing to compare yet",
            detail: "Choose two studies in All studies, two runs on a study's Overview, or Compare " +
                    "with\u2026 in the palette (\u2318K)." });
      return Promise.resolve(null);
    }
    say({ kind: "loading", title: "Comparing\u2026" });
    var asked = location.hash;
    asking = asked;
    return fetch("/api/compared?" + new URLSearchParams({ a: pair[0], b: pair[1] }),
                 { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .catch(function () { return { ok: false, reason: "The server did not answer." }; })
      .then(function (data) {
        if (asking === asked) asking = "";
        if (location.hash !== asked) return null;
        draw(pair, data);
        return data;
      });
  }

  function draw(pair, data) {
    var page = document.querySelector('.page[data-page="compare"]');
    if (!data || !data.ok) {
      say({ kind: "error", title: "They could not be compared",
            detail: (data && (data.reason || data.error)) || "The server gave no reason.",
            action: { label: "Try again", run: function () { shown = null; asking = ""; load(); } } });
      if (page) page.setAttribute("data-compared", "no");
      return;
    }
    shown = { pair: pair, data: data, hash: location.hash };
    say(null);
    sayNotRead(data.not_read || "");
    byId("compare-said").textContent = data.first.said + " and " + data.second.said;
    byId("compare-swap").hidden = false;
    byId("compare-beside").hidden = false;
    heads(data);
    settings(data);
    means(data);
    series(data);
    if (page) page.setAttribute("data-compared", "yes");
  }

  /* Each of the two: its name, its system and state, and the way to open
   * it. */
  function heads(data) {
    var host = byId("compare-heads");
    host.hidden = false;
    [data.first, data.second].forEach(function (side, i) {
      var card = make("div", "card compare-head");
      card.dataset.side = i ? "second" : "first";
      var swatch = make("span", "compare-swatch");
      swatch.style.background = colourOf(i);
      var title = make("div", "compare-head-title");
      title.appendChild(swatch);
      title.appendChild(make("strong", "compare-head-name", side.said));
      card.appendChild(title);
      var facts = [side.system, side.kind, side.state].filter(Boolean).join(" \u00b7 ");
      if (facts) card.appendChild(make("div", "muted small", facts));
      if (side.run_of) {
        card.appendChild(make("div", "muted small", "A run of " + side.run_of.name + ": " +
                                                    side.run_of.label));
      }
      var go = make("button", "chip-btn compare-open", "Open");
      go.type = "button";
      go.title = "Open " + side.said + " on its Overview";
      go.addEventListener("click", function () { switchTo(side.path, "overview", side.resolved); });
      card.appendChild(go);
      host.appendChild(card);
    });
  }

  function settings(data) {
    var host = byId("compare-settings");
    host.hidden = false;
    host.appendChild(make("h2", "card-title", "Settings that differ"));
    var found = data.settings || [];
    if (!found.length) {
      host.appendChild(make("p", "muted small", "None: they asked for the same study."));
    } else {
      var table = make("table", "kv-table compare-settings-table");
      var top = make("tr", "compare-head-row");
      ["Setting", data.first.said, data.second.said].forEach(function (label) {
        top.appendChild(make("th", "", label));
      });
      table.appendChild(top);
      found.forEach(function (d, i) {
        var row = make("tr");
        if (i >= SHOWN_FIRST) row.hidden = true;
        row.appendChild(settingName(d.setting));
        row.appendChild(cell("mono", settingSaid(d, "first"), data.first.said));
        row.appendChild(cell("mono", settingSaid(d, "second"), data.second.said));
        table.appendChild(row);
      });
      host.appendChild(table);
      if (found.length > SHOWN_FIRST) {
        var more = make("button", "file-action", "Show the other " + (found.length - SHOWN_FIRST));
        more.type = "button";
        more.addEventListener("click", function () {
          table.querySelectorAll("tr[hidden]").forEach(function (row) { row.hidden = false; });
          more.remove();
        });
        host.appendChild(more);
      }
    }
    var unrecorded = data.unrecorded || [];
    if (unrecorded.length) {
      host.appendChild(make("p", "muted small",
        "Not compared: " + unrecorded.join(", ") + ". The run works " +
        (unrecorded.length === 1 ? "it" : "these") + " out from the other settings, " +
        "and one of the two never recorded " + (unrecorded.length === 1 ? "it" : "them") +
        " (it stopped or failed before them, or analysed a trajectory and ran no simulation)."));
    }
  }

  function means(data) {
    var host = byId("compare-means");
    host.hidden = false;
    host.appendChild(make("h2", "card-title", "What they recorded"));
    var found = data.measures || [];
    if (!found.length) {
      host.appendChild(make("p", "muted small", "Neither recorded a mean."));
      return;
    }
    var table = make("table", "kv-table compare-means-table");
    var top = make("tr", "compare-head-row");
    ["Quantity", data.first.said, data.second.said, "Difference"].forEach(function (label) {
      top.appendChild(make("th", "", label));
    });
    table.appendChild(top);
    var noMean = [], noted = [], notedResolved = [];
    found.forEach(function (m) {
      var row = make("tr");
      row.dataset.analysis = m.analysis;
      row.appendChild(cell("compare-quantity", m.label || m.analysis));
      var used = m.versus && m.versus.errors ? m.versus.errors : [null, null];
      [m.first, m.second].forEach(function (side, i) {
        var said = i ? data.second.said : data.first.said;
        var node = cell("mono", meanSaid(side, used[i]), said);
        if (used[i] != null && side && side.error != null && used[i] !== side.error) {
          node.title = "Its own error: " + withError(side.error, null, m.unit);
        }
        if (side && side.mean != null && side.noted) {
          // Its image note beside it (image_note.py), said in full under
          // the table: a difference from it may be that bias.
          var line = side.noted.charAt(0).toUpperCase() + side.noted.slice(1) + ".";
          node.appendChild(document.createTextNode(" †"));
          node.classList.add("is-noted");
          node.title = (node.title ? node.title + ". " : "") + line;
          noted.push("† " + said + ", " + (m.label || m.analysis) + ": " + side.noted + ".");
          if (m.versus && m.versus.resolved && notedResolved.indexOf(m) < 0) notedResolved.push(m);
        }
        row.appendChild(node);
        if (side && side.mean == null && side.withheld) {
          // One line for each run and reason, its quantities listed.
          var why = String(side.withheld).replace(/\.+$/, "");
          var key = said + "\u0000" + why;
          var entry = noMean.filter(function (e) { return e.key === key; })[0];
          if (!entry) noMean.push(entry = { key: key, said: said, why: why, what: [] });
          entry.what.push((m.label || m.analysis).toLowerCase());
        }
      });
      var difference = cell("mono", "", "Difference");
      if (m.versus && m.versus.error_of === "alone") {
        // Two repeats of one measurement, the only two read: never said
        // to differ; their spread against their own errors, as the
        // study's replicas are aggregated (batch/aggregate.py).
        // Without the error the same cell calls too tight.
        var check = m.versus.spread_against_own;
        difference.textContent = withError(m.versus.difference,
                                           check && check.too_tight ? null : m.versus.error, m.unit) +
          spreadSaid(check);
        difference.dataset.errorOf = "alone";
      } else if (m.versus) {
        difference.textContent = withError(m.versus.difference, m.versus.error, m.unit) +
          (m.versus.resolved ? ", resolved" : ", within the errors") +
          (ERROR_OF[m.versus.error_of] || "");
        if (m.versus.error_of) difference.dataset.errorOf = m.versus.error_of;
        if (m.versus.resolved) row.classList.add("is-resolved");
      } else {
        difference.textContent = "not compared";
      }
      row.appendChild(difference);
      table.appendChild(row);
    });
    host.appendChild(table);
    if (noMean.length) {
      // An analysis that ran and gave no mean says why (overview_view.py).
      host.appendChild(make("p", "muted small compare-no-mean",
        "Not determined, no mean: " + noMean.map(function (e) {
          return e.said + ", " + e.what.join(", ") + ": " + e.why;
        }).join("; ") + "."));
    }
    noted.forEach(function (line) {
      host.appendChild(make("p", "muted small compare-noted", line));
    });
    if (notedResolved.length) {
      // As the runs compared say it (runs_compared.py).
      var one = notedResolved.length === 1;
      host.appendChild(make("p", "muted small compare-noted",
        (one ? "One resolved difference has" : notedResolved.length + " resolved differences have") +
        " a mean that may be biased by the solute's own image: " + (one ? "it" : "each") +
        " may be that bias and not the setting."));
    }
    // Resolved or not is said of two measurements; of two replicas read
    // alone no row is judged so, and the rule is not said beside them.
    var judged = found.some(function (m) { return m.versus && m.versus.error_of !== "alone"; });
    host.appendChild(make("p", "muted small compare-difference-said",
      "The difference is the second's mean less the first's." + (judged || !found.some(
        function (m) { return m.versus; }) ? " It is resolved past " + data.resolved_at +
        " times the two's combined standard error." : "") +
      " A mean an analysis did not stand behind is not compared."));
    if (data.replicas) {
      // Two repeats of one measurement (batch/aggregate.py): their spread
      // is its error, and each run's own estimate can understate it.
      host.appendChild(make("p", "muted small compare-replicas", replicasSaid(data)));
    } else if (data.replicas_not_used || data.replicas_unknown) {
      host.appendChild(make("p", "muted small compare-replicas",
                            data.replicas_not_used || data.replicas_unknown));
    }
  }

  /* The replicas' note: what the two differ in, from the Settings diff
   * above; how many runs each spread was taken over, where fewer than all
   * of them were read ("from 2 of the 3 runs"); and the quantities only
   * these two were read for, set against their own errors. */
  function replicasSaid(data) {
    var n = data.replicas.runs;
    var fewer = {}, every = false, spread = false, alone = [];
    (data.measures || []).forEach(function (m) {
      var v = m.versus;
      if (!v || v.from_runs == null) return;
      if (v.error_of === "alone") { alone.push(m.label || m.analysis); return; }
      spread = true;
      var k = v.from_runs;
      if (k < n) (fewer[k] = fewer[k] || []).push(m.label || m.analysis);
      if (k === n) every = true;
    });
    var also = data.replicas.also_differ || [];
    // How the study's replicas differ, as every page says it
    // (batch/aggregate.py, how_replicas_differ).
    var differ = data.replicas.differ, differing = data.replicas.differing;
    var said = also.length
      ? "These are two of " + n + " runs the study ran as repeats of one measurement, " +
        differing + "; these two also differ in " + also.join(", ") +
        " (Settings that differ, above)."
      : "These are two of " + n + " runs " + differ + ": repeats of one measurement.";
    if (spread) {
      var counts = Object.keys(fewer).map(Number).sort(function (a, b) { return b - a; });
      var of = counts.length
        ? "the spread of the runs' means that were read (" + counts.map(function (k) {
            return fewer[k].join(", ") + " from " + k + " of the " + n + " runs";
          }).join("; ") + (every ? "; the others from all " + n : "") + ")"
        : "the spread of the " + n + " runs' means";
      said += " Each mean's error, as the columns give it, is " + of + ", or the run's own " +
        "where that is larger, as the study's replicas are aggregated; each difference says " +
        "which it was set against.";
    }
    if (alone.length) {
      // "Only these two read" where the study has more runs than the two.
      var of = data.replicas.of || n;
      said += " " + alone.join(", ") + (of > 2 ? ": only these two runs' means were read"
        : ": the study has these two runs alone") + ", and the spread of two means is their " +
        "difference over the square root of 2, so a difference set against it would equal " +
        "its error whatever the two are. As the study's replicas are aggregated, their spread " +
        "is set against the errors the two estimated for themselves instead" +
        (factorOf(data) ? ": past " + factorOf(data) + " times those, the errors are too tight"
          : "") + ". Two repeats of one measurement are not said to differ.";
    }
    return said;
  }

  /* The factor the replicas' spread is held to (batch/aggregate.py). */
  function factorOf(data) {
    var found = (data.measures || []).map(function (m) {
      return m.versus && m.versus.spread_against_own;
    }).filter(Boolean)[0];
    return found ? found.factor : null;
  }

  function colourOf(i) {
    var runs = window.FastMDXRunsCompared;
    return runs && runs.colourOf ? runs.colourOf(i) : (i ? "#E69F00" : "#0072B2");
  }

  /* Each series both recorded, the two overlaid on one clock. */
  function series(data) {
    var host = byId("compare-series");
    host.hidden = false;
    host.appendChild(make("h2", "card-title", "Over time"));
    var found = data.series || [];
    var unread = data.series_not_read || [];
    if (unread.length) {
      // Both recorded it, a record of one was not read (named above).
      host.appendChild(make("p", "muted small compare-series-unread",
        "Not plotted: " + unread.join(", ") + ". A record of one of the two was not read."));
    }
    if (!found.length) {
      if (!unread.length) {
        host.appendChild(make("p", "muted small", "They recorded no series over time in common."));
      }
      return;
    }
    var runs = window.FastMDXRunsCompared;
    // One clock for the card: every series on production's time, said in
    // one unit chosen over all of them (ps under 1 ns).
    var times = [];
    found.forEach(function (s) {
      [s.first, s.second].forEach(function (line) {
        if (line.x_label === "Time (ns)") times = times.concat(line.x);
      });
    });
    found.forEach(function (s) {
      var block = make("div", "compare-series-block");
      block.dataset.analysis = s.analysis;
      var chart = make("div", "compare-series-chart");
      var legend = make("div", "runs-overlay-legend compare-series-legend");
      block.appendChild(chart);
      block.appendChild(legend);
      host.appendChild(block);
      if (s.first.x_label !== s.second.x_label) {
        // One on production's clock, the other on frame numbers: drawn on
        // one axis, they would say the frames line up in time.
        chart.appendChild(make("p", "muted small", (s.label || s.analysis) + ": not overlaid. " +
          "One is recorded against " + s.first.x_label.toLowerCase() + ", the other against " +
          s.second.x_label.toLowerCase() + "."));
        return;
      }
      if (!runs || !runs.drawOverlay) return;
      var drawn = [data.first, data.second].map(function (side, i) {
        var line = i ? s.second : s.first;
        return { r: { run: { run_id: i ? "second" : "first", label: side.said }, index: i },
                 series: { x: line.x, y: line.y, x_label: line.x_label } };
      });
      runs.drawOverlay(chart, legend, drawn, { label: s.label, unit: s.unit }, 0, times);
      chart.setAttribute("data-drawn", "2");
    });
  }

  /* The study open here, by its folder, or "". */
  function openHere() {
    try {
      return window.FastMDXDashboard.state.appState.active_run || "";
    } catch (error) {
      return "";
    }
  }

  /* Open a folder on a page, as every way to another study does
   * (place.js): asked first whether a switch would lose work here, then
   * the server's study switched and the page loaded again on it. The one
   * open already is only gone to. False where the switch was held. */
  function switchTo(folder, page, resolved) {
    var here = openHere();
    if (here && (folder === here || resolved === here)) {
      window.FastMDXDashboard.navigate(page);
      return true;
    }
    return Boolean(window.FastMDXPlace && window.FastMDXPlace.open(folder, page));
  }

  /* The first in the Viewer, the second played beside it (viewer-beside.js).
   * Opening the first loads the page again; what to show beside it is kept
   * for the Viewer to take up then. */
  function playBeside() {
    if (!shown) return;
    var first = shown.pair[0], second = shown.pair[1];
    var beside = window.FastMDXBeside;
    var said = shown.data.second.said;
    var resolved = shown.data.first.resolved;
    // Kept only for a switch that goes on, and only for the study it opens
    // (as asked, or as the server opens it, its links resolved).
    if (switchTo(first, "viewer", resolved) && beside && beside.next) {
      beside.next(second, said, [first, resolved || first]);
    }
  }

  function swap() {
    if (shown) open(shown.pair[1], shown.pair[0]);
  }

  function wire() {
    if (!byId("compare-heads")) return;
    byId("compare-swap").addEventListener("click", swap);
    byId("compare-beside").addEventListener("click", playBeside);
    window.addEventListener("dashboard:navigate", function (event) {
      if (event.detail && event.detail.page === "compare") load();
    });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", wire);
  else wire();

  window.FastMDXCompare = { open: open, load: load, pairOf: pairOf, withError: withError };
}());
