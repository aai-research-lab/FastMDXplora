/* The Agent's conversations in the sidebar: the open study's, under its
 * pages, and the chats of no study after them.
 *
 * A conversation belongs to the study it is about and is kept in its
 * folder; a chat begun with no study, or begun here as one, is kept in the
 * workspace and stays a chat while a study is open, until it starts a
 * study of its own. Each list gives the ones talked in last, the one open
 * in the Agent marked; a click opens it in the Agent beside the page, a +
 * begins another, and More lists them all in the Agent's Conversations.
 */
(function () {
  "use strict";

  var SHOWN = 5;
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

  function rows(host, group, shown) {
    var now = panel() ? panel().current : {};
    var all = group ? group.conversations : [];
    host.replaceChildren();
    // None yet: the head and its + say enough, and the sidebar keeps its
    // height for the pages.
    if (!all.length) return;
    all.slice(0, shown).forEach(function (c) {
      var item = make("button", "sidebar-conv");
      item.type = "button";
      item.dataset.id = c.id;
      var here = now && now.id === c.id && (now.study || null) === (c.study || null);
      if (here) item.setAttribute("aria-current", "true");
      item.title = c.title + "\n" + c.entries + (c.entries === 1 ? " message" : " messages");
      item.append(make("span", "sidebar-conv-title", c.title),
                  make("span", "sidebar-conv-when", ago(c.updated)));
      item.addEventListener("click", function () { open(c, group); });
      host.appendChild(item);
    });
    if (all.length > shown) {
      var more = make("button", "sidebar-conv sidebar-conv-more",
        "All " + all.length + (group.chats ? " chats" : " conversations"));
      more.type = "button";
      more.addEventListener("click", function () {
        besideThePage();
        if (panel()) panel().list();
      });
      host.appendChild(more);
    }
  }

  function show() {
    if (asking) return asking;
    asking = fetch("/api/agent/conversations", { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var groups = (d && d.groups) || [];
        var study = groups.find(function (g) { return g.loaded && !g.chats; }) || null;
        var chats = groups.find(function (g) { return g.chats; }) || { chats: true, conversations: [] };
        var block = el("sidebar-study-convs");
        if (block) {
          block.hidden = !study;
          if (study) rows(el("sidebar-study-convs-list"), study, SHOWN);
        }
        rows(el("sidebar-chats-list"), chats, SHOWN);
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
    var forStudy = el("sidebar-study-new");
    if (forStudy) forStudy.addEventListener("click", function () {
      var board = window.FastMDXDashboard;
      var open = board && board.state && board.state.appState ? board.state.appState.active_run : "";
      begin(open || null);
    });
    var chat = el("sidebar-chat-new");
    if (chat) chat.addEventListener("click", function (event) {
      // In the Chats head: begins a chat and does not fold the list.
      event.preventDefault();
      event.stopPropagation();
      begin(null);
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
