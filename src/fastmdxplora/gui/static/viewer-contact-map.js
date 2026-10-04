/* Which of the protein's residues touch which over the frames played, and a
 * pair followed in the structure (gui/contact_map.py).
 *
 * The map gives each pair of residues the share of frames played they were
 * in contact in (heavy atoms within 4.5 Å), darker the more often; or, for
 * two states the cluster analysis found, the share in one state's frames
 * less the other's, red where the first holds the pair more and blue where
 * less. Pointing at a cell names the pair; a click follows it: the two
 * residues are selected in the structure and a dashed line joins their
 * closest heavy atoms in the frame shown, as the frames play, with how far
 * apart they are. The map is computed when the section is first opened.
 */
(function () {
  "use strict";

  var GROUP = "contact-pair";
  var LINE = 0xe69f00;
  var state = { data: null, asking: null, values: null, pair: null, pairData: null, frame: 0,
                states: null, image: null };

  function byId(id) { return document.getElementById(id); }

  function viewer() { return window.FastMDXMoleculeViewer; }

  function engine() {
    var v = viewer();
    return v && v.STATE && v.STATE.framesRendered ? v.STATE.engine : null;
  }

  function say(text) {
    var note = byId("cmap-note");
    if (note) note.textContent = text || "";
  }

  function token(name, fallback) {
    var value = "";
    try {
      value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    } catch (e) { /* no styles yet */ }
    return value || fallback;
  }

  function rgb(colour) {
    var probe = document.createElement("span");
    probe.style.color = colour;
    document.body.appendChild(probe);
    var parts = getComputedStyle(probe).color.match(/\d+(\.\d+)?/g) || [0, 0, 0];
    probe.remove();
    return [Number(parts[0]), Number(parts[1]), Number(parts[2])];
  }

  function nameOf(residue) {
    return (residue[0] ? residue[0] + " " : "") + residue[3] + " " + residue[1] + (residue[2] || "");
  }

  function percent(value) { return Math.round(value * 100) + "%"; }

  function geometry(canvas) {
    var size = canvas.width;
    var left = Math.round(size * 0.12);
    var top = Math.round(size * 0.03);
    var inner = size - left - top;
    return { left: left, top: top, inner: inner, cell: inner / state.data.residues.length };
  }

  /* The map as one picture, a pixel a pair, both halves. */
  function picture() {
    var n = state.data.residues.length;
    var image = new ImageData(n, n);
    var compared = !!state.data.compared;
    var ink = rgb(token("--text-primary", "#f2f2f4"));
    var red = [213, 94, 0];
    var blue = [0, 114, 178];
    var p = state.data.pairs;
    for (var k = 0; k < p.v.length; k += 1) {
      var value = p.v[k];
      var colour = compared ? (value > 0 ? red : blue) : ink;
      var alpha = Math.round(255 * Math.min(1, Math.abs(value)));
      [[p.i[k], p.j[k]], [p.j[k], p.i[k]]].forEach(function (cell) {
        var at = 4 * (cell[1] * n + cell[0]);
        image.data[at] = colour[0];
        image.data[at + 1] = colour[1];
        image.data[at + 2] = colour[2];
        image.data[at + 3] = alpha;
      });
    }
    var holder = document.createElement("canvas");
    holder.width = n;
    holder.height = n;
    holder.getContext("2d").putImageData(image, 0, 0);
    state.image = holder;
  }

  function draw() {
    var canvas = byId("cmap-canvas");
    if (!canvas || !state.data) return;
    var ratio = window.devicePixelRatio || 1;
    var width = Math.max(200, Math.round((canvas.clientWidth || 300) * ratio));
    if (canvas.width !== width) {
      canvas.width = width;
      canvas.height = width;
    }
    var g = geometry(canvas);
    var context = canvas.getContext("2d");
    context.clearRect(0, 0, canvas.width, canvas.height);
    context.strokeStyle = token("--border-subtle", "rgba(255,255,255,0.12)");
    context.lineWidth = 1;
    context.strokeRect(g.left, g.top, g.inner, g.inner);
    context.imageSmoothingEnabled = false;
    context.drawImage(state.image, g.left, g.top, g.inner, g.inner);
    // Where a chain ends, a line across; residue numbers along the side.
    var residues = state.data.residues;
    context.fillStyle = token("--text-muted", "#85858f");
    context.font = Math.round(9 * Math.max(1, canvas.width / 300)) + "px sans-serif";
    context.textAlign = "right";
    context.textBaseline = "middle";
    var step = Math.max(1, Math.round(residues.length / 6));
    for (var r = 0; r < residues.length; r += 1) {
      if (r > 0 && residues[r][0] !== residues[r - 1][0]) {
        var at = g.left + r * g.cell;
        context.beginPath();
        context.moveTo(at, g.top);
        context.lineTo(at, g.top + g.inner);
        context.moveTo(g.left, g.top + r * g.cell);
        context.lineTo(g.left + g.inner, g.top + r * g.cell);
        context.stroke();
      }
      if (r % step === 0) {
        context.textAlign = "right";
        context.textBaseline = "middle";
        context.fillText(String(residues[r][1]), g.left - 3, g.top + (r + 0.5) * g.cell);
        context.textAlign = "center";
        context.textBaseline = "top";
        context.fillText(String(residues[r][1]), g.left + (r + 0.5) * g.cell,
          g.top + g.inner + 3);
      }
    }
    if (state.pair) {
      context.strokeStyle = "#e69f00";
      context.lineWidth = Math.max(2, g.cell);
      [[state.pair[0], state.pair[1]], [state.pair[1], state.pair[0]]].forEach(function (cell) {
        var size = Math.max(6, 3 * g.cell);
        context.strokeRect(g.left + (cell[0] + 0.5) * g.cell - size / 2,
          g.top + (cell[1] + 0.5) * g.cell - size / 2, size, size);
      });
    }
  }

  function valueOf(i, j) {
    var a = Math.min(i, j);
    var b = Math.max(i, j);
    return state.values.get(a * state.data.residues.length + b) || 0;
  }

  function cellAt(event) {
    var canvas = byId("cmap-canvas");
    var box = canvas.getBoundingClientRect();
    var scale = canvas.width / box.width;
    var g = geometry(canvas);
    var i = Math.floor(((event.clientX - box.left) * scale - g.left) / g.cell);
    var j = Math.floor(((event.clientY - box.top) * scale - g.top) / g.cell);
    var n = state.data.residues.length;
    return i >= 0 && j >= 0 && i < n && j < n ? [i, j] : null;
  }

  function described(i, j) {
    var residues = state.data.residues;
    var value = valueOf(i, j);
    var said = nameOf(residues[i]) + " and " + nameOf(residues[j]) + ": ";
    if (state.data.compared) {
      var c = state.data.compared;
      return said + (value === 0 ? "in contact as often in both states"
        : (value > 0 ? "in contact more often" : "in contact less often") + " in state " + c[1]
          + " than in state " + c[0] + ", by " + percent(Math.abs(value)) + " of frames");
    }
    return said + "in contact in " + percent(value) + " of frames";
  }

  function loaded(said) {
    state.data = said;
    state.values = new Map();
    var n = said.residues.length;
    for (var k = 0; k < said.pairs.v.length; k += 1) {
      state.values.set(said.pairs.i[k] * n + said.pairs.j[k], said.pairs.v[k]);
    }
    picture();
    draw();
    say(said.said);
    byId("cmap-all").hidden = !said.compared;
  }

  function ask(query) {
    say(query ? "Comparing the states' contacts…" : "Finding the contacts in the frames played…");
    state.asking = fetch("/api/contact-map" + (query ? "?" + query : ""), { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .catch(function () { return { ok: false, reason: "The server did not answer." }; })
      .then(function (said) {
        state.asking = null;
        if (!said.ok) say(said.reason || "No contact map.");
        else loaded(said);
        return said;
      });
    return state.asking;
  }

  function offerStates() {
    return fetch("/api/states", { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .catch(function () { return { ok: false }; })
      .then(function (said) {
        state.states = said.ok ? said : null;
        var row = byId("cmap-states-row");
        row.hidden = !(said.ok && said.states.length > 1);
        if (row.hidden) return;
        ["cmap-first", "cmap-second"].forEach(function (id, k) {
          var select = byId(id);
          select.replaceChildren.apply(select, said.states.map(function (found) {
            var option = document.createElement("option");
            option.value = String(found.state);
            option.textContent = String(found.state) + " (" + percent(found.share) + ")";
            return option;
          }));
          select.selectedIndex = k === 0 ? 0 : 1;
        });
      });
  }

  function load() {
    if (state.data || state.asking) return state.asking;
    offerStates();
    return ask(null);
  }

  /* The pair followed, in the structure at the frame shown. */
  function showPair() {
    var shown = engine();
    if (!shown || !shown.showInteractions) return;
    var d = state.pairData;
    if (!d) {
      shown.showInteractions([], GROUP).catch(function () {});
      return;
    }
    var frame = Math.min(state.frame, d.atoms.length - 1);
    var atoms = d.atoms[frame];
    shown.showInteractions([{ a: atoms[0], b: atoms[1], colour: LINE }], GROUP)
      .catch(function () {});
    say(nameOf(d.a) + " and " + nameOf(d.b) + ": in contact in " + percent(d.share)
      + " of frames; their closest heavy atoms " + d.angstrom[frame].toFixed(1)
      + " Å apart in frame " + frame + ".");
  }

  async function follow(i, j) {
    if (i === j) return;
    state.pair = [i, j];
    draw();
    var said = await fetch("/api/contact-pair?a=" + i + "&b=" + j, { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .catch(function () { return { ok: false, reason: "The server did not answer." }; });
    if (!state.pair || state.pair[0] !== i || state.pair[1] !== j) return;
    if (!said.ok) {
      say(said.reason || "The pair could not be followed.");
      return;
    }
    state.pairData = said;
    byId("cmap-pair-row").hidden = false;
    var v = viewer();
    var shown = v && v.STATE && v.STATE.engine;
    if (shown && v.selectResidues) {
      var residues = [said.a, said.b].map(function (r) {
        return { chain: r[0], resi: r[1], icode: r[2], resn: r[3] };
      });
      v.selectResidues(residues, shown.atomsOfResidues(residues));
    }
    showPair();
  }

  function clear() {
    state.pair = null;
    state.pairData = null;
    byId("cmap-pair-row").hidden = true;
    showPair();
    draw();
    if (state.data) say(state.data.said);
  }

  function offer() {
    var v = viewer();
    var payload = v && v.STATE && v.STATE.playbackPayload;
    var section = byId("side-cmap");
    if (section) section.hidden = !(payload && payload.playback_available);
    if (section && !section.hidden && section.open) load();
  }

  function wire() {
    var section = byId("side-cmap");
    if (!section) return;
    section.addEventListener("toggle", function () { if (section.open) load(); });
    var canvas = byId("cmap-canvas");
    canvas.addEventListener("mousemove", function (event) {
      if (!state.data) return;
      var cell = cellAt(event);
      byId("cmap-hover").textContent = cell && cell[0] !== cell[1] ? described(cell[0], cell[1]) : "";
    });
    canvas.addEventListener("mouseleave", function () { byId("cmap-hover").textContent = ""; });
    canvas.addEventListener("click", function (event) {
      if (!state.data) return;
      var cell = cellAt(event);
      if (cell) follow(Math.min(cell[0], cell[1]), Math.max(cell[0], cell[1]));
    });
    byId("cmap-compare").addEventListener("click", function () {
      // "State X less Y": the share in X's frames less the share in Y's.
      var query = new URLSearchParams({ first: byId("cmap-first").value,
        second: byId("cmap-second").value });
      if (state.states) query.set("method", state.states.method);
      ask(query.toString());
    });
    byId("cmap-all").addEventListener("click", function () { ask(null); });
    byId("cmap-clear").addEventListener("click", clear);
    window.addEventListener("dashboard:frame-shown", function (event) {
      state.frame = Number(event.detail && event.detail.index) || 0;
      if (state.pairData) showPair();
    });
    window.addEventListener("dashboard:viewer-rendered", offer);
    window.addEventListener("dashboard:frames-ready", offer);
    window.FastMDXDashboard && window.FastMDXDashboard.on("playback-ready", offer);
    window.addEventListener("resize", function () { if (state.data && section.open) draw(); });
    window.addEventListener("dashboard:run-changed", function () {
      state.data = null;
      state.pair = null;
      state.pairData = null;
      section.hidden = true;
      say("");
    });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", wire);
  else wire();

  window.FastMDXContactMap = { load: load, follow: follow, clear: clear, ask: ask, state: state,
    described: described };
}());
