/* Everything the GUI can go to or do, found by typing (⌘K, or Ctrl+K).
 *
 * A page, a study under Recent, an analysis of the study open, a tool of
 * the Viewer, the scheme, Preferences, Cite, the Viewer's keys: each is a
 * line, found by the words typed (each word anywhere in its name or its
 * kind), chosen with the arrows and Enter or a click. It is built from the
 * page as it stands when opened, so it lists only what is there to go to.
 */
(function () {
  "use strict";

  var MOST = 60;
  var items = [];
  var shown = [];
  var at = 0;

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
    pages.forEach(function (page) {
      if (document.querySelector('.page[data-page="' + page[0] + '"]')) {
        found.push({ group: "Page", label: page[1], run: go(page[0]) });
      }
    });
    Array.prototype.forEach.call(document.querySelectorAll(".sidebar-recent-item"), function (item) {
      var name = item.getAttribute("aria-label") || item.textContent.trim();
      if (!name) return;
      found.push({ group: "Recent study", label: name.split(",")[0], hint: name,
                   run: function () { item.click(); } });
    });
    var named = {};
    Array.prototype.forEach.call(document.querySelectorAll(".analysis-card[data-analysis]"), function (card) {
      var name = card.getAttribute("data-analysis");
      if (named[name]) return;
      named[name] = true;
      var title = (card.querySelector(".ac-title") || {}).textContent || name;
      found.push({ group: "Analysis", label: title.trim(),
                   run: function () { if (dashboard()) dashboard().showAnalysis(name); } });
    });
    Array.prototype.forEach.call(document.querySelectorAll("#viewer-rail .rail-btn"), function (button) {
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
    found.push({ group: "Action", label: "Preferences", run: dialog("prefs-dialog") });
    found.push({ group: "Action", label: "Cite FastMDXplora", run: dialog("cite-dialog") });
    found.push({ group: "Action", label: "The Viewer's keys", run: function () {
      if (dashboard()) dashboard().navigate("viewer");
      dialog("viewer-help")();
    } });
    var folder = el("study-folder");
    if (folder && !folder.hidden && folder.textContent.trim()) {
      found.push({ group: "Action", label: "Copy the study's folder", run: function () { folder.click(); } });
    }
    return found;
  }

  /* Every word typed in its name or its kind; those whose name begins
   * with the first word first. */
  function match(query) {
    var words = query.toLowerCase().split(/\s+/).filter(Boolean);
    if (!words.length) return items.slice(0, MOST);
    var scored = [];
    items.forEach(function (item, index) {
      var name = item.label.toLowerCase();
      var all = name + " " + item.group.toLowerCase();
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
      if (item.hint && item.hint !== item.label) line.title = item.hint;
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

  function open() {
    items = gather();
    at = 0;
    var input = el("palette-input");
    input.value = "";
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
