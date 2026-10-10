/* The page's line icons for its scripts: the set the server writes into the
 * page (sidebar_icons.ICONS, as JSON in #fmx-icons), so a button a script
 * makes is drawn from the same set as the template's and never a copy of it.
 *
 * FastMDXIcons.svg(name, cls)      the icon as inline SVG, hidden from screen
 *                                  readers (its button carries the words);
 * FastMDXIcons.button(name, label) a button that is its icon, named on hover
 *                                  and to a screen reader;
 * FastMDXIcons.name(button, name, label)  an existing button made so;
 * FastMDXIcons.flash(button, ok, said)    a tick (or a cross) for a moment
 *                                  where a button's work is done (copied),
 *                                  its name saying so and the page's notice
 *                                  too (seen, and read out), then put back.
 *                                  Its hover text is left alone: tooltips.js
 *                                  holds it while the pointer is on it. A
 *                                  button in words keeps them: the tick
 *                                  stands in for its icon, or before them;
 * FastMDXIcons.copyText(text)      the text copied however the browser
 *                                  allows, to a promise of whether it was;
 * FastMDXIcons.copy(button, text, copied, refused)  copied, and the button
 *                                  ticked ("Copied") or crossed (the reason);
 *                                  with no button, only copied. Every Copy
 *                                  on the page goes through it. */
(function () {
  "use strict";

  var set = {};
  try {
    var data = document.getElementById("fmx-icons");
    if (data) set = JSON.parse(data.textContent);
  } catch (e) {
    set = {};
  }

  function svg(name, cls) {
    return '<svg class="' + (cls || "line-icon") + '" viewBox="0 0 24 24" width="16" height="16" '
      + 'fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" '
      + 'stroke-linejoin="round" aria-hidden="true">' + (set[name] || "") + "</svg>";
  }

  function name(button, icon, label) {
    button.classList.add("line-btn");
    button.innerHTML = svg(icon);
    button.title = label;
    button.setAttribute("aria-label", label);
    return button;
  }

  function button(icon, label, cls) {
    var made = document.createElement("button");
    made.type = "button";
    if (cls) made.className = cls;
    return name(made, icon, label);
  }

  /* A button in words keeps them; the tick goes where its icon is (the
   * study card's chevron), or before them. */
  function flashWords(made, ok, said) {
    var was = made._fmxTick;
    if (was) {
      clearTimeout(was.timer);
      was.tick.remove();
    } else {
      was = { name: made.getAttribute("aria-label"), own: null };
      for (var i = 0; i < made.children.length; i++) {
        if (made.children[i].tagName.toLowerCase() === "svg") was.own = made.children[i];
      }
      made._fmxTick = was;
    }
    var holder = document.createElement("span");
    holder.innerHTML = svg(ok ? "check" : "close", "line-icon copy-tick");
    was.tick = holder.firstChild;
    if (was.own) {
      was.own.style.display = "none";
      made.insertBefore(was.tick, was.own);
    } else {
      made.insertBefore(was.tick, made.firstChild);
    }
    made.classList.add("is-ticked");
    made.setAttribute("aria-label", said);
    was.timer = setTimeout(function () {
      was.tick.remove();
      if (was.own) was.own.style.display = "";
      made.classList.remove("is-ticked");
      if (was.name === null) made.removeAttribute("aria-label");
      else made.setAttribute("aria-label", was.name);
      delete made._fmxTick;
    }, 1800);
  }

  function flash(made, ok, said) {
    if (!made) return;
    if (made._fmxTick || (!made.dataset.label && made.textContent.trim())) {
      flashWords(made, ok, said);
      var notice = window.FastMDXDashboard;
      if (notice && notice.toast) notice.toast(said, ok ? "ok" : "warning");
      return;
    }
    // Its own name and drawing, not those a flash still showing gave it.
    var flashing = !!made.dataset.label;
    var label = flashing ? made.dataset.label : made.getAttribute("aria-label") || "";
    if (!flashing) made._fmxShape = made.innerHTML;
    made.dataset.label = label;
    clearTimeout(made._fmxFlash);
    made.innerHTML = svg(ok ? "check" : "close");
    made.setAttribute("aria-label", said);
    // Said where it is seen and read out: a name changing is neither.
    var page = window.FastMDXDashboard;
    if (page && page.toast) page.toast(said, ok ? "ok" : "warning");
    made._fmxFlash = setTimeout(function () {
      made.innerHTML = made._fmxShape;
      made.setAttribute("aria-label", label);
      delete made.dataset.label;
    }, 1800);
  }

  /* Copied however the browser allows: the clipboard is offered only on a
   * secure page, and a GUI reached over plain http from another machine is
   * not one, so there the text is selected in a hidden box and copied. */
  function copyText(text) {
    text = text == null ? "" : String(text);
    if (!text) return Promise.resolve(false);
    function selected() {
      var focused = document.activeElement;
      var area = document.createElement("textarea");
      area.value = text;
      area.setAttribute("readonly", "");
      area.style.position = "fixed";
      area.style.top = "0";
      area.style.opacity = "0";
      document.body.appendChild(area);
      area.select();
      var ok = false;
      try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
      area.remove();
      // Back where the keyboard was, where the tick is shown.
      if (focused && focused.focus) focused.focus({ preventScroll: true });
      return ok;
    }
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        return navigator.clipboard.writeText(text).then(function () { return true; }, selected);
      }
    } catch (e) {
      // Refused outright: the hidden box, as where there is no clipboard.
    }
    return Promise.resolve(selected());
  }

  function copy(made, text, copied, refused) {
    return copyText(text).then(function (ok) {
      if (made) flash(made, ok, ok ? copied || "Copied" : refused || "Select the text to copy it.");
      return ok;
    });
  }

  window.FastMDXIcons = {
    svg: svg, button: button, name: name, flash: flash, copyText: copyText, copy: copy,
  };
})();
