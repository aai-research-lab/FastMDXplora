/* Another study beside this one in the Viewer (gui/beside.py).
 *
 * A wild type and a mutant, or two related proteins: the other study's
 * frames played beside this one's, its residues paired by sequence, fitted
 * on the paired alpha carbons to this study's first frame and timed by
 * simulation time; the residues that differ listed, and this study's
 * protein coloured by the difference in RMSF where both have one.
 */
(function () {
  "use strict";

  var COLOUR = "#E69F00";
  var KEY = "rmsf-difference";

  var state = { said: null, listed: false, colourBefore: null };

  function byId(id) { return document.getElementById(id); }

  function viewer() { return window.FastMDXMoleculeViewer; }

  function engine() {
    var v = viewer();
    return v && v.STATE ? v.STATE.engine : null;
  }

  function say(text) {
    var note = byId("beside-note");
    if (note) note.textContent = text || "";
  }

  async function listStudies() {
    if (state.listed) return;
    state.listed = true;
    var select = byId("beside-study");
    var said;
    try {
      said = await (await fetch("/api/studies", { cache: "no-store" })).json();
    } catch (error) {
      said = null;
    }
    var dashboard = window.FastMDXDashboard;
    var here = dashboard && dashboard.state.appState ? dashboard.state.appState.active_run || "" : "";
    var cards = (said && said.ok ? said.studies : []).filter(function (card) {
      return card.path !== here;
    });
    select.replaceChildren.apply(select, cards.map(function (card) {
      var option = document.createElement("option");
      option.value = card.path;
      option.textContent = card.name + (card.system ? " (" + card.system + ")" : "");
      return option;
    }));
    byId("side-beside").hidden = cards.length === 0;
  }

  function listMutations() {
    var list = byId("beside-mutations");
    list.replaceChildren();
    if (!state.said) return;
    state.said.mutations.forEach(function (m) {
      var item = document.createElement("li");
      item.className = "occ-site";
      item.textContent = (m.chain ? m.chain + ":" : "") + m.from + m.resi + (m.icode || "")
        + " → " + m.to + (m.theirs !== m.resi ? " (" + m.theirs + " there)" : "");
      list.append(item);
    });
  }

  async function show() {
    var v = viewer();
    var path = byId("beside-study").value;
    if (!path) return;
    say("Fitting the other study's frames beside these…");
    var frames = await v.movie.frames();
    if (!frames) { say("This study has no frames to play beside."); return; }
    var said;
    try {
      said = await (await fetch("/api/beside?" + new URLSearchParams({ path: path }),
        { cache: "no-store" })).json();
    } catch (error) {
      said = { ok: false, reason: "The server did not answer." };
    }
    if (!said.ok) { say("Not shown: " + said.reason); return; }
    state.said = said;
    var version = Date.now();
    if (!v.placedFitAsPlayed() || v.STATE.superposed !== "backbone") {
      v.STATE.superposedTo = "first";
      v.STATE.smoothedOver = 1;
      await v.superpose("backbone");
    }
    await engine().showRuns([{ label: said.name, colour: COLOUR,
      topology: said.topology + "&v=" + version,
      coordinates: said.coordinates + "&v=" + version }], null, "beside");
    await engine().setRunsAside(v.STATE.superposed !== "backbone");
    byId("beside-remove").hidden = false;
    byId("beside-colour-row").hidden = !said.property;
    listMutations();
    say(said.said + (said.frames < (v.STATE.playbackFrames || 0)
      ? " It ends at frame " + (said.frames - 1) + ", where its run does." : ""));
    if (byId("beside-colour").checked) await colour(true);
  }

  async function colour(on) {
    var v = viewer();
    if (on && state.said && state.said.property) {
      if (state.colourBefore == null) state.colourBefore = v.STATE.colorMode;
      v.addResult(state.said.property);
      await v.colourBy("result:" + KEY);
    } else if (state.colourBefore != null) {
      var before = state.colourBefore;
      state.colourBefore = null;
      await v.removeResult(KEY);
      if (before !== "result:" + KEY) await v.colourBy(before);
    }
  }

  async function remove() {
    var shown = engine();
    if (shown) await shown.removeRuns("beside");
    byId("beside-colour").checked = false;
    await colour(false);
    state.said = null;
    listMutations();
    byId("beside-remove").hidden = true;
    byId("beside-colour-row").hidden = true;
    say("");
  }

  function wire() {
    if (!byId("side-beside")) return;
    byId("beside-show").addEventListener("click", function () { show(); });
    byId("beside-remove").addEventListener("click", function () { remove(); });
    byId("beside-colour").addEventListener("change", function () {
      colour(byId("beside-colour").checked);
    });
    window.FastMDXDashboard && window.FastMDXDashboard.on("playback-ready", listStudies);
    window.addEventListener("dashboard:viewer-rendered", listStudies);
    window.addEventListener("dashboard:frames-ready", function () {
      if (state.said) show();
    });
    window.addEventListener("dashboard:run-changed", function () {
      state.said = null;
      state.listed = false;
      state.colourBefore = null;
      byId("side-beside").hidden = true;
      byId("beside-remove").hidden = true;
      byId("beside-colour").checked = false;
      listMutations();
      say("");
    });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", wire);
  else wire();

  window.FastMDXBeside = { show: show, remove: remove };
}());
