/* The Report page: the study's report, as a document, in the centre.
 *
 * Fetched when the page is shown and when the run's state changes, since
 * the report is written last and a person may be on this page while the
 * run finishes. Anything the report phase could not produce is a notice
 * above the document, with the reason the phase gave -- a PDF without
 * WeasyPrint is the usual one -- rather than a dead download button.
 */
(function () {
  "use strict";

  function el(id) { return document.getElementById(id); }

  var LABELS = { pdf: "PDF", slides: "Slides", bundle: "Bundle",
                 markdown: "Markdown", summary: "Summary figure" };
  /* What each download is, said beside its name. */
  var KINDS = { pdf: "the report, to read or print", slides: "a deck to present",
                bundle: "the report, its figures and data, zipped",
                markdown: "the report as text, to edit", summary: "one figure of the whole study" };
  var DOWNLOAD_ICON = '<svg class="line-icon" viewBox="0 0 24 24" width="16" height="16" fill="none" '
    + 'stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" '
    + 'aria-hidden="true"><path d="M12 4v11M7.5 10.5L12 15l4.5-4.5M5 20h14"/></svg>';

  function item(tag, label, kind) {
    var line = document.createElement(tag);
    line.className = "menu-item";
    line.setAttribute("role", "menuitem");
    var name = document.createElement("span");
    name.className = "menu-item-name";
    name.textContent = label;
    line.appendChild(name);
    if (kind) {
      var said = document.createElement("span");
      said.className = "menu-item-kind";
      said.textContent = kind;
      line.appendChild(said);
    }
    return line;
  }

  /* The downloads behind one button: each was a button of its own along
   * the page's head. Without a PDF (no WeasyPrint where the report was
   * written), the browser prints the document, and saves it as a PDF from
   * its dialog: the print stylesheet gives it alone, in black on white. */
  function downloadMenu(offered) {
    var menu = document.createElement("details");
    menu.className = "menu-btn report-download";
    menu.id = "report-download";
    var head = document.createElement("summary");
    head.className = "primary-btn";
    head.setAttribute("aria-haspopup", "menu");
    head.innerHTML = DOWNLOAD_ICON + "<span>Download</span>";
    menu.appendChild(head);
    var list = document.createElement("div");
    list.className = "menu-list";
    list.setAttribute("role", "menu");
    list.setAttribute("aria-label", "Download");
    if (!offered.pdf) {
      var print = item("button", "Print or save as PDF", "the browser's print dialog");
      print.type = "button";
      print.id = "report-print";
      print.title = "Print the report, or choose Save as PDF in the print dialog";
      print.addEventListener("click", function () { menu.open = false; window.print(); });
      list.appendChild(print);
    }
    Object.keys(LABELS).forEach(function (key) {
      if (!offered[key]) return;
      var a = item("a", LABELS[key], KINDS[key]);
      a.href = offered[key];
      a.setAttribute("download", "");
      a.dataset.download = key;
      a.addEventListener("click", function () { menu.open = false; });
      list.appendChild(a);
    });
    menu.appendChild(list);
    menu.addEventListener("toggle", function () {
      if (!menu.open) return;
      var first = list.querySelector(".menu-item");
      if (first && document.activeElement === head) first.focus();
    });
    menu.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && menu.open) {
        menu.open = false;
        head.focus();
      } else if ((event.key === "ArrowDown" || event.key === "ArrowUp") && menu.open) {
        event.preventDefault();
        var all = Array.prototype.slice.call(list.querySelectorAll(".menu-item"));
        var at = all.indexOf(document.activeElement);
        var next = all[(at + (event.key === "ArrowDown" ? 1 : -1) + all.length) % all.length];
        if (next) next.focus();
      }
    });
    return menu;
  }

  // Open, the menu closes on a click anywhere else.
  document.addEventListener("click", function (event) {
    var open = document.querySelector("#report-download[open]");
    if (open && !open.contains(event.target)) open.open = false;
  });

  function render(data) {
    var doc = el("report-document");
    var empty = el("report-empty");
    var notices = el("report-notices");
    var downloads = el("report-downloads");
    if (!doc) return;

    /* A fetch that failed says nothing about the report: one written
     * stays, and the next load tries again. It was hidden, and the next
     * load that succeeded found the same text already rendered and
     * returned, so the document stayed hidden, its figures and chips
     * with it, until the report changed. */
    if (!data && doc.dataset.rendered) return;

    if (!data || !data.ok) {
      delete doc.dataset.rendered;
      doc.hidden = true;
      empty.hidden = false;
      notices.hidden = true;
      downloads.innerHTML = "";
      /* A study of runs says which of them are still to come. */
      var why = el("report-empty-reason");
      if (why) why.textContent = data && data.reason && data.pending ? data.reason : "";
      return;
    }

    /* Replace the document only when it changed. The page reloaded on
     * every poll while visible and rebuilt the DOM each time, which
     * flickered and threw the scroll back to wherever the browser landed
     * -- the citation, mostly. The report is finished text; it changes
     * when the run writes a new one, not every two seconds. */
    if (doc.dataset.rendered === data.html) return;
    doc.dataset.rendered = data.html;
    doc.innerHTML = data.html;
    doc.hidden = false;
    empty.hidden = true;

    var sub = el("report-subtitle");
    if (sub) sub.textContent = data.generated ? "Generated " + data.generated : "The study, written up.";

    downloads.innerHTML = "";
    downloads.appendChild(downloadMenu(data.downloads || {}));

    notices.innerHTML = "";
    var rows = data.not_produced || [];
    notices.hidden = !rows.length;
    rows.forEach(function (row) {
      var block = document.createElement("div");
      block.className = "report-notice";
      var head = document.createElement("span");
      head.className = "report-notice-kind";
      head.textContent = "not produced · " + row.artifact;
      block.appendChild(head);
      block.appendChild(document.createTextNode(row.reason || ""));
      notices.appendChild(block);
    });

    /* Figures the report refers to by relative path live under
     * report/ or analysis/; point them at the artifacts route. */
    /* A study of runs keeps its comparison, and its figures, under
     * comparison/ rather than report/; the payload says which. */
    var under = "/artifacts/" + (data.figures_under || "report") + "/";
    Array.prototype.forEach.call(doc.querySelectorAll("img"), function (img) {
      var src = img.getAttribute("src") || "";
      if (src && !/^(https?:|\/|data:)/.test(src)) {
        img.src = under + src.replace(/^\.\//, "");
      }
    });
    Array.prototype.forEach.call(doc.querySelectorAll("a[href]"), function (a) {
      var href = a.getAttribute("href") || "";
      if (href && !/^(https?:|#|\/|mailto:)/.test(href)) {
        a.href = under + href.replace(/^\.\//, "");
        a.target = "_blank";
        a.rel = "noopener";
      }
    });
    chipFigures(doc, data.figure_provenance || {});
  }

  /* Under each figure an analysis plotted, the chip the Analysis page gives
   * it: what made it and the command that plots it again. The records are
   * the study's (figure_provenance.py); the chip and its panel are the
   * Analysis page's own, from dashboard.js. Under a comparison's overlay or
   * trend, the runs it was plotted from and the releases behind them
   * (comparison/figures.json). */
  var provenance = {};
  function chipFigures(doc, found) {
    provenance = found;
    var dashboard = window.FastMDXDashboard;
    if (!dashboard || !dashboard.figureChip) return;
    Array.prototype.forEach.call(doc.querySelectorAll("img"), function (img) {
      var named = /\/analysis\/([a-z][a-z0-9_]*)\/[^\/?#]+\.png(?:[?#]|$)/.exec(img.src || "")
        || /\/comparison\/((?:overlay|trend)_[a-z0-9_]+)\.png(?:[?#]|$)/.exec(img.src || "");
      var chip = named ? dashboard.figureChip(named[1], provenance[named[1]]) : "";
      if (!chip) return;
      var block = img.closest("p, figure") || img;
      var made = document.createElement("div");
      made.className = "report-figure-made";
      made.innerHTML = chip + '<div class="figure-provenance" hidden></div>';
      block.parentNode.insertBefore(made, block.nextSibling);
    });
    dashboard.listenForFigureChips(doc, function (name) { return provenance[name]; });
  }

  /* A failed fetch is tried again, a few times and further apart each
   * time: a finished study's state does not change, so nothing else would
   * ask again, and the page would say there is no report. */
  var retrying = 0;
  function load() {
    return fetch("/api/report").then(function (r) { return r.json(); })
      .then(function (data) { retrying = 0; render(data); })
      .catch(function () {
        render(null);
        if (retrying < 5) {
          retrying += 1;
          setTimeout(load, 1000 * retrying);
        }
      });
  }

  /* Writing the report again: the report phase alone on the study open,
   * from its records and the report settings it recorded, as `fastmdx
   * report --output <study> --rerun` does. Offered while a study is open and
   * nothing runs (a study of several runs has each run's written again);
   * pressing it asks once more, saying what is written again, what is
   * kept aside and that nothing is simulated or analysed, and only a
   * second press starts it. */
  /* When the writing started here began, as the server recorded it; null
   * while none is waited for. A state sent before it began is not its end. */
  var writing = null;
  var several = false;
  var WRITE_TITLE = "Write the report again from this study's records, " +
    "simulating and analysing nothing";

  function node(tag, cls, text) {
    var made = document.createElement(tag);
    if (cls) made.className = cls;
    if (text) made.textContent = text;
    return made;
  }

  function say(text) {
    var ask = el("report-write-ask");
    if (!ask) return;
    ask.innerHTML = "";
    ask.appendChild(node("span", "fix-said", text));
    ask.hidden = false;
  }

  function startWriting() {
    say("Starting\u2026");
    fetch("/api/report/write", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}"
    }).then(function (res) { return res.json(); }).then(function (d) {
      if (d && d.ok) {
        writing = (d.state && d.state.started_at) || "";
        say("Writing it again. Its log is in the side panel, and the report is shown here once written.");
      } else {
        say((d && d.error) || "Could not start it.");
      }
    }).catch(function () { say("Could not reach the server to start it."); });
  }

  function askFirst() {
    var ask = el("report-write-ask");
    if (!ask) return;
    ask.innerHTML = "";
    ask.appendChild(node("span", "fix-said",
      "Write " + (several ? "each run's report" : "the report") + " again from " +
      "this study's records? The report, its slides, PDF, dashboard.html and " +
      "bundle are written again as the study's report settings ask. The report " +
      "there now is kept in previous/, in place of what was kept there before. " +
      "Nothing is simulated or analysed."));
    var yes = node("button", "primary-btn fix-confirm", "Yes, write it");
    yes.type = "button";
    yes.addEventListener("click", startWriting);
    var no = node("button", "ghost-btn fix-cancel", "Not now");
    no.type = "button";
    no.addEventListener("click", function () { ask.hidden = true; ask.innerHTML = ""; });
    ask.appendChild(yes);
    ask.appendChild(no);
    ask.hidden = false;
    yes.focus();
  }

  /* Shown while a study is open; held while anything runs. A writing
   * started here that has ended reloads the report. */
  function offerWriting(app) {
    var button = el("report-write");
    if (!button) return;
    app = app || {};
    several = Array.isArray(app.runs) && app.runs.length > 0;
    var running = !!app.process_running;
    button.hidden = !app.active_run;
    button.disabled = running;
    button.title = running
      ? "A run is going. The report can be written again once it ends."
      : WRITE_TITLE;
    if (writing === null || running || app.started_at !== writing) return;
    if (app.returncode === null || app.returncode === undefined) return;
    writing = null;
    if (app.returncode !== 0 || app.error) {
      say(app.error || "It stopped before the report was written again; its log is in the side panel.");
      return;
    }
    say("Written again.");
    load();
  }

  document.addEventListener("DOMContentLoaded", function () {
    if (!el("report-document")) return;
    load();
    var write = el("report-write");
    if (write) write.addEventListener("click", askFirst);
    if (window.FastMDXDashboard) offerWriting((window.FastMDXDashboard.state || {}).appState);
    if (window.FastMDXDashboard && window.FastMDXDashboard.on) {
      window.FastMDXDashboard.on("app-state", function (app) {
        offerWriting(app);
        // Reload only when the page is showing; the fetch is cheap but
        // rendering a 20 KB document every poll is not.
        var page = document.querySelector('.page[data-page="report"]');
        if (page && !page.hidden) load();
      });
      window.FastMDXDashboard.on("navigate", function (detail) {
        if (detail === "report" || (detail && detail.page === "report")) load();
      });
    }
  });

  window.FastMDXReport = { load: load };
})();
