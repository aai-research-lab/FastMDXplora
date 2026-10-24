/* An analysis's other figures, plotted from their numbers in the page's
 * own colours (figure_data.py, /api/figure-data).
 *
 * The Analysis page plotted an analysis's own series from its numbers
 * (series-chart.js); every other figure was the picture the analysis wrote,
 * white on a dark page, with nothing to point at. Here the same numbers are
 * plotted as the analysis plotted them: secondary structure, residue
 * against time; each frame's cluster, the clusters' populations, the
 * hierarchy and the RMSD between frames; a projection coloured by time and
 * its free-energy landscape; the backbone dihedrals' density; how tightly
 * each thermodynamic observable is known; the principal moments of inertia
 * over time; the residues in contact with the ligand and the interactions
 * that hold it. Pointing at a
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
  var FIGURES = /(?:^|\/)(analysis\/(?:ss\/ss|cluster\/cluster_[a-z]+(?:_counts|_dendrogram|_matrix)?|dimred\/dimred_[a-z]+(?:_landscape)?|dihedrals\/dihedrals|thermodynamics\/thermodynamics|moments_of_inertia\/moments_of_inertia|pl_contacts\/pl_contacts|pl_interactions\/pl_interactions)\.png)$/;

  function token(name, fallback) {
    var value = "";
    try {
      value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    } catch (e) { /* no styles yet */ }
    return value || fallback;
  }

  function colours() {
    return {
      line: token("--hue-cyan", "#00aed8"),
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
    var margin = { top: options.legend ? 30 : 14, right: bar ? 78 : 20, bottom: 40,
                   left: options.left || 62 };
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
    // What is plotted is drawn in a layer kept inside the axes, so a plot
    // zoomed into shows nothing outside them.
    var clipId = "fig-clip-" + Math.random().toString(36).slice(2, 9);
    el("rect", { x: plot.left, y: plot.top, width: plot.right - plot.left, height: plot.bottom - plot.top },
       el("clipPath", { id: clipId }, el("defs", {}, svg)));
    var layer = el("g", { "clip-path": "url(#" + clipId + ")", class: "figure-layer" }, svg);
    var tip = document.createElement("div");
    tip.className = "series-tip";
    tip.hidden = true;
    host.appendChild(tip);
    return { svg: svg, layer: layer, plot: plot, width: width, height: height, tip: tip, c: colours(),
             view: viewOf(host, data), clock: { factor: 1, unit: "ns" }, host: host, data: data };
  }

  /* The kinds plotted on the simulation's clock (or on frame numbers, where
   * the run kept none): a range dragged across one is the page's. */
  var TIMED = { states: "x_label", labels: "x_label", lines: "x_label", matrix: "axis_label" };

  function clockOf(data) {
    var key = TIMED[data.kind];
    var label = key ? data[key] : "";
    return label === "Time (ns)" || label === "Frame" ? label : "";
  }

  /* What a plot shows: the page's time range for one on its clock, else
   * the part it was zoomed to, else all of it. */
  function viewOf(host, data) {
    var tools = window.FastMDXPlotTools;
    var clock = clockOf(data);
    var shared = clock && tools ? tools.range(clock) : null;
    if (shared) {
      return data.kind === "matrix" ? { x: [shared.from, shared.to], y: [shared.from, shared.to] }
        : { x: [shared.from, shared.to] };
    }
    return host._view || null;
  }

  /* The axes' extents as shown (the view, where there is one), and times in
   * picoseconds where what is shown is under a nanosecond. */
  function scale(f, x, y) {
    x.full = [x.low, x.high];
    y.full = [y.low, y.high];
    if (f.view && f.view.x) { x.low = f.view.x[0]; x.high = f.view.x[1]; }
    if (f.view && f.view.y) { y.low = f.view.y[0]; y.high = f.view.y[1]; }
    // Fixed ticks (the dihedrals' every 90 degrees) that leave fewer than
    // three in what is shown give way to ticks for the view.
    [[x, "x"], [y, "y"]].forEach(function (pair) {
      var axis = pair[0];
      if (!Array.isArray(axis.ticks) || !f.view || !f.view[pair[1]]) return;
      var inside = axis.ticks.filter(function (v) { return v >= axis.low && v <= axis.high; });
      if (inside.length < 3) axis.ticks = undefined;
    });
    if (x.label === "Time (ns)" && Math.max(Math.abs(x.low), Math.abs(x.high)) < 1) {
      f.clock = { factor: 1000, unit: "ps" };
    }
    f.x = x;
    f.y = y;
    var plot = f.plot;
    return {
      px: function (v) { return plot.left + (v - x.low) / (x.high - x.low) * (plot.right - plot.left); },
      py: function (v) { return plot.bottom - (v - y.low) / (y.high - y.low) * (plot.bottom - plot.top); },
    };
  }

  function axes(f, x, y) {
    var plot = f.plot, svg = f.svg, c = f.c;
    if (!f.x) scale(f, x, y);
    // On the simulation's clock in the unit its span reads in.
    function timed(axis) {
      if (axis.label !== "Time (ns)" || f.clock.factor === 1 || axis.ticks === false) return;
      var k = f.clock.factor;
      var shown = ticks(axis.low * k, axis.high * k, axis === x ? 5 : 4);
      var say = tickText(shown);
      axis.ticks = shown.map(function (v) { return v / k; });
      axis.say = function (v) { return say(v * k); };
      axis.label = "Time (ps)";
    }
    timed(x);
    timed(y);
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

  /* Past this many cells, a map is drawn as an image of its cells; under
   * it, as the cells themselves, each run of one colour along a row one
   * rectangle, so it is sharp at any size and saved as such. */
  var MOST_CELLS = 8000;

  /* A grid of values over the extent `where` covers: row 0 at the bottom. */
  function raster(f, grid, colourOf, where) {
    var ny = grid.length, nx = ny ? grid[0].length : 0;
    if (!nx) return;
    var w = (where.right - where.left) / nx, h = (where.bottom - where.top) / ny;
    var runs = [], count = 0;
    for (var row = 0; row < ny && count <= MOST_CELLS; row += 1) {
      var i = 0;
      while (i < nx) {
        var colour = colourOf(grid[row][i], i, row);
        var key = colour ? colour.join(",") : "";
        var k = i + 1;
        while (k < nx) {
          var next = colourOf(grid[row][k], k, row);
          if ((next ? next.join(",") : "") !== key) break;
          k += 1;
        }
        if (colour) { runs.push([row, i, k - i, colour]); count += 1; }
        i = k;
      }
    }
    if (count <= MOST_CELLS) {
      var cells = el("g", { class: "figure-cells", "shape-rendering": "crispEdges" }, f.layer);
      runs.forEach(function (r) {
        el("rect", { x: (where.left + r[1] * w).toFixed(2), y: (where.bottom - (r[0] + 1) * h).toFixed(2),
                     width: (r[2] * w + 0.4).toFixed(2), height: (h + 0.4).toFixed(2), fill: rgb(r[3]) }, cells);
      });
      f.svg.setAttribute("data-cells", String(count));
      return;
    }
    f.svg.setAttribute("data-cells", "image");
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
      // Each cell its own colour, not blended with the next.
      style: "image-rendering: pixelated",
    }, f.layer);
    el("title", {}, shown).textContent = "Plotted as a picture of its " + nx + " by " + ny +
      " cells: more runs of a colour than are plotted one by one";
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

  function when(data, i, f) {
    var t = data.x ? data.x[i] : data.t[i];
    var label = data.x_label || data.t_label;
    var clock = f && f.clock ? f.clock : { factor: 1, unit: "ns" };
    return label === "Frame" ? "frame " + data.frames[i]
      : format(t * clock.factor) + " " + clock.unit + " \u00b7 frame " + data.frames[i];
  }

  /* A click opens its frame once it is not the first of a double click,
   * which shows all again (plot-tools.js). */
  var pendingFrame = null;
  document.addEventListener("dblclick", function () { clearTimeout(pendingFrame); }, true);

  function openFrame(data, frameNumber) {
    if (!data.linked || frameNumber == null || !window.FastMDXSeries || !window.FastMDXSeries.openFrame) return;
    clearTimeout(pendingFrame);
    pendingFrame = setTimeout(function () { window.FastMDXSeries.openFrame(frameNumber); }, 280);
  }

  function focusResidue(entity) {
    if (!entity || !window.FastMDXDashboard) return;
    window.FastMDXDashboard.focusEntity(entity);
  }

  function openFrameAndResidue(data, index, entity) {
    openFrame(data, data.frames[index]);
    focusResidue(entity);
  }

  function nearestIndex(xs, x) {
    var lo = 0, hi = xs.length - 1;
    while (hi - lo > 1) {
      var mid = (lo + hi) >> 1;
      if (xs[mid] < x) lo = mid; else hi = mid;
    }
    return Math.abs(xs[lo] - x) <= Math.abs(xs[hi] - x) ? lo : hi;
  }

  /* Whether a value lies in what an axis shows (the view, once zoomed). */
  function shownOn(axis, v) {
    return axis != null && v != null && isFinite(v) && v >= axis.low - 1e-9 && v <= axis.high + 1e-9;
  }

  /* The keyboard's place on a plot, kept on its host for the data it
   * shows: zooming, moving or a time range draws the plot again, and the
   * new drawing goes on from the point (or cell) the last one had, rather
   * than from the first. */
  function keptPlace(f, fresh) {
    var host = f.host;
    if (!host) return fresh;
    if (!host._keyPlace || host._keyPlace.data !== f.data) {
      host._keyPlace = Object.assign({ data: f.data }, fresh);
    }
    return host._keyPlace;
  }

  /* What a plot zoomed into says of its keys, after what it says of its
   * points. */
  var ZOOMED_KEYS = " Zoomed in: the arrows move the view; Home and End select the first and" +
    " last shown; Enter opens the one selected.";

  /* The keys along a plot's points. visible(i) says whether point i is in
   * view: one selected that a zoom or a move takes out of view gives way
   * to the shown point nearest it, so Enter never opens a point not
   * shown; with none shown, nothing is selected and Enter opens nothing. */
  function keyboard(f, label, count, show, activate, selectable, vertical, visible) {
    if (!count) return { set: function () {}, current: function () { return -1; } };
    var place = keptPlace(f, { index: -1 });
    var svg = f.svg;
    // Its arrows move along its points; zoom keeps + - [ ] (plot-tools.js).
    f.ownArrows = true;
    svg.setAttribute("role", "application");
    svg.setAttribute("tabindex", "0");
    svg.setAttribute("aria-label", label + (f.view ? ZOOMED_KEYS : ""));
    svg.setAttribute("aria-keyshortcuts", (vertical ? "ArrowUp ArrowDown" : "ArrowLeft ArrowRight")
      + " Home End Enter");
    function usable(i) {
      return i >= 0 && i < count && (!selectable || selectable(i)) && (!visible || visible(i));
    }
    // The point chosen in this drawing: the one remembered where it is
    // shown, else the shown point nearest it (which becomes the one
    // remembered), else none, the one remembered kept for a later drawing
    // that shows it again.
    var current = -1;
    function set(i, display) {
      if (i < 0 || i >= count) return current;
      current = place.index = i;
      if (display !== false) show(i);
      return i;
    }
    /* The first usable point from i on, in the direction given; -1 for none. */
    function seek(i, direction) {
      i = Math.max(0, Math.min(count - 1, i));
      while (i >= 0 && i < count && !usable(i)) i += direction;
      return i >= 0 && i < count ? i : -1;
    }
    /* The usable point nearest i (the earlier one on a tie). */
    function nearest(i) {
      for (var d = 0; d < count; d += 1) {
        if (usable(i - d)) return i - d;
        if (usable(i + d)) return i + d;
      }
      return -1;
    }
    if (place.index >= 0) {
      current = usable(place.index) ? place.index : nearest(place.index);
      if (current >= 0) place.index = current;
    }
    svg.addEventListener("focus", function () {
      if (current < 0) set(seek(0, 1));
      else show(current);
    });
    svg.addEventListener("keydown", function (event) {
      // Zoomed in, the arrows move the view (plot-tools.js); Home, End and
      // Enter still go along the points shown.
      if (f.view && /^Arrow/.test(event.key)) return;
      var base = current < 0 ? 0 : current;
      if (event.key === "Home") set(seek(0, 1));
      else if (event.key === "End") set(seek(count - 1, -1));
      else if ((!vertical && event.key === "ArrowRight") || (vertical && event.key === "ArrowDown")) {
        set(seek(Math.min(count - 1, base + 1), 1));
      } else if ((!vertical && event.key === "ArrowLeft") || (vertical && event.key === "ArrowUp")) {
        set(seek(Math.max(0, base - 1), -1));
      } else if ((event.key === "Enter" || event.key === " ") && usable(current)) {
        activate(current, event);
      } else return;
      event.preventDefault();
    });
    return { set: set, current: function () { return current; } };
  }

  /* The keys across a grid's cells: a column and a row, each kept through
   * a drawing again as keyboard() keeps a point, and each giving way to the
   * shown one nearest it when a zoom or a move takes it out of view.
   * activate(column, row, event) has the key, for Shift+Enter. */
  function gridKeyboard(f, label, columns, rows, show, activate, columnSeen, rowSeen) {
    var svg = f.svg;
    if (!columns || !rows) return { set: function () {} };
    var place = keptPlace(f, { column: -1, row: 0 });
    f.ownArrows = true;
    svg.setAttribute("role", "application");
    svg.setAttribute("tabindex", "0");
    svg.setAttribute("aria-label", label + (f.view ? ZOOMED_KEYS : ""));
    svg.setAttribute("aria-keyshortcuts", "ArrowLeft ArrowRight ArrowUp ArrowDown Home End Enter");
    function seenColumn(i) { return i >= 0 && i < columns && (!columnSeen || columnSeen(i)); }
    function seenRow(j) { return j >= 0 && j < rows && (!rowSeen || rowSeen(j)); }
    function nearestOf(seen, i, n) {
      for (var d = 0; d < n; d += 1) {
        if (seen(i - d)) return i - d;
        if (seen(i + d)) return i + d;
      }
      return -1;
    }
    function firstOf(seen, n, from, direction) {
      for (var i = from; i >= 0 && i < n; i += direction) if (seen(i)) return i;
      return -1;
    }
    // The cell chosen in this drawing, as keyboard() chooses its point:
    // the remembered column and row where shown, else the shown ones
    // nearest them, else none, the remembered ones kept.
    var column = -1, row = 0;
    if (place.column >= 0) {
      var c = seenColumn(place.column) ? place.column : nearestOf(seenColumn, place.column, columns);
      var r = seenRow(place.row) ? place.row : nearestOf(seenRow, place.row, rows);
      if (c >= 0 && r >= 0) { column = place.column = c; row = place.row = r; }
    }
    function set(i, j, display) {
      column = place.column = Math.max(0, Math.min(columns - 1, i));
      row = place.row = Math.max(0, Math.min(rows - 1, j));
      if (display !== false) show(column, row);
    }
    function selected() { return column >= 0 && seenColumn(column) && seenRow(row); }
    svg.addEventListener("focus", function () {
      if (column < 0) {
        var i = firstOf(seenColumn, columns, 0, 1), j = firstOf(seenRow, rows, 0, 1);
        if (i >= 0 && j >= 0) set(i, j);
      } else {
        show(column, row);
      }
    });
    svg.addEventListener("keydown", function (event) {
      if (f.view && /^Arrow/.test(event.key)) return;
      var at = column < 0 ? 0 : column;
      var first;
      if (event.key === "Home") {
        first = firstOf(seenColumn, columns, 0, 1);
        if (first >= 0 && seenRow(row)) set(first, row);
      } else if (event.key === "End") {
        first = firstOf(seenColumn, columns, columns - 1, -1);
        if (first >= 0 && seenRow(row)) set(first, row);
      } else if (event.key === "ArrowLeft") set(at - 1, row);
      else if (event.key === "ArrowRight") set(at + 1, row);
      else if (event.key === "ArrowUp") set(at, row - 1);
      else if (event.key === "ArrowDown") set(at, row + 1);
      else if ((event.key === "Enter" || event.key === " ") && selected()) {
        activate(column, row, event);
      } else return;
      event.preventDefault();
    });
    return { set: set };
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

  /* Where a grid's whole extent falls, drawn on the axes as shown. */
  function extentOf(map, x, y) {
    return { left: map.px(x.full[0]), right: map.px(x.full[1]),
             top: map.py(y.full[1]), bottom: map.py(y.full[0]) };
  }

  /* ------------------------------------------------------------------ */
  /* Each kind of figure                                                  */
  /* ------------------------------------------------------------------ */

  function plotStates(host, data) {
    var f = frame(host, data, { height: 310, legend: true, said: data.title + ", " + data.residues.length +
      " residues over " + data.x.length + " frames" });
    var c = f.c;
    var fill = { H: hexToRgb(c.line.charAt(0) === "#" ? c.line : "#00aed8"),
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
    var map = scale(f, x, y);
    raster(f, grid, function (code) { return fill[code] || null; }, extentOf(map, x, y));
    axes(f, x, y);
    legend(f, data.classes.map(function (k) {
      return { label: k.label, colour: rgb(fill[k.code]) };
    }));
    var names = {};
    data.classes.forEach(function (k) { names[k.code] = k.label; });
    var hint = data.linked ? "Click to open this frame and focus the residue; use arrow keys, then Enter" : "";
    function cellAt(event) {
      var at = pointer(f, event);
      if (!inside(f, at)) return null;
      return {
        at: at,
        frame: nearestIndex(data.x, x.low + (at.x - f.plot.left) /
          (f.plot.right - f.plot.left) * (x.high - x.low)),
        residue: Math.min(residues, Math.max(1, Math.round(y.low +
          (f.plot.bottom - at.y) / (f.plot.bottom - f.plot.top) * (y.high - y.low)))),
      };
    }
    function showCell(i, res, at) {
      var entity = data.entities && data.entities[res - 1];
      showTip(f, at ? at.x : map.px(data.x[i]), at ? at.y : map.py(res),
        [names[data.states[i].charAt(res - 1)] || "", "residue " + data.residues[res - 1], when(data, i, f)], hint);
      return entity;
    }
    var keys = gridKeyboard(f, data.title + ". Left and right select a frame; up and down select a residue.",
      data.x.length, residues, function (i, res) { showCell(i, res + 1); }, function (i, res) {
        openFrameAndResidue(data, i, data.entities && data.entities[res]);
      }, function (i) { return shownOn(f.x, data.x[i]); }, function (res) { return shownOn(f.y, res + 1); });
    f.svg.addEventListener("mousemove", function (event) {
      var cell = cellAt(event);
      if (!cell) { f.tip.hidden = true; return; }
      keys.set(cell.frame, cell.residue - 1, false);
      showCell(cell.frame, cell.residue, cell.at);
    });
    f.svg.addEventListener("mouseleave", function () { f.tip.hidden = true; });
    f.svg.addEventListener("click", function (event) {
      var cell = cellAt(event);
      if (cell) openFrameAndResidue(data, cell.frame,
        data.entities && data.entities[cell.residue - 1]);
    });
    return f;
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
    var layer = el("g", {}, f.layer);
    for (var i = 0; i < n; i += 1) {
      el("circle", { cx: map.px(data.x[i]).toFixed(1), cy: map.py(data.labels[i]).toFixed(1), r: r,
                     fill: category(data.labels[i]) }, layer);
    }
    var hint = data.linked ? "Click to open this frame; use left and right arrows, then Enter" : "";
    function indexAt(event) {
      var at = pointer(f, event);
      if (!inside(f, at)) return -1;
      return nearestIndex(data.x, x.low + (at.x - f.plot.left) /
        (f.plot.right - f.plot.left) * (x.high - x.low));
    }
    function show(i) {
      var k = data.labels[i];
      showTip(f, map.px(data.x[i]), map.py(k),
        [k < 0 ? "noise" : "cluster " + k, when(data, i, f)], hint);
    }
    var keys = keyboard(f, data.title + ". Select a frame with left and right arrows.", n,
      show, function (i) { openFrame(data, data.frames[i]); }, null, false,
      function (i) { return shownOn(f.x, data.x[i]); });
    f.svg.addEventListener("mousemove", function (event) {
      var i = indexAt(event);
      if (i < 0) { f.tip.hidden = true; return; }
      keys.set(i, false);
      show(i);
    });
    f.svg.addEventListener("mouseleave", function () { f.tip.hidden = true; });
    f.svg.addEventListener("click", function (event) {
      var i = indexAt(event);
      if (i >= 0) openFrame(data, data.frames[i]);
    });
    return f;
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
    function indexAt(event) {
      var at = pointer(f, event);
      if (!inside(f, at)) return -1;
      var i = Math.round(x.low + (at.x - f.plot.left) /
        (f.plot.right - f.plot.left) * (x.high - x.low));
      return i >= 0 && i < n ? i : -1;
    }
    function show(i) {
      var k = data.clusters[i];
      var parts = ["cluster " + k.cluster, k.frames + " frames" +
        (k.fraction != null ? " (" + (k.fraction * 100).toFixed(1) + "%)" : "")];
      if (k.medoid_frame != null) {
        parts.push("medoid " + (k.medoid_time_ns != null ? format(k.medoid_time_ns) + " ns, " : "") +
                   "frame " + k.medoid_frame);
      }
      showTip(f, map.px(i), map.py(k.frames), parts,
              data.linked && k.medoid_frame != null ? "Click to open its medoid" : "");
    }
    var keys = keyboard(f, "Cluster populations. Up and down select a cluster; Enter opens its saved medoid.",
      n, show, function (i) { openFrame(data, data.clusters[i].medoid_frame); },
      function (i) { return data.linked && data.clusters[i].medoid_frame != null; }, true);
    f.svg.addEventListener("mousemove", function (event) {
      var i = indexAt(event);
      if (i < 0) { f.tip.hidden = true; return; }
      keys.set(i, false);
      show(i);
    });
    f.svg.addEventListener("mouseleave", function () { f.tip.hidden = true; });
    f.svg.addEventListener("click", function (event) {
      var i = indexAt(event);
      if (i >= 0 && data.clusters[i].medoid_frame != null) {
        openFrame(data, data.clusters[i].medoid_frame);
      }
    });
    return f;
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
                   stroke: group == null ? f.c.axis : category(group), class: "figure-link" }, f.layer);
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
    return f;
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
    var map = scale(f, x, y);
    raster(f, values, function (v) {
      if (v == null) return opts.empty === "shade" ? shade : null;
      return viridis((v - range[0]) / (range[1] - range[0]));
    }, extentOf(map, x, y));
    axes(f, x, y);
    (data.guides || []).forEach(function (g) {
      el("line", { x1: map.px(g), x2: map.px(g), y1: f.plot.top, y2: f.plot.bottom,
                   stroke: f.c.text, "stroke-opacity": 0.5, "stroke-width": 0.8 }, f.layer);
      el("line", { x1: f.plot.left, x2: f.plot.right, y1: map.py(g), y2: map.py(g),
                   stroke: f.c.text, "stroke-opacity": 0.5, "stroke-width": 0.8 }, f.layer);
    });
    colourBar(f, range[0], range[1], data.label + (data.unit ? " (" + data.unit + ")" : ""), viridis);
    var nx = values[0].length, ny = values.length;
    // The cell under the pointer, by where it is on the axes as shown
    // (zoomed, the view's extents, not the whole grid's).
    function cellOf(edgesOf, value, count) {
      var k = 0;
      while (k < count - 1 && edgesOf[k + 1] <= value) k += 1;
      return k;
    }
    function cellAt(event) {
      var at = pointer(f, event);
      if (!inside(f, at)) return null;
      var dx = x.low + (at.x - f.plot.left) / (f.plot.right - f.plot.left) * (x.high - x.low);
      var dy = y.low + (f.plot.bottom - at.y) / (f.plot.bottom - f.plot.top) * (y.high - y.low);
      return { at: at, i: cellOf(ex, dx, nx), j: cellOf(ey, dy, ny) };
    }
    function showCell(i, j, at) {
      var v = values[j][i];
      var cx = (ex[i] + ex[i + 1]) / 2, cy = (ey[j] + ey[j + 1]) / 2;
      showTip(f, at ? at.x : map.px(cx), at ? at.y : map.py(cy),
        [v == null ? opts.emptySaid : format(v) + (data.unit ? " " + data.unit : ""),
         opts.where(cx, cy, i, j, f.clock)], opts.hint || "");
    }
    f.svg.addEventListener("mousemove", function (event) {
      var cell = cellAt(event);
      if (!cell) { f.tip.hidden = true; return; }
      showCell(cell.i, cell.j, cell.at);
    });
    f.svg.addEventListener("mouseleave", function () { f.tip.hidden = true; });
    f.svg.addEventListener("click", function (event) {
      var cell = cellAt(event);
      if (cell && opts.activate) opts.activate(cell.i, cell.j, event);
    });
    f.showCell = showCell;
    return f;
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
    var exact = Array.isArray(data.frames) && data.frames.length === n && data.linked;
    var grouped = Array.isArray(data.frame_groups) && data.frame_groups.length === n;
    var hint = exact ? "Click opens the row frame; Shift-click opens the column frame. Use arrows, then Enter or Shift+Enter."
      : (grouped ? "Each cell averages frame groups; no single frame is selected." : "");
    function rowSaid(index) {
      if (exact) return "frame " + data.frames[index];
      var group = grouped ? data.frame_groups[index] : null;
      return group ? "source frames " + group.first + "–" + group.last
        + " (" + group.count + " sampled frames)" : "frame group " + (index + 1);
    }
    var f = plotGrid(host, data, {
      edgesX: e, edgesY: e, xLabel: data.axis_label, yLabel: data.axis_label,
      emptySaid: "—",
      hint: hint,
      where: function (a, b, column, row, clock) {
        var k = clock ? clock.factor : 1, unit = clock ? clock.unit : "ns";
        var axis = timed ? format(a * k) + " " + unit + " and " + format(b * k) + " " + unit
          : "axis frames " + Math.round(a) + " and " + Math.round(b);
        return axis + "; row " + rowSaid(row) + "; column " + rowSaid(column);
      },
      activate: exact ? function (column, row, event) {
        openFrame(data, data.frames[event.shiftKey ? column : row]);
      } : null,
    });
    if (exact) {
      // Its row and its column each kept through a zoom or a move: Enter
      // opens the row's frame, Shift+Enter the column's.
      var centre = function (k) { return (e[k] + e[k + 1]) / 2; };
      gridKeyboard(f, "RMSD frame matrix. Arrows select a cell. Enter opens its row frame; " +
        "Shift+Enter opens its column frame.", n, n,
        function (column, row) { f.showCell(column, row); },
        function (column, row, event) { openFrame(data, data.frames[event.shiftKey ? column : row]); },
        function (column) { return shownOn(f.x, centre(column)); },
        function (row) { return shownOn(f.y, centre(row)); });
      f.svg.setAttribute("aria-keyshortcuts",
        "ArrowLeft ArrowRight ArrowUp ArrowDown Home End Enter Shift+Enter");
    }
    return f;
  }

  function plotLandscape(host, data) {
    return plotGrid(host, data, {
      edgesX: data.edges_x, edgesY: data.edges_y, xLabel: data.axes[0], yLabel: data.axes[1],
      emptySaid: "not visited",
      where: function (a, b) { return format(a) + ", " + format(b); },
    });
  }

  function plotDensity(host, data) {
    return plotGrid(host, data, {
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
    var layer = el("g", {}, f.layer);
    var at = [];
    for (var i = 0; i < n; i += 1) {
      if (data.x[i] == null || data.y[i] == null) { at.push(null); continue; }
      var cx = map.px(data.x[i]), cy = map.py(data.y[i]);
      at.push([cx, cy]);
      el("circle", { cx: cx.toFixed(1), cy: cy.toFixed(1), r: r,
                     fill: rgb(viridis((data.t[i] - tr[0]) / (tr[1] - tr[0]))) }, layer);
    }
    // Its time in the unit the run's length reads in, as the axes are: a
    // run under a nanosecond in ps.
    var framed = data.t_label === "Frame";
    var inPs = !framed && Math.max(Math.abs(tr[0]), Math.abs(tr[1])) < 1;
    colourBar(f, inPs ? tr[0] * 1000 : tr[0], inPs ? tr[1] * 1000 : tr[1],
              framed ? "Frame" : inPs ? "Time (ps)" : "Time (ns)", viridis);
    var hint = data.linked ? "Click to open this frame; use left and right arrows, then Enter" : "";
    function pointAt(event) {
      var p = pointer(f, event);
      if (!inside(f, p)) return -1;
      var best = -1, distance = Infinity;
      at.forEach(function (q, k) {
        if (!q) return;
        var d = (q[0] - p.x) * (q[0] - p.x) + (q[1] - p.y) * (q[1] - p.y);
        if (d < distance) { distance = d; best = k; }
      });
      return best >= 0 && distance <= 400 ? best : -1;
    }
    function show(best) {
      var unit = data.unit ? " " + data.unit : "";
      showTip(f, at[best][0], at[best][1], [when(data, best, f),
        format(data.x[best]) + unit + ", " + format(data.y[best]) + unit], hint);
    }
    var keys = keyboard(f, data.title + ". Left and right select a point; Enter opens its frame.", n,
      show, function (i) { openFrame(data, data.frames[i]); },
      function (i) { return !!at[i]; }, false, function (i) {
        return at[i][0] >= f.plot.left - 0.5 && at[i][0] <= f.plot.right + 0.5 &&
          at[i][1] >= f.plot.top - 0.5 && at[i][1] <= f.plot.bottom + 0.5;
      });
    f.svg.addEventListener("mousemove", function (event) {
      var i = pointAt(event);
      if (i < 0) { f.tip.hidden = true; return; }
      keys.set(i, false);
      show(i);
    });
    f.svg.addEventListener("mouseleave", function () { f.tip.hidden = true; });
    f.svg.addEventListener("click", function (event) {
      var i = pointAt(event);
      if (i >= 0) openFrame(data, data.frames[i]);
    });
    return f;
  }

  /* One bar a row, each named at its left, the largest first: hollow
   * where the analysis says it rests on too few observations, with its
   * error where it has one. A note under it says what is left out. */
  function plotHBars(host, data) {
    var bars = data.bars || [];
    if (!bars.length) {
      host.replaceChildren();
      host.appendChild(Object.assign(document.createElement("p"), {
        className: "muted small figure-note", textContent: data.note || "Nothing to plot." }));
      return;
    }
    var width = Math.max(280, Math.round(host.clientWidth || 480));
    // Room for the names, up to two fifths of the width; longer is cut.
    var longest = Math.max.apply(null, bars.map(function (b) { return b.label.length; }));
    var left = Math.min(Math.round(width * 0.4), Math.max(70, longest * 6.2 + 14));
    var row = 20;
    var f = frame(host, data, { left: left, height: 14 + 40 + bars.length * row,
      said: data.title + ": " + bars.map(function (b) { return b.said.slice(0, 2).join(", "); }).join("; ") });
    var high = data.x_high != null ? data.x_high : Math.max.apply(null, bars.map(function (b) {
      return b.value + (b.error || 0);
    })) * 1.1 || 1;
    var x = { low: data.x_low || 0, high: high, label: data.x_label };
    var y = { low: 0, high: bars.length, label: "", ticks: false, grid: false };
    var map = axes(f, x, y);
    var c = f.c;
    var fit = Math.floor((left - 12) / 6.2);
    bars.forEach(function (b, i) {
      var top = f.plot.top + i * row + 3;
      var right = map.px(Math.max(x.low, Math.min(x.high, b.value)));
      var shape = { x: f.plot.left, y: top, width: Math.max(0, right - f.plot.left),
                    height: row - 6, rx: 2, class: "figure-bar", fill: c.line };
      if (b.hollow) {
        shape.fill = "none";
        shape.stroke = c.line;
        shape["stroke-width"] = 1.2;
        shape["stroke-dasharray"] = "3 2";
      }
      var bar = el("rect", shape, f.svg);
      bar.dataset.index = String(i);
      if (b.error != null && isFinite(b.error) && b.error > 0) {
        var mid = top + (row - 6) / 2;
        var lo = map.px(Math.max(x.low, b.value - b.error)), hi = map.px(Math.min(x.high, b.value + b.error));
        el("path", { d: "M" + lo + " " + mid + "H" + hi + "M" + lo + " " + (mid - 4) + "V" + (mid + 4)
                     + "M" + hi + " " + (mid - 4) + "V" + (mid + 4),
                     stroke: c.warm, "stroke-width": 1.2, fill: "none" }, f.svg);
      }
      var name = b.label.length > fit ? b.label.slice(0, Math.max(1, fit - 1)) + "\u2026" : b.label;
      text(f.svg, f.plot.left - 8, top + (row - 6) / 2 + 3.5, name,
           { fill: c.text, "text-anchor": "end" });
    });
    if (data.note) {
      var note = document.createElement("p");
      note.className = "muted small figure-note";
      note.textContent = data.note;
      host.appendChild(note);
    }
    function indexAt(event) {
      var at = pointer(f, event);
      var i = Math.floor((at.y - f.plot.top) / row);
      if (at.x < 0 || at.x > f.plot.right || at.y < f.plot.top || at.y > f.plot.bottom
          || i < 0 || i >= bars.length) return -1;
      return i;
    }
    function show(i, at) {
      var b = bars[i];
      var action = b.entity ? "Click to focus this residue; use up and down arrows, then Enter" : "";
      showTip(f, at ? Math.min(at.x, f.plot.right - 20) : f.plot.left,
        f.plot.top + i * row + 4, b.said, action);
    }
    var selectable = function (i) { return !!bars[i].entity; };
    var keys = bars.some(function (b) { return b.entity; })
      ? keyboard(f, data.title + ". Up and down select an identified residue; Enter focuses it.",
        bars.length, show, function (i) { focusResidue(bars[i].entity); }, selectable, true)
      : { set: function () {} };
    f.svg.addEventListener("mousemove", function (event) {
      var i = indexAt(event);
      if (i < 0) { f.tip.hidden = true; return; }
      keys.set(i, false);
      show(i, pointer(f, event));
    });
    f.svg.addEventListener("mouseleave", function () { f.tip.hidden = true; });
    f.svg.addEventListener("click", function (event) {
      var i = indexAt(event);
      if (i >= 0 && bars[i].entity) focusResidue(bars[i].entity);
    });
    return f;
  }

  /* A few series over the same frames, each in its colour, named above
   * the plot; under the pointer, each one's value at the frame there. */
  function plotLines(host, data) {
    var f = frame(host, data, { height: 280, legend: true, said: data.title + " over "
      + data.x.length + " frames" });
    var all = [];
    data.series.forEach(function (s) { all = all.concat(s.y); });
    var yr = span(all), pad = (yr[1] - yr[0]) * 0.06;
    var n = data.x.length;
    var x = { low: data.x[0], high: n > 1 && data.x[n - 1] > data.x[0] ? data.x[n - 1] : data.x[0] + 1,
              label: data.x_label };
    var y = { low: yr[0] - pad, high: yr[1] + pad, label: data.y_label };
    var map = axes(f, x, y);
    data.series.forEach(function (s, k) {
      var d = "";
      s.y.forEach(function (v, i) {
        if (v == null || !isFinite(v)) return;
        d += (d ? "L" : "M") + map.px(data.x[i]).toFixed(1) + " " + map.py(v).toFixed(1);
      });
      el("path", { d: d, fill: "none", stroke: category(k), "stroke-width": 1.4,
                   "stroke-linejoin": "round", class: "figure-line" }, f.layer);
    });
    legend(f, data.series.map(function (s, k) { return { label: s.label, colour: category(k) }; }));
    var guide = el("line", { y1: f.plot.top, y2: f.plot.bottom, stroke: f.c.axis,
                             "stroke-dasharray": "3 3", visibility: "hidden" }, f.svg);
    var hint = data.linked ? "Click to open this frame; use left and right arrows, then Enter" : "";
    var unit = data.unit ? " " + data.unit : "";
    function indexAt(event) {
      var at = pointer(f, event);
      if (!inside(f, at)) return -1;
      return nearestIndex(data.x, x.low + (at.x - f.plot.left) /
        (f.plot.right - f.plot.left) * (x.high - x.low));
    }
    function show(i, at) {
      var px = map.px(data.x[i]);
      guide.setAttribute("x1", px);
      guide.setAttribute("x2", px);
      guide.setAttribute("visibility", "visible");
      showTip(f, px, at ? at.y : (f.plot.top + f.plot.bottom) / 2,
        [when(data, i, f)].concat(data.series.map(function (s) {
        return s.label + " " + format(s.y[i]) + unit;
      })), hint);
    }
    var keys = keyboard(f, data.title + ". Left and right select a frame; Enter opens it.", n,
      show, function (i) { openFrame(data, data.frames[i]); }, null, false,
      function (i) { return shownOn(f.x, data.x[i]); });
    f.svg.addEventListener("mousemove", function (event) {
      var at = pointer(f, event), i = indexAt(event);
      if (i < 0) { f.tip.hidden = true; guide.setAttribute("visibility", "hidden"); return; }
      keys.set(i, false);
      show(i, at);
    });
    f.svg.addEventListener("mouseleave", function () {
      f.tip.hidden = true;
      guide.setAttribute("visibility", "hidden");
    });
    f.svg.addEventListener("click", function (event) {
      var i = indexAt(event);
      if (i >= 0) openFrame(data, data.frames[i]);
    });
    return f;
  }

  var PLOTS = {
    states: plotStates, labels: plotLabels, bars: plotBars, dendrogram: plotDendrogram,
    matrix: plotMatrix, landscape: plotLandscape, density: plotDensity, projection: plotProjection,
    hbars: plotHBars, lines: plotLines,
  };

  /* How a plot of each kind zooms: along x, in both, or not at all (a row
   * of categories, a list of bars). */
  var ZOOM = { states: "x", labels: "x", lines: "x", matrix: "x", dendrogram: "x",
               landscape: "xy", density: "xy", projection: "xy", bars: "", hbars: "" };

  /* The smallest span a key zooms to: about four points', frames' or
   * cells' worth of the whole (in the data's units, as the view is). */
  function leastOf(data, x, y) {
    var n = (data.x && data.x.length) || (data.values && data.values.length) ||
      (data.icoord && data.icoord.length) || 50;
    // Points spread over both axes (a projection) keep four in view with
    // each axis at two points' worth.
    var share = Math.min(1, data.kind === "projection" ? Math.sqrt(4 / Math.max(4, n))
      : 4 / Math.max(4, n));
    var span = function (axis) {
      var full = axis.full || [axis.low, axis.high];
      return Math.abs(full[1] - full[0]) * share;
    };
    return { x: span(x), y: span(y) };
  }

  function plot(host, data) {
    var how = PLOTS[data.kind];
    if (!how) return;
    var f = how(host, data);
    if (f) host.dataset.view = f.view ? (f.view === host._view ? "zoomed" : "range") : "";
    var tools = window.FastMDXPlotTools;
    if (!f || !tools) return;
    var clock = clockOf(data);
    var x = f.x || { low: 0, high: 1 }, y = f.y || { low: 0, high: 1 };
    tools.attach(host, {
      svg: f.svg, width: f.width, height: f.height, plot: f.plot,
      zoom: ZOOM[data.kind] || "", shared: clock, zoomed: !!f.view,
      // A plot's arrows go along its points until it is zoomed in, then move
      // the view.
      ownArrows: !!f.ownArrows && !f.view,
      least: leastOf(data, x, y),
      full: { x: x.full || null, y: y.full || null },
      name: (data.figure || data.title || data.kind).replace(/^analysis\//, "").replace(/\.png$/, "") +
        (f.view ? "_range" : ""),
      invert: function (px, py) {
        return { x: x.low + (px - f.plot.left) / (f.plot.right - f.plot.left) * (x.high - x.low),
                 y: y.low + (f.plot.bottom - py) / (f.plot.bottom - f.plot.top) * (y.high - y.low) };
      },
      onView: function (view) { host._view = view; plot(host, data); },
      onReset: function () { host._view = null; plot(host, data); },
      csv: function () { return csvOf(data, x, y); },
    });
    if (clock) drawnOnTheClock.push({ host: host, data: data });
  }

  /* A row's time and frame, or its frame alone where the run kept no
   * clock. */
  function clockHead(label) { return label === "Frame" ? ["frame"] : ["time_ns", "frame"]; }
  function clockCells(label, t, frame) { return label === "Frame" ? [frame] : [t, frame]; }

  /* The numbers a plot shows, as rows: those in view where it is zoomed. */
  function csvOf(data, x, y) {
    function inX(v) { return v >= x.low - 1e-12 && v <= x.high + 1e-12; }
    function inY(v) { return v >= y.low - 1e-12 && v <= y.high + 1e-12; }
    var rows;
    if (data.kind === "states") {
      rows = [clockHead(data.x_label).concat(data.residues)];
      data.x.forEach(function (t, i) {
        if (inX(t)) rows.push(clockCells(data.x_label, t, data.frames[i]).concat(data.states[i].split("")));
      });
    } else if (data.kind === "labels") {
      rows = [clockHead(data.x_label).concat(["cluster"])];
      data.x.forEach(function (t, i) {
        if (inX(t)) rows.push(clockCells(data.x_label, t, data.frames[i]).concat([data.labels[i]]));
      });
    } else if (data.kind === "lines") {
      rows = [clockHead(data.x_label).concat(data.series.map(function (s) {
        return s.label + (data.unit ? " (" + data.unit + ")" : "");
      }))];
      data.x.forEach(function (t, i) {
        if (inX(t)) {
          rows.push(clockCells(data.x_label, t, data.frames[i])
            .concat(data.series.map(function (s) { return s.y[i]; })));
        }
      });
    } else if (data.kind === "bars") {
      rows = [["cluster", "frames", "fraction", "medoid_frame", "medoid_time_ns"]];
      data.clusters.forEach(function (k) {
        rows.push([k.cluster, k.frames, k.fraction, k.medoid_frame, k.medoid_time_ns]);
      });
    } else if (data.kind === "hbars") {
      // The value as its axis says it, with its unit or what it is a
      // share of; the error, where there is one, in the same.
      rows = [["quantity", data.x_label || "value", "standard error"]];
      (data.bars || []).forEach(function (b) { rows.push([b.label, b.value, b.error]); });
    } else if (data.kind === "dendrogram") {
      rows = [["link", "x1", "x2", "x3", "x4", "height1", "height2", "height3", "height4", "cluster"]];
      data.icoord.forEach(function (xs, i) {
        rows.push([i].concat(xs, data.dcoord[i], [data.groups[i]]));
      });
    } else if (data.kind === "projection") {
      var per = data.unit ? " (" + data.unit + ")" : "";
      rows = [["frame", data.t_label === "Frame" ? "frame_shown" : "time_ns",
               data.axes[0] + per, data.axes[1] + per]];
      data.x.forEach(function (v, i) {
        if (v != null && data.y[i] != null && inX(v) && inY(data.y[i])) {
          rows.push([data.frames[i], data.t[i], v, data.y[i]]);
        }
      });
    } else {
      // A map: each cell's centre and its value.
      var ex = data.edges_x, ey = data.edges_y;
      if (data.kind === "matrix") {
        ex = ey = edges(data.extent[0], data.extent[1], data.values.length);
      }
      var names = data.kind === "matrix" ? [data.axis_label, data.axis_label] : data.axes;
      rows = [[names[0], names[1], data.label + (data.unit ? " (" + data.unit + ")" : "")]];
      data.values.forEach(function (row, j) {
        var cy = (ey[j] + ey[j + 1]) / 2;
        if (!inY(cy)) return;
        row.forEach(function (v, i) {
          var cx = (ex[i] + ex[i + 1]) / 2;
          if (inX(cx)) rows.push([cx, cy, v]);
        });
      });
    }
    return rows;
  }

  /* The plots on the simulation's clock, plotted again as the page's time
   * range changes. */
  var drawnOnTheClock = [];
  document.addEventListener("fmx:time-range", function () {
    var seen = [];
    drawnOnTheClock = drawnOnTheClock.filter(function (d) {
      if (!d.host.isConnected || seen.indexOf(d.host) >= 0) return false;
      seen.push(d.host);
      return true;
    });
    drawnOnTheClock.slice().forEach(function (d) {
      if (!d.host.hidden) plot(d.host, d.data);
    });
  });

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
