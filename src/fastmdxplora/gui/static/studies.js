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
  /* The sidebar's close icon (sidebar_icons.py "close"). */
  var CLOSE_ICON = '<svg class="line-icon" viewBox="0 0 24 24" width="16" height="16" fill="none" '
    + 'stroke="currentColor" stroke-width="1.7" stroke-linecap="round" aria-hidden="true">'
    + '<path d="M6 6l12 12M18 6L6 18"/></svg>';
  var chosen = [];
  var lookedIn = "";
  var loading = null;
  var SHOWN_FIRST = 20;
  // Tags: the one the cards are narrowed to, the card being tagged, and
  // the tags the studies here carry (offered as one is typed).
  var tagged = null;
  var editing = null;
  var tagsUsed = [];
  var TAG_LENGTH = 40;
  var NOTE_LENGTH = 200;

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

  /* A time within the day said as how long ago; earlier, its date. */
  function timeSaid(when) {
    var date = new Date(when);
    if (isNaN(date.getTime())) return "";
    var minutes = Math.round((Date.now() - date.getTime()) / 60000);
    if (minutes < 1) return "Just now";
    if (minutes < 60) return minutes + " min ago";
    if (minutes < 24 * 60) return Math.round(minutes / 60) + " h ago";
    return dateSaid(when);
  }

  /* Tags and a note of the person's own, changed on the card or under
   * the table's row. */
  function tagButton(study) {
    var tag = make("button", "file-action study-tag-edit",
      (study.tags || []).length || study.note ? "Tags" : "Tag");
    tag.type = "button";
    tag.title = "Tags and a note of your own, kept in the study's folder";
    tag.addEventListener("click", function () {
      editing = study.path;
      draw();
      var input = document.querySelector(".study-tag-editor .study-tag-input");
      if (input) input.focus();
    });
    return tag;
  }

  /* A study's system as it is shown: the ID the person gave it in this
   * browser (preferences.js, by its folder), else its own. */
  function shownId(study) {
    try {
      var kept = JSON.parse(localStorage.getItem("fmx.study:" + study.path) || "null");
      if (kept && /^[A-Z0-9]{1,4}$/.test(kept.name || "")) return kept.name;
    } catch (e) { /* not kept */ }
    return study.system || "";
  }

  function dateSaid(when) {
    var date = new Date(when);
    return isNaN(date.getTime()) ? "" : date.toLocaleDateString(undefined,
      { year: "numeric", month: "short", day: "numeric" });
  }

  function searchText(study) {
    return [study.name, shownId(study), study.system, study.structure || "", study.kind,
      study.state, study.forcefield || "",
      (study.tags || []).join(" "), study.note || ""].join(" ").toLowerCase();
  }

  function hasTag(study, tag) {
    var wanted = tag.toLowerCase();
    return (study.tags || []).some(function (t) { return t.toLowerCase() === wanted; });
  }

  /* The tags a card carries, each narrowing the cards to it, and its note. */
  function tagsShown(study) {
    var holder = make("div", "study-tags");
    (study.tags || []).forEach(function (tag) {
      var chip = make("button", "study-tag", tag);
      chip.type = "button";
      chip.title = "Show only the studies tagged " + tag;
      chip.addEventListener("click", function () { narrowTo(tag); });
      holder.appendChild(chip);
    });
    return holder;
  }

  function narrowTo(tag) {
    tagged = tag;
    draw();
  }

  /* The card's tags and note, changed on it and kept in the study's folder. */
  function tagEditor(study) {
    var form = make("form", "study-tag-editor");
    form.setAttribute("aria-label", "Tags of " + study.name);
    var tags = (study.tags || []).slice();
    var list = make("div", "study-tags");
    function drawTags() {
      list.replaceChildren.apply(list, tags.map(function (tag, i) {
        var chip = make("span", "study-tag is-editing", tag);
        var drop = make("button", "study-tag-drop", "\u00d7");
        drop.type = "button";
        drop.setAttribute("aria-label", "Remove the tag " + tag);
        drop.addEventListener("click", function () {
          tags.splice(i, 1);
          drawTags();
        });
        chip.appendChild(drop);
        return chip;
      }));
    }
    drawTags();
    var adding = make("input", "study-tag-input");
    adding.type = "text";
    adding.maxLength = TAG_LENGTH;
    adding.setAttribute("list", "studies-tags-used");
    adding.placeholder = "Add a tag";
    adding.setAttribute("aria-label", "A tag to add");
    function add() {
      var tag = adding.value.replace(/\s+/g, " ").trim();
      if (tag && !tags.some(function (t) { return t.toLowerCase() === tag.toLowerCase(); })) {
        tags.push(tag);
        drawTags();
      }
      adding.value = "";
    }
    adding.addEventListener("keydown", function (event) {
      if (event.key === "Enter") {
        event.preventDefault();
        add();
      }
    });
    var addButton = make("button", "file-action", "Add");
    addButton.type = "button";
    addButton.addEventListener("click", add);
    var note = make("input", "study-note-input");
    note.type = "text";
    note.maxLength = NOTE_LENGTH;
    note.value = study.note || "";
    note.placeholder = "A note: one line";
    note.setAttribute("aria-label", "A note on the study");
    var said = make("p", "muted small study-tag-said");
    said.setAttribute("role", "status");
    var save = make("button", "file-action", "Save");
    save.type = "submit";
    var cancel = make("button", "file-action", "Cancel");
    cancel.type = "button";
    cancel.addEventListener("click", function () {
      editing = null;
      draw();
    });
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      if (adding.value.trim()) add();
      save.disabled = true;
      fetch("/api/study-tags", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ path: study.path, tags: tags, note: note.value })
      }).then(function (r) { return r.json(); }).then(function (d) {
        save.disabled = false;
        if (!d || !d.ok) {
          said.textContent = (d && (d.reason || d.error)) || "The tags could not be kept.";
          return;
        }
        study.tags = d.tags;
        study.note = d.note;
        editing = null;
        tagsUsed = countTags();
        offerTags();
        draw();
      }).catch(function () {
        save.disabled = false;
        said.textContent = "The server did not answer.";
      });
    });
    var row = make("div", "study-tag-row");
    row.append(adding, addButton);
    var buttons = make("div", "study-tag-row");
    buttons.append(save, cancel);
    form.append(list, row, note, buttons, said);
    return form;
  }

  function countTags() {
    var counted = {};
    studies.forEach(function (study) {
      (study.tags || []).forEach(function (tag) {
        var key = tag.toLowerCase();
        if (!counted[key]) counted[key] = { tag: tag, studies: 0 };
        counted[key].studies += 1;
      });
    });
    return Object.keys(counted).map(function (key) { return counted[key]; })
      .sort(function (a, b) { return b.studies - a.studies || a.tag.localeCompare(b.tag); });
  }

  /* The tags used here, offered as a tag is typed. */
  function offerTags() {
    var listed = el("studies-tags-used");
    if (!listed) return;
    listed.replaceChildren.apply(listed, tagsUsed.map(function (used) {
      var option = make("option");
      option.value = used.tag;
      return option;
    }));
  }

  /* A figure the study plotted, else its backbone rendered from its
   * structure, in the page's scheme; its name where neither can be had. */
  function picture(frame, study, named) {
    var img = make("img");
    img.loading = "lazy";
    img.alt = "";
    var asked = { path: study.path };
    if (study.thumbnail === "backbone") asked.scheme = scheme();
    img.src = "/api/study-thumbnail?" + new URLSearchParams(asked);
    img.addEventListener("error", named);
    frame.dataset.picture = study.thumbnail;
    if (study.thumbnail === "backbone") frame.title = backboneSaid();
    frame.replaceChildren(img);
  }

  function backboneSaid() {
    return "No figure yet: the protein's backbone, coloured from its N terminus " +
      "(purple) to its C terminus (" + (scheme() === "light" ? "green" : "yellow") + ")";
  }

  /* The page's scheme, for a picture made for it. */
  function scheme() {
    return document.documentElement.dataset.theme === "light" ? "light" : "dark";
  }

  function token(name, fallback) {
    var value = "";
    try {
      value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    } catch (e) { /* no styles yet */ }
    return value || fallback;
  }

  var SVG = "http://www.w3.org/2000/svg";
  function svg(tag, attrs, parent) {
    var node = document.createElementNS(SVG, tag);
    Object.keys(attrs).forEach(function (key) { node.setAttribute(key, attrs[key]); });
    if (parent) parent.appendChild(node);
    return node;
  }

  /* A card's series in the page's colours: the line, the part its mean is
   * taken over shaded, the mean dashed, and what it is in a corner. Its
   * figure was the analysis's, white on any scheme. */
  var seriesOf = {};
  function plotSeries(frame, data) {
    var width = 480, height = 180, pad = { left: 10, right: 10, top: 26, bottom: 12 };
    var xs = data.x, ys = data.y;
    // A frame an analysis could not measure is a gap, not a zero.
    var known = ys.filter(function (y) { return y != null && isFinite(y); });
    if (known.length < 2) return false;
    var lo = Math.min.apply(null, known), hi = Math.max.apply(null, known);
    if (data.mean != null) { lo = Math.min(lo, data.mean); hi = Math.max(hi, data.mean); }
    var room = (hi - lo) * 0.08 || Math.abs(hi) * 0.05 || 1;
    lo -= room; hi += room;
    var x0 = xs[0], x1 = xs[xs.length - 1] > x0 ? xs[xs.length - 1] : x0 + 1;
    var px = function (x) { return pad.left + (x - x0) / (x1 - x0) * (width - pad.left - pad.right); };
    var py = function (y) { return pad.top + (1 - (y - lo) / (hi - lo)) * (height - pad.top - pad.bottom); };
    var line = token("--accent-cyan", "#2698ba");
    var muted = token("--text-muted", "#9a9ca1");
    var plot = svg("svg", { viewBox: "0 0 " + width + " " + height, class: "study-series",
                            role: "img", "aria-label": data.label + " over the run" }, null);
    if (data.from_x != null && data.kind === "time") {
      svg("rect", { x: px(data.from_x), y: pad.top, width: Math.max(0, px(x1) - px(data.from_x)),
                    height: height - pad.top - pad.bottom, fill: line, "fill-opacity": 0.08 }, plot);
    }
    var d = "", gap = true;
    xs.forEach(function (x, i) {
      if (ys[i] == null || !isFinite(ys[i])) { gap = true; return; }
      d += (gap ? "M" : "L") + px(x).toFixed(1) + " " + py(ys[i]).toFixed(1);
      gap = false;
    });
    svg("path", { d: d, fill: "none", stroke: line, "stroke-width": 1.6,
                  "stroke-linejoin": "round" }, plot);
    if (data.mean != null) {
      svg("line", { x1: px(data.from_x != null ? data.from_x : x0), x2: px(x1),
                    y1: py(data.mean), y2: py(data.mean), stroke: token("--accent-orange", "#ffb86b"),
                    "stroke-width": 1.2, "stroke-dasharray": "5 4" }, plot);
    }
    var said = svg("text", { x: pad.left, y: 16, fill: muted, "font-size": 12 }, plot);
    said.textContent = data.label + (data.unit ? " (" + data.unit + ")" : "") +
      (data.kind === "residue" ? " by residue" : "");
    frame.replaceChildren(plot);
    return true;
  }

  function cardSeries(frame, study, named) {
    var key = study.path;
    if (!seriesOf[key]) {
      seriesOf[key] = fetch("/api/study-thumbnail?" + new URLSearchParams({ path: study.path, series: "1" }))
        .then(function (r) { return r.json(); })
        .catch(function () { return null; });
    }
    seriesOf[key].then(function (data) {
      if (data && data.ok && data.y && data.y.length > 1 && plotSeries(frame, data)) {
        frame.dataset.picture = "series";
      } else {
        named();
      }
    });
  }

  /* Plotted again, and the backbone fetched again, in the new scheme. */
  document.addEventListener("fmx:theme", function () {
    Array.prototype.forEach.call(document.querySelectorAll(".study-thumb"), function (frame) {
      var card = frame.closest(".study-card");
      var path = card && card.dataset.path;
      if (!path) return;
      if (frame.dataset.picture === "series" && seriesOf[path]) {
        seriesOf[path].then(function (data) { if (data && data.ok) plotSeries(frame, data); });
      } else if (frame.dataset.picture === "backbone") {
        var img = frame.querySelector("img");
        if (img) img.src = "/api/study-thumbnail?" + new URLSearchParams({ path: path, scheme: scheme() });
        frame.title = backboneSaid();
      }
    });
  });

  function card(study) {
    var item = make("article", "study-card");
    item.setAttribute("role", "listitem");
    item.dataset.path = study.path;
    item.dataset.state = study.state;
    var frame = make("div", "study-thumb");
    var named = function () {
      frame.replaceChildren(make("span", "study-thumb-none", shownId(study) || study.name));
    };
    if (study.series) {
      // Its first measure's series, plotted in the page's colours.
      cardSeries(frame, study, study.thumbnail ? function () { picture(frame, study, named); } : named);
    } else if (study.thumbnail) {
      picture(frame, study, named);
    } else {
      named();
    }
    item.appendChild(frame);
    var body = make("div", "study-body");
    var head = make("div", "study-head");
    head.appendChild(make("span", "study-name", study.name));
    head.appendChild(make("span", "study-state", study.state));
    body.appendChild(head);
    body.appendChild(make("div", "study-what",
      [shownId(study), study.kind, study.forcefield].filter(Boolean).join(" · ")));
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
    if (editing === study.path) {
      body.appendChild(tagEditor(study));
    } else {
      if ((study.tags || []).length) body.appendChild(tagsShown(study));
      if (study.note) body.appendChild(make("div", "study-note", study.note));
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
    actions.append(open, tagButton(study), pick);
    body.appendChild(actions);
    item.appendChild(body);
    return item;
  }

  /* ---- As a table --------------------------------------------------- */
  var VIEW_KEPT = "fmx.studiesView";
  var view = "cards";
  var sortBy = { key: "when", down: true };
  var STATE_ORDER = { running: 0, failed: 1, stopped: 2, interrupted: 3, incomplete: 4,
                      completed: 5, "not started": 6 };

  function keptView() {
    try { return localStorage.getItem(VIEW_KEPT) === "table" ? "table" : "cards"; }
    catch (e) { return "cards"; }
  }

  function showAs(chosenView, remember) {
    view = chosenView === "table" ? "table" : "cards";
    Array.prototype.forEach.call(document.querySelectorAll("[data-studies-view]"), function (b) {
      var on = b.getAttribute("data-studies-view") === view;
      b.classList.toggle("active", on);
      b.setAttribute("aria-pressed", String(on));
    });
    if (remember) {
      try { localStorage.setItem(VIEW_KEPT, view); } catch (e) { /* not kept */ }
    }
    draw();
  }

  /* The means the table has columns for: the first three named among the
   * studies shown, in the order the cards give them. */
  function meanColumns(shown) {
    var columns = [];
    shown.forEach(function (study) {
      (study.means || []).forEach(function (m) {
        var key = m.analysis + ":" + (m.label || m.analysis);
        if (columns.length < 3 && !columns.some(function (c) { return c.key === key; })) {
          columns.push({ key: key, label: m.label || m.analysis, analysis: m.analysis });
        }
      });
    });
    return columns;
  }

  function meanOf(study, column) {
    return (study.means || []).find(function (m) {
      return m.analysis + ":" + (m.label || m.analysis) === column.key;
    }) || null;
  }

  function sortValue(study, key, columns) {
    if (key === "name") return String(study.name || "").toLowerCase();
    if (key === "system") return String(shownId(study) || "").toLowerCase();
    if (key === "state") return STATE_ORDER[String(study.state || "").toLowerCase()];
    if (key === "when" || key === "active") {
      var t = new Date(key === "when" ? study.when : study.last_active || study.when).getTime();
      return isNaN(t) ? null : t;
    }
    var column = columns.find(function (c) { return c.key === key; });
    var m = column ? meanOf(study, column) : null;
    return m && m.mean != null ? m.mean : null;
  }

  function sorted(shown, columns) {
    var key = sortBy.key;
    return shown.slice().sort(function (a, b) {
      var x = sortValue(a, key, columns), y = sortValue(b, key, columns);
      // What has no value goes last, either way.
      if (x == null && y == null) return 0;
      if (x == null) return 1;
      if (y == null) return -1;
      var order = x < y ? -1 : x > y ? 1 : 0;
      return sortBy.down ? -order : order;
    });
  }

  function refocus(selector, key, value) {
    var found = Array.prototype.find.call(document.querySelectorAll(selector), function (node) {
      return node.dataset[key] === value;
    });
    if (found) found.focus();
  }

  function table(shown) {
    var host = el("studies-table");
    var columns = meanColumns(shown);
    var heads = [["", null], ["Study", "name"], ["System", "system"], ["State", "state"],
                 ["Started", "when"], ["Last activity", "active"]]
                 .concat(columns.map(function (c) { return [c.label, c.key]; }))
                 .concat([["Tags", null], ["", null]]);
    var head = make("thead");
    var row = make("tr");
    heads.forEach(function (h) {
      var th = make("th");
      th.scope = "col";
      if (h[1]) {
        var button = make("button", "studies-sort", h[0]);
        button.type = "button";
        button.dataset.key = h[1];
        var on = sortBy.key === h[1];
        th.setAttribute("aria-sort", on ? (sortBy.down ? "descending" : "ascending") : "none");
        if (on) button.dataset.dir = sortBy.down ? "down" : "up";
        button.addEventListener("click", function () {
          // A date is sorted newest first to begin with.
          var dated = h[1] === "when" || h[1] === "active";
          sortBy = { key: h[1], down: sortBy.key === h[1] ? !sortBy.down : dated };
          draw();
          // The table is built again: the keyboard stays where it was.
          refocus(".studies-sort", "key", h[1]);
        });
        th.appendChild(button);
      } else if (h[0]) {
        th.textContent = h[0];
      } else {
        th.appendChild(make("span", "sr-only", heads.indexOf(h) === 0 ? "Compare" : "Open"));
      }
      row.appendChild(th);
    });
    head.appendChild(row);
    var body = make("tbody");
    sorted(shown, columns).forEach(function (study) {
      var tr = make("tr", "studies-row");
      tr.dataset.path = study.path;
      tr.dataset.state = String(study.state || "").toLowerCase();
      var pick = make("td");
      var box = make("input");
      box.type = "checkbox";
      box.checked = chosen.indexOf(study.path) >= 0;
      box.setAttribute("aria-label", "Compare " + study.name);
      box.dataset.path = study.path;
      box.addEventListener("change", function () {
        choose(study.path, box.checked);
        refocus("#studies-table tbody input[type=checkbox]", "path", study.path);
      });
      pick.appendChild(box);
      var name = make("td", "studies-name");
      // The name opens the study, as Open does at the row's end.
      var named = make("button", "studies-name-text", study.name);
      named.type = "button";
      named.title = "Open " + study.path;
      named.addEventListener("click", function () { openStudy(study.path, named); });
      name.appendChild(named);
      var kind = [study.kind, study.forcefield].filter(Boolean).join(" · ");
      if (kind) name.appendChild(make("span", "studies-kind muted small", kind));
      var state = make("td", "studies-state");
      var light = make("span", "recent-light");
      light.dataset.state = String(study.state || "").toLowerCase();
      light.setAttribute("aria-hidden", "true");
      state.append(light, document.createTextNode(" " + (study.state || "")));
      var active = make("td", "muted", timeSaid(study.last_active || study.when));
      if (study.last_active) active.title = new Date(study.last_active).toLocaleString();
      var system = make("td", "", shownId(study));
      if (study.structure && study.structure !== system.textContent) system.title = study.structure;
      tr.append(pick, name, system, state,
                make("td", "muted", dateSaid(study.when)), active);
      columns.forEach(function (column) {
        var m = meanOf(study, column);
        tr.appendChild(make("td", "mono studies-mean", m ? meanSaid(m) : ""));
      });
      var tags = make("td", "studies-tags");
      (study.tags || []).forEach(function (t) { tags.appendChild(make("span", "study-tag", t)); });
      if (study.note) tags.title = study.note;
      var open = make("td");
      var acts = make("div", "studies-actions");
      var button = make("button", "file-action study-open", "Open");
      button.type = "button";
      button.addEventListener("click", function () { openStudy(study.path, button); });
      acts.append(button, tagButton(study));
      open.appendChild(acts);
      tr.append(tags, open);
      body.appendChild(tr);
      // Its tags and note changed under its row, as on its card.
      if (editing === study.path) {
        var under = make("tr", "studies-editing");
        var cell = make("td");
        cell.colSpan = heads.length;
        cell.appendChild(tagEditor(study));
        under.appendChild(cell);
        body.appendChild(under);
      }
    });
    host.replaceChildren(head, body);
  }

  function draw() {
    var grid = el("studies-grid");
    var empty = el("studies-empty");
    if (!grid) return;
    var wanted = (el("studies-search").value || "").trim().toLowerCase().split(/\s+/)
      .filter(Boolean);
    var shown = studies.filter(function (study) {
      var text = searchText(study);
      return (!tagged || hasTag(study, tagged))
        && wanted.every(function (word) { return text.indexOf(word) >= 0; });
    });
    var filter = el("studies-tag-filter");
    if (filter) {
      filter.hidden = !tagged;
      el("studies-tag-filter-said").textContent = tagged ? "Tagged " + tagged : "";
    }
    var wrap = el("studies-table-wrap");
    var asTable = view === "table" && !!wrap;
    grid.hidden = asTable;
    if (wrap) wrap.hidden = !asTable || !shown.length;
    if (asTable) {
      grid.replaceChildren();
      table(shown);
    } else {
      grid.replaceChildren.apply(grid, shown.map(card));
    }
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
    var close = make("button", "line-btn");
    close.type = "button";
    close.title = "Close";
    close.setAttribute("aria-label", "Close");
    close.innerHTML = CLOSE_ICON;
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
        // Listed again: a study may have new numbers since.
        seriesOf = {};
        if (!where) counted(data);
        tagsUsed = data.tags_used || [];
        offerTags();
        if (tagged && !studies.some(function (s) { return hasTag(s, tagged); })) tagged = null;
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
    view = keptView();
    Array.prototype.forEach.call(document.querySelectorAll("[data-studies-view]"), function (b) {
      var on = b.getAttribute("data-studies-view") === view;
      b.classList.toggle("active", on);
      b.setAttribute("aria-pressed", String(on));
      b.addEventListener("click", function () { showAs(b.getAttribute("data-studies-view"), true); });
    });
    el("studies-tag-filter-clear").addEventListener("click", function () { narrowTo(null); });
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

  /* The sidebar's part: how many studies the workspace holds, beside All
   * studies, and the newest of them under Recent, to switch to. */
  var workspace = null;
  var asked = null;

  function counted(data) {
    workspace = data && data.ok ? data : null;
    var count = el("nav-studies-count");
    if (count) {
      count.textContent = workspace
        ? String(workspace.studies.length) + (workspace.more ? "+" : "") : "";
    }
  }

  /* Asked once and kept, unless `fresh`: the sidebar's Recent asks again
   * when another study is opened, and a while after it last asked, so a
   * study's state and a study begun since are said. */
  var askedAt = 0;
  function askTheWorkspace(fresh) {
    if (fresh) asked = null;
    if (asked) return asked;
    askedAt = Date.now();
    asked = fetch("/api/studies")
      .then(function (r) { return r.json(); })
      .then(function (data) { counted(data); return workspace; })
      .catch(function () { asked = null; return null; });
    return asked;
  }

  function sameFolder(a, b) {
    return String(a || "").replace(/\/+$/, "") === String(b || "").replace(/\/+$/, "");
  }

  /* The sidebar's Recent: the workspace's newest studies, the open one
   * marked, each opened with a click. Folded or not as last left. Each
   * name is shortened in its middle to the width there is, as the active
   * study's folder is, and its state is a light: the word is on hover and
   * read out with the name. */
  var SHOWN_RECENT = 6;
  var STATE_WORDS = { running: "Running", completed: "Completed", stopped: "Stopped",
                      failed: "Failed", interrupted: "Interrupted",
                      incomplete: "Incomplete", "not started": "Not started" };

  function stateWord(state) {
    var key = String(state || "").toLowerCase();
    return STATE_WORDS[key] || (key ? key.charAt(0).toUpperCase() + key.slice(1) : "");
  }

  function fitRecentNames() {
    var dashboard = window.FastMDXDashboard;
    if (!dashboard || !dashboard.fitInTheMiddle) return;
    Array.prototype.forEach.call(document.querySelectorAll(".sidebar-recent-name"),
      function (shown) {
        var room = shown.getBoundingClientRect().width;
        if (room > 0) dashboard.fitInTheMiddle(shown, shown.dataset.name || "", room);
      });
  }

  function showRecent(fresh, evenUnderThePointer) {
    var list = el("sidebar-recent-list");
    if (!list) return;
    askTheWorkspace(fresh).then(function (data) {
      // Not built again under somebody's keyboard, nor, unless another
      // study was opened, under their pointer.
      if (list.contains(document.activeElement)) return;
      if (!evenUnderThePointer && list.matches(":hover")) return;
      var open = ((el("sidebar-output-folder") || {}).textContent || "").trim();
      var recent = (data && data.studies || []).slice(0, SHOWN_RECENT);
      if (!recent.length) {
        list.replaceChildren(make("div", "sidebar-recent-empty",
          data ? "No studies in this workspace yet." : "The studies could not be listed."));
        return;
      }
      list.replaceChildren.apply(list, recent.map(function (study) {
        var here = sameFolder(study.path, open);
        var word = stateWord(study.state);
        var name = shownId(study) || study.name;
        var item = make("button", "sidebar-recent-item");
        item.type = "button";
        item.title = (word ? word + "\n" : "") + study.path;
        item.dataset.path = study.path;
        if (here) item.setAttribute("aria-current", "true");
        var light = make("span", "recent-light");
        light.dataset.state = String(study.state || "").toLowerCase();
        light.setAttribute("aria-hidden", "true");
        var shown = make("span", "sidebar-recent-name", name);
        shown.dataset.name = name;
        item.append(shown, light);
        // Named in full, as the shortened name on screen is not.
        item.setAttribute("aria-label", name + (word ? ", " + word : ""));
        item.addEventListener("click", function () {
          if (!here) openStudy(study.path, item);
          else if (window.FastMDXDashboard) window.FastMDXDashboard.navigate("overview");
        });
        return item;
      }));
      fitRecentNames();
    });
  }

  /* Fitted again as the sidebar's width changes, and once its typeface
   * has come. */
  function fitRecentAsTheSidebarChanges() {
    var list = el("sidebar-recent-list");
    if (!list) return;
    var width = 0;
    if (window.ResizeObserver) {
      new ResizeObserver(function (seen) {
        var now = seen[0] ? seen[0].contentRect.width : 0;
        // Hidden, nothing is fitted; shown again, it is, at whatever width.
        if (!now) { width = 0; return; }
        if (Math.abs(now - width) > 0.5) { width = now; fitRecentNames(); }
      }).observe(list);
    }
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(fitRecentNames);
  }

  function keepRecentFolded() {
    var fold = el("sidebar-recent");
    if (!fold) return;
    try { if (localStorage.getItem("fmx.recentOpen") === "0") fold.open = false; } catch (e) {}
    fold.addEventListener("toggle", function () {
      try { localStorage.setItem("fmx.recentOpen", fold.open ? "1" : "0"); } catch (e) {}
    });
  }

  /* With the sidebar folded to its strip, Recent is its icon there, and
   * a click shows its studies beside the strip, to open one; a click
   * elsewhere, Escape, or a study opened puts them away. The fold kept for
   * the sidebar shown is left as it was. */
  function folded() {
    return document.body.classList.contains("sidebar-collapsed")
      && !document.body.classList.contains("sidebar-peek")
      && window.matchMedia("(min-width: 801px)").matches;
  }

  function recentBesideTheStrip() {
    var fold = el("sidebar-recent");
    if (!fold) return;
    var head = fold.querySelector("summary");
    var list = el("sidebar-recent-list");
    var wasOpen = fold.open;

    function shut(back) {
      if (!fold.classList.contains("flyout")) return;
      fold.classList.remove("flyout");
      head.setAttribute("aria-expanded", "false");
      fold.open = wasOpen;
      if (back) head.focus();
    }

    head.addEventListener("click", function (event) {
      if (!folded()) return;
      event.preventDefault();
      if (fold.classList.contains("flyout")) { shut(false); return; }
      wasOpen = fold.open;
      var at = head.getBoundingClientRect();
      list.style.top = Math.round(at.top) + "px";
      fold.classList.add("flyout");
      fold.open = true;
      head.setAttribute("aria-expanded", "true");
      showRecent(false, true);
      var first = list.querySelector(".sidebar-recent-item");
      if (first && event.detail === 0) first.focus();
    });
    // Its toggle is not the fold kept for the sidebar shown.
    fold.addEventListener("toggle", function (event) {
      if (fold.classList.contains("flyout")) event.stopImmediatePropagation();
    }, true);
    list.addEventListener("click", function (event) {
      if (event.target.closest(".sidebar-recent-item")) shut(false);
    });
    document.addEventListener("click", function (event) {
      if (!fold.contains(event.target)) shut(false);
    });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && fold.classList.contains("flyout")) shut(true);
    });
    // The sidebar shown again, or folded: the list goes back in place.
    new MutationObserver(function () { if (!folded()) shut(false); })
      .observe(document.body, { attributes: true, attributeFilter: ["class"] });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", attach);
    document.addEventListener("DOMContentLoaded", recentBesideTheStrip);
    document.addEventListener("DOMContentLoaded", keepRecentFolded);
    document.addEventListener("DOMContentLoaded", fitRecentAsTheSidebarChanges);
  } else {
    attach();
    recentBesideTheStrip();
    keepRecentFolded();
    fitRecentAsTheSidebarChanges();
  }
  setTimeout(showRecent, 1500);
  // Again once the open study is known, or another is opened.
  var recentFor = null;
  var RECENT_AGAIN_MS = 30000;
  window.addEventListener("dashboard:app-state", function () {
    var open = ((el("sidebar-output-folder") || {}).textContent || "").trim();
    var stale = Date.now() - askedAt > RECENT_AGAIN_MS;
    if (open === recentFor && !stale) return;
    var moved = open !== recentFor;
    recentFor = open;
    showRecent(moved || stale, moved);
  });

  // The open study's ID given or cleared: shown so at once.
  document.addEventListener("change", function (event) {
    if (!event.target || event.target.id !== "setting-run-name") return;
    showRecent(false, true);
    if (studies.length) draw();
  });

  window.FastMDXStudies = { load: load, narrowTo: narrowTo,
                            showRecent: showRecent };
}());
