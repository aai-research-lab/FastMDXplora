/* One empty, loading and error state, the same on every page (IMDE 2.10).
 *
 * A page with nothing to show said so in a muted line on some pages and a
 * block with a title on others; an error went into a subtitle, a toast or
 * nowhere (the Files page said "Reading the study's files..." for ever when
 * the server did not answer). Each says it with this one block now: what
 * is happening or missing, why, and the one thing to do about it.
 *
 * FastMDXPageState.show(host, {kind: "loading" | "empty" | "error", title,
 *                           detail, action: {label, run}})
 * FastMDXPageState.block(options) the block itself, for a page that places it.
 */
(function () {
  "use strict";

  function make(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  function block(o) {
    o = o || {};
    var kind = o.kind || "empty";
    var box = make("div", "fmx-state");
    box.dataset.kind = kind;
    // Said as it changes: a reader hears the error, or that it is read.
    box.setAttribute("role", kind === "error" ? "alert" : "status");
    if (kind === "loading") {
      var spin = make("span", "fmx-spin");
      spin.setAttribute("aria-hidden", "true");
      box.appendChild(spin);
    }
    box.appendChild(make("span", "fmx-state-title", o.title || ""));
    if (o.detail) box.appendChild(make("span", "fmx-state-detail", o.detail));
    if (o.action && o.action.label && typeof o.action.run === "function") {
      var act = make("button", "ghost-btn fmx-state-act", o.action.label);
      act.type = "button";
      act.addEventListener("click", o.action.run);
      box.appendChild(act);
    }
    return box;
  }

  function show(host, o) {
    if (!host) return null;
    var box = block(o);
    host.replaceChildren(box);
    host.hidden = false;
    return box;
  }

  window.FastMDXPageState = { show: show, block: block };
}());
