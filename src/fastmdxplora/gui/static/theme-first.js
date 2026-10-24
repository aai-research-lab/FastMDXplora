/* The scheme before the first paint, as frame.js chooses it: Light or
 * Dark where one was chosen (Graphite and Ink, chosen before, are Dark;
 * Paper is Light), otherwise the computer's. Loaded in the page's head, so
 * a light computer's page does not open dark and turn light.
 *
 * The accent too: the person's own (`fmx.accent`), else the one the
 * service hosting the GUI chose (`data-accent-default` on the root
 * element, `fastmdx gui --accent`), else Cyan, so the page never shows
 * Cyan first and turns. */
(function () {
  var kept = null, former = { graphite: "dark", ink: "dark", paper: "light" };
  try { kept = localStorage.getItem("fmx.theme"); } catch (e) { /* private mode */ }
  kept = former[kept] || kept;
  if (kept !== "light" && kept !== "dark") {
    try { kept = window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark"; }
    catch (e) { kept = "dark"; }
  }
  var root = document.documentElement;
  root.dataset.theme = kept;
  var accents = ["cyan", "blue", "violet", "magenta", "graphite"];
  var accent = null;
  try { accent = localStorage.getItem("fmx.accent"); } catch (e) { /* private mode */ }
  if (accents.indexOf(accent) < 0) accent = root.getAttribute("data-accent-default");
  if (accents.indexOf(accent) >= 0 && accent !== "cyan") root.dataset.accent = accent;
  else root.removeAttribute("data-accent");
})();

/* The Display size, before the first paint (IMDE 2.9; the ideas session's
 * large-screen entry, Auto by default, user 10-21): everything at once,
 * text included, as CSS `zoom` on the page. Auto picks from the screen the
 * browser reports, in CSS pixels, so a system's own scaling is already in
 * it and never doubled: the GUI was drawn for about 1920 by 1080, and Auto
 * is the largest of 100%, 110%, 125% and 150% that the screen holds as many
 * times over (100% to about 1920 by 1080, 125% at 2560 by 1440 or an
 * ultrawide 3440 by 1440, 150% at 3840 by 2160). It is chosen as the page
 * opens and again only when the window is moved to another screen. A size
 * chosen in Settings (`fmx.displaySize`, 90 to 150) is used as it is, but
 * for one thing: a Display size never makes the page narrower than the
 * 390 pixels of a phone, which the page is laid out for. At 150% a 390
 * pixel window was a page 260 wide, and Analysis, All studies and Files
 * scrolled sideways. The step applied is the largest not above the one
 * chosen (or Auto's) that keeps the page at least 390 wide, as the window
 * opens and each time it is resized, and Settings says so beside the
 * select while the size chosen is held back.
 *
 * A page zoomed this way reports what is on the screen in zoomed pixels
 * (an element's box, where the pointer is, the window's width) while the
 * page lays itself out and is positioned in its own: every popover, menu
 * and tooltip placed from a box would land a quarter off at 125%, and the
 * Viewer's canvas would draw at the page's pixels, not the screen's. The
 * Display size is one zoom, applied the same to every rule and script:
 * while the page is zoomed, what it is told of the screen (a box, the
 * pointer, the window's width and height however read) is said in its own
 * pixels, as at 100%, the screen's pixels per page pixel include the zoom,
 * a width or height in a media query is compared at the page's own size,
 * and a rule sized by the window is sized by the page's (below); at 100%
 * nothing is changed. Where the browser does not zoom so (no
 * `currentCSSZoom`), the page stays at 100% and Settings says to use the
 * browser's own zoom. */
(function () {
  var root = document.documentElement;
  var STEPS = [1, 1.1, 1.25, 1.5];
  var CHOICES = ["auto", "90", "100", "110", "125", "150"];
  var NARROWEST = 390;
  var works = typeof Element !== "undefined" && "currentCSSZoom" in Element.prototype;
  var zoom = 1;
  var shimmed = false;
  // The window's own width, in screen pixels, read past the shim below.
  var widthOf = Object.getOwnPropertyDescriptor(window, "innerWidth");
  function windowWidth() {
    try { return widthOf && widthOf.get ? widthOf.get.call(window) : window.innerWidth; }
    catch (e) { return 0; }
  }

  function chosen() {
    var saved = null;
    try { saved = localStorage.getItem("fmx.displaySize"); } catch (e) { /* private mode */ }
    return CHOICES.indexOf(saved) >= 0 ? saved : "auto";
  }

  function auto() {
    var w = (window.screen && screen.width) || 1920;
    var h = (window.screen && screen.height) || 1080;
    var room = Math.min(w / 1920, h / 1080);
    var best = 1;
    STEPS.forEach(function (s) { if (s <= room + 0.001) best = s; });
    return best;
  }

  function wanted() {
    var c = chosen();
    return c === "auto" ? auto() : Number(c) / 100;
  }

  /* The step applied for ``z``: the largest not above it that keeps the
   * page at least 390 pixels wide. A size under 100% is never held back,
   * nor 100% in a window under 390. */
  function fitted(z) {
    var width = windowWidth();
    if (z <= 1 || !(width > 0)) return z;
    var best = 1;
    STEPS.forEach(function (s) { if (s <= z + 0.001 && width / s >= NARROWEST) best = s; });
    return best;
  }

  function apply(z) {
    zoom = works ? z : 1;
    if (zoom === 1) {
      root.style.removeProperty("zoom");
      root.removeAttribute("data-display-size");
    } else {
      root.style.zoom = String(zoom);
      root.setAttribute("data-display-size", String(Math.round(zoom * 100)));
      shim();
    }
    root.style.setProperty("--fmx-zoom", String(zoom));
    queries();
    try { window.dispatchEvent(new CustomEvent("fmx:display-size", { detail: { zoom: zoom } })); }
    catch (e) { /* an old browser: the size still applies */ }
  }

  /* What the page is told of the screen, in its own pixels while zoomed. */
  function shim() {
    if (shimmed) return;
    shimmed = true;
    function scaled(r) {
      return zoom === 1 ? r : new DOMRect(r.x / zoom, r.y / zoom, r.width / zoom, r.height / zoom);
    }
    function boxes(proto) {
      var rect = proto.getBoundingClientRect, rects = proto.getClientRects;
      if (rect) proto.getBoundingClientRect = function () { return scaled(rect.call(this)); };
      if (rects) proto.getClientRects = function () {
        var all = rects.call(this);
        if (zoom === 1) return all;
        return Array.prototype.map.call(all, scaled);
      };
    }
    boxes(Element.prototype);
    if (window.Range) boxes(Range.prototype);
    function divided(proto, names) {
      names.forEach(function (name) {
        var d = Object.getOwnPropertyDescriptor(proto, name);
        if (!d || !d.get || !d.configurable) return;
        Object.defineProperty(proto, name, { configurable: true, enumerable: d.enumerable,
          get: function () { return d.get.call(this) / zoom; } });
      });
    }
    divided(MouseEvent.prototype, ["clientX", "clientY", "x", "y", "offsetX", "offsetY"]);
    if (window.Touch) divided(Touch.prototype, ["clientX", "clientY"]);
    ["pageX", "pageY"].forEach(function (name) {
      var d = Object.getOwnPropertyDescriptor(MouseEvent.prototype, name);
      var client = name === "pageX" ? "clientX" : "clientY";
      var scroll = name === "pageX" ? "scrollX" : "scrollY";
      if (!d || !d.configurable) return;
      Object.defineProperty(MouseEvent.prototype, name, { configurable: true, enumerable: d.enumerable,
        get: function () { return zoom === 1 ? d.get.call(this) : this[client] + window[scroll]; } });
    });
    ["innerWidth", "innerHeight"].forEach(function (name) {
      var d = Object.getOwnPropertyDescriptor(window, name);
      if (!d || !d.configurable || !d.get) return;
      Object.defineProperty(window, name, { configurable: true, enumerable: d.enumerable,
        get: function () { return d.get.call(window) / zoom; } });
    });
    // The window's size read from the root element (as tooltips.js reads
    // it): the root's own box is the page's, and only the root's is the
    // window's.
    ["clientWidth", "clientHeight", "scrollWidth", "scrollHeight"].forEach(function (name) {
      var d = Object.getOwnPropertyDescriptor(Element.prototype, name);
      if (!d || !d.configurable || !d.get) return;
      Object.defineProperty(Element.prototype, name, { configurable: true, enumerable: d.enumerable,
        get: function () {
          var value = d.get.call(this);
          return this === document.documentElement ? value / zoom : value;
        } });
    });
    var media = window.matchMedia;
    if (typeof media === "function") {
      window.matchMedia = function (query) { return media.call(window, zoomedQuery(String(query))); };
    }
    var ratio = Object.getOwnPropertyDescriptor(window, "devicePixelRatio");
    if (ratio && ratio.configurable && ratio.get) {
      Object.defineProperty(window, "devicePixelRatio", { configurable: true, enumerable: ratio.enumerable,
        get: function () { return ratio.get.call(window) * zoom; } });
    }
    ["elementFromPoint", "elementsFromPoint", "caretPositionFromPoint", "caretRangeFromPoint"].forEach(function (name) {
      var at = Document.prototype[name];
      if (typeof at !== "function") return;
      Document.prototype[name] = function (x, y) { return at.call(this, x * zoom, y * zoom); };
    });
  }

  /* A width or height in a media query, compared at the page's own size:
   * the browser compares it with the window's, in screen pixels, so at
   * 150% a 900 pixel window, a page 600 wide, still had the layout for a
   * wide one. Each media rule's query is said again with its lengths times
   * the zoom, from its own words saved the first time; a sheet added later
   * is said so as it comes. A bound of whole pixels stays one of whole
   * pixels, so a pair that meets at 100% meets at every size: a `max-*` of
   * N is floor(N * zoom) and a `min-*` of N is floor((N - 1) * zoom) + 1.
   * Times the zoom, `(max-width: 800px)` and `(min-width: 801px)` were 1000
   * and 1001.25 at 125%, and a window 1001 wide held neither layout. */
  var LENGTH = /((min|max)-(width|height)\s*:\s*)?(\d*\.?\d+)(px|em|rem)\b/g;
  function zoomedQuery(text) {
    if (zoom === 1) return text;
    return text.replace(LENGTH, function (all, bound, side, axis, n, unit) {
      var v = Number(n);
      if (bound && unit === "px" && Math.floor(v) === v) {
        var at = side === "max" ? Math.floor(v * zoom + 1e-9) : Math.floor((v - 1) * zoom + 1e-9) + 1;
        return bound + at + unit;
      }
      return (bound || "") + String(Math.round(v * zoom * 1000) / 1000) + unit;
    });
  }
  var asWritten = typeof WeakMap === "function" ? new WeakMap() : null;
  function written(rule) {
    return (asWritten && asWritten.get(rule)) || rule.media.mediaText;
  }
  function queries() {
    if (!asWritten || !document.styleSheets) return;
    function each(rules) {
      Array.prototype.forEach.call(rules, function (rule) {
        if (rule.media && rule.cssRules && typeof CSSMediaRule !== "undefined"
            && rule instanceof CSSMediaRule) {
          var text = asWritten.get(rule);
          if (text === undefined) {
            if (zoom === 1) { each(rule.cssRules); return; }
            text = rule.media.mediaText;
            asWritten.set(rule, text);
          }
          var now = zoomedQuery(text);
          if (rule.media.mediaText !== now) rule.media.mediaText = now;
        }
        if (rule.cssRules && !(rule.style && rule.selectorText)) each(rule.cssRules);
      });
    }
    Array.prototype.forEach.call(document.styleSheets, function (sheet) {
      try { each(sheet.cssRules); } catch (e) { /* another origin's sheet: none here */ }
    });
  }

  /* Rules sized by the window (vh, vw) sized by the page's own window:
   * zoomed, 100vh is the window's height in screen pixels and would run
   * a quarter past its foot at 125%. Copied, after the page's own rules,
   * divided by the zoom, for while it is zoomed. A property sized so
   * anywhere is copied from every rule that sets it, in the page's order,
   * so a later rule's own value still wins over an earlier one sized by
   * the window: copied alone, `.app-shell`'s first `min-height: 100vh` came
   * after its later `min-height: 0`, and the shell ran under the status
   * bar. */
  var VIEWPORT = /(-?\d*\.?\d+)(dvh|svh|lvh|vh|dvw|svw|lvw|vw|vmin|vmax)\b/g;
  function sizedByTheWindow() {
    if (document.getElementById("fmx-display-size-rules")) return;
    function scope(selector) {
      return selector.split(",").map(function (one) {
        var s = one.trim();
        if (/^:root\b/.test(s)) return s.replace(/^:root/, ":root[data-display-size]");
        if (/^html\b/.test(s)) return s.replace(/^html/, "html[data-display-size]");
        return ":where(html[data-display-size]) " + s;
      }).join(", ");
    }
    function each(rules, one) {
      Array.prototype.forEach.call(rules, function (rule) {
        if (rule.style && rule.selectorText) {
          for (var i = 0; i < rule.style.length; i += 1) one(rule, rule.style[i]);
        } else if (rule.cssRules) each(rule.cssRules, one);
      });
    }
    var sheets = [];
    Array.prototype.forEach.call(document.styleSheets, function (sheet) {
      try { sheets.push(sheet.cssRules); } catch (e) { /* another origin's sheet: none here */ }
    });
    var sized = {};
    sheets.forEach(function (rules) {
      each(rules, function (rule, name) {
        VIEWPORT.lastIndex = 0;
        if (VIEWPORT.test(rule.style.getPropertyValue(name))) sized[name] = true;
      });
    });
    /* Every grouping rule (`@media`, `@supports`, `@container`, `@layer`)
     * copied with its own prelude, so a copy holds where its rule holds:
     * copied bare, a rule within `@container` held at every width. */
    function prelude(rule) {
      if (typeof CSSMediaRule !== "undefined" && rule instanceof CSSMediaRule) {
        return "@media " + written(rule);
      }
      var text = rule.cssText;
      return text.slice(0, text.indexOf("{")).trim();
    }
    function walk(rules, into) {
      Array.prototype.forEach.call(rules, function (rule) {
        if (rule.cssRules && !(rule.style && rule.selectorText)) {
          var inner = [];
          walk(rule.cssRules, inner);
          if (inner.length) into.push(prelude(rule) + " {\n" + inner.join("\n") + "\n}");
          return;
        }
        if (!rule.style || !rule.selectorText) return;
        var props = [];
        for (var i = 0; i < rule.style.length; i += 1) {
          var name = rule.style[i];
          var value = rule.style.getPropertyValue(name);
          if (!sized[name] || !value) continue;
          var priority = rule.style.getPropertyPriority(name);
          props.push(name + ": " + value.replace(VIEWPORT, "calc($1$2 / var(--fmx-zoom, 1))") +
                     (priority ? " !" + priority : "") + ";");
        }
        if (props.length) into.push(scope(rule.selectorText) + " { " + props.join(" ") + " }");
      });
    }
    var out = [];
    sheets.forEach(function (rules) { walk(rules, out); });
    var style = document.createElement("style");
    style.id = "fmx-display-size-rules";
    style.textContent = out.join("\n");
    document.head.appendChild(style);
    queries();
    // A sheet added later (a script's own) is said at the page's size too.
    if (window.MutationObserver) {
      new MutationObserver(function (changes) {
        changes.forEach(function (change) {
          Array.prototype.forEach.call(change.addedNodes, function (node) {
            if (node.tagName === "STYLE") queries();
            else if (node.tagName === "LINK") node.addEventListener("load", queries);
          });
        });
      }).observe(document.head, { childList: true });
    }
  }

  apply(fitted(wanted()));
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", sizedByTheWindow);
  else sizedByTheWindow();
  // Moved to another screen, Auto chooses again; resized, the size chosen
  // is held back where the page would be under 390 wide, or given back.
  window.addEventListener("resize", function () {
    var z = fitted(wanted());
    if ((works ? z : 1) !== zoom) apply(z);
  });

  window.FastMDXDisplay = {
    works: works,
    choices: CHOICES,
    chosen: chosen,
    auto: auto,
    zoom: function () { return zoom; },
    // The size chosen, or Auto's, before it is held back for a narrow window.
    wanted: function () { return works ? wanted() : 1; },
    narrowest: NARROWEST,
    /* "auto", or "90" to "150": saved, and applied at once. */
    choose: function (value) {
      if (CHOICES.indexOf(value) < 0) return;
      try { localStorage.setItem("fmx.displaySize", value); } catch (e) { /* this page only */ }
      apply(fitted(wanted()));
    },
  };
})();
