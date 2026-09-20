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

    if (!data || !data.ok) {
      doc.hidden = true;
      empty.hidden = false;
      notices.hidden = true;
      downloads.innerHTML = "";
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
    Array.prototype.forEach.call(doc.querySelectorAll("img"), function (img) {
      var src = img.getAttribute("src") || "";
      if (src && !/^(https?:|\/|data:)/.test(src)) {
        img.src = "/artifacts/report/" + src.replace(/^\.\//, "");
      }
    });
    Array.prototype.forEach.call(doc.querySelectorAll("a[href]"), function (a) {
      var href = a.getAttribute("href") || "";
      if (href && !/^(https?:|#|\/|mailto:)/.test(href)) {
        a.href = "/artifacts/report/" + href.replace(/^\.\//, "");
        a.target = "_blank";
        a.rel = "noopener";
      }
    });
  }

  function load() {
    return fetch("/api/report").then(function (r) { return r.json(); })
      .then(render)
      .catch(function () { render(null); });
  }

  document.addEventListener("DOMContentLoaded", function () {
    if (!el("report-document")) return;
    load();
    if (window.FastMDXDashboard && window.FastMDXDashboard.on) {
      window.FastMDXDashboard.on("app-state", function () {
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
