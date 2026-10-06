/* The Viewer's tools on a rail, one shown at a time.
 *
 * The settings beside the molecule were sixteen sections one under the
 * other, the one wanted often a long scroll down a narrow column. Each is
 * now a button on a rail at the column's edge, named on hover, and the
 * column shows the one chosen. A section the study has nothing for (a
 * ligand's tools without a ligand, the runs of a study of one) has no
 * button while it is hidden. The tool chosen is kept in this browser.
 * Opening a section from elsewhere in the page (a residue clicked, a saved
 * view shown) chooses it. `[` and `]` choose the tool above or below.
 * What is picked and measured (Information) is shown under every tool.
 */
(function () {
  "use strict";

  var KEPT = "fmx.viewerTool";
  var FIRST = "side-display";
  /* Each tool's line icon (24 x 24, stroked as the sidebar's). */
  var ICONS = {
    "side-view": '<path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
    "side-display": '<path d="M12 3a9 9 0 1 0 0 18c1.1 0 1.5-.8 1.5-1.6 0-1.2-1-1.4-1-2.4 0-.8.6-1.5 1.5-1.5H17a4 4 0 0 0 4-4c0-4.7-4-8.5-9-8.5z"/><circle cx="7.5" cy="11" r="1.2"/><circle cx="10.5" cy="7" r="1.2"/><circle cx="15" cy="7.5" r="1.2"/>',
    "side-runs": '<path d="M12 3l9 5-9 5-9-5z"/><path d="M3 13l9 5 9-5"/>',
    "side-selections": '<path d="M5 3l14 8-6 2-2 6z"/>',
    "side-ligand": '<path d="M12 3l7.8 4.5v9L12 21l-7.8-4.5v-9z"/><circle cx="12" cy="12" r="3.2"/>',
    "side-occupancy": '<path d="M12 3c3 4 6 7.2 6 10.5A6 6 0 0 1 6 13.5C6 10.2 9 7 12 3z"/>',
    "side-motion": '<path d="M4 12h16M16 8l4 4-4 4M8 8l-4 4 4 4"/>',
    "side-states": '<circle cx="7" cy="12" r="3.5"/><circle cx="17" cy="12" r="3.5"/><path d="M10.5 12h3"/>',
    "side-rama": '<path d="M4 4v16h16"/><circle cx="9" cy="14" r="1.4"/><circle cx="12" cy="9" r="1.4"/><circle cx="16" cy="12" r="1.4"/>',
    "side-cmap": '<rect x="4" y="4" width="16" height="16" rx="1"/><path d="M4 9.3h16M4 14.7h16M9.3 4v16M14.7 4v16"/>',
    "side-pocket": '<path d="M5 6c0 7 3 12 7 12s7-5 7-12"/><path d="M8 6h8"/>',
    "side-beside": '<rect x="3" y="6" width="8" height="12" rx="1.5"/><rect x="13" y="6" width="8" height="12" rx="1.5"/>',
    "side-chains": '<path d="M10 14a4 4 0 0 1 0-5.6l2-2a4 4 0 0 1 5.6 5.6l-1 1"/><path d="M14 10a4 4 0 0 1 0 5.6l-2 2a4 4 0 0 1-5.6-5.6l1-1"/>',
    "side-saved": '<path d="M7 4h10v16l-5-4-5 4z"/>',
    "side-movie": '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 9h18M3 15h18M8 5v4M8 15v4M16 5v4M16 15v4"/>',
    "side-info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5v.5"/>',
  };
  var FALLBACK = '<circle cx="12" cy="12" r="7"/>';

  var side = null;
  var rail = null;
  var chosen = "";
  var settled = false;
  /* Whether the person has chosen a tool in this page: until then, the one
   * kept is chosen as soon as the study shows it. */
  var picked = false;

  /* The tools: each section but what is shown under every tool (what is
   * picked and measured). */
  function sections() {
    return side ? Array.prototype.filter.call(side.children, function (node) {
      return node.matches && node.matches('details.side-section[id]:not([data-rail="pinned"])');
    }) : [];
  }

  function titleOf(section) {
    var summary = section.querySelector(":scope > summary");
    return summary ? summary.textContent.trim() : section.id;
  }

  function keep(id) {
    try { localStorage.setItem(KEPT, id); } catch (e) { /* not kept */ }
  }

  function kept() {
    try { return localStorage.getItem(KEPT) || ""; } catch (e) { return ""; }
  }

  function build() {
    rail.replaceChildren();
    sections().forEach(function (section) {
      // A tool's panel, named by its button; its head is a title, not a
      // fold (it opens and closes nothing on the rail).
      section.setAttribute("role", "tabpanel");
      section.setAttribute("aria-labelledby", "rail-" + section.id);
      var head = section.querySelector(":scope > summary");
      if (head) head.tabIndex = -1;
      var button = document.createElement("button");
      button.type = "button";
      button.className = "rail-btn";
      button.id = "rail-" + section.id;
      button.dataset.tool = section.id;
      button.setAttribute("role", "tab");
      button.setAttribute("aria-controls", section.id);
      button.setAttribute("aria-label", titleOf(section));
      button.title = titleOf(section);
      button.innerHTML = '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true">' +
        (ICONS[section.id] || FALLBACK) + "</svg>";
      rail.appendChild(button);
    });
  }

  /* The rail as the sections stand: a hidden section has no button. */
  function mirror() {
    var shown = [];
    sections().forEach(function (section) {
      var button = rail.querySelector('[data-tool="' + section.id + '"]');
      if (!button) return;
      // A section is hidden by its attribute, or by a rule of its own
      // (the ligand's, without a ligand); not by the rail.
      var hidden = section.hidden || section.matches(".side-section:has(> #ligand-tools[hidden])");
      // Set only when it changes: the rail is watched with the sections.
      if (button.hidden !== hidden) button.hidden = hidden;
      if (!hidden) shown.push(section.id);
    });
    var wanted = kept();
    if (!picked && wanted && wanted !== chosen && shown.indexOf(wanted) >= 0) {
      // The tool kept, once the study has shown it (most are shown only as
      // what the study holds is learned).
      choose(wanted, false);
    } else if (shown.indexOf(chosen) < 0) {
      choose(shown.indexOf(FIRST) >= 0 ? FIRST : shown[0] || "", false);
    }
  }

  /* One tool shown: its section open, the rest out of the column. */
  function choose(id, remember) {
    if (!side) return;
    var section = id ? document.getElementById(id) : null;
    if (!section || section.parentNode !== side) return;
    chosen = id;
    side.dataset.toolChosen = id;
    sections().forEach(function (node) {
      node.classList.toggle("is-chosen", node === section);
    });
    if (!section.open) section.open = true;
    Array.prototype.forEach.call(rail.querySelectorAll(".rail-btn"), function (button) {
      var on = button.dataset.tool === id;
      button.setAttribute("aria-selected", String(on));
      button.tabIndex = on ? 0 : -1;
    });
    side.scrollTop = 0;
    if (remember) keep(id);
    document.dispatchEvent(new CustomEvent("fmx:viewer-tool", { detail: { tool: id } }));
  }

  function choosing(id) {
    picked = true;
    choose(id, true);
  }

  function step(by) {
    var shown = Array.prototype.filter.call(rail.querySelectorAll(".rail-btn"), function (b) {
      return !b.hidden;
    });
    if (!shown.length) return;
    var at = shown.findIndex(function (b) { return b.dataset.tool === chosen; });
    var next = by === "first" ? shown[0] : by === "last" ? shown[shown.length - 1]
      : shown[(Math.max(0, at) + by + shown.length) % shown.length];
    choosing(next.dataset.tool);
    return next;
  }

  /* Said as it is laid out: a column beside the tool, or a row above it
   * where the Viewer's columns stack. */
  function orient() {
    var row = getComputedStyle(rail).flexDirection === "row";
    rail.setAttribute("aria-orientation", row ? "horizontal" : "vertical");
  }

  function init() {
    side = document.querySelector(".viewer-side");
    rail = document.getElementById("viewer-rail");
    if (!side || !rail) return;
    build();
    rail.hidden = false;
    side.classList.add("has-rail");
    mirror();
    orient();
    window.addEventListener("resize", orient);
    rail.addEventListener("click", function (event) {
      var button = event.target.closest(".rail-btn");
      if (button) choosing(button.dataset.tool);
    });
    // The arrows move along the rail, as along any list of tabs.
    rail.addEventListener("keydown", function (event) {
      var by = { ArrowDown: 1, ArrowRight: 1, ArrowUp: -1, ArrowLeft: -1,
                 Home: "first", End: "last" }[event.key];
      if (!by) return;
      event.preventDefault();
      event.stopPropagation();
      var next = step(by);
      if (next) next.focus();
    });
    // A section shown or hidden as the study is learned.
    new MutationObserver(function (changes) {
      if (changes.some(function (change) { return !rail.contains(change.target); })) mirror();
    }).observe(side, {
      subtree: true, attributes: true, attributeFilter: ["hidden"],
    });
    // A section opened from elsewhere (a residue clicked, a view shown) is
    // the tool wanted; not the sections the page opens as it loads.
    setTimeout(function () { settled = true; }, 1500);
    side.addEventListener("toggle", function (event) {
      var section = event.target;
      if (!section.matches || !section.matches("details.side-section")) return;
      if (settled && section.open && section.id !== chosen && !section.hidden) choosing(section.id);
      // The tool shown stays open: its head folds nothing on the rail.
      if (!section.open && section.id === chosen) section.open = true;
    }, true);
    document.addEventListener("keydown", function (event) {
      if (document.documentElement.dataset.page !== "viewer") return;
      if (event.ctrlKey || event.metaKey || event.altKey || event.isComposing) return;
      var target = event.target;
      if (target && (target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName))) return;
      if (document.querySelector(".agent-dialog:not([hidden])")) return;
      // A movie being made keeps its tool (viewer-movie.js).
      var viewer = window.FastMDXMoleculeViewer;
      if (viewer && viewer.STATE && viewer.STATE.makingMovie) return;
      if (event.key === "?") {
        event.preventDefault();
        if (window.FastMDXDialog) window.FastMDXDialog.open("viewer-help");
      } else if (event.key === "[" || event.key === "]") {
        event.preventDefault();
        step(event.key === "]" ? 1 : -1);
      }
    });
  }

  document.addEventListener("DOMContentLoaded", init);

  window.FastMDXViewerRail = {
    choose: function (id) { choosing(id); },
    get chosen() { return chosen; },
  };
}());
