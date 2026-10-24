/* What every plot on the Analysis page can do (series-chart.js,
 * figure-chart.js): zoom by dragging across it, pan with Shift held, show
 * all again by a double click or its button, and save what it shows as
 * SVG, PNG or CSV.
 *
 * A time range dragged across a plot on the simulation's clock is the
 * page's: every plot on that clock shows it, and the Viewer's timeline
 * marks it (viewer-timeline.js). One range at a time, kept while the page
 * is open; Show all clears it.
 */
(function () {
  "use strict";

  var NS = "http://www.w3.org/2000/svg";
  var range = null;

  /* The page's time range: {label, from, to} on the clock its label names
   * ("Time (ns)", or "Frame" where the run kept no clock), or null. */
  function getRange(label) {
    if (!range) return null;
    return label === undefined || range.label === label ? range : null;
  }

  function setRange(label, from, to) {
    if (!(to > from)) return;
    range = { label: label, from: from, to: to };
    document.dispatchEvent(new CustomEvent("fmx:time-range", { detail: range }));
  }

  function clearRange() {
    if (!range) return;
    range = null;
    document.dispatchEvent(new CustomEvent("fmx:time-range", { detail: null }));
  }

  function button(text, title) {
    var b = document.createElement("button");
    b.type = "button";
    b.className = "plot-tool";
    b.textContent = text;
    b.title = title;
    return b;
  }

  function fileName(name, ext) {
    return String(name || "plot").replace(/[^A-Za-z0-9_.-]+/g, "_").replace(/^_+|_+$/g, "") + "." + ext;
  }

  function save(blob, name) {
    var link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = name;
    document.body.appendChild(link);
    link.click();
    setTimeout(function () { URL.revokeObjectURL(link.href); link.remove(); }, 0);
  }

  /* The ground the plot is drawn on, for a PNG: the first colour behind it. */
  function ground(node) {
    for (var n = node; n && n.nodeType === 1; n = n.parentElement) {
      var colour = getComputedStyle(n).backgroundColor;
      if (colour && colour !== "transparent" && !/rgba\(.*,\s*0\)$/.test(colour)) return colour;
    }
    return "#ffffff";
  }

  /* The plot as it is drawn, standing alone: its fonts named, its tools and
   * brush left out. */
  function standalone(svg, host) {
    var copy = svg.cloneNode(true);
    // On the ground it is drawn on, so its words, coloured for that ground
    // (light on a dark page), read where the file is opened.
    var back = document.createElementNS(NS, "rect");
    back.setAttribute("x", "0");
    back.setAttribute("y", "0");
    back.setAttribute("width", "100%");
    back.setAttribute("height", "100%");
    back.setAttribute("fill", ground(host || svg));
    back.setAttribute("class", "plot-ground");
    copy.insertBefore(back, copy.firstChild);
    copy.setAttribute("xmlns", NS);
    copy.setAttribute("xmlns:xlink", "http://www.w3.org/1999/xlink");
    // The page's font for the plot's words, named in the file itself.
    var words = svg.querySelector("text");
    copy.setAttribute("font-family", (words ? getComputedStyle(words) : getComputedStyle(svg)).fontFamily
                      || "sans-serif");
    Array.prototype.forEach.call(copy.querySelectorAll(".plot-brush"), function (n) { n.remove(); });
    return new XMLSerializer().serializeToString(copy);
  }

  function saveSvg(svg, name, host) {
    save(new Blob([standalone(svg, host)], { type: "image/svg+xml" }), fileName(name, "svg"));
  }

  function savePng(svg, name, host) {
    var box = svg.viewBox && svg.viewBox.baseVal;
    var width = box && box.width ? box.width : svg.clientWidth;
    var height = box && box.height ? box.height : svg.clientHeight;
    var scale = 3;
    var image = new Image();
    var url = URL.createObjectURL(new Blob([standalone(svg, host)], { type: "image/svg+xml" }));
    image.onload = function () {
      var canvas = document.createElement("canvas");
      canvas.width = Math.round(width * scale);
      canvas.height = Math.round(height * scale);
      var ctx = canvas.getContext("2d");
      ctx.fillStyle = ground(host || svg);
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.drawImage(image, 0, 0, canvas.width, canvas.height);
      URL.revokeObjectURL(url);
      canvas.toBlob(function (blob) { if (blob) save(blob, fileName(name, "png")); }, "image/png");
    };
    image.onerror = function () { URL.revokeObjectURL(url); };
    image.src = url;
  }

  function csvText(rows) {
    return rows.map(function (row) {
      return row.map(function (cell) {
        var text = cell == null ? "" : typeof cell === "number" ? String(+cell.toPrecision(10)) : String(cell);
        return /[",\n]/.test(text) ? '"' + text.replace(/"/g, '""') + '"' : text;
      }).join(",");
    }).join("\n") + "\n";
  }

  function saveCsv(rows, name) {
    save(new Blob([csvText(rows)], { type: "text/csv" }), fileName(name, "csv"));
  }

  /* Tools on one drawn plot. `opts`:
   *   svg, width, height, plot ({left, top, right, bottom} in the svg's units),
   *   invert(x, y) -> {x, y} in the data's units as drawn,
   *   zoom: "x", "xy" or "" (none: categories, or a list of bars),
   *   shared: the clock's label where a dragged range is the page's,
   *   zoomed: whether it shows less than all,
   *   onView(view) with {x: [lo, hi], y: [lo, hi]} (y where "xy"), onReset(),
   *   csv() -> rows, the first its head; name: the file's name;
   *   ownArrows: the plot's arrow keys are its own (a cursor along it);
   *   least: {x, y}, the smallest span a key zooms to (a few points' worth);
   *   full: {x: [lo, hi], y: [lo, hi]}, the data's extent: no zoom out or
   *     move goes past it, and a view of all of it is Show all.
   * By the keyboard on a focused plot: + and - zoom about the middle of
   * what it shows, [ and ] move along it (and the arrows, where they are
   * not the plot's own), 0 or Escape shows all. */
  function attach(host, opts) {
    var svg = opts.svg;
    var bar = document.createElement("div");
    bar.className = "plot-tools";
    var all = button("Show all", "Show all of it again (or double-click the plot)");
    all.hidden = !opts.zoomed;
    all.addEventListener("click", reset);
    bar.appendChild(all);
    [["SVG", "Save the plot as shown, as SVG"], ["PNG", "Save the plot as shown, as PNG"],
     ["CSV", "Save the numbers it shows, as CSV"]].forEach(function (kind) {
      var b = button(kind[0], kind[1]);
      b.addEventListener("click", function () {
        if (kind[0] === "SVG") saveSvg(svg, opts.name, host);
        else if (kind[0] === "PNG") savePng(svg, opts.name, host);
        else saveCsv(opts.csv(), opts.name);
      });
      bar.appendChild(b);
    });
    host.appendChild(bar);
    if (opts.zoom) {
      svg.classList.add("plot-zoomable");
      var tip = opts.shared
        ? "Drag across to see a time range on every plot on this clock; Shift and drag to move along; double-click for all"
        : "Drag across to zoom; Shift and drag to move; double-click for all";
      var keys = "Keys: + and - zoom, [ and ] move along" + (opts.ownArrows ? "" : " (or the arrows)") +
        ", 0 shows all.";
      svg.setAttribute("data-zoom-hint", tip + ". " + keys);
      // Reached by the keyboard, and its keys said with what it shows.
      if (!svg.hasAttribute("tabindex")) svg.setAttribute("tabindex", "0");
      // Added to the plot's own keys (its arrows and Enter), not over them.
      var had = svg.getAttribute("aria-keyshortcuts") || "";
      if (had.indexOf("+ - [ ] 0") < 0) {
        svg.setAttribute("aria-keyshortcuts", had ? had + " + - [ ] 0" : "+ - [ ] 0");
      }
      var label = svg.getAttribute("aria-label") || "";
      if (label.indexOf(keys) < 0) svg.setAttribute("aria-label", (label ? label + ". " : "") + keys);
    }

    /* What the plot shows now, in the data's units. */
    function shown() {
      var r = opts.plot;
      var a = opts.invert(r.left, r.bottom), b = opts.invert(r.right, r.top);
      return { x: [Math.min(a.x, b.x), Math.max(a.x, b.x)], y: [Math.min(a.y, b.y), Math.max(a.y, b.y)] };
    }
    function scaled(k) {
      var now = shown(), view = {}, changed = false;
      var least = opts.least || {};
      [["x", now.x], ["y", now.y]].forEach(function (axis) {
        if (axis[0] === "y" && opts.zoom !== "xy") return;
        var span = axis[1][1] - axis[1][0], mid = (axis[1][0] + axis[1][1]) / 2;
        var wanted = span * k;
        // Zoomed in no further than a few points' worth.
        var floor = least[axis[0]] > 0 ? least[axis[0]] : 0;
        if (k < 1 && wanted < floor) wanted = Math.min(span, floor);
        if (Math.abs(wanted - span) > 1e-12 * Math.max(1, Math.abs(span))) changed = true;
        view[axis[0]] = [mid - wanted / 2, mid + wanted / 2];
      });
      return changed ? view : null;
    }
    function along(fx, fy) {
      if (!opts.zoomed) return null;
      var now = shown(), dx = (now.x[1] - now.x[0]) * fx, dy = (now.y[1] - now.y[0]) * fy;
      var view = { x: [now.x[0] + dx, now.x[1] + dx] };
      if (opts.zoom === "xy") view.y = [now.y[0] + dy, now.y[1] + dy];
      return view;
    }

    /* A view kept inside the data's extent: no wider than it, moved back
     * inside it where it went past; null where it is all of it. */
    function bounded(view) {
      var full = opts.full || {}, whole = true;
      ["x", "y"].forEach(function (axis) {
        var v = view[axis], f = full[axis];
        if (!v || !f || !(f[1] > f[0])) { if (v) whole = false; return; }
        var span = Math.min(v[1] - v[0], f[1] - f[0]);
        var lo = Math.min(Math.max(v[0], f[0]), f[1] - span);
        view[axis] = [lo, lo + span];
        if (span < (f[1] - f[0]) * (1 - 1e-9)) whole = false;
      });
      return whole ? null : view;
    }

    /* A view asked for: the page's range or the plot's own, or all of it. */
    function apply(view) {
      var kept = bounded(view);
      if (!kept) { reset(); return; }
      if (opts.shared) setRange(opts.shared, kept.x[0], kept.x[1]);
      else if (opts.onView) opts.onView(kept);
    }

    function reset() {
      if (opts.shared && getRange(opts.shared)) clearRange();
      else if (opts.onReset) opts.onReset();
    }

    function at(event) {
      var box = svg.getBoundingClientRect();
      return { x: (event.clientX - box.left) * (opts.width / box.width),
               y: (event.clientY - box.top) * (opts.height / box.height) };
    }
    function within(p) {
      var r = opts.plot;
      return p.x >= r.left && p.x <= r.right && p.y >= r.top && p.y <= r.bottom;
    }
    function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

    var start = null, brush = null, moved = false, pan = false;
    if (opts.zoom) {
      svg.addEventListener("pointerdown", function (event) {
        if (event.button !== 0) return;
        var p = at(event);
        if (!within(p)) return;
        start = p;
        moved = false;
        pan = event.shiftKey && opts.zoomed;
        try { svg.setPointerCapture(event.pointerId); } catch (e) { /* not captured */ }
      });
      svg.addEventListener("pointermove", function (event) {
        if (!start) return;
        var p = at(event);
        if (Math.abs(p.x - start.x) + Math.abs(p.y - start.y) < 5) return;
        moved = true;
        if (pan) return;
        var r = opts.plot;
        var x0 = clamp(Math.min(start.x, p.x), r.left, r.right), x1 = clamp(Math.max(start.x, p.x), r.left, r.right);
        var y0 = opts.zoom === "xy" ? clamp(Math.min(start.y, p.y), r.top, r.bottom) : r.top;
        var y1 = opts.zoom === "xy" ? clamp(Math.max(start.y, p.y), r.top, r.bottom) : r.bottom;
        if (!brush) {
          brush = document.createElementNS(NS, "rect");
          brush.setAttribute("class", "plot-brush");
          svg.appendChild(brush);
        }
        brush.setAttribute("x", x0);
        brush.setAttribute("y", y0);
        brush.setAttribute("width", Math.max(0, x1 - x0));
        brush.setAttribute("height", Math.max(0, y1 - y0));
      });
      svg.addEventListener("pointerup", function (event) {
        if (!start) return;
        var p = at(event);
        var began = start;
        start = null;
        if (brush) { brush.remove(); brush = null; }
        if (!moved) return;
        // A drag is not a click: the frame under it is not opened.
        svg.dataset.dragged = String(Date.now());
        var r = opts.plot;
        var a = opts.invert(clamp(began.x, r.left, r.right), clamp(began.y, r.top, r.bottom));
        var b = opts.invert(clamp(p.x, r.left, r.right), clamp(p.y, r.top, r.bottom));
        var view;
        if (pan) {
          var here = opts.invert(r.left, r.bottom), there = opts.invert(r.right, r.top);
          var dx = a.x - b.x, dy = a.y - b.y;
          view = { x: [here.x + dx, there.x + dx] };
          if (opts.zoom === "xy") view.y = [here.y + dy, there.y + dy];
        } else {
          if (Math.abs(p.x - began.x) < 5) return;
          view = { x: [Math.min(a.x, b.x), Math.max(a.x, b.x)] };
          if (opts.zoom === "xy") {
            if (Math.abs(p.y - began.y) < 5) {
              var full = [opts.invert(r.left, r.bottom).y, opts.invert(r.left, r.top).y];
              view.y = [Math.min(full[0], full[1]), Math.max(full[0], full[1])];
            } else {
              view.y = [Math.min(a.y, b.y), Math.max(a.y, b.y)];
            }
          }
        }
        apply(view);
      });
      svg.addEventListener("pointercancel", function () {
        start = null;
        if (brush) { brush.remove(); brush = null; }
      });
      svg.addEventListener("dblclick", function (event) {
        if (!within(at(event))) return;
        event.preventDefault();
        reset();
      });
      // The click a drag ends with opens nothing.
      svg.addEventListener("click", function (event) {
        if (Date.now() - Number(svg.dataset.dragged || 0) < 400) {
          event.stopImmediatePropagation();
          event.preventDefault();
        }
      }, true);
      svg.addEventListener("keydown", function (event) {
        if (event.ctrlKey || event.metaKey || event.altKey) return;
        var key = event.key, view = null;
        if ((key === "Escape" || key === "0") && opts.zoomed) {
          reset();
        } else if (key === "+" || key === "=") {
          view = scaled(0.5);
        } else if ((key === "-" || key === "_") && opts.zoomed) {
          view = scaled(2);
        } else if ((key === "[" || key === "]") ||
                   (!opts.ownArrows && /^Arrow(Left|Right)$/.test(key))) {
          view = along(key === "[" || key === "ArrowLeft" ? -0.25 : 0.25, 0);
        } else if (!opts.ownArrows && opts.zoom === "xy" && /^Arrow(Up|Down)$/.test(key)) {
          view = along(0, key === "ArrowDown" ? -0.25 : 0.25);
        } else {
          return;
        }
        event.preventDefault();
        if (view) apply(view);
        // Drawn again: the keyboard stays on the plot.
        requestAnimationFrame(function () {
          var again = host.querySelector("svg.plot-zoomable");
          if (again && document.activeElement !== again) again.focus({ preventScroll: true });
        });
      });
    }
    return bar;
  }

  window.FastMDXPlotTools = {
    attach: attach, range: getRange, setRange: setRange, clearRange: clearRange,
    csvText: csvText, standalone: standalone,
  };
}());
