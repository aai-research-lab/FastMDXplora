/* An analysis's series, drawn so it can be read and followed to the frame.
 *
 * The Analysis page showed each analysis as the figure it wrote: a picture
 * of a line, from which neither a value nor the frame it came from could be
 * taken. Here the same numbers (from /api/series) are drawn with what the
 * analysis determined: the frames it left out as equilibration, the mean of
 * the rest, and its error where the run supports one, or a note where it
 * does not. Pointing at the line gives the value, the time and the frame;
 * choosing a point opens that frame in the viewer, and a residue of a
 * profile is shown in the structure.
 *
 * The figure the analysis drew stays one click away: it is the one drawn at
 * publication settings, and the one to download.
 */
(function () {
  "use strict";

  var NS = "http://www.w3.org/2000/svg";
  var HEIGHT = 260;
  var MARGIN = { top: 16, right: 28, bottom: 38, left: 64 };
  var cache = {};
  var playback = null;
  var ranges = Object.create(null);

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
      mean: token("--accent-orange", "#ffb86b"),
      grid: token("--border-subtle", "rgba(255,255,255,0.07)"),
      axis: token("--text-muted", "#85858f"),
      text: token("--text-secondary", "#b5b5bb"),
      shade: token("--background-soft", "#151518"),
    };
  }

  function el(name, attrs, parent) {
    var node = document.createElementNS(NS, name);
    Object.keys(attrs || {}).forEach(function (key) { node.setAttribute(key, attrs[key]); });
    if (parent) parent.appendChild(node);
    return node;
  }

  /* Round ticks: 1, 2 or 5 times a power of ten, about five of them. */
  function ticks(low, high, count) {
    if (!(high > low)) return [low];
    var span = high - low;
    var step = Math.pow(10, Math.floor(Math.log10(span / count)));
    var err = (count * step) / span;
    if (err <= 0.15) step *= 10;
    else if (err <= 0.35) step *= 5;
    else if (err <= 0.75) step *= 2;
    var out = [];
    for (var v = Math.ceil(low / step) * step; v <= high + step * 1e-9; v += step) {
      out.push(Math.abs(v) < step * 1e-9 ? 0 : v);
    }
    return out;
  }

  /* A tick to the places its spacing needs: 0.02, 0.04, not 0.0200. */
  function tickText(values) {
    var step = values.length > 1 ? Math.abs(values[1] - values[0]) : Math.abs(values[0]) || 1;
    var magnitude = Math.max.apply(null, values.map(Math.abs));
    if (magnitude >= 1e5 || (magnitude > 0 && magnitude < 1e-3)) {
      return function (v) { return v === 0 ? "0" : v.toExponential(1); };
    }
    var places = Math.max(0, Math.min(6, -Math.floor(Math.log10(step) + 1e-9)));
    return function (v) { return v.toFixed(places); };
  }

  /* A series on the simulation's clock in the unit its span reads in:
   * picoseconds under a nanosecond, as the Overview's charts say it
   * (charts.js), so a 20 ps run is not plotted from 0.005 to 0.02 ns. */
  function timeScale(xs, label) {
    if (label !== "Time (ns)") return { factor: 1, label: label, unit: "" };
    var most = 0;
    (xs || []).forEach(function (x) { if (isFinite(x)) most = Math.max(most, Math.abs(x)); });
    return most < 1 ? { factor: 1000, label: "Time (ps)", unit: "ps" }
      : { factor: 1, label: label, unit: "ns" };
  }

  /* A frame's time in the unit the Viewer's ruler reads its frames in
   * (`timeScale`), with places enough to tell one frame from the next: a
   * 40 ps run read "0.000 ns" for its first two frames, under a ruler in ps. */
  function frameTimeSaid(t, times) {
    if (t == null || !isFinite(t)) return "\u2014";
    var scale = timeScale(times, "Time (ns)");
    var finite = (times || []).filter(function (x) { return isFinite(x); });
    var step = finite.length > 1
      ? Math.abs(finite[finite.length - 1] - finite[0]) / (finite.length - 1) * scale.factor : 0;
    var places = step > 0 ? Math.max(0, Math.min(6, Math.ceil(-Math.log10(step) + 1e-9))) : 3;
    if (scale.unit === "ns") places = Math.max(places, 3);
    return (t * scale.factor).toFixed(places) + " " + scale.unit;
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

  function withUnit(value, unit) {
    return format(value) + (unit ? " " + unit : "");
  }

  /* The index of the point nearest x, over x sorted ascending. */
  function nearest(xs, x) {
    var lo = 0;
    var hi = xs.length - 1;
    while (hi - lo > 1) {
      var mid = (lo + hi) >> 1;
      if (xs[mid] < x) lo = mid; else hi = mid;
    }
    return Math.abs(xs[lo] - x) <= Math.abs(xs[hi] - x) ? lo : hi;
  }

  function describe(data) {
    var parts = [data.label + ", " + data.y.length + " points"];
    var mean = data.mean;
    if (mean && mean.value != null) {
      parts.push("full-analysis mean " + withUnit(mean.value, data.unit)
        + (mean.error != null ? " plus or minus " + format(mean.error) : "")
        + (mean.noted ? ", " + mean.noted : ""));
    }
    return parts.join("; ");
  }

  /* What a chart shows of its x: the page's time range on its clock, or
   * the part it was zoomed to; all of it where neither. In the data's own
   * units. */
  function viewOf(host, data) {
    var tools = window.FastMDXPlotTools;
    if (data.kind !== "residue" && tools) {
      var shared = tools.range(data.x_label);
      if (shared) return [shared.from, shared.to];
    }
    return host._view || null;
  }

  /* How long a click waits for a second one, as a double click. */
  var DOUBLE_CLICK_MS = 280;

  function draw(host, data) {
    host.innerHTML = "";
    var width = Math.max(260, Math.round(host.clientWidth || 440));
    var c = colours();
    var plot = {
      left: MARGIN.left, top: MARGIN.top,
      right: width - MARGIN.right, bottom: HEIGHT - MARGIN.bottom,
    };
    var view = viewOf(host, data);
    host.dataset.view = view ? (host._view === view ? "zoomed" : "range") : "";
    // Said on the chart too, which holds this plot under its range form.
    var chart = host.closest ? host.closest(".series-chart") : null;
    if (chart && chart !== host) chart.dataset.view = host.dataset.view;
    var clock = data.kind === "residue" ? { factor: 1, label: data.x_label, unit: "" }
      : timeScale(view || data.x, data.x_label);
    var xs = clock.factor === 1 ? data.x : data.x.map(function (x) { return x * clock.factor; });
    var ys = data.y;
    var xLow = view ? view[0] * clock.factor : xs[0];
    var xHigh = view ? view[1] * clock.factor : xs[xs.length - 1];
    if (xHigh === xLow) xHigh = xLow + 1;
    // The values in view set the height, so a range zoomed into is read.
    var seen = ys.filter(function (y, i) { return xs[i] >= xLow && xs[i] <= xHigh; });
    if (!seen.length) seen = ys;
    var yLow = Math.min.apply(null, seen);
    var yHigh = Math.max.apply(null, seen);
    var mean = data.mean || null;
    if (mean && mean.value != null) {
      var reach = mean.error != null ? mean.error : 0;
      yLow = Math.min(yLow, mean.value - reach);
      yHigh = Math.max(yHigh, mean.value + reach);
    }
    // Zero on the axis where the values reach towards it (an RMSD, a
    // profile), not under a series that varies by a few percent about a
    // large value: Total SASA of 91 nm2 varying by 0.3 read as a flat line.
    if (data.kind === "residue" || (yLow >= 0 && yLow <= 0.5 * yHigh)) yLow = Math.min(0, yLow);
    var pad = (yHigh - yLow) * 0.06 || Math.abs(yHigh) * 0.1 || 1;
    yHigh += pad;
    if (yLow < 0) yLow -= pad;
    if (data.y_bounds) {
      yLow = data.y_bounds[0];
      yHigh = data.y_bounds[1];
    }

    function px(x) { return plot.left + (x - xLow) / (xHigh - xLow) * (plot.right - plot.left); }
    function py(y) { return plot.bottom - (y - yLow) / (yHigh - yLow) * (plot.bottom - plot.top); }

    var svg = el("svg", {
      width: width, height: HEIGHT, viewBox: "0 0 " + width + " " + HEIGHT,
      role: "img", "aria-label": describe(data), tabindex: "0", class: "series-svg",
    }, host);
    // What is plotted stays inside the axes when zoomed.
    var clipId = "series-clip-" + Math.random().toString(36).slice(2, 9);
    el("rect", { x: plot.left, y: plot.top, width: plot.right - plot.left,
                 height: plot.bottom - plot.top },
       el("clipPath", { id: clipId }, el("defs", {}, svg)));
    var layer = el("g", { "clip-path": "url(#" + clipId + ")" }, svg);

    // What the analysis left out, first, so the line is drawn over it.
    if (mean && mean.from_x != null && mean.discard > 0) {
      var cut = px(mean.from_x * clock.factor);
      el("rect", {
        x: plot.left, y: plot.top, width: Math.max(0, cut - plot.left),
        height: plot.bottom - plot.top, fill: c.axis, "fill-opacity": 0.12,
        class: "series-excluded",
      }, layer);
      var note = el("text", {
        x: plot.left + 6, y: plot.top + 12, fill: c.axis, "font-size": 10,
        class: "series-excluded-label",
      }, svg);
      note.textContent = "equilibration, not averaged";
    }

    var yTicks = ticks(yLow, yHigh, 4);
    var yText = tickText(yTicks);
    yTicks.forEach(function (v) {
      el("line", { x1: plot.left, x2: plot.right, y1: py(v), y2: py(v), stroke: c.grid }, svg);
      var label = el("text", {
        x: plot.left - 8, y: py(v) + 3, fill: c.axis, "font-size": 10, "text-anchor": "end",
      }, svg);
      label.textContent = yText(v);
    });
    var xTicks = data.kind === "residue"
      ? ticks(xLow, xHigh, Math.min(8, xs.length)).filter(function (v) { return v === Math.round(v); })
      : ticks(xLow, xHigh, 5);
    var xText = tickText(xTicks);
    xTicks.forEach(function (v) {
      if (v < xLow || v > xHigh) return;
      var label = el("text", {
        x: px(v), y: plot.bottom + 15, fill: c.axis, "font-size": 10, "text-anchor": "middle",
      }, svg);
      label.textContent = data.kind === "residue"
        ? (data.labels[xs.indexOf(v)] || "") : xText(v);
    });
    el("line", { x1: plot.left, x2: plot.right, y1: plot.bottom, y2: plot.bottom, stroke: c.axis }, svg);
    var xTitle = el("text", {
      x: (plot.left + plot.right) / 2, y: HEIGHT - 6, fill: c.text, "font-size": 11,
      "text-anchor": "middle",
    }, svg);
    xTitle.textContent = clock.label;
    var yTitle = el("text", {
      x: 11, y: (plot.top + plot.bottom) / 2, fill: c.text, "font-size": 11,
      "text-anchor": "middle",
      transform: "rotate(-90 11 " + ((plot.top + plot.bottom) / 2) + ")",
    }, svg);
    yTitle.textContent = data.label + (data.unit ? " (" + data.unit + ")" : "");

    var path = xs.map(function (x, i) {
      return (i ? "L" : "M") + px(x).toFixed(1) + " " + py(ys[i]).toFixed(1);
    }).join("");
    el("path", {
      d: path, fill: "none", stroke: c.line, "stroke-width": 1.5,
      "stroke-linejoin": "round", class: "series-line",
    }, layer);
    if (data.kind === "residue" && xs.length <= 400) {
      xs.forEach(function (x, i) {
        el("circle", { cx: px(x), cy: py(ys[i]), r: 2.2, fill: c.line }, layer);
      });
    }
    if (Array.isArray(data.reference)) {
      data.reference.forEach(function (value, i) {
        if (value == null || !isFinite(value)) return;
        el("rect", { x: px(xs[i]) - 3, y: py(value) - 3, width: 6, height: 6,
          fill: c.mean, class: "series-reference" }, layer);
      });
      // Under the mean's label, which is drawn at the same corner.
      var referenceLabel = el("text", { x: plot.right, y: plot.top + (mean && mean.value != null ? 24 : 12),
        fill: c.mean, "font-size": 10, "text-anchor": "end" }, svg);
      referenceLabel.textContent = data.reference.some(function (v) { return v != null; })
        ? "Squares: measured S²" : "";
    }

    // The mean over the frames kept, with its error where there is one.
    if (mean && mean.value != null) {
      // Inside the axes: a range ending before the part averaged leaves
      // nothing of the band to draw.
      var from = Math.max(plot.left, Math.min(plot.right,
        px(mean.from_x != null ? mean.from_x * clock.factor : xLow)));
      if (mean.error != null && from < plot.right) {
        el("rect", {
          x: from, y: py(mean.value + mean.error), width: plot.right - from,
          height: Math.max(1, py(mean.value - mean.error) - py(mean.value + mean.error)),
          fill: c.mean, "fill-opacity": 0.16, class: "series-error",
        }, layer);
      }
      if (from < plot.right) {
        el("line", {
          x1: from, x2: plot.right, y1: py(mean.value), y2: py(mean.value),
          stroke: mean.not_a_measurement ? c.axis : c.mean, "stroke-width": 1.4,
          "stroke-dasharray": "5 4", class: "series-mean",
        }, layer);
        var meanLabel = el("text", {
          x: plot.right, y: plot.top + 12, fill: c.mean, "font-size": 10,
          "text-anchor": "end", class: "series-mean-label",
        }, svg);
        meanLabel.textContent = "Full-analysis mean " + withUnit(mean.value, data.unit);
      }
    }

    var cursor = el("line", {
      y1: plot.top, y2: plot.bottom, stroke: c.axis, "stroke-width": 1, visibility: "hidden",
      "pointer-events": "none",
    }, svg);
    var dot = el("circle", { r: 4, fill: c.line, stroke: c.shade, "stroke-width": 1.5,
                             visibility: "hidden", "pointer-events": "none" }, svg);
    var tip = document.createElement("div");
    tip.className = "series-tip";
    tip.hidden = true;
    host.appendChild(tip);

    // The point the keyboard (or the pointer) is on, kept on the host by
    // its x in the data's own units: a zoom, a move, a time range or the
    // From/To range draws the chart again, and the keyboard goes on from
    // the same point, not from the first. Out of view, it gives way to the
    // shown point nearest it; Enter opens only a point shown.
    function shownAt(i) {
      return i >= 0 && i < xs.length && xs[i] >= xLow - 1e-9 && xs[i] <= xHigh + 1e-9;
    }
    function firstShown(direction) {
      for (var i = direction > 0 ? 0 : xs.length - 1; i >= 0 && i < xs.length; i += direction) {
        if (shownAt(i)) return i;
      }
      return -1;
    }
    function nearestShown(value) {
      if (value < xLow) return firstShown(1);
      if (value > xHigh) return firstShown(-1);
      var i = nearest(xs, value);
      return shownAt(i) ? i : firstShown(1);
    }
    var at = -1;
    if (host._keyAt != null) {
      at = data.x.indexOf(host._keyAt);
      if (!shownAt(at)) at = nearestShown(host._keyAt * clock.factor);
    }
    function show(index) {
      if (index < 0 || index >= xs.length) return;
      at = index;
      host._keyAt = data.x[index];
      var x = px(xs[index]);
      var y = py(ys[index]);
      cursor.setAttribute("x1", x); cursor.setAttribute("x2", x);
      cursor.setAttribute("visibility", "visible");
      dot.setAttribute("cx", x); dot.setAttribute("cy", y);
      dot.setAttribute("visibility", "visible");
      var entity = Array.isArray(data.entities) ? data.entities[index] : null;
      var where = data.kind === "residue" || data.kind === "atom"
        ? data.labels[index]
        : (data.x_label === "Frame" ? "frame " + data.frames[index]
          : format(xs[index]) + " " + (clock.unit || "ns") + " · frame " + data.frames[index]);
      var action = (data.kind === "residue" || data.kind === "atom") && entity
        ? "select it in the structure"
        : (data.linked ? "open this frame" : "");
      tip.innerHTML = "";
      var value = document.createElement("strong");
      value.textContent = withUnit(ys[index], data.unit);
      tip.appendChild(value);
      tip.appendChild(document.createTextNode(" · " + where));
      if (data.reference && data.reference[index] != null) {
        tip.appendChild(document.createTextNode(" · measured " + format(data.reference[index])));
      }
      if (action) {
        var hint = document.createElement("span");
        hint.className = "series-tip-hint";
        hint.textContent = "Click to " + action;
        tip.appendChild(hint);
      } else if ((data.kind === "residue" || data.kind === "atom") && !entity) {
        var unavailable = document.createElement("span");
        unavailable.className = "series-tip-hint";
        unavailable.textContent = "Selection unavailable: this row has no unique topology match.";
        tip.appendChild(unavailable);
      }
      tip.hidden = false;
      var left = Math.min(Math.max(x + 12, 4), width - tip.offsetWidth - 4);
      if (x + 12 + tip.offsetWidth > width) left = Math.max(4, x - tip.offsetWidth - 12);
      tip.style.left = left + "px";
      tip.style.top = Math.max(4, y - tip.offsetHeight - 10) + "px";
      host.setAttribute("data-at", String(index));
    }
    function hide() {
      cursor.setAttribute("visibility", "hidden");
      dot.setAttribute("visibility", "hidden");
      tip.hidden = true;
    }
    function indexAt(event) {
      var box = svg.getBoundingClientRect();
      var x = (event.clientX - box.left) * (width / box.width);
      var y = (event.clientY - box.top) * (HEIGHT / box.height);
      if (x < plot.left || x > plot.right || y < plot.top || y > plot.bottom) return -1;
      var value = xLow + (x - plot.left) / (plot.right - plot.left) * (xHigh - xLow);
      return nearest(xs, value);
    }
    var tools = window.FastMDXPlotTools;
    if (tools) {
      var timed = data.kind !== "residue";
      tools.attach(host, {
        svg: svg, width: width, height: HEIGHT, plot: plot, zoom: "x", ownArrows: true,
        // A key zooms in to four points (three steps between them) at the least.
        full: { x: [Math.min.apply(null, data.x), Math.max.apply(null, data.x)] },
        least: { x: data.x.length > 1
          ? 3 * Math.abs(data.x[data.x.length - 1] - data.x[0]) / (data.x.length - 1) : 0 },
        shared: timed ? data.x_label : "",
        zoomed: !!view,
        name: data.analysis + (view ? "_range" : ""),
        invert: function (x, y) {
          return { x: (xLow + (x - plot.left) / (plot.right - plot.left) * (xHigh - xLow)) / clock.factor,
                   y: yLow + (plot.bottom - y) / (plot.bottom - plot.top) * (yHigh - yLow) };
        },
        onView: function (v) { host._view = v.x; draw(host, data); },
        onReset: function () { host._view = null; draw(host, data); },
        csv: function () {
          var value = data.analysis + (data.unit ? " (" + data.unit + ")" : "");
          // On frame numbers where the run kept no clock: the frame alone.
          var clocked = timed && data.x_label !== "Frame";
          var head = clocked ? ["time_ns", "frame", value] : [timed ? "frame" : "residue", value];
          var rows = [head];
          data.x.forEach(function (x, i) {
            var shown = xs[i] >= xLow - 1e-12 && xs[i] <= xHigh + 1e-12;
            if (!shown) return;
            rows.push(clocked ? [x, data.frames ? data.frames[i] : "", data.y[i]]
                      : timed ? [data.frames ? data.frames[i] : x, data.y[i]]
                      : [data.labels ? data.labels[i] : x, data.y[i]]);
          });
          return rows;
        },
      });
    }
    svg.addEventListener("mousemove", function (event) {
      var index = indexAt(event);
      if (index < 0) hide(); else show(index);
    });
    svg.addEventListener("mouseleave", hide);
    svg.addEventListener("blur", hide);
    svg.addEventListener("focus", function () { show(at >= 0 ? at : firstShown(1)); });
    // A click opens the point; the first click of a double click (show
    // all, plot-tools.js) does not: it waits to see if a second follows.
    var pending = null;
    svg.addEventListener("click", function (event) {
      if (event.detail > 1) return;
      var index = indexAt(event);
      clearTimeout(pending);
      pending = setTimeout(function () { open(data, index); }, DOUBLE_CLICK_MS);
    });
    svg.addEventListener("dblclick", function () { clearTimeout(pending); });
    svg.addEventListener("keydown", function (event) {
      var step = event.shiftKey ? Math.max(1, Math.round(xs.length / 20)) : 1;
      // Along the points shown: zoomed in, the cursor stays in the view.
      var first = firstShown(1), last = firstShown(-1);
      if (event.key === "ArrowRight") show(at < 0 ? first : Math.min(last, at + step));
      else if (event.key === "ArrowLeft") show(at < 0 ? first : Math.max(first, at - step));
      else if (event.key === "Home") show(first);
      else if (event.key === "End") show(last);
      else if ((event.key === "Enter" || event.key === " ") && shownAt(at)) open(data, at);
      else return;
      event.preventDefault();
    });
  }

  function open(data, index) {
    if (index < 0 || !window.FastMDXDashboard) return;
    if (data.kind === "residue" || data.kind === "atom") {
      var entity = Array.isArray(data.entities) ? data.entities[index] : null;
      if (!entity) return;
      window.FastMDXDashboard.focusEntity(entity);
      return;
    }
    if (!data.linked) return;
    var frame = data.frames[index];
    var go = function (source) {
      window.FastMDXDashboard.navigate("viewer");
      window.dispatchEvent(new CustomEvent("dashboard:trajectory-seek",
        { detail: { trajectoryFrame: frame, sourceSignature: source.source_signature } }));
    };
    if (playback && playback.playback_available && playback.source_kind === "production-dcd") {
      go(playback);
      return;
    }
    fetch("/api/frames-info", { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (payload) {
        // Frames of the trajectory only when the viewer plays the
        // trajectory; during a run it plays snapshots, numbered otherwise.
        playback = payload && payload.playback_available
          && payload.source_kind === "production-dcd" ? payload : null;
        if (playback) go(playback); else window.FastMDXDashboard.navigate("viewer");
      })
      .catch(function () { /* the viewer says why when opened */ });
  }

  function rangeBounds(data) {
    var values = (data.x || []).filter(function (value) { return isFinite(value); });
    return values.length ? [Math.min.apply(null, values), Math.max.apply(null, values)] : null;
  }

  function rangeOf(name, data) {
    var bounds = rangeBounds(data);
    return ranges[name] || (bounds ? { from: bounds[0], to: bounds[1] } : null);
  }

  /* Keep each selected point's source frame or residue identity attached.
   * The chart gets a view of the arrays; the analysis data stays untouched. */
  function withinRange(data, range) {
    if (!range) return null;
    var indices = [];
    (data.x || []).forEach(function (value, index) {
      if (isFinite(value) && value >= range.from && value <= range.to) indices.push(index);
    });
    if (!indices.length) return null;
    var shown = Object.assign({}, data);
    ["x", "y", "frames", "labels", "entities", "reference"].forEach(function (key) {
      if (Array.isArray(data[key]) && data[key].length === data.x.length) {
        shown[key] = indices.map(function (index) { return data[key][index]; });
      }
    });
    return shown;
  }

  function rangeInput(form, field, labelText, bounds, value) {
    var label = document.createElement("label");
    label.className = "series-range-field";
    var said = document.createElement("span");
    said.textContent = labelText;
    var input = document.createElement("input");
    input.type = "number";
    input.step = "any";
    input.required = true;
    input.min = String(bounds[0]);
    input.max = String(bounds[1]);
    input.value = String(value);
    input.setAttribute("aria-label", labelText);
    input.dataset.range = field;
    label.append(said, input);
    form.appendChild(label);
    return input;
  }

  function rangeControls(chart, data, name) {
    var bounds = rangeBounds(data);
    if (!bounds) return null;
    var form = document.createElement("form");
    form.className = "series-range";
    form.noValidate = true;
    form.setAttribute("aria-label", "Limit " + data.label + " plot range");
    var profile = data.kind === "residue" || data.kind === "atom";
    var unit = data.x_label === "Time (ns)" ? "time (ns)"
      : profile ? "profile position" : "frame";
    var initial = rangeOf(name, data);
    var from = rangeInput(form, "from", "From " + unit, bounds, initial.from);
    var to = rangeInput(form, "to", "To " + unit, bounds, initial.to);
    var apply = document.createElement("button");
    apply.type = "submit";
    apply.className = "line-btn";
    apply.textContent = "Apply range";
    form.appendChild(apply);
    var reset = document.createElement("button");
    reset.type = "button";
    reset.className = "line-btn";
    reset.textContent = "Reset";
    reset.setAttribute("aria-label", "Reset plot range");
    form.appendChild(reset);
    var status = document.createElement("span");
    status.className = "series-range-status";
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");
    form.appendChild(status);

    function render() {
      var current = chart.__seriesData;
      var shown = withinRange(current, rangeOf(name, current));
      if (!shown) {
        chart.__seriesPlot.replaceChildren();
        var empty = document.createElement("p");
        empty.className = "series-range-empty";
        empty.textContent = "No points fall inside this range. Choose a wider range or reset it.";
        chart.__seriesPlot.appendChild(empty);
        return;
      }
      draw(chart.__seriesPlot, shown);
    }

    function mark(input, invalid, message) {
      if (invalid) input.setAttribute("aria-invalid", "true");
      else input.removeAttribute("aria-invalid");
      status.textContent = message;
    }

    form.addEventListener("submit", function (event) {
      event.preventDefault();
      var low = from.value.trim() === "" ? NaN : Number(from.value);
      var high = to.value.trim() === "" ? NaN : Number(to.value);
      if (!isFinite(low)) return mark(from, true, "Enter a numeric start value.");
      if (!isFinite(high)) return mark(to, true, "Enter a numeric end value.");
      if (low < bounds[0] || low > bounds[1]) {
        return mark(from, true, "Start must be within the available range.");
      }
      if (high < bounds[0] || high > bounds[1]) {
        return mark(to, true, "End must be within the available range.");
      }
      if (low > high) return mark(to, true, "End must be at or after start.");
      ranges[name] = { from: low, to: high };
      mark(from, false, "");
      mark(to, false, "");
      render();
      status.textContent = withinRange(chart.__seriesData, ranges[name])
        ? "Showing the selected range." : "No plotted points fall inside this range.";
    });
    reset.addEventListener("click", function () {
      delete ranges[name];
      from.value = String(bounds[0]);
      to.value = String(bounds[1]);
      mark(from, false, "");
      mark(to, false, "");
      render();
    });
    return {
      form: form, from: from, to: to, render: render,
      updateData: function (fresh) {
        bounds = rangeBounds(fresh);
        from.min = String(bounds[0]);
        from.max = String(bounds[1]);
        to.min = String(bounds[0]);
        to.max = String(bounds[1]);
        from.value = String(bounds[0]);
        to.value = String(bounds[1]);
        mark(from, false, "");
        mark(to, false, "");
      },
    };
  }

  function load(name) {
    if (!cache[name]) {
      cache[name] = fetch("/api/series?analysis=" + encodeURIComponent(name), { cache: "no-store" })
        .then(function (r) { return r.json(); })
        .catch(function () { return { ok: false }; });
    }
    return cache[name];
  }

  /* Every card that names a series gets its chart, and a way back to the
   * figure. A card whose series cannot be drawn keeps its figure. */
  function hydrate(root) {
    Array.prototype.forEach.call((root || document).querySelectorAll("[data-series]"), function (frame) {
      if (frame.getAttribute("data-series-ready")) return;
      frame.setAttribute("data-series-ready", "pending");
      var name = frame.getAttribute("data-series");
      load(name).then(function (data) {
        if (!data || !data.ok || !data.y || data.y.length < 1) {
          frame.setAttribute("data-series-ready", "none");
          return;
        }
        var chart = document.createElement("div");
        chart.className = "series-chart";
        chart.setAttribute("data-analysis", name);
        chart.__seriesData = data;
        var controls = rangeControls(chart, data, name);
        var plot = document.createElement("div");
        plot.className = "series-plot";
        chart.__seriesPlot = plot;
        chart.__seriesRender = controls ? controls.render : function () { draw(plot, chart.__seriesData); };
        if (controls) chart.appendChild(controls.form);
        chart.appendChild(plot);
        var notice = document.createElement("p");
        notice.className = "series-notice";
        notice.textContent = data.notice || "";
        notice.hidden = !data.notice;
        chart.appendChild(notice);
        chart.__seriesSetData = function (fresh) {
          chart.__seriesData = fresh;
          notice.textContent = fresh.notice || "";
          notice.hidden = !fresh.notice;
          if (controls) controls.updateData(fresh);
          chart.__seriesRender();
        };
        frame.parentNode.insertBefore(chart, frame);
        var card = frame.closest(".analysis-card");
        var toggle = card && card.querySelector("[data-series-toggle]");
        function mode(chartShown) {
          chart.hidden = !chartShown;
          frame.hidden = chartShown;
          if (toggle) toggle.textContent = chartShown ? "Show the figure" : "Show the chart";
          if (chartShown) chart.__seriesRender();
        }
        if (toggle) {
          toggle.hidden = false;
          toggle.addEventListener("click", function (event) {
            event.preventDefault();
            mode(chart.hidden);
          });
        }
        mode(true);
        drawn.push({ chart: chart, data: data });
        frame.setAttribute("data-series-ready", "drawn");
        if (window.ResizeObserver) {
          var width = chart.clientWidth;
          new ResizeObserver(function () {
            if (!chart.hidden && Math.abs(chart.clientWidth - width) > 4) {
              width = chart.clientWidth;
              chart.__seriesRender();
            }
          }).observe(chart);
        }
      });
    });
  }

  /* The charts drawn, to draw again when the page's time range changes. */
  var drawn = [];
  document.addEventListener("fmx:time-range", function () {
    drawn = drawn.filter(function (d) { return d.chart.isConnected; });
    drawn.forEach(function (d) {
      // Into the chart's plot, under its own From/To range, not over the
      // chart (its range form and notice are its own).
      if (!d.chart.hidden && d.data.kind !== "residue") d.chart.__seriesRender();
    });
  });

  /* Drawn again in the new scheme's colours. */
  document.addEventListener("fmx:theme", function () {
    Array.prototype.forEach.call(document.querySelectorAll(".series-chart"), function (chart) {
      var name = chart.getAttribute("data-analysis");
      if (chart.hidden || !cache[name]) return;
      cache[name].then(function (data) { if (data && data.ok) chart.__seriesRender(); });
    });
  });
  /* A new run, or new results, may have new numbers. */
  window.addEventListener("dashboard:run-changed", function () {
    cache = {};
    playback = null;
    ranges = Object.create(null);
    Array.prototype.forEach.call(document.querySelectorAll(".series-chart"), function (chart) {
      var name = chart.getAttribute("data-analysis");
      load(name).then(function (data) {
        if (data && data.ok && data.y && data.y.length) {
          chart.__seriesSetData(data);
          return;
        }
        // The new study has no such series: its figure, not the last
        // study's numbers, is what the card shows.
        chart.hidden = true;
        var frame = chart.nextElementSibling;
        if (frame && frame.hasAttribute("data-series")) frame.hidden = false;
        var card = chart.closest(".analysis-card");
        var toggle = card && card.querySelector("[data-series-toggle]");
        if (toggle) toggle.hidden = true;
      });
    });
  });

  window.FastMDXSeries = {
    hydrate: hydrate, ticks: ticks, timeScale: timeScale, frameTimeSaid: frameTimeSaid,
    /* A series drawn into a host from its payload (as /api/series gives it). */
    draw: draw,
    forget: function () { cache = {}; playback = null; ranges = Object.create(null); },
    /* A frame of the trajectory opened in the viewer (figure-chart.js). */
    openFrame: function (frame) { open({ linked: true, frames: [frame] }, 0); },
  };
}());
