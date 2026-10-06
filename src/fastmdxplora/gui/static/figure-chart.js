/* An analysis's other figures, plotted from their numbers in the page's
 * own colours (figure_data.py, /api/figure-data).
 *
 * The Analysis page plotted an analysis's own series from its numbers
 * (series-chart.js); every other figure was the picture the analysis wrote,
 * white on a dark page, with nothing to point at. Here the same numbers are
 * plotted as the analysis plotted them: secondary structure, residue
 * against time; each frame's cluster, the clusters' populations, the
 * hierarchy and the RMSD between frames; a projection coloured by time and
 * its free-energy landscape; the backbone dihedrals' density. Pointing at a
 * plot gives its value there; a frame of the trajectory opens in the viewer.
 *
 * The figure the analysis wrote stays one click away: it is the one at
 * publication settings, and the one to download.
 */
(function () {
  "use strict";

  var NS = "http://www.w3.org/2000/svg";
  var cache = {};
  /* Okabe and Ito's palette, as the analyses' own figures colour their
   * clusters (plotting.PALETTE), told apart by most colour vision. */
  var CATEGORY = ["#0072B2", "#D55E00", "#009E73", "#CC79A7", "#E69F00", "#56B4E9",
                  "#F0E442", "#8C8C8C", "#7F3C8D", "#B2DF8A"];
  /* Viridis, as the analyses' maps: even in lightness, and read in grey. */
  var VIRIDIS = ["#440154", "#482878", "#3e4989", "#31688e", "#26828e", "#1f9e89",
                 "#35b779", "#6ece58", "#b5de2b", "#fde725"];
  /* The figures this plots, by their path in the study. */
  var FIGURES = /(?:^|\/)(analysis\/(?:ss\/ss|cluster\/cluster_[a-z]+(?:_counts|_dendrogram|_matrix)?|dimred\/dimred_[a-z]+(?:_landscape)?|dihedrals\/dihedrals)\.png)$/;

  function token(name, fallback) {
    var value = "";
    try {
      value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    } catch (e) { /* no styles yet */ }
    return value || fallback;
  }

  function colours() {
    return {
      line: token("--accent-cyan", "#2698ba"),
      warm: token("--accent-orange", "#ffb86b"),
      grid: token("--border-subtle", "rgba(255,255,255,0.07)"),
      axis: token("--text-muted", "#85858f"),
      text: token("--text-secondary", "#b5b5bb"),
      shade: token("--background-soft", "#151518"),
      quiet: token("--border-primary", "#3a3d42"),
    };
  }

  function el(name, attrs, parent) {
    var node = document.createElementNS(NS, name);
    Object.keys(attrs || {}).forEach(function (key) { node.setAttribute(key, attrs[key]); });
    if (parent) parent.appendChild(node);
    return node;
  }

  function text(parent, x, y, said, attrs) {
    var node = el("text", Object.assign({ x: x, y: y, "font-size": 10 }, attrs || {}), parent);
    node.textContent = said;
    return node;
  }

  function ticks(low, high, count) {
    var shared = window.FastMDXSeries && window.FastMDXSeries.ticks;
    if (shared) return shared(low, high, count);
    return [low, high];
  }

  function tickText(values) {
    var step = values.length > 1 ? Math.abs(values[1] - values[0]) : Math.abs(values[0]) || 1;
    var places = Math.max(0, Math.min(6, -Math.floor(Math.log10(step) + 1e-9)));
    return function (v) { return v.toFixed(places); };
  }

  function format(value) {
    if (value == null || !isFinite(value)) return "—";
    var magnitude = Math.abs(value);
    if (magnitude === 0) return "0";
    if (magnitude >= 1e5 || magnitude < 1e-3) return value.toExponential(2);
    if (magnitude >= 100) return value.toFixed(1);
    if (magnitude >= 1) return value.toFixed(3);
    return value.toPrecision(3);
  }

  function category(k) {
    return k < 0 ? "#BBBBBB" : CATEGORY[k % CATEGORY.length];
  }

  function hexToRgb(hex) {
    var n = parseInt(hex.slice(1), 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }

  /* A value from 0 to 1 as viridis. */
  function viridis(f) {
    var x = Math.max(0, Math.min(1, f)) * (VIRIDIS.length - 1);
    var i = Math.min(VIRIDIS.length - 2, Math.floor(x));
    var a = hexToRgb(VIRIDIS[i]);
    var b = hexToRgb(VIRIDIS[i + 1]);
    var t = x - i;
    return [0, 1, 2].map(function (k) { return Math.round(a[k] + (b[k] - a[k]) * t); });
  }

  function rgb(c) { return "rgb(" + c[0] + "," + c[1] + "," + c[2] + ")"; }

  function span(values) {
    var low = Infinity, high = -Infinity;
    values.forEach(function (v) {
      if (v == null || !isFinite(v)) return;
      if (v < low) low = v;
      if (v > high) high = v;
    });
    if (low === Infinity) return [0, 1];
    if (high === low) high = low + (Math.abs(low) || 1) * 0.01;
    return [low, high];
  }

  /* ------------------------------------------------------------------ */
  /* The frame: axes, ticks, titles, a colour bar                         */
  /* ------------------------------------------------------------------ */

  function frame(host, data, options) {
    host.replaceChildren();
    var width = Math.max(280, Math.round(host.clientWidth || 480));
    var bar = !!options.bar;
    var margin = { top: options.legend ? 30 : 14, right: bar ? 78 : 20, bottom: 40, left: 62 };
    var height = options.square
      ? Math.min(420, Math.max(260, width - margin.left - margin.right + margin.top + margin.bottom))
      : (options.height || 280);
    var plot = { left: margin.left, top: margin.top, right: width - margin.right,
                 bottom: height - margin.bottom };
    if (options.square) {
      // As the analysis plotted it: one unit across is one unit up.
      var side = Math.min(plot.right - plot.left, plot.bottom - plot.top);
      // Centred, with its colour bar, in the width there is.
      var spare = plot.right - plot.left - side;
      plot.left += Math.floor(spare / 2);
      plot.right = plot.left + side;
      plot.bottom = plot.top + side;
    }
    var svg = el("svg", {
      width: width, height: height, viewBox: "0 0 " + width + " " + height, role: "img",
      // Read as one picture: its toggle, beside it, is the way to the figure.
      "aria-label": options.said || data.title || "", class: "figure-svg",
    }, host);
    svg.style.height = height + "px";
    var tip = document.createElement("div");
    tip.className = "series-tip";
    tip.hidden = true;
    host.appendChild(tip);
    return { svg: svg, plot: plot, width: width, height: height, tip: tip, c: colours() };
  }

  function axes(f, x, y) {
    var plot = f.plot, svg = f.svg, c = f.c;
    function px(v) { return plot.left + (v - x.low) / (x.high - x.low) * (plot.right - plot.left); }
    function py(v) { return plot.bottom - (v - y.low) / (y.high - y.low) * (plot.bottom - plot.top); }
    if (y.ticks !== false) {
      var yTicks = y.ticks || ticks(y.low, y.high, 4);
      var yText = y.say || tickText(yTicks);
      yTicks.forEach(function (v) {
        if (v < y.low - 1e-9 || v > y.high + 1e-9) return;
        if (y.grid !== false) {
          el("line", { x1: plot.left, x2: plot.right, y1: py(v), y2: py(v), stroke: c.grid }, svg);
        }
        text(svg, plot.left - 8, py(v) + 3, yText(v), { fill: c.axis, "text-anchor": "end" });
      });
    }
    if (x.ticks !== false) {
      var xTicks = x.ticks || ticks(x.low, x.high, 5);
      var xText = x.say || tickText(xTicks);
      xTicks.forEach(function (v) {
        if (v < x.low - 1e-9 || v > x.high + 1e-9) return;
        text(svg, px(v), plot.bottom + 15, xText(v), { fill: c.axis, "text-anchor": "middle" });
      });
    }
    el("line", { x1: plot.left, x2: plot.right, y1: plot.bottom, y2: plot.bottom, stroke: c.axis }, svg);
    el("line", { x1: plot.left, x2: plot.left, y1: plot.top, y2: plot.bottom, stroke: c.axis }, svg);
    text(svg, (plot.left + plot.right) / 2, f.height - 6, x.label || "",
         { fill: c.text, "font-size": 11, "text-anchor": "middle" });
    var middle = (plot.top + plot.bottom) / 2;
    var beside = Math.max(12, plot.left - 50);
    text(svg, beside, middle, y.label || "", {
      fill: c.text, "font-size": 11, "text-anchor": "middle",
      transform: "rotate(-90 " + beside + " " + middle + ")",
    });
    return { px: px, py: py };
  }

  /* A vertical bar of the colour scale, with its ends and middle said. */
  function colourBar(f, low, high, label, scale) {
    var plot = f.plot, svg = f.svg, c = f.c;
    var x = plot.right + 16;
    var id = "fig-grad-" + Math.random().toString(36).slice(2, 9);
    var defs = el("defs", {}, svg);
    var grad = el("linearGradient", { id: id, x1: 0, y1: 1, x2: 0, y2: 0 }, defs);
    for (var i = 0; i <= 10; i += 1) {
      el("stop", { offset: (i * 10) + "%", "stop-color": rgb(scale(i / 10)) }, grad);
    }
    el("rect", { x: x, y: plot.top, width: 10, height: plot.bottom - plot.top,
                 fill: "url(#" + id + ")", stroke: c.quiet }, svg);
    var marks = ticks(low, high, 4);
    var say = tickText(marks);
    marks.forEach(function (v) {
      if (v < low - 1e-9 || v > high + 1e-9) return;
      var y = plot.bottom - (v - low) / (high - low) * (plot.bottom - plot.top);
      text(svg, x + 14, y + 3, say(v), { fill: c.axis });
    });
    var middle = (plot.top + plot.bottom) / 2;
    var at = Math.min(f.width - 6, x + 62);
    text(svg, at, middle, label, {
      fill: c.text, "font-size": 10, "text-anchor": "middle",
      transform: "rotate(-90 " + at + " " + middle + ")",
    });
  }

  /* A grid of values as an image over the plot: row 0 at the bottom. */
  function raster(f, grid, colourOf, where) {
    var ny = grid.length, nx = ny ? grid[0].length : 0;
    if (!nx) return;
    var canvas = document.createElement("canvas");
    canvas.width = nx;
    canvas.height = ny;
    var ctx = canvas.getContext("2d");
    var image = ctx.createImageData(nx, ny);
    for (var j = 0; j < ny; j += 1) {
      for (var i = 0; i < nx; i += 1) {
        var colour = colourOf(grid[j][i], i, j);
        var at = ((ny - 1 - j) * nx + i) * 4;
        if (!colour) { image.data[at + 3] = 0; continue; }
        image.data[at] = colour[0];
        image.data[at + 1] = colour[1];
        image.data[at + 2] = colour[2];
        image.data[at + 3] = 255;
      }
    }
    ctx.putImageData(image, 0, 0);
    var shown = el("image", {
      x: where.left, y: where.top, width: Math.max(1, where.right - where.left),
      height: Math.max(1, where.bottom - where.top), preserveAspectRatio: "none",
      class: "figure-raster",
    }, f.svg);
    shown.setAttributeNS("http://www.w3.org/1999/xlink", "href", canvas.toDataURL("image/png"));
    shown.setAttribute("href", canvas.toDataURL("image/png"));
  }

  function showTip(f, x, y, parts, hint) {
    var tip = f.tip;
    tip.replaceChildren();
    var strong = document.createElement("strong");
    strong.textContent = parts[0];
    tip.appendChild(strong);
    if (parts.length > 1) tip.appendChild(document.createTextNode(" · " + parts.slice(1).join(" · ")));
    if (hint) {
      var more = document.createElement("span");
      more.className = "series-tip-hint";
      more.textContent = hint;
      tip.appendChild(more);
    }
    tip.hidden = false;
    var left = x + 12;
    if (left + tip.offsetWidth > f.width - 4) left = Math.max(4, x - tip.offsetWidth - 12);
    tip.style.left = left + "px";
    tip.style.top = Math.max(4, y - tip.offsetHeight - 10) + "px";
  }

  function pointer(f, event) {
    var box = f.svg.getBoundingClientRect();
    return { x: (event.clientX - box.left) * (f.width / box.width),
             y: (event.clientY - box.top) * (f.height / box.height) };
  }

  function inside(f, at) {
    return at.x >= f.plot.left && at.x <= f.plot.right && at.y >= f.plot.top && at.y <= f.plot.bottom;
  }

  function when(data, i) {
    var t = data.x ? data.x[i] : data.t[i];
    var label = data.x_label || data.t_label;
    return label === "Frame" ? "frame " + data.frames[i]
      : format(t) + " ns · frame " + data.frames[i];
  }

  function openFrame(data, frameNumber) {
    if (!data.linked || frameNumber == null || !window.FastMDXSeries || !window.FastMDXSeries.openFrame) return;
    window.FastMDXSeries.openFrame(frameNumber);
  }

  function nearestIndex(xs, x) {
    var lo = 0, hi = xs.length - 1;
    while (hi - lo > 1) {
      var mid = (lo + hi) >> 1;
      if (xs[mid] < x) lo = mid; else hi = mid;
    }
    return Math.abs(xs[lo] - x) <= Math.abs(xs[hi] - x) ? lo : hi;
  }

  /* Above the plot, so it hides nothing plotted. */
  function legend(f, items) {
    var x = f.plot.left, y = f.plot.top - 10;
    var box = el("g", { class: "figure-legend" }, f.svg);
    items.forEach(function (item) {
      el("rect", { x: x, y: y - 7, width: 9, height: 9, rx: 2, fill: item.colour,
                   stroke: f.c.axis, "stroke-width": 0.6 }, box);
      var said = text(box, x + 13, y + 1, item.label, { fill: f.c.text });
      x += 22 + said.getComputedTextLength();
      if (x > f.plot.right - 60) { x = f.plot.left + 6; y += 14; }
    });
  }

  /* ------------------------------------------------------------------ */
  /* Each kind of figure                                                  */
  /* ------------------------------------------------------------------ */

  function plotStates(host, data) {
    var f = frame(host, data, { height: 310, legend: true, said: data.title + ", " + data.residues.length +
      " residues over " + data.x.length + " frames" });
    var c = f.c;
    var fill = { H: hexToRgb(c.line.charAt(0) === "#" ? c.line : "#2698ba"),
                 E: hexToRgb(c.warm.charAt(0) === "#" ? c.warm : "#ffb86b"),
                 C: hexToRgb(c.shade.charAt(0) === "#" ? c.shade : "#2a2d31") };
    var n = data.x.length, residues = data.residues.length;
    var step = n > 1 ? (data.x[n - 1] - data.x[0]) / (n - 1) : 1;
    var x = { low: data.x[0] - step / 2, high: data.x[n - 1] + step / 2, label: data.x_label };
    var y = { low: 0.5, high: residues + 0.5, label: data.y_label, grid: false,
              ticks: ticks(1, residues, Math.min(6, residues)).filter(function (v) { return v === Math.round(v); }),
              say: function (v) { return data.residues[v - 1] || ""; } };
    var grid = [];
    for (var r = 0; r < residues; r += 1) {
      var row = [];
      for (var i = 0; i < n; i += 1) row.push(data.states[i].charAt(r));
      grid.push(row);
    }
    raster(f, grid, function (code) { return fill[code] || null; }, f.plot);
    var map = axes(f, x, y);
    legend(f, data.classes.map(function (k) {
      return { label: k.label, colour: rgb(fill[k.code]) };
    }));
    var names = {};
    data.classes.forEach(function (k) { names[k.code] = k.label; });
    var hint = data.linked ? "Click to open this frame" : "";
    f.svg.addEventListener("mousemove", function (event) {
      var at = pointer(f, event);
      if (!inside(f, at)) { f.tip.hidden = true; return; }
      var i = nearestIndex(data.x, x.low + (at.x - f.plot.left) / (f.plot.right - f.plot.left) * (x.high - x.low));
      var res = Math.min(residues, Math.max(1, Math.round(y.low + (f.plot.bottom - at.y) / (f.plot.bottom - f.plot.top) * (y.high - y.low))));
      showTip(f, at.x, at.y, [names[data.states[i].charAt(res - 1)] || "", "residue " + data.residues[res - 1], when(data, i)], hint);
      f.svg.dataset.at = String(i);
    });
    f.svg.addEventListener("mouseleave", function () { f.tip.hidden = true; });
    f.svg.addEventListener("click", function () {
      if (f.svg.dataset.at) openFrame(data, data.frames[+f.svg.dataset.at]);
    });
    void map;
  }

  function plotLabels(host, data) {
    var f = frame(host, data, { height: 260, said: data.title + ", " + data.clusters.length +
      " clusters over " + data.x.length + " frames" });
    var n = data.x.length;
    var low = Math.min.apply(null, data.clusters), high = Math.max.apply(null, data.clusters);
    var x = { low: data.x[0], high: data.x[n - 1] > data.x[0] ? data.x[n - 1] : data.x[0] + 1,
              label: data.x_label };
    var y = { low: low - 0.6, high: high + 0.6, label: data.y_label, ticks: data.clusters,
              say: function (v) { return v < 0 ? "noise" : String(v); } };
    var map = axes(f, x, y);
    var r = n > 2000 ? 1.4 : n > 500 ? 2 : 2.6;
    var layer = el("g", {}, f.svg);
    for (var i = 0; i < n; i += 1) {
      el("circle", { cx: map.px(data.x[i]).toFixed(1), cy: map.py(data.labels[i]).toFixed(1), r: r,
                     fill: category(data.labels[i]) }, layer);
    }
    var hint = data.linked ? "Click to open this frame" : "";
    f.svg.addEventListener("mousemove", function (event) {
      var at = pointer(f, event);
      if (!inside(f, at)) { f.tip.hidden = true; return; }
      var i = nearestIndex(data.x, x.low + (at.x - f.plot.left) / (f.plot.right - f.plot.left) * (x.high - x.low));
      var k = data.labels[i];
      showTip(f, map.px(data.x[i]), map.py(k), [k < 0 ? "noise" : "cluster " + k, when(data, i)], hint);
      f.svg.dataset.at = String(i);
    });
    f.svg.addEventListener("mouseleave", function () { f.tip.hidden = true; });
    f.svg.addEventListener("click", function () {
      if (f.svg.dataset.at) openFrame(data, data.frames[+f.svg.dataset.at]);
    });
  }

  function plotBars(host, data) {
    var f = frame(host, data, { height: 260, said: data.title + ": " + data.clusters.map(function (k) {
      return "cluster " + k.cluster + " " + k.frames + " frames";
    }).join(", ") });
    var n = data.clusters.length;
    var most = Math.max.apply(null, data.clusters.map(function (k) { return k.frames; }));
    var x = { low: -0.6, high: n - 0.4, label: data.x_label, ticks: false };
    var y = { low: 0, high: most * 1.12 || 1, label: data.y_label };
    var map = axes(f, x, y);
    var widthOf = (map.px(1) - map.px(0)) * 0.72;
    data.clusters.forEach(function (k, i) {
      var top = map.py(k.frames);
      var bar = el("rect", { x: map.px(i) - widthOf / 2, y: top, width: widthOf,
                             height: Math.max(0, f.plot.bottom - top), rx: 3,
                             fill: category(k.cluster), class: "figure-bar" }, f.svg);
      bar.dataset.index = String(i);
      text(f.svg, map.px(i), f.plot.bottom + 15, k.cluster < 0 ? "noise" : String(k.cluster),
           { fill: f.c.axis, "text-anchor": "middle" });
      if (k.fraction != null) {
        text(f.svg, map.px(i), top - 4, Math.round(k.fraction * 100) + "%",
             { fill: f.c.text, "text-anchor": "middle" });
      }
    });
    f.svg.addEventListener("mousemove", function (event) {
      var at = pointer(f, event);
      var i = Math.round(x.low + (at.x - f.plot.left) / (f.plot.right - f.plot.left) * (x.high - x.low));
      if (!inside(f, at) || i < 0 || i >= n) { f.tip.hidden = true; delete f.svg.dataset.at; return; }
      var k = data.clusters[i];
      var parts = ["cluster " + k.cluster, k.frames + " frames" +
        (k.fraction != null ? " (" + (k.fraction * 100).toFixed(1) + "%)" : "")];
      if (k.medoid_frame != null) {
        parts.push("medoid " + (k.medoid_time_ns != null ? format(k.medoid_time_ns) + " ns, " : "") +
                   "frame " + k.medoid_frame);
      }
      showTip(f, map.px(i), map.py(k.frames), parts,
              data.linked && k.medoid_frame != null ? "Click to open its medoid" : "");
      f.svg.dataset.at = String(i);
    });
    f.svg.addEventListener("mouseleave", function () { f.tip.hidden = true; });
    f.svg.addEventListener("click", function () {
      if (!f.svg.dataset.at) return;
      openFrame(data, data.clusters[+f.svg.dataset.at].medoid_frame);
    });
  }

  function plotDendrogram(host, data) {
    var f = frame(host, data, { height: 300, said: data.title + " of " + data.frames + " frames" });
    var heights = [];
    data.dcoord.forEach(function (d) { heights.push(d[1], d[2]); });
    var top = Math.max.apply(null, heights);
    var x = { low: 0, high: data.leaves * 10, label: data.truncated
      ? "Frames (the last " + data.leaves + " branches of " + data.frames + ")"
      : "Frames (" + data.frames + ")", ticks: false };
    var y = { low: 0, high: top * 1.06 || 1, label: data.y_label };
    var map = axes(f, x, y);
    data.icoord.forEach(function (xs, i) {
      var ys = data.dcoord[i];
      var group = data.groups[i];
      var d = "M" + map.px(xs[0]).toFixed(1) + " " + map.py(ys[0]).toFixed(1);
      for (var k = 1; k < 4; k += 1) d += "L" + map.px(xs[k]).toFixed(1) + " " + map.py(ys[k]).toFixed(1);
      el("path", { d: d, fill: "none", "stroke-width": 1.2,
                   stroke: group == null ? f.c.axis : category(group), class: "figure-link" }, f.svg);
    });
    if (data.cut != null) {
      el("line", { x1: f.plot.left, x2: f.plot.right, y1: map.py(data.cut), y2: map.py(data.cut),
                   stroke: f.c.text, "stroke-dasharray": "4 4", "stroke-width": 1 }, f.svg);
      text(f.svg, f.plot.right - 4, map.py(data.cut) - 4, "cut", { fill: f.c.text, "text-anchor": "end" });
    }
    f.svg.addEventListener("mousemove", function (event) {
      var at = pointer(f, event);
      if (!inside(f, at)) { f.tip.hidden = true; return; }
      var height = y.low + (f.plot.bottom - at.y) / (f.plot.bottom - f.plot.top) * (y.high - y.low);
      showTip(f, at.x, at.y, ["distance " + format(height)],
              data.cut != null ? "merges below the cut are coloured by their cluster" : "");
    });
    f.svg.addEventListener("mouseleave", function () { f.tip.hidden = true; });
  }

  function plotGrid(host, data, opts) {
    var values = data.values;
    var flat = [];
    values.forEach(function (row) { row.forEach(function (v) { if (v != null) flat.push(v); }); });
    var range = span(flat);
    if (data.low != null) range[0] = Math.min(range[0], data.low);
    var f = frame(host, data, { bar: true, square: true, said: data.title });
    var ex = opts.edgesX, ey = opts.edgesY;
    var x = { low: ex[0], high: ex[ex.length - 1], label: opts.xLabel, ticks: opts.ticks };
    var y = { low: ey[0], high: ey[ey.length - 1], label: opts.yLabel, ticks: opts.ticks };
    var shade = hexToRgb(f.c.shade.charAt(0) === "#" ? f.c.shade : "#2a2d31");
    raster(f, values, function (v) {
      if (v == null) return opts.empty === "shade" ? shade : null;
      return viridis((v - range[0]) / (range[1] - range[0]));
    }, f.plot);
    var map = axes(f, x, y);
    (data.guides || []).forEach(function (g) {
      el("line", { x1: map.px(g), x2: map.px(g), y1: f.plot.top, y2: f.plot.bottom,
                   stroke: f.c.text, "stroke-opacity": 0.5, "stroke-width": 0.8 }, f.svg);
      el("line", { x1: f.plot.left, x2: f.plot.right, y1: map.py(g), y2: map.py(g),
                   stroke: f.c.text, "stroke-opacity": 0.5, "stroke-width": 0.8 }, f.svg);
    });
    colourBar(f, range[0], range[1], data.label + (data.unit ? " (" + data.unit + ")" : ""), viridis);
    var nx = values[0].length, ny = values.length;
    f.svg.addEventListener("mousemove", function (event) {
      var at = pointer(f, event);
      if (!inside(f, at)) { f.tip.hidden = true; return; }
      var i = Math.min(nx - 1, Math.floor((at.x - f.plot.left) / (f.plot.right - f.plot.left) * nx));
      var j = Math.min(ny - 1, Math.floor((f.plot.bottom - at.y) / (f.plot.bottom - f.plot.top) * ny));
      var v = values[j][i];
      var cx = (ex[i] + ex[i + 1]) / 2, cy = (ey[j] + ey[j + 1]) / 2;
      showTip(f, at.x, at.y, [v == null ? opts.emptySaid : format(v) + (data.unit ? " " + data.unit : ""),
                              opts.where(cx, cy)], "");
    });
    f.svg.addEventListener("mouseleave", function () { f.tip.hidden = true; });
  }

  function edges(low, high, n) {
    var out = [];
    for (var i = 0; i <= n; i += 1) out.push(low + (high - low) * i / n);
    return out;
  }

  function plotMatrix(host, data) {
    var n = data.values.length;
    var e = edges(data.extent[0], data.extent[1], n);
    var timed = data.axis_label !== "Frame";
    plotGrid(host, data, {
      edgesX: e, edgesY: e, xLabel: data.axis_label, yLabel: data.axis_label,
      emptySaid: "—",
      where: function (a, b) {
        return timed ? format(a) + " ns and " + format(b) + " ns" : "frames " + Math.round(a) + " and " + Math.round(b);
      },
    });
  }

  function plotLandscape(host, data) {
    plotGrid(host, data, {
      edgesX: data.edges_x, edgesY: data.edges_y, xLabel: data.axes[0], yLabel: data.axes[1],
      emptySaid: "not visited",
      where: function (a, b) { return format(a) + ", " + format(b); },
    });
  }

  function plotDensity(host, data) {
    plotGrid(host, data, {
      edgesX: data.edges_x, edgesY: data.edges_y, xLabel: data.axes[0], yLabel: data.axes[1],
      ticks: data.ticks, empty: "shade", emptySaid: "none",
      where: function (a, b) { return "φ " + Math.round(a) + "°, ψ " + Math.round(b) + "°"; },
    });
  }

  function plotProjection(host, data) {
    var f = frame(host, data, { bar: true, height: 320, said: data.title + ", " + data.x.length + " frames" });
    var xr = span(data.x), yr = span(data.y), tr = span(data.t);
    var padX = (xr[1] - xr[0]) * 0.05, padY = (yr[1] - yr[0]) * 0.05;
    var x = { low: xr[0] - padX, high: xr[1] + padX, label: data.axes[0] };
    var y = { low: yr[0] - padY, high: yr[1] + padY, label: data.axes[1] };
    var map = axes(f, x, y);
    var n = data.x.length;
    var r = n > 2000 ? 1.6 : n > 500 ? 2.2 : 3;
    var layer = el("g", {}, f.svg);
    var at = [];
    for (var i = 0; i < n; i += 1) {
      if (data.x[i] == null || data.y[i] == null) { at.push(null); continue; }
      var cx = map.px(data.x[i]), cy = map.py(data.y[i]);
      at.push([cx, cy]);
      el("circle", { cx: cx.toFixed(1), cy: cy.toFixed(1), r: r,
                     fill: rgb(viridis((data.t[i] - tr[0]) / (tr[1] - tr[0]))) }, layer);
    }
    colourBar(f, tr[0], tr[1], data.t_label === "Frame" ? "Frame" : "Time (ns)", viridis);
    var hint = data.linked ? "Click to open this frame" : "";
    f.svg.addEventListener("mousemove", function (event) {
      var p = pointer(f, event);
      if (!inside(f, p)) { f.tip.hidden = true; delete f.svg.dataset.at; return; }
      var best = -1, distance = Infinity;
      at.forEach(function (q, k) {
        if (!q) return;
        var d = (q[0] - p.x) * (q[0] - p.x) + (q[1] - p.y) * (q[1] - p.y);
        if (d < distance) { distance = d; best = k; }
      });
      if (best < 0 || distance > 400) { f.tip.hidden = true; delete f.svg.dataset.at; return; }
      var unit = data.unit ? " " + data.unit : "";
      showTip(f, at[best][0], at[best][1], [when(data, best),
        format(data.x[best]) + unit + ", " + format(data.y[best]) + unit], hint);
      f.svg.dataset.at = String(best);
    });
    f.svg.addEventListener("mouseleave", function () { f.tip.hidden = true; });
    f.svg.addEventListener("click", function () {
      if (f.svg.dataset.at) openFrame(data, data.frames[+f.svg.dataset.at]);
    });
  }

  var PLOTS = {
    states: plotStates, labels: plotLabels, bars: plotBars, dendrogram: plotDendrogram,
    matrix: plotMatrix, landscape: plotLandscape, density: plotDensity, projection: plotProjection,
  };

  function plot(host, data) {
    var how = PLOTS[data.kind];
    if (how) how(host, data);
  }

  function load(figure) {
    if (!cache[figure]) {
      cache[figure] = fetch("/api/figure-data?figure=" + encodeURIComponent(figure), { cache: "no-store" })
        .then(function (r) { return r.json(); })
        .catch(function () { return { ok: false }; });
    }
    return cache[figure];
  }

  /* The figure a card shows, where it is one plotted here. */
  function figureOf(source) {
    var found = FIGURES.exec(source || "");
    return found ? found[1] : "";
  }

  /* Every card that names such a figure gets its plot, and a way back to
   * the figure. A card whose numbers cannot be read keeps its figure. */
  function hydrate(root) {
    Array.prototype.forEach.call((root || document).querySelectorAll("[data-figure]"), function (box) {
      if (box.getAttribute("data-figure-ready")) return;
      box.setAttribute("data-figure-ready", "pending");
      var figure = box.getAttribute("data-figure");
      load(figure).then(function (data) {
        if (!data || !data.ok || !PLOTS[data.kind]) {
          box.setAttribute("data-figure-ready", "none");
          return;
        }
        var chart = document.createElement("div");
        chart.className = "figure-chart";
        chart.setAttribute("data-figure-kind", data.kind);
        box.parentNode.insertBefore(chart, box);
        var card = box.closest(".analysis-card");
        var toggle = card && card.querySelector("[data-figure-toggle]");
        function mode(chartShown) {
          chart.hidden = !chartShown;
          box.hidden = chartShown;
          if (toggle) toggle.textContent = chartShown ? "Show the figure" : "Show the plot";
          if (chartShown) plot(chart, data);
        }
        if (toggle) {
          toggle.hidden = false;
          toggle.addEventListener("click", function (event) {
            event.preventDefault();
            mode(chart.hidden);
          });
        }
        mode(true);
        box.setAttribute("data-figure-ready", "plotted");
        if (window.ResizeObserver) {
          var width = chart.clientWidth;
          var watching = new ResizeObserver(function () {
            // The cards are built again when the results change.
            if (!chart.isConnected) { watching.disconnect(); return; }
            if (!chart.hidden && Math.abs(chart.clientWidth - width) > 4) {
              width = chart.clientWidth;
              plot(chart, data);
            }
          });
          watching.observe(chart);
        }
      });
    });
  }

  /* Plotted again in the new scheme's colours. */
  document.addEventListener("fmx:theme", function () {
    Array.prototype.forEach.call(document.querySelectorAll(".figure-chart"), function (chart) {
      var box = chart.nextElementSibling;
      var figure = box && box.getAttribute("data-figure");
      if (chart.hidden || !figure || !cache[figure]) return;
      cache[figure].then(function (data) { if (data && data.ok) plot(chart, data); });
    });
  });
  window.addEventListener("dashboard:run-changed", function () { cache = {}; });

  window.FastMDXFigures = {
    hydrate: hydrate, figureOf: figureOf, plot: plot,
    forget: function () { cache = {}; },
  };
}());
