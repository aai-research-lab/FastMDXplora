/* The frame: three columns, a side panel, a settings popup, a theme.
 *
 * None of this knows what page is in the middle. The sidebar is the study,
 * the side panel is the run's own narration and files, and both are the
 * same whatever task is showing. That is the point: you never lose the run
 * to look at the molecule.
 *
 * Column widths and the theme are remembered in localStorage, which is
 * right for a preference and wrong for anything about a study -- nothing
 * about a run is kept here.
 */
(function () {
  "use strict";

  function el(id) { return document.getElementById(id); }
  function $$(sel, root) { return Array.from((root || document).querySelectorAll(sel)); }

  var store = {
    get: function (key, fallback) {
      try { var v = localStorage.getItem("fmx." + key); return v === null ? fallback : v; }
      catch (e) { return fallback; }
    },
    set: function (key, value) {
      try { localStorage.setItem("fmx." + key, String(value)); } catch (e) { /* private mode */ }
    }
  };

  /* ---- Theme -------------------------------------------------------- */
  function applyTheme(name) {
    document.body.dataset.theme = name;
    $$(".seg-btn[data-theme]").forEach(function (b) {
      b.classList.toggle("active", b.dataset.theme === name);
    });
    store.set("theme", name);
  }

  /* ---- Column widths ------------------------------------------------ */
  var LIMITS = { sidebar: [180, 320], panel: [280, 640] };

  function setWidth(which, px) {
    var lim = LIMITS[which];
    px = Math.max(lim[0], Math.min(lim[1], px));
    document.documentElement.style.setProperty("--" + which + "-width", px + "px");
    store.set(which + "Width", px);
  }

  function wireHandle(handle) {
    var which = handle.dataset.handle;
    handle.addEventListener("mousedown", function (down) {
      down.preventDefault();
      var startX = down.clientX;
      var startW = parseFloat(getComputedStyle(document.documentElement)
        .getPropertyValue("--" + which + "-width")) || (which === "sidebar" ? 232 : 560);
      handle.classList.add("dragging");
      document.body.classList.add("col-dragging");
      function move(e) {
        // The sidebar grows to the right; the panel grows to the left.
        var delta = e.clientX - startX;
        setWidth(which, which === "sidebar" ? startW + delta : startW - delta);
      }
      function up() {
        handle.classList.remove("dragging");
        document.body.classList.remove("col-dragging");
        window.removeEventListener("mousemove", move);
        window.removeEventListener("mouseup", up);
      }
      window.addEventListener("mousemove", move);
      window.addEventListener("mouseup", up);
    });
    // Double-click puts it back.
    handle.addEventListener("dblclick", function () {
      setWidth(which, which === "sidebar" ? 232 : 560);
    });
  }

  /* ---- Side panel: tabs and collapse -------------------------------- */
  function showTab(name) {
    $$(".side-tab").forEach(function (t) {
      var on = t.dataset.sideTab === name;
      t.classList.toggle("active", on);
      t.setAttribute("aria-selected", String(on));
    });
    $$(".side-pane").forEach(function (p) { p.hidden = p.dataset.sidePane !== name; });
    store.set("sideTab", name);
    if (name === "files") loadFiles();
  }

  function setCollapsed(yes) {
    document.body.classList.toggle("panel-collapsed", yes);
    var expand = el("side-expand");
    if (expand) expand.hidden = !yes;
    store.set("panelCollapsed", yes ? "1" : "0");
  }

  function setSidebarCollapsed(yes) {
    document.body.classList.toggle("sidebar-collapsed", yes);
    var expand = el("sidebar-expand");
    if (expand) expand.hidden = !yes;
    store.set("sidebarCollapsed", yes ? "1" : "0");
  }

  /* ---- The log ------------------------------------------------------ */
  var logFilter = "all";
  var lastSeen = 0;

  /* What the CLI prints, sorted into what a reader wants to filter on.
   * The explain text -- the "why" blocks -- arrives as ordinary info
   * events with a citation on the end, and a refusal arrives as an error
   * whose message names its code. Neither is tagged; this reads the shape. */
  function classify(ev) {
    var msg = String(ev.message || "");
    var level = String(ev.level || "info").toLowerCase();
    if (level === "explain") return { kind: "why", level: "info" };
    if (level === "error" || /refused|Refusal|\b[a-z]+\.[a-z_]+\.[a-z_]+\b.*(?:refus|cannot|must)/.test(msg)) {
      return { kind: "refused", level: "error" };
    }
    if (/^QUALIFIED|qualif/i.test(msg) && level !== "info") return { kind: "qualified", level: "warning" };
    if (/→ .+ \d{4}|\(doi:|et al\./.test(msg)) return { kind: "why", level: level };
    if (/complete|Resumed from|✓/.test(msg) && level !== "error") return { kind: "line", level: "success" };
    return { kind: "line", level: level };
  }

  function stamp(iso) {
    if (!iso) return "";
    var d = new Date(iso);
    if (isNaN(d)) return "";
    var pad = function (n) { return String(n).padStart(2, "0"); };
    return pad(d.getHours()) + ":" + pad(d.getMinutes()) + ":" + pad(d.getSeconds());
  }

  function renderLog(events) {
    var box = el("side-log");
    if (!box) return;
    var wasAtBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 24;
    box.innerHTML = "";
    var shown = 0;
    events.forEach(function (ev) {
      var c = classify(ev);
      if (logFilter === "refusals" && c.kind !== "refused") return;
      if (logFilter === "why" && c.kind !== "why" && c.kind !== "qualified") return;
      shown += 1;
      if (c.kind === "why" || c.kind === "refused" || c.kind === "qualified") {
        var block = document.createElement("div");
        block.className = "side-log-block";
        block.dataset.kind = c.kind;
        if (c.kind !== "why") {
          var kind = document.createElement("span");
          kind.className = "side-log-kind";
          kind.textContent = c.kind;
          block.appendChild(kind);
        }
        block.appendChild(document.createTextNode(String(ev.message || "")));
        box.appendChild(block);
      } else {
        var line = document.createElement("div");
        line.className = "side-log-line";
        line.dataset.level = c.level;
        var ts = document.createElement("span");
        ts.className = "side-log-ts";
        ts.textContent = stamp(ev.timestamp);
        var text = document.createElement("span");
        text.className = "side-log-text";
        text.textContent = String(ev.message || "");
        line.appendChild(ts);
        line.appendChild(text);
        box.appendChild(line);
      }
    });
    if (!shown) {
      var empty = document.createElement("div");
      empty.className = "side-log-empty";
      empty.textContent = events.length
        ? "Nothing matches this filter."
        : "Nothing yet. The run's narration appears here as it happens.";
      box.appendChild(empty);
    }
    var follow = el("side-follow");
    if ((follow && follow.checked) || wasAtBottom) box.scrollTop = box.scrollHeight;
  }

  var cachedEvents = [];
  function loadLog() {
    return fetch("/api/events").then(function (r) { return r.json(); }).then(function (d) {
      cachedEvents = Array.isArray(d.events) ? d.events : [];
      renderLog(cachedEvents);
    }).catch(function () { /* the panel is not load-bearing */ });
  }

  /* ---- Files -------------------------------------------------------- */
  var filesLoaded = false;
  var lastArtifacts = [];

  function human(size) {
    var n = Number(size);
    if (!isFinite(n)) return "";
    if (n < 1024) return n + " B";
    if (n < 1048576) return (n / 1024).toFixed(0) + " KB";
    return (n / 1048576).toFixed(1) + " MB";
  }

  /* ---- The file preview ------------------------------------------------
   *
   * Two modes. Code is the bytes, with line numbers, and it is the
   * default for anything scientific: a person checking a config wants
   * the file, not a rendering of it. View is the convenience, and means
   * something different per type -- a table for CSV, structure for JSON
   * and YAML, a document for Markdown, a header summary for a structure
   * file, levels coloured for a log. The choice is remembered.
   * -------------------------------------------------------------------- */

  var VIEWABLE = {
    md: "doc", markdown: "doc",
    csv: "table", tsv: "table", dat: "table",
    json: "tree", yml: "tree", yaml: "tree", toml: "tree", ini: "tree", cfg: "tree",
    xml: "tree",                     /* OpenMM's state.xml and system.xml fold by their indentation */
    log: "log", txt: "log", sha256: "log",
    pdb: "structure", cif: "structure",
    sdf: "molecule", mol2: "molecule",  /* a ligand: name, atoms and bonds before the text */
    /* .py has no View: Code is the view. */
  };

  function escapeText(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  function renderCode(host, data) {
    var wrap = document.createElement("div");
    wrap.className = "file-code";
    var lines = String(data.text || "").split("\n");
    var gutter = document.createElement("div");
    gutter.className = "gutter";
    gutter.textContent = lines.map(function (_, i) { return i + 1; }).join("\n");
    var pre = document.createElement("pre");
    pre.className = "lines";
    pre.textContent = data.text || "";
    wrap.appendChild(gutter);
    wrap.appendChild(pre);
    host.appendChild(wrap);
  }

  function renderFileLog(host, text) {
    text.split("\n").slice(0, 5000).forEach(function (line) {
      var div = document.createElement("div");
      var upper = line.toUpperCase();
      div.className = "log-line" + (/ERROR|REFUS|FAIL|TRACEBACK/.test(upper) ? " error"
        : /WARN/.test(upper) ? " warn"
        : /\u2713|DONE|COMPLETE|WROTE/.test(upper) ? " ok" : "");
      div.textContent = line;
      host.appendChild(div);
    });
  }

  function renderStructure(host, text) {
    /* What the file holds, before the file: atoms, residues, chains,
     * models. A structure file is thousands of lines that all look the
     * same, and the counts are what a person is checking. */
    var atoms = 0, models = 0;
    var residues = {}, chains = {};
    text.split("\n").forEach(function (line) {
      if (line.indexOf("ATOM") === 0 || line.indexOf("HETATM") === 0) {
        atoms += 1;
        chains[line.substr(21, 1)] = true;
        residues[line.substr(21, 1) + line.substr(22, 5)] = true;
      } else if (line.indexOf("MODEL") === 0) { models += 1; }
    });
    if (atoms) {
      var note = document.createElement("div");
      note.className = "summary";
      note.textContent = atoms.toLocaleString() + " atoms \u00b7 "
        + Object.keys(residues).length.toLocaleString() + " residues \u00b7 "
        + Object.keys(chains).length + " chain(s)"
        + (models > 1 ? " \u00b7 " + models + " models" : "");
      host.appendChild(note);
    }
    var pre = document.createElement("pre");
    pre.textContent = text;
    host.appendChild(pre);
  }

  function renderMolecule(host, text, suffix) {
    /* An SDF's header is three lines then a counts line, "  9  8" for
     * atoms and bonds; a MOL2 says @<TRIPOS>MOLECULE, then the name, then
     * the counts. The counts are what a person is checking. */
    var lines = text.split("\n");
    var name = "", atoms = null, bonds = null;
    if (suffix === "sdf") {
      name = (lines[0] || "").trim();
      var counts = (lines[3] || "").trim().split(/\s+/);
      atoms = parseInt(counts[0], 10); bonds = parseInt(counts[1], 10);
    } else {
      var at = lines.findIndex(function (l) { return l.indexOf("@<TRIPOS>MOLECULE") === 0; });
      if (at !== -1) {
        name = (lines[at + 1] || "").trim();
        var c2 = (lines[at + 2] || "").trim().split(/\s+/);
        atoms = parseInt(c2[0], 10); bonds = parseInt(c2[1], 10);
      }
    }
    var note = document.createElement("div");
    note.className = "summary";
    note.textContent = (name ? name + " \u00b7 " : "")
      + (isNaN(atoms) || atoms === null ? "" : atoms + " atoms")
      + (isNaN(bonds) || bonds === null ? "" : " \u00b7 " + bonds + " bonds");
    if (note.textContent) host.appendChild(note);
    var pre = document.createElement("pre");
    pre.textContent = text;
    host.appendChild(pre);
  }

  function renderDoc(host, data) {
    /* The server renders Markdown with the same library the report page
     * uses, so a .md reads the same on both and nothing is vendored. On
     * an install without the library the text is shown as written,
     * exactly as the report page does. */
    var div = document.createElement("div");
    div.className = "report-document";
    if (data.html) {
      div.innerHTML = data.html;
    } else {
      var pre = document.createElement("pre");
      pre.textContent = data.text || "";
      div.appendChild(pre);
    }
    host.appendChild(div);
  }

  /* ---- A collapsible tree for JSON, and for YAML by its indentation ---- */

  function treeNode(key, value, depth) {
    var row = document.createElement("div");
    row.className = "tree-row";
    row.style.paddingLeft = (depth * 14 + 4) + "px";
    var isObj = value && typeof value === "object";
    var isArr = Array.isArray(value);
    if (isObj) {
      var n = isArr ? value.length : Object.keys(value).length;
      var toggle = document.createElement("button");
      toggle.type = "button";
      toggle.className = "tree-toggle";
      toggle.textContent = depth < 1 ? "\u25be" : "\u25b8";
      var label = document.createElement("span");
      label.className = "tree-key";
      label.textContent = key === null ? "" : key;
      var meta = document.createElement("span");
      meta.className = "tree-meta";
      meta.textContent = (isArr ? "[" + n + "]" : "{" + n + "}");
      row.appendChild(toggle); row.appendChild(label); row.appendChild(meta);
      var kids = document.createElement("div");
      kids.className = "tree-kids";
      kids.hidden = depth >= 1;
      (isArr ? value.map(function (v, i) { return [i, v]; })
             : Object.keys(value).map(function (k) { return [k, value[k]]; }))
        .forEach(function (pair) { kids.appendChild(treeNode(pair[0], pair[1], depth + 1)); });
      toggle.addEventListener("click", function () {
        kids.hidden = !kids.hidden;
        toggle.textContent = kids.hidden ? "\u25b8" : "\u25be";
      });
      var wrap = document.createElement("div");
      wrap.appendChild(row); wrap.appendChild(kids);
      return wrap;
    }
    var k = document.createElement("span");
    k.className = "tree-key";
    k.textContent = key === null ? "" : key;
    var v = document.createElement("span");
    v.className = "tree-val " + (value === null ? "null" : typeof value);
    v.textContent = value === null ? "null" : typeof value === "string" ? value : String(value);
    row.appendChild(k); row.appendChild(v);
    return row;
  }

  function renderJsonTree(host, text) {
    var data;
    try { data = JSON.parse(text); } catch (e) { return false; }
    var tree = document.createElement("div");
    tree.className = "file-tree";
    if (data && typeof data === "object") {
      (Array.isArray(data) ? data.map(function (v, i) { return [i, v]; })
                           : Object.keys(data).map(function (k) { return [k, data[k]]; }))
        .forEach(function (pair) { tree.appendChild(treeNode(pair[0], pair[1], 0)); });
    } else {
      tree.appendChild(treeNode(null, data, 0));
    }
    host.appendChild(tree);
    return true;
  }

  function renderYamlTree(host, text) {
    /* YAML is its indentation. Each line becomes a row at its depth; a
     * line whose next line is deeper gets a toggle that folds what is
     * under it. No parser, so what is shown is what the file says. */
    var lines = text.split("\n");
    var tree = document.createElement("div");
    tree.className = "file-tree";
    var rows = [];
    lines.forEach(function (line, i) {
      if (!line.trim() || line.trim().indexOf("#") === 0) return;
      var indent = line.match(/^ */)[0].length;
      var next = null;
      for (var j = i + 1; j < lines.length; j++) {
        if (lines[j].trim() && lines[j].trim().indexOf("#") !== 0) { next = lines[j].match(/^ */)[0].length; break; }
      }
      rows.push({ text: line.trim(), depth: indent, parent: next !== null && next > indent });
    });
    var stack = [];
    rows.forEach(function (r) {
      while (stack.length && stack[stack.length - 1].depth >= r.depth) stack.pop();
      var row = document.createElement("div");
      row.className = "tree-row";
      row.style.paddingLeft = (r.depth * 7 + 4) + "px";  /* a space of YAML is 7px of tree */
      var parts = r.text.match(/^(-?\s*[^:]+?:)(\s.*)?$/);
      if (r.parent) {
        var toggle = document.createElement("button");
        toggle.type = "button"; toggle.className = "tree-toggle"; toggle.textContent = "\u25be";
        row.appendChild(toggle);
      }
      var k = document.createElement("span"); k.className = "tree-key";
      var v = document.createElement("span"); v.className = "tree-val string";
      if (parts) { k.textContent = parts[1]; v.textContent = (parts[2] || "").trim(); }
      else { k.textContent = r.text; }
      row.appendChild(k); row.appendChild(v);
      var kids = document.createElement("div");
      kids.className = "tree-kids";
      var wrap = document.createElement("div");
      wrap.appendChild(row); wrap.appendChild(kids);
      (stack.length ? stack[stack.length - 1].kids : tree).appendChild(wrap);
      if (r.parent) {
        var t = row.querySelector(".tree-toggle");
        t.addEventListener("click", function () {
          kids.hidden = !kids.hidden; t.textContent = kids.hidden ? "\u25b8" : "\u25be";
        });
        stack.push({ depth: r.depth, kids: kids });
      }
    });
    host.appendChild(tree);
    return true;
  }

  /* ---- A table that sorts on a click of its header ---------------------- */

  function renderTable(host, text, sep) {
    var rows = text.trim().split("\n").slice(0, 5000).map(function (r) { return r.split(sep); });
    if (!rows.length) return false;
    var table = document.createElement("table");
    table.className = "file-table";
    var thead = document.createElement("thead");
    var head = document.createElement("tr");
    var body = document.createElement("tbody");
    var data = rows.slice(1);
    function fill() {
      body.innerHTML = "";
      data.forEach(function (r) {
        var tr = document.createElement("tr");
        r.forEach(function (c) { var td = document.createElement("td"); td.textContent = String(c).trim(); tr.appendChild(td); });
        body.appendChild(tr);
      });
    }
    rows[0].forEach(function (c, i) {
      var th = document.createElement("th");
      th.textContent = String(c).trim();
      th.title = "Sort by " + th.textContent;
      var asc = true;
      th.addEventListener("click", function () {
        data.sort(function (a, b) {
          var x = a[i], y = b[i];
          var nx = parseFloat(x), ny = parseFloat(y);
          var cmp = (!isNaN(nx) && !isNaN(ny)) ? nx - ny : String(x).localeCompare(String(y));
          return asc ? cmp : -cmp;
        });
        asc = !asc;
        Array.prototype.forEach.call(head.children, function (h) { h.classList.remove("sorted"); });
        th.classList.add("sorted");
        fill();
      });
      head.appendChild(th);
    });
    thead.appendChild(head);
    table.appendChild(thead);
    table.appendChild(body);
    fill();
    host.appendChild(table);
    return true;
  }

  function showText(body, data, mode) {
    body.innerHTML = "";
    if (data.truncated) {
      var cut = document.createElement("div");
      cut.className = "cut";
      cut.textContent = "Shown: the start and the end of the file. The middle is "
        + "not here; download it to read the whole.";
      body.appendChild(cut);
    }
    if (mode === "code") { renderCode(body, data); return; }
    var host = document.createElement("div");
    host.className = "file-view";
    var kind = VIEWABLE[data.suffix] || "log";
    var text = data.text || "";
    if (kind === "table") {
      if (!renderTable(host, text, data.suffix === "tsv" ? "\t" : data.suffix === "dat" ? /\s+/ : ",")) {
        renderFileLog(host, text);
      }
    } else if (kind === "tree") {
      var ok = data.suffix === "json" ? renderJsonTree(host, text) : renderYamlTree(host, text);  /* yaml, toml, ini, xml: by indentation */
      if (!ok) renderFileLog(host, text);
    } else if (kind === "structure") {
      renderStructure(host, text);
    } else if (kind === "molecule") {
      renderMolecule(host, text, data.suffix);
    } else if (kind === "doc") {
      renderDoc(host, data);
    } else {
      renderFileLog(host, text);
    }
    body.appendChild(host);
  }

  function preview(item) {
    var pane = el("side-preview");
    var body = el("side-preview-body");
    if (!pane || !body) return;
    pane.hidden = false;
    var seam = el("side-seam");
    if (seam) seam.hidden = false;
    el("side-preview-name").textContent = item.path;
    el("side-preview-facts").textContent = "";
    el("side-preview-download").href = item.download_href || item.href;
    el("side-preview-open").href = item.href;
    body.innerHTML = "";
    var modes = el("side-preview-modes");
    if (modes) modes.hidden = true;
    var ext = (item.name || "").split(".").pop().toLowerCase();
    /* Open shows the file the way the browser would: a PDF in its
     * viewer, an image at full size, an HTML report. For text it is
     * pointless -- you are reading it -- so it is offered only where it
     * means something. Download stays: the study may be on a machine
     * the browser is not. */
    el("side-preview-open").hidden = ["png", "jpg", "jpeg", "gif", "svg", "webp", "pdf", "html"].indexOf(ext) === -1;
    if (["png", "jpg", "jpeg", "gif", "svg", "webp"].indexOf(ext) !== -1) {
      var img = document.createElement("img");
      img.src = item.href;
      img.alt = item.name;
      body.appendChild(img);
      return;
    }
    if (ext === "pdf" || ext === "html") {
      var frame = document.createElement("iframe");
      frame.src = item.href;
      frame.title = item.name;
      body.appendChild(frame);
      return;
    }
    /* Everything the Agent's + accepts, read by the same reader over the
     * same list of types. The old preview had a shorter list of its own
     * and a 512 KB cliff with no explanation. */
    fetch("/api/file-text?path=" + encodeURIComponent(item.path))
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!data || !data.ok) {
          var note = document.createElement("div");
          note.className = "side-preview-note";
          note.textContent = (data && data.error)
            || "Could not read it here. Open or download it instead.";
          body.appendChild(note);
          return;
        }
        el("side-preview-facts").textContent =
          (data.size < 1024 ? data.size + " B"
           : data.size < 1048576 ? (data.size / 1024).toFixed(1) + " KB"
           : (data.size / 1048576).toFixed(1) + " MB")
          + " \u00b7 " + data.lines.toLocaleString() + " lines \u00b7 " + data.sha256;
        var canView = Object.prototype.hasOwnProperty.call(VIEWABLE, data.suffix);
        var mode = canView ? (store.get("previewMode", "code") === "view" ? "view" : "code") : "code";
        if (modes && canView) {
          modes.hidden = false;
          Array.prototype.forEach.call(modes.querySelectorAll(".mode-btn"), function (b) {
            b.setAttribute("aria-pressed", String(b.dataset.mode === mode));
            b.onclick = function () {
              mode = b.dataset.mode;
              store.set("previewMode", mode);
              Array.prototype.forEach.call(modes.querySelectorAll(".mode-btn"), function (o) {
                o.setAttribute("aria-pressed", String(o.dataset.mode === mode));
              });
              showText(body, data, mode);
            };
          });
        }
        showText(body, data, mode);
      })
      .catch(function () {
        var note = document.createElement("div");
        note.className = "side-preview-note";
        note.textContent = "Could not read it here. Open or download it instead.";
        body.appendChild(note);
      });
  }

  function renderFiles(items) {
    var box = el("side-files");
    if (!box) return;
    box.innerHTML = "";
    if (!items.length) {
      var empty = document.createElement("div");
      empty.className = "side-log-empty";
      empty.textContent = "Nothing written yet.";
      box.appendChild(empty);
      return;
    }
    // Group by top-level directory, in the order a study runs.
    var order = ["", "setup", "simulation", "analysis", "report"];
    var groups = {};
    items.forEach(function (it) {
      var top = it.path.indexOf("/") === -1 ? "" : it.path.split("/")[0];
      (groups[top] = groups[top] || []).push(it);
    });
    Object.keys(groups).sort(function (a, b) {
      var ia = order.indexOf(a), ib = order.indexOf(b);
      return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib) || a.localeCompare(b);
    }).forEach(function (top) {
      if (top) {
        var dir = document.createElement("div");
        dir.className = "side-file side-file-dir";
        dir.innerHTML = '<span class="side-file-name"></span><span class="side-file-meta"></span>';
        dir.querySelector(".side-file-name").textContent = top + "/";
        dir.querySelector(".side-file-meta").textContent = groups[top].length + " files";
        box.appendChild(dir);
      }
      groups[top].forEach(function (it) {
        var btn = document.createElement("button");
        btn.type = "button";
        btn.className = "side-file";
        btn.dataset.path = it.path;
        btn.style.paddingLeft = top ? "24px" : "10px";
        btn.innerHTML = '<span class="side-file-name"></span><span class="side-file-meta"></span>';
        btn.querySelector(".side-file-name").textContent = it.name;
        btn.querySelector(".side-file-meta").textContent = human(it.size);
        btn.addEventListener("click", function () {
          $$(".side-file.active").forEach(function (b) { b.classList.remove("active"); });
          btn.classList.add("active");
          preview(it);
        });
        box.appendChild(btn);
      });
    });
  }

  function loadFiles() {
    return fetch("/api/artifacts").then(function (r) { return r.json(); }).then(function (d) {
      filesLoaded = true;
      lastArtifacts = Array.isArray(d.artifacts) ? d.artifacts : [];
      renderFiles(lastArtifacts);
    }).catch(function () { /* not load-bearing */ });
  }

  /* ---- Settings popup ----------------------------------------------- */
  function setPopup(open) {
    var popup = el("settings-popup");
    var trigger = el("settings-open");
    if (!popup || !trigger) return;
    popup.hidden = !open;
    trigger.setAttribute("aria-expanded", String(open));
  }

  function loadAgentStatus() {
    fetch("/api/agent/model", {
      method: "POST", headers: {"content-type": "application/json"}, body: "{}"
    }).then(function (r) { return r.json(); }).then(function (d) {
      var cur = d && d.current;
      var engine = el("settings-engine");
      if (engine) engine.textContent = cur ? cur.provider + " \u00b7 " + cur.model : "none";
    }).catch(function () { /* fine */ });
  }

  /* ---- Wire it up --------------------------------------------------- */
  document.addEventListener("DOMContentLoaded", function () {
    if (!el("side-panel")) return;

    applyTheme(store.get("theme", "graphite"));
    $$(".seg-btn[data-theme]").forEach(function (b) {
      b.addEventListener("click", function () { applyTheme(b.dataset.theme); });
    });

    var sw = parseInt(store.get("sidebarWidth", "232"), 10);
    var pw = parseInt(store.get("panelWidth", "560"), 10);
    if (sw) setWidth("sidebar", sw);
    if (pw) setWidth("panel", pw);
    $$(".col-handle").forEach(wireHandle);

    setCollapsed(store.get("panelCollapsed", "0") === "1");
    el("side-collapse").addEventListener("click", function () { setCollapsed(true); });
    el("side-expand").addEventListener("click", function () { setCollapsed(false); });
    setSidebarCollapsed(store.get("sidebarCollapsed", "0") === "1");
    el("sidebar-collapse").addEventListener("click", function () { setSidebarCollapsed(true); });
    el("sidebar-expand").addEventListener("click", function () { setSidebarCollapsed(false); });

    $$(".side-tab").forEach(function (t) {
      t.addEventListener("click", function () { showTab(t.dataset.sideTab); });
    });
    showTab(store.get("sideTab", "log"));

    $$(".side-filter").forEach(function (f) {
      f.addEventListener("click", function () {
        logFilter = f.dataset.logFilter;
        $$(".side-filter").forEach(function (g) { g.classList.toggle("active", g === f); });
        renderLog(cachedEvents);
      });
    });

    el("settings-open").addEventListener("click", function (e) {
      e.stopPropagation();
      setPopup(el("settings-popup").hidden);
    });
    document.addEventListener("click", function (e) {
      var popup = el("settings-popup");
      if (popup && !popup.hidden && !popup.contains(e.target)) setPopup(false);
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") setPopup(false);
    });
    /* The version is on the Cite page, filled in by the server. Read it
     * from there rather than asking for a second copy. */
    /* One button. Open opens the folder where the browser can, and the
     * path goes to the clipboard either way, so the button is useful on
     * a machine the browser is not on. Four buttons in a 232px sidebar
     * was too many. */
    var openOut = el("open-output");
    if (openOut) {
      openOut.addEventListener("click", function () {
        var path = (el("sidebar-output-folder") || {}).textContent || "";
        if (!path || path === "\u2014") return;
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(path.trim()).then(function () {
            openOut.textContent = "Path copied";
            setTimeout(function () { openOut.textContent = "Output"; }, 1400);
          });
        }
      }, true);
    }

    var loadStudy = el("load-study");
    var loadPath = el("load-study-path");
    if (loadStudy && loadPath && window.FastMDXPicker) {
      loadStudy.addEventListener("click", function () {
        // The same folder picker the builder uses, opening into a hidden
        // input. When it writes a folder, load that study.
        window.FastMDXPicker.open({ into: "load-study-path", mode: "folder" });
      });
      loadPath.addEventListener("change", function () {
        var folder = loadPath.value.trim();
        if (!folder) return;
        loadStudy.disabled = true;
        loadStudy.textContent = "Loading\u2026";
        fetch("/api/explore/switch", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ folder: folder })
        }).then(function (r) { return r.json(); }).then(function (d) {
          loadStudy.disabled = false;
          loadStudy.textContent = "Load study";
          loadPath.value = "";
          if (d && d.ok) {
            // Read the new study at once rather than waiting for the poll,
            // and land on the overview.
            if (window.FastMDXDashboard && window.FastMDXDashboard.navigate) {
              window.FastMDXDashboard.navigate("overview");
            }
            location.reload();
          } else {
            window.alert((d && d.error) || "Could not load that folder.");
          }
        }).catch(function () {
          loadStudy.disabled = false;
          loadStudy.textContent = "Load study";
          window.alert("Could not reach the server.");
        });
      });
    }

    /* A run in another folder: say so, offer the way back. */
    var elsewhere = el("study-elsewhere");
    var elsewhereName = el("study-elsewhere-name");
    var elsewhereView = el("study-elsewhere-view");
    if (elsewhere && window.FastMDXDashboard) {
      var elsewherePath = "";
      window.FastMDXDashboard.on("app-state", function (s) {
        elsewherePath = (s && s.running_elsewhere) || "";
        elsewhere.hidden = !elsewherePath;
        if (elsewherePath && elsewhereName) {
          /* The system's name, as the folder now carries it:
           * fastmdxplora_<system>_study_<stamp>. Older folders show
           * their whole name. */
          var folder = elsewherePath.split("/").pop();
          var m = /^fastmdxplora_(.+)_study_\d+$/.exec(folder);
          elsewhereName.textContent = m ? m[1] : folder;
          elsewhereName.title = elsewherePath;
          var pct = el("study-elsewhere-pct");
          var prog = s.running_elsewhere_progress || {};
          if (pct) {
            pct.textContent = typeof prog.percent === "number" ? prog.percent.toFixed(1) + "%"
              : (prog.stage ? String(prog.stage) : "");
          }
        }
      });
      if (elsewhereView) {
        elsewhereView.addEventListener("click", function () {
          if (!elsewherePath) return;
          fetch("/api/explore/switch", {
            method: "POST", headers: { "content-type": "application/json" },
            body: JSON.stringify({ folder: elsewherePath })
          }).then(function () { location.reload(); });
        });
      }
    }

    /* The seam between the file list and the preview. Drag it; either
     * side can take the whole panel. Double-click resets. Remembered,
     * as the column seams are. */
    var seam = el("side-seam");
    var files = el("side-files");
    if (seam && files) {
      function setFilesHeight(pct) {
        var bounded = Math.max(0, Math.min(100, pct));
        document.documentElement.style.setProperty("--side-files-height", bounded + "%");
        store.set("sideFilesHeight", String(bounded));
      }
      setFilesHeight(parseFloat(store.get("sideFilesHeight", "45")) || 45);
      seam.addEventListener("mousedown", function (e) {
        e.preventDefault();
        seam.classList.add("dragging");
        var pane = files.parentElement;
        function move(ev) {
          var box = pane.getBoundingClientRect();
          if (box.height <= 0) return;
          setFilesHeight(((ev.clientY - box.top) / box.height) * 100);
        }
        function up() {
          seam.classList.remove("dragging");
          document.removeEventListener("mousemove", move);
          document.removeEventListener("mouseup", up);
        }
        document.addEventListener("mousemove", move);
        document.addEventListener("mouseup", up);
      });
      seam.addEventListener("dblclick", function () { setFilesHeight(45); });
    }

    var version = el("settings-version");
    var cite = el("cite-version");
    if (version && cite) version.textContent = cite.textContent.trim();

    /* "Agent settings…" opens the Agent's own dialog. Landing on the page
     * and leaving somebody to find the button was the same as not
     * linking it. */
    var agentLink = el("settings-agent-link");
    if (agentLink) {
      agentLink.addEventListener("click", function (e) {
        e.preventDefault();
        setPopup(false);
        /* No navigation. The dialog is at body level and opens over
         * whatever page is showing; changing the page to open a
         * settings dialog is a detour nobody asked for. */
        if (window.FastMDXAgent && window.FastMDXAgent.openSettings) {
          window.FastMDXAgent.openSettings();
        }
      });
    }
    /* The other popup items navigate; close the popup when they do. */
    $$(".settings-item[data-view-link]").forEach(function (a) {
      a.addEventListener("click", function () { setPopup(false); });
    });

    loadLog();
    loadAgentStatus();
    // Follow the run. The dashboard already polls app-state; the log rides
    // the same cadence rather than adding its own.
    if (window.FastMDXDashboard && window.FastMDXDashboard.on) {
      window.FastMDXDashboard.on("app-state", function () {
        loadLog();
        if (filesLoaded && !el("side-pane-files-hidden")) loadFiles();
      });
    } else {
      setInterval(loadLog, 5000);
    }
  });

  /* Open a file in the panel's reader by its path, for the centre Files
   * page's View button. The panel's tab shows, the list loads if it has
   * not, the row is marked, and the file opens. */
  function previewPath(path) {
    setCollapsed(false);
    showTab("files");
    var open = function () {
      var item = lastArtifacts.find(function (it) { return it.path === path; });
      if (!item) return false;
      $$(".side-file.active").forEach(function (b) { b.classList.remove("active"); });
      $$("#side-files .side-file").forEach(function (b) {
        if (b.dataset.path === path) { b.classList.add("active"); b.scrollIntoView({ block: "nearest" }); }
      });
      preview(item);
      return true;
    };
    if (!open()) loadFiles().then(open);
  }

  window.FastMDXFrame = { showTab: showTab, setCollapsed: setCollapsed, applyTheme: applyTheme,
                          previewPath: previewPath };
})();
