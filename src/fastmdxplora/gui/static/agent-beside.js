/* The Agent beside whatever page is open.
 *
 * The Agent was a page of its own, so asking it about the RMSD on the
 * Analysis page took the Analysis page away. With ⌘J (Ctrl+J), or the
 * button beside Agent in the sidebar, its conversation opens in the side
 * panel's place, the page left as it was. It is the Agent page's own
 * conversation, moved here and back, so a thread begun on the page goes
 * on beside another and the reverse; nothing is drawn twice.
 */
(function () {
  "use strict";

  function el(id) { return document.getElementById(id); }

  var body, drawer, slot, home, after = null, lastPage = "overview", placeholder = "";
  /* The page's prompt runs to two lines in the narrower column. */
  var BESIDE_PROMPT = "Ask about this study, or describe one.";

  function onTheAgentPage() {
    return document.documentElement.getAttribute("data-page") === "agent";
  }

  function isOpen() { return !!drawer && !drawer.hidden; }

  function open() {
    if (!body || !drawer) return;
    if (onTheAgentPage() && window.FastMDXDashboard) {
      // Beside a page, not beside itself: back to the page before it.
      window.FastMDXDashboard.navigate(lastPage);
    }
    slot.appendChild(body);
    drawer.hidden = false;
    document.body.classList.add("agent-beside");
    var box = el("agent-request");
    if (box) {
      placeholder = box.placeholder;
      box.placeholder = BESIDE_PROMPT;
      box.focus();
    }
  }

  /* A dialog over the page has the keyboard: the drawer opened behind
   * Preferences took its focus. */
  function aDialogIsOpen() {
    return !!document.querySelector(".agent-dialog:not([hidden])");
  }

  function close() {
    if (!body || !drawer || drawer.hidden) return;
    // Focus in the drawer goes back to where it was opened from, not to
    // nothing as the drawer goes.
    var hadFocus = drawer.contains(document.activeElement);
    home.insertBefore(body, after);
    drawer.hidden = true;
    document.body.classList.remove("agent-beside");
    var box = el("agent-request");
    if (box && placeholder) box.placeholder = placeholder;
    if (hadFocus) {
      var back = el("agent-beside-open");
      if (back && back.offsetParent !== null) back.focus();
      else document.querySelector(".main")?.focus?.();
    }
  }

  function toggle() {
    if (isOpen()) close();
    else open();
  }

  document.addEventListener("DOMContentLoaded", function () {
    body = el("agent-body");
    drawer = el("agent-drawer");
    slot = el("agent-drawer-body");
    home = body ? body.parentNode : null;
    after = body ? body.nextSibling : null;
    if (!body || !drawer || !slot || !home) return;

    var mac = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent || "");
    var keys = mac ? "⌘J" : "Ctrl J";
    var said = el("agent-drawer-keys");
    if (said) said.textContent = keys;
    var button = el("agent-beside-open");
    if (button) {
      button.title = "The Agent beside this page (" + keys + ")";
      button.addEventListener("click", function (e) {
        e.preventDefault();
        toggle();
      });
    }
    el("agent-drawer-close").addEventListener("click", close);
    el("agent-drawer-page").addEventListener("click", function () {
      close();
      if (window.FastMDXDashboard) window.FastMDXDashboard.navigate("agent");
    });

    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && isOpen() && drawer.contains(document.activeElement)
          && !aDialogIsOpen()) {
        close();
        return;
      }
      if (!(e.metaKey || e.ctrlKey) || e.altKey || e.shiftKey) return;
      if (String(e.key).toLowerCase() !== "j") return;
      e.preventDefault();
      if (e.repeat || aDialogIsOpen()) return;
      toggle();
    });

    // The Agent page opened: the conversation goes home to it.
    window.addEventListener("dashboard:navigate", function (event) {
      var page = event.detail && event.detail.page;
      if (page === "agent") close();
      else if (page) lastPage = page;
    });
  });

  window.FastMDXAgentBeside = { open: open, close: close, toggle: toggle, isOpen: isOpen };
})();
