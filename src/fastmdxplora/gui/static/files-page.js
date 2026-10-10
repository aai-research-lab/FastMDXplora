/* The Files page: what a person does with it.
 *
 * The page is rendered by the server (gui/files_page.py), in the GUI from
 * /api/files-page and in the standalone dashboard into the page itself.
 * This finds and filters the files, sorts them, folds the sections,
 * chooses a run of a study of several, and does what is asked of a file:
 * read it in the side panel, copy its path, show it in the file manager,
 * copy its SHA-256. In the GUI it also asks the server again when the
 * study changes, keeping what was typed, chosen and unfolded.
 */
(function () {
  "use strict";

  var host = null;
  var source = "";
  var shown = "";
  var asking = null;
  var askedAt = 0;
  var stale = true;
  var again = false;
  var can = {};
  var word = "folder";
  var kept = { filter: "", find: "", sort: "order", view: "phases", run: "*", open: {}, expanded: {} };

  function byId(id) { return document.getElementById(id); }
  function all(root, selector) { return Array.prototype.slice.call(root.querySelectorAll(selector)); }

  /* ---- Saying things ------------------------------------------------ */
  function toast(message, kind) {
    var note = byId("dashboard-toast");
    if (!note) {
      note = document.createElement("div");
      note.id = "dashboard-toast";
      note.className = "dashboard-toast";
      note.setAttribute("role", "status");
      document.body.appendChild(note);
    }
    note.textContent = message;
    note.setAttribute("data-kind", kind || "ok");
    note.classList.add("show");
    clearTimeout(toast.timer);
    toast.timer = setTimeout(function () { note.classList.remove("show"); }, 3500);
  }

  /* Through the GUI's Copy (icons.js), which ticks the button; the
   * standalone dashboard carries this script without it, and copies here. */
  function copy(text, button, copied) {
    var icons = window.FastMDXIcons;
    if (icons && icons.copy) return icons.copy(button, text, copied, "Could not copy it.");
    function fallback() {
      var area = document.createElement("textarea");
      area.value = text;
      area.style.position = "fixed";
      area.style.opacity = "0";
      document.body.appendChild(area);
      area.select();
      var ok = false;
      try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
      area.remove();
      return Promise.resolve(ok);
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text).then(function () { return true; }, fallback);
    }
    return fallback();
  }

  // The GUI's Copy says itself, by the button and the notice.
  function ticks() { return !!(window.FastMDXIcons && window.FastMDXIcons.copy); }

  function human(bytes) {
    if (!isFinite(bytes)) return "";
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1048576) return (bytes / 1024).toFixed(1) + " KB";
    if (bytes < 1073741824) return (bytes / 1048576).toFixed(1) + " MB";
    return (bytes / 1073741824).toFixed(2) + " GB";
  }

  /* When each file was written, in the reader's own time and words. */
  function sayWhen(root) {
    var year = new Date().getFullYear();
    all(root, "time[data-when]").forEach(function (el) {
      var at = new Date(parseFloat(el.getAttribute("data-when")) * 1000);
      if (isNaN(at.getTime())) return;
      var options = { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" };
      if (at.getFullYear() !== year) options.year = "numeric";
      el.textContent = at.toLocaleString(undefined, options);
      el.title = at.toLocaleString();
    });
  }

  /* ---- Finding, filtering, choosing a run --------------------------- */
  function words(text) {
    return String(text || "").toLowerCase().split(/\s+/).filter(Boolean);
  }

  function rowText(row) {
    if (!row._text) {
      var name = row.querySelector(".files-name");
      var path = row.querySelector(".files-path");
      row._text = ((name ? name.textContent : "") + " " + (path ? path.textContent : "") + " " +
                   (row.getAttribute("data-path") || "")).toLowerCase();
    }
    return row._text;
  }

  function ofTheRun(el) {
    var run = el.getAttribute("data-run") || "";
    return kept.run === "*" || run === "" || run === kept.run;
  }

  function apply() {
    if (!host) return;
    var wanted = words(kept.find);
    var filtering = Boolean(kept.filter || wanted.length);
    var any = false;
    function matches(row) {
      if (!ofTheRun(row)) return false;
      if (kept.filter && row.getAttribute("data-filter") !== kept.filter) return false;
      var text = rowText(row);
      return wanted.every(function (w) { return text.indexOf(w) !== -1; });
    }
    all(host, ".files-body:not([hidden]) .files-section").forEach(function (section) {
      var seen = 0;
      all(section, ".files-arow").forEach(function (group) {
        var title = (group.getAttribute("data-search") || "");
        var titled = wanted.length && wanted.every(function (w) { return title.indexOf(w) !== -1; });
        var inside = 0;
        all(group, ".files-row").forEach(function (row) {
          var show = matches(row) || (titled && ofTheRun(row) &&
                                      (!kept.filter || row.getAttribute("data-filter") === kept.filter));
          row.hidden = !show;
          if (show) inside += 1;
        });
        var show = ofTheRun(group) && (filtering ? inside > 0 : true);
        group.hidden = !show;
        var files = group.querySelector(".files-arow-files");
        var button = group.querySelector(".files-expand");
        var open = filtering ? inside > 0 : Boolean(kept.expanded[group.getAttribute("data-key")]);
        if (files) files.hidden = !open;
        if (button) button.setAttribute("aria-expanded", String(open));
        if (show) seen += 1;
      });
      all(section, ":scope > .files-rows > .files-row").forEach(function (row) {
        var show = matches(row);
        row.hidden = !show;
        if (show) seen += 1;
      });
      // A theme's heading goes with the analyses under it.
      var heading = null;
      var under = 0;
      all(section, ":scope > .files-rows > .files-theme, :scope > .files-rows > .files-arow, " +
                   ":scope > .files-rows > .files-row")
        .concat([null]).forEach(function (el) {
          if (el === null || el.classList.contains("files-theme")) {
            if (heading) heading.hidden = under === 0;
            heading = el;
            under = 0;
          } else if (!el.hidden) {
            under += 1;
          }
        });
      section.hidden = seen === 0;
      var fold = section.querySelector(".files-fold");
      var key = section.getAttribute("data-phase") === "dir" && fold
        ? fold.getAttribute("aria-controls") : section.getAttribute("data-phase");
      var rows = fold && byId(fold.getAttribute("aria-controls"));
      if (fold && rows) {
        var open = filtering ? seen > 0
          : (key in kept.open ? kept.open[key] : fold.getAttribute("data-first") !== "false");
        rows.hidden = !open;
        fold.setAttribute("aria-expanded", String(open));
      }
      if (seen) any = true;
    });
    var none = host.querySelector(".files-none");
    if (none) none.hidden = any;
    var keys = host.querySelector(".files-keys");
    if (keys) {
      keys.hidden = filtering;
      var first = firstRun();
      all(keys, ".files-tile").forEach(function (tile) {
        var run = tile.getAttribute("data-run") || "";
        tile.hidden = !(run === "" || run === kept.run || (kept.run === "*" && run === first));
      });
    }
    all(host, ".files-filter").forEach(function (chip) {
      chip.setAttribute("aria-pressed", String((chip.getAttribute("data-filter") || "") === kept.filter));
    });
    usage();
  }

  function firstRun() {
    var pick = host.querySelector("[data-run-pick]");
    return pick && pick.options.length > 1 ? pick.options[1].value : "";
  }

  /* The space each phase takes, of the run chosen. */
  function usage() {
    var bar = host.querySelector(".files-bar");
    if (!bar) return;
    var by = {};
    var total = 0;
    all(host, '.files-body[data-body="phases"] .files-row').forEach(function (row) {
      if (!ofTheRun(row)) return;
      var section = row.closest("[data-phase]");
      var phase = section ? section.getAttribute("data-phase") : "other";
      var key = ["setup", "simulation", "analysis", "report", "previous", "scratch"].indexOf(phase) === -1
        ? "other" : phase;
      var size = parseInt(row.getAttribute("data-size"), 10) || 0;
      by[key] = (by[key] || 0) + size;
      total += size;
    });
    if (!total) return;
    all(bar, "[data-usage]").forEach(function (part) {
      part.style.flex = String(by[part.getAttribute("data-usage")] || 0);
    });
    all(host, "[data-usage-key]").forEach(function (key) {
      var size = by[key.getAttribute("data-usage-key")] || 0;
      key.hidden = !size;
      var b = key.querySelector("b");
      if (b) b.textContent = human(size);
    });
    var title = host.querySelector("[data-usage-total]");
    if (title) title.textContent = human(total) + (kept.run === "*" ? " in this study" : " in this run");
  }

  /* ---- Sorting ------------------------------------------------------ */
  function sortKey(el) {
    if (kept.sort === "name") {
      var name = el.querySelector(".files-name");
      return name ? name.textContent.toLowerCase() : "";
    }
    if (kept.sort === "size") return -(parseInt(el.getAttribute("data-size"), 10) || 0);
    if (kept.sort === "mtime") return -(parseFloat(el.getAttribute("data-mtime")) || 0);
    return parseInt(el.getAttribute("data-order"), 10) || 0;
  }

  function sortRows() {
    if (!host) return;
    all(host, ".files-rows").forEach(function (list) {
      if (!list._first) list._first = Array.prototype.slice.call(list.children);
      list.classList.toggle("files-sorted", kept.sort !== "order");
      if (kept.sort === "order") {
        list._first.forEach(function (el) { list.appendChild(el); });
        return;
      }
      var items = list._first.filter(function (el) {
        return el.classList.contains("files-row") || el.classList.contains("files-arow");
      });
      items.sort(function (a, b) {
        var x = sortKey(a), y = sortKey(b);
        return x < y ? -1 : x > y ? 1 : 0;
      });
      items.forEach(function (el) { list.appendChild(el); });
    });
  }

  /* ---- The menu of a file ------------------------------------------- */
  var menu = null;
  function closeMenu(back) {
    if (!menu) return;
    var opener = menu._opener;
    menu.remove();
    menu = null;
    if (back && opener) opener.focus();
  }

  function openMenu(button) {
    closeMenu(false);
    var row = button.closest(".files-row");
    if (!row) return;
    var path = row.getAttribute("data-path");
    var where = row.getAttribute("data-where") || path;
    menu = document.createElement("div");
    menu.className = "files-menu";
    menu.setAttribute("role", "menu");
    menu._opener = button;
    menu._at = Date.now();
    var items = [];
    items.push('<a role="menuitem" href="' + escapeAttr(row.getAttribute("data-href")) +
               '" target="_blank" rel="noopener">Open in a new tab</a>');
    items.push('<button type="button" role="menuitem" data-do="copy">Copy path' +
               '<span class="files-menu-path">' + escapeHTML(where) + "</span></button>");
    if (can.reveal) {
      items.push('<button type="button" role="menuitem" data-do="reveal">Show in ' +
                 escapeHTML(word) + "</button>");
    }
    if (can.sha) items.push('<button type="button" role="menuitem" data-do="sha">Copy SHA-256</button>');
    menu.innerHTML = items.join("");
    document.body.appendChild(menu);
    var r = button.getBoundingClientRect();
    var width = menu.offsetWidth;
    var left = Math.max(8, Math.min(window.innerWidth - width - 8, r.right - width));
    var top = r.bottom + 4;
    if (top + menu.offsetHeight > window.innerHeight - 8) top = Math.max(8, r.top - menu.offsetHeight - 4);
    menu.style.left = left + "px";
    menu.style.top = top + "px";
    menu.addEventListener("click", function (event) {
      var item = event.target.closest("[role=menuitem]");
      if (!item) return;
      var what = item.getAttribute("data-do");
      if (what === "copy") {
        var opener = menu._opener;
        copy(where, opener, "Path copied").then(function (ok) {
          if (!ticks()) toast(ok ? "Path copied." : "Could not copy the path.", ok ? "ok" : "warning");
        });
      } else if (what === "reveal") {
        post("/api/files/reveal", { path: path }).then(function (said) {
          if (!said || !said.ok) toast((said && said.error) || "Could not show it.", "warning");
        });
      } else if (what === "sha") {
        var shaOpener = menu._opener;
        toast("Computing its SHA-256…", "ok");
        fetch("/api/files/sha256?path=" + encodeURIComponent(path), { cache: "no-store" })
          .then(function (r) { return r.json(); })
          .then(function (said) {
            if (!said || !said.ok) { toast((said && said.error) || "Could not compute it.", "warning"); return; }
            var copied = "SHA-256 copied: " + said.sha256.slice(0, 16) + "…";
            copy(said.sha256, shaOpener, copied).then(function (ok) {
              if (!ticks()) toast(ok ? copied : "Could not copy it.", ok ? "ok" : "warning");
            });
          }).catch(function () { toast("The server did not answer.", "warning"); });
      }
      closeMenu(true);
    });
    var first = menu.querySelector("[role=menuitem]");
    if (first) first.focus({ preventScroll: true });
  }

  document.addEventListener("keydown", function (event) {
    if (!menu) return;
    var items = all(menu, "[role=menuitem]");
    var at = items.indexOf(document.activeElement);
    if (event.key === "Escape") { event.preventDefault(); closeMenu(true); }
    else if (event.key === "ArrowDown") { event.preventDefault(); items[(at + 1) % items.length].focus(); }
    else if (event.key === "ArrowUp") { event.preventDefault(); items[(at - 1 + items.length) % items.length].focus(); }
    else if (event.key === "Tab") closeMenu(false);
  }, true);
  document.addEventListener("click", function (event) {
    if (menu && !menu.contains(event.target) && !event.target.closest("[data-menu]")) closeMenu(false);
  });
  window.addEventListener("resize", function () { closeMenu(false); });
  // The page scrolled from under the menu, not the scroll of opening it.
  document.addEventListener("scroll", function () {
    if (menu && Date.now() - menu._at > 300) closeMenu(false);
  }, true);

  function escapeHTML(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function escapeAttr(value) { return escapeHTML(value); }

  function post(url, body) {
    return fetch(url, { method: "POST", headers: { "Content-Type": "application/json" },
                        body: JSON.stringify(body || {}) })
      .then(function (r) { return r.json(); })
      .catch(function () { return { ok: false, error: "The server did not answer." }; });
  }

  /* ---- What is clicked ---------------------------------------------- */
  function wire() {
    host.addEventListener("click", function (event) {
      var target = event.target;
      var fold = target.closest(".files-fold");
      if (fold) {
        var rows = byId(fold.getAttribute("aria-controls"));
        var open = fold.getAttribute("aria-expanded") !== "true";
        fold.setAttribute("aria-expanded", String(open));
        if (rows) rows.hidden = !open;
        var section = fold.closest(".files-section");
        var key = section.getAttribute("data-phase") === "dir" ? fold.getAttribute("aria-controls")
          : section.getAttribute("data-phase");
        kept.open[key] = open;
        return;
      }
      var expand = target.closest(".files-expand");
      if (expand) {
        var group = expand.closest(".files-arow");
        var files = byId(expand.getAttribute("aria-controls"));
        var opened = expand.getAttribute("aria-expanded") !== "true";
        expand.setAttribute("aria-expanded", String(opened));
        if (files) files.hidden = !opened;
        kept.expanded[group.getAttribute("data-key")] = opened;
        return;
      }
      var chip = target.closest(".files-filter");
      if (chip) {
        kept.filter = chip.getAttribute("data-filter") || "";
        apply();
        return;
      }
      var more = target.closest("[data-menu]");
      if (more) { event.preventDefault(); openMenu(more); return; }
      var preview = target.closest("[data-preview], [data-preview-path]");
      if (preview) {
        var path = preview.getAttribute("data-preview-path") ||
          preview.closest(".files-row").getAttribute("data-path");
        if (window.FastMDXFrame && window.FastMDXFrame.previewPath) window.FastMDXFrame.previewPath(path);
        return;
      }
      var copier = target.closest("[data-copy]");
      if (copier) {
        copy(copier.getAttribute("data-copy"), copier).then(function (ok) {
          if (!ticks()) toast(ok ? "Copied." : "Could not copy it.", ok ? "ok" : "warning");
        });
        return;
      }
      var analysis = target.closest("[data-open-analysis]");
      if (analysis && window.FastMDXDashboard) {
        window.FastMDXDashboard.showAnalysis(analysis.getAttribute("data-open-analysis"));
        return;
      }
      var view = target.closest("[data-view]");
      if (view) {
        kept.view = kept.view === "folders" ? "phases" : "folders";
        if (source) refresh(true);
        else showView();
        return;
      }
      var link = target.closest("[data-view-link]");
      if (link && window.FastMDXDashboard) {
        event.preventDefault();
        window.FastMDXDashboard.navigate(link.getAttribute("data-view-link"));
      }
    });
    host.addEventListener("input", function (event) {
      if (event.target.matches("[data-find]")) {
        kept.find = event.target.value;
        apply();
      }
    });
    host.addEventListener("change", function (event) {
      if (event.target.matches("[data-sort]")) {
        kept.sort = event.target.value;
        sortRows();
        apply();
      } else if (event.target.matches("[data-run-pick]")) {
        kept.run = event.target.value;
        apply();
      }
    });
    host.addEventListener("keydown", function (event) {
      if (event.target.matches("[data-find]") && event.key === "Escape" && event.target.value) {
        event.target.value = "";
        kept.find = "";
        apply();
      }
    });
  }

  /* The controls as they were left, after the page is rendered again. */
  function restore() {
    var find = host.querySelector("[data-find]");
    if (find) find.value = kept.find;
    var sort = host.querySelector("[data-sort]");
    if (sort) sort.value = kept.sort;
    var pick = host.querySelector("[data-run-pick]");
    if (pick) {
      if (!Array.prototype.some.call(pick.options, function (o) { return o.value === kept.run; })) kept.run = "*";
      pick.value = kept.run;
    }
    var view = host.querySelector("[data-view]");
    if (view) view.setAttribute("aria-pressed", String(kept.view === "folders"));
    all(host, ".files-fold").forEach(function (fold) {
      fold.setAttribute("data-first", fold.getAttribute("aria-expanded"));
    });
    if (kept.filter && !host.querySelector('.files-filter[data-filter="' + kept.filter + '"]')) kept.filter = "";
    showView();
    sayWhen(host);
    sortRows();
    apply();
  }

  function showView() {
    var bodies = all(host, ".files-body");
    if (bodies.length > 1) {
      bodies.forEach(function (body) { body.hidden = body.getAttribute("data-body") !== kept.view; });
    }
    var view = host.querySelector("[data-view]");
    if (view) view.setAttribute("aria-pressed", String(kept.view === "folders"));
    apply();
  }

  /* ---- In the GUI: asked for, and asked again ----------------------- */
  function active() {
    return document.documentElement.getAttribute("data-page") === "files";
  }

  function refresh(force) {
    if (!source) return;
    if (!force && !active()) { stale = true; return; }
    var now = Date.now();
    // A change while the page is being asked for is asked for again once
    // the answer is in: dropped, the last change of a run (its report)
    // stayed off the page until something else changed.
    if (!force && asking) { again = true; return; }
    if (!force && now - askedAt < 2500) {
      clearTimeout(refresh.later);
      refresh.later = setTimeout(function () { refresh(false); }, 2600 - (now - askedAt));
      return;
    }
    askedAt = now;
    stale = false;
    var asked = fetch(source + "?view=" + encodeURIComponent(kept.view), { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (said) {
        if (asked !== asking) return;
        asking = null;
        if (again) {
          again = false;
          setTimeout(function () { refresh(false); }, 0);
        }
        if (!said || !said.ok) return;
        can = said.can || {};
        word = said.reveal_word || "folder";
        if (said.html === shown) return;
        var column = document.querySelector(".main");
        var top = column ? column.scrollTop : 0;
        var focused = document.activeElement && host.contains(document.activeElement)
          ? document.activeElement.getAttribute("data-find") !== null : false;
        shown = said.html;
        host.innerHTML = said.html;
        restore();
        if (column) column.scrollTop = top;
        if (focused) {
          var find = host.querySelector("[data-find]");
          if (find) { find.focus(); find.setSelectionRange(find.value.length, find.value.length); }
        }
        window.dispatchEvent(new CustomEvent("files:rendered", { detail: { can: can } }));
      })
      .catch(function () {
        asking = null;
        if (again) { again = false; setTimeout(function () { refresh(false); }, 2600); }
      });
    asking = asked;
  }

  function start() {
    host = byId("files-page");
    if (!host) return;
    source = host.getAttribute("data-source") || "";
    wire();
    if (source) {
      window.addEventListener("dashboard:navigate", function (event) {
        if (event.detail && event.detail.page === "files" && stale) refresh(true);
      });
      window.addEventListener("dashboard:results-updated", function () { refresh(false); });
      window.addEventListener("dashboard:run-changed", function () {
        shown = "";
        kept.run = "*";
        kept.expanded = {};
        stale = true;
        refresh(false);
      });
      if (active()) refresh(true);
    } else {
      can = {};
      restore();
    }
  }

  window.FastMDXFiles = {
    refresh: function () { refresh(true); },
    state: function () { return JSON.parse(JSON.stringify(kept)); },
    toast: toast,
    post: post,
    human: human,
  };

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
}());
