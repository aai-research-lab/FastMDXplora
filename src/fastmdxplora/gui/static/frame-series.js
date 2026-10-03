/* A series under the Viewer's transport, following the frame shown.
 *
 * The trajectory was played with a slider that said only a frame number and
 * a time: where the RMSD rose, or the radius of gyration fell, had to be
 * found on the Analysis page and carried back by hand. One of the study's
 * series over time (from /api/series) is plotted under the transport, with
 * the frames the analysis left out as equilibration and the mean of the
 * rest. A line marks the frame shown and moves as it plays; its value is
 * given beside it, and a click or a drag along the series shows that frame.
 *
 * Only the frames of the trajectory itself are followed: a series is of the
 * trajectory analysed, and while a run is going the Viewer plays snapshots,
 * numbered otherwise.
 */
(function () {
  "use strict";

  var NS = "http://www.w3.org/2000/svg";
  var HEIGHT = 76;
  var MARGIN = { top: 8, right: 10, bottom: 16, left: 48 };
  var CHOSEN = "fastmdx.frameSeries";

  var state = {
    list: null,       // [{analysis, label, unit}]
    listAsked: null,
    name: null,
    data: null,       // the series, as /api/series gives it
    playback: null,   // the frames played: frame_indices
    index: null,      // the played frame shown
    plot: null,       // how to place an x on the chart, once plotted
  };

  function byId(id) { return document.getElementById(id); }

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
      here: token("--text-primary", "#f2f2f4"),
    };
  }

  function el(name, attrs, parent) {
    var node = document.createElementNS(NS, name);
    Object.keys(attrs || {}).forEach(function (key) { node.setAttribute(key, attrs[key]); });
    if (parent) parent.appendChild(node);
    return node;
  }

  function format(value) {
    if (value == null || !isFinite(value)) return "\u2014";
    var magnitude = Math.abs(value);
    if (magnitude === 0) return "0";
    if (magnitude >= 1e5 || magnitude < 1e-3) return value.toExponential(2);
    if (magnitude >= 100) return value.toFixed(1);
    if (magnitude >= 1) return value.toFixed(3);
    return value.toPrecision(3);
  }

  function remembered() {
    try { return window.localStorage.getItem(CHOSEN); } catch (e) { return null; }
  }

  function remember(name) {
    try { window.localStorage.setItem(CHOSEN, name); } catch (e) { /* not kept */ }
  }

  /* The frames played are the trajectory's own, not a run's snapshots. */
  function ofTheTrajectory(payload) {
    return !!(payload && payload.playback_available && payload.source_kind === "production-dcd"
      && Array.isArray(payload.frame_indices) && payload.frame_indices.length > 1);
  }

  function row() { return byId("frame-series"); }

  function hide(reason) {
    var host = row();
    if (!host) return;
    host.hidden = true;
    host.setAttribute("data-state", reason || "none");
  }

  /* The trajectory frame of a played frame, and back. */
  function trajectoryFrame(index) {
    var frames = state.playback ? state.playback.frame_indices : [];
    return index != null && index >= 0 && index < frames.length ? frames[index] : null;
  }

  function playedIndex(frame) {
    var frames = state.playback ? state.playback.frame_indices : [];
    if (!frames.length) return null;
    var best = 0;
    for (var i = 1; i < frames.length; i += 1) {
      if (Math.abs(frames[i] - frame) < Math.abs(frames[best] - frame)) best = i;
    }
    return best;
  }

  /* The analysed point nearest a trajectory frame, over frames ascending. */
  function nearestPoint(frame) {
    var frames = state.data.frames;
    var lo = 0;
    var hi = frames.length - 1;
    while (hi - lo > 1) {
      var mid = (lo + hi) >> 1;
      if (frames[mid] < frame) lo = mid; else hi = mid;
    }
    return Math.abs(frames[lo] - frame) <= Math.abs(frames[hi] - frame) ? lo : hi;
  }

  /* The chart's axis is the series' own (time, or frame where no clock is
   * recorded); a trajectory frame is placed on it by the line through the
   * series' first and last points, which is how the loader numbered them. */
  function xOfFrame(frame) {
    var d = state.data;
    var n = d.frames.length;
    var span = d.frames[n - 1] - d.frames[0];
    if (!span) return d.x[0];
    return d.x[0] + (frame - d.frames[0]) * (d.x[n - 1] - d.x[0]) / span;
  }

  function frameOfX(x) {
    var d = state.data;
    var n = d.frames.length;
    var span = d.x[n - 1] - d.x[0];
    if (!span) return d.frames[0];
    return d.frames[0] + (x - d.x[0]) * (d.frames[n - 1] - d.frames[0]) / span;
  }

  function plot() {
    var host = byId("frame-series-chart");
    var d = state.data;
    if (!host || !d) return;
    host.replaceChildren();
    var width = Math.max(200, Math.round(host.clientWidth || 600));
    var c = colours();
    var plot = { left: MARGIN.left, top: MARGIN.top, right: width - MARGIN.right,
      bottom: HEIGHT - MARGIN.bottom };
    var xs = d.x;
    var ys = d.y;
    // The axis runs over the frames played, so the line under the slider
    // spans what the slider spans.
    var frames = state.playback.frame_indices;
    var xLow = Math.min(xs[0], xOfFrame(frames[0]));
    var xHigh = Math.max(xs[xs.length - 1], xOfFrame(frames[frames.length - 1]));
    if (xHigh === xLow) xHigh = xLow + 1;
    var yLow = Math.min.apply(null, ys);
    var yHigh = Math.max.apply(null, ys);
    var mean = d.mean && d.mean.value != null ? d.mean : null;
    if (mean) {
      yLow = Math.min(yLow, mean.value);
      yHigh = Math.max(yHigh, mean.value);
    }
    var pad = (yHigh - yLow) * 0.08 || Math.abs(yHigh) * 0.1 || 1;
    yLow -= pad;
    yHigh += pad;
    function px(x) { return plot.left + (x - xLow) / (xHigh - xLow) * (plot.right - plot.left); }
    function py(y) { return plot.bottom - (y - yLow) / (yHigh - yLow) * (plot.bottom - plot.top); }
    state.plot = { px: px, width: width, plot: plot, xLow: xLow, xHigh: xHigh, py: py };

    var svg = el("svg", {
      width: width, height: HEIGHT, viewBox: "0 0 " + width + " " + HEIGHT, role: "img",
      "aria-label": d.label + " over the trajectory, the frame shown marked",
      class: "frame-series-svg",
    }, host);
    if (mean && mean.from_x != null && mean.discard > 0) {
      el("rect", {
        x: plot.left, y: plot.top, width: Math.max(0, px(mean.from_x) - plot.left),
        height: plot.bottom - plot.top, fill: c.axis, "fill-opacity": 0.12,
        class: "frame-series-excluded",
      }, svg);
    }
    [yLow + pad, yHigh - pad].forEach(function (value) {
      el("line", { x1: plot.left, x2: plot.right, y1: py(value), y2: py(value),
        stroke: c.grid }, svg);
      var label = el("text", { x: plot.left - 6, y: py(value) + 3, fill: c.axis,
        "font-size": 10, "text-anchor": "end" }, svg);
      label.textContent = format(value);
    });
    // The two ends of the axis, in its units: the series is chosen by name
    // and unit, and its value is given beside it.
    var byFrame = d.x_label === "Frame";
    var from = el("text", { x: plot.left, y: HEIGHT - 3, fill: c.axis, "font-size": 10 }, svg);
    from.textContent = byFrame ? "frame " + Math.round(xLow) : format(xLow) + " ns";
    var to = el("text", { x: plot.right, y: HEIGHT - 3, fill: c.axis, "font-size": 10,
      "text-anchor": "end" }, svg);
    to.textContent = byFrame ? "frame " + Math.round(xHigh) : format(xHigh) + " ns";
    el("path", {
      d: xs.map(function (x, i) {
        return (i ? "L" : "M") + px(x).toFixed(1) + " " + py(ys[i]).toFixed(1);
      }).join(""),
      fill: "none", stroke: c.line, "stroke-width": 1.4, "stroke-linejoin": "round",
      class: "frame-series-line",
    }, svg);
    if (mean) {
      var start = px(mean.from_x != null ? mean.from_x : xs[0]);
      el("line", { x1: start, x2: px(xs[xs.length - 1]), y1: py(mean.value), y2: py(mean.value),
        stroke: mean.not_a_measurement ? c.axis : c.mean, "stroke-width": 1.2,
        "stroke-dasharray": "5 4", class: "frame-series-mean" }, svg);
    }
    el("line", { class: "frame-series-here", y1: plot.top, y2: plot.bottom, stroke: c.here,
      "stroke-width": 1.4, visibility: "hidden" }, svg);
    el("circle", { class: "frame-series-dot", r: 3.5, fill: c.line, stroke: c.here,
      "stroke-width": 1.2, visibility: "hidden" }, svg);

    // A click, or a drag, along the series shows that frame.
    var dragging = false;
    function seek(event) {
      var box = svg.getBoundingClientRect();
      var x = (event.clientX - box.left) * (width / box.width);
      var value = xLow + (x - plot.left) / (plot.right - plot.left) * (xHigh - xLow);
      var played = playedIndex(frameOfX(value));
      if (played == null || played === state.index) return;
      window.dispatchEvent(new CustomEvent("dashboard:trajectory-seek",
        { detail: { frame: played, trajectoryFrame: trajectoryFrame(played) } }));
    }
    svg.addEventListener("pointerdown", function (event) {
      dragging = true;
      if (svg.setPointerCapture) svg.setPointerCapture(event.pointerId);
      seek(event);
    });
    svg.addEventListener("pointermove", function (event) { if (dragging) seek(event); });
    svg.addEventListener("pointerup", function () { dragging = false; });
    svg.addEventListener("pointercancel", function () { dragging = false; });
    mark();
  }

  /* The frame shown, on the chart and in words. */
  function mark() {
    var d = state.data;
    var host = byId("frame-series-chart");
    var said = byId("frame-series-value");
    if (!d || !host || !state.plot) return;
    var frame = trajectoryFrame(state.index);
    var here = host.querySelector(".frame-series-here");
    var dot = host.querySelector(".frame-series-dot");
    if (frame == null || !here || !dot) return;
    var x = state.plot.px(xOfFrame(frame));
    here.setAttribute("x1", x);
    here.setAttribute("x2", x);
    here.setAttribute("visibility", "visible");
    var point = nearestPoint(frame);
    // The analysis may have read every fifth frame, or not the first ones:
    // a frame further from its nearest point than the points are apart has
    // no value of its own.
    var spacing = d.frames.length > 1 ? (d.frames[d.frames.length - 1] - d.frames[0])
      / (d.frames.length - 1) : 0;
    var close = Math.abs(d.frames[point] - frame) <= Math.max(0.5, spacing / 2 + 1e-9);
    if (close) {
      dot.setAttribute("cx", state.plot.px(d.x[point]));
      dot.setAttribute("cy", state.plot.py(d.y[point]));
      dot.setAttribute("visibility", "visible");
    } else {
      dot.setAttribute("visibility", "hidden");
    }
    host.setAttribute("data-frame", String(frame));
    if (said) {
      said.textContent = close
        ? format(d.y[point]) + (d.unit ? " " + d.unit : "")
          + (d.frames[point] === frame ? "" : " (frame " + d.frames[point] + ", the nearest analysed)")
        : "not analysed at this frame";
    }
  }

  function listSeries() {
    if (!state.listAsked) {
      state.listAsked = fetch("/api/series", { cache: "no-store" })
        .then(function (r) { return r.json(); })
        .then(function (said) { state.list = said && said.ok ? said.series || [] : []; })
        .catch(function () { state.list = []; });
    }
    return state.listAsked;
  }

  function choose(name) {
    state.name = name;
    state.data = null;
    return fetch("/api/series?analysis=" + encodeURIComponent(name), { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (state.name !== name) return;
        var note = byId("frame-series-note");
        if (!data || !data.ok || !Array.isArray(data.frames) || data.frames.length < 2) {
          state.data = null;
          byId("frame-series-chart").replaceChildren();
          if (note) { note.hidden = false; note.textContent = (data && data.reason) || "No numbers."; }
          return;
        }
        if (!data.linked) {
          // Of another trajectory than the one played: its frames are not
          // these.
          state.data = null;
          byId("frame-series-chart").replaceChildren();
          if (note) {
            note.hidden = false;
            note.textContent = data.label + " was computed from another trajectory than the "
              + "one played, so its frames are not these.";
          }
          return;
        }
        if (note) note.hidden = true;
        state.data = data;
        plot();
      })
      .catch(function () { /* the row keeps its last chart */ });
  }

  function show() {
    var host = row();
    if (!host || !ofTheTrajectory(state.playback)) { hide("not-the-trajectory"); return; }
    listSeries().then(function () {
      if (!ofTheTrajectory(state.playback)) return;
      if (!state.list.length) { hide("no-series"); return; }
      var pick = byId("frame-series-pick");
      if (pick && pick.options.length !== state.list.length) {
        pick.replaceChildren();
        state.list.forEach(function (series) {
          var option = document.createElement("option");
          option.value = series.analysis;
          option.textContent = series.label + (series.unit ? " (" + series.unit + ")" : "");
          pick.appendChild(option);
        });
      }
      var wanted = state.name || remembered();
      var names = state.list.map(function (series) { return series.analysis; });
      var name = names.indexOf(wanted) >= 0 ? wanted : names[0];
      if (pick) pick.value = name;
      host.hidden = false;
      host.setAttribute("data-state", "shown");
      if (name !== state.name || !state.data) choose(name); else plot();
    });
  }

  function forget() {
    state.list = null;
    state.listAsked = null;
    state.data = null;
    state.name = null;
    state.playback = null;
    state.index = null;
    state.plot = null;
    hide("none");
  }

  function wire() {
    var pick = byId("frame-series-pick");
    if (pick) {
      pick.addEventListener("change", function () {
        remember(pick.value);
        choose(pick.value);
      });
    }
    window.addEventListener("dashboard:playback-ready", function (event) {
      var payload = event.detail || {};
      var changed = !state.playback || !payload.frame_indices
        || String(payload.frame_indices) !== String(state.playback.frame_indices);
      state.playback = ofTheTrajectory(payload) ? payload : null;
      if (changed) state.plot = null;
      if (state.index != null || !row().hidden) show();
    });
    window.addEventListener("dashboard:frame-shown", function (event) {
      state.index = event.detail ? event.detail.index : null;
      if (!state.playback) {
        fetch("/api/frames-info", { cache: "no-store" })
          .then(function (r) { return r.json(); })
          .then(function (payload) {
            state.playback = ofTheTrajectory(payload) ? payload : null;
            show();
          })
          .catch(function () { /* nothing to follow */ });
        return;
      }
      if (row().hidden) show(); else mark();
    });
    // New results may have new series; the page is sent its results every
    // few seconds, so only a change in which analyses there are asks again.
    var analysed = null;
    window.addEventListener("dashboard:results-updated", function (event) {
      var payload = event.detail || {};
      var now = JSON.stringify((Array.isArray(payload.analyses) ? payload.analyses : [])
        .map(function (a) { return [a.name || a.analysis || "", a.status || ""]; }));
      if (analysed === null) { analysed = now; return; }
      if (now === analysed) return;
      analysed = now;
      state.listAsked = null;
      state.data = null;
      if (!row().hidden) show();
    });
    window.addEventListener("dashboard:run-changed", forget);
    document.addEventListener("fmx:theme", function () { if (state.data) plot(); });
    var chart = byId("frame-series-chart");
    if (chart && window.ResizeObserver) {
      var width = 0;
      new ResizeObserver(function () {
        if (state.data && Math.abs(chart.clientWidth - width) > 4) {
          width = chart.clientWidth;
          plot();
        }
      }).observe(chart);
    }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", wire);
  else wire();

  window.FastMDXFrameSeries = { state: state, show: show };
}());
