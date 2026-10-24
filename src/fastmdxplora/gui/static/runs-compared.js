/* The runs of a study, side by side (runs_compared.py).
 *
 * On the Analysis page of a study of several runs: one table with the
 * settings that differ and the mean each run recorded, a difference marked
 * only where the code judged it resolved, and one quantity's series from
 * every finished run overlaid on one chart. Everything is built as
 * elements; a label read from a study is data, never markup. */
(function () {
  "use strict";

  /* Okabe and Ito's palette, told apart by the colour-blind, without its
   * black and yellow (one vanishes on a dark scheme, the other on a light
   * one). Past six runs the lines are dashed as well. */
  var PALETTE = ["#0072B2", "#E69F00", "#009E73", "#CC79A7", "#56B4E9", "#D55E00"];
  var SVG = "http://www.w3.org/2000/svg";
  var shown = null;

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  /* A mean to the precision its error allows: the error to two figures. */
  function withError(mean, error) {
    if (error === null || error === undefined || !(error > 0)) return plain(mean);
    var places = Math.max(0, 1 - Math.floor(Math.log10(error)));
    return mean.toFixed(places) + " ± " + error.toFixed(places);
  }

  function plain(value) {
    var size = Math.abs(value);
    if (size >= 100) return value.toFixed(1);
    if (size >= 10) return value.toFixed(2);
    if (size >= 1) return value.toFixed(3);
    return value.toFixed(4);
  }

  function signed(value, error) {
    var text = withError(Math.abs(value), error);
    return (value < 0 ? "−" : "+") + text;
  }

  function colourOf(i) { return PALETTE[i % PALETTE.length]; }
  function dashOf(i) { return i < PALETTE.length ? "" : "5 3"; }

  function render(data) {
    var host = document.getElementById("runs-compared");
    if (!host) return;
    var page = host.closest(".page");
    if (!data || !data.ok) {
      host.hidden = true;
      if (page) page.removeAttribute("data-runs-compared");
      return;
    }
    host.hidden = false;
    if (page) page.setAttribute("data-runs-compared", "");
    var labels = {};
    data.runs.forEach(function (run) { labels[run.run_id] = run.label || run.run_id; });

    document.getElementById("runs-compared-meta").textContent =
      data.completed + " of " + data.runs.length + " runs completed" +
      (data.so_far ? "; " + data.so_far + " read as far as their finished pieces go, between rounds"
        : "") +
      // How they differ, as every page says it (batch/aggregate.py).
      (data.replicas ? "; replicas, " + data.replicas_said : "");

    var table = document.getElementById("runs-compared-table");
    table.innerHTML = "";
    var head = el("tr");
    head.appendChild(el("th", "", "Run"));
    data.axes.forEach(function (axis) {
      var th = el("th", "is-varied", axis.label);
      th.title = axis.axis + ": differs between the runs";
      head.appendChild(th);
    });
    data.measures.forEach(function (m) {
      head.appendChild(el("th", "is-measure", m.label + (m.unit ? " (" + m.unit + ")" : "")));
    });
    var thead = el("thead");
    thead.appendChild(head);
    table.appendChild(thead);

    var body = el("tbody");
    data.runs.forEach(function (run, i) {
      var row = el("tr");
      row.setAttribute("data-run", run.run_id);
      var name = el("th", "runs-compared-run");
      name.setAttribute("scope", "row");
      var swatch = el("span", "runs-compared-swatch");
      swatch.style.background = colourOf(i);
      name.appendChild(swatch);
      // By what sets it apart, as every place names it; its folder on hover.
      name.appendChild(el("span", "", run.label || run.run_id));
      name.title = run.run_id;
      if (run.state !== "completed") {
        name.appendChild(el("span", "runs-compared-state",
          run.state + (run.fraction ? " " + Math.round(100 * run.fraction) + "%" : "")));
      }
      row.appendChild(name);
      data.axes.forEach(function (axis) {
        // As the server says it under its head: a length with its unit.
        var said = (run.values_said || {})[axis.axis];
        var value = run.values[axis.axis];
        row.appendChild(el("td", "is-varied mono", said !== undefined ? said
          : value === undefined || value === null ? "" : String(value)));
      });
      data.measures.forEach(function (m) {
        var cell = m.cells[i] || {};
        var td = el("td", "mono");
        if (cell.mean === null || cell.mean === undefined) {
          td.textContent = "—";
          td.className += " is-missing";
          td.title = run.state === "completed" ? "Not recorded by this run" : "Not finished";
        } else if (cell.withheld) {
          td.textContent = plain(cell.mean) + " *";
          td.className += " is-withheld";
          td.title = "Not determined: " + cell.withheld;
        } else {
          td.textContent = withError(cell.mean, cell.error);
          if (m.reference === run.run_id) {
            td.appendChild(el("span", "runs-compared-mark is-reference", "reference"));
          } else if (cell.versus && cell.versus.resolved) {
            td.className += " is-resolved";
            var mark = el("span", "runs-compared-mark", "differs");
            mark.title = "Differs from " + labels[m.reference] + " by " +
              signed(cell.versus.difference, cell.versus.error) + (m.unit ? " " + m.unit : "") +
              ", more than " + data.resolved_at + " times their combined error.";
            td.appendChild(mark);
          }
        }
        if (cell.mean !== null && cell.mean !== undefined && cell.noted) {
          // Its image note, said in full under the table (runs_compared.py).
          var noted = el("span", "runs-compared-mark is-noted", "\u2020");
          noted.title = cell.noted.charAt(0).toUpperCase() + cell.noted.slice(1) + ".";
          td.appendChild(noted);
        }
        row.appendChild(td);
      });
      body.appendChild(row);
    });
    table.appendChild(body);

    markMore(table.parentElement);
    var said = document.getElementById("runs-compared-said");
    said.innerHTML = "";
    data.measures.forEach(function (m) {
      var line = el("p");
      line.appendChild(el("strong", "", m.label + ". "));
      line.appendChild(document.createTextNode(m.said));
      said.appendChild(line);
    });
    data.measures.forEach(function (m) {
      m.cells.forEach(function (c) {
        if (!c.noted || c.mean === null || c.mean === undefined) return;
        said.appendChild(el("p", "muted small runs-compared-noted", "\u2020 " +
          (labels[c.run_id] || c.run_id) + ", " + m.label + ": " + c.noted + "."));
      });
    });
    if (data.measures.some(function (m) { return m.cells.some(function (c) { return c.withheld; }); })) {
      said.appendChild(el("p", "muted small",
        "* Not determined: the analysis found the series too short, or with too few " +
        "independent samples, for its mean to be determined. Point at the value for why."));
    }

    var select = document.getElementById("runs-overlay-measure");
    var chosen = select.value;
    select.innerHTML = "";
    data.measures.forEach(function (m) {
      var option = el("option", "", m.label);
      option.value = m.analysis;
      select.appendChild(option);
    });
    if (chosen && data.measures.some(function (m) { return m.analysis === chosen; })) select.value = chosen;
    document.getElementById("runs-overlay").hidden = data.measures.length === 0;
    overlay(data, select.value);
  }

  /* The runs' series of one measure, overlaid, into `chart` and `legend`
   * (the Analysis page's own unless others are given: the Overview of a
   * study of several runs draws the same). */
  function overlay(data, analysis, chartHost, legendHost) {
    var chart = chartHost || document.getElementById("runs-overlay-chart");
    var legend = legendHost || document.getElementById("runs-overlay-legend");
    if (!chart || !analysis) return;
    var measure = data.measures.filter(function (m) { return m.analysis === analysis; })[0];
    var finished = data.runs.map(function (run, i) { return { run: run, index: i }; })
      .filter(function (r) { return r.run.state === "completed" || r.run.so_far; });
    // Another measure is not zoomed as the one before was.
    if (chart.getAttribute("data-analysis") !== analysis) chart._view = null;
    chart.setAttribute("data-analysis", analysis);
    chart.setAttribute("data-drawn", "");
    Promise.all(finished.map(function (r) {
      return fetch("/api/series?analysis=" + encodeURIComponent(analysis) +
                   "&run=" + encodeURIComponent(r.run.run_id))
        .then(function (res) { return res.json(); })
        .then(function (series) { return { r: r, series: series }; })
        .catch(function () { return { r: r, series: null }; });
    })).then(function (all) {
      if (chart.getAttribute("data-analysis") !== analysis) return;
      var drawn = all.filter(function (a) {
        return a.series && a.series.ok && a.series.kind === "time" && a.series.x.length > 1;
      });
      draw(chart, legend, drawn, measure, all.length - drawn.length);
      chart.setAttribute("data-drawn", String(drawn.length));
    });
  }

  /* ``over``, where given, is every time on the card the chart is on: the
   * unit is chosen once for all of them (Compare), so one card never says
   * one series in ps and the next in ns. */
  function draw(chart, legend, drawn, measure, missing, over) {
    chart.innerHTML = "";
    legend.innerHTML = "";
    if (!drawn.length) {
      chart.appendChild(el("div", "muted small", "No finished run has this series to plot."));
      return;
    }
    // Drawn at the width it is shown at (up to 720), so its labels keep
    // their size on a phone rather than the whole chart scaled down to 5 px.
    var room = Math.round(chart.clientWidth || 0);
    var W = room > 0 ? Math.max(280, Math.min(720, room)) : 720;
    var H = W < 480 ? 240 : 280, L = 64, R = 16, T = 12, B = 40;
    var xs = [], ys = [];
    drawn.forEach(function (d) { xs = xs.concat(d.series.x); ys = ys.concat(d.series.y); });
    // Zoomed, or the page's time range on its clock (plot-tools.js), in the
    // series' own units: what it shows. Only the Analysis page's own chart
    // takes the tools; Compare's cards are drawn whole.
    var tools = over ? null : window.FastMDXPlotTools;
    var xLabel = drawn[0].series.x_label;
    var shared = tools ? tools.range(xLabel) : null;
    var view = shared ? [shared.from, shared.to] : (tools && chart._view) || null;
    chart.dataset.view = view ? (shared ? "range" : "zoomed") : "";
    // In picoseconds under a nanosecond, as every chart of a series says it.
    var clock = window.FastMDXSeries && window.FastMDXSeries.timeScale
      ? window.FastMDXSeries.timeScale(over || view || xs, xLabel)
      : { factor: 1, label: xLabel };
    if (clock.factor !== 1) xs = xs.map(function (x) { return x * clock.factor; });
    var x0 = view ? view[0] * clock.factor : Math.min.apply(null, xs);
    var x1 = view ? view[1] * clock.factor : Math.max.apply(null, xs);
    // The values in view set the height.
    var seen = ys.filter(function (y, k) { return xs[k] >= x0 && xs[k] <= x1; });
    if (!seen.length) seen = ys;
    var y0 = Math.min.apply(null, seen), y1 = Math.max.apply(null, seen);
    if (y0 >= 0 && y0 < 0.5 * y1) y0 = 0;
    if (!(y1 > y0)) { y1 = y0 + 1; }
    if (!(x1 > x0)) { x1 = x0 + 1; }
    var px = function (x) { return L + (W - L - R) * (x - x0) / (x1 - x0); };
    var py = function (y) { return H - B - (H - T - B) * (y - y0) / (y1 - y0); };
    var tick = (window.FastMDXSeries && window.FastMDXSeries.ticks) || function (a, b) { return [a, b]; };

    var svg = document.createElementNS(SVG, "svg");
    svg.setAttribute("viewBox", "0 0 " + W + " " + H);
    svg.setAttribute("class", "runs-overlay-svg");
    svg.setAttribute("role", "img");
    var unit = measure && measure.unit ? " (" + measure.unit + ")" : "";
    svg.setAttribute("aria-label", (measure ? measure.label : "") + " of " + drawn.length +
                     " runs overlaid, against " + clock.label.toLowerCase());
    var line = function (x1_, y1_, x2_, y2_, cls) {
      var l = document.createElementNS(SVG, "line");
      l.setAttribute("x1", x1_); l.setAttribute("y1", y1_);
      l.setAttribute("x2", x2_); l.setAttribute("y2", y2_);
      l.setAttribute("class", cls);
      svg.appendChild(l);
    };
    var text = function (x, y, value, anchor, cls) {
      var t = document.createElementNS(SVG, "text");
      t.setAttribute("x", x); t.setAttribute("y", y);
      t.setAttribute("text-anchor", anchor);
      t.setAttribute("class", cls || "runs-overlay-tick");
      t.textContent = value;
      svg.appendChild(t);
    };
    tick(y0, y1, 5).forEach(function (v) {
      line(L, py(v), W - R, py(v), "runs-overlay-grid");
      text(L - 6, py(v) + 4, +v.toPrecision(4), "end");
    });
    tick(x0, x1, W < 480 ? 4 : 6).forEach(function (v) {
      text(px(v), H - B + 16, +v.toPrecision(4), "middle");
    });
    line(L, H - B, W - R, H - B, "runs-overlay-axis");
    line(L, T, L, H - B, "runs-overlay-axis");
    text((L + W - R) / 2, H - 6, clock.label, "middle", "runs-overlay-label");
    var ylabel = document.createElementNS(SVG, "text");
    ylabel.setAttribute("transform", "translate(14," + ((T + H - B) / 2) + ") rotate(-90)");
    ylabel.setAttribute("text-anchor", "middle");
    ylabel.setAttribute("class", "runs-overlay-label");
    ylabel.textContent = (measure ? measure.label : "") + unit;
    svg.appendChild(ylabel);

    // What is plotted stays inside the axes when zoomed.
    var clipId = "runs-clip-" + Math.random().toString(36).slice(2, 9);
    var defs = document.createElementNS(SVG, "defs");
    var clip = document.createElementNS(SVG, "clipPath");
    clip.setAttribute("id", clipId);
    var box = document.createElementNS(SVG, "rect");
    box.setAttribute("x", L); box.setAttribute("y", T);
    box.setAttribute("width", W - L - R); box.setAttribute("height", H - T - B);
    clip.appendChild(box);
    defs.appendChild(clip);
    svg.appendChild(defs);
    var layer = document.createElementNS(SVG, "g");
    layer.setAttribute("clip-path", "url(#" + clipId + ")");
    svg.appendChild(layer);
    drawn.forEach(function (d) {
      var path = document.createElementNS(SVG, "polyline");
      path.setAttribute("points", d.series.x.map(function (x, k) {
        return px(x * clock.factor).toFixed(1) + "," + py(d.series.y[k]).toFixed(1);
      }).join(" "));
      path.setAttribute("class", "runs-overlay-line");
      path.setAttribute("data-run", d.r.run.run_id);
      path.setAttribute("stroke", colourOf(d.r.index));
      var dash = dashOf(d.r.index);
      if (dash) path.setAttribute("stroke-dasharray", dash);
      layer.appendChild(path);

      var item = el("span", "runs-overlay-key");
      var swatch = el("span", "runs-compared-swatch");
      swatch.style.background = colourOf(d.r.index);
      item.appendChild(swatch);
      item.appendChild(document.createTextNode(d.r.run.label || d.r.run.run_id));
      legend.appendChild(item);
    });
    chart.appendChild(svg);
    if (tools) {
      var again = function () { draw(chart, legend, drawn, measure, missing, over); };
      chart._redraw = again;
      var clocked = xLabel !== "Frame";
      tools.attach(chart, {
        svg: svg, width: W, height: H, plot: { left: L, top: T, right: W - R, bottom: H - B },
        zoom: "x", shared: xLabel, zoomed: !!view,
        full: { x: (function () {
          var all = [];
          drawn.forEach(function (d) { all = all.concat(d.series.x); });
          return [Math.min.apply(null, all), Math.max.apply(null, all)];
        }()) },
        least: { x: (function (x) {
          return x.length > 1 ? 3 * Math.abs(x[x.length - 1] - x[0]) / (x.length - 1) : 0;
        }(drawn[0].series.x)) },
        name: "runs_" + (measure ? measure.analysis : "series") + (view ? "_range" : ""),
        invert: function (x, y) {
          return { x: (x0 + (x - L) / (W - L - R) * (x1 - x0)) / clock.factor,
                   y: y0 + (H - B - y) / (H - T - B) * (y1 - y0) };
        },
        onView: function (v) { chart._view = v.x; again(); },
        onReset: function () { chart._view = null; again(); },
        csv: function () {
          var rows = [["run", clocked ? "time_ns" : "frame",
                       (measure ? measure.analysis : "value") + unit]];
          drawn.forEach(function (d) {
            d.series.x.forEach(function (x, k) {
              var at = x * clock.factor;
              if (at < x0 - 1e-12 || at > x1 + 1e-12) return;
              rows.push([d.r.run.run_id, x, d.series.y[k]]);
            });
          });
          return rows;
        },
      });
    }
    if (missing) {
      legend.appendChild(el("span", "muted small",
        missing + " finished run" + (missing === 1 ? " has" : "s have") + " no such series"));
    }
  }

  /* Which edge of the table has columns past it, for the shading there. */
  function markMore(scroll) {
    if (!scroll) return;
    var left = scroll.scrollLeft > 1;
    var right = scroll.scrollLeft + scroll.clientWidth < scroll.scrollWidth - 1;
    if (left || right) scroll.dataset.more = left && right ? "both" : left ? "left" : "right";
    else delete scroll.dataset.more;
    if (!scroll.dataset.watched) {
      scroll.dataset.watched = "1";
      scroll.addEventListener("scroll", function () { markMore(scroll); }, { passive: true });
      // Shown, or the window or the panels beside it changed its width.
      if (typeof ResizeObserver === "function") {
        new ResizeObserver(function () { markMore(scroll); }).observe(scroll);
      }
    }
  }

  function load() {
    return fetch("/api/runs-compared")
      .then(function (res) { return res.json(); })
      .then(function (data) { shown = data; render(data); })
      .catch(function () { render(null); });
  }

  /* Registered now rather than when the page is parsed: the dashboard
   * opens a page as it boots, and the first opening must not be missed. */
  window.addEventListener("dashboard:results-page-opened", load);
  document.addEventListener("DOMContentLoaded", function () {
    var select = document.getElementById("runs-overlay-measure");
    if (select) {
      select.addEventListener("change", function () {
        if (shown && shown.ok) overlay(shown, select.value);
      });
    }
  });

  /* The page's time range changed: the Analysis page's overlay with it. */
  document.addEventListener("fmx:time-range", function () {
    var chart = document.getElementById("runs-overlay-chart");
    if (chart && chart._redraw && chart.isConnected) chart._redraw();
  });

  window.FastMDXRunsCompared = { load: load, palette: PALETTE.slice(), overlay: overlay,
                                 colourOf: colourOf, markMore: markMore, drawOverlay: draw };
}());
