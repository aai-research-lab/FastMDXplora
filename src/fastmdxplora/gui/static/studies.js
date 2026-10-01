/* Every study in the workspace, as cards, and two of them compared
 * (workspace.py).
 *
 * Each card says the study's structure, what kind of study it is, where it
 * stands, when it began, the means it recorded with their errors and a
 * figure it plotted. Search narrows the cards as typed. Two chosen are
 * compared: the settings in which they differ, defaults filled in, as
 * `fastmdx diff` says them, and the means both recorded, a difference
 * marked only past twice their combined error. Open makes a study the one
 * on screen. Everything is built as elements: a study's name and its
 * records are data. */
(function () {
  "use strict";

  var studies = [];
  var chosen = [];
  var lookedIn = "";
  var loading = null;
  var SHOWN_FIRST = 20;

  function el(id) { return document.getElementById(id); }

  function make(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  /* A mean with its error, to the error's second significant figure. */
  function withError(mean, error, unit) {
    if (mean === null || mean === undefined) return "not recorded";
    var said;
    if (error !== null && error !== undefined && error > 0) {
      var places = Math.max(0, Math.min(6, 1 - Math.floor(Math.log10(error))));
      said = mean.toFixed(places) + " ± " + error.toFixed(places);
    } else {
      said = Number(mean.toPrecision(4)).toString();
    }
    return said + (unit ? " " + unit : "");
  }

  function meanSaid(record) {
    if (!record) return "not recorded";
    if (record.withheld) return withError(record.mean, null, record.unit) + ", not determined";
    return withError(record.mean, record.error, record.unit);
  }

  function dateSaid(when) {
    var date = new Date(when);
    return isNaN(date.getTime()) ? "" : date.toLocaleDateString(undefined,
      { year: "numeric", month: "short", day: "numeric" });
  }

  function searchText(study) {
    return [study.name, study.system, study.kind, study.state, study.forcefield || ""]
      .join(" ").toLowerCase();
  }

  function card(study) {
    var item = make("article", "study-card");
    item.setAttribute("role", "listitem");
    item.dataset.path = study.path;
    item.dataset.state = study.state;
    var frame = make("div", "study-thumb");
    if (study.thumbnail) {
      var img = make("img");
      img.loading = "lazy";
      img.alt = "";
      img.src = "/api/study-thumbnail?" + new URLSearchParams({ path: study.path });
      frame.appendChild(img);
    } else {
      frame.appendChild(make("span", "study-thumb-none", study.system || study.name));
    }
    item.appendChild(frame);
    var body = make("div", "study-body");
    var head = make("div", "study-head");
    head.appendChild(make("span", "study-name", study.name));
    head.appendChild(make("span", "study-state", study.state));
    body.appendChild(head);
    body.appendChild(make("div", "study-what",
      [study.system, study.kind, study.forcefield].filter(Boolean).join(" · ")));
    var when = dateSaid(study.when);
    if (when) body.appendChild(make("div", "study-when muted", when));
    if ((study.means || []).length) {
      var list = make("dl", "study-means");
      study.means.forEach(function (m) {
        list.appendChild(make("dt", "", m.label || m.analysis));
        list.appendChild(make("dd", "mono", meanSaid(m)));
      });
      body.appendChild(list);
    }
    if (study.free_energy) {
      body.appendChild(make("div", "study-free-energy",
        study.free_energy.refused ? "Free energy refused: " + study.free_energy.refused
          : "Free energy recombined."));
    }
    var actions = make("div", "study-actions");
    var open = make("button", "file-action study-open", "Open");
    open.type = "button";
    open.addEventListener("click", function () { openStudy(study.path, open); });
    var pick = make("label", "study-pick");
    var box = make("input");
    box.type = "checkbox";
    box.checked = chosen.indexOf(study.path) >= 0;
    box.addEventListener("change", function () { choose(study.path, box.checked); });
    pick.append(box, document.createTextNode(" Compare"));
    actions.append(open, pick);
    body.appendChild(actions);
    item.appendChild(body);
    return item;
  }

  function draw() {
    var grid = el("studies-grid");
    var empty = el("studies-empty");
    if (!grid) return;
    var wanted = (el("studies-search").value || "").trim().toLowerCase().split(/\s+/)
      .filter(Boolean);
    var shown = studies.filter(function (study) {
      var text = searchText(study);
      return wanted.every(function (word) { return text.indexOf(word) >= 0; });
    });
    grid.replaceChildren.apply(grid, shown.map(card));
    empty.hidden = shown.length > 0;
    empty.textContent = studies.length
      ? "No study matches that."
      : "No studies here. Look in another folder, or start one from the Agent or the Config page.";
    sayChosen();
  }

  function choose(path, on) {
    chosen = chosen.filter(function (p) { return p !== path; });
    if (on) chosen.push(path);
    // Two at a time: a third chosen replaces the first.
    if (chosen.length > 2) chosen.shift();
    draw();
  }

  function sayChosen() {
    var bar = el("studies-compare-bar");
    if (!bar) return;
    bar.hidden = !chosen.length;
    el("studies-compare").disabled = chosen.length !== 2;
    el("studies-chosen-said").textContent = chosen.length === 2
      ? "Two chosen." : "Choose one more to compare.";
  }

  function compare() {
    var host = el("studies-compared");
    if (chosen.length !== 2 || !host) return;
    host.hidden = false;
    host.replaceChildren(make("p", "muted", "Comparing…"));
    fetch("/api/studies-compared?" + new URLSearchParams({ a: chosen[0], b: chosen[1] }))
      .then(function (r) { return r.json(); })
      .then(drawComparison)
      .catch(function () { host.replaceChildren(make("p", "muted", "The server did not answer.")); });
  }

  function drawComparison(data) {
    var host = el("studies-compared");
    host.replaceChildren();
    if (!data || !data.ok) {
      host.appendChild(make("p", "muted", (data && data.reason) || "They could not be compared."));
      return;
    }
    var head = make("div", "card-header");
    head.appendChild(make("h2", "card-title", data.first.name + " and " + data.second.name));
    var close = make("button", "ghost-btn", "Close");
    close.type = "button";
    close.addEventListener("click", function () { host.hidden = true; });
    head.appendChild(close);
    host.appendChild(head);

    host.appendChild(make("h3", "studies-compared-title", "Settings that differ"));
    var settings = data.settings || [];
    if (!settings.length) {
      host.appendChild(make("p", "muted small", "None: they asked for the same study."));
    } else {
      var table = make("table", "kv-table studies-settings");
      var top = make("tr");
      ["Setting", data.first.name, data.second.name].forEach(function (label) {
        top.appendChild(make("th", "", label));
      });
      table.appendChild(top);
      settings.forEach(function (d, i) {
        var row = make("tr");
        // Two different kinds of study differ everywhere; the first
        // twenty are shown and the rest on asking.
        if (i >= SHOWN_FIRST) row.hidden = true;
        row.appendChild(make("td", "mono", d.setting));
        row.appendChild(make("td", "mono", d.in_first ? JSON.stringify(d.first) : "not set"));
        row.appendChild(make("td", "mono", d.in_second ? JSON.stringify(d.second) : "not set"));
        table.appendChild(row);
      });
      host.appendChild(table);
      if (settings.length > SHOWN_FIRST) {
        var more = make("button", "file-action studies-more",
          "Show the other " + (settings.length - SHOWN_FIRST));
        more.type = "button";
        more.addEventListener("click", function () {
          table.querySelectorAll("tr[hidden]").forEach(function (row) { row.hidden = false; });
          more.remove();
        });
        host.appendChild(more);
      }
    }

    host.appendChild(make("h3", "studies-compared-title", "What they recorded"));
    var measures = data.measures || [];
    if (!measures.length) {
      host.appendChild(make("p", "muted small", "Neither recorded a mean."));
      return;
    }
    var means = make("table", "kv-table studies-means");
    var header = make("tr");
    ["Quantity", data.first.name, data.second.name, "Difference"].forEach(function (label) {
      header.appendChild(make("th", "", label));
    });
    means.appendChild(header);
    measures.forEach(function (m) {
      var row = make("tr");
      row.dataset.analysis = m.analysis;
      row.appendChild(make("td", "", m.label || m.analysis));
      row.appendChild(make("td", "mono", meanSaid(m.first)));
      row.appendChild(make("td", "mono", meanSaid(m.second)));
      var versus = m.versus;
      var cell = make("td", "mono");
      if (versus) {
        cell.textContent = withError(versus.difference, versus.error, m.unit) +
          (versus.resolved ? ", resolved" : ", within the errors");
        if (versus.resolved) row.classList.add("is-resolved");
      } else {
        cell.textContent = "not compared";
      }
      row.appendChild(cell);
      means.appendChild(row);
    });
    host.appendChild(means);
    host.appendChild(make("p", "muted small",
      "A difference is resolved past " + data.resolved_at + " times the two studies' " +
      "combined standard error. A mean an analysis did not stand behind is not compared."));
  }

  function openStudy(path, button) {
    button.disabled = true;
    fetch("/api/explore/switch", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ folder: path })
    }).then(function (r) { return r.json(); }).then(function (d) {
      button.disabled = false;
      if (d && d.ok) {
        location.hash = "#overview";
        location.reload();
      } else {
        button.textContent = (d && d.error) || "Could not open it.";
      }
    }).catch(function () {
      button.disabled = false;
      button.textContent = "The server did not answer.";
    });
  }

  function load(where) {
    if (loading) return loading;
    var query = where ? "?" + new URLSearchParams({ path: where }) : "";
    loading = fetch("/api/studies" + query)
      .then(function (r) { return r.json(); })
      .then(function (data) {
        loading = null;
        if (!data || !data.ok) {
          studies = [];
          el("studies-where").textContent = (data && (data.reason || data.error)) ||
            "The studies could not be listed.";
          draw();
          return;
        }
        lookedIn = data.root;
        studies = data.studies || [];
        chosen = chosen.filter(function (path) {
          return studies.some(function (s) { return s.path === path; });
        });
        el("studies-where").textContent = studies.length + " stud" +
          (studies.length === 1 ? "y" : "ies") + " in " + data.root +
          (data.more ? ", the newest first; there are more than this shows." : ".");
        draw();
      })
      .catch(function () {
        loading = null;
        el("studies-where").textContent = "The server did not answer.";
      });
    return loading;
  }

  function attach() {
    if (!el("studies-grid")) return;
    el("studies-search").addEventListener("input", draw);
    el("studies-compare").addEventListener("click", compare);
    el("studies-clear").addEventListener("click", function () {
      chosen = [];
      el("studies-compared").hidden = true;
      draw();
    });
    var path = el("studies-path");
    el("studies-look-in").addEventListener("click", function () {
      if (window.FastMDXPicker) window.FastMDXPicker.open({ into: "studies-path", mode: "folder" });
    });
    path.addEventListener("change", function () {
      if (path.value.trim()) load(path.value.trim());
    });
    var opened = function (detail) {
      var page = detail && detail.page ? detail.page : detail;
      if (page === "studies") load(lookedIn);
    };
    if (window.FastMDXDashboard && window.FastMDXDashboard.on) {
      window.FastMDXDashboard.on("navigate", opened);
    }
    if (location.hash === "#studies") load();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", attach);
  else attach();

  window.FastMDXStudies = { load: load };
}());
