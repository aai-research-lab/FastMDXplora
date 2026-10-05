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
    Object.keys(LABELS).forEach(function (key) {
      if (!data.downloads || !data.downloads[key]) return;
      var a = document.createElement("a");
      a.className = key === "pdf" ? "primary-btn" : "ghost-btn";
      a.href = data.downloads[key];
      a.textContent = LABELS[key];
      a.setAttribute("download", "");
      downloads.appendChild(a);
    });
    /* Without a PDF (no WeasyPrint where the report was written), the
     * browser prints the document, and saves it as a PDF from its dialog:
     * the print stylesheet gives it alone, in black on white. */
    if (!data.downloads || !data.downloads.pdf) {
      var print = document.createElement("button");
      print.type = "button";
      print.className = "primary-btn";
      print.id = "report-print";
      print.textContent = "Print or save as PDF";
      print.title = "Print the report, or choose Save as PDF in the print dialog";
      print.addEventListener("click", function () { window.print(); });
      downloads.insertBefore(print, downloads.firstChild);
    }

    notices.innerHTML = "";
    var rows = data.not_produced || [];
    notices.hidden = !rows.length;
    rows.forEach(function (row) {
      var block = document.createElement("div");
      block.className = "report-notice";
      var head = document.createElement("span");
      head.className = "report-notice-kind mono";
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
  var writing = false;
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
        writing = true;
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
    if (writing && !running) {
      writing = false;
      say("Written again.");
      load();
    }
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
