/* The study's series as tracks under the Viewer's timeline, following the
 * frame shown.
 *
 * The trajectory was played with a slider that said only a frame number and
 * a time: where the RMSD rose, or the radius of gyration fell, had to be
 * found on the Analysis page and carried back by hand. The study's series
 * over time (from /api/series) are drawn as tracks under the timeline,
 * three at first (RMSD, the radius of gyration, the next listed), each with
 * the frames its analysis left out as equilibration shaded and the mean of
 * the rest. A line marks the frame shown on every track and moves as it
 * plays; each track says its value there, and a click or a drag along any
 * track shows that frame. A track is added from the list and taken away by
 * its ×; which are shown is kept in this browser.
 *
 * Only the frames of the trajectory itself are followed: a series is of the
 * trajectory analysed, and while a run is going the Viewer plays snapshots,
 * numbered otherwise.
 */
(function () {
  "use strict";

  var NS = "http://www.w3.org/2000/svg";
  var HEIGHT = 40;
  var MARGIN = { top: 14, right: 10, bottom: 3, left: 48 };
  var KEPT = "fastmdx.frameTracks";
  var FIRST = 3;

  var state = {
    list: null,       // [{analysis, label, unit}]
    listAsked: null,
    tracks: [],       // [{name, data, plot, asked}]
    playback: null,   // the frames played: frame_indices
    index: null,      // the played frame shown
    sourceFrame: null,
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
      line: token("--hue-cyan", "#00aed8"),
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

  function html(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = text;
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

  function kept() {
    try {
      var said = JSON.parse(window.localStorage.getItem(KEPT) || "null");
      return Array.isArray(said) ? said.map(String) : null;
    } catch (e) { return null; }
  }

  function keep(names) {
    try { window.localStorage.setItem(KEPT, JSON.stringify(names)); } catch (e) { /* not kept */ }
  }

  /* The frames played are the trajectory's own, not a run's snapshots. */
  function ofTheTrajectory(payload) {
    return !!(payload && payload.playback_available && payload.source_kind === "production-dcd"
      && Array.isArray(payload.frame_indices) && payload.frame_indices.length > 1);
  }

  function row() { return byId("frame-series"); }
  function host() { return byId("frame-series-chart"); }

  function hide(reason) {
    var shown = row();
    if (!shown) return;
    shown.hidden = true;
    shown.setAttribute("data-state", reason || "none");
  }

  /* The trajectory frame of a played frame, and back. */
  function trajectoryFrame(index) {
    var frames = state.playback ? state.playback.frame_indices : [];
    return index != null && index >= 0 && index < frames.length ? frames[index] : null;
  }

  /* The loaded frame nearest a trajectory frame, or none before playback. */
  function nearestLoaded(frame) {
    var frames = state.playback && Array.isArray(state.playback.frame_indices)
      ? state.playback.frame_indices : [];
    var best = null;
    frames.forEach(function (loaded) {
      if (best == null || Math.abs(loaded - frame) < Math.abs(best - frame)) best = loaded;
    });
    return best;
  }

  function shownFrame() {
    return state.sourceFrame != null ? state.sourceFrame : trajectoryFrame(state.index);
  }

  /* The analysed point nearest a trajectory frame, over frames ascending. */
  function nearestPoint(d, frame) {
    var frames = d.frames;
    var lo = 0;
    var hi = frames.length - 1;
    while (hi - lo > 1) {
      var mid = (lo + hi) >> 1;
      if (frames[mid] < frame) lo = mid; else hi = mid;
    }
    return Math.abs(frames[lo] - frame) <= Math.abs(frames[hi] - frame) ? lo : hi;
  }

  /* A series' axis is its own (time, or frame where no clock is recorded);
   * a trajectory frame is placed on it by the line through the series'
   * first and last points, which is how the loader numbered them. */
  function xOfFrame(d, frame) {
    var n = d.frames.length;
    var span = d.frames[n - 1] - d.frames[0];
    if (!span) return d.x[0];
    return d.x[0] + (frame - d.frames[0]) * (d.x[n - 1] - d.x[0]) / span;
  }

  function frameOfX(d, x) {
    var n = d.frames.length;
    var span = d.x[n - 1] - d.x[0];
    if (!span) return d.frames[0];
    return d.frames[0] + (x - d.x[0]) * (d.frames[n - 1] - d.frames[0]) / span;
  }

  /* -------------------------------------------------------------------- */
  /* One track                                                             */
  /* -------------------------------------------------------------------- */

  function named(track) {
    return (state.list || []).find(function (s) { return s.analysis === track.name; }) || {};
  }

  function trackNode(track) {
    var node = host().querySelector('.frame-track[data-analysis="' + track.name + '"]');
    if (node) return node;
    node = html("div", "frame-track");
    node.dataset.analysis = track.name;
    var head = html("div", "frame-track-head");
    var listed = (state.list || []).find(function (s) { return s.analysis === track.name; }) || {};
    head.appendChild(html("span", "frame-track-label",
      (listed.label || track.name) + (listed.unit ? " (" + listed.unit + ")" : "")));
    head.appendChild(html("span", "frame-track-value mono"));
    var drop = html("button", "frame-track-drop");
    drop.type = "button";
    drop.textContent = "×";
    drop.title = "Take this series away";
    drop.setAttribute("aria-label", "Take " + (listed.label || track.name) + " away");
    drop.addEventListener("click", function () { remove(track.name); });
    head.appendChild(drop);
    node.appendChild(head);
    node.appendChild(html("div", "frame-track-chart"));
    node.appendChild(html("p", "frame-track-note muted small"));
    host().appendChild(node);
    return node;
  }

  function plot(track) {
    var node = trackNode(track);
    var chart = node.querySelector(".frame-track-chart");
    var d = track.data;
    chart.replaceChildren();
    if (!d) return;
    var width = Math.max(200, Math.round(chart.clientWidth || host().clientWidth || 600));
    var c = colours();
    var area = { left: MARGIN.left, top: MARGIN.top, right: width - MARGIN.right,
      bottom: HEIGHT - MARGIN.bottom };
    var xs = d.x;
    var ys = d.y;
    // The axis runs over the frames played, so every track and the ruler
    // above them span what the slider spans.
    var frames = state.playback.frame_indices;
    var xLow = Math.min(xs[0], xOfFrame(d, frames[0]));
    var xHigh = Math.max(xs[xs.length - 1], xOfFrame(d, frames[frames.length - 1]));
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
    function px(x) { return area.left + (x - xLow) / (xHigh - xLow) * (area.right - area.left); }
    function py(y) { return area.bottom - (y - yLow) / (yHigh - yLow) * (area.bottom - area.top); }
    track.plot = { px: px, py: py, xLow: xLow, xHigh: xHigh, area: area, width: width };

    var svg = el("svg", {
      width: width, height: HEIGHT, viewBox: "0 0 " + width + " " + HEIGHT, role: "img",
      "aria-label": d.label + " over the trajectory, the frame shown marked",
      class: "frame-series-svg",
    }, chart);
    if (mean && mean.from_x != null && mean.discard > 0) {
      el("rect", {
        x: area.left, y: area.top, width: Math.max(0, px(mean.from_x) - area.left),
        height: area.bottom - area.top, fill: c.axis, "fill-opacity": 0.12,
        class: "frame-series-excluded",
      }, svg);
    }
    // The range, at the track's foot and head.
    [[yLow + pad, area.bottom], [yHigh - pad, area.top + 7]].forEach(function (pair) {
      var label = el("text", { x: area.left - 6, y: pair[1], fill: c.axis,
        "font-size": 8, "text-anchor": "end" }, svg);
      label.textContent = format(pair[0]);
    });
    el("path", {
      d: xs.map(function (x, i) {
        return (i ? "L" : "M") + px(x).toFixed(1) + " " + py(ys[i]).toFixed(1);
      }).join(""),
      fill: "none", stroke: c.line, "stroke-width": 1.3, "stroke-linejoin": "round",
      class: "frame-series-line",
    }, svg);
    if (mean) {
      var start = px(mean.from_x != null ? mean.from_x : xs[0]);
      el("line", { x1: start, x2: px(xs[xs.length - 1]), y1: py(mean.value), y2: py(mean.value),
        stroke: mean.not_a_measurement ? c.axis : c.mean, "stroke-width": 1.1,
        "stroke-dasharray": "5 4", class: "frame-series-mean" }, svg);
    }
    el("line", { class: "frame-series-here", y1: area.top, y2: area.bottom, stroke: c.here,
      "stroke-width": 1.3, visibility: "hidden" }, svg);
    el("circle", { class: "frame-series-dot", r: 3, fill: c.line, stroke: c.here,
      "stroke-width": 1.1, visibility: "hidden" }, svg);

    // A click, or a drag, along the track shows that frame. While the
    // pointer moves, the frame shown is the nearest one already loaded;
    // where it is let go, that exact frame. Asking for each frame passed
    // over fetched (and fitted) one exact frame per pointer move.
    var dragging = false;
    function seek(event, exact) {
      var box = svg.getBoundingClientRect();
      var x = (event.clientX - box.left) * (width / box.width);
      var value = xLow + (x - area.left) / (area.right - area.left) * (xHigh - xLow);
      var frame = Math.round(frameOfX(d, value));
      if (!exact) {
        var loaded = nearestLoaded(frame);
        if (loaded != null) frame = loaded;
      }
      if (frame === shownFrame()) return;
      window.dispatchEvent(new CustomEvent("dashboard:trajectory-seek",
        { detail: { trajectoryFrame: frame } }));
    }
    svg.addEventListener("pointerdown", function (event) {
      dragging = true;
      if (svg.setPointerCapture) svg.setPointerCapture(event.pointerId);
      seek(event, false);
    });
    svg.addEventListener("pointermove", function (event) { if (dragging) seek(event, false); });
    svg.addEventListener("pointerup", function (event) {
      if (dragging) seek(event, true);
      dragging = false;
    });
    svg.addEventListener("pointercancel", function () { dragging = false; });
    markTrack(track);
  }

  /* The frame shown, on a track and in words beside it. */
  function markTrack(track) {
    var d = track.data;
    var node = host() && host().querySelector('.frame-track[data-analysis="' + track.name + '"]');
    if (!d || !node || !track.plot) return;
    var frame = shownFrame();
    var here = node.querySelector(".frame-series-here");
    var dot = node.querySelector(".frame-series-dot");
    var said = node.querySelector(".frame-track-value");
    if (frame == null || !here || !dot) return;
    var x = track.plot.px(xOfFrame(d, frame));
    here.setAttribute("x1", x);
    here.setAttribute("x2", x);
    here.setAttribute("visibility", "visible");
    var point = nearestPoint(d, frame);
    // The analysis may have read every fifth frame, or not the first ones:
    // a frame further from its nearest point than the points are apart has
    // no value of its own.
    var spacing = d.frames.length > 1 ? (d.frames[d.frames.length - 1] - d.frames[0])
      / (d.frames.length - 1) : 0;
    var close = Math.abs(d.frames[point] - frame) <= Math.max(0.5, spacing / 2 + 1e-9);
    if (close) {
      dot.setAttribute("cx", track.plot.px(d.x[point]));
      dot.setAttribute("cy", track.plot.py(d.y[point]));
      dot.setAttribute("visibility", "visible");
    } else {
      dot.setAttribute("visibility", "hidden");
    }
    if (said) {
      said.textContent = close
        // Its unit is in the track's name beside it.
        ? format(d.y[point]) + (d.unit && !named(track).unit ? " " + d.unit : "")
          + (d.frames[point] === frame ? "" : " (frame " + d.frames[point] + ", the nearest analysed)")
        : "not analysed at this frame";
    }
  }

  function mark() {
    var frame = shownFrame();
    if (host() && frame != null) host().setAttribute("data-frame", String(frame));
    state.tracks.forEach(markTrack);
    window.dispatchEvent(new CustomEvent("dashboard:tracks-marked", { detail: { frame: frame } }));
  }

  function fetchTrack(track) {
    track.asked = fetch("/api/series?analysis=" + encodeURIComponent(track.name), { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (state.tracks.indexOf(track) < 0) return;
        var node = trackNode(track);
        var note = node.querySelector(".frame-track-note");
        if (!data || !data.ok || !Array.isArray(data.frames) || data.frames.length < 2) {
          track.data = null;
          node.querySelector(".frame-track-chart").replaceChildren();
          note.textContent = (data && data.reason) || "No numbers.";
          return;
        }
        if (!data.linked) {
          // Of another trajectory than the one played: its frames are not
          // these.
          track.data = null;
          node.querySelector(".frame-track-chart").replaceChildren();
          note.textContent = data.label + " was computed from another trajectory than the "
            + "one played, so its frames are not these.";
          return;
        }
        note.textContent = "";
        track.data = data;
        plot(track);
        window.dispatchEvent(new CustomEvent("dashboard:tracks-drawn"));
      })
      .catch(function () { /* the track keeps its last chart */ });
    return track.asked;
  }

  /* -------------------------------------------------------------------- */
  /* Which tracks                                                          */
  /* -------------------------------------------------------------------- */

  function listSeries() {
    if (!state.listAsked) {
      state.listAsked = fetch("/api/series", { cache: "no-store" })
        .then(function (r) { return r.json(); })
        .then(function (said) { state.list = said && said.ok ? said.series || [] : []; })
        .catch(function () { state.list = []; });
    }
    return state.listAsked;
  }

  function wanted() {
    var names = (state.list || []).map(function (s) { return s.analysis; });
    var chosen = kept();
    var shown = (chosen || names.slice(0, FIRST)).filter(function (n) { return names.indexOf(n) >= 0; });
    return shown.length || chosen ? shown : names.slice(0, FIRST);
  }

  function offerTheRest() {
    var pick = byId("frame-series-pick");
    if (!pick) return;
    var shown = state.tracks.map(function (t) { return t.name; });
    pick.replaceChildren();
    var first = html("option", "", "Add a series…");
    first.value = "";
    pick.appendChild(first);
    (state.list || []).forEach(function (series) {
      if (shown.indexOf(series.analysis) >= 0) return;
      var option = html("option", "", series.label + (series.unit ? " (" + series.unit + ")" : ""));
      option.value = series.analysis;
      pick.appendChild(option);
    });
    pick.value = "";
    pick.hidden = pick.options.length < 2;
  }

  function setTracks(names) {
    var before = state.tracks;
    state.tracks = names.map(function (name) {
      return before.find(function (t) { return t.name === name; }) || { name: name, data: null, plot: null };
    });
    before.forEach(function (t) {
      if (state.tracks.indexOf(t) < 0) {
        var node = host() && host().querySelector('.frame-track[data-analysis="' + t.name + '"]');
        if (node) node.remove();
      }
    });
    // In the order chosen.
    state.tracks.forEach(function (t) { host().appendChild(trackNode(t)); });
    offerTheRest();
    state.tracks.forEach(function (t) { if (!t.data && !t.asked) fetchTrack(t); else if (t.data) plot(t); });
  }

  function add(name) {
    var names = state.tracks.map(function (t) { return t.name; });
    if (!name || names.indexOf(name) >= 0) return;
    names.push(name);
    keep(names);
    setTracks(names);
  }

  function remove(name) {
    var names = state.tracks.map(function (t) { return t.name; }).filter(function (n) { return n !== name; });
    keep(names);
    setTracks(names);
  }

  function show() {
    var shown = row();
    if (!shown || !ofTheTrajectory(state.playback)) { hide("not-the-trajectory"); return; }
    listSeries().then(function () {
      if (!ofTheTrajectory(state.playback)) return;
      if (!state.list.length) { hide("no-series"); return; }
      shown.hidden = false;
      shown.setAttribute("data-state", "shown");
      setTracks(wanted());
    });
  }

  function forget() {
    state.list = null;
    state.listAsked = null;
    state.tracks.forEach(function (t) {
      var node = host() && host().querySelector('.frame-track[data-analysis="' + t.name + '"]');
      if (node) node.remove();
    });
    state.tracks = [];
    state.playback = null;
    state.index = null;
    state.sourceFrame = null;
    hide("none");
  }

  function wire() {
    var pick = byId("frame-series-pick");
    if (pick) pick.addEventListener("change", function () { add(pick.value); });
    window.addEventListener("dashboard:playback-ready", function (event) {
      var payload = event.detail || {};
      var changed = !state.playback || !payload.frame_indices
        || String(payload.frame_indices) !== String(state.playback.frame_indices);
      state.playback = ofTheTrajectory(payload) ? payload : null;
      if (changed) state.tracks.forEach(function (t) { t.plot = null; });
      // Shown as soon as there are frames to follow, not at the first one
      // shown: the timeline is always under the molecule.
      show();
    });
    window.addEventListener("dashboard:frame-shown", function (event) {
      state.index = event.detail ? event.detail.index : null;
      state.sourceFrame = Number.isInteger(event.detail?.trajectoryFrame)
        ? event.detail.trajectoryFrame : null;
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
      state.tracks.forEach(function (t) { t.data = null; t.asked = null; });
      if (!row().hidden) show();
    });
    window.addEventListener("dashboard:run-changed", forget);
    document.addEventListener("fmx:theme", function () {
      state.tracks.forEach(function (t) { if (t.data) plot(t); });
    });
    var chart = host();
    if (chart && window.ResizeObserver) {
      var width = 0;
      new ResizeObserver(function () {
        if (Math.abs(chart.clientWidth - width) > 4) {
          width = chart.clientWidth;
          state.tracks.forEach(function (t) { if (t.data) plot(t); });
        }
      }).observe(chart);
    }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", wire);
  else wire();

  window.FastMDXFrameSeries = {
    state: state, show: show, add: add, remove: remove,
    /* Where a trajectory frame is across the tracks, for the ruler above
     * them (viewer-timeline.js): the first track drawn's mapping. */
    margins: MARGIN,
  };
}());
