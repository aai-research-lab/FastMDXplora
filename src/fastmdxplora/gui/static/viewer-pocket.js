/* The room in the ligand's pocket over the frames played, and the pocket
 * rendered at the frame shown (gui/pocket_volume.py).
 *
 * The pocket's volume in each frame is plotted, with its mean, a line at the
 * frame shown and its value said; a click on the plot shows that frame.
 * **Show the pocket** renders the pocket's empty points in the frame shown
 * as a surface, the frames superposed on the pocket to their first frame,
 * where the points are placed; it follows the frame shown, the last asked
 * for once the one before has arrived. The volumes are computed when the
 * section is first opened.
 */
(function () {
  "use strict";

  var KEY = "pocket";
  var COLOUR = 0x009e73;
  var state = { data: null, asking: null, frame: 0, shownFrame: null, rendering: false,
                wanted: null };

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

  function ligand() {
    var v = viewer();
    return v ? (v.STATE.ligandResname || v.ligandResnames()[0] || "") : "";
  }

  function cutoff() {
    var v = viewer();
    return v && v.STATE.pocketCutoff ? v.STATE.pocketCutoff : 5;
  }

  function query(extra) {
    var asked = new URLSearchParams({ ligand: ligand(), cutoff: String(cutoff()) });
    Object.keys(extra || {}).forEach(function (key) { asked.set(key, String(extra[key])); });
    return asked.toString();
  }

  function margins(canvas) {
    var ratio = canvas.width / Math.max(1, canvas.clientWidth || canvas.width);
    return { left: 44 * ratio, right: 6 * ratio, top: 6 * ratio, bottom: 16 * ratio, ratio: ratio };
  }

  /* Where a frame is across the plot, in the canvas's own pixels. */
  function xOf(canvas, frame) {
    var m = margins(canvas);
    var last = Math.max(1, state.data.frames - 1);
    return m.left + (canvas.width - m.left - m.right) * frame / last;
  }

  function draw() {
    var canvas = byId("pocket-canvas");
    if (!canvas || !state.data) return;
    var ratio = window.devicePixelRatio || 1;
    var width = Math.max(200, Math.round((canvas.clientWidth || 300) * ratio));
    var height = Math.round((canvas.clientHeight || 110) * ratio);
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
    }
    var m = margins(canvas);
    var context = canvas.getContext("2d");
    context.clearRect(0, 0, canvas.width, canvas.height);
    var volumes = state.data.volumes;
    var low = Math.min.apply(null, volumes);
    var high = Math.max.apply(null, volumes);
    if (high - low < 1) { low -= 1; high += 1; }
    var y = function (v) {
      return m.top + (canvas.height - m.top - m.bottom) * (high - v) / (high - low);
    };
    context.font = Math.round(10 * m.ratio) + "px sans-serif";
    context.fillStyle = token("--text-muted", "#85858f");
    context.textAlign = "right";
    context.textBaseline = "middle";
    context.fillText(Math.round(high) + " Å³", m.left - 4 * m.ratio, y(high));
    context.fillText(Math.round(low) + " Å³", m.left - 4 * m.ratio, y(low));
    context.strokeStyle = token("--accent-orange", "#ffb86b");
    context.setLineDash([4 * m.ratio, 3 * m.ratio]);
    context.beginPath();
    context.moveTo(m.left, y(state.data.mean));
    context.lineTo(canvas.width - m.right, y(state.data.mean));
    context.stroke();
    context.setLineDash([]);
    context.strokeStyle = token("--accent-cyan", "#2698ba");
    context.lineWidth = 1.5 * m.ratio;
    context.beginPath();
    volumes.forEach(function (v, k) {
      if (k === 0) context.moveTo(xOf(canvas, k), y(v));
      else context.lineTo(xOf(canvas, k), y(v));
    });
    context.stroke();
    var frame = Math.min(state.frame, volumes.length - 1);
    context.strokeStyle = token("--text-primary", "#f2f2f4");
    context.lineWidth = m.ratio;
    context.beginPath();
    context.moveTo(xOf(canvas, frame), m.top);
    context.lineTo(xOf(canvas, frame), canvas.height - m.bottom);
    context.stroke();
    var time = state.data.times && state.data.times[frame];
    byId("pocket-now").textContent = "Frame " + frame
      + (time != null ? " (" + Number(time).toFixed(3) + " ns)" : "") + ": "
      + Math.round(volumes[frame]).toLocaleString() + " Å³.";
  }

  function load() {
    if (state.data || state.asking) return state.asking;
    byId("pocket-note").textContent = "Finding the pocket in each frame played…";
    state.asking = fetch("/api/pocket-volume?" + query(), { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .catch(function () { return { ok: false, reason: "The server did not answer." }; })
      .then(function (said) {
        state.asking = null;
        byId("pocket-note").textContent = said.ok ? said.said : (said.reason || "");
        if (!said.ok) return said;
        state.data = said;
        var v = viewer();
        state.frame = v && v.movie ? v.movie.shownFrame() : 0;
        draw();
        return said;
      });
    return state.asking;
  }

  /* The frames superposed on the pocket to their first frame, where the
   * pocket's points are placed. */
  async function fitted() {
    var v = viewer();
    var S = v.STATE;
    if (!(await v.movie.frames())) return false;
    if (S.superposed === "pocket" && (S.appliedTo || "first") === "first"
        && (S.appliedSmooth || 1) === 1) return true;
    S.superposedTo = "first";
    S.smoothedOver = 1;
    var to = byId("traj-superpose-to");
    if (to) to.value = "first";
    var smooth = byId("traj-smooth");
    if (smooth) smooth.value = "1";
    var select = byId("traj-superpose");
    if (select) select.value = "pocket";
    await v.superpose("pocket");
    return S.superposed === "pocket";
  }

  /* The pocket of the frame shown; the last frame asked for is rendered once
   * the one before is. */
  async function render(frame) {
    state.wanted = frame;
    if (state.rendering) return;
    state.rendering = true;
    try {
      while (state.wanted != null) {
        var now = state.wanted;
        state.wanted = null;
        if (!byId("pocket-shown").checked) break;
        await engine().showVolume(KEY, "/structure/pocket.dx?" + query({ frame: now }), 0.5,
          COLOUR, 0.55);
        state.shownFrame = now;
      }
    } catch (error) {
      byId("pocket-note").textContent = "The pocket could not be rendered: "
        + (error && error.message ? error.message : error);
    } finally {
      state.rendering = false;
    }
  }

  async function show(on) {
    var shown = engine();
    if (!shown) return;
    if (!on) {
      state.wanted = null;
      state.shownFrame = null;
      await shown.removeVolume(KEY);
      return;
    }
    if (!(await fitted())) {
      byId("pocket-shown").checked = false;
      byId("pocket-note").textContent = "The pocket is shown on frames superposed on it, "
        + "which they could not be.";
      return;
    }
    await render(state.frame);
  }

  function offer() {
    var v = viewer();
    var payload = v && v.STATE && v.STATE.playbackPayload;
    var section = byId("side-pocket");
    if (!section) return;
    section.hidden = !(payload && payload.playback_available && v.ligandResnames().length);
    if (!section.hidden && section.open) load();
  }

  function wire() {
    var section = byId("side-pocket");
    if (!section) return;
    section.addEventListener("toggle", function () { if (section.open) load(); });
    byId("pocket-shown").addEventListener("change", function (event) {
      show(event.target.checked);
    });
    byId("pocket-canvas").addEventListener("click", function (event) {
      if (!state.data) return;
      var canvas = byId("pocket-canvas");
      var box = canvas.getBoundingClientRect();
      var x = (event.clientX - box.left) * canvas.width / box.width;
      var m = margins(canvas);
      var share = (x - m.left) / (canvas.width - m.left - m.right);
      var frame = Math.max(0, Math.min(state.data.frames - 1,
        Math.round(share * (state.data.frames - 1))));
      window.dispatchEvent(new CustomEvent("dashboard:trajectory-seek", { detail: { frame: frame } }));
    });
    window.addEventListener("dashboard:frame-shown", function (event) {
      state.frame = Number(event.detail && event.detail.index) || 0;
      if (state.data) draw();
      if (byId("pocket-shown").checked) render(state.frame);
    });
    window.addEventListener("dashboard:viewer-rendered", offer);
    window.addEventListener("dashboard:frames-ready", offer);
    window.FastMDXDashboard && window.FastMDXDashboard.on("playback-ready", offer);
    window.addEventListener("resize", function () { if (state.data && section.open) draw(); });
    window.addEventListener("dashboard:run-changed", function () {
      state.data = null;
      state.shownFrame = null;
      byId("pocket-shown").checked = false;
      section.hidden = true;
      byId("pocket-note").textContent = "";
    });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", wire);
  else wire();

  window.FastMDXPocketVolume = {
    load: load, show: show, state: state,
    // Where a frame is across the plot, in CSS pixels from its left.
    xOfFrame: function (frame) {
      var canvas = byId("pocket-canvas");
      return xOf(canvas, frame) * canvas.clientWidth / canvas.width;
    },
  };
}());
