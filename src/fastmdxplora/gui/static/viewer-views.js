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

  function say(text) {
    var live = byId("sr-live");
    if (live) live.textContent = text;
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
      select.appendChild(option);
    });
    select.value = chosen && views.some(function (v) { return v.name === chosen; }) ? chosen : "";
    var forget = byId("viewer-view-forget");
    if (forget) forget.disabled = !select.value;
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

  function post(body) {
    return fetch("/api/views", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }).then(function (r) {
      return r.json().catch(function () { return { ok: false, reason: "HTTP " + r.status }; });
    }).catch(function () { return { ok: false, reason: "The server did not answer." }; });
  }

  function naming(open) {
    var span = byId("viewer-view-naming");
    var input = byId("viewer-view-name");
    if (!span || !input) return;
    span.hidden = !open;
    if (open) {
      input.value = "View " + (views.length + 1);
      input.focus();
      input.select();
    }
  }

  function keep() {
    var input = byId("viewer-view-name");
    var name = input ? input.value.trim() : "";
    var shown = viewer() && viewer().viewNow ? viewer().viewNow() : null;
    if (!name || !shown) {
      say(shown ? "A view needs a name." : "There is no view to save yet.");
      return;
    }
    post({ action: "save", name: name, view: shown }).then(function (said) {
      if (!said || !said.ok) {
        say("The view was not saved: " + ((said && (said.reason || said.error)) || "no reason given"));
        return;
      }
      views = said.views || [];
      list(name);
      naming(false);
      say("Saved the view " + name + " with the study.");
    });
  }

  function wire() {
    var select = byId("viewer-views");
    if (!select) return;
    select.addEventListener("change", function () {
      var forget = byId("viewer-view-forget");
      if (forget) forget.disabled = !select.value;
      var view = views.filter(function (v) { return v.name === select.value; })[0];
      if (view && viewer() && viewer().showView) {
        viewer().showView(view).then(function (shown) {
          if (shown) say("Showing the view " + view.name + ".");
        });
      }
    });
    byId("viewer-view-save").addEventListener("click", function () {
      naming(byId("viewer-view-naming").hidden);
    });
    byId("viewer-view-keep").addEventListener("click", keep);
    byId("viewer-view-name").addEventListener("keydown", function (event) {
      // The Viewer's own keys (Space, the arrows, M) are not for a name.
      event.stopPropagation();
      if (event.key === "Enter") { event.preventDefault(); keep(); }
      if (event.key === "Escape") { event.preventDefault(); naming(false); }
    });
    byId("viewer-view-forget").addEventListener("click", function () {
      var name = select.value;
      if (!name) return;
      post({ action: "delete", name: name }).then(function (said) {
        if (!said || !said.ok) {
          say("The view was not forgotten: " + ((said && (said.reason || said.error)) || "no reason given"));
          return;
        }
        views = said.views || [];
        list();
        say("Forgot the view " + name + ".");
      });
    });
    window.addEventListener("dashboard:viewer-page-opened", function () { load(select.value); });
    window.addEventListener("dashboard:run-changed", function () { views = []; list(); load(); });
    load();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", wire);
  else wire();

  window.FastMDXViewerViews = { load: load };
}());
