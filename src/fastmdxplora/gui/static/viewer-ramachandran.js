/* A residue's backbone dihedrals over the frames played, on a Ramachandran
 * plot tied to the frame shown (gui/backbone_angles.py).
 *
 * Behind, in grey, where every residue's φ and ψ fell over every frame
 * played; over it, each residue at the frame shown, moving as the frames
 * play (glycine hollow); and the residue chosen, its path over the frames,
 * faint at the first frames and solid at the last, with a ring at the frame
 * shown. A key under the plot shows each mark beside what it is, the
 * residue chosen named in it. A click on the path shows that frame; a click
 * on a dot follows that residue and selects it in the structure; a residue
 * selected in the structure or the sequence is followed. The angles are
 * computed once, when the section is first opened.
 */
(function () {
  "use strict";

  var MISSING = -32768;
  var BINS = 72;
  var state = { data: null, asking: null, phi: null, psi: null, chosen: null, frame: 0,
                path: null, dots: null };

  function byId(id) { return document.getElementById(id); }

  function viewer() { return window.FastMDXMoleculeViewer; }

  function say(text) {
    var note = byId("rama-note");
    if (note) note.textContent = text || "";
  }

  function token(name, fallback) {
    var value = "";
    try {
      value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    } catch (e) { /* no styles yet */ }
    return value || fallback;
  }

  /* Tenths of a degree as 16-bit integers, little-endian, in base 64. */
  function unpack(text) {
    var raw = atob(text);
    var view = new DataView(new ArrayBuffer(raw.length));
    for (var i = 0; i < raw.length; i += 1) view.setUint8(i, raw.charCodeAt(i));
    var out = new Float32Array(raw.length / 2);
    for (var k = 0; k < out.length; k += 1) {
      var value = view.getInt16(2 * k, true);
      out[k] = value === MISSING ? NaN : value / 10;
    }
    return out;
  }

  function angle(values, frame, residue) {
    return values[frame * state.data.residues.length + residue];
  }

  function nameOf(residue) {
    return (residue[0] ? residue[0] + " " : "") + residue[3] + " " + residue[1] + (residue[2] || "");
  }

  /* -180 to 180 on both axes, φ across and ψ up. */
  function geometry(canvas) {
    var size = canvas.width;
    var left = Math.round(size * 0.14);
    var bottom = Math.round(size * 0.12);
    var inner = size - left - Math.round(size * 0.04);
    var top = size - bottom - inner;
    return {
      left: left, top: top, inner: inner,
      x: function (phi) { return left + (phi + 180) / 360 * inner; },
      y: function (psi) { return top + (180 - psi) / 360 * inner; },
    };
  }

  function fit(canvas) {
    var ratio = window.devicePixelRatio || 1;
    var width = Math.max(200, Math.round((canvas.clientWidth || 300) * ratio));
    if (canvas.width !== width) {
      canvas.width = width;
      canvas.height = width;
    }
  }

  function background(g, context) {
    var d = state.data;
    var counts = new Float32Array(BINS * BINS);
    var most = 0;
    for (var i = 0; i < state.phi.length; i += 1) {
      var phi = state.phi[i];
      var psi = state.psi[i];
      if (!isFinite(phi) || !isFinite(psi)) continue;
      var bx = Math.min(BINS - 1, Math.floor((phi + 180) / 360 * BINS));
      var by = Math.min(BINS - 1, Math.floor((psi + 180) / 360 * BINS));
      var k = by * BINS + bx;
      counts[k] += 1;
      if (counts[k] > most) most = counts[k];
    }
    if (!most || !d) return;
    var cell = g.inner / BINS;
    var shade = token("--text-muted", "#85858f");
    context.fillStyle = shade;
    for (var y = 0; y < BINS; y += 1) {
      for (var x = 0; x < BINS; x += 1) {
        var count = counts[y * BINS + x];
        if (!count) continue;
        context.globalAlpha = 0.12 + 0.6 * Math.log(1 + count) / Math.log(1 + most);
        context.fillRect(g.left + x * cell, g.top + (BINS - 1 - y) * cell, cell + 0.5, cell + 0.5);
      }
    }
    context.globalAlpha = 1;
  }

  function axes(g, context) {
    var ratio = g.inner / 260;
    context.strokeStyle = token("--border-subtle", "rgba(255,255,255,0.12)");
    context.fillStyle = token("--text-muted", "#85858f");
    context.lineWidth = 1;
    context.font = Math.round(10 * Math.max(1, ratio)) + "px sans-serif";
    context.textAlign = "center";
    context.textBaseline = "top";
    [-180, -90, 0, 90, 180].forEach(function (tick) {
      var x = g.x(tick);
      var y = g.y(tick);
      context.beginPath();
      context.moveTo(x, g.top);
      context.lineTo(x, g.top + g.inner);
      context.moveTo(g.left, y);
      context.lineTo(g.left + g.inner, y);
      context.stroke();
      context.fillText(String(tick), x, g.top + g.inner + 3);
    });
    context.textAlign = "right";
    context.textBaseline = "middle";
    [-180, -90, 0, 90, 180].forEach(function (tick) {
      context.fillText(String(tick), g.left - 4, g.y(tick));
    });
    context.textAlign = "center";
    context.textBaseline = "bottom";
    context.fillText("φ (°)", g.left + g.inner / 2, g.top + g.inner + 6 + 24 * Math.max(1, ratio));
    context.save();
    context.translate(12 * Math.max(1, ratio), g.top + g.inner / 2);
    context.rotate(-Math.PI / 2);
    context.textBaseline = "middle";
    context.fillText("ψ (°)", 0, 0);
    context.restore();
  }

  function draw() {
    var canvas = byId("rama-canvas");
    if (!canvas || !state.data) return;
    fit(canvas);
    var context = canvas.getContext("2d");
    var g = geometry(canvas);
    context.clearRect(0, 0, canvas.width, canvas.height);
    background(g, context);
    axes(g, context);
    var d = state.data;
    var accent = token("--accent-cyan", "#63e6ff");
    var chosenColour = token("--accent-orange", "#ffb86b");
    var dot = Math.max(2, canvas.width / 150);
    state.dots = [];
    var frame = Math.min(state.frame, d.frames - 1);
    context.lineWidth = Math.max(1, dot / 2);
    for (var r = 0; r < d.residues.length; r += 1) {
      var phi = angle(state.phi, frame, r);
      var psi = angle(state.psi, frame, r);
      if (!isFinite(phi) || !isFinite(psi)) continue;
      var x = g.x(phi);
      var y = g.y(psi);
      state.dots.push({ x: x, y: y, residue: r });
      context.beginPath();
      context.arc(x, y, dot, 0, 2 * Math.PI);
      if (d.residues[r][3] === "GLY") {
        context.strokeStyle = accent;
        context.stroke();
      } else {
        context.fillStyle = accent;
        context.fill();
      }
    }
    state.path = [];
    if (state.chosen == null) return;
    var previous = null;
    for (var f = 0; f < d.frames; f += 1) {
      var a = angle(state.phi, f, state.chosen);
      var b = angle(state.psi, f, state.chosen);
      if (!isFinite(a) || !isFinite(b)) { previous = null; continue; }
      var point = { x: g.x(a), y: g.y(b), frame: f, phi: a, psi: b };
      state.path.push(point);
      context.globalAlpha = 0.35 + 0.65 * f / Math.max(1, d.frames - 1);
      context.strokeStyle = chosenColour;
      context.fillStyle = chosenColour;
      // No line across the plot where an angle passes ±180°.
      if (previous && Math.abs(previous.phi - a) < 180 && Math.abs(previous.psi - b) < 180) {
        context.beginPath();
        context.moveTo(previous.x, previous.y);
        context.lineTo(point.x, point.y);
        context.stroke();
      }
      context.beginPath();
      context.arc(point.x, point.y, dot * 0.8, 0, 2 * Math.PI);
      context.fill();
      previous = point;
    }
    context.globalAlpha = 1;
    var here = state.path.find(function (p) { return p.frame === frame; });
    if (here) {
      context.lineWidth = Math.max(2, dot * 0.9);
      context.strokeStyle = token("--text-primary", "#f2f2f4");
      context.beginPath();
      context.arc(here.x, here.y, dot * 2.6, 0, 2 * Math.PI);
      context.stroke();
    }
    var residue = d.residues[state.chosen];
    say(here ? nameOf(residue) + " in frame " + frame + ": φ " + here.phi.toFixed(1)
      + "°, ψ " + here.psi.toFixed(1) + "°." : nameOf(residue) + " has no φ or ψ (a chain's end).");
  }

  function list() {
    var select = byId("rama-residue");
    var none = document.createElement("option");
    none.value = "";
    none.textContent = "None chosen";
    select.replaceChildren(none);
    state.data.residues.forEach(function (residue, i) {
      var option = document.createElement("option");
      option.value = String(i);
      option.textContent = nameOf(residue);
      select.append(option);
    });
    select.value = state.chosen == null ? "" : String(state.chosen);
  }

  /* The key's lines for the residue chosen, named; or how to choose one. */
  function keyed() {
    var key = byId("rama-key");
    if (!key) return;
    var chosen = state.chosen != null && state.data ? nameOf(state.data.residues[state.chosen]) : "";
    key.querySelectorAll(".rama-key-chosen").forEach(function (span) { span.textContent = chosen; });
    key.querySelectorAll("[data-key='path'], [data-key='ring']").forEach(function (li) {
      li.hidden = !chosen;
    });
    var ask = key.querySelector("[data-key='choose']");
    if (ask) ask.hidden = !!chosen;
  }

  function choose(index, select) {
    state.chosen = index == null || index < 0 ? null : index;
    var listed = byId("rama-residue");
    if (listed) listed.value = state.chosen == null ? "" : String(state.chosen);
    keyed();
    draw();
    if (!select || state.chosen == null) return;
    var v = viewer();
    var engine = v && v.STATE && v.STATE.engine;
    var r = state.data.residues[state.chosen];
    var residue = { chain: r[0], resi: r[1], icode: r[2], resn: r[3] };
    if (engine && v.selectResidues) {
      v.selectResidues([residue], engine.atomsOfResidues([residue]), { announce: true });
    }
  }

  /* The residue selected in the structure or the sequence, where one is. */
  function followTheSelection() {
    var v = viewer();
    var selected = v && v.STATE && v.STATE.selection;
    if (!state.data || !selected || selected.residues.length !== 1) return;
    var wanted = selected.residues[0];
    var found = state.data.residues.findIndex(function (r) {
      return r[1] === wanted.resi && (r[2] || "") === (wanted.icode || "")
        && (r[0] || null) === (wanted.chain || null);
    });
    if (found >= 0 && found !== state.chosen) choose(found, false);
  }

  async function load() {
    if (state.data || state.asking) return state.asking;
    say("Computing each residue's φ and ψ in the frames played…");
    state.asking = fetch("/api/backbone-angles", { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .catch(function () { return { ok: false, reason: "The server did not answer." }; })
      .then(function (said) {
        state.asking = null;
        if (!said.ok) {
          say(said.reason || "The angles could not be computed.");
          return said;
        }
        state.data = said;
        state.phi = unpack(said.phi);
        state.psi = unpack(said.psi);
        var v = viewer();
        state.frame = v && v.movie ? v.movie.shownFrame() : 0;
        list();
        say(said.said);
        followTheSelection();
        draw();
        return said;
      });
    return state.asking;
  }

  function nearest(points, x, y, reach) {
    var best = null;
    var bestDistance = reach * reach;
    (points || []).forEach(function (p) {
      var distance = (p.x - x) * (p.x - x) + (p.y - y) * (p.y - y);
      if (distance <= bestDistance) {
        best = p;
        bestDistance = distance;
      }
    });
    return best;
  }

  function clicked(event) {
    var canvas = byId("rama-canvas");
    var box = canvas.getBoundingClientRect();
    var scale = canvas.width / box.width;
    var x = (event.clientX - box.left) * scale;
    var y = (event.clientY - box.top) * scale;
    var reach = Math.max(6, canvas.width / 40);
    var onPath = nearest(state.path, x, y, reach);
    if (onPath) {
      window.dispatchEvent(new CustomEvent("dashboard:trajectory-seek",
        { detail: { frame: onPath.frame } }));
      return;
    }
    var dot = nearest(state.dots, x, y, reach);
    if (dot) choose(dot.residue, true);
  }

  function offer() {
    var v = viewer();
    var payload = v && v.STATE && v.STATE.playbackPayload;
    var section = byId("side-rama");
    if (section) section.hidden = !(payload && payload.playback_available);
    if (section && !section.hidden && section.open) load();
  }

  function wire() {
    var section = byId("side-rama");
    if (!section) return;
    section.addEventListener("toggle", function () { if (section.open) load(); });
    byId("rama-residue").addEventListener("change", function (event) {
      var value = event.target.value;
      choose(value === "" ? null : Number(value), true);
    });
    byId("rama-canvas").addEventListener("click", clicked);
    window.addEventListener("dashboard:frame-shown", function (event) {
      state.frame = Number(event.detail && event.detail.index) || 0;
      if (state.data && section.open) draw();
    });
    window.addEventListener("dashboard:selection-changed", followTheSelection);
    window.addEventListener("dashboard:viewer-rendered", offer);
    window.addEventListener("dashboard:frames-ready", offer);
    window.FastMDXDashboard && window.FastMDXDashboard.on("playback-ready", offer);
    window.addEventListener("resize", function () { if (state.data && section.open) draw(); });
    window.addEventListener("dashboard:run-changed", function () {
      state.data = null;
      state.chosen = null;
      state.phi = state.psi = null;
      section.hidden = true;
      keyed();
      say("");
    });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", wire);
  else wire();

  window.FastMDXRamachandran = { load: load, choose: choose, state: state, draw: draw };
}());
