/* A study run until it is determined (stopping_view.py), on the Overview.
 *
 * `simulation.stop_when` runs a study in pieces and judges it after each.
 * The question a person watching it has is the rule's own: is the answer
 * determined yet? For each measure this draws the error after each round
 * against the error asked for, with the production at which the error
 * would reach it if it keeps falling as one over the root of the frames,
 * and the mean after each round with every replica's own mean beside it,
 * so replicas that disagree are seen as well as said. Everything is built
 * as elements; a name read from a study is data, never markup. */
(function () {
  "use strict";

  var SVG = "http://www.w3.org/2000/svg";
  var FALLBACK = ["#0072B2", "#E69F00", "#009E73", "#CC79A7", "#56B4E9", "#D55E00"];
  var last = null;       // the last payload drawn
  var pending = false;   // a request in flight

  var STATE_WORDS = {
    met: "determined as asked",
    "short": "not yet as asked",
    disagree: "replicas disagree",
    "not yet a measurement": "not yet a measurement",
    "no mean": "no mean recorded",
    waiting: "first piece running"
  };
  var PILL = { met: "completed", running: "live", ceiling: "waiting", rounds: "waiting",
               stopped: "error", not_applied: "stale" };
  var DECIDED = { extend: "extended", met: "stopped, determined", ceiling: "stopped at the ceiling",
                  stopped: "stopped" };

  function palette() {
    var shared = window.FastMDXRunsCompared && window.FastMDXRunsCompared.palette;
    return shared && shared.length ? shared : FALLBACK;
  }

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  function svgEl(tag, attrs, cls) {
    var node = document.createElementNS(SVG, tag);
    Object.keys(attrs || {}).forEach(function (k) { node.setAttribute(k, attrs[k]); });
    if (cls) node.setAttribute("class", cls);
    return node;
  }

  function titled(node, text) {
    var t = document.createElementNS(SVG, "title");
    t.textContent = text;
    node.appendChild(t);
    return node;
  }

  /* To the precision its error allows: the error to two figures. */
  function withError(mean, error) {
    if (error === null || error === undefined || !(error > 0)) return plain(mean);
    var places = Math.max(0, 1 - Math.floor(Math.log10(error)));
    return mean.toFixed(places) + " ± " + error.toFixed(places);
  }

  function plain(value) {
    if (value === null || value === undefined) return "";
    return String(+Number(value).toPrecision(3));
  }

  function ns(value) {
    return value === null || value === undefined ? "" : +Number(value).toPrecision(3) + " ns";
  }

  function duration(seconds) {
    var minutes = Math.max(1, Math.round(seconds / 60));
    if (minutes < 60) return minutes + " min";
    var hours = Math.floor(minutes / 60);
    minutes = minutes % 60;
    if (hours < 48) return hours + " h" + (minutes ? " " + minutes + " min" : "");
    return (hours / 24).toFixed(1) + " days";
  }

  function ticks(low, high, count) {
    var shared = window.FastMDXSeries && window.FastMDXSeries.ticks;
    return shared ? shared(low, high, count) : [low, high];
  }

  /* A frame for one small chart: axes, ticks and labels, and the scales. */
  function frame(title, xMax, yLow, yHigh, xLabel, yLabel) {
    var W = 460, H = 220, L = 56, R = 14, T = 14, B = 38;
    var svg = svgEl("svg", { viewBox: "0 0 " + W + " " + H, role: "img" }, "stopping-svg");
    svg.setAttribute("aria-label", title);
    var px = function (x) { return L + (W - L - R) * x / xMax; };
    var py = function (y) { return H - B - (H - T - B) * (y - yLow) / (yHigh - yLow); };
    ticks(yLow, yHigh, 4).forEach(function (v) {
      svg.appendChild(svgEl("line", { x1: L, x2: W - R, y1: py(v), y2: py(v) }, "stopping-grid"));
      var t = svgEl("text", { x: L - 6, y: py(v) + 4, "text-anchor": "end" }, "stopping-tick");
      t.textContent = +v.toPrecision(3);
      svg.appendChild(t);
    });
    ticks(0, xMax, 5).forEach(function (v) {
      var t = svgEl("text", { x: px(v), y: H - B + 15, "text-anchor": "middle" }, "stopping-tick");
      t.textContent = +v.toPrecision(3);
      svg.appendChild(t);
    });
    svg.appendChild(svgEl("line", { x1: L, x2: W - R, y1: H - B, y2: H - B }, "stopping-axis"));
    svg.appendChild(svgEl("line", { x1: L, x2: L, y1: T, y2: H - B }, "stopping-axis"));
    var xl = svgEl("text", { x: (L + W - R) / 2, y: H - 5, "text-anchor": "middle" }, "stopping-label");
    xl.textContent = xLabel;
    svg.appendChild(xl);
    var yl = svgEl("text", { transform: "translate(13," + ((T + H - B) / 2) + ") rotate(-90)",
                             "text-anchor": "middle" }, "stopping-label");
    yl.textContent = yLabel;
    svg.appendChild(yl);
    return { svg: svg, px: px, py: py, W: W, H: H, L: L, R: R, T: T, B: B };
  }

  function ceilingLine(f, ceiling, xMax) {
    if (!(ceiling > 0) || ceiling > xMax) return;
    var line = svgEl("line", { x1: f.px(ceiling), x2: f.px(ceiling), y1: f.T, y2: f.H - f.B },
                     "stopping-ceiling");
    f.svg.appendChild(titled(line, "Ceiling: at most " + ns(ceiling) + " of production"));
  }

  /* The error after each round, against the error asked for. */
  function errorChart(m, data, xMax) {
    var measured = m.points.filter(function (p) { return p.measured; });
    var unit = m.unit ? " (" + m.unit + ")" : "";
    var top = 0;
    measured.forEach(function (p) { top = Math.max(top, p.error, p.allowed || 0); });
    if (!(top > 0)) {
      m.points.forEach(function (p) { top = Math.max(top, p.allowed || 0); });
    }
    if (!(top > 0)) top = 1;
    var f = frame(m.label + ": standard error after each round, against the " + m.asked +
                  " asked", xMax, 0, top * 1.18, "Production per run (ns)", "Standard error" + unit);
    ceilingLine(f, data.ceiling_ns, xMax);

    // The target: a level for an absolute error; for a relative one it
    // moves with the mean, so it is drawn through each round's value.
    var targets = m.points.filter(function (p) { return p.allowed !== null; });
    if (targets.length) {
      var pts = [];
      targets.forEach(function (p, i) {
        var x = f.px(p.production_ns);
        if (i === 0) pts.push(f.px(0) + "," + f.py(p.allowed));
        else pts.push(x + "," + f.py(targets[i - 1].allowed));
        pts.push(x + "," + f.py(p.allowed));
      });
      pts.push(f.px(xMax) + "," + f.py(targets[targets.length - 1].allowed));
      f.svg.appendChild(titled(svgEl("polyline", { points: pts.join(" ") }, "stopping-target"),
                               "Asked: " + m.asked));
      var label = svgEl("text", { x: f.W - f.R - 4, y: f.py(targets[targets.length - 1].allowed) - 5,
                                  "text-anchor": "end" }, "stopping-target-label");
      label.textContent = "asked " + m.asked;
      f.svg.appendChild(label);
    }

    // Where the error would reach the target at the rate it has fallen.
    var proj = m.projection;
    if (proj) {
      var end = Math.min(proj.to_ns, xMax);
      var path = [];
      for (var k = 0; k <= 24; k++) {
        var x = proj.from_ns + (end - proj.from_ns) * k / 24;
        var e = proj.error * Math.sqrt(proj.kept_ns / (proj.kept_ns + x - proj.from_ns));
        path.push(f.px(x).toFixed(1) + "," + f.py(e).toFixed(1));
      }
      f.svg.appendChild(titled(svgEl("polyline", { points: path.join(" ") }, "stopping-projection"),
        "If the error keeps falling as one over the root of the frames, it reaches the " +
        m.asked + " asked at about " + ns(proj.to_ns) + " per run"));
      if (proj.to_ns <= xMax) {
        f.svg.appendChild(titled(svgEl("circle", { cx: f.px(proj.to_ns), cy: f.py(proj.allowed), r: 3.5 },
                                       "stopping-projected"), "Projected: " + ns(proj.to_ns)));
      }
    }

    m.points.forEach(function (p) {
      if (!p.measured) {
        var x0 = f.px(p.production_ns), y0 = f.H - f.B;
        f.svg.appendChild(titled(svgEl("path", {
          d: "M" + (x0 - 5) + "," + (y0 + 1) + " L" + (x0 + 5) + "," + (y0 + 1) + " L" + x0 + "," + (y0 - 8) + " Z"
        }, "stopping-withheld"), "Round " + p.round + ", " + ns(p.production_ns) + ": " + p.said));
        return;
      }
      var cls = "stopping-point " + (p.met ? "is-met" : p.agree === false ? "is-disagree" : "is-short");
      f.svg.appendChild(titled(svgEl("circle", { cx: f.px(p.production_ns), cy: f.py(p.error), r: 4.5 }, cls),
        "Round " + p.round + ", " + ns(p.production_ns) + ": error " + plain(p.error) + unit.replace(/[()]/g, "") +
        (p.allowed !== null ? ", asked " + plain(p.allowed) : "") +
        (p.agree === false ? "; the replicas disagree" : "")));
    });
    return f.svg;
  }

  /* The mean after each round, with each replica's own mean beside it. */
  function meanChart(m, data, xMax) {
    var colours = palette();
    var runs = data.runs;
    var lows = [], highs = [];
    m.points.forEach(function (p) {
      if (p.measured) { lows.push(p.value - p.error); highs.push(p.value + p.error); }
      p.replicas.forEach(function (r) {
        if (r.mean === null) return;
        var se = r.standard_error || 0;
        lows.push(r.mean - se); highs.push(r.mean + se);
      });
    });
    if (!lows.length) return null;
    var low = Math.min.apply(null, lows), high = Math.max.apply(null, highs);
    var pad = (high - low) * 0.12 || Math.abs(high) * 0.05 || 1;
    var unit = m.unit ? " (" + m.unit + ")" : "";
    var f = frame(m.label + ": the mean after each round, with each replica's own mean",
                  xMax, low - pad, high + pad, "Production per run (ns)", m.label + unit);
    ceilingLine(f, data.ceiling_ns, xMax);
    // Each replica just to the right of the pooled mean it went into, so
    // the one does not hide the others.
    var step = 5;
    m.points.forEach(function (p) {
      var cx = f.px(p.production_ns);
      p.replicas.forEach(function (r, i) {
        if (r.mean === null) return;
        var index = runs.indexOf(r.run);
        var x = cx + 7 + i * step;
        var se = r.standard_error || 0;
        var colour = colours[(index < 0 ? i : index) % colours.length];
        var bar = svgEl("line", { x1: x, x2: x, y1: f.py(r.mean - se), y2: f.py(r.mean + se),
                                  stroke: colour }, "stopping-replica-bar");
        f.svg.appendChild(bar);
        f.svg.appendChild(titled(svgEl("circle", { cx: x, cy: f.py(r.mean), r: 3, fill: colour },
                                       "stopping-replica"),
          r.run + ", round " + p.round + ": " + withError(r.mean, r.standard_error)));
      });
      if (!p.measured) return;
      var cls = p.agree === false ? " is-disagree" : "";
      f.svg.appendChild(svgEl("line", { x1: cx, x2: cx, y1: f.py(p.value - p.error),
                                        y2: f.py(p.value + p.error) }, "stopping-mean-bar" + cls));
      f.svg.appendChild(titled(svgEl("rect", { x: cx - 4, y: f.py(p.value) - 4, width: 8, height: 8 },
                                     "stopping-mean" + cls),
        "Round " + p.round + (p.replicas.length ? ", replicas pooled: " : ": ") +
        withError(p.value, p.error) + unit.replace(/[()]/g, "")));
    });
    return f.svg;
  }

  function measureBlock(m, data, xMax) {
    var block = el("section", "stopping-measure");
    block.setAttribute("data-analysis", m.analysis);
    block.setAttribute("data-state", m.state);
    var head = el("div", "stopping-measure-head");
    head.appendChild(el("h3", "stopping-measure-title", m.label));
    head.appendChild(el("span", "stopping-asked mono", "asked " + m.asked));
    var chip = el("span", "stopping-chip", STATE_WORDS[m.state] || m.state);
    chip.setAttribute("data-state", m.state);
    head.appendChild(chip);
    block.appendChild(head);

    var now = m.points.length ? m.points[m.points.length - 1] : null;
    var line = el("p", "stopping-now");
    if (!now) {
      line.textContent = "Judged when the first piece's analyses are done.";
    } else if (now.measured) {
      line.textContent = "Now " + withError(now.value, now.error) + (m.unit ? " " + m.unit : "") +
        " after " + ns(now.production_ns) + (now.replicas.length ? " per run, " + now.replicas.length + " replicas pooled" : "") +
        (m.projection ? "; at the rate it has fallen, " + m.asked + " at about " + ns(m.projection.to_ns) : "") + ".";
    } else {
      line.textContent = now.said + ".";
    }
    block.appendChild(line);

    var charts = el("div", "stopping-charts");
    if (m.points.length) {
      charts.appendChild(errorChart(m, data, xMax));
      var means = meanChart(m, data, xMax);
      if (means) charts.appendChild(means);
    }
    block.appendChild(charts);
    return block;
  }

  /* What the marks mean, once for the card, with each replica's colour. */
  function key(data) {
    var host = document.getElementById("stopping-key");
    host.innerHTML = "";
    var item = function (cls, text, colour) {
      var span = el("span", "stopping-key-item");
      var mark = el("span", "stopping-key-mark " + cls);
      if (colour) mark.style.background = colour;
      span.appendChild(mark);
      span.appendChild(document.createTextNode(text));
      host.appendChild(span);
    };
    item("is-short", "error, not yet as asked");
    item("is-met", "error as asked");
    if (data.runs.length > 1) {
      item("is-disagree", "replicas disagree");
      item("is-mean", "pooled mean ± error");
      var colours = palette();
      data.runs.forEach(function (run, i) { item("is-replica", run, colours[i % colours.length]); });
    } else {
      item("is-mean", "mean ± error");
    }
    item("is-withheld", "not yet a measurement");
    item("is-target", "asked");
    item("is-projection", "at the rate it has fallen");
    item("is-ceiling", "ceiling");
  }

  function strip(data) {
    var dl = document.getElementById("stopping-strip");
    dl.innerHTML = "";
    var add = function (term, value, title) {
      var div = el("div");
      div.appendChild(el("dt", "", term));
      var dd = el("dd", "mono", value);
      if (title) dd.title = title;
      div.appendChild(dd);
      dl.appendChild(div);
    };
    add("Production per run", ns(data.production_ns) + (data.ceiling_ns ? " of " + ns(data.ceiling_ns) : ""));
    add("Runs", data.runs.length > 1
      ? data.runs.length + " replicas" : "1 run",
      data.independent_starts === "required" ? "The replicas must agree before the rule is met"
        : "One run's own precision; not checked against independent starts");
    add("Rounds judged", String(data.rounds.length));
    if (data.next) {
      add("Now running", "+" + ns(data.next.more_ns) + " each, to " + ns(data.next.to_ns) +
          (data.next.seconds ? ", about " + duration(data.next.seconds) : ""),
          data.next.seconds ? "At the speed this study has run" +
            (data.next.platform ? " (" + data.next.platform + ")" : "") : "");
    }
  }

  function roundsTable(data) {
    var table = document.getElementById("stopping-rounds-table");
    table.innerHTML = "";
    var head = el("tr");
    ["Round", "Production per run"].concat(data.measures.map(function (m) { return m.label; }))
      .concat(["Decision"]).forEach(function (h) { head.appendChild(el("th", "", h)); });
    var thead = el("thead");
    thead.appendChild(head);
    table.appendChild(thead);
    var body = el("tbody");
    data.rounds.forEach(function (r, i) {
      var row = el("tr");
      row.appendChild(el("td", "", String(r.round)));
      row.appendChild(el("td", "mono", ns(r.production_ns)));
      data.measures.forEach(function (m) {
        var p = m.points.filter(function (q) { return q.round === r.round; })[0];
        var text = !p ? "" : p.measured
          ? withError(p.value, p.error) + (p.agree === false ? ", disagree" : p.met ? ", met" : "")
          : "not yet a measurement";
        row.appendChild(el("td", "mono", text));
      });
      var decided = DECIDED[r.decision] || r.decision;
      if (r.decision === "extend" && r.more_ns) decided += " by " + ns(r.more_ns);
      row.appendChild(el("td", "", decided));
      body.appendChild(row);
    });
    table.appendChild(body);
    document.getElementById("stopping-rounds").hidden = data.rounds.length === 0;
  }

  function render(data) {
    var card = document.getElementById("stopping-card");
    if (!card) return;
    if (!data || !data.ok) {
      card.hidden = true;
      last = data;
      return;
    }
    if (last && last.ok && last.version === data.version) return;
    last = data;
    card.hidden = false;
    card.setAttribute("data-outcome", data.outcome);
    var pill = document.getElementById("stopping-outcome");
    pill.textContent = data.outcome_label;
    pill.setAttribute("data-state", PILL[data.outcome] || "stale");
    document.getElementById("stopping-said").textContent = data.said;
    strip(data);
    var produced = data.production_ns || 0;
    data.measures.forEach(function (m) {
      if (m.projection) produced = Math.max(produced, m.projection.to_ns);
    });
    var xMax = data.ceiling_ns || produced * 1.15 || 1;
    var host = document.getElementById("stopping-measures");
    host.innerHTML = "";
    data.measures.forEach(function (m) { host.appendChild(measureBlock(m, data, xMax)); });
    key(data);
    document.getElementById("stopping-key").hidden = data.rounds.length === 0;
    roundsTable(data);
  }

  function load() {
    if (pending) return Promise.resolve();
    pending = true;
    return fetch("/api/stopping", { cache: "no-store" })
      .then(function (res) { return res.json(); })
      .then(render)
      .catch(function () { render(null); })
      .then(function () { pending = false; });
  }

  /* Asked for when the study or the page changes; on each poll while a
   * study with a rule is still running; and every half minute for a study
   * that had none when last asked, since a study launched from here
   * writes its rule a moment after it becomes the active one. A finished
   * study stops asking. */
  var askedAt = 0;
  var RECHECK_MS = 30000;
  function loadNow() { askedAt = Date.now(); return load(); }
  window.addEventListener("dashboard:run-changed", function () { last = null; loadNow(); });
  window.addEventListener("dashboard:live-page-opened", loadNow);
  window.addEventListener("dashboard:status-updated", function () {
    if (last && last.ok) {
      if (last.outcome === "running") loadNow();
    } else if (Date.now() - askedAt > RECHECK_MS) {
      loadNow();
    }
  });

  window.FastMDXStopping = { load: loadNow, render: render };
}());
