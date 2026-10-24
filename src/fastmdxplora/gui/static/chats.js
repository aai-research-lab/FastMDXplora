/* The Agent's conversations in the sidebar, in one list (IMDE 2.10):
 * the open study's first, then the chats of no study.
 *
 * A conversation belongs to the study it is about and is saved in its
 * folder; a chat begun with no study, or begun here as one, is saved in
 * the workspace and stays a chat while a study is open, until it starts a
 * study of its own. They were two lists, Conversations under the study's
 * pages and Chats after them; one now, each part named where both have
 * some, the ones talked in last first, the one open in the Agent marked; a
 * click opens it in the Agent beside the page, the head's + begins one
 * about the open study (a chat where none is open), New chat begins a
 * chat of no study while one is, and All lists them all in the Agent's
 * Conversations.
 */
(function () {
  "use strict";

  var SHOWN = 6;
  var asking = null;

  function el(id) { return document.getElementById(id); }

  function make(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  function panel() { return window.FastMDXAgentPanel; }

  function ago(when) {
    var t = new Date(when).getTime();
    if (isNaN(t)) return "";
    var minutes = Math.round((Date.now() - t) / 60000);
    if (minutes < 1) return "now";
    if (minutes < 60) return minutes + " min";
    if (minutes < 24 * 60) return Math.round(minutes / 60) + " h";
    if (minutes < 7 * 24 * 60) return Math.round(minutes / 1440) + " d";
    return new Date(t).toLocaleDateString(undefined, { month: "short", day: "numeric" });
  }

  /* The Agent beside the page, where it is not on its own page. */
  function besideThePage() {
    if (document.documentElement.getAttribute("data-page") === "agent") return;
    var beside = window.FastMDXAgentBeside;
    if (beside && !beside.isOpen()) beside.open(true);
  }

  function open(c, group) {
    var agent = panel();
    if (!agent) return;
    besideThePage();
    agent.open(c.id, group.study, group.loaded || group.chats);
  }

  function row(c, group, now) {
    var item = make("button", "sidebar-conv");
    item.type = "button";
    item.dataset.id = c.id;
    item.dataset.group = group.chats ? "chats" : "study";
    var here = now && now.id === c.id && (now.study || null) === (c.study || null);
    if (here) item.setAttribute("aria-current", "true");
    item.title = c.title + "\n" + c.entries + (c.entries === 1 ? " message" : " messages");
    item.append(make("span", "sidebar-conv-title", c.title),
                make("span", "sidebar-conv-when", ago(c.updated)));
    item.addEventListener("click", function () { open(c, group); });
    return item;
  }

  /* One list: the open study's, then the chats; each part named where
   * both have some. */
  function rows(host, study, chats) {
    var now = panel() ? panel().current : {};
    var mine = study ? study.conversations : [];
    var others = chats ? chats.conversations : [];
    host.replaceChildren();
    // Beside the folded strip the head's + is not shown: the list begins
    // with it (hidden in the sidebar shown, by the CSS).
    var fresh = make("button", "sidebar-conv-new", study ? "New conversation" : "New chat");
    fresh.type = "button";
    fresh.addEventListener("click", function () { begin(openStudy()); });
    host.appendChild(fresh);
    // With a study open the head's + is about it: a chat of no study is
    // begun here, in the sidebar shown and beside the folded strip alike.
    if (openStudy()) {
      var chat = make("button", "sidebar-conv-new sidebar-conv-chat", "New chat, about no study");
      chat.type = "button";
      chat.addEventListener("click", function () { begin(null); });
      host.appendChild(chat);
    }
    var both = mine.length && others.length;
    var each = both ? Math.ceil(SHOWN / 2) + 1 : SHOWN + 1;
    if (mine.length) {
      if (both) host.appendChild(make("div", "sidebar-conv-part", "This study"));
      mine.slice(0, each).forEach(function (c) { host.appendChild(row(c, study, now)); });
    }
    if (others.length) {
      if (both) host.appendChild(make("div", "sidebar-conv-part", "Not about a study"));
      others.slice(0, each).forEach(function (c) { host.appendChild(row(c, chats, now)); });
    }
    var all = mine.length + others.length;
    if (all > Math.min(mine.length, each) + Math.min(others.length, each)) {
      var more = make("button", "sidebar-conv sidebar-conv-more", "All " + all + "…");
      more.type = "button";
      more.addEventListener("click", function () {
        besideThePage();
        if (panel()) panel().list();
      });
      host.appendChild(more);
    }
  }

  function openStudy() {
    var board = window.FastMDXDashboard;
    return (board && board.state && board.state.appState ? board.state.appState.active_run : "") || null;
  }

  function show() {
    if (asking) return asking;
    asking = fetch("/api/agent/conversations", { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var groups = (d && d.groups) || [];
        var study = groups.find(function (g) { return g.loaded && !g.chats; }) || null;
        var chats = groups.find(function (g) { return g.chats; }) || { chats: true, conversations: [] };
        rows(el("sidebar-chats-list"), study, chats);
        var add = el("sidebar-chat-new");
        if (add) {
          var words = study ? "A new conversation about this study" : "A new chat, about no study";
          add.title = words;
          add.setAttribute("aria-label", words);
        }
      })
      .catch(function () { /* the lists stay as they were */ })
      .then(function () { asking = null; });
    return asking;
  }

  function begin(study) {
    var agent = panel();
    if (!agent) return;
    besideThePage();
    agent.fresh(study).then(function () {
      var box = el("agent-request");
      if (box) box.focus();
    });
  }

  function wire() {
    var chat = el("sidebar-chat-new");
    if (chat) chat.addEventListener("click", function (event) {
      // In the list's head: begins one and does not fold the list.
      event.preventDefault();
      event.stopPropagation();
      begin(openStudy());
    });
    var fold = el("sidebar-chats");
    if (fold) {
      try { if (localStorage.getItem("fmx.chatsOpen") === "0") fold.open = false; } catch (e) {}
      fold.addEventListener("toggle", function () {
        try { localStorage.setItem("fmx.chatsOpen", fold.open ? "1" : "0"); } catch (e) {}
      });
    }
    window.addEventListener("fmx:conversations", show);
    window.addEventListener("dashboard:run-changed", show);
    setTimeout(show, 1200);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", wire);
  else wire();

  window.FastMDXChats = { show: show };
}());
