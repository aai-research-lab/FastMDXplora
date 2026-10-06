/* The page's tooltips, in the page's own style.
 *
 * Every hint in the GUI is an element's `title`, which the browser shows in
 * its own box: square, in the system's colours, whatever the scheme. Here,
 * while the pointer is on such an element (or the keyboard has brought
 * focus to it), its title is taken off, so the browser shows nothing, and
 * shown in a rounded box in the scheme's colours, named as the element's
 * description for a screen reader; it goes back on as the pointer or focus
 * leaves. A title the page changes meanwhile is shown as changed. Nothing
 * on a touch screen, which has no hover.
 */
(function () {
  "use strict";

  var DELAY_MS = 450;
  /* Moving from one hint to the next shows the next at once, as the
   * browser's own do. */
  var WARM_MS = 300;
  var GAP = 8;
  var EDGE = 8;

  var tip = null;
  var target = null;
  var title = "";
  var timer = 0;
  var hiddenAt = 0;
  var watcher = null;
  /* What brought the hint up, the pointer or the keyboard: only what
   * brought it puts it away by leaving. */
  var byKeyboard = false;
  /* While a hint is up, whether its element is still in the page: one
   * built again under the pointer takes its hint with it. */
  var checking = 0;

  function box() {
    if (tip) return tip;
    tip = document.createElement("div");
    tip.className = "fmx-tooltip";
    tip.id = "fmx-tooltip";
    tip.setAttribute("role", "tooltip");
    tip.hidden = true;
    document.body.appendChild(tip);
    return tip;
  }

  function place() {
    if (!target || !tip || tip.hidden) return;
    var at = target.getBoundingClientRect();
    var size = tip.getBoundingClientRect();
    var width = document.documentElement.clientWidth;
    var height = document.documentElement.clientHeight;
    // Below the element, or above it where there is no room below.
    var top = at.bottom + GAP;
    if (top + size.height > height - EDGE && at.top - GAP - size.height >= EDGE) {
      top = at.top - GAP - size.height;
    }
    var left = at.left + at.width / 2 - size.width / 2;
    left = Math.max(EDGE, Math.min(left, width - EDGE - size.width));
    tip.style.left = Math.round(left) + "px";
    tip.style.top = Math.round(Math.max(EDGE, top)) + "px";
  }

  /* Shown in the element the browser has made fullscreen, if any (the
   * viewer's), since nothing outside it is shown. */
  function show() {
    if (!target || !title) return;
    var shown = box();
    var host = document.fullscreenElement || document.body;
    if (shown.parentNode !== host) host.appendChild(shown);
    shown.textContent = title;
    shown.hidden = false;
    place();
    var described = (target.getAttribute("aria-describedby") || "").split(/\s+/);
    if (described.indexOf(shown.id) < 0) {
      target.setAttribute("aria-describedby", described.concat(shown.id).join(" ").trim());
      target.dataset.tipDescribed = "1";
    }
  }

  /* The title back where it was, unless the page has given the element
   * another meanwhile. */
  function release() {
    clearTimeout(timer);
    clearInterval(checking);
    if (watcher) { watcher.disconnect(); watcher = null; }
    if (target) {
      if (!target.hasAttribute("title") && title) target.setAttribute("title", title);
      if (target.dataset.tipDescribed) {
        var left = (target.getAttribute("aria-describedby") || "").split(/\s+/)
          .filter(function (id) { return id && id !== "fmx-tooltip"; });
        if (left.length) target.setAttribute("aria-describedby", left.join(" "));
        else target.removeAttribute("aria-describedby");
        delete target.dataset.tipDescribed;
      }
    }
    if (tip && !tip.hidden) { tip.hidden = true; hiddenAt = Date.now(); }
    target = null;
    title = "";
    byKeyboard = false;
  }

  function take(element, once) {
    if (element === target) return;
    release();
    target = element;
    byKeyboard = once;
    title = element.getAttribute("title") || "";
    element.removeAttribute("title");
    // The page may set the title while it is off: the new one is shown.
    if (window.MutationObserver) {
      watcher = new MutationObserver(function () {
        if (!target || !target.hasAttribute("title")) return;
        title = target.getAttribute("title") || "";
        target.removeAttribute("title");
        if (!tip || tip.hidden) return;
        if (title) { tip.textContent = title; place(); } else { tip.hidden = true; }
      });
      watcher.observe(element, { attributes: true, attributeFilter: ["title"] });
    }
    checking = setInterval(function () {
      if (target && !target.isConnected) release();
    }, 400);
    var warm = Date.now() - hiddenAt < WARM_MS;
    if (once || warm) show();
    else timer = setTimeout(show, DELAY_MS);
  }

  function hinted(node) {
    var found = node && node.closest ? node.closest("[title]") : null;
    // A select's options are the system's own; the document's own title
    // is not a hint.
    if (!found || found.closest("select, option, svg title") || found === document.documentElement) {
      return null;
    }
    return found.getAttribute("title") ? found : null;
  }

  document.addEventListener("pointerover", function (event) {
    if (event.pointerType === "touch") return;
    if (target && !target.isConnected) release();
    // Within the element whose hint is up, its own hint stays, unless the
    // pointer is on an element inside it with a hint of its own. Its title
    // is off, so the search for one stops at it, or an element around it
    // would be found.
    if (target && target.contains(event.target)) {
      var inner = event.target.closest("[title]");
      if (inner && inner !== target && target.contains(inner) && inner.getAttribute("title")) {
        take(inner, false);
      }
      return;
    }
    var found = hinted(event.target);
    if (found) take(found, false);
    else if (target && !byKeyboard) release();
  }, true);

  document.addEventListener("pointerout", function (event) {
    if (!target || byKeyboard || !target.contains(event.target)) return;
    var to = event.relatedTarget;
    if (!to || !target.contains(to)) release();
  }, true);

  document.addEventListener("focusin", function (event) {
    var found = hinted(event.target);
    if (!found || found !== event.target) return;
    var keyboard = true;
    try { keyboard = found.matches(":focus-visible"); } catch (e) { keyboard = true; }
    if (keyboard) take(found, true);
  });

  document.addEventListener("focusout", function (event) {
    if (target && event.target === target) release();
  });

  /* A press, the wheel or Escape puts the hint away, as the browser's own
   * goes; its title stays off until the pointer leaves, or the browser
   * would show its own box in its place. A scroll moves it with its
   * element, and puts it away once the element is out of sight: focus
   * scrolls a column, often by nothing. */
  function putAway() {
    clearTimeout(timer);
    if (tip && !tip.hidden) { tip.hidden = true; hiddenAt = Date.now(); }
  }
  document.addEventListener("pointerdown", putAway, true);
  document.addEventListener("wheel", putAway, { capture: true, passive: true });
  document.addEventListener("scroll", function () {
    if (!target || !tip || tip.hidden) return;
    var at = target.getBoundingClientRect();
    if (at.bottom < 0 || at.top > window.innerHeight || !at.width) putAway();
    else place();
  }, true);
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") putAway();
  }, true);
  window.addEventListener("blur", function () { if (target) release(); });

  /* An element's title, whether or not it is off while its hint is up. */
  function titleOf(element) {
    return element && element === target ? title : (element && element.getAttribute("title"));
  }

  window.FastMDXTooltips = { release: release, titleOf: titleOf };
})();
