/* Everything the GUI can go to or do, found by typing (⌘K, or Ctrl+K), as
 * an editor's quick open.
 *
 * A page (with its key), every study of the workspace, a file of the open
 * study (⌘P lists those alone), what to compare it with, an analysis, a
 * tool of the Viewer, a setting, the scheme, Cite, every key: each is a
 * line, found by the words typed (each word anywhere in its name or its
 * kind), chosen with the arrows and Enter or a click. `@TRP23` (or
 * `@A:23`) selects that residue in the Viewer; `:2140` shows that frame
 * of the trajectory there. It is built from the page as it stands when
 * opened, and from the workspace's studies and the study's files as the
 * server lists them.
 */
(function () {
  "use strict";

  var MOST = 60;
  var KEPT_MS = 30000;
  var items = [];
  var shown = [];
  var at = 0;
  var mode = "";
  var fetched = { studies: null, files: null };

  function el(id) { return document.getElementById(id); }

  function make(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  function dashboard() { return window.FastMDXDashboard; }

  function go(page) {
    return function () { if (dashboard()) dashboard().navigate(page); };
  }

  function dialog(id) {
    return function () { if (window.FastMDXDialog) window.FastMDXDialog.open(id); };
  }

  /* What there is to go to or do, now. */
  function gather() {
    var found = [];
    var pages = [
      ["overview", "Overview"], ["viewer", "Viewer"], ["analysis", "Analysis"],
      ["report", "Report"], ["files", "Files"], ["studies", "All studies"],
      ["run", "New study"], ["agent", "Agent"],
    ];
    if (mode === "files") return filesOf();
    if (mode === "compare") return compareWith();
    pages.forEach(function (page) {
      if (document.querySelector('.page[data-page="' + page[0] + '"]')) {
        found.push({ group: "Page", label: page[1], run: go(page[0]), keys: keysOf("page:" + page[0]) });
      }
    });
    found = found.concat(studiesOf());
    // Each analysis once, by its section's heading on the Analysis page:
    // the Report page's figures are cards too, and a section's first card
    // is titled by its figure ("KMeans trajectory scatter" was listed for
    // the clustering and again for its own figure).
    var named = {}, said = {};
    // Only the analyses' own sections: the cards of the clustering's
    // figures below them were listed as analyses ("KMeans population")
    // and opened the page at its top.
    var cards = document.querySelectorAll(
      '.page[data-page="analysis"] .analysis-section .analysis-card[data-analysis]');
    Array.prototype.forEach.call(cards, function (card) {
      var name = card.getAttribute("data-analysis");
      var section = card.closest(".analysis-section");
      var link = section && section.id
        ? document.querySelector('.analysis-index-link[href="#' + section.id + '"]') : null;
      var title = ((link || card.querySelector(".ac-title") || {}).textContent || name).trim();
      if (named[name] || said[title.toLowerCase()]) return;
      named[name] = true;
      said[title.toLowerCase()] = true;
      found.push({ group: "Analysis", label: title,
                   run: function () { if (dashboard()) dashboard().showAnalysis(name); } });
    });
    Array.prototype.forEach.call(document.querySelectorAll("#viewer-rail-tools .rail-btn"), function (button) {
      if (button.hidden) return;
      found.push({ group: "Viewer tool", label: button.getAttribute("aria-label"), run: function () {
        if (dashboard()) dashboard().navigate("viewer");
        if (window.FastMDXViewerRail) window.FastMDXViewerRail.choose(button.dataset.tool);
      } });
    });
    [["system", "System"], ["light", "Light"], ["dark", "Dark"]].forEach(function (scheme) {
      found.push({ group: "Scheme", label: scheme[1], run: function () {
        if (window.FastMDXFrame) window.FastMDXFrame.applyTheme(scheme[0], true);
      } });
    });
    if (window.FastMDXStop && window.FastMDXStop.running()) {
      // Asked once more, on the Overview beside how the run is going.
      found.push({ group: "Action", label: "Stop the run", run: function () {
        if (dashboard()) dashboard().navigate("overview");
        window.FastMDXStop.ask(el("health-stop-ask"));
      } });
    }
    found = found.concat(settingsOf());
    // Not offered beyond loopback, where /api/compared is refused.
    if (openStudy() && document.body.dataset.beyondLoopback !== "true") {
      found.push({ group: "Action", label: "Compare with\u2026",
                   hint: "Another study, or another run of this one, on the Compare page",
                   run: function () { open("compare"); } });
    }
    // Found by the word a returning person has for them too.
    found.push({ group: "Action", label: "Settings", hint: "Every setting, in one place",
                 also: "preferences",
                 run: function () { if (window.FastMDXSettings) window.FastMDXSettings.show(); } });
    found.push({ group: "Action", label: "Cite FastMDXplora", run: dialog("cite-dialog") });
    found.push({ group: "Action", label: "Every key", run: dialog("viewer-help"), keys: keysOf("keys") });
    found.push({ group: "Action", label: "Fold or show the sidebar", keys: keysOf("sidebar"),
                 run: keyRun("sidebar") });
    found.push({ group: "Action", label: "Fold or show the side panel", keys: keysOf("panel"),
                 run: keyRun("panel") });
    found.push({ group: "Action", label: "The Agent beside the page", keys: keysOf("agent-beside"),
                 run: function () { if (window.FastMDXAgentBeside) window.FastMDXAgentBeside.open(); } });
    var folder = el("study-folder");
    if (folder && !folder.hidden && folder.textContent.trim()) {
      found.push({ group: "Action", label: "Copy the study's folder", run: function () { folder.click(); } });
    }
    found = found.concat(filesOf());
    return found;
  }

  function keysOf(name) { return window.FastMDXKeys ? window.FastMDXKeys.of(name) : []; }
  function keyRun(name) {
    return function () {
      var key = window.FastMDXKeys && window.FastMDXKeys.list().find(function (k) { return k.name === name; });
      if (key && key.run) key.run();
    };
  }

  /* -------------------------------------------------------------------- */
  /* The workspace's studies and the open study's files, as listed         */
  /* -------------------------------------------------------------------- */

  /* Asked for when the palette opens, kept half a minute; what arrives
   * while it is open is added to what it lists. */
  function fetchList(which, path, read) {
    var kept = fetched[which];
    if (kept && Date.now() - kept.at < KEPT_MS) return;
    fetched[which] = { at: Date.now(), list: kept ? kept.list : [] };
    fetch(path, { cache: "no-store" }).then(function (r) { return r.json(); }).then(function (data) {
      fetched[which] = { at: Date.now(), list: read(data) || [] };
      var palette = el("palette");
      if (palette && !palette.hidden) {
        items = gather();
        render();
      }
    }).catch(function () { fetched[which] = null; });
  }

  /* Through the place's way to another study, which asks first whether
   * the switch would lose work here, on the page shown (Overview from a
   * page of no study), as the sidebar's way to it did (place.js). */
  function openFolder(path) {
    return function () {
      if (window.FastMDXPlace) window.FastMDXPlace.open(path);
    };
  }

  function studiesOf() {
    var kept = fetched.studies;
    return (kept ? kept.list : []).map(function (study) {
      var id = study.system && study.system !== study.name ? study.system + " \u00b7 " : "";
      return { group: "Study", label: id + study.name, hint: study.path,
               said: String(study.state || ""), run: openFolder(study.path) };
    });
  }

  /* The study open, and the runs it has or is one of. */
  function openStudy() {
    var app = dashboard() && dashboard().state.appState;
    return app && app.active_run ? app : null;
  }

  /* What the open study can be compared with: the other runs of its study
   * of several, then the workspace's other studies. */
  function compareWith() {
    var app = openStudy();
    if (!app) return [];
    var here = app.active_run;
    var runs = app.run_of ? app.run_of.runs || [] : Array.isArray(app.runs) ? app.runs : [];
    var with_ = function (path) {
      return function () { if (window.FastMDXCompare) window.FastMDXCompare.open(here, path); };
    };
    var found = runs.filter(function (run) { return run.path && run.path !== here; }).map(function (run) {
      return { group: "Run", label: run.label || run.run_id, hint: run.path,
               said: String(run.state || ""), run: with_(run.path) };
    });
    var kept = fetched.studies;
    (kept ? kept.list : []).forEach(function (study) {
      if (study.path === here) return;
      var id = study.system && study.system !== study.name ? study.system + " \u00b7 " : "";
      found.push({ group: "Study", label: id + study.name, hint: study.path,
                   said: String(study.state || ""), run: with_(study.path) });
    });
    return found;
  }

  function filesOf() {
    var kept = fetched.files;
    return (kept ? kept.list : []).map(function (file) {
      return { group: "File", label: file.path, hint: file.label || "",
               run: function () { if (window.FastMDXFrame) window.FastMDXFrame.previewPath(file.path); } };
    });
  }

  /* Each setting of this computer's by its words (Settings' You),
   * opening the Settings page at it. */
  function settingsOf() {
    var rows = document.querySelectorAll("#settings-you .pref-row");
    var found = [];
    Array.prototype.forEach.call(rows, function (row) {
      var words = row.querySelector(":scope > span");
      var label = words ? (words.firstChild && words.firstChild.nodeType === 3
        ? words.firstChild.textContent : words.textContent).trim() : "";
      var group = row.closest(".pref-group");
      var title = group ? ((group.querySelector(".pref-group-title") || {}).textContent || "").trim() : "";
      if (!label) return;
      // A row with no control of its own (the Agent's engine and mode) is
      // changed by its part's: the page opens at that part.
      var own = row.querySelector("input, select, textarea, button");
      var part = !own && group ? group.getAttribute("data-settings-part") : "";
      found.push({ group: "Setting", label: label, hint: title, run: function () {
        if (!window.FastMDXSettings) return;
        window.FastMDXSettings.show(part || undefined);
        if (part) return;
        setTimeout(function () {
          if (own) { own.focus(); row.scrollIntoView({ block: "nearest" }); }
        }, 50);
      } });
    });
    return found;
  }

  /* -------------------------------------------------------------------- */
  /* @ a residue, : a frame                                                */
  /* -------------------------------------------------------------------- */

  /* `@TRP23`, `@A:TRP23`, `@23`, `@A:23B`: the residues of the structure
   * shown with that number, and that name and chain where given. */
  var RESIDUE = /^@(?:([A-Za-z0-9]{1,4}):)?([A-Za-z]{1,4})?\s*(-?\d+)([A-Za-z]?)$/;
  var FRAME = /^:(\d+)$/;

  function viewer() { return window.FastMDXMoleculeViewer; }
  function engine() { var v = viewer(); return v && v.STATE && v.STATE.engine; }

  function residuesNamed(found) {
    var e = engine();
    if (!e || !e.structure || !e.structure()) return null;
    var chain = found[1] || "", resn = (found[2] || "").toUpperCase(), resi = Number(found[3]);
    var icode = (found[4] || "").toUpperCase();
    return e.residues().filter(function (r) {
      return r.resi === resi && (!resn || String(r.resn).toUpperCase() === resn)
        && (!chain || r.chain === chain) && String(r.icode || "").toUpperCase() === icode;
    });
  }

  /* Once the Viewer has a structure: it is opened first. */
  function whenTheViewerHasIt(then) {
    if (dashboard()) dashboard().navigate("viewer");
    var tries = 0;
    (function wait() {
      var e = engine();
      if (e && e.structure && e.structure()) { then(); return; }
      if ((tries += 1) < 150) setTimeout(wait, 100);
    }());
  }

  function selectResidue(query) {
    return function () {
      whenTheViewerHasIt(function () {
        var found = RESIDUE.exec(query);
        var residues = residuesNamed(found) || [];
        var v = viewer();
        if (!residues.length) {
          if (dashboard()) dashboard().toast("No residue " + query.slice(1) + " in the structure shown.", "error");
          return;
        }
        var atoms = engine().atomsOfResidues(residues.map(function (r) {
          return [r.chain, r.resi, r.icode || "", r.resn];
        }));
        v.selectResidues(residues, atoms, { announce: true });
        window.dispatchEvent(new CustomEvent("dashboard:residue-focus",
          { detail: { resi: residues[0].resi, chain: residues[0].chain || null } }));
      });
    };
  }

  function showFrame(frame) {
    return function () {
      if (window.FastMDXSeries && window.FastMDXSeries.openFrame) window.FastMDXSeries.openFrame(frame);
      else if (dashboard()) dashboard().navigate("viewer");
    };
  }

  /* `@` and part of a residue's name or number: the residues it fits, of
   * the structure the Viewer has; a way to it where it has none yet. */
  function residuesLike(text) {
    var e = engine();
    if (!e || !e.structure || !e.structure()) {
      return [{ group: "Viewer", label: "Open the Viewer to find its residues",
                hint: "@ then a residue's name or number, as @TRP23", run: go("viewer") }];
    }
    var want = text.replace(/\s+/g, "").toUpperCase();
    var found = [];
    e.residues().some(function (r) {
      var name = (r.chain ? r.chain + ":" : "") + r.resn + r.resi + (r.icode || "");
      var bare = r.resn + r.resi + (r.icode || "");
      if (want && name.toUpperCase().indexOf(want) !== 0 && bare.toUpperCase().indexOf(want) !== 0
          && String(r.resi).indexOf(want) !== 0) return false;
      found.push({ group: "Residue", label: name, hint: "Select it in the Viewer",
                   run: selectResidue("@" + name) });
      return found.length >= MOST;
    });
    return found.length ? found : [{ group: "Viewer", label: "No residue " + text + " in the structure shown",
                                     run: go("viewer") }];
  }

  /* The line a query of its own makes, before what its words find. */
  function asked(query) {
    var q = query.trim();
    var found = RESIDUE.exec(q);
    if (found) {
      var named = residuesNamed(found);
      var said = named && named.length
        ? named.map(function (r) { return (r.chain ? r.chain + ":" : "") + r.resn + r.resi + (r.icode || ""); }).join(", ")
        : "";
      return [{ group: "Viewer", label: "Select " + q.slice(1) + " in the Viewer",
                hint: said || "In the structure the Viewer shows", run: selectResidue(q) }];
    }
    if (q.charAt(0) === "@") return residuesLike(q.slice(1));
    found = FRAME.exec(q);
    if (found) {
      return [{ group: "Viewer", label: "Frame " + found[1] + " in the Viewer",
                hint: "Of the trajectory, as the analyses number it", run: showFrame(Number(found[1])) }];
    }
    return [];
  }

  /* Every word typed in its name or its kind; those whose name begins
   * with the first word first. */
  function match(query) {
    var own = asked(query);
    if (own.length) return own;
    var words = query.toLowerCase().split(/\s+/).filter(Boolean);
    if (!words.length) return items.slice(0, MOST);
    var scored = [];
    items.forEach(function (item, index) {
      var name = item.label.toLowerCase();
      var all = name + " " + item.group.toLowerCase() + " " + String(item.hint || "").toLowerCase() +
        " " + String(item.also || "");
      if (!words.every(function (w) { return all.indexOf(w) >= 0; })) return;
      var score = name.indexOf(words[0]) === 0 ? 0 : (" " + name).indexOf(" " + words[0]) >= 0 ? 1 : 2;
      scored.push({ item: item, score: score, index: index });
    });
    scored.sort(function (a, b) { return a.score - b.score || a.index - b.index; });
    return scored.slice(0, MOST).map(function (s) { return s.item; });
  }

  function render() {
    var list = el("palette-list");
    var input = el("palette-input");
    shown = match(input.value);
    at = Math.min(at, Math.max(0, shown.length - 1));
    list.replaceChildren();
    var last = "";
    shown.forEach(function (item, i) {
      if (item.group !== last) {
        var group = make("li", "palette-group", item.group);
        group.setAttribute("role", "presentation");
        list.appendChild(group);
        last = item.group;
      }
      var line = make("li", "palette-item");
      line.id = "palette-item-" + i;
      line.setAttribute("role", "option");
      line.setAttribute("aria-selected", String(i === at));
      line.dataset.index = String(i);
      line.appendChild(make("span", "palette-label", item.label));
      if (item.hint && item.hint !== item.label) {
        line.appendChild(make("span", "palette-hint", item.said || item.hint));
        line.title = item.hint;
      } else if (item.said) {
        line.appendChild(make("span", "palette-hint", item.said));
      }
      if (item.keys && item.keys.length) {
        var keys = make("span", "palette-keys");
        item.keys.forEach(function (k) { keys.appendChild(make("kbd", "", k)); });
        line.appendChild(keys);
      }
      list.appendChild(line);
    });
    if (!shown.length) list.appendChild(make("li", "palette-none", "Nothing by that name."));
    if (shown.length) input.setAttribute("aria-activedescendant", "palette-item-" + at);
    else input.removeAttribute("aria-activedescendant");
    var chosen = el("palette-item-" + at);
    if (chosen) chosen.scrollIntoView({ block: "nearest" });
  }

  function choose(index) {
    var item = shown[index];
    if (!item) return;
    if (window.FastMDXDialog) window.FastMDXDialog.close("palette");
    // After the palette has given focus back.
    setTimeout(item.run, 0);
  }

  /* Opened on everything, on the open study's files alone ("files"), or on
   * what the open study can be compared with ("compare"). */
  function open(only) {
    mode = only === "files" || only === "compare" ? only : "";
    fetchList("files", "/api/files", function (data) { return data && data.artifacts; });
    if (mode !== "files") fetchList("studies", "/api/studies", function (data) { return data && data.studies; });
    items = gather();
    at = 0;
    var input = el("palette-input");
    input.value = "";
    if (!input.dataset.everything) input.dataset.everything = input.placeholder;
    input.placeholder = mode === "files" ? "A file of the open study, by its name or folder"
      : mode === "compare" ? "A study, or a run, to compare the open one with"
      : input.dataset.everything;
    render();
    if (window.FastMDXDialog) window.FastMDXDialog.open("palette");
    setTimeout(function () { input.focus(); }, 0);
  }

  document.addEventListener("DOMContentLoaded", function () {
    var input = el("palette-input");
    var list = el("palette-list");
    if (!input || !list) return;
    input.addEventListener("input", function () { at = 0; render(); });
    input.addEventListener("keydown", function (event) {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        if (!shown.length) return;
        at = (at + (event.key === "ArrowDown" ? 1 : -1) + shown.length) % shown.length;
        render();
      } else if (event.key === "Enter") {
        event.preventDefault();
        choose(at);
      }
    });
    list.addEventListener("click", function (event) {
      var line = event.target.closest(".palette-item");
      if (line) choose(Number(line.dataset.index));
    });
    list.addEventListener("mousemove", function (event) {
      var line = event.target.closest(".palette-item");
      if (!line || Number(line.dataset.index) === at) return;
      at = Number(line.dataset.index);
      Array.prototype.forEach.call(list.querySelectorAll(".palette-item"), function (each) {
        each.setAttribute("aria-selected", String(each === line));
      });
      input.setAttribute("aria-activedescendant", line.id);
    });
    document.addEventListener("keydown", function (event) {
      if (!(event.metaKey || event.ctrlKey) || event.altKey || event.shiftKey) return;
      if (String(event.key).toLowerCase() !== "k") return;
      event.preventDefault();
      if (event.repeat) return;
      var palette = el("palette");
      if (palette && !palette.hidden) {
        if (window.FastMDXDialog) window.FastMDXDialog.close("palette");
        return;
      }
      // Not over another dialog: it has the keyboard.
      if (document.querySelector(".agent-dialog:not([hidden])")) return;
      open();
    });
  });

  window.FastMDXPalette = { open: open };
}());
