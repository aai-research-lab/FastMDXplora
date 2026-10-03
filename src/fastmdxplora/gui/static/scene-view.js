/* A scene written with the study, shown as Mol* shows it.
 *
 * The page's canvas is the Viewer's engine, given the scene's MolViewSpec
 * (/scenes/<name>/index.mvsj, the files it names beside it) and nothing
 * else: what is shown here is what any viewer built on Mol* shows of it.
 */
(function () {
  "use strict";

  function say(text) {
    var line = document.getElementById("scene-said");
    if (line) line.textContent = text;
  }

  function show() {
    var name = document.body.dataset.scene || "";
    var url = new URL("/scenes/" + encodeURIComponent(name) + "/index.mvsj",
      window.location.href).href;
    var engine = null;
    window.FastMDXViewerEngine.create(document.getElementById("scene-canvas"), {})
      .then(function (made) {
        engine = made;
        return fetch(url, { cache: "no-store" });
      })
      .then(function (r) {
        if (!r.ok) throw new Error("There is no scene called " + name + ".");
        return r.text();
      })
      .then(function (text) {
        var mvs = engine.lib.extensions.mvs;
        var data = mvs.MVSData.fromMVSJ(text);
        return mvs.loadMVS(engine.plugin, data, { sourceUrl: url, sanityChecks: true,
          replaceExisting: true }).then(function () { return data; });
      })
      .then(function (data) {
        var meta = (data && data.metadata) || {};
        say(meta.description || "");
        window.FastMDXScene = { engine: engine, loaded: true, metadata: meta };
      })
      .catch(function (error) {
        say("The scene could not be shown: " + (error && error.message ? error.message : error));
        window.FastMDXScene = { engine: engine, loaded: false, error: String(error) };
      });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", show);
  else show();
}());
