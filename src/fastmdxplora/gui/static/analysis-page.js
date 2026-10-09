/* The Analysis page read as a whole: what the study determined, where each
 * analysis is, and how each series converged.
 *
 * Three things beside the figures dashboard.js renders:
 *
 * - **Results**, one table of every analysis and what it recorded: each
 *   mean to the place its error allows, the frames and independent samples
 *   it rests on, and whether it was determined or why not. The numbers are
 *   the analyses' own records (/api/analysis-overview), the ones the report
 *   gives; nothing is computed here.
 * - **An index** of the sections by what they study, with a filter, so a
 *   page of thirty analyses is found by name rather than by scrolling.
 * - **Convergence**, under any time series: the running mean of the
 *   equilibrated frames with its error, the block averages, the correlation
 *   and the distribution, from /api/convergence, computed by the estimator
 *   the recorded error comes from.
 */
(function () {
  "use strict";

  var NS = "http://www.w3.org/2000/svg";
  var overviewKey = "";
  var overview = null;
  var convergence = {};
  var sectionsShown = [];

  function byId(id) { return document.getElementById(id); }

  function escapeHTML(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function token(name, fallback) {
    var value = "";
    try {
      value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    } catch (e) { /* no styles yet */ }
    return value || fallback;
  }

  function colours() {
    return {
      line: token("--accent-cyan", "#2698ba"),
      mean: token("--accent-orange", "#ffb86b"),
      second: token("--accent-violet", "#b49cff"),
      grid: token("--border-subtle", "rgba(255,255,255,0.07)"),
      axis: token("--text-muted", "#85858f"),
      text: token("--text-secondary", "#b5b5bb"),
    };
  }

  /* Tick labels: exponents kept short so they clear the axis title. */
  function tick(value, values) {
    var magnitude = Math.max.apply(null, values.map(Math.abs));
    if (value === 0) return "0";
    if (magnitude >= 1e5 || magnitude < 1e-3) return value.toExponential(1);
    // To the places the spacing needs: 1.5, 2.0, not 1.500, 2.000.
    var step = values.length > 1 ? Math.abs(values[1] - values[0]) : Math.abs(value);
    var places = step > 0 ? Math.max(0, Math.min(6, -Math.floor(Math.log10(step) + 1e-9))) : 3;
    return value.toFixed(places);
  }

  function format(value) {
    if (value == null || !isFinite(value)) return "−";
    var magnitude = Math.abs(value);
    if (magnitude === 0) return "0";
    if (magnitude >= 1e5 || magnitude < 1e-3) return value.toExponential(2);
    if (magnitude >= 100) return value.toFixed(1);
    if (magnitude >= 1) return value.toFixed(3);
    return value.toPrecision(3);
  }

  function count(value) {
    return value == null ? "−" : Number(value).toLocaleString("en-US");
  }

  /* ------------------------------------------------------------------ */
  /* The index                                                          */
  /* ------------------------------------------------------------------ */

  /* Called by dashboard.js once the sections are on the page. */
  function sectionsRendered(host, sections) {
    sectionsShown = Array.isArray(sections) ? sections : [];
    var index = byId("analysis-index");
    if (!index) return;
    if (!sectionsShown.length) {
      index.hidden = true;
      index.innerHTML = "";
      return;
    }
    var themes = [];
    var byTheme = {};
    sectionsShown.forEach(function (section) {
      var theme = section.theme || "Other";
      if (!byTheme[theme]) { byTheme[theme] = []; themes.push(theme); }
      byTheme[theme].push(section);
    });
    var figures = sectionsShown.reduce(function (sum, s) {
      return sum + (Array.isArray(s.panels) ? s.panels.length : 0);
    }, 0);
    var previous = (byId("analysis-filter") || {}).value || "";
    // What to type, from this study's own analyses and themes: "the
    // ligand" was suggested for studies with none, and found nothing.
    var hint = sectionsShown.slice(0, 2).map(function (s) { return s.title; })
      .concat(themes.length > 1 ? [themes[themes.length - 1].toLowerCase()] : [])
      .join(", ");
    index.innerHTML =
      '<div class="analysis-index-head">' +
        '<label class="analysis-filter-label" for="analysis-filter">Find an analysis</label>' +
        '<input type="search" id="analysis-filter" class="analysis-filter" autocomplete="off" ' +
          'placeholder="' + escapeHTML(hint ? hint + "\u2026" : "") + '" value="' + escapeHTML(previous) + '">' +
        '<span class="analysis-index-count muted small" id="analysis-index-count">' +
          sectionsShown.length + " analys" + (sectionsShown.length === 1 ? "is" : "es") + ", " +
          figures + " figure" + (figures === 1 ? "" : "s") + "</span>" +
      "</div>" +
      '<div class="analysis-index-themes">' +
      themes.map(function (theme) {
        return '<div class="analysis-index-theme">' +
          '<span class="analysis-index-theme-name">' + escapeHTML(theme) + "</span>" +
          byTheme[theme].map(function (section) {
            return '<a class="analysis-index-link" href="#analysis-section-' +
              escapeHTML(section.anchor) + '" data-anchor="' + escapeHTML(section.anchor) + '">' +
              escapeHTML(section.title) + "</a>";
          }).join("") + "</div>";
      }).join("") + "</div>";
    index.hidden = false;
    var filter = byId("analysis-filter");
    filter.addEventListener("input", function () { applyFilter(filter.value); });
    if (previous) applyFilter(previous);
    decorateCards(host);
  }

  /* An index link scrolls to its section and moves focus there, rather than
   * changing the address, which the page uses for its own navigation. */
  document.addEventListener("click", function (event) {
    var link = event.target.closest && event.target.closest(".analysis-index-link, [data-goto-analysis]");
    if (!link) return;
    event.preventDefault();
    var anchor = link.getAttribute("data-anchor");
    var target = anchor ? byId("analysis-section-" + anchor) : null;
    if (!target && link.getAttribute("data-goto-analysis") && window.FastMDXDashboard) {
      window.FastMDXDashboard.showAnalysis(link.getAttribute("data-goto-analysis"));
      return;
    }
    if (!target) return;
    target.scrollIntoView({ behavior: "smooth", block: "start" });
    var heading = target.querySelector(".analysis-section-title");
    if (heading) {
      heading.setAttribute("tabindex", "-1");
      heading.focus({ preventScroll: true });
    }
  });

  /* Sections whose title, theme or figures' titles hold every word typed;
   * a theme with none left is hidden with them. */
  function applyFilter(text) {
    var words = String(text || "").toLowerCase().split(/\s+/).filter(Boolean);
    var shown = 0;
    document.querySelectorAll('.page[data-page="analysis"] .analysis-section').forEach(function (section) {
      var haystack = (section.getAttribute("data-search") || section.textContent || "").toLowerCase();
      var match = words.every(function (word) { return haystack.indexOf(word) !== -1; });
      section.hidden = !match;
      if (match) shown += 1;
    });
    document.querySelectorAll('.page[data-page="analysis"] .analysis-theme').forEach(function (theme) {
      theme.hidden = !theme.querySelector(".analysis-section:not([hidden])");
    });
    document.querySelectorAll(".analysis-index-link").forEach(function (link) {
      var section = byId("analysis-section-" + link.getAttribute("data-anchor"));
      link.classList.toggle("is-filtered-out", !!(section && section.hidden));
    });
    var counted = byId("analysis-index-count");
    if (counted) {
      counted.textContent = words.length
        ? shown + " of " + sectionsShown.length + " analyses match"
        : sectionsShown.length + " analys" + (sectionsShown.length === 1 ? "is" : "es");
    }
    var none = byId("analysis-filter-none");
    if (none) none.hidden = !(words.length && shown === 0);
  }

  /* ------------------------------------------------------------------ */
  /* The results                                                         */
  /* ------------------------------------------------------------------ */

  function statusOf(quantity) {
    if (quantity.determined) return { key: "determined", said: "Determined" };
    return { key: "undetermined", said: "Not determined" };
  }

  function nameLink(row) {
    return '<a href="#" data-goto-analysis="' + escapeHTML(row.analysis) + '" data-anchor="' +
      escapeHTML(row.anchor) + '">' + escapeHTML(row.title) + "</a>";
  }

  /* A status chip; a mean not determined opens to say why. */
  function statusCell(key, said, why) {
    var chip = '<span class="analysis-results-status" data-status="' + key + '">' + escapeHTML(said) + "</span>";
    if (!why) return "<td>" + chip + "</td>";
    return '<td><details class="analysis-results-why"><summary>' + chip + "</summary>" +
      "<p>" + escapeHTML(why) + "</p></details></td>";
  }

  /* A simulated time as every page says one: picoseconds under one
   * nanosecond (43 ps, not 0.0430 ns), to a tenth of one
   * (`simulated_time.say_length`, the Overview's tiles). */
  function sayLength(ns) {
    var ps = Math.abs(ns) * 1000;
    // Under a tenth of a picosecond, to two figures: a correlation time of
    // 0.013 ps read "0 ps".
    if (ps > 0 && ps < 0.1) return Number((ns * 1000).toPrecision(2)) + " ps";
    if (Math.abs(ns) < 1) return (Math.round(ns * 10000) / 10) + " ps";
    return (Math.round(ns * 10000) / 10000) + " ns";
  }

  function quantityRow(row, quantity, label, indented) {
    var status = statusOf(quantity);
    var from = quantity.from_ns != null && quantity.from_frame
      ? "from " + sayLength(quantity.from_ns)
      : (quantity.from_frame ? "from frame " + count(quantity.from_frame) : "");
    var frames = quantity.of_frames != null
      ? count(quantity.of_frames - (quantity.from_frame || 0)) + " of " + count(quantity.of_frames)
      : (quantity.reweighted ? "reweighted" : "−");
    return '<tr data-analysis="' + escapeHTML(row.analysis) + '" data-status="' + status.key + '"' +
      (indented ? ' class="is-quantity"' : "") + ">" +
      '<th scope="row" class="analysis-results-name">' + label + "</th>" +
      '<td class="analysis-results-value mono">' + escapeHTML(quantity.said) +
        (quantity.reweighted ? ' <span class="analysis-results-note">reweighted</span>' : "") + "</td>" +
      '<td class="analysis-results-frames mono" data-label="Frames averaged">' + escapeHTML(frames) +
        (from ? '<span class="analysis-results-from">' + escapeHTML(from) + "</span>" : "") + "</td>" +
      '<td class="analysis-results-samples mono" data-label="Independent samples">' +
        escapeHTML(quantity.samples === 0 ? "< 1" : count(quantity.samples)) + "</td>" +
      statusCell(status.key, status.said, quantity.why) + "</tr>";
  }

  function rowsOf(row) {
    var quantities = Array.isArray(row.quantities) ? row.quantities : [];
    if (!quantities.length) {
      var key = row.status === "failed" ? "failed" : row.status === "skipped" ? "skipped"
        : row.status === "running" ? "running" : "result";
      var said = { failed: "Failed", skipped: "Skipped", running: "Running", result: "In its section" }[key];
      var what = key === "result" ? "A profile, map or table"
        : key === "running" ? "Being analysed" : (row.message || "");
      // A failure's first sentence in the row; the chip opens to the rest.
      var cut = what.search(/[.:]\s/);
      var plain = key === "result" || key === "running";
      var head = plain || cut < 0 ? what : what.slice(0, cut + 1);
      var rest = plain || cut < 0 ? "" : what.slice(cut + 1).trim();
      return '<tr data-analysis="' + escapeHTML(row.analysis) + '" data-status="' + key + '">' +
        '<th scope="row" class="analysis-results-name">' + nameLink(row) + "</th>" +
        '<td colspan="3" class="analysis-results-other">' + escapeHTML(head) + "</td>" +
        statusCell(key, said, rest) + "</tr>";
    }
    if (quantities.length === 1 && quantities[0].key === "mean") {
      return quantityRow(row, quantities[0], nameLink(row), false);
    }
    // Several quantities: the analysis heads them, each on its own row,
    // whose header names the analysis too for whoever reads it alone.
    return '<tr class="analysis-results-group" data-analysis="' + escapeHTML(row.analysis) + '">' +
      '<th scope="row" colspan="5" class="analysis-results-name">' + nameLink(row) + "</th></tr>" +
      quantities.map(function (q) {
        return quantityRow(row, q, '<span class="sr-only">' + escapeHTML(row.title) + ": </span>" +
          escapeHTML(q.key === "mean" ? "Mean" : q.label), true);
      }).join("");
  }

  function renderOverview(data) {
    var host = byId("analysis-results");
    var table = byId("analysis-results-table");
    decorateCards(document.querySelector('.page[data-page="analysis"]'));
    if (!host || !table) return;
    var rows = data && data.ok && Array.isArray(data.rows) ? data.rows : [];
    if (!rows.length) { host.hidden = true; return; }
    var determined = 0;
    var undetermined = 0;
    // One body per theme, headed by it.
    var theme = null;
    var html = "";
    rows.forEach(function (row) {
      if (row.theme !== theme) {
        html += (theme === null ? "" : "</tbody>") + "<tbody>" +
          '<tr class="analysis-results-theme-row"><th colspan="5" scope="rowgroup">' +
          escapeHTML(row.theme) + "</th></tr>";
        theme = row.theme;
      }
      (row.quantities || []).forEach(function (q) { if (q.determined) determined += 1; else undetermined += 1; });
      html += rowsOf(row);
    });
    if (theme !== null) html += "</tbody>";
    Array.prototype.slice.call(table.tBodies).forEach(function (old) { old.remove(); });
    table.insertAdjacentHTML("beforeend", html);
    var failed = rows.filter(function (row) { return row.status === "failed"; }).length;
    var said = [];
    said.push(determined + " determined");
    if (undetermined) said.push(undetermined + " not determined");
    if (failed) said.push(failed + " failed");
    var summary = byId("analysis-results-summary");
    if (summary) {
      summary.textContent = said.join(", ") + (data.biased
        ? ". The run was biased: means are the reweighted equilibrium values where they were recovered."
        : ". A mean is determined where the frames hold enough independent samples for its error.");
    }
    host.hidden = false;
    linkData(rows);
  }

  /* A card's data files, beside its figure, so the numbers behind it are
   * one click away rather than three pages down the Files tab. */
  function linkData(rows) {
    var byName = {};
    (rows || []).forEach(function (row) { byName[row.analysis] = row; });
    document.querySelectorAll('.page[data-page="analysis"] .analysis-card[data-analysis]').forEach(function (card) {
      var row = byName[card.getAttribute("data-analysis")];
      var footer = card.querySelector(".ac-footer");
      if (!row || !footer || footer.querySelector("[data-data-file]")) return;
      (row.data || []).slice(0, 1).forEach(function (file) {
        var link = document.createElement("a");
        link.className = "file-action";
        link.href = file.href;
        link.setAttribute("download", file.name);
        link.setAttribute("data-data-file", file.name);
        link.title = "The numbers this figure is plotted from";
        link.textContent = "Data (" + file.name.split(".").pop() + ")";
        var chip = footer.querySelector(".figure-chip");
        footer.insertBefore(link, chip || null);
      });
    });
  }

  function tableAsRows() {
    var rows = [["Analysis", "Quantity", "Mean", "Standard error", "Unit", "Frames analysed",
      "From (ns)", "Independent samples", "Determined", "Why not"]];
    ((overview && overview.rows) || []).forEach(function (row) {
      (row.quantities || []).forEach(function (q) {
        rows.push([row.title, q.key === "mean" ? "Mean" : q.label,
          q.value == null ? "" : String(q.value), q.error == null ? "" : String(q.error),
          q.unit || "", q.of_frames == null ? "" : String(q.of_frames - (q.from_frame || 0)),
          q.from_ns == null ? "" : String(q.from_ns), q.samples == null ? "" : String(q.samples),
          q.determined ? "yes" : "no", q.why || ""]);
      });
    });
    return rows;
  }

  function asCsv(rows) {
    return rows.map(function (row) {
      return row.map(function (cell) {
        var text = String(cell);
        return /[",\n]/.test(text) ? '"' + text.replace(/"/g, '""') + '"' : text;
      }).join(",");
    }).join("\n") + "\n";
  }

  document.addEventListener("click", function (event) {
    var button = event.target.closest && event.target.closest("[data-results-download]");
    if (!button) return;
    var blob = new Blob([asCsv(tableAsRows())], { type: "text/csv" });
    var link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = "analysis_results.csv";
    document.body.appendChild(link);
    link.click();
    setTimeout(function () { URL.revokeObjectURL(link.href); link.remove(); }, 0);
  });

  function refreshOverview(payload) {
    var analyses = (payload && payload.analyses) || [];
    var key = JSON.stringify([payload && payload.output_dir, analyses.map(function (a) {
      return [a.name, a.status, a.finished_at];
    }), payload && payload.figure_provenance ? Object.keys(payload.figure_provenance).length : 0]);
    // Asked again on every update until the analysis phase has recorded
    // every analysis: until then they arrive one by one, and nothing in
    // the results says which have.
    if (key === overviewKey && overview && overview.complete) { linkData(overview.rows); return; }
    overviewKey = key;
    convergence = {};
    fetch("/api/analysis-overview", { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (key !== overviewKey) return;
        overview = data;
        renderOverview(data);
      })
      .catch(function () { overviewKey = ""; });
  }

  /* ------------------------------------------------------------------ */
  /* Convergence                                                         */
  /* ------------------------------------------------------------------ */

  /* Whether an analysis recorded a mean over its frames, so that it has a
   * convergence to show: RMSF's series is one value a residue, and its
   * Convergence said only that it had none. Unknown until the overview is
   * read, and offered meanwhile. */
  function hasAMean(name) {
    var rows = (overview && overview.rows) || [];
    var row = rows.filter(function (r) { return r.analysis === name; })[0];
    return !row || row.kind === "mean";
  }

  function decorateCards(host) {
    // Withdrawn where the overview, read since, says there is no mean.
    (host || document).querySelectorAll(".analysis-card [data-convergence]").forEach(function (button) {
      if (hasAMean(button.getAttribute("data-convergence"))) return;
      var panel = button.closest(".ac-footer") && button.closest(".ac-footer").nextSibling;
      if (panel && panel.classList && panel.classList.contains("convergence-panel")) panel.remove();
      button.remove();
    });
    (host || document).querySelectorAll(".analysis-card [data-series]").forEach(function (frame) {
      var card = frame.closest(".analysis-card");
      var footer = card && card.querySelector(".ac-footer");
      if (!footer || footer.querySelector("[data-convergence]")) return;
      if (!hasAMean(frame.getAttribute("data-series"))) return;
      var button = document.createElement("button");
      button.type = "button";
      button.className = "file-action";
      button.setAttribute("data-convergence", frame.getAttribute("data-series"));
      button.setAttribute("aria-expanded", "false");
      button.title = "The running mean, block averages, correlation and distribution behind the error";
      button.textContent = "Convergence";
      var chip = footer.querySelector(".figure-chip");
      footer.insertBefore(button, chip || null);
      var panel = document.createElement("div");
      panel.className = "convergence-panel";
      panel.hidden = true;
      footer.parentNode.insertBefore(panel, footer.nextSibling);
    });
  }

  document.addEventListener("click", function (event) {
    var button = event.target.closest && event.target.closest("[data-convergence]");
    if (!button) return;
    var card = button.closest(".analysis-card");
    var panel = card && card.querySelector(".convergence-panel");
    if (!panel) return;
    var open = panel.hidden;
    panel.hidden = !open;
    button.setAttribute("aria-expanded", String(open));
    button.classList.toggle("is-chosen", open);
    if (open) showConvergence(panel, button.getAttribute("data-convergence"));
  });

  function load(name) {
    if (!convergence[name]) {
      convergence[name] = fetch("/api/convergence?analysis=" + encodeURIComponent(name), { cache: "no-store" })
        .then(function (r) { return r.json(); })
        .catch(function () { return { ok: false, reason: "The convergence could not be read." }; });
    }
    return convergence[name];
  }

  function showConvergence(panel, name) {
    panel.innerHTML = '<p class="muted small">Reading the series…</p>';
    load(name).then(function (data) {
      panel.setAttribute("data-analysis", name);
      if (!data || !data.ok) {
        panel.innerHTML = '<p class="muted small">' + escapeHTML((data && data.reason) || "No convergence to show.") + "</p>";
        return;
      }
      drawConvergence(panel, data);
    });
  }

  function firstSentence(text) {
    var reason = String(text || "");
    var cut = reason.search(/[.:]\s/);
    return cut > 0 ? reason.slice(0, cut) : reason.replace(/\.$/, "");
  }

  /* What the plots show, in words. The mean and its error are the record's,
   * the ones the report and the table give: an analysis can withhold its
   * error for a reason the series cannot show (a chain reaching its own
   * periodic image), and the view does not give one the record does not. */
  function said(data) {
    var eq = data.equilibration || {};
    var recorded = data.recorded || {};
    var unit = data.unit ? " " + data.unit : "";
    var timed = data.time_unit === "ns";
    var parts = [];
    if (eq.discard_frames) {
      parts.push("Equilibrated from " + (timed && eq.start_time != null
        ? sayLength(eq.start_time) : "frame " + count(eq.discard_frames)) +
        ": the first " + count(eq.discard_frames) + " of " + count(data.n_values) +
        " frames are left out.");
    } else {
      parts.push("No equilibration period detected: all " + count(data.n_values) + " frames are averaged.");
    }
    if (eq.statistical_inefficiency != null) {
      var auto = data.autocorrelation || {};
      parts.push("Statistical inefficiency " + Number(eq.statistical_inefficiency).toPrecision(3) + " frames" +
        (auto.tau_int_time != null && timed ? " (integrated correlation time " +
          sayLength(auto.tau_int_time) + ")" : "") +
        ((eq.effective_samples || 0) < 0.5 ? ", so under 1 independent sample."
          : ", so about " + count(Math.round(eq.effective_samples)) + " independent sample" +
            (Math.round(eq.effective_samples) === 1 ? "." : "s.")));
    }
    // As the table above says it (`recorded.said`, the server's): an error
    // only where the record stands behind it.
    if (recorded.mean != null && recorded.standard_error != null) {
      parts.push("Recorded mean " + (recorded.said ||
        format(recorded.mean) + " \u00b1 " + format(recorded.standard_error) + unit) + ".");
    } else if (recorded.mean != null) {
      var why = recorded.not_a_measurement || eq.withheld;
      parts.push("Recorded mean " + (recorded.said || format(recorded.mean) + unit) +
        ", not determined" + (why ? ": " + firstSentence(why) : "") + ".");
    }
    if (recorded.start_shared_with_replicas) {
      parts.push("The recorded mean starts where the study's replicas equilibrate together, at frame " +
        count(recorded.discard_frames) + ", not where this run alone does.");
    } else if (recorded.same_start === false) {
      parts.push("The recorded mean starts at frame " + count(recorded.discard_frames) + ".");
    }
    // The line to read them against only where the record gives an error:
    // under a mean not determined it pointed at a line not drawn.
    parts.push("Block averages longer than the correlation time reach the error of the mean" +
      (recorded.standard_error != null
        ? "; the recorded error is the line to read them against." : "."));
    return parts.join(" ");
  }

  /* A time axis as every page says a time: in picoseconds while all of it
   * is under a nanosecond (`sayLength`), the Overview's charts' way. */
  function timeAxis(values, word) {
    var most = 0;
    (values || []).forEach(function (v) { if (v != null && Math.abs(v) > most) most = Math.abs(v); });
    var scale = most < 1 ? 1000 : 1;
    return {
      x: (values || []).map(function (v) { return v == null ? v : v * scale; }),
      label: word + (scale === 1000 ? " (ps)" : " (ns)"),
      scale: scale,
    };
  }

  function drawConvergence(panel, data) {
    panel.innerHTML =
      '<p class="convergence-said">' + escapeHTML(said(data)) + "</p>" +
      '<div class="convergence-grid">' +
        '<figure class="convergence-plot" data-plot="running"><figcaption>Running mean of the equilibrated frames</figcaption><div class="convergence-canvas"></div></figure>' +
        '<figure class="convergence-plot" data-plot="blocking"><figcaption>Standard error by block length</figcaption><div class="convergence-canvas"></div></figure>' +
        '<figure class="convergence-plot" data-plot="correlation"><figcaption>Autocorrelation</figcaption><div class="convergence-canvas"></div></figure>' +
        '<figure class="convergence-plot" data-plot="histogram"><figcaption>Distribution</figcaption><div class="convergence-canvas"></div></figure>' +
      "</div>";
    var c = colours();
    var unit = data.unit ? " (" + data.unit + ")" : "";
    var timed = data.time_unit === "ns";
    var running = data.running_mean || {};
    var runningTime = timed && running.time && running.time.every(function (v) { return v != null; })
      ? timeAxis(running.time, "Time") : null;
    var rx = runningTime ? runningTime.x : running.frames;
    var recorded = data.recorded || {};
    chart(panel.querySelector('[data-plot="running"] .convergence-canvas'), {
      x: rx || [], xLabel: runningTime ? runningTime.label : "Frames after equilibration",
      yLabel: (data.label || "") + unit,
      // No band where the record gives no error: the view does not give
      // one the record withholds.
      lines: [{ y: running.mean || [], colour: c.line,
                band: recorded.standard_error != null ? (running.standard_error || []) : [] }],
      level: recorded.mean != null ? { y: recorded.mean, colour: c.mean, label: "recorded mean" } : null,
      describe: "Running mean of " + (data.label || "the series"),
    });
    var block = data.blocking || {};
    chart(panel.querySelector('[data-plot="blocking"] .convergence-canvas'), {
      x: block.block_length || [], logX: true, xLabel: "Block length (frames)",
      yLabel: "Standard error" + unit, points: true,
      lines: [{ y: block.standard_error || [], colour: c.line, bars: block.standard_error_uncertainty || [] }],
      level: recorded.standard_error != null
        ? { y: recorded.standard_error, colour: c.mean, label: "recorded error" } : null,
      zero: true,
      describe: "Standard error of the mean by block length",
    });
    var auto = data.autocorrelation || {};
    var lagTime = timed && auto.lag_time ? timeAxis(auto.lag_time, "Lag") : null;
    var ax = lagTime ? lagTime.x : auto.lag_frames;
    chart(panel.querySelector('[data-plot="correlation"] .convergence-canvas'), {
      x: ax || [], xLabel: lagTime ? lagTime.label : "Lag (frames)", yLabel: "Correlation",
      lines: [{ y: auto.correlation || [], colour: c.line }],
      level: { y: 0, colour: c.axis },
      mark: auto.tau_int_time != null && lagTime
        ? { x: auto.tau_int_time * lagTime.scale, label: "τ " + sayLength(auto.tau_int_time), colour: c.mean }
        : (auto.tau_int_frames != null ? { x: auto.tau_int_frames, label: "τ " + format(auto.tau_int_frames), colour: c.mean } : null),
      describe: "Normalised autocorrelation of the equilibrated frames",
    });
    var hist = data.histogram || {};
    histogram(panel.querySelector('[data-plot="histogram"] .convergence-canvas'), hist, (data.label || "") + unit, c);
  }

  function svgIn(host, width, height, label) {
    host.innerHTML = "";
    var svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", "0 0 " + width + " " + height);
    svg.setAttribute("width", width);
    svg.setAttribute("height", height);
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", label);
    svg.setAttribute("class", "convergence-svg");
    host.appendChild(svg);
    return svg;
  }

  function el(name, attrs, parent) {
    var node = document.createElementNS(NS, name);
    Object.keys(attrs || {}).forEach(function (key) { node.setAttribute(key, attrs[key]); });
    if (parent) parent.appendChild(node);
    return node;
  }

  function niceTicks(low, high, n) {
    var make = window.FastMDXSeries && window.FastMDXSeries.ticks;
    return make ? make(low, high, n) : [low, high];
  }

  function frame(host, spec) {
    var width = Math.max(240, Math.round(host.clientWidth || 320));
    var height = 190;
    var m = { top: 12, right: 14, bottom: 34, left: 62 };
    return { width: width, height: height, left: m.left, right: width - m.right,
             top: m.top, bottom: height - m.bottom, svg: svgIn(host, width, height, spec.describe || "") };
  }

  function axes(f, xs, yLow, yHigh, spec, c) {
    var xLow, xHigh;
    var tx = spec.logX ? function (v) { return Math.log2(v); } : function (v) { return v; };
    var finiteX = xs.filter(function (v) { return v != null && isFinite(v) && (!spec.logX || v > 0); });
    xLow = Math.min.apply(null, finiteX.map(tx));
    xHigh = Math.max.apply(null, finiteX.map(tx));
    if (!(xHigh > xLow)) xHigh = xLow + 1;
    if (!(yHigh > yLow)) { yHigh = yLow + (Math.abs(yLow) || 1) * 0.1; yLow -= (Math.abs(yLow) || 1) * 0.1; }
    var pad = (yHigh - yLow) * 0.08;
    yLow -= pad; yHigh += pad;
    function px(v) { return f.left + (tx(v) - xLow) / (xHigh - xLow) * (f.right - f.left); }
    function py(v) { return f.bottom - (v - yLow) / (yHigh - yLow) * (f.bottom - f.top); }
    var yt = niceTicks(yLow, yHigh, 4);
    yt.forEach(function (v) {
      el("line", { x1: f.left, x2: f.right, y1: py(v), y2: py(v), stroke: c.grid }, f.svg);
      var t = el("text", { x: f.left - 6, y: py(v) + 3, fill: c.axis, "font-size": 10, "text-anchor": "end" }, f.svg);
      t.textContent = tick(v, yt);
    });
    var xt = spec.logX
      ? finiteX.filter(function (v, i) { return i % Math.max(1, Math.ceil(finiteX.length / 6)) === 0; })
      : niceTicks(xLow, xHigh, 4);
    xt.forEach(function (v) {
      if (tx(v) < xLow - 1e-9 || tx(v) > xHigh + 1e-9) return;
      var t = el("text", { x: px(v), y: f.bottom + 14, fill: c.axis, "font-size": 10, "text-anchor": "middle" }, f.svg);
      t.textContent = spec.logX ? String(v) : tick(v, xt);
    });
    el("line", { x1: f.left, x2: f.right, y1: f.bottom, y2: f.bottom, stroke: c.axis }, f.svg);
    var xl = el("text", { x: (f.left + f.right) / 2, y: f.height - 4, fill: c.text, "font-size": 10.5, "text-anchor": "middle" }, f.svg);
    xl.textContent = spec.xLabel || "";
    var mid = (f.top + f.bottom) / 2;
    var yl = el("text", { x: 11, y: mid, fill: c.text, "font-size": 10.5, "text-anchor": "middle",
                          transform: "rotate(-90 11 " + mid + ")" }, f.svg);
    yl.textContent = spec.yLabel || "";
    return { px: px, py: py };
  }

  function chart(host, spec) {
    if (!host) return;
    var c = colours();
    var xs = spec.x || [];
    if (xs.length < 2) {
      host.innerHTML = '<p class="muted small">Too few points to plot.</p>';
      return;
    }
    var values = [];
    spec.lines.forEach(function (line) {
      line.y.forEach(function (v, i) {
        if (v == null || !isFinite(v)) return;
        var spread = (line.band && line.band[i]) || (line.bars && line.bars[i]) || 0;
        values.push(v - spread, v + spread);
      });
    });
    if (spec.level && spec.level.y != null) values.push(spec.level.y);
    if (spec.zero) values.push(0);
    if (!values.length) { host.innerHTML = '<p class="muted small">Nothing to plot.</p>'; return; }
    var f = frame(host, spec);
    var at = axes(f, xs, Math.min.apply(null, values), Math.max.apply(null, values), spec, c);
    spec.lines.forEach(function (line) {
      if (line.band && line.band.length) {
        var upper = [];
        var lower = [];
        xs.forEach(function (x, i) {
          var v = line.y[i];
          var e = line.band[i];
          if (v == null || e == null || !isFinite(v) || !isFinite(e)) return;
          upper.push(at.px(x).toFixed(1) + " " + at.py(v + e).toFixed(1));
          lower.unshift(at.px(x).toFixed(1) + " " + at.py(v - e).toFixed(1));
        });
        if (upper.length > 1) {
          el("path", { d: "M" + upper.join("L") + "L" + lower.join("L") + "Z", fill: line.colour,
                       "fill-opacity": 0.16, stroke: "none", class: "convergence-band" }, f.svg);
        }
      }
      var d = "";
      var pen = false;
      xs.forEach(function (x, i) {
        var v = line.y[i];
        if (v == null || !isFinite(v) || x == null || (spec.logX && !(x > 0))) { pen = false; return; }
        d += (pen ? "L" : "M") + at.px(x).toFixed(1) + " " + at.py(v).toFixed(1);
        pen = true;
      });
      el("path", { d: d, fill: "none", stroke: line.colour, "stroke-width": 1.5,
                   "stroke-linejoin": "round", class: "convergence-line" }, f.svg);
      if (spec.points) {
        xs.forEach(function (x, i) {
          var v = line.y[i];
          if (v == null || !isFinite(v)) return;
          var e = line.bars && line.bars[i];
          if (e != null && isFinite(e)) {
            el("line", { x1: at.px(x), x2: at.px(x), y1: at.py(v - e), y2: at.py(v + e),
                         stroke: line.colour, "stroke-width": 1 }, f.svg);
          }
          el("circle", { cx: at.px(x), cy: at.py(v), r: 2.6, fill: line.colour }, f.svg);
        });
      }
    });
    if (spec.level && spec.level.y != null) {
      el("line", { x1: f.left, x2: f.right, y1: at.py(spec.level.y), y2: at.py(spec.level.y),
                   stroke: spec.level.colour, "stroke-dasharray": "5 4", "stroke-width": 1.2,
                   class: "convergence-level" }, f.svg);
      if (spec.level.label) {
        var t = el("text", { x: f.right - 2, y: at.py(spec.level.y) - 4, fill: spec.level.colour,
                             "font-size": 10, "text-anchor": "end" }, f.svg);
        t.textContent = spec.level.label;
      }
    }
    if (spec.mark && spec.mark.x != null && isFinite(spec.mark.x)) {
      var mx = at.px(spec.mark.x);
      if (mx >= f.left && mx <= f.right) {
        el("line", { x1: mx, x2: mx, y1: f.top, y2: f.bottom, stroke: spec.mark.colour,
                     "stroke-dasharray": "3 3" }, f.svg);
        var mt = el("text", { x: mx + 4, y: f.top + 10, fill: spec.mark.colour, "font-size": 10 }, f.svg);
        mt.textContent = spec.mark.label;
      }
    }
  }

  function histogram(host, hist, label, c) {
    if (!host) return;
    var kept = hist.equilibrated;
    var left = hist.discarded;
    if (!kept || !Array.isArray(kept.counts) || !kept.counts.length) {
      host.innerHTML = '<p class="muted small">Nothing to plot.</p>';
      return;
    }
    function density(part) {
      var total = part.counts.reduce(function (a, b) { return a + b; }, 0) || 1;
      return part.counts.map(function (n, i) {
        return n / total / ((part.edges[i + 1] - part.edges[i]) || 1);
      });
    }
    var parts = [{ part: kept, d: density(kept), colour: c.line, fill: 0.35, name: "equilibrated" }];
    if (left && Array.isArray(left.counts) && left.counts.length) {
      parts.push({ part: left, d: density(left), colour: c.axis, fill: 0, name: "left out" });
    }
    var xs = [];
    var ys = [0];
    parts.forEach(function (p) { xs = xs.concat(p.part.edges); ys = ys.concat(p.d); });
    var spec = { describe: "Distribution of the equilibrated frames" + (parts.length > 1 ? " and of the frames left out" : ""),
                 xLabel: label, yLabel: "Density" };
    var f = frame(host, spec);
    var at = axes(f, xs, 0, Math.max.apply(null, ys), spec, c);
    parts.forEach(function (p) {
      var d = "";
      p.d.forEach(function (v, i) {
        var x0 = at.px(p.part.edges[i]);
        var x1 = at.px(p.part.edges[i + 1]);
        d += (i ? "L" : "M" + x0.toFixed(1) + " " + at.py(0).toFixed(1) + "L") +
          x0.toFixed(1) + " " + at.py(v).toFixed(1) + "L" + x1.toFixed(1) + " " + at.py(v).toFixed(1);
      });
      d += "L" + at.px(p.part.edges[p.part.edges.length - 1]).toFixed(1) + " " + at.py(0).toFixed(1);
      el("path", { d: d, fill: p.fill ? p.colour : "none", "fill-opacity": p.fill, stroke: p.colour,
                   "stroke-width": 1.2, "stroke-dasharray": p.fill ? "" : "4 3",
                   class: "convergence-histogram" }, f.svg);
    });
    // The key under the plot, where it covers nothing.
    var key = document.createElement("div");
    key.className = "convergence-key";
    key.innerHTML = parts.map(function (p) {
      return '<span><i style="border-color:' + p.colour + ';background:' +
        (p.fill ? p.colour : "transparent") + '"></i>' + escapeHTML(p.name) + "</span>";
    }).join("");
    host.appendChild(key);
  }

  function redraw() {
    document.querySelectorAll(".convergence-panel:not([hidden])[data-analysis]").forEach(function (panel) {
      var name = panel.getAttribute("data-analysis");
      if (convergence[name]) convergence[name].then(function (data) { if (data && data.ok) drawConvergence(panel, data); });
    });
  }

  document.addEventListener("fmx:theme", redraw);
  var resizing = null;
  window.addEventListener("resize", function () {
    clearTimeout(resizing);
    resizing = setTimeout(redraw, 200);
  });
  window.addEventListener("dashboard:results-updated", function (event) { refreshOverview(event.detail || {}); });
  window.addEventListener("dashboard:run-changed", function () {
    overviewKey = ""; overview = null; convergence = {};
  });

  window.FastMDXAnalysisPage = {
    sectionsRendered: sectionsRendered,
    applyFilter: applyFilter,
    tableAsRows: tableAsRows,
  };
}());
