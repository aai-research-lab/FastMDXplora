/* The way through a channel, in the Viewer (gui/pore.py).
 *
 * The `pore_profile` analysis leaves the radius of the widest sphere among
 * the protein's atoms at each height along the pore's axis, frame by frame.
 * Here its mean is plotted against height with the range over the frames
 * and the profile of the frame shown, over the two radii HOLE marks: under
 * 1.15 angstroms a water does not fit, and up to 2.30 there is room for one
 * abreast. **Show the pore** renders the mean pore, the union of the mean
 * spheres, as a surface placed on the first frame, the frames superposed on
 * the backbone to it; it is hidden while the frames are fitted otherwise.
 */
(function () {
  "use strict";

  var KEY = "pore";
  var COLOUR = 0x56b4e9;
  var state = { data: null, asking: null, frame: 0, name: "" };

  function byId(id) { return document.getElementById(id); }

  function viewer() { return window.FastMDXMoleculeViewer; }

  function engine() {
    var v = viewer();
    return v && v.STATE ? v.STATE.engine : null;
  }

  function token(name, fallback) {
    var value = "";
    try {
      value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    } catch (e) { /* no styles yet */ }
    return value || fallback;
  }

  function margins(canvas) {
    var ratio = canvas.width / Math.max(1, canvas.clientWidth || canvas.width);
    return { left: 34 * ratio, right: 6 * ratio, top: 6 * ratio, bottom: 18 * ratio, ratio: ratio };
  }

  /* The profile of the frame shown, or none where it is not a frame played. */
  function ofFrame() {
    var profiles = state.data && state.data.profiles;
    if (!profiles || state.frame == null) return null;
    return profiles[Math.min(state.frame, profiles.length - 1)] || null;
  }

  function finite(values) {
    return values.filter(function (v) { return v != null; });
  }

  function draw() {
    var canvas = byId("pore-canvas");
    var data = state.data;
    if (!canvas || !data) return;
    var ratio = window.devicePixelRatio || 1;
    var width = Math.max(200, Math.round((canvas.clientWidth || 300) * ratio));
    var height = Math.round((canvas.clientHeight || 130) * ratio);
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
    }
    var m = margins(canvas);
    var context = canvas.getContext("2d");
    context.clearRect(0, 0, canvas.width, canvas.height);
    var heights = data.heights;
    var first = heights[0];
    var last = heights[heights.length - 1];
    var now = ofFrame();
    var top = Math.max.apply(null, finite(data.high).concat(now ? finite(now) : [],
      [data.one_water + 0.5]));
    var x = function (h) {
      return m.left + (canvas.width - m.left - m.right) * (h - first) / Math.max(1e-9, last - first);
    };
    var y = function (r) {
      return m.top + (canvas.height - m.top - m.bottom) * (top - Math.max(0, r)) / top;
    };
    var muted = token("--text-muted", "#85858f");
    context.font = Math.round(10 * m.ratio) + "px sans-serif";
    context.fillStyle = muted;
    context.textAlign = "right";
    context.textBaseline = "middle";
    context.fillText(top.toFixed(1) + " Å", m.left - 4 * m.ratio, y(top));
    context.fillText("0", m.left - 4 * m.ratio, y(0));
    context.textBaseline = "top";
    context.textAlign = "left";
    context.fillText(first.toFixed(0) + " Å", m.left, canvas.height - m.bottom + 4 * m.ratio);
    context.textAlign = "right";
    context.fillText(last.toFixed(0) + " Å along the pore", canvas.width - m.right,
      canvas.height - m.bottom + 4 * m.ratio);
    // The two radii HOLE marks.
    context.strokeStyle = muted;
    context.lineWidth = m.ratio;
    [[data.narrow, [2, 3]], [data.one_water, [6, 3]]].forEach(function (mark) {
      context.setLineDash(mark[1].map(function (v) { return v * m.ratio; }));
      context.beginPath();
      context.moveTo(m.left, y(mark[0]));
      context.lineTo(canvas.width - m.right, y(mark[0]));
      context.stroke();
    });
    context.setLineDash([]);
    // The range over the frames, then the mean.
    var line = token("--hue-cyan", "#00aed8");
    context.fillStyle = line;
    context.globalAlpha = 0.22;
    context.beginPath();
    var begun = false;
    heights.forEach(function (h, k) {
      if (data.high[k] == null) return;
      if (begun) context.lineTo(x(h), y(data.high[k]));
      else { context.moveTo(x(h), y(data.high[k])); begun = true; }
    });
    for (var k = heights.length - 1; k >= 0; k -= 1) {
      if (data.low[k] != null) context.lineTo(x(heights[k]), y(data.low[k]));
    }
    context.closePath();
    context.fill();
    context.globalAlpha = 1;
    var trace = function (values, colour, wide) {
      context.strokeStyle = colour;
      context.lineWidth = wide * m.ratio;
      context.beginPath();
      var pen = false;
      heights.forEach(function (h, i) {
        if (values[i] == null) { pen = false; return; }
        if (pen) context.lineTo(x(h), y(values[i]));
        else { context.moveTo(x(h), y(values[i])); pen = true; }
      });
      context.stroke();
    };
    trace(data.mean, line, 1.5);
    if (now) trace(now, token("--accent-orange", "#ffb86b"), 1.25);
    say(now);
  }

  /* The frame shown, named as the Viewer names it (molecule-viewer.js). */
  function shownName(fallback) {
    if (state.name) return state.name;
    var v = viewer();
    var named = v && v.movie && v.movie.shownFrameName ? v.movie.shownFrameName() : "";
    return named || "frame " + fallback;
  }

  function say(now) {
    var said = byId("pore-now");
    if (!said) return;
    if (!state.data.profiles) {
      said.textContent = "The frames played are not the frames analysed, so no frame's own "
        + "profile is plotted.";
      return;
    }
    if (!now) {
      said.textContent = "The frame shown was opened from the trajectory and is not one of "
        + "the frames played; its pore is not rendered.";
      return;
    }
    var radii = finite(now);
    if (!radii.length) {
      said.textContent = "No pore was found in the frame shown.";
      return;
    }
    var least = Math.min.apply(null, radii);
    var named = shownName(state.frame);
    said.textContent = named.charAt(0).toUpperCase() + named.slice(1) + ": narrowest "
      + least.toFixed(2) + " Å, " + (least < state.data.narrow ? "too narrow for a water."
        : least < state.data.one_water ? "room for one water abreast." : "wider than one water.");
  }

  function key() {
    var list = byId("pore-key");
    if (!list) return;
    list.replaceChildren();
    [["var(--hue-cyan, #00aed8)", "Mean over the frames, with the range"],
     ["var(--accent-orange, #ffb86b)", "The frame shown"],
     ["var(--text-muted, #85858f)", "Dashed: " + state.data.narrow.toFixed(2)
       + " Å, under which a water does not fit, and " + state.data.one_water.toFixed(2)
       + " Å, up to which one fits abreast"]].forEach(function (row) {
      var item = document.createElement("li");
      item.className = "occ-site";
      var swatch = document.createElement("span");
      swatch.className = "occ-site-swatch";
      swatch.style.background = row[0];
      var said = document.createElement("span");
      said.textContent = row[1];
      item.append(swatch, said);
      list.append(item);
    });
  }

  function ask() {
    if (state.data || state.asking) return state.asking || Promise.resolve(state.data);
    state.asking = fetch("/api/pore-profile", { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .catch(function () { return { ok: false, reason: "The server did not answer." }; })
      .then(function (said) {
        state.asking = null;
        if (said && said.ok) state.data = said;
        return said;
      });
    return state.asking;
  }

  /* The frames loaded, superposed on the backbone to the first frame, where
   * they are not fitted as the pore is placed. */
  async function framesFitted() {
    var v = viewer();
    if (!(await v.movie.frames())) return false;
    if (!v.placedFitAsPlayed()) {
      v.STATE.superposedTo = "first";
      v.STATE.smoothedOver = 1;
      var to = byId("traj-superpose-to");
      if (to) to.value = "first";
      var smooth = byId("traj-smooth");
      if (smooth) smooth.value = "1";
      await v.superpose("backbone");
    }
    return v.placedFitAsPlayed();
  }

  async function show(on) {
    var shown = engine();
    var note = byId("pore-note");
    if (!shown) return;
    if (!on) {
      await shown.removeVolume(KEY);
      return;
    }
    if (!(await framesFitted())) {
      byId("pore-shown").checked = false;
      note.textContent = "The pore is shown on frames superposed on the backbone to the "
        + "first frame, which they could not be.";
      return;
    }
    try {
      // The surface is where a point is on a mean sphere: depth zero.
      await shown.showVolume(KEY, "/structure/pore.dx", 0, COLOUR, 0.5);
      await shown.setPlacedAside(!viewer().placedFitAsPlayed());
    } catch (error) {
      byId("pore-shown").checked = false;
      note.textContent = "The pore could not be rendered: "
        + (error && error.message ? error.message : error);
    }
  }

  /* Offered where the study's `pore_profile` analysis ran and its frames
   * are played. */
  async function offer() {
    var v = viewer();
    var section = byId("side-pore");
    if (!section || !v || !v.STATE) return;
    var payload = v.STATE.playbackPayload;
    if (!(payload && payload.playback_available)) { section.hidden = true; return; }
    var said = await ask();
    var analysed = !!(said && (said.ok
      || !/^The study has no pore profile/.test(String(said.reason || ""))));
    section.hidden = !analysed;
    if (!analysed) return;
    byId("pore-note").textContent = said.ok ? said.said : (said.not_read || said.reason || "");
    byId("pore-shown").disabled = !said.ok;
    if (said.ok) {
      state.frame = v.movie ? v.movie.shownPlayedFrame() : 0;
      key();
      draw();
    }
  }

  function wire() {
    var section = byId("side-pore");
    if (!section) return;
    section.addEventListener("toggle", function () { if (section.open && state.data) draw(); });
    byId("pore-shown").addEventListener("change", function (event) {
      show(event.target.checked);
    });
    window.addEventListener("dashboard:frame-shown", function (event) {
      var played = event.detail ? event.detail.playedIndex : undefined;
      // A frame opened from the trajectory between the frames played has
      // no profile here (null): never a neighbour's.
      state.frame = played === undefined ? (Number(event.detail && event.detail.index) || 0)
        : played;
      state.name = event.detail && event.detail.name || "";
      if (state.data) draw();
    });
    window.addEventListener("dashboard:viewer-rendered", function () { offer(); });
    window.addEventListener("dashboard:frames-ready", function () {
      offer();
      if (byId("pore-shown").checked) show(true);
    });
    window.addEventListener("resize", function () { if (state.data && section.open) draw(); });
    window.addEventListener("dashboard:run-changed", function () {
      state.data = null;
      state.asking = null;
      byId("pore-shown").checked = false;
      section.hidden = true;
      byId("pore-note").textContent = "";
      byId("pore-now").textContent = "";
    });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", wire);
  else wire();

  window.FastMDXPore = { offer: offer, show: show, state: state };
}());
