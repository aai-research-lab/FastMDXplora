/* Where the ligand and the water went over the frames played, and the water
 * sites the study found, in the Viewer (gui/occupancy.py).
 *
 * Each map is the fraction of frames in which an atom's centre is near each
 * point, the frames fitted on the ligand's pocket to the first frame; it is
 * rendered as a surface where that fraction reaches the level chosen. The
 * water sites `water_sites` found are spheres placed on the first frame.
 * Both are where the first frame is: the frames are superposed on the pocket
 * as they are shown, and they are hidden while the frames are fitted
 * otherwise.
 */
(function () {
  "use strict";

  // The ligand's colour when it is shown alone, and the water's.
  var COLOURS = { ligand: 0x63e6ff, water: 0x4da3ff };
  var ALPHA = { ligand: 0.5, water: 0.4 };
  var NAMES = { ligand: "the ligand", water: "water" };

  var state = { said: {}, sites: null, sitesAsked: false, busy: {} };

  function byId(id) { return document.getElementById(id); }

  function viewer() { return window.FastMDXMoleculeViewer; }

  function engine() {
    var v = viewer();
    return v && v.STATE ? v.STATE.engine : null;
  }

  function level(of) {
    var input = byId("occ-" + of + "-level");
    return input ? Number(input.value) / 100 : 0.3;
  }

  function sayLevel(of) {
    var said = byId("occ-" + of + "-level-said");
    if (said) said.textContent = Math.round(level(of) * 100) + "%";
  }

  function wanted(of) {
    var box = byId("occ-" + of);
    return !!(box && box.checked);
  }

  /* What is shown, said under the controls. */
  function sayWhat(extra) {
    var note = byId("occ-note");
    if (!note) return;
    var parts = [];
    ["ligand", "water"].forEach(function (of) {
      if (wanted(of) && state.said[of]) parts.push(state.said[of].said);
    });
    if (wanted("sites") && state.sites && state.sites.said) parts.push(state.sites.said);
    if (extra) parts.push(extra);
    note.textContent = parts.join(" ");
  }

  /* The frames loaded, superposed on the pocket to the first frame, where
   * they are not fitted as the maps are placed. */
  async function framesFitted() {
    var v = viewer();
    var frames = await v.movie.frames();
    if (!frames) return false;
    if (!v.placedFitAsPlayed()) {
      v.STATE.superposedTo = "first";
      v.STATE.smoothedOver = 1;
      var to = byId("traj-superpose-to");
      if (to) to.value = "first";
      var smooth = byId("traj-smooth");
      if (smooth) smooth.value = "1";
      await v.superpose(v.ligandResnames().length ? "pocket" : "backbone");
    }
    return true;
  }

  async function showMap(of) {
    var v = viewer();
    if (state.busy[of]) return;
    state.busy[of] = true;
    try {
      sayWhat("Making the map of where " + NAMES[of] + " was…");
      if (!(await framesFitted())) {
        byId("occ-" + of).checked = false;
        sayWhat("There are no frames to make a map of.");
        return;
      }
      var query = new URLSearchParams({ of: of, ligand: v.STATE.ligandResname
        || v.ligandResnames()[0] || "", cutoff: String(v.STATE.pocketCutoff || 5) });
      var said;
      try {
        said = await (await fetch("/api/occupancy?" + query, { cache: "no-store" })).json();
      } catch (error) {
        said = { ok: false, reason: "The server did not answer." };
      }
      if (!wanted(of)) return;
      if (!said.ok) {
        byId("occ-" + of).checked = false;
        sayWhat("No map of " + NAMES[of] + ": " + said.reason);
        return;
      }
      state.said[of] = said;
      await engine().showVolume(of, said.url, level(of), COLOURS[of], ALPHA[of]);
      await engine().setPlacedAside(!v.placedFitAsPlayed());
      sayWhat();
    } finally {
      state.busy[of] = false;
    }
  }

  async function askForSites() {
    if (state.sitesAsked) return state.sites;
    state.sitesAsked = true;
    try {
      state.sites = await (await fetch("/api/water-sites", { cache: "no-store" })).json();
    } catch (error) {
      state.sites = { ok: false, reason: "The server did not answer." };
    }
    return state.sites;
  }

  function listSites() {
    var list = byId("occ-sites-list");
    if (!list) return;
    list.replaceChildren();
    if (!wanted("sites") || !state.sites || !state.sites.ok) return;
    state.sites.sites.forEach(function (site) {
      var item = document.createElement("li");
      item.className = "occ-site";
      var swatch = document.createElement("span");
      swatch.className = "occ-site-swatch";
      swatch.style.background = site.bound ? "#D55E00" : "#56B4E9";
      var said = document.createElement("span");
      said.textContent = "Site " + site.site + ": " + Math.round(site.occupancy * 100)
        + "% of frames, " + site.said;
      item.append(swatch, said);
      list.append(item);
    });
  }

  async function showSites() {
    var v = viewer();
    var said = await askForSites();
    if (!wanted("sites")) return;
    if (!said.ok) {
      byId("occ-sites").checked = false;
      sayWhat("No water sites shown: " + said.reason);
      return;
    }
    if (!(await framesFitted())) return;
    await engine().showWaterSites(said.sites);
    await engine().setPlacedAside(!v.placedFitAsPlayed());
    listSites();
    sayWhat();
  }

  /* What the study offers: a map where it has a ligand, the sites where
   * its `water_sites` analysis ran. */
  async function offer() {
    var v = viewer();
    if (!v || !v.STATE) return;
    var ligand = v.ligandResnames().length > 0;
    byId("occ-ligand-row").hidden = !ligand;
    byId("occ-water-row").hidden = !ligand;
    var sites = await askForSites();
    var analysed = !!(sites && (sites.ok
      || !/^The study has no water sites/.test(String(sites.reason || ""))));
    byId("occ-sites-row").hidden = !analysed;
    byId("side-occupancy").hidden = !(ligand || analysed);
  }

  /* The frames loaded again: what was shown is placed on them again. */
  async function again() {
    if (wanted("ligand")) await showMap("ligand");
    if (wanted("water")) await showMap("water");
    if (wanted("sites")) await showSites();
  }

  function wire() {
    if (!byId("side-occupancy")) return;
    ["ligand", "water"].forEach(function (of) {
      byId("occ-" + of).addEventListener("change", function () {
        if (wanted(of)) { showMap(of); return; }
        var shown = engine();
        if (shown) shown.removeVolume(of);
        sayWhat();
      });
      var input = byId("occ-" + of + "-level");
      input.addEventListener("input", function () { sayLevel(of); });
      input.addEventListener("change", function () {
        sayLevel(of);
        var shown = engine();
        if (shown && wanted(of)) shown.setVolumeLevel(of, level(of));
      });
      sayLevel(of);
    });
    byId("occ-sites").addEventListener("change", function () {
      if (wanted("sites")) { showSites(); return; }
      var shown = engine();
      if (shown) shown.removeWaterSites();
      listSites();
      sayWhat();
    });
    window.addEventListener("dashboard:viewer-rendered", function () { offer(); });
    window.addEventListener("dashboard:frames-ready", function () { again(); });
    window.addEventListener("dashboard:run-changed", function () {
      state.said = {};
      state.sites = null;
      state.sitesAsked = false;
      ["ligand", "water", "sites"].forEach(function (of) {
        var box = byId("occ-" + of);
        if (box) box.checked = false;
      });
      listSites();
      sayWhat();
    });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", wire);
  else wire();

  window.FastMDXOccupancy = { showMap: showMap, showSites: showSites, offer: offer };
}());
