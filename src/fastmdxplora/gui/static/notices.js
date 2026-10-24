/* Notices saved: every notice the page shows, every run that ended and
 * every refusal, in a list behind the bell in the status bar (IMDE 2.8).
 *
 * The toast is a notice's first sight and goes in seconds; what it said
 * was then lost, and a run that ended while the person was elsewhere said
 * so only in the tab's title. Each is saved now on this computer
 * (localStorage `fmx.notices`, the last 200, newest first): the toast as it
 * is shown (read from `#dashboard-toast`, so every caller's notice is
 * saved without each saving it), a run's end as `run-notice.js` sees it
 * (`fmx:run-ended`), and a refusal as the Log reads it from the study's
 * events (`fmx:events`), only those written while the page is open. The
 * bell says how many are new; its list has All, Runs and Refusals and
 * warnings; a notice keeps the study it was about and, for a run's end,
 * opens that study's Overview.
 */
(function () {
  "use strict";

  var SAVED = "fmx.notices";
  var SEEN = "fmx.noticesSeen";
  var MOST = 200;
  var notices = [];
  var seenAt = 0;
  var filter = "all";
  var cleared = null;

  function el(id) { return document.getElementById(id); }
  function make(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  function newestFirst(a, b) { return b.at - a.at; }

  /* The list as saved on this computer, newest first, or null where the
   * browser saves nothing. Only a run's end saves its study's key (to open
   * it); nothing saves a folder, nor a path in its words. */
  function stored() {
    try {
      var kept = JSON.parse(localStorage.getItem(SAVED) || "[]");
      var list = Array.isArray(kept) ? kept.filter(function (n) {
        return n && typeof n.said === "string" && isFinite(n.at);
      }) : [];
      var scrubbed = false;
      list.forEach(function (n) {
        delete n.folder;
        if (n.act !== "overview") n.key = "";
        // Saved before a path was left out: left out now, and saved so.
        var said = withoutPaths(n.said);
        var id = typeof n.id === "string" ? idWithoutPaths(n.id, said) : n.id;
        if (said === n.said && id === n.id) return;
        n.said = said;
        n.id = id;
        scrubbed = true;
      });
      list = list.filter(function (n) { return n.said; });
      return { list: list.sort(newestFirst), seen: Number(localStorage.getItem(SEEN)) || 0,
               scrubbed: scrubbed };
    } catch (e) { return null; }
  }

  /* A refusal is known by its words too ("event:<study>:<time>:<line>"),
   * saved before as its Log line was, spaces and all: its line is put as
   * its words are now. */
  function idWithoutPaths(id, said) {
    if (withoutPaths(id) === id) return id;
    for (var at = id.indexOf(":"); at >= 0; at = id.indexOf(":", at + 1)) {
      var line = id.slice(at + 1).replace(/\s+/g, " ").trim();
      if (withoutPaths(line) === said) return id.slice(0, at + 1) + said;
    }
    return withoutPaths(id);
  }

  function read() {
    var kept = stored();
    if (!kept) return;
    notices = kept.list;
    seenAt = kept.seen;
    if (kept.scrubbed) write();
  }

  function write() {
    try {
      localStorage.setItem(SAVED, JSON.stringify(notices.slice(0, MOST)));
      localStorage.setItem(SEEN, String(seenAt));
    } catch (e) { /* a browser that saves nothing: this page's list still holds them */ }
  }

  function board() { return window.FastMDXDashboard; }

  /* The open study: the server's key for it and its folder's name, as the
   * sidebar names it. */
  function study() {
    var state = board() && board().state ? board().state : {};
    var named = el("study-folder");
    var folder = String(state.outputDir || "");
    return { key: String(state.studyShown || ""),
             name: (named && named.dataset.name) || folder.replace(/[\\/]+$/, "").split(/[\\/]/).pop() || "" };
  }

  /* The study a toast is about: the open one on its pages; on a page of no
   * study (All studies, Compare, New study, the Agent) none, as a toast
   * there is about another study or none. */
  var STUDY_PAGES = ["overview", "viewer", "analysis", "report", "files"];
  function studyOfThePage() {
    var page = document.documentElement.dataset.page || "overview";
    return STUDY_PAGES.indexOf(page) >= 0 ? study() : { key: "", name: "" };
  }

  /* ---- No path in what is saved ---- */
  /* A notice is saved without any path it named: a folder from the root
   * ("/Users/name/..."), from the home ("~/..."), on a drive ("C:\..."),
   * on a share ("\\server\...", "//server/...", "smb://..."), on another
   * machine ("lab:/home/...", "me@lab.edu:/home/...") or a file's
   * address, whatever showed it (the study menu's Open the folder, a
   * server's refusal, a Log line). A path
   * in quotes goes with its quotes, to the closing one or, where a line cut
   * short left it open, to the end. One not in quotes runs over a space
   * as far as the words let it be told: to the closing bracket of one in
   * brackets, over a part with a slash in it, and over a name after it
   * ("/Users/Kunle Aina", "C:\Users\Jane Doe", "/Users/jane doe" ending the
   * words), not over a word that says something of it ("is full",
   * "could not be read") nor past a file's name. A path after a colon goes
   * with the colon where nothing follows it ("Opened output folder:
   * /Users/..." is saved "Opened output folder"), and a sentence that only
   * named it goes whole ("Its path: ..."); one in the words goes with the
   * word that led to it ("Saved to /Users/....": "Saved."). Words with a
   * slash in them (ns/day, and/or), a web address and the page's own
   * addresses (/static/..., /api/...) are not paths. */
  var START = /(?:file:\/\/|(?:smb|afp|nfs|cifs|sftp|ssh):\/\/|(?<!:)\/\/(?=[^\s\/])|(?:[\w.-]+@[\w.-]+|[A-Za-z][\w-]*\.[\w.-]+):\/(?=[^\s\/])|\/(?!(?:static|api)\/)(?=[^\s\/])|~[\w.-]*[\\\/]|[A-Za-z]:[\\\/]|\\\\(?=[^\s\\]))/.source;
  var MARK = "\u0000";
  var OPENS = "\"'`\u2018\u201c";
  var CLOSES = "\"'`\u2019\u201d";
  // To its closing quote (not an apostrophe in a name: "Bob's"), or to the
  // end of the words where a line cut short left it open.
  var QUOTED = new RegExp("[" + OPENS + "]" + START + "(?:[^" + CLOSES + "\\n]|['\u2019](?=\\w))*" +
    "(?:[" + CLOSES + "]|(?=\\n)|$)", "g");
  var BEGINS = new RegExp("(^|[\\s(\\[{=,:<" + OPENS + "])" + START, "g");
  var NAMES_A_PATH = /\b(?:path|folder|file|directory|location)$/i;
  // A file's name ends the path: what follows says something of it.
  var A_FILE = /\.[A-Za-z][A-Za-z0-9]{0,5}$/;
  var ENDS = /[.,;:!?)\]}]+$/;
  // Words that say something of a path, not part of its name.
  var SAYS = new RegExp("^(?:is|are|was|were|be|been|being|has|have|had|do|does|did|not|no|could|" +
    "can|cannot|will|would|should|may|might|must|and|or|but|nor|which|that|who|where|when|while|" +
    "because|so|for|with|without|from|to|into|in|on|at|by|of|as|than|then|if|the|a|an|it|its|" +
    "exists|missing|empty|found|full|denied|failed|gone|unreadable|locked|busy|refused|opened|" +
    "closed|written|read|removed|deleted|moved|changed|already|still|now|again|here|there|" +
    "instead|too|either|neither|isn't|doesn't|wasn't|can't|won't)$", "i");

  /* Where a path that begins at `from` ends. */
  function pathEnd(text, from, bracketed) {
    var part = /[^\s"'`<>|]*/y;
    part.lastIndex = from;
    var end = from + part.exec(text)[0].length;
    var named = 0;
    var next = / ([^\s"'`<>|]+)/y;
    for (;;) {
      var sofar = text.slice(from, end);
      if (ENDS.test(sofar)) break;
      // In brackets: to the closing one, where its clause has no other end
      // and nothing in it says something of the path.
      if (bracketed) {
        var shut = /^[^.;:!?()\[\]\n]*?[)\]]/.exec(text.slice(end));
        if (shut && !shut[0].slice(0, -1).trim().split(/\s+/).some(function (w) { return SAYS.test(w); })) {
          end += shut[0].length - 1;
          break;
        }
      }
      next.lastIndex = end;
      var word = next.exec(text);
      if (!word) break;
      var bare = word[1].replace(ENDS, "");
      if (/[\\\/]/.test(bare)) { end += word[0].length; continue; }
      if (A_FILE.test(sofar) || named >= 3 || !bare || SAYS.test(bare)) break;
      var last = end + word[0].length >= text.length || ENDS.test(word[1]);
      if (!/^[A-Z]/.test(bare) && !last) break;
      end += word[0].length;
      named += 1;
    }
    // Its clause's end stays.
    var tail = ENDS.exec(text.slice(from, end));
    return tail ? end - tail[0].length : end;
  }

  function withoutPaths(said) {
    var words = String(said || "");
    var text = words.replace(QUOTED, MARK);
    var out = "";
    var at = 0;
    BEGINS.lastIndex = 0;
    for (var found = BEGINS.exec(text); found; found = BEGINS.exec(text)) {
      var from = found.index + found[1].length;
      if (from < at) continue;
      var end = pathEnd(text, from, found[1] === "(" || found[1] === "[");
      out += text.slice(at, from) + MARK;
      at = end;
      BEGINS.lastIndex = Math.max(end, BEGINS.lastIndex);
    }
    text = out + text.slice(at);
    // No path: the words as they were.
    if (text === words) return words;
    // Paths in a list, one.
    for (var listed = ""; listed !== text;) {
      listed = text;
      text = text.replace(/\u0000(?:,? and |, | or )\u0000/g, MARK);
    }
    return text
      // A quote left with nothing in it.
      .replace(new RegExp("[" + OPENS + "]\u0000[" + CLOSES + "]?", "g"), MARK)
      // A sentence that did no more than name it.
      .replace(/([.!?]) +([^.!?\u0000]*?) *: *\u0000(?= *(?:[.!?]|$))/g, function (match, stop, label) {
        return NAMES_A_PATH.test(label.trim()) ? stop : match;
      })
      .replace(/ *: *\u0000(?= *(?:[.;!?,()\]]|$))/g, "")
      // A machine's name before its path ("lab:/home/..."): the colon goes.
      .replace(/(\w):\u0000/g, "$1")
      .replace(/\s+(?:in|at|to|from|into|under|on|of|as)\s+\u0000/gi, "")
      .replace(/\u0000/g, "")
      .replace(/\s+/g, " ")
      .replace(/\(\s*(?:see|in|at|from)?\s*\)|\[\s*\]|\{\s*\}|<\s*>|``/gi, "")
      .replace(/(`[^`]*?) +`/g, "$1`")
      .replace(/ +([.,;:!?)\]])/g, "$1")
      .replace(/([(\[]) +/g, "$1")
      .replace(/:(?=[.,;!?])/g, "")
      .replace(/,(?=[.;!?])/g, "")
      .replace(/(^|[^.])\.\.(?!\.)/g, "$1.")
      .replace(/ {2,}/g, " ")
      .trim()
      .replace(/[,;:]+$/, "");
  }

  /* A study opened elsewhere is one showing in every window that hears of
   * it, whenever each hears and whichever switches each missed (a window
   * in the background hears late, and may hear two switches as one): known
   * by the study it opened. It is saved already where an opening of that
   * study was saved since the last switch this window heard or saved, or
   * since this page was loaded (a study opened from it loads it again). The
   * server gives a switch no identity of its own, so a switch no window
   * heard on its own (two within one read of every window) is not saved. */
  var heardAt = Date.now();
  function opened(id) { return String(id || "").split(">")[1] || ""; }
  function sameSwitch(id) {
    var seen = null;
    notices.some(function (n) {
      if (n.at <= heardAt) return true;
      if (n.id && n.id.indexOf("switch:") === 0 && opened(n.id) === opened(id)) seen = n;
      return Boolean(seen);
    });
    if (seen) heardAt = seen.at;
    return Boolean(seen);
  }

  /* A notice saved, unless it is saved already: one with the same identity
   * (a run's end, a refusal of the Log, a study opened elsewhere, as
   * another window of the GUI saved it), or the same words of the same kind
   * about the same study within two seconds (a toast shown again as a page
   * is drawn again). The list is read from this computer first, where
   * another window may have saved it. Saved is what was said with no path
   * in it, the study's name and, only for what opens it (a run's end), the
   * study's key: nothing of the study the notice did not show. */
  function add(kind, said, about, act, id) {
    said = withoutPaths(String(said || "").replace(/\s+/g, " ").trim());
    if (!said) return;
    read();
    var now = Date.now();
    var where = about || studyOfThePage();
    var name = where.name || "";
    var already = id && id.indexOf("switch:") === 0 ? sameSwitch(id) : notices.some(function (n) {
      if (id) return n.id === id && Math.abs(now - n.at) < 300000;
      return n.kind === kind && n.said === said && (n.name || "") === name && Math.abs(now - n.at) < 2000;
    });
    if (already) return;
    var notice = { at: now, kind: kind, said: said, name: name, act: act || "",
                   key: act === "overview" ? where.key || "" : "" };
    if (id) notice.id = id;
    notices.unshift(notice);
    if (id && id.indexOf("switch:") === 0) heardAt = now;
    if (notices.length > MOST) notices.length = MOST;
    write();
    drawBell();
    if (!panel().hidden) drawAgain();
  }

  /* ---- What is saved ---- */
  // Every toast, as it is shown.
  function watchToast() {
    var watched = null;
    var lastWords = null;
    function readToast() {
      var toast = el("dashboard-toast");
      var said = toast && toast.querySelector(".dashboard-toast-said");
      if (!said || !toast.classList.contains("show")) return;
      // Its words are set anew each time it is shown; anything else that
      // changes in it (its Undo going) is not a notice.
      if (said.firstChild === lastWords) return;
      lastWords = said.firstChild;
      var kind = toast.getAttribute("data-kind") || "ok";
      add(kind === "warning" || kind === "error" ? "warning" : "ok", said.textContent, null, "",
          switchSaid(said.textContent));
    }
    var reading = new MutationObserver(readToast);
    function attach() {
      var toast = el("dashboard-toast");
      if (!toast || toast === watched) return;
      watched = toast;
      reading.observe(toast, { childList: true, subtree: true, characterData: true,
                               attributes: true, attributeFilter: ["class"] });
      // Made for its first notice, already shown.
      readToast();
    }
    new MutationObserver(attach).observe(document.body, { childList: true });
    attach();
  }

  /* A study opened elsewhere (dashboard.js says it once the page has read
   * the study's name, the latest only and while it is shown): its identity
   * is the two studies the page gave it, the one left and the one shown. */
  var switched = null;
  window.addEventListener("dashboard:study-changed-elsewhere", function (event) {
    var d = event.detail || {};
    switched = d.from && d.to ? { from: String(d.from), to: String(d.to) } : null;
  });
  function switchSaid(words) {
    if (!switched || !/ was opened elsewhere, /.test(words) || switched.to !== study().key) return undefined;
    var id = "switch:" + switched.from + ">" + switched.to;
    switched = null;
    return id;
  }

  // A run that ended, as run-notice.js sees it: how it ended, as the
  // browser's notice says it; the study by the server's key for it, named
  // by its folder.
  window.addEventListener("fmx:run-ended", function (event) {
    var d = event.detail || {};
    var ended = { failed: "failed", stopped: "stopped", interrupted: "was interrupted" }[d.run] || "completed";
    var said = (d.name || "The run") + " " + ended + (d.stage ? ": " + d.stage : ".");
    var here = study();
    var about = { key: d.study || "", name: d.folder || (d.study && d.study === here.key ? here.name : "") };
    add(d.run === "failed" || d.run === "interrupted" ? "refused" : "ended", said, about, "overview",
        "run:" + (d.study || "") + ":" + (d.started || "") + ":" + ended);
  });

  /* A refusal as a notice says it: its first line with no path in it,
   * then at most 200 characters (a path cut first would leave what was cut
   * of it); what follows (a traceback, the paths in it) stays in the Log. */
  function firstLine(text) {
    var line = withoutPaths(String(text || "").split(/\r?\n/)[0].trim());
    return line.length > 200 ? line.slice(0, 199) + "\u2026" : line;
  }

  // A refusal the Log reads, written while the page is open: the events a
  // study had when it was first read are its past, the Log's to show.
  var since = {};
  window.addEventListener("fmx:events", function (event) {
    var d = event.detail || {};
    var where = study();
    var events = Array.isArray(d.events) ? d.events : [];
    var times = events.map(function (e) { return Date.parse(e.timestamp || ""); }).filter(isFinite);
    var newest = times.length ? Math.max.apply(null, times) : 0;
    if (!(where.key in since)) { since[where.key] = newest; return; }
    var from = since[where.key];
    events.forEach(function (e) {
      var at = Date.parse(e.timestamp || "");
      if (!isFinite(at) || at <= from || typeof d.refused !== "function" || !d.refused(e)) return;
      // Its words with no path, in what it is saved as and known by.
      var line = firstLine(e.message);
      add("refused", line, where, "", "event:" + where.key + ":" + e.timestamp + ":" + line);
    });
    since[where.key] = Math.max(from, newest);
  });

  /* ---- The bell and its list ---- */
  function unread() {
    return notices.filter(function (n) { return n.at > seenAt; }).length;
  }

  function drawBell() {
    var bell = el("sb-notices");
    if (!bell) return;
    var n = unread();
    var count = bell.querySelector(".sb-count");
    count.textContent = n > 99 ? "99+" : String(n);
    count.hidden = !n;
    bell.setAttribute("aria-label", "Notices" + (n ? ", " + n + " new" : ""));
    bell.title = "Every notice, refusal and run that ended" + (n ? ": " + n + " new" : "");
  }

  function panel() {
    var box = el("notices");
    if (box) return box;
    box = make("div", "notices");
    box.id = "notices";
    box.hidden = true;
    box.setAttribute("role", "dialog");
    box.setAttribute("aria-label", "Notices");
    // Keys pressed in the list stay in it: Escape closes the list alone,
    // and a page's own keys (All studies' c, j, k, t, x) do nothing under
    // it. Tab goes round its buttons and a notice's Undo beside it: the
    // keyboard stays with the open list until Escape or a choice closes it.
    box.addEventListener("keydown", function (event) {
      if (event.ctrlKey || event.metaKey || event.altKey) return;
      event.stopPropagation();
      if (event.key === "Escape") { event.preventDefault(); close(true); return; }
      if (event.key === "Tab") goRound(event);
    });
    document.body.appendChild(box);
    return box;
  }

  /* What Tab goes round while the list is open: its buttons, then the
   * action (Undo) of a notice shown beside it, while that notice shows. */
  function stops() {
    function offered(b) { return !b.disabled && b.offsetParent !== null; }
    var inList = Array.prototype.filter.call(panel().querySelectorAll("button"), offered);
    var toast = el("dashboard-toast");
    var beside = toast && toast.classList.contains("show") ?
      Array.prototype.filter.call(toast.querySelectorAll(".dashboard-toast-action"), offered) : [];
    return inList.concat(beside);
  }

  function goRound(event) {
    event.preventDefault();
    var all = stops();
    if (!all.length) return;
    var at = all.indexOf(document.activeElement);
    var last = all.length - 1;
    all[event.shiftKey ? (at <= 0 ? last : at - 1) : (at < 0 || at === last ? 0 : at + 1)].focus();
  }

  // On that notice's action, Tab goes on round the list, and Escape
  // closes the list as in it.
  document.addEventListener("keydown", function (event) {
    var box = el("notices");
    if (!box || box.hidden || event.ctrlKey || event.metaKey || event.altKey) return;
    if (!event.target.closest || !event.target.closest("#dashboard-toast")) return;
    if (event.key !== "Tab" && event.key !== "Escape") return;
    event.stopPropagation();
    if (event.key === "Escape") { event.preventDefault(); close(true); return; }
    goRound(event);
  }, true);

  var KINDS = { all: "All", runs: "Runs", refused: "Refusals and warnings" };
  function shownNow() {
    return notices.filter(function (n) {
      return filter === "all" || (filter === "runs" ? n.act === "overview"
        : n.kind === "refused" || n.kind === "warning");
    });
  }

  function dayOf(at) {
    var d = new Date(at);
    var today = new Date();
    var yesterday = new Date(today.getTime() - 86400000);
    if (d.toDateString() === today.toDateString()) return "Today";
    if (d.toDateString() === yesterday.toDateString()) return "Yesterday";
    return d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
  }

  function timeOf(at) {
    var d = new Date(at);
    return String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0");
  }

  function drawList() {
    var box = panel();
    box.replaceChildren();
    var head = make("div", "notices-head");
    head.appendChild(make("b", "", "Notices"));
    head.appendChild(make("span", "notices-n", notices.length + " saved"));
    box.appendChild(head);
    var chips = make("div", "notices-chips");
    chips.setAttribute("role", "group");
    chips.setAttribute("aria-label", "Show");
    Object.keys(KINDS).forEach(function (k) {
      var chip = make("button", "notices-chip", KINDS[k]);
      chip.type = "button";
      chip.dataset.show = k;
      chip.setAttribute("aria-pressed", String(filter === k));
      chip.addEventListener("click", function () { filter = k; drawList(); refocus("[data-show='" + k + "']"); });
      chips.appendChild(chip);
    });
    box.appendChild(chips);
    var list = make("div", "notices-list");
    var shown = shownNow();
    var day = "";
    shown.forEach(function (n) {
      var on = dayOf(n.at);
      if (on !== day) { day = on; list.appendChild(make("div", "notices-day", on)); }
      var row = make("div", "notice" + (n.at > seenAt ? " is-new" : ""));
      row.dataset.kind = n.kind;
      row.appendChild(make("span", "notice-dot"));
      row.appendChild(make("span", "notice-said", n.said));
      row.appendChild(make("span", "notice-when", timeOf(n.at)));
      var about = make("span", "notice-about");
      if (n.name) about.appendChild(make("span", "", n.name));
      // Another study opens by a POST, refused to a page bound beyond
      // this computer: not offered there.
      if (n.act === "overview" && n.key && (n.key === study().key || !beyondLoopback())) {
        var go = make("button", "notice-act", "Open its Overview");
        go.type = "button";
        go.addEventListener("click", function () { openOverview(n.key); });
        about.appendChild(go);
      }
      if (about.childNodes.length) row.appendChild(about);
      list.appendChild(row);
    });
    if (!shown.length) {
      list.appendChild(make("p", "notices-none", notices.length ? "None of these yet." :
        "Nothing yet. What the page tells you, each run that ends and each refusal are saved here."));
    }
    box.appendChild(list);
    var foot = make("div", "notices-foot");
    if (cleared) {
      foot.appendChild(make("span", "", "Cleared " + cleared.length + "."));
      var undo = make("button", "notices-undo", "Undo");
      undo.type = "button";
      undo.addEventListener("click", function () {
        // Newest first, with any saved since the list was cleared.
        notices = cleared.concat(notices).sort(newestFirst).slice(0, MOST);
        cleared = null;
        write();
        drawBell();
        drawList();
        refocus(".notices-clear");
      });
      foot.appendChild(undo);
    } else {
      foot.appendChild(make("span", "", "Saved on this computer, the last " + MOST));
      var clear = make("button", "notices-clear", "Clear the list");
      clear.type = "button";
      clear.disabled = !notices.length;
      clear.addEventListener("click", function () {
        cleared = notices;
        notices = [];
        write();
        drawBell();
        drawList();
        refocus(".notices-undo");
      });
      foot.appendChild(clear);
    }
    box.appendChild(foot);
  }

  /* The open list drawn again for a notice saved meanwhile, here or in
   * another window: the keyboard stays where it was in it (a notice's
   * Open its Overview, moved by the new one, gives it to the chip shown). */
  function drawAgain() {
    var on = document.activeElement;
    if (!panel().contains(on)) { drawList(); return; }
    var named = /\bnotices-(?:clear|undo)\b/.exec(on.className);
    drawList();
    refocus(on.dataset.show ? "[data-show='" + on.dataset.show + "']" :
      named ? "." + named[0] : ".notices-chip[aria-pressed='true']");
  }

  function refocus(selector) {
    var node = panel().querySelector(selector);
    if (node) node.focus();
  }

  function beyondLoopback() { return document.body.dataset.beyondLoopback === "true"; }

  /* Another study opens by its key as the strip's tabs open one
   * (dashboard.js): asked first whether the switch would lose work here,
   * one switch at a time; why it did not open is said. */
  function openOverview(key) {
    var dashboard = board();
    if (!dashboard) return;
    close(false);
    if (study().key === key) { dashboard.navigate("overview"); return; }
    if (!dashboard.openStudyTab) return;
    dashboard.openStudyTab(key, "overview").then(function (answer) {
      if (answer && !answer.ok && dashboard.toast) {
        dashboard.toast(answer.error || "The study could not be opened.", "warning");
      }
    });
  }

  function open() {
    var box = panel();
    cleared = null;
    drawList();
    box.hidden = false;
    var bell = el("sb-notices");
    if (bell) bell.setAttribute("aria-expanded", "true");
    // Read once seen: the bell's count goes, the new ones stay marked in
    // the list while it is open.
    seenAt = notices.reduce(function (most, n) { return Math.max(most, n.at); }, 0) || Date.now();
    write();
    drawBell();
    var first = box.querySelector(".notices-chip[aria-pressed='true']");
    if (first) first.focus();
  }

  function close(back) {
    var box = el("notices");
    if (!box || box.hidden) return;
    box.hidden = true;
    var bell = el("sb-notices");
    if (bell) {
      bell.setAttribute("aria-expanded", "false");
      if (back) bell.focus();
    }
  }

  function attach() {
    read();
    var bell = el("sb-notices");
    if (bell) {
      bell.hidden = false;
      bell.addEventListener("click", function () {
        if (panel().hidden) open(); else close(false);
      });
    }
    document.addEventListener("click", function (event) {
      var box = el("notices");
      // The action of a notice beside the open list, done: the keyboard
      // back in the list.
      if (box && !box.hidden && event.target.classList && event.target.classList.contains("dashboard-toast-action")) {
        refocus(".notices-chip[aria-pressed='true']");
        return;
      }
      // A click in the list that drew it again took its target out of the
      // page: still in the list.
      if (!event.target.isConnected) return;
      if (box && !box.hidden && !box.contains(event.target) && !event.target.closest("#sb-notices")) close(false);
    });
    // Another window of the GUI saved one.
    window.addEventListener("storage", function (event) {
      if (event.key !== SAVED && event.key !== SEEN) return;
      read();
      drawBell();
      if (!panel().hidden) drawAgain();
    });
    watchToast();
    drawBell();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", attach);
  else attach();

  window.FastMDXNotices = { add: add, open: open, close: close, get all() { return notices.slice(); } };
}());
