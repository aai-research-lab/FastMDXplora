/* The ruler of the Viewer's timeline: the frames played, on production's
 * clock, with the frame shown and what happened on them marked.
 *
 * Under the transport and over the series' tracks (frame-series.js), on the
 * same axis: where each analysis's averaging began (the first track's
 * equilibration, shaded), each cluster's medoid, the frame its clustering
 * points to as most like the rest of its cluster, and each time a study run
 * until it is determined judged it (`/api/timeline`, timeline_view.py). A
 * click or a drag on the ruler shows that frame; a mark is named on hover
 * and is a stop of the keyboard's, Enter showing its frame.
 *
 * Only the trajectory's own frames are ruled: while a run is going the
 * Viewer plays snapshots, numbered otherwise.
 */
(function () {
  "use strict";

  var NS = "http://www.w3.org/2000/svg";
  var HEIGHT = 30;
  var state = { playback: null, index: null, sourceFrame: null, timeNs: null,
    marks: [], unread: [], asked: null, map: null };

  function byId(id) { return document.getElementById(id); }
  function el(name, attrs, parent) {
    var node = document.createElementNS(NS, name);
    Object.keys(attrs || {}).forEach(function (key) { node.setAttribute(key, attrs[key]); });
    if (parent) parent.appendChild(node);
    return node;
  }
  function token(name, fallback) {
    var value = "";
    try { value = getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
    catch (e) { /* no styles yet */ }
    return value || fallback;
  }

  function ofTheTrajectory(payload) {
    return !!(payload && payload.playback_available && payload.source_kind === "production-dcd"
      && Array.isArray(payload.frame_indices) && payload.frame_indices.length > 1);
  }

  /* Production's clock where every frame played has a time; the frames'
   * own numbers where the run recorded no clock (as the tracks then are). */
  function clocked() {
    var t = state.playback.frame_times_ns;
    return Array.isArray(t) && t.length === state.playback.frame_indices.length
      && t.every(function (v) { return typeof v === "number" && isFinite(v); });
  }

  function margins() {
    var m = window.FastMDXFrameSeries && window.FastMDXFrameSeries.margins;
    return m || { left: 48, right: 10 };
  }

  function times() { return clocked() ? state.playback.frame_times_ns : state.playback.frame_indices; }

  function playedOfTime(t) {
    var all = times();
    var best = 0;
    for (var i = 1; i < all.length; i += 1) if (Math.abs(all[i] - t) < Math.abs(all[best] - t)) best = i;
    return best;
  }

  function seek(played) {
    if (played == null) return;
    seekSource(state.playback.frame_indices[played]);
  }

  function seekSource(frame) {
    if (frame == null || frame === state.sourceFrame) return;
    window.dispatchEvent(new CustomEvent("dashboard:trajectory-seek", {
      detail: { trajectoryFrame: frame, sourceSignature: state.playback.source_signature } }));
  }

  /* Where averaging began, by the first track drawn. */
  function averagedFrom() {
    var tracks = window.FastMDXFrameSeries ? window.FastMDXFrameSeries.state.tracks : [];
    var drawn = (tracks || []).find(function (t) { return t.data; });
    var mean = drawn && drawn.data.mean;
    // On the ruler's own axis: production's clock, or the frames' numbers.
    var same = clocked() ? drawn && drawn.data.x_label === "Time (ns)" : drawn && drawn.data.x_label === "Frame";
    if (!mean || mean.from_x == null || !(mean.discard > 0) || !same) return null;
    return { time: mean.from_x, label: "Averaged from here: " + drawn.data.label
      + "'s equilibration, " + mean.discard + " frames, left out before it" };
  }

  function draw() {
    var host = byId("timeline-ruler");
    if (!host) return;
    if (!ofTheTrajectory(state.playback)) {
      host.hidden = true;
      host.replaceChildren();
      return;
    }
    host.hidden = false;
    host.replaceChildren();
    var width = Math.max(200, Math.round(host.clientWidth || 600));
    var m = margins();
    var all = times();
    var low = all[0], high = all[all.length - 1];
    if (!(high > low)) high = low + 1;
    var left = m.left, right = width - m.right;
    function px(t) { return left + (t - low) / (high - low) * (right - left); }
    function tOf(x) { return low + (x - left) / (right - left) * (high - low); }
    state.map = { px: px, width: width };
    var clock = !clocked() ? { factor: 1, unit: "frame" }
      : window.FastMDXSeries && window.FastMDXSeries.timeScale
        ? window.FastMDXSeries.timeScale(all, "Time (ns)") : { factor: 1, unit: "ns" };
    var c = { axis: token("--text-muted", "#85858f"), text: token("--text-secondary", "#b5b5bb"),
              here: token("--text-primary", "#f2f2f4"), shade: token("--text-muted", "#85858f"),
              medoid: token("--accent-violet", "#a78bfa"), check: token("--accent-orange", "#ffb86b") };
    var svg = el("svg", { width: width, height: HEIGHT, viewBox: "0 0 " + width + " " + HEIGHT,
      class: "timeline-svg", role: "group",
      "aria-label": clocked() ? "The frames, on production's clock" : "The frames, by number" }, host);
    var from = averagedFrom();
    if (from && from.time > low) {
      var shade = el("rect", { x: left, y: 2, width: Math.max(0, px(from.time) - left), height: 12,
        fill: c.shade, "fill-opacity": 0.14, class: "timeline-equilibration" }, svg);
      el("title", {}, shade).textContent = from.label;
    }
    // The time range chosen on the Analysis page (plot-tools.js), on the
    // ruler and across the tracks under it.
    var tools = window.FastMDXPlotTools;
    var chosen = tools ? tools.range(clocked() ? "Time (ns)" : "Frame") : null;
    var tracks = byId("frame-series");
    if (chosen && chosen.to > low && chosen.from < high) {
      var a = px(Math.max(low, chosen.from)), b = px(Math.min(high, chosen.to));
      var band = el("rect", { x: a, y: 2, width: Math.max(1, b - a), height: 12,
        fill: c.medoid, "fill-opacity": 0.28, class: "timeline-range" }, svg);
      el("title", {}, band).textContent = "The range chosen on the Analysis page";
      if (tracks) {
        tracks.style.setProperty("--range-left", a + "px");
        tracks.style.setProperty("--range-width", Math.max(1, b - a) + "px");
        tracks.dataset.range = "on";
      }
    } else if (tracks) {
      delete tracks.dataset.range;
    }
    el("line", { x1: left, x2: right, y1: 14, y2: 14, stroke: c.axis, "stroke-width": 1 }, svg);
    var ticks = window.FastMDXSeries && window.FastMDXSeries.ticks
      ? window.FastMDXSeries.ticks(low * clock.factor, high * clock.factor, 6) : [];
    ticks.forEach(function (v, i) {
      var t = v / clock.factor;
      if (t < low - 1e-12 || t > high + 1e-12) return;
      el("line", { x1: px(t), x2: px(t), y1: 11, y2: 17, stroke: c.axis }, svg);
      var label = el("text", { x: px(t), y: 28, fill: c.text, "font-size": 9, "text-anchor": "middle" }, svg);
      label.textContent = +v.toPrecision(4) + (i === ticks.length - 1 ? " " + clock.unit : "");
    });
    state.marks.forEach(function (mark) {
      var t = clocked() ? mark.time_ns : null;
      if (mark.frame != null && !clocked()) t = mark.frame;
      if (t == null || t < low - 1e-12 || t > high + 1e-12) return;
      var x = px(t);
      var shape = mark.kind === "medoid"
        ? el("path", { d: "M" + x + " 4 l4 4 l-4 4 l-4 -4 z", fill: c.medoid }, svg)
        : el("path", { d: "M" + (x - 4) + " 3 h8 l-4 7 z", fill: c.check }, svg);
      shape.setAttribute("class", "timeline-mark");
      shape.setAttribute("data-kind", mark.kind);
      shape.setAttribute("tabindex", "0");
      shape.setAttribute("role", "button");
      shape.setAttribute("aria-label", mark.label);
      el("title", {}, shape).textContent = mark.label;
      var go = function (event) {
        event.stopPropagation();
        if (mark.frame != null) seekSource(mark.frame);
        else seek(playedOfTime(t));
      };
      shape.addEventListener("pointerdown", go);
      shape.addEventListener("keydown", function (event) {
        if (event.key === "Enter" || event.key === " ") { event.preventDefault(); go(event); }
      });
    });
    el("line", { class: "timeline-here", y1: 2, y2: 20, stroke: c.here, "stroke-width": 1.5,
      visibility: "hidden" }, svg);
    var dragging = false;
    function at(event) {
      var box = svg.getBoundingClientRect();
      var x = (event.clientX - box.left) * (width / box.width);
      seek(playedOfTime(tOf(Math.max(left, Math.min(right, x)))));
    }
    svg.addEventListener("pointerdown", function (event) {
      dragging = true;
      if (svg.setPointerCapture) svg.setPointerCapture(event.pointerId);
      at(event);
    });
    svg.addEventListener("pointermove", function (event) { if (dragging) at(event); });
    svg.addEventListener("pointerup", function () { dragging = false; });
    svg.addEventListener("pointercancel", function () { dragging = false; });
    // A record a held read refused is not read: its marks are not missing.
    if (state.unread.length) {
      var note = document.createElement("p");
      note.className = "timeline-note";
      note.textContent = "Not marked: " + state.unread.join(", ")
        + (state.unread.length === 1 ? " was" : " were") + " not read.";
      host.appendChild(note);
    }
    mark();
  }

  function mark() {
    var host = byId("timeline-ruler");
    if (!host || !state.map || state.index == null || !ofTheTrajectory(state.playback)) return;
    var here = host.querySelector(".timeline-here");
    var frame = state.sourceFrame != null ? state.sourceFrame : state.playback.frame_indices[state.index];
    var t = !clocked() ? frame
      : state.timeNs != null ? state.timeNs : times()[state.playback.frame_indices.indexOf(frame)];
    if (!here || t == null) return;
    var x = state.map.px(t);
    here.setAttribute("x1", x);
    here.setAttribute("x2", x);
    here.setAttribute("visibility", "visible");
    host.setAttribute("data-frame", String(frame));
  }

  function loadMarks() {
    if (state.asked) return state.asked;
    state.asked = fetch("/api/timeline", { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (said) {
        state.marks = said && said.ok ? said.marks || [] : [];
        state.unread = said && Array.isArray(said.unavailable) ? said.unavailable : [];
        draw();
      })
      .catch(function () { state.marks = []; state.unread = []; });
    return state.asked;
  }

  function wire() {
    window.addEventListener("dashboard:playback-ready", function (event) {
      state.playback = ofTheTrajectory(event.detail) ? event.detail : null;
      draw();
      if (state.playback) loadMarks();
    });
    window.addEventListener("dashboard:frame-shown", function (event) {
      state.index = event.detail ? event.detail.index : null;
      state.sourceFrame = Number.isInteger(event.detail?.trajectoryFrame)
        ? event.detail.trajectoryFrame : null;
      state.timeNs = typeof event.detail?.timeNs === "number" ? event.detail.timeNs : null;
      mark();
    });
    // The first track's equilibration, once it is drawn.
    window.addEventListener("dashboard:tracks-drawn", draw);
    window.addEventListener("dashboard:run-changed", function () {
      state.playback = null;
      state.index = null;
      state.sourceFrame = null;
      state.timeNs = null;
      state.marks = [];
      state.unread = [];
      state.asked = null;
      draw();
    });
    window.addEventListener("dashboard:results-updated", function () { state.asked = null; });
    document.addEventListener("fmx:theme", draw);
    document.addEventListener("fmx:time-range", draw);
    var host = byId("timeline-ruler");
    if (host && window.ResizeObserver) {
      var width = 0;
      new ResizeObserver(function () {
        if (Math.abs(host.clientWidth - width) > 4) { width = host.clientWidth; draw(); }
      }).observe(host);
    }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", wire);
  else wire();

  window.FastMDXTimeline = { state: state, draw: draw };
}());
