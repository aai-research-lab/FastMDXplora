/* A study's main motions in the Viewer (gui/motion.py).
 *
 * The principal components of its atoms' fluctuation: each shown as the
 * atoms swinging along it, from two standard deviations of its projection
 * one way to two the other, and as lines from each atom to where the motion
 * takes it, placed on the first frame played. The frames are superposed to
 * the first as a motion is shown, and it is hidden while they are fitted
 * otherwise.
 */
(function () {
  "use strict";

  var COLOUR = 0xcc79a7;
  var STEP_MS = 60;

  var state = { said: null, timer: null, offered: false, busy: false };

  function byId(id) { return document.getElementById(id); }

  function viewer() { return window.FastMDXMoleculeViewer; }

  function engine() {
    var v = viewer();
    return v && v.STATE ? v.STATE.engine : null;
  }

  function wanted() {
    var swing = byId("motion-swing");
    var arrows = byId("motion-arrows");
    return { swing: !!(swing && swing.checked), arrows: !!(arrows && arrows.checked) };
  }

  function say(text) {
    var note = byId("motion-note");
    if (note) note.textContent = text || "";
  }

  function stop() {
    state.timer = null;
  }

  /* The swing played on its own clock, whatever the frames are doing: a
   * step once the last is rendered, so a slow machine is not handed a
   * queue of them. */
  function play() {
    stop();
    if (!wanted().swing) return;
    var token = {};
    state.timer = token;
    var frame = 0;
    (async function step() {
      while (state.timer === token) {
        var shown = engine();
        var motion = shown && shown.motionShown();
        if (motion && motion.rendered) {
          frame += 1;
          await shown.setMotionFrame(frame);
        }
        await new Promise(function (resolve) { window.setTimeout(resolve, STEP_MS); });
      }
    }());
  }

  async function show() {
    var v = viewer();
    var parts = wanted();
    if (!parts.swing && !parts.arrows) {
      stop();
      var shown = engine();
      if (shown) await shown.removeMotion();
      say("");
      return;
    }
    if (state.busy) return;
    state.busy = true;
    try {
      say("Finding the motion…");
      var frames = await v.movie.frames();
      if (!frames) { say("There are no frames to find a motion in."); return; }
      if (!v.placedFitAsPlayed()) {
        v.STATE.superposedTo = "first";
        v.STATE.smoothedOver = 1;
        var to = byId("traj-superpose-to");
        if (to) to.value = "first";
        var smooth = byId("traj-smooth");
        if (smooth) smooth.value = "1";
        await v.superpose("backbone");
      }
      var query = new URLSearchParams({ mode: byId("motion-mode").value || "1",
        scale: byId("motion-scale").value || "1" });
      var said;
      try {
        said = await (await fetch("/api/motion?" + query, { cache: "no-store" })).json();
      } catch (error) {
        said = { ok: false, reason: "The server did not answer." };
      }
      if (!said.ok) { say("No motion shown: " + said.reason); return; }
      state.said = said;
      await engine().showMotion(said.pdb, said.arrows, COLOUR);
      await engine().setMotionParts(wanted().swing, wanted().arrows);
      await engine().setPlacedAside(!v.placedFitAsPlayed());
      offerModes(said.modes);
      say(said.said);
      play();
    } finally {
      state.busy = false;
    }
  }

  function offerModes(shares) {
    var select = byId("motion-mode");
    if (!select || !Array.isArray(shares)) return;
    var chosen = select.value || "1";
    select.replaceChildren.apply(select, shares.map(function (share, i) {
      var option = document.createElement("option");
      option.value = String(i + 1);
      option.textContent = "Motion " + (i + 1) + " (" + Math.round(share * 100) + "%)";
      return option;
    }));
    select.value = chosen;
  }

  /* Offered where there are frames: the motions are found from them where
   * the study's analysis kept none. */
  async function offer() {
    var section = byId("side-motion");
    if (!section || state.offered) return;
    var v = viewer();
    if (!v || !v.STATE || !(v.STATE.playbackPayload && v.STATE.playbackPayload.playback_available)) {
      return;
    }
    state.offered = true;
    section.hidden = false;
  }

  function wire() {
    if (!byId("side-motion")) return;
    ["motion-swing", "motion-arrows"].forEach(function (id) {
      byId(id).addEventListener("change", function () {
        var shown = engine();
        if (shown && shown.motionShown() && (wanted().swing || wanted().arrows)) {
          shown.setMotionParts(wanted().swing, wanted().arrows).then(play);
          return;
        }
        show();
      });
    });
    ["motion-mode", "motion-scale"].forEach(function (id) {
      byId(id).addEventListener("change", function () {
        if (wanted().swing || wanted().arrows) show();
      });
    });
    window.FastMDXDashboard && window.FastMDXDashboard.on("playback-ready", offer);
    window.addEventListener("dashboard:viewer-rendered", offer);
    window.addEventListener("dashboard:frames-ready", function () {
      if (wanted().swing || wanted().arrows) show();
    });
    window.addEventListener("dashboard:run-changed", function () {
      stop();
      state.offered = false;
      state.said = null;
      byId("motion-swing").checked = false;
      byId("motion-arrows").checked = false;
      byId("side-motion").hidden = true;
      say("");
    });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", wire);
  else wire();

  window.FastMDXMotion = { show: show, stop: stop };
}());
