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
 *                                  holds it while the pointer is on it. */
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

  function flash(made, ok, said) {
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

  window.FastMDXIcons = { svg: svg, button: button, name: name, flash: flash };
})();
