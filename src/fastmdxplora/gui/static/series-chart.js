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

  function token(name, fallback) {
    var value = "";
    try {
      value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    } catch (e) { /* no styles yet */ }
    return value || fallback;
  }

  function colours() {
    return {
      line: token("--accent-cyan", "#63e6ff"),
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
      parts.push("mean " + withUnit(mean.value, data.unit)
        + (mean.error != null ? " plus or minus " + format(mean.error) : ""));
    }
    return parts.join("; ");
  }

  function draw(host, data) {
    host.innerHTML = "";
    var width = Math.max(260, Math.round(host.clientWidth || 440));
    var c = colours();
    var plot = {
      left: MARGIN.left, top: MARGIN.top,
      right: width - MARGIN.right, bottom: HEIGHT - MARGIN.bottom,
    };
    var xs = data.x;
    var ys = data.y;
    var xLow = xs[0];
    var xHigh = xs[xs.length - 1];
    if (xHigh === xLow) xHigh = xLow + 1;
    var yLow = Math.min.apply(null, ys);
    var yHigh = Math.max.apply(null, ys);
    var mean = data.mean || null;
    if (mean && mean.value != null) {
      var reach = mean.error != null ? mean.error : 0;
      yLow = Math.min(yLow, mean.value - reach);
      yHigh = Math.max(yHigh, mean.value + reach);
    }
    if (data.kind === "residue" || yLow >= 0) yLow = Math.min(0, yLow);
    var pad = (yHigh - yLow) * 0.06 || Math.abs(yHigh) * 0.1 || 1;
    yHigh += pad;
    if (yLow < 0) yLow -= pad;

    function px(x) { return plot.left + (x - xLow) / (xHigh - xLow) * (plot.right - plot.left); }
    function py(y) { return plot.bottom - (y - yLow) / (yHigh - yLow) * (plot.bottom - plot.top); }

    var svg = el("svg", {
      width: width, height: HEIGHT, viewBox: "0 0 " + width + " " + HEIGHT,
      role: "img", "aria-label": describe(data), tabindex: "0", class: "series-svg",
    }, host);

    // What the analysis left out, first, so the line is drawn over it.
    if (mean && mean.from_x != null && mean.discard > 0) {
      var cut = px(mean.from_x);
      el("rect", {
        x: plot.left, y: plot.top, width: Math.max(0, cut - plot.left),
        height: plot.bottom - plot.top, fill: c.axis, "fill-opacity": 0.12,
        class: "series-excluded",
      }, svg);
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
        ? (data.labels[Math.round(v) - 1] || "") : xText(v);
    });
    el("line", { x1: plot.left, x2: plot.right, y1: plot.bottom, y2: plot.bottom, stroke: c.axis }, svg);
    var xTitle = el("text", {
      x: (plot.left + plot.right) / 2, y: HEIGHT - 6, fill: c.text, "font-size": 11,
      "text-anchor": "middle",
    }, svg);
    xTitle.textContent = data.x_label;
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
    }, svg);
    if (data.kind === "residue" && xs.length <= 400) {
      xs.forEach(function (x, i) {
        el("circle", { cx: px(x), cy: py(ys[i]), r: 2.2, fill: c.line }, svg);
      });
    }

    // The mean over the frames kept, with its error where there is one.
    if (mean && mean.value != null) {
      var from = px(mean.from_x != null ? mean.from_x : xLow);
      if (mean.error != null) {
        el("rect", {
          x: from, y: py(mean.value + mean.error), width: plot.right - from,
          height: Math.max(1, py(mean.value - mean.error) - py(mean.value + mean.error)),
          fill: c.mean, "fill-opacity": 0.16, class: "series-error",
        }, svg);
      }
      el("line", {
        x1: from, x2: plot.right, y1: py(mean.value), y2: py(mean.value),
        stroke: mean.not_a_measurement ? c.axis : c.mean, "stroke-width": 1.4,
        "stroke-dasharray": "5 4", class: "series-mean",
      }, svg);
    }

    var cursor = el("line", {
      y1: plot.top, y2: plot.bottom, stroke: c.axis, "stroke-width": 1, visibility: "hidden",
    }, svg);
    var dot = el("circle", { r: 4, fill: c.line, stroke: c.shade, "stroke-width": 1.5,
                             visibility: "hidden" }, svg);
    var tip = document.createElement("div");
    tip.className = "series-tip";
    tip.hidden = true;
    host.appendChild(tip);

    var at = -1;
    function show(index) {
      if (index < 0 || index >= xs.length) return;
      at = index;
      var x = px(xs[index]);
      var y = py(ys[index]);
      cursor.setAttribute("x1", x); cursor.setAttribute("x2", x);
      cursor.setAttribute("visibility", "visible");
      dot.setAttribute("cx", x); dot.setAttribute("cy", y);
      dot.setAttribute("visibility", "visible");
      var where = data.kind === "residue"
        ? "residue " + data.labels[index]
        : (data.x_label === "Frame" ? "frame " + data.frames[index]
          : format(xs[index]) + " ns · frame " + data.frames[index]);
      var action = data.kind === "residue" ? "show it in the structure"
        : (data.linked ? "open this frame" : "");
      tip.innerHTML = "";
      var value = document.createElement("strong");
      value.textContent = withUnit(ys[index], data.unit);
      tip.appendChild(value);
      tip.appendChild(document.createTextNode(" · " + where));
      if (action) {
        var hint = document.createElement("span");
        hint.className = "series-tip-hint";
        hint.textContent = "Click to " + action;
        tip.appendChild(hint);
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
      var value = xLow + (x - plot.left) / (plot.right - plot.left) * (xHigh - xLow);
      return nearest(xs, value);
    }
    svg.addEventListener("mousemove", function (event) { show(indexAt(event)); });
    svg.addEventListener("mouseleave", hide);
    svg.addEventListener("blur", hide);
    svg.addEventListener("focus", function () { show(at >= 0 ? at : 0); });
    svg.addEventListener("click", function (event) { open(data, indexAt(event)); });
    svg.addEventListener("keydown", function (event) {
      var step = event.shiftKey ? Math.max(1, Math.round(xs.length / 20)) : 1;
      if (event.key === "ArrowRight") show(Math.min(xs.length - 1, (at < 0 ? 0 : at) + step));
      else if (event.key === "ArrowLeft") show(Math.max(0, (at < 0 ? 0 : at) - step));
      else if (event.key === "Home") show(0);
      else if (event.key === "End") show(xs.length - 1);
      else if ((event.key === "Enter" || event.key === " ") && at >= 0) open(data, at);
      else return;
      event.preventDefault();
    });
  }

  /* The frame of a point, as the viewer numbers what it plays: the viewer
   * plays a subset of the trajectory's frames (at most a few hundred), so
   * the nearest of those. */
  function playedIndex(frame) {
    var frames = playback && Array.isArray(playback.frame_indices) ? playback.frame_indices : [];
    if (!frames.length) return null;
    var best = 0;
    for (var i = 1; i < frames.length; i += 1) {
      if (Math.abs(frames[i] - frame) < Math.abs(frames[best] - frame)) best = i;
    }
    return best;
  }

  function open(data, index) {
    if (index < 0 || !window.FastMDXDashboard) return;
    if (data.kind === "residue") {
      window.FastMDXDashboard.navigate("viewer");
      window.dispatchEvent(new CustomEvent("dashboard:residue-focus",
        { detail: data.residues[index] }));
      return;
    }
    if (!data.linked) return;
    var frame = data.frames[index];
    var go = function () {
      var played = playedIndex(frame);
      if (played == null) return;
      window.FastMDXDashboard.navigate("viewer");
      window.dispatchEvent(new CustomEvent("dashboard:trajectory-seek",
        { detail: { frame: played, trajectoryFrame: frame } }));
    };
    if (playback) { go(); return; }
    fetch("/api/frames-info", { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (payload) {
        // Frames of the trajectory only when the viewer plays the
        // trajectory; during a run it plays snapshots, numbered otherwise.
        playback = payload && payload.playback_available
          && payload.source_kind === "production-dcd" ? payload : null;
        if (playback) go(); else window.FastMDXDashboard.navigate("viewer");
      })
      .catch(function () { /* the viewer says why when opened */ });
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
        if (!data || !data.ok || !data.y || data.y.length < 2) {
          frame.setAttribute("data-series-ready", "none");
          return;
        }
        var chart = document.createElement("div");
        chart.className = "series-chart";
        chart.setAttribute("data-analysis", name);
        frame.parentNode.insertBefore(chart, frame);
        var card = frame.closest(".analysis-card");
        var toggle = card && card.querySelector("[data-series-toggle]");
        function mode(chartShown) {
          chart.hidden = !chartShown;
          frame.hidden = chartShown;
          if (toggle) toggle.textContent = chartShown ? "Show the figure" : "Show the chart";
          if (chartShown) draw(chart, data);
        }
        if (toggle) {
          toggle.hidden = false;
          toggle.addEventListener("click", function (event) {
            event.preventDefault();
            mode(chart.hidden);
          });
        }
        mode(true);
        frame.setAttribute("data-series-ready", "drawn");
        if (window.ResizeObserver) {
          var width = chart.clientWidth;
          new ResizeObserver(function () {
            if (!chart.hidden && Math.abs(chart.clientWidth - width) > 4) {
              width = chart.clientWidth;
              draw(chart, data);
            }
          }).observe(chart);
        }
      });
    });
  }

  /* Drawn again in the new scheme's colours. */
  document.addEventListener("fmx:theme", function () {
    Array.prototype.forEach.call(document.querySelectorAll(".series-chart"), function (chart) {
      var name = chart.getAttribute("data-analysis");
      if (chart.hidden || !cache[name]) return;
      cache[name].then(function (data) { if (data && data.ok) draw(chart, data); });
    });
  });
  /* A new run, or new results, may have new numbers. */
  window.addEventListener("dashboard:run-changed", function () { cache = {}; playback = null; });

  window.FastMDXSeries = { hydrate: hydrate, ticks: ticks, forget: function () { cache = {}; playback = null; } };
}());
