/* The states a study visited, in the Viewer (gui/states.py).
 *
 * The states the cluster analysis found, each with its share of the frames
 * and a representative, its medoid among the frames played, shown with a
 * click; and two compared: the second state's representative placed beside
 * the first's frame, fitted on the alpha carbons, and the protein coloured
 * by how far each residue moved between them.
 */
(function () {
  "use strict";

  var KEY = "state-difference";
  var COMPARED = 0xb0b0b8;

  var state = { data: null, asked: false, comparing: null, colourBefore: null };

  function byId(id) { return document.getElementById(id); }

  function viewer() { return window.FastMDXMoleculeViewer; }

  function engine() {
    var v = viewer();
    return v && v.STATE ? v.STATE.engine : null;
  }

  function say(text) {
    var note = byId("states-note");
    if (note) note.textContent = text || "";
  }

  function percent(share) { return Math.round(share * 100) + "%"; }

  function nameOf(found) {
    return "State " + found.state + " (" + percent(found.share) + ")";
  }

  async function showFrame(frame) {
    var v = viewer();
    var frames = await v.movie.frames();
    if (!frames) return false;
    v.movie.hold();
    await v.movie.showFrame(frame);
    return true;
  }

  function list() {
    var holder = byId("states-list");
    var first = byId("states-first");
    var second = byId("states-second");
    if (!holder || !state.data) return;
    holder.replaceChildren();
    first.replaceChildren();
    second.replaceChildren();
    state.data.states.forEach(function (found) {
      var item = document.createElement("li");
      item.className = "state-row";
      item.setAttribute("data-state", String(found.state));
      var bar = document.createElement("span");
      bar.className = "state-share";
      var fill = document.createElement("span");
      fill.style.width = percent(found.share);
      bar.append(fill);
      var said = document.createElement("span");
      said.textContent = nameOf(found) + (found.representative == null ? ", no frame played"
        : ", frame " + found.representative);
      item.append(bar, said);
      if (found.representative != null) {
        var button = window.FastMDXIcons.button("eye", "Show " + nameOf(found));
        button.title = "Show the state's representative frame";
        button.addEventListener("click", function () { showFrame(found.representative); });
        item.append(button);
        [first, second].forEach(function (select) {
          var option = document.createElement("option");
          option.value = String(found.state);
          option.textContent = nameOf(found);
          select.append(option);
        });
      }
      holder.append(item);
    });
    if (second.options.length > 1) second.selectedIndex = 1;
    byId("states-compare-row").hidden = first.options.length < 2;
    say(state.data.said);
  }

  function chosen(select) {
    var value = Number(byId(select).value);
    return state.data.states.find(function (found) { return found.state === value; });
  }

  async function compare() {
    var v = viewer();
    var a = chosen("states-first");
    var b = chosen("states-second");
    if (!a || !b || a.state === b.state) { say("Choose two different states to compare."); return; }
    if (!(await showFrame(a.representative))) { say("There are no frames to compare."); return; }
    var words = v.superposition();
    var query = new URLSearchParams({ a: String(a.representative), b: String(b.representative),
      on: words.on, to: words.to, smooth: words.smooth, ligand: words.ligand,
      cutoff: words.cutoff });
    var said;
    try {
      said = await (await fetch("/api/state-difference?" + query, { cache: "no-store" })).json();
    } catch (error) {
      said = { ok: false, reason: "The server did not answer." };
    }
    if (!said.ok) { say("The states could not be compared: " + said.reason); return; }
    await engine().showCompared(said.pdb, a.representative, COMPARED);
    if (state.colourBefore == null) state.colourBefore = v.STATE.colorMode;
    v.addResult(said.property);
    await v.colourBy("result:" + KEY);
    state.comparing = { a: a, b: b };
    byId("states-stop").hidden = false;
    say(nameOf(b) + ", in grey, beside " + nameOf(a) + "; " + said.property.about
      + " Coloured by how far each residue moved.");
  }

  async function stopComparing() {
    var v = viewer();
    var shown = engine();
    if (shown) await shown.removeCompared();
    if (state.colourBefore != null) {
      var before = state.colourBefore;
      state.colourBefore = null;
      await v.removeResult(KEY);
      if (before !== "result:" + KEY) await v.colourBy(before);
    }
    state.comparing = null;
    byId("states-stop").hidden = true;
    if (state.data) say(state.data.said);
  }

  async function load(method) {
    var query = method ? "?method=" + encodeURIComponent(method) : "";
    try {
      state.data = await (await fetch("/api/states" + query, { cache: "no-store" })).json();
    } catch (error) {
      state.data = null;
    }
    var section = byId("side-states");
    if (!state.data || !state.data.ok) {
      section.hidden = true;
      return;
    }
    section.hidden = false;
    var methods = byId("states-method");
    methods.replaceChildren.apply(methods, state.data.methods.map(function (name) {
      var option = document.createElement("option");
      option.value = name;
      option.textContent = name;
      return option;
    }));
    methods.value = state.data.method;
    byId("states-method-row").hidden = state.data.methods.length < 2;
    list();
  }

  function offer() {
    if (state.asked) return;
    state.asked = true;
    load(null);
  }

  function wire() {
    if (!byId("side-states")) return;
    byId("states-method").addEventListener("change", function () {
      stopComparing().then(function () { load(byId("states-method").value); });
    });
    byId("states-compare").addEventListener("click", function () { compare(); });
    byId("states-stop").addEventListener("click", function () { stopComparing(); });
    // Where there is a structure, or frames: a study with no structure of
    // its own is shown only by its frames.
    window.addEventListener("dashboard:viewer-rendered", offer);
    window.FastMDXDashboard && window.FastMDXDashboard.on("playback-ready", offer);
    window.addEventListener("dashboard:run-changed", function () {
      state.data = null;
      state.asked = false;
      state.comparing = null;
      state.colourBefore = null;
      byId("side-states").hidden = true;
      byId("states-stop").hidden = true;
    });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", wire);
  else wire();

  window.FastMDXStates = { compare: compare, stop: stopComparing };
}());
