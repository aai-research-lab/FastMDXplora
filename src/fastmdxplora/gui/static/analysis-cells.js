/* Each analysis on the Analysis page as a cell, as a notebook's cells are:
 * under its heading, what it determined (the results table's numbers, its
 * own), what it was run with (the frames, the selection and the options
 * its record keeps, figure_provenance.py) and Analyze again for it alone.
 * Its plots then each zoom, pan and save (plot-tools.js); a time range
 * dragged across one is said at the top of the page, with Show all.
 */
(function () {
  "use strict";

  function byId(id) { return document.getElementById(id); }

  function make(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  function page() { return document.querySelector('.page[data-page="analysis"]'); }

  function provenance() {
    var board = window.FastMDXDashboard;
    try {
      return (board && board.state.figureProvenance) || {};
    } catch (error) {
      return {};
    }
  }

  function rows() {
    var analysis = window.FastMDXAnalysisPage;
    return analysis && analysis.overviewRows ? analysis.overviewRows() : [];
  }

  /* Whether the analyses' own records are in: until then a cell does not
   * know how many frames its analysis read (thermodynamics reads the
   * state log's rows), only how many the trajectory had. */
  function rowsRead() {
    var analysis = window.FastMDXAnalysisPage;
    return !analysis || !analysis.overviewRead || analysis.overviewRead();
  }

  function ordinal(n) {
    var k = n % 100;
    if (k >= 11 && k <= 13) return n + "th";
    return n + ({ 1: "st", 2: "nd", 3: "rd" }[n % 10] || "th");
  }

  /* What it was run with, in a line: the frames, the atoms, its options. */
  function params(record, row) {
    var host = make("span", "cell-params");
    if (!record) return null;
    var parts = [];
    // The frames the analysis itself read, as the results table says them:
    // thermodynamics reads the state log's rows, not the trajectory's frames.
    var own = ((row && row.quantities) || []).map(function (q) { return q.of_frames; })
      .filter(function (n) { return n != null; })[0];
    if (own != null && own !== record.frames) {
      parts.push(own + " frames");
    } else if (!row && !rowsRead()) {
      // Not said until the records come: the trajectory's count said
      // meanwhile was wrong for an analysis that reads other rows, and the
      // cell is drawn again once they are read (analysis-page.js).
    } else if (record.frames != null) {
      var stride = Number(record.stride || 1);
      parts.push(record.frames + " frames" + (stride > 1 ? ", every " + ordinal(stride) : ""));
    }
    if (record.first != null || record.last != null) {
      parts.push("frames " + (record.first != null ? record.first : 0) + " to " +
                 (record.last != null ? record.last : "the end"));
    }
    var options = record.options && typeof record.options === "object" ? record.options : {};
    function simple(value) {
      return typeof value === "string" ? value !== "" : typeof value === "number" || typeof value === "boolean";
    }
    var said = Object.keys(options).filter(function (key) {
      var value = options[key];
      return simple(value) || (Array.isArray(value) && value.length && value.every(simple));
    }).slice(0, 4).map(function (key) {
      var value = options[key];
      return key + " " + (Array.isArray(value) ? value.join(", ") : String(value));
    });
    host.appendChild(document.createTextNode(parts.join(" \u00b7 ")));
    if (row && row.atoms_said) {
      // The atoms it reads, as it says them (the image check: the heavy
      // atoms of the macromolecules), not the analyses' scope.
      host.appendChild(document.createTextNode((parts.length ? " \u00b7 " : "") + row.atoms_said));
    } else if (record.selection) {
      host.appendChild(document.createTextNode((parts.length ? " \u00b7 " : "") + "atoms "));
      host.appendChild(make("code", "", String(record.selection)));
    }
    if (said.length) {
      host.appendChild(document.createTextNode(" \u00b7 " + said.join(", ") +
        (Object.keys(options).length > said.length ? ", \u2026" : "")));
    }
    host.title = "What it was run with, as its record keeps it; the chip under its figure has all of it " +
      "and the command that makes it again";
    return host.textContent ? host : null;
  }

  /* What it determined, as the results table says it. */
  function values(row) {
    var quantities = row && Array.isArray(row.quantities) ? row.quantities : [];
    if (!quantities.length) {
      // A profile, map or table: its image note, as its row says it.
      if (!row || !row.noted) return null;
      var only = make("span", "cell-values");
      var note = make("span", "cell-value");
      note.appendChild(make("span", "cell-noted", row.noted));
      if (row.noted_why) note.title = row.noted_why;
      only.appendChild(note);
      return only;
    }
    var host = make("span", "cell-values");
    quantities.slice(0, 4).forEach(function (q) {
      var item = make("span", "cell-value");
      item.dataset.determined = String(!!q.determined);
      item.appendChild(make("b", "", q.key === "mean" ? "Mean" : q.label));
      item.appendChild(make("span", "mono", q.said));
      var titles = [];
      if (!q.determined && q.why) titles.push("Not determined: " + q.why);
      if (q.noted) {
        // Its image note beside it, as the table above says it.
        item.appendChild(make("span", "cell-noted", q.noted));
        if (q.noted_why) titles.push(q.noted_why);
      }
      if (titles.length) item.title = titles.join("\n\n");
      host.appendChild(item);
    });
    if (quantities.length > 4) host.appendChild(make("span", "muted", "+" + (quantities.length - 4) + " in the table above"));
    return host;
  }

  /* Analyze again POSTs, which a GUI served beyond this computer refuses:
   * not offered there. Elsewhere shown as the page's own is, now and as
   * the run's state changes. */
  function beyondLoopback() { return document.body.dataset.beyondLoopback === "true"; }

  function again(name) {
    var offered = byId("analysis-again");
    if (!offered || beyondLoopback()) return null;
    var button = make("button", "chip-btn cell-again", "Analyze again");
    button.type = "button";
    button.hidden = offered.hidden;
    button.disabled = offered.disabled;
    button.dataset.title = "Analyze this study's frames again with " + name +
      " alone ticked; the panel above says what it writes again before it starts";
    button.title = offered.disabled ? offered.title : button.dataset.title;
    button.addEventListener("click", function () {
      if (window.FastMDXAgain) window.FastMDXAgain.open([name]);
    });
    return button;
  }

  function render() {
    var root = page();
    if (!root) return;
    var made = provenance();
    var byName = {};
    rows().forEach(function (row) { byName[row.analysis] = row; });
    Array.prototype.forEach.call(root.querySelectorAll(".analysis-section"), function (section) {
      var old = section.querySelector(":scope > .analysis-cell-head");
      if (old) old.remove();
      var card = section.querySelector(".analysis-card[data-analysis]");
      var name = card ? card.getAttribute("data-analysis") : "";
      if (!name) return;
      section.dataset.cell = name;
      var head = make("div", "analysis-cell-head");
      head.dataset.analysis = name;
      [values(byName[name]), params(made[name], byName[name]), again(name)].forEach(function (part) {
        if (part) head.appendChild(part);
      });
      if (!head.childElementCount) return;
      var heading = section.querySelector(".analysis-section-heading");
      if (heading && heading.nextSibling) section.insertBefore(head, heading.nextSibling);
      else section.appendChild(head);
    });
    sayRange();
  }

  /* The page's time range, said once at the top, with Show all. */
  function sayRange() {
    var root = page();
    var sections = byId("analysis-sections");
    if (!root || !sections) return;
    var line = byId("analysis-cell-range");
    if (!line) {
      line = make("div", "analysis-cell-range");
      line.id = "analysis-cell-range";
      line.setAttribute("role", "status");
      line.hidden = true;
      sections.parentNode.insertBefore(line, sections);
    }
    var tools = window.FastMDXPlotTools;
    var range = tools ? tools.range() : null;
    line.replaceChildren();
    line.hidden = !range;
    if (!range) return;
    var said;
    if (range.label === "Frame") {
      said = "frames " + Math.round(range.from) + " to " + Math.round(range.to);
    } else {
      var ps = Math.max(Math.abs(range.from), Math.abs(range.to)) < 1;
      var k = ps ? 1000 : 1;
      said = +(range.from * k).toPrecision(4) + " to " + +(range.to * k).toPrecision(4) + (ps ? " ps" : " ns");
    }
    line.appendChild(make("span", "", "Every plot on the simulation's clock shows " + said +
      ", and the Viewer's timeline marks it."));
    var all = make("button", "plot-tool", "Show all");
    all.type = "button";
    all.addEventListener("click", function () { tools.clearRange(); });
    line.appendChild(all);
  }

  document.addEventListener("fmx:time-range", sayRange);
  /* Analyze again follows the page's own as the run's state changes:
   * analyze-again.js sets both on the same state. */
  window.FastMDXCells = { render: render };
}());
