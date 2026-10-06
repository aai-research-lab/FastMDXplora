/* The Overview, results first (overview_view.py, /api/overview).
 *
 * A study that has ended opens on what its analyses determined: each mean
 * with its error, the independent samples behind it and where its
 * equilibrated part began, Determined or Not determined with the reason on
 * hover, and its series on the production's clock. Then the run in one card
 * (its phases and how long each took, what it ran) and its thermodynamics
 * (charts.js). A running study keeps its health and charts first. The study,
 * its word and its facts lead, in a line.
 *
 * Every plot on the page reads time from the start of production, the
 * equilibration before 0, as the analyses' own times do, and one crosshair
 * moves across them all: a plot pointed at says where with the event
 * `fmx:crosshair` ({t_ns}), and every plot marks that moment.
 */
(function () {
  "use strict";

  var SVG = "http://www.w3.org/2000/svg";
  var data = null;
  var loading = null;
  var loadedFor = "";
  var lastLoad = 0;
  var AGAIN_MS = 20000;
  var series = {};
  var sparks = [];
  /* The payload as last rendered: one that has not changed is not rendered
   * again, so a tile keeps its focus and its hint while a run writes. */
  var shown = "";
  var tileCount = 0;

  function byId(id) { return document.getElementById(id); }

  function make(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  function svg(tag, attrs, parent) {
    var node = document.createElementNS(SVG, tag);
    Object.keys(attrs || {}).forEach(function (key) { node.setAttribute(key, attrs[key]); });
    if (parent) parent.appendChild(node);
    return node;
  }

  /* A time on the production's clock, in the unit its span reads in. */
  function sayTime(ns, spanNs) {
    if (ns == null || !isFinite(ns)) return "";
    var ps = (spanNs == null ? Math.abs(ns) : spanNs) < 1;
    return ps ? (Math.round(ns * 10000) / 10) + " ps" : (Math.round(ns * 1000) / 1000) + " ns";
  }

  function body() { return byId("overview-body"); }
  function ended() { var b = body(); return !!b && b.dataset.run === "ended"; }

  /* ------------------------------------------------------------------ */
  /* What the analyses determined                                        */
  /* ------------------------------------------------------------------ */

  function renderTiles() {
    var host = byId("overview-tiles");
    var section = byId("overview-results");
    if (!host || !section) return;
    var tiles = data && data.tiles ? data.tiles : [];
    section.hidden = tiles.length === 0;
    sparks = [];
    host.replaceChildren();
    tiles.forEach(function (tile) { host.appendChild(tileFor(tile)); });
    var said = byId("overview-results-said");
    if (said) said.replaceChildren();
    if (said && data) {
      if (data.means) {
        said.appendChild(document.createTextNode(
          data.determined + " of " + data.means + " mean" + (data.means === 1 ? "" : "s") +
          " determined" + (data.more ? ", " + data.more + " more not shown" : "") + " · "));
      }
      var all = make("a", "overview-all-link",
        "All " + data.analyses + " analys" + (data.analyses === 1 ? "is" : "es") +
        " on the Analysis page →");
      all.href = "#analysis";
      said.appendChild(all);
    }
    drawSparks();
  }

  function tileFor(tile) {
    var link = make("a", "overview-tile");
    link.href = "#analysis";
    link.dataset.tileAnalysis = tile.analysis;
    var perResidue = tile.kind === "per_residue";
    link.dataset.determined = perResidue ? "" : String(!!tile.determined);
    var head = make("div", "tile-head");
    head.appendChild(make("span", "tile-label", tile.label));
    var badge = make("span", "tile-badge",
      perResidue ? "per " + tile.each : tile.determined ? "Determined" : "Not determined");
    badge.dataset.kind = perResidue ? "per" : tile.determined ? "yes" : "no";
    head.appendChild(badge);
    link.appendChild(head);
    link.appendChild(make("div", "tile-value mono", tile.said));
    var note = perResidue
      ? "over " + tile.count.toLocaleString() + " " + tile.each + "s, mean " + tile.mean_said
      : [tile.samples != null ? Math.round(tile.samples) + " independent sample" +
           (Math.round(tile.samples) === 1 ? "" : "s") : "",
         tile.from_ns != null ? "from " + sayTime(tile.from_ns) : "",
         tile.reweighted ? "reweighted" : ""].filter(Boolean).join(" · ");
    link.appendChild(make("div", "tile-note", note));
    if (perResidue) {
      link.appendChild(bars(tile.values));
    } else if (tile.series) {
      var plot = make("div", "tile-plot");
      plot.dataset.series = tile.series;
      if (tile.from_ns != null) plot.dataset.from = String(tile.from_ns);
      link.appendChild(plot);
      sparks.push(plot);
      want(tile.series);
    }
    if (!perResidue && !tile.determined && tile.why) {
      // Shown on hover or focus, and the tile's description for a screen
      // reader, which does not read what is not displayed.
      tileCount += 1;
      var why = make("div", "tile-why");
      why.id = "tile-why-" + tileCount;
      why.appendChild(make("b", "", "Not determined. "));
      why.appendChild(document.createTextNode(tile.why));
      link.appendChild(why);
      link.setAttribute("aria-describedby", why.id);
    }

    return link;
  }

  function bars(values) {
    var box = make("div", "tile-plot tile-bars");
    var most = Math.max.apply(null, values.concat([1e-12]));
    var plot = svg("svg", { viewBox: "0 0 " + values.length + " 40", preserveAspectRatio: "none",
                            "aria-hidden": "true" }, null);
    values.forEach(function (value, i) {
      var h = Math.max(0.5, value / most * 38);
      svg("rect", { x: i + 0.12, width: 0.76, y: 40 - h, height: h, class: "tile-bar" }, plot);
    });
    box.appendChild(plot);
    return box;
  }

  function want(name) {
    if (series[name] !== undefined) return;
    series[name] = null;
    fetch("/api/series?analysis=" + encodeURIComponent(name))
      .then(function (r) { return r.json(); })
      .then(function (found) {
        series[name] = found && found.ok && found.kind === "time" ? found : false;
        drawSparks();
      })
      .catch(function () { series[name] = undefined; });
  }

  /* Each tile's series over the production: its equilibrated part shaded
   * from where the mean begins, plotted at the width it has. */
  function drawSparks() {
    sparks.forEach(function (box) {
      var found = series[box.dataset.series];
      if (!found) return;
      var width = Math.max(60, Math.round(box.clientWidth));
      var height = 42;
      var timed = /time|ns|ps/i.test(String(found.x_label || ""));
      var xs = found.x, ys = found.y;
      if (!xs || xs.length < 2) return;
      var x0 = timed ? 0 : xs[0], x1 = xs[xs.length - 1];
      var lo = Math.min.apply(null, ys), hi = Math.max.apply(null, ys);
      var pad = (hi - lo) * 0.1 || Math.abs(hi) * 0.01 || 1;
      lo -= pad; hi += pad;
      var px = function (x) { return 1 + (x - x0) / ((x1 - x0) || 1) * (width - 2); };
      var py = function (y) { return 2 + (1 - (y - lo) / (hi - lo)) * (height - 4); };
      var plot = svg("svg", { width: width, height: height, viewBox: "0 0 " + width + " " + height,
                              "aria-hidden": "true" }, null);
      var from = parseFloat(box.dataset.from);
      if (timed && isFinite(from)) {
        svg("rect", { x: px(from), y: 0, width: Math.max(0, px(x1) - px(from)), height: height,
                      class: "spark-equilibrated" }, plot);
        svg("line", { x1: px(from), x2: px(from), y1: 0, y2: height, class: "spark-from" }, plot);
      }
      var d = "";
      for (var i = 0; i < xs.length; i += 1) {
        d += (i ? "L" : "M") + px(xs[i]).toFixed(1) + " " + py(ys[i]).toFixed(1);
      }
      svg("path", { d: d, class: "spark-line" }, plot);
      var cross = svg("line", { y1: 0, y2: height, class: "spark-cross" }, plot);
      cross.style.display = "none";
      box.replaceChildren(plot);
      box._spark = { xs: xs, timed: timed, px: px, cross: cross, x0: x0, x1: x1, width: width };
      if (timed) {
        plot.addEventListener("mousemove", function (event) {
          var r = plot.getBoundingClientRect();
          var t = x0 + ((event.clientX - r.left) / r.width * width - 1) / (width - 2) * (x1 - x0);
          point(t);
        });
        plot.addEventListener("mouseleave", function () { point(null); });
      }
    });
  }

  function point(tNs) {
    window.dispatchEvent(new CustomEvent("fmx:crosshair", { detail: { t_ns: tNs } }));
  }

  window.addEventListener("fmx:crosshair", function (event) {
    var t = event.detail ? event.detail.t_ns : null;
    sparks.forEach(function (box) {
      var s = box._spark;
      if (!s || !s.timed) return;
      if (t == null || t < s.x0 || t > s.x1) { s.cross.style.display = "none"; return; }
      var x = s.px(t);
      s.cross.setAttribute("x1", x);
      s.cross.setAttribute("x2", x);
      s.cross.style.display = "";
    });
  });

  /* ------------------------------------------------------------------ */
  /* The run                                                             */
  /* ------------------------------------------------------------------ */

  var PHASE_WORDS = { setup: "Setup", simulation: "Simulation", analysis: "Analysis", report: "Report" };

  function duration(seconds) {
    if (seconds == null) return "";
    if (seconds < 60) return Math.round(seconds) + " s";
    var m = Math.floor(seconds / 60), h = Math.floor(m / 60);
    if (h) return h + "h " + (m % 60) + "m";
    return m + "m " + Math.round(seconds % 60) + "s";
  }

  function renderPhases() {
    var host = byId("overview-phases");
    if (!host) return;
    var phases = data && data.phases ? data.phases : [];
    host.hidden = phases.length === 0;
    host.replaceChildren.apply(host, phases.map(function (phase) {
      var item = make("li", "overview-phase");
      var status = String(phase.status || "").toLowerCase();
      item.dataset.state = status === "ok" ? "done" : status === "error" ? "failed" : status;
      item.appendChild(make("span", "phase-name", PHASE_WORDS[phase.name] || phase.name));
      item.appendChild(make("span", "phase-time mono",
        status === "error" ? "failed" : status === "skipped" ? "skipped" : duration(phase.seconds)));
      return item;
    }));
  }

  /* Atoms and platform, from what the page already has. */
  function renderFacts(results) {
    var cards = results && Array.isArray(results.summary_cards) ? results.summary_cards : null;
    if (cards) {
      var atoms = cards.find(function (card) { return card.label === "Atom count"; });
      var row = byId("overview-atoms-row");
      if (row) row.hidden = !atoms || !atoms.value || atoms.value === "—";
      if (atoms) {
        byId("overview-atoms-cell").textContent = atoms.value;
        byId("overview-atoms-note").textContent = atoms.detail || "";
      }
    }
    var platform = (byId("sidebar-platform") || {}).textContent || "";
    var speed = (document.querySelector('[data-chart-value="speed"]') || {}).textContent || "";
    var prow = byId("overview-platform-row");
    var known = platform && platform !== "—";
    if (prow) prow.hidden = !known;
    if (known) {
      byId("overview-platform-cell").textContent = platform +
        (speed && speed !== "—" ? " · " + speed + " ns/day" : "");
    }
    renderVerdict();
  }

  /* The study, its word and light (the sidebar's), and what it ran. */
  function renderVerdict() {
    var box = byId("overview-verdict");
    if (!box) return;
    var name = ((byId("topbar-run-title") || {}).textContent || "").trim();
    var open = name && name !== "No active study";
    box.hidden = !open;
    if (!open) return;
    byId("verdict-name").textContent = name;
    byId("verdict-word").textContent = ((byId("topbar-status-text") || {}).textContent || "").trim();
    var dot = byId("topbar-status-dot");
    byId("verdict-dot").className = dot ? dot.className : "status-dot";
    var chip = byId("verdict-chip");
    if (chip && dot) {
      var tone = /status-dot-live/.test(dot.className) ? "live"
        : /status-dot-completed/.test(dot.className) ? "completed"
        : /status-dot-error/.test(dot.className) ? "failed"
        : /status-dot-waiting/.test(dot.className) ? "waiting" : "quiet";
      chip.dataset.tone = tone;
    }
    var text = function (id) { return ((byId(id) || {}).textContent || "").trim(); };
    var parts = [];
    var length = text("live-simtime-cell");
    if (length && length !== "—") {
      var label = text("live-simtime-label").toLowerCase();
      var note = byId("live-simtime-note");
      parts.push(length + " " + label + (note && !note.hidden && note.textContent ? " " + note.textContent : ""));
    }
    var atomsRow = byId("overview-atoms-row");
    if (atomsRow && !atomsRow.hidden) parts.push(text("overview-atoms-cell") + " atoms");
    var platform = text("sidebar-platform");
    if (platform && platform !== "—") parts.push(platform);
    var wall = text("live-elapsed-cell");
    if (wall && wall !== "—") parts.push(wall);
    var when = text("live-lastupdate-cell");
    if (when && when !== "—") parts.push(when);
    byId("verdict-facts").textContent = parts.join(" · ");
  }

  /* ------------------------------------------------------------------ */
  /* Loading                                                             */
  /* ------------------------------------------------------------------ */

  /* What the page shows, as given: rendered, and told to the charts. */
  function use(found) {
    var said = found ? JSON.stringify(found) : "";
    if (said === shown) return;
    shown = said;
    // An analysis run again may have a new series behind its tile.
    series = {};
    data = found;
    renderTiles();
    renderPhases();
    window.dispatchEvent(new CustomEvent("fmx:overview", { detail: data }));
  }

  function load(force) {
    var folder = ((byId("sidebar-output-folder") || {}).textContent || "").trim();
    if (!folder || folder === "\u2014") {
      loadedFor = "";
      use(null);
      return Promise.resolve(null);
    }
    var stale = Date.now() - lastLoad > AGAIN_MS;
    if (!force && folder === loadedFor && !stale) return Promise.resolve(data);
    if (loading) return loading;
    lastLoad = Date.now();
    loading = fetch("/api/overview")
      .then(function (r) { return r.json(); })
      .then(function (found) {
        loading = null;
        loadedFor = folder;
        use(found && found.ok ? found : null);
        return data;
      })
      .catch(function () {
        // Nothing kept from another study, or from before.
        loading = null;
        loadedFor = "";
        use(null);
        return null;
      });
    return loading;
  }

  document.addEventListener("DOMContentLoaded", function () {
    if (!byId("overview-body")) return;
    // Results arrive with every poll while a run writes; the record is
    // read again at most every 20 s then, and at once for another study.
    window.addEventListener("dashboard:results-updated", function (event) {
      renderFacts(event.detail);
      load(false);
    });
    window.addEventListener("dashboard:status-updated", function () {
      renderVerdict();
      load(false);
    });
    window.addEventListener("dashboard:metrics-updated", function () { renderFacts(null); });
    window.addEventListener("dashboard:run-changed", function () {
      use(null);
      load(true);
    });
    window.addEventListener("dashboard:live-page-opened", function () {
      requestAnimationFrame(drawSparks);
    });
    window.addEventListener("resize", function () { requestAnimationFrame(drawSparks); });
    document.addEventListener("fmx:theme", function () { requestAnimationFrame(drawSparks); });
    // A tile opens its analysis on the Analysis page.
    document.addEventListener("click", function (event) {
      if (event.metaKey || event.ctrlKey || !event.target.closest) return;
      var tile = event.target.closest("[data-tile-analysis]");
      var all = event.target.closest(".overview-all-link");
      if (!tile && !all) return;
      event.preventDefault();
      var dashboard = window.FastMDXDashboard;
      if (!dashboard) return;
      if (tile) dashboard.showAnalysis(tile.dataset.tileAnalysis);
      else dashboard.navigate("analysis");
    });
  });

  window.FastMDXOverview = {
    load: load,
    get data() { return data; },
    ended: ended,
    sayTime: sayTime,
  };
})();
