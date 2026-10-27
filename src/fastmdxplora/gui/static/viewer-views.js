/* Views of the Viewer saved with the study, and shown again.
 *
 * A figure of a trajectory is a camera, a frame and how the molecule is
 * shown, set by turning and clicking and lost with the page. A view is
 * saved under a name in the study (/api/views, viewer_views.json); choosing
 * it again sets each of those and the camera as they were. Saving is the
 * study's own folder written, so a GUI served beyond this computer only
 * shows the views saved.
 */
(function () {
  "use strict";

  var views = [];

  function byId(id) { return document.getElementById(id); }

  function viewer() { return window.FastMDXMoleculeViewer; }

  /* The study this page shows, by its key: named with each write, which
   * the server refuses for another study open since (another window). */
  function pageStudy() {
    var board = window.FastMDXDashboard;
    var key = board && board.state ? board.state.studyShown : "";
    return /^[0-9a-f]{16}$/.test(key || "") ? key : "";
  }

  function say(text) {
    var live = byId("sr-live");
    if (live) live.textContent = text;
  }

  /** The note of the view chosen, under the list, or nothing. */
  function sayTheNote() {
    var select = byId("viewer-views");
    var said = byId("viewer-view-said");
    if (!said) return;
    var view = select && views.filter(function (v) { return v.name === select.value; })[0];
    said.textContent = view && view.note ? view.note : "";
    said.hidden = !said.textContent;
  }

  function list(chosen) {
    var select = byId("viewer-views");
    if (!select) return;
    select.replaceChildren();
    var none = document.createElement("option");
    none.value = "";
    none.textContent = views.length ? "Saved views" : "No views saved";
    select.appendChild(none);
    views.forEach(function (view) {
      var option = document.createElement("option");
      option.value = view.name;
      option.textContent = view.name;
      if (view.note) option.title = view.note;
      select.appendChild(option);
    });
    select.value = chosen && views.some(function (v) { return v.name === chosen; }) ? chosen : "";
    var forget = byId("viewer-view-forget");
    if (forget) forget.disabled = !select.value;
    sayTheNote();
  }

  function load(chosen) {
    return fetch("/api/views", { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (said) {
        views = said && said.ok && Array.isArray(said.views) ? said.views : [];
        list(chosen);
      })
      .catch(function () { views = []; list(); });
  }

  /** A saved view as it is kept, to put back; null where there is none. */
  function keptView(name) {
    var found = views.filter(function (view) { return view && view.name === name; })[0];
    if (!found) return null;
    var kept = Object.assign({}, found.view || found);
    delete kept.name;
    return kept;
  }

  /** The change said in the page's notice with Undo, which sends ``back``. */
  function offerUndo(said, back, name) {
    var board = window.FastMDXDashboard;
    if (!board || !board.toast) return;
    // The study it was done in: another open since is not undone into.
    var study = board.state && board.state.studyShown;
    board.toast(said, "ok", { label: "Undo", run: function () {
      if ((board.state && board.state.studyShown) !== study) {
        board.toast("The view was not put back: another study is open now.", "warning");
        return;
      }
      // Named for the study it was done in: refused if another is open by
      // now, though this window has not seen it yet.
      return post(back, study).then(function (answer) {
        if (answer && answer.study_changed) {
          board.toast("The view was not put back: another study is open now.", "warning");
          return;
        }
        if (!answer || !answer.ok) {
          say("It could not be undone: " + ((answer && (answer.reason || answer.error)) || "no reason given"));
          return;
        }
        views = answer.views || [];
        list(back.action === "save" ? name : undefined);
        say(back.action === "save" ? "The view " + name + " is back as it was." : "The view " + name + " is gone again.");
      });
    } });
  }

  function post(body, study) {
    var page = study === undefined ? pageStudy() : study;
    return fetch("/api/views", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify(page ? Object.assign({ page_study: page }, body) : body),
    }).then(function (r) {
      return r.json().catch(function () { return { ok: false, reason: "HTTP " + r.status }; });
    }).catch(function () { return { ok: false, reason: "The server did not answer." }; });
  }

  /** The scenes written with the study, newest first, in their list. */
  function loadScenes(chosen) {
    return fetch("/api/scenes", { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .catch(function () { return { scenes: [] }; })
      .then(function (said) {
        var found = said && Array.isArray(said.scenes) ? said.scenes : [];
        scenes = found.length;
        var select = byId("viewer-scenes");
        if (!select) return;
        // The one chosen while the list was asked for stays chosen.
        chosen = chosen || select.value;
        select.replaceChildren();
        var none = document.createElement("option");
        none.value = "";
        none.textContent = found.length ? "Scenes written" : "No scenes written";
        select.appendChild(none);
        found.forEach(function (scene) {
          var option = document.createElement("option");
          option.value = scene.name;
          option.textContent = scene.name;
          select.appendChild(option);
        });
        select.value = chosen && found.some(function (s) { return s.name === chosen; })
          ? chosen : "";
        var open = byId("viewer-scene-open");
        if (open) open.disabled = !select.value;
        var here = byId("viewer-scene-show");
        if (here) here.disabled = !select.value;
      });
  }

  /** A scene written with the study shown in this Viewer again, from the
   * view it keeps for FastMDXplora (custom.fastmdxplora in index.mvsj): a
   * scene written without a camera (from the command line or an AI app)
   * keeps the camera shown. */
  function showScene(name) {
    return fetch("/scenes/" + encodeURIComponent(name) + "/index.mvsj", { cache: "no-store" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .catch(function () { return null; })
      .then(function (scene) {
        var ours = scene && scene.root && scene.root.custom && scene.root.custom.fastmdxplora;
        if (!ours || !ours.view || !viewer() || !viewer().showView) {
          say("The scene " + name + " does not say how the Viewer showed it.");
          return false;
        }
        var view = Object.assign({}, ours.view);
        if (ours.of === "frames" && typeof ours.frame === "number") view.frame = ours.frame;
        else delete view.frame;
        if (!view.camera) {
          var now = viewer().viewNow ? viewer().viewNow() : null;
          if (now) view.camera = now.camera;
        }
        var ended = viewer().endTheScene ? viewer().endTheScene(view) : null;
        return Promise.resolve(ended).then(function () {
          return viewer().showView(view);
        }).then(function (shown) {
          say(shown ? "Showing the scene " + name + "." : "The scene " + name
            + " could not be shown here.");
          return shown;
        });
      });
  }

  // What the name being typed is for: a view, or a scene file.
  var namingFor = "view";
  var scenes = 0;

  function naming(open, what) {
    var span = byId("viewer-view-naming");
    var input = byId("viewer-view-name");
    if (!span || !input) return;
    span.hidden = !open;
    var note = byId("viewer-view-note");
    if (note) {
      // A note is a view's: a scene file says what it shows by itself.
      note.hidden = (what || "view") !== "view";
      note.value = "";
    }
    if (open) {
      namingFor = what || "view";
      input.setAttribute("aria-label", namingFor === "scene" ? "Name of the scene" : "Name of the view");
      // One button saves either: named for the one being named.
      var keep = byId("viewer-view-keep");
      var keeps = namingFor === "scene" ? "Save the scene" : "Save the view";
      if (keep) { keep.setAttribute("aria-label", keeps); keep.title = keeps; }
      input.value = namingFor === "scene" ? "Scene " + (scenes + 1) : "View " + (views.length + 1);
      input.focus();
      input.select();
    }
  }

  function ligands() {
    var state = viewer() && viewer().STATE;
    var names = state && state.structureInfo && Array.isArray(state.structureInfo.ligand_resnames)
      ? state.structureInfo.ligand_resnames.filter(Boolean) : [];
    if (state && state.ligandResname && names.indexOf(state.ligandResname) < 0) {
      names.unshift(state.ligandResname);
    }
    return names;
  }

  /** The view shown written with the study as a MolViewSpec scene
   * (scenes/<name>.mvsx, with the selections named), and downloaded. */
  /* Why a view or a scene was not saved, in the server's own words (a 403
   * beyond this computer gives them as ``error``), said after a colon with
   * a small first letter. */
  function notDone(what, said) {
    var why = String((said && (said.reason || said.error)) || "no reason given");
    // A word, not a name (MDTraj): its first letter small.
    return what + ": " + (/^[A-Z][a-z]/.test(why) ? why.charAt(0).toLowerCase() + why.slice(1)
      : why);
  }

  function keepScene(name, shown) {
    say("Writing the scene " + name + "\u2026");
    return fetch("/api/scenes", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ name: name, view: shown, ligands: ligands(), page_study: pageStudy() || undefined }),
    }).then(function (r) {
      return r.json().catch(function () { return { ok: false, reason: "HTTP " + r.status }; });
    }).catch(function () { return { ok: false, reason: "The server did not answer." }; })
      .then(function (said) {
        if (!said || !said.ok) {
          say(notDone("The scene was not written", said));
          return;
        }
        naming(false);
        loadScenes(said.name);
        var link = document.createElement("a");
        link.href = "/artifacts/scenes/" + encodeURIComponent(said.name) + ".mvsx?download=1";
        link.download = said.name + ".mvsx";
        document.body.appendChild(link);
        link.click();
        link.remove();
        say("Wrote the scene " + said.name + " with the study (scenes/" + said.name + ".mvsx)"
          + (said.notes && said.notes.length ? ". " + said.notes.join(" ") : "."));
      });
  }

  function keep() {
    var input = byId("viewer-view-name");
    var name = input ? input.value.trim() : "";
    var shown = viewer() && viewer().viewNow ? viewer().viewNow() : null;
    if (!name || !shown) {
      say(shown ? "A view needs a name." : "There is no view to save yet.");
      return;
    }
    if (namingFor === "scene") {
      keepScene(name, shown);
      return;
    }
    var note = byId("viewer-view-note");
    var written = note ? note.value.trim() : "";
    if (written) shown = Object.assign({}, shown, { note: written });
    var before = keptView(name);
    post({ action: "save", name: name, view: shown }).then(function (said) {
      if (!said || !said.ok) {
        say(notDone("The view was not saved", said));
        return;
      }
      views = said.views || [];
      list(name);
      naming(false);
      say("Saved the view " + name + " with the study.");
      // Undone, a view saved over another is that one again; a new one goes.
      offerUndo("Saved the view " + name + ".", before
        ? { action: "save", name: name, view: before } : { action: "delete", name: name },
        name);
    });
  }

  function wire() {
    var select = byId("viewer-views");
    if (!select) return;
    select.addEventListener("change", function () {
      var forget = byId("viewer-view-forget");
      if (forget) forget.disabled = !select.value;
      sayTheNote();
      var view = views.filter(function (v) { return v.name === select.value; })[0];
      if (view && viewer() && viewer().showView) {
        var ended = viewer().endTheScene ? viewer().endTheScene(view) : null;
        Promise.resolve(ended).then(function () {
          return viewer().showView(view);
        }).then(function (shown) {
          if (shown) say("Showing the view " + view.name + ".");
        });
      }
    });
    byId("viewer-view-save").addEventListener("click", function () {
      naming(byId("viewer-view-naming").hidden || namingFor !== "view", "view");
    });
    byId("viewer-scene-save").addEventListener("click", function () {
      naming(byId("viewer-view-naming").hidden || namingFor !== "scene", "scene");
    });
    byId("viewer-view-keep").addEventListener("click", keep);
    ["viewer-view-name", "viewer-view-note"].forEach(function (id) {
      var field = byId(id);
      if (!field) return;
      field.addEventListener("keydown", function (event) {
        // The Viewer's own keys (Space, the arrows, M) are not for typing.
        event.stopPropagation();
        if (event.key === "Enter") { event.preventDefault(); keep(); }
        if (event.key === "Escape") { event.preventDefault(); naming(false); }
      });
    });
    byId("viewer-view-forget").addEventListener("click", function () {
      var name = select.value;
      if (!name) return;
      var before = keptView(name);
      post({ action: "delete", name: name }).then(function (said) {
        if (!said || !said.ok) {
          say("The view was not forgotten: " + ((said && (said.reason || said.error)) || "no reason given"));
          return;
        }
        views = said.views || [];
        list();
        say("Forgot the view " + name + ".");
        if (before) offerUndo("Forgot the view " + name + ".", { action: "save", name: name, view: before }, name);
      });
    });
    var scenesList = byId("viewer-scenes");
    if (scenesList) {
      scenesList.addEventListener("change", function () {
        byId("viewer-scene-open").disabled = !scenesList.value;
        byId("viewer-scene-show").disabled = !scenesList.value;
      });
      byId("viewer-scene-show").addEventListener("click", function () {
        if (scenesList.value) showScene(scenesList.value);
      });
      byId("viewer-scene-open").addEventListener("click", function () {
        if (!scenesList.value) return;
        window.open("/scenes/" + encodeURIComponent(scenesList.value) + "/view", "_blank",
          "noopener");
      });
    }
    window.addEventListener("dashboard:viewer-page-opened", function () {
      load(select.value);
      loadScenes();
    });
    window.addEventListener("dashboard:run-changed", function (event) {
      // The page's first state is the study it opened, whose views these
      // are: a view chosen before it arrived was put back to none, and
      // Forget refused it.
      if (event.detail && event.detail.first) {
        load(select.value);
        loadScenes();
        return;
      }
      views = [];
      list();
      load();
      loadScenes();
    });
    load();
    loadScenes();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", wire);
  else wire();

  window.FastMDXViewerViews = { load: load, keepScene: keepScene, loadScenes: loadScenes,
    showScene: showScene };
}());
