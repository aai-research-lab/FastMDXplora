/* Open study documents in this browser window. Only keys and last pages
 * are kept here; records still come from the one active study on the
 * server. Closing a document never changes files or stops its run. */
(function () {
  "use strict";
  var api, host, list, note, unkept, goneNotice, storageKey = "", tabs = [], closed = [];
  var active = "", pending = false, hydrated = false;
  var lastRender = "", explicitKey = "", roving = "", shownActive = null;
  // Whether the open study's tab is being kept in view: true until the
  // strip is moved so that it is not (a wheel, a drag, the keyboard).
  var following = true, tabsSized = null;
  var focusOnOpen = "";
  /* A switch asked another way (Recent, All studies, Load study, Back):
   * the strip is busy until it is answered. */
  var elsewhere = false;
  /* Said once the page now being shown has been announced. */
  var saidOnArrival = "";
  /* How many of the workspace's studies share each ID (studies.js). */
  var idsShared = {};
  var MAX_TABS = 100;
  var GONE_SAID = {
    folder: "This study's folder is no longer there.",
    missing: "This study is not among the studies here."
  };
  function validKey(key) { return typeof key === "string" && /^[0-9a-f]{16}$/.test(key); }
  function safeRoute(route) {
    var match = /^#?([a-z-]+)(?:\/([a-zA-Z0-9_-]{1,120}))?$/.exec(String(route || ""));
    if (!match || api.state.pages.indexOf(match[1]) < 0) return "overview";
    return match[1] + (match[1] === "analysis" && match[2] ? "/" + match[2] : "");
  }
  function cut(text) { return typeof text === "string" ? text.slice(0, 200) : ""; }
  function pageNow() {
    var state = api.state;
    return safeRoute(state.activePage + (state.activePlace ? "/" + state.activePlace : ""));
  }
  function studyPage() { return ["studies", "run", "compare"].indexOf(api.state.activePage) < 0; }
  /* Named as Recent names it: the system, and the folder too where
   * another study of the workspace, or another open one, has the same. */
  function label(tab) {
    var twin = tab.folder && tab.folder !== tab.title && ((idsShared[tab.title] || 0) > 1
      || tabs.some(function (t) { return t !== tab && t.title === tab.title; }));
    return twin ? tab.title + " · " + tab.folder : tab.title;
  }
  function askIdsShared() {
    var studies = window.FastMDXStudies;
    if (!studies || !studies.idsShared) return;
    studies.idsShared().then(function (times) {
      if (times && typeof times === "object") { idsShared = times; render(); }
    }).catch(function () { /* named by the open tabs alone */ });
  }
  /* Its gone words, naming it: said when another tab is being closed. */
  function goneOf(tab) {
    return tab.gone === "folder" ? "The folder of " + label(tab) + " is no longer there."
      : label(tab) + " is not among the studies here.";
  }
  function unkeptSaid(failed) {
    if (!unkept) return;
    unkept.hidden = !failed;
    unkept.textContent = failed ? "Open studies are not saved in this window: this browser does not allow it to store them." : "";
  }
  function save() {
    if (!storageKey) return;
    try {
      sessionStorage.setItem(storageKey, JSON.stringify({tabs: tabs, closed: closed, focus: focusOnOpen}));
      unkeptSaid(false);
    } catch (error) { unkeptSaid(true); }
  }
  function hydrate(app) {
    var workspace = app && (app.exploration_root || app.workspace);
    if (!workspace) return;
    var key = "fmx.studyTabs:" + workspace;
    if (key === storageKey) return;
    storageKey = key;
    tabs = []; closed = []; focusOnOpen = ""; hydrated = true;
    try {
      var stored = JSON.parse(sessionStorage.getItem(key) || "{}");
      if (Array.isArray(stored.tabs)) stored.tabs.slice(0, MAX_TABS).forEach(function (item) {
        if (!item || !validKey(item.key) || tabs.some(function (t) { return t.key === item.key; })) return;
        tabs.push({key: item.key, title: cut(item.title) || "Study", folder: cut(item.folder),
          page: safeRoute(item.page), gone: GONE_SAID[item.gone] ? item.gone : ""});
      });
      if (Array.isArray(stored.closed)) closed = stored.closed.filter(validKey).slice(-MAX_TABS);
      if (validKey(stored.focus)) focusOnOpen = stored.focus;
    } catch (error) {
      // Storage refused (not a stored list unreadable): said under the strip.
      if (!(error instanceof SyntaxError)) unkeptSaid(true);
    }
    render();
    askIdsShared();
  }
  function remember(key, title, explicit, folder, gone) {
    if (!hydrated || pending || !validKey(key)) return;
    active = key;
    if (explicit || key === explicitKey) {
      closed = closed.filter(function (k) { return k !== key; });
      if (api.state.studyClosed === key) api.studyClosed("");
    }
    explicitKey = "";
    if (closed.indexOf(key) >= 0) {
      // Its tab was closed: the study the server still has open is not named.
      api.studyClosed(key);
      active = ""; render(); return;
    }
    var tab = tabs.find(function (t) { return t.key === key; });
    if (!tab) {
      tab = {key: key, title: "Study", folder: "", page: "overview", gone: ""};
      tabs.push(tab);
      if (tabs.length > MAX_TABS) tabs.shift();
      askIdsShared();
    }
    if (gone !== undefined) tab.gone = gone ? "folder" : "";
    if (tab.gone) forgetUnsent(key);
    // A gone study's page names its folder, not its system: a name it had
    // stays, and one never named takes its folder's.
    if (!tab.gone || tab.title === "Study") {
      if (typeof title === "string" && title.trim()) tab.title = cut(title);
      if (typeof folder === "string" && folder.trim()) tab.folder = cut(folder);
    }
    if (studyPage() && !tab.gone) tab.page = pageNow();
    save(); render();
    if (focusOnOpen === key) { focusOnOpen = ""; save(); focusTab(key); }
  }
  function rememberShown(explicit) {
    var state = api.state;
    if (state.studyIsOne === false) return;
    // A run of a study of several by its study and what sets it apart.
    var of = state.appState && state.appState.run_of;
    remember(state.studyShown, document.getElementById("topbar-run-title")?.textContent, explicit,
             of ? of.study + " \u203a " + (of.label || of.run_id)
               : document.getElementById("study-folder")?.dataset.name);
  }
  function hrefFor(tab) {
    var url = new URL(location.href);
    url.searchParams.set("study", tab.key);
    url.hash = safeRoute(tab.page);
    return url.pathname + url.search + url.hash;
  }
  function message(words) { note.textContent = words || ""; note.hidden = !words; }
  /* Unsent Agent words kept for a study whose tab is closed, or whose
   * folder has gone, are not kept on for it. */
  function forgetUnsent(key) {
    if (window.FastMDXAgent && window.FastMDXAgent.forgetUnsent) window.FastMDXAgent.forgetUnsent(key);
  }
  function forget(key) {
    forgetUnsent(key);
    tabs = tabs.filter(function (t) { return t.key !== key; });
    closed = closed.filter(function (k) { return k !== key; });
    closed.push(key);
    closed = closed.slice(-MAX_TABS);
    save();
  }
  function setRoving(key) {
    roving = key || "";
    Array.prototype.forEach.call(list.querySelectorAll(".study-tab-link"), function (a) {
      a.tabIndex = a.dataset.studyKey === roving ? 0 : -1;
    });
  }
  function focusTab(key) {
    var links = Array.from(list.querySelectorAll(".study-tab-link"));
    var link = links.find(function (a) { return a.dataset.studyKey === key; }) || links[0];
    if (link) {
      setRoving(link.dataset.studyKey);
      link.focus(); link.scrollIntoView({block: "nearest", inline: "nearest"});
    } else document.getElementById("study-tabs-open").focus();
  }
  /* A tab chosen: its study opened at its last page. One marked gone is
   * asked for again, as its folder may be back. `closing` is the open
   * tab being closed and the neighbours still to try after this one. */
  async function activate(tab, closing) {
    if (pending) return;
    // Navigation announces its place on the next frame. Flush the current
    // place before pending suppresses that announcement.
    rememberShown(false);
    if (tab.key === active && !closing) { api.navigate(safeRoute(tab.page)); return; }
    pending = true; message("Opening " + label(tab) + "..."); render();
    var answer = null;
    try {
      answer = await api.openStudyTab(tab.key, safeRoute(tab.page), function () {
        if (closing) forget(closing.tab.key);
        closed = closed.filter(function (key) { return key !== tab.key; });
        focusOnOpen = tab.key;
        save();
      });
    } catch (error) {
      answer = null;
    }
    if (answer && answer.ok) return;
    pending = false;
    // Not there to open: said on its tab, which offers Close, and asked
    // for again only when chosen.
    var gone = Boolean(answer && GONE_SAID[answer.gone]);
    if (gone) { tab.gone = answer.gone; forgetUnsent(tab.key); save(); }
    if (closing && gone) {
      // Closing the open tab goes on to the next neighbour, and the note
      // names the study whose folder is gone, not the one being closed.
      closeOpen(closing.tab, closing.rest, (closing.said ? closing.said + " " : "") + goneOf(tab));
      return;
    }
    message(gone ? goneOf(tab)
      : (answer && answer.error) || "Could not open this study. The current study stays open.");
    render(); focusTab(closing ? closing.tab.key : tab.key);
  }
  function close(tab) {
    if (pending) return;
    var index = tabs.indexOf(tab);
    var next = tabs[index + 1] || tabs[index - 1];
    message("");
    if (tab.key !== active) {
      forget(tab.key); render(); focusTab(next && next.key); return;
    }
    closeOpen(tab, tabs.slice(index + 1).concat(tabs.slice(0, index).reverse()), "");
  }
  /* The open tab closed: the nearest neighbour not known to be gone is
   * opened in its place, else All studies. */
  function closeOpen(tab, others, said) {
    var at = others.findIndex(function (t) { return !t.gone; });
    if (at >= 0) {
      activate(others[at], {tab: tab, rest: others.slice(at + 1), said: said});
      if (said && pending) message(said + " Opening " + label(others[at]) + "...");
      return;
    }
    var next = others[0];
    // No study switch is needed to show All studies. The active study's
    // records remain open on the server, so a running simulation goes on;
    // it is no longer named as open here.
    forget(tab.key); api.studyClosed(tab.key); active = "";
    // Said once All studies is shown, which clears what was said before.
    saidOnArrival = said;
    render(); api.navigate("studies"); focusTab(next && next.key);
  }
  /* Whether more tabs lie past either edge of the strip: shown by a fade
   * at that edge. */
  function edges() {
    var before = list.scrollLeft > 1;
    var after = list.scrollLeft + list.clientWidth < list.scrollWidth - 1;
    host.classList.toggle("more-before", before);
    host.classList.toggle("more-after", after);
  }
  function showGone() {
    var tab = tabs.find(function (t) { return t.key === active; });
    var gone = Boolean(tab && tab.gone) && studyPage();
    document.body.classList.toggle("study-gone-shown", gone);
    if (goneNotice) goneNotice.hidden = !gone;
  }
  function render() {
    if (!host || !hydrated) return;
    showGone();
    var signature = JSON.stringify([tabs, tabs.map(label), active, pending]);
    if (signature === lastRender) return;
    lastRender = signature;
    var scroll = list.scrollLeft;
    var focused = document.activeElement;
    var focusKey = focused && list.contains(focused) && focused.dataset.studyKey;
    var focusClose = focusKey && focused.classList.contains("study-tab-close");
    var here = function (key) { return key && tabs.some(function (t) { return t.key === key; }); };
    // The strip's one keyboard stop: the tab in focus, else the open one.
    var keyStop = focusKey || (here(active) && active) || (here(roving) && roving) || (tabs[0] && tabs[0].key) || "";
    list.replaceChildren();
    tabs.forEach(function (tab) {
      var name = label(tab);
      var item = document.createElement("li");
      item.className = "study-tab";
      if (tab.key === active) item.classList.add("is-current");
      if (tab.gone) item.classList.add("is-gone");
      var link = document.createElement("a");
      link.className = "study-tab-link";
      link.dataset.studyKey = tab.key;
      link.href = hrefFor(tab);
      link.tabIndex = tab.key === keyStop ? 0 : -1;
      link.setAttribute("aria-keyshortcuts", "Delete");
      var words = document.createElement("span");
      words.className = "study-tab-name";
      words.textContent = name;
      link.appendChild(words);
      if (tab.gone) {
        var gone = document.createElement("span");
        gone.className = "study-tab-gone";
        gone.textContent = tab.gone === "folder" ? "folder gone" : "not found";
        link.append(" ", gone);
      }
      link.title = tab.gone ? name + "\n" + GONE_SAID[tab.gone] : name;
      if (tab.key === active) link.setAttribute("aria-current", "page");
      if (pending) link.setAttribute("aria-disabled", "true");
      link.onclick = function (event) {
        if (pending) { event.preventDefault(); return; }
        if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button !== 0) return;
        event.preventDefault(); activate(tab);
      };
      link.onfocus = function () { setRoving(tab.key); };
      // Reached by Delete from its tab, so the strip is one keyboard stop.
      var remove = window.FastMDXIcons.button("close", "Close " + name, "study-tab-close");
      remove.dataset.studyKey = tab.key;
      remove.tabIndex = -1;
      remove.disabled = pending;
      remove.onclick = function () { close(tab); };
      item.append(link, remove); list.appendChild(item);
    });
    roving = keyStop;
    host.hidden = false;
    host.setAttribute("aria-busy", String(pending));
    list.scrollLeft = scroll;
    if (focusKey) {
      var selector = focusClose ? ".study-tab-close" : ".study-tab-link";
      var restored = Array.from(list.querySelectorAll(selector)).find(function (el) { return el.dataset.studyKey === focusKey; });
      if (restored) restored.focus({preventScroll: true});
    }
    // The open study's tab is kept in view as it changes or first shows,
    // and as the names before it change (a study's id read late, a tab
    // found gone): the strip's own box keeps its size then, so the
    // observer on it does not see that.
    if (active !== shownActive) { shownActive = active; following = true; showCurrent(); }
    else if (following && !list.contains(document.activeElement)) showCurrent();
    if (tabsSized) {
      tabsSized.disconnect();
      Array.prototype.forEach.call(list.children, function (item) { tabsSized.observe(item); });
    }
    edges();
  }
  /* Whether the open study's tab lies wholly inside the strip's view. */
  function currentShown() {
    var current = list.querySelector(".study-tab.is-current");
    if (!current) return false;
    var box = current.getBoundingClientRect(), room = list.getBoundingClientRect();
    return box.left >= room.left - 1 && box.right <= room.right + 1;
  }
  /* The open study's tab scrolled into the strip's view, without taking
   * the keyboard to it. */
  function showCurrent() {
    var current = list.querySelector(".study-tab.is-current");
    if (!current) return;
    var box = current.getBoundingClientRect(), room = list.getBoundingClientRect();
    if (box.left < room.left) list.scrollLeft -= room.left - box.left + 8;
    else if (box.right > room.right) list.scrollLeft += box.right - room.right + 8;
  }
  function boot() {
    api = window.FastMDXDashboard;
    host = document.getElementById("study-tabs");
    list = document.getElementById("study-tabs-list");
    note = document.getElementById("study-tabs-note");
    unkept = document.getElementById("study-tabs-unkept");
    goneNotice = document.getElementById("study-gone");
    if (!api || !host || !list) return;
    if (performance.getEntriesByType?.("navigation")?.[0]?.type !== "reload") {
      explicitKey = new URL(location.href).searchParams.get("study") || "";
    }
    document.getElementById("study-tabs-open").onclick = function () { if (!pending) api.navigate("studies"); };
    var goneClose = document.getElementById("study-gone-close");
    if (goneClose) goneClose.onclick = function () {
      var tab = tabs.find(function (t) { return t.key === active; });
      if (tab) close(tab);
    };
    var goneOthers = document.getElementById("study-gone-studies");
    if (goneOthers) goneOthers.onclick = function () { api.navigate("studies"); };
    list.addEventListener("scroll", function () { following = currentShown(); edges(); }, {passive: true});
    // A plain mouse wheel moves a strip longer than its room along it.
    list.addEventListener("wheel", function (event) {
      if (event.ctrlKey || Math.abs(event.deltaY) <= Math.abs(event.deltaX)) return;
      if (list.scrollWidth <= list.clientWidth) return;
      var step = event.deltaMode === 1 ? event.deltaY * 16 : event.deltaMode === 2 ? event.deltaY * list.clientWidth : event.deltaY;
      var before = list.scrollLeft;
      list.scrollLeft += step;
      if (list.scrollLeft !== before) {
        event.preventDefault();
        // Moved away now, not at the scroll event a frame later: a redraw
        // in between (a study's id read) brought the open tab back.
        following = currentShown();
      }
    }, {passive: false});
    // The page's own sticky header and bars stay below the strip, its note
    // included, whatever its height (study-tabs.css, dashboard.css). Under
    // a bar across the top (the sidebar on a phone) the strip stays below
    // that bar, and the page's bars stay under the bar as they did.
    var bar = document.querySelector(".sidebar");
    var tall = function () {
      var under = 0;
      if (bar && getComputedStyle(bar).position === "sticky") {
        var box = bar.getBoundingClientRect();
        if (box.width >= window.innerWidth - 1) under = Math.round(box.height);
      }
      var height = host.hidden || under ? 0 : host.offsetHeight;
      document.documentElement.style.setProperty("--study-tabs-top", under + "px");
      document.documentElement.style.setProperty("--study-tabs-height", height + "px");
    };
    if (window.ResizeObserver) {
      var sized = new ResizeObserver(tall);
      sized.observe(host);
      if (bar) sized.observe(bar);
    }
    window.addEventListener("resize", tall);
    tall();
    // A narrower strip (the Agent opened beside) keeps the open tab in view,
    // unless the keyboard is moving along it.
    if (window.ResizeObserver) new ResizeObserver(function () {
      if (!list.contains(document.activeElement)) showCurrent();
      edges();
    }).observe(list);
    // A tab that changes width without the strip doing so (its name drawn
    // once the page's font has come) keeps the open tab in view too.
    if (window.ResizeObserver) {
      tabsSized = new ResizeObserver(function () {
        if (following && !list.contains(document.activeElement)) showCurrent();
        edges();
      });
      Array.prototype.forEach.call(list.children, function (item) { tabsSized.observe(item); });
    }
    list.onkeydown = function (event) {
      var key = event.target.dataset.studyKey;
      var tab = tabs.find(function (t) { return t.key === key; });
      if (!tab || pending) return;
      if (event.key === "Delete") { event.preventDefault(); close(tab); return; }
      var index = tabs.indexOf(tab), at = null;
      if (event.key === "ArrowRight") at = (index + 1) % tabs.length;
      if (event.key === "ArrowLeft") at = (index - 1 + tabs.length) % tabs.length;
      if (event.key === "Home") at = 0;
      if (event.key === "End") at = tabs.length - 1;
      if (at !== null) { event.preventDefault(); focusTab(tabs[at].key); }
    };
    api.on("app-state", function (app) {
      hydrate(app);
      if (!app.active_run && !pending) { active = ""; render(); }
    });
    api.on("study-addressed", function (study) {
      if (!study.key && !pending) { active = ""; render(); }
      else remember(study.key, study.title, false, study.folder, study.gone);
    });
    api.on("results-updated", function () { rememberShown(false); });
    api.on("study-opening", function (study) {
      rememberShown(false);
      if (validKey(study.key)) closed = closed.filter(function (key) { return key !== study.key; });
      save();
    });
    // A switch asked another way is in flight: the strip is busy and
    // asks nothing more of the server until it is answered.
    api.on("study-switching", function (on) {
      if (on && !pending) { rememberShown(false); pending = elsewhere = true; render(); }
      else if (!on && elsewhere) { pending = elsewhere = false; render(); }
    });
    // A switch held on another way to a study: said here as well.
    api.on("study-switch-held", function (reason) { if (!pending) message(reason); });
    api.on("navigate", function () {
      // A reason given before is no longer about what is shown.
      if (!pending) message(saidOnArrival);
      saidOnArrival = "";
      if (studyPage()) rememberShown(true);
      showGone();
    });
    window.addEventListener("pagehide", save);
    window.addEventListener("pageshow", function (event) {
      if (event.persisted) {
        pending = elsewhere = false; message(""); storageKey = ""; lastRender = "";
        explicitKey = new URL(location.href).searchParams.get("study") || "";
        hydrate(api.state.appState);
      }
    });
    hydrate(api.state.appState);
  }
  document.addEventListener("DOMContentLoaded", boot);
})();
