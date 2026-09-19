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
  var LIMITS = { sidebar: [180, 360], panel: [280, 960] };

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
        .getPropertyValue("--" + which + "-width")) || (which === "sidebar" ? 232 : 700);
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
      setWidth(which, which === "sidebar" ? 232 : 700);
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

  function human(size) {
    var n = Number(size);
    if (!isFinite(n)) return "";
    if (n < 1024) return n + " B";
    if (n < 1048576) return (n / 1024).toFixed(0) + " KB";
    return (n / 1048576).toFixed(1) + " MB";
  }

  function preview(item) {
    var pane = el("side-preview");
    var body = el("side-preview-body");
    if (!pane || !body) return;
    pane.hidden = false;
    el("side-preview-name").textContent = item.path;
    el("side-preview-download").href = item.download_href || item.href;
    el("side-preview-open").href = item.href;
    body.innerHTML = "";
    var ext = (item.name || "").split(".").pop().toLowerCase();
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
    if (["yml", "yaml", "json", "md", "txt", "log", "csv", "pdb", "dat"].indexOf(ext) !== -1
        && Number(item.size || 0) < 512 * 1024) {
      fetch(item.href).then(function (r) { return r.text(); }).then(function (text) {
        var pre = document.createElement("pre");
        pre.textContent = text;
        body.appendChild(pre);
      }).catch(function () {
        var note = document.createElement("div");
        note.className = "side-preview-note";
        note.textContent = "Could not read it here. Open or download it instead.";
        body.appendChild(note);
      });
      return;
    }
    var note = document.createElement("div");
    note.className = "side-preview-note";
    note.textContent = "No preview for this kind of file. Open or download it.";
    body.appendChild(note);
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
      renderFiles(Array.isArray(d.artifacts) ? d.artifacts : []);
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
    var pw = parseInt(store.get("panelWidth", "700"), 10);
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

  window.FastMDXFrame = { showTab: showTab, setCollapsed: setCollapsed, applyTheme: applyTheme };
})();
