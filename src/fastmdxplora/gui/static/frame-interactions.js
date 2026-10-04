/* What holds the ligand, under the Viewer's transport and in the structure.
 *
 * The interactions analysis says which contacts hold the ligand and how
 * often each is present, in a table on the Analysis page, apart from the
 * structure it is about. Here each contact is a row of a timeline under the
 * frames played, marked where it was present, the frame shown marked across
 * them; and the contacts present in the frame shown are shown in the
 * structure as dashed lines between their atoms, coloured by kind, as the
 * frames play. They are the analysis's own contacts (/api/interactions-over-
 * frames), by its published criteria, not distances the page works out.
 */
(function () {
  "use strict";

  var NS = "http://www.w3.org/2000/svg";
  var MOST_ROWS = 12;
  var ROW = 13;
  var MARGIN = { top: 4, right: 44, bottom: 14, left: 178 };
  //  The kinds in colours that stay apart for most colour vision (Okabe and
  //  Ito), and on either scheme's ground.
  var KIND_COLOURS = {
    hydrogen_bond: "#56B4E9", salt_bridge: "#D55E00", hydrophobic: "#009E73",
    pi_stacking: "#CC79A7", pi_cation: "#0072B2", halogen_bond: "#E69F00",
    metal_coordination: "#999999", water_bridge: "#7FC8F8",
  };

  //  A kind's colour, or its family's: the analysis names a hydrogen bond
  //  by its direction and a stack by its arrangement
  //  (hydrogen_bond_ligand_donor, pi_stacking_face_to_face), and those take
  //  the colour of hydrogen_bond and pi_stacking.
  function kindColour(kind, fallback) {
    if (KIND_COLOURS[kind]) return KIND_COLOURS[kind];
    var family = Object.keys(KIND_COLOURS).filter(function (name) {
      return String(kind || "").indexOf(name + "_") === 0;
    }).sort(function (a, b) { return b.length - a.length; })[0];
    return family ? KIND_COLOURS[family] : fallback;
  }

  var state = {
    data: null,
    asked: null,
    playback: null,
    index: null,
    plot: null,
    shownKey: "",
  };

  function byId(id) { return document.getElementById(id); }

  function token(name, fallback) {
    var value = "";
    try {
      value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    } catch (e) { /* no styles yet */ }
    return value || fallback;
  }

  function el(name, attrs, parent) {
    var node = document.createElementNS(NS, name);
    Object.keys(attrs || {}).forEach(function (key) { node.setAttribute(key, attrs[key]); });
    if (parent) parent.appendChild(node);
    return node;
  }

  function ofTheTrajectory(payload) {
    return !!(payload && payload.playback_available && payload.source_kind === "production-dcd"
      && Array.isArray(payload.frame_indices) && payload.frame_indices.length > 1);
  }

  function row() { return byId("frame-interactions"); }

  function hide() {
    var host = row();
    if (host) host.hidden = true;
    showInStructure([]);
  }

  function engine() {
    var viewer = window.FastMDXMoleculeViewer;
    return viewer && viewer.STATE && viewer.STATE.model && viewer.STATE.model.of === "frames"
      ? viewer.STATE.engine : null;
  }

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

  /* The analysed frame a trajectory frame is, where the analysis read it or
   * one within half its spacing; else null. */
  function analysedAt(frame) {
    var frames = state.data.frames;
    var lo = 0;
    var hi = frames.length - 1;
    while (hi - lo > 1) {
      var mid = (lo + hi) >> 1;
      if (frames[mid] < frame) lo = mid; else hi = mid;
    }
    var best = Math.abs(frames[lo] - frame) <= Math.abs(frames[hi] - frame) ? lo : hi;
    var spacing = frames.length > 1 ? (frames[frames.length - 1] - frames[0]) / (frames.length - 1) : 0;
    return Math.abs(frames[best] - frame) <= Math.max(0.5, spacing / 2 + 1e-9) ? best : null;
  }

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

  function present(pair, analysed) {
    var runs = pair.episodes;
    var lo = 0;
    var hi = runs.length - 1;
    while (lo <= hi) {
      var mid = (lo + hi) >> 1;
      if (runs[mid][1] < analysed) lo = mid + 1;
      else if (runs[mid][0] > analysed) hi = mid - 1;
      else return true;
    }
    return false;
  }

  function plot() {
    var host = byId("frame-interactions-chart");
    var d = state.data;
    if (!host || !d || !state.playback) return;
    host.replaceChildren();
    var pairs = d.pairs.slice(0, MOST_ROWS);
    var height = MARGIN.top + pairs.length * ROW + MARGIN.bottom;
    var width = Math.max(260, Math.round(host.clientWidth || 600));
    var plotBox = { left: MARGIN.left, right: width - MARGIN.right, top: MARGIN.top,
      bottom: height - MARGIN.bottom };
    var frames = state.playback.frame_indices;
    var xLow = Math.min(d.x[0], xOfFrame(frames[0]));
    var xHigh = Math.max(d.x[d.x.length - 1], xOfFrame(frames[frames.length - 1]));
    if (xHigh === xLow) xHigh = xLow + 1;
    function px(x) { return plotBox.left + (x - xLow) / (xHigh - xLow) * (plotBox.right - plotBox.left); }
    // Each analysed frame a band as wide as the frames between its neighbours.
    var half = d.x.length > 1 ? (d.x[d.x.length - 1] - d.x[0]) / (d.x.length - 1) / 2 : 0.5;
    var text = token("--text-secondary", "#b5b5bb");
    var muted = token("--text-muted", "#85858f");
    var grid = token("--border-subtle", "rgba(255,255,255,0.07)");
    var svg = el("svg", {
      width: width, height: height, viewBox: "0 0 " + width + " " + height, role: "img",
      "aria-label": "The " + pairs.length + " contacts most often present, frame by frame",
      class: "frame-interactions-svg",
    }, host);
    pairs.forEach(function (pair, i) {
      var y = plotBox.top + i * ROW;
      var colour = kindColour(pair.kind, muted);
      el("line", { x1: plotBox.left, x2: plotBox.right, y1: y + ROW - 0.5, y2: y + ROW - 0.5,
        stroke: grid }, svg);
      var swatch = el("rect", { x: 2, y: y + 3, width: 7, height: 7, fill: colour }, svg);
      swatch.setAttribute("class", "frame-interactions-kind");
      var label = el("text", { x: 13, y: y + 10, fill: text, "font-size": 10 }, svg);
      label.textContent = pair.said + " " + pair.residue + " " + pair.atoms_said;
      pair.episodes.forEach(function (run) {
        var from = px(d.x[run[0]] - half);
        var to = px(d.x[run[1]] + half);
        el("rect", { x: Math.max(plotBox.left, from), y: y + 2,
          width: Math.max(1, Math.min(plotBox.right, to) - Math.max(plotBox.left, from)),
          height: ROW - 4, fill: colour, class: "frame-interactions-run" }, svg);
      });
      var share = el("text", { x: width - 4, y: y + 10, fill: muted, "font-size": 10,
        "text-anchor": "end" }, svg);
      share.textContent = pair.occupancy == null ? "" : Math.round(pair.occupancy * 100) + "%";
    });
    var ends = [[xLow, plotBox.left, "start"], [xHigh, plotBox.right, "end"]];
    ends.forEach(function (end) {
      var label = el("text", { x: end[1], y: height - 3, fill: muted, "font-size": 10,
        "text-anchor": end[2] }, svg);
      label.textContent = d.x_label === "Frame" ? "frame " + Math.round(end[0])
        : Number(end[0].toPrecision(4)) + " ns";
    });
    el("line", { class: "frame-interactions-here", y1: plotBox.top, y2: plotBox.bottom,
      stroke: token("--text-primary", "#f2f2f4"), "stroke-width": 1.4, visibility: "hidden" }, svg);
    state.plot = { px: px, width: width, plotBox: plotBox, xLow: xLow, xHigh: xHigh };

    var dragging = false;
    function seek(event) {
      var box = svg.getBoundingClientRect();
      var x = (event.clientX - box.left) * (width / box.width);
      if (x < plotBox.left - 4) return;
      var value = xLow + (x - plotBox.left) / (plotBox.right - plotBox.left) * (xHigh - xLow);
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

  function showInStructure(pairs) {
    var key = pairs.map(function (p) { return p.a + ":" + p.b + ":" + p.colour; }).join(",");
    var shown = engine();
    if (!shown || !shown.showInteractions) return;
    // The engine forgets them when it loads the frames again.
    var held = shown.interactions ? shown.interactions.size : 0;
    if (key === state.shownKey && held === pairs.length) return;
    state.shownKey = key;
    shown.showInteractions(pairs).catch(function () { state.shownKey = ""; });
  }

  /* The frame shown: across the timeline, in words, and in the structure. */
  function mark() {
    var d = state.data;
    var host = byId("frame-interactions-chart");
    var said = byId("frame-interactions-now");
    if (!d || !host || !state.plot) return;
    var frame = trajectoryFrame(state.index);
    var here = host.querySelector(".frame-interactions-here");
    if (frame == null || !here) return;
    var x = state.plot.px(xOfFrame(frame));
    here.setAttribute("x1", x);
    here.setAttribute("x2", x);
    here.setAttribute("visibility", "visible");
    host.setAttribute("data-frame", String(frame));
    var analysed = analysedAt(frame);
    var now = analysed == null ? [] : d.pairs.filter(function (pair) { return present(pair, analysed); });
    if (said) {
      said.textContent = analysed == null ? "not analysed at this frame"
        : now.length + " of " + d.total_pairs + " present in this frame";
    }
    var wanted = byId("frame-interactions-shown");
    showInStructure(!d.linked || (wanted && !wanted.checked) ? [] : now
      .filter(function (pair) { return Array.isArray(pair.atoms); })
      .map(function (pair) {
        return { a: pair.atoms[0], b: pair.atoms[1],
          colour: parseInt(kindColour(pair.kind, "#999999").slice(1), 16) };
      }));
  }

  function note(text) {
    var line = byId("frame-interactions-note");
    if (!line) return;
    line.hidden = !text;
    line.textContent = text || "";
  }

  function load() {
    if (!state.asked) {
      state.asked = fetch("/api/interactions-over-frames", { cache: "no-store" })
        .then(function (r) { return r.json(); })
        .catch(function () { return { ok: false }; });
    }
    return state.asked;
  }

  function show() {
    var host = row();
    if (!host || !ofTheTrajectory(state.playback)) { hide(); return; }
    load().then(function (said) {
      if (!ofTheTrajectory(state.playback)) return;
      if (!said || !said.ok) {
        // A study analysed before the frames were recorded is told how to
        // see them; one with no interactions analysed shows nothing.
        if (said && said.reason && /again/.test(said.reason)) {
          host.hidden = false;
          state.data = null;
          byId("frame-interactions-chart").replaceChildren();
          note(said.reason);
        } else {
          hide();
        }
        return;
      }
      if (!said.pairs.length) {
        host.hidden = false;
        state.data = null;
        byId("frame-interactions-chart").replaceChildren();
        note("The analysis found no contact holding the ligand.");
        return;
      }
      state.data = said;
      host.hidden = false;
      var notes = [];
      if (!said.linked) {
        notes.push("These contacts were computed from another trajectory than the one played, "
          + "so they are not shown in the structure.");
      }
      if (said.total_pairs > MOST_ROWS) {
        notes.push("The " + MOST_ROWS + " most often present of " + said.total_pairs
          + "; the Analysis page lists them all.");
      }
      note(notes.join(" "));
      plot();
    });
  }

  function forget() {
    state.data = null;
    state.asked = null;
    state.playback = null;
    state.index = null;
    state.plot = null;
    state.shownKey = "";
    var host = row();
    if (host) host.hidden = true;
  }

  function wire() {
    window.addEventListener("dashboard:playback-ready", function (event) {
      var payload = event.detail || {};
      state.playback = ofTheTrajectory(payload) ? payload : null;
      state.shownKey = "";
      if (state.index != null) show();
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
      if (!state.data && row().hidden) show(); else mark();
    });
    var analysed = null;
    window.addEventListener("dashboard:results-updated", function (event) {
      var payload = event.detail || {};
      var now = JSON.stringify((Array.isArray(payload.analyses) ? payload.analyses : [])
        .map(function (a) { return [a.name || a.analysis || "", a.status || ""]; }));
      if (analysed === null) { analysed = now; return; }
      if (now === analysed) return;
      analysed = now;
      state.asked = null;
      state.data = null;
      if (state.playback) show();
    });
    window.addEventListener("dashboard:run-changed", forget);
    var wanted = byId("frame-interactions-shown");
    if (wanted) wanted.addEventListener("change", mark);
    document.addEventListener("fmx:theme", function () { if (state.data) plot(); });
    var chart = byId("frame-interactions-chart");
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

  window.FastMDXFrameInteractions = { state: state, show: show };
}());
