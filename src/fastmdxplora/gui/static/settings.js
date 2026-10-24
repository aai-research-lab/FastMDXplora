/* Settings: every setting on one page (IMDE 2.9).
 *
 * Two scopes, as tabs. You is this computer: the theme, the accent and
 * the Display size (frame.js, theme-first.js), the Viewer, time, charts
 * and notices (preferences.js, which saves them as they change) and the
 * Agent (its own dialog, opened from here). This folder is the
 * workspace's `fastmdx-defaults.yml`, which every new study made here
 * starts from, from the GUI, the command line and the Agent alike: each
 * setting it may hold, FastMDXplora's own value beside it; one set here
 * is written into the file at once and checked as any defaults file is
 * (`/api/defaults-file`, settings_page.py), Use FastMDXplora's takes it out
 * again, and a reason given is saved with it in the file's decisions.
 * Nothing waits for a Save.
 *
 * One search finds a setting by its words in both, the other scope's
 * count beside its name. Preferences were a dialog and the Agent's
 * settings are one: the popup's Settings… leads here, the Agent's dialog
 * leads to its part (`[data-settings-part-link]`), and `#preferences`, the
 * old address, is this page.
 */
(function () {
  "use strict";

  var BLOCKS = { setup: "Setup", simulation: "Simulation", analysis: "Analysis",
                 report: "Report", execution: "Execution" };
  var SCOPE_KEY = "fmx.settingsScope";

  function el(id) { return document.getElementById(id); }
  function $$(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }
  function make(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }
  /* Words from the server, a setting's name in them (`simulation.ph`) as
   * code, not between backticks. */
  function sayInto(node, text) {
    node.textContent = "";
    String(text).split("`").forEach(function (part, i) {
      if (part) node.appendChild(i % 2 ? make("code", "mono", part) : document.createTextNode(part));
    });
    return node;
  }
  function read(key) { try { return localStorage.getItem(key); } catch (e) { return null; } }
  function write(key, value) { try { localStorage.setItem(key, value); } catch (e) { /* this page only */ } }

  /* The old address of Preferences is this page's: said before the page
   * reads the address (dashboard.js), as it opens and as it changes. */
  function oldAddress() {
    if (location.hash === "#preferences") history.replaceState(history.state, "", "#settings");
  }
  oldAddress();
  window.addEventListener("hashchange", oldAddress);

  function shownHere() { return document.documentElement.dataset.page === "settings"; }
  function go() {
    if (window.FastMDXDashboard) window.FastMDXDashboard.navigate("settings");
    else location.hash = "#settings";
  }

  /* ---- Scopes ------------------------------------------------------ */
  function scopes() { return $$(".settings-scope"); }
  function scopeShown() {
    var on = scopes().filter(function (b) { return b.getAttribute("aria-selected") === "true"; })[0];
    return on ? on.dataset.settingsScope : "you";
  }
  function chooseScope(name, focus) {
    scopes().forEach(function (b) {
      var on = b.dataset.settingsScope === name;
      b.setAttribute("aria-selected", String(on));
      b.tabIndex = on ? 0 : -1;
      var panel = el(b.getAttribute("aria-controls"));
      if (panel) panel.hidden = !on;
      if (on && focus) b.focus();
    });
    write(SCOPE_KEY, name);
    if (name === "folder" && !folder.said && !folder.asking) loadFolder();
    found();
  }

  /* ---- Search ------------------------------------------------------ */
  function words(row) { return (row.textContent || "").replace(/\s+/g, " ").toLowerCase(); }

  /* Each row shown that has every word typed; a group with none is
   * hidden, and each scope's count is said beside its name. */
  function found() {
    var input = el("settings-find");
    var typed = input ? input.value.trim().toLowerCase() : "";
    var wanted = typed ? typed.split(/\s+/) : [];
    var onlySet = el("settings-only-set") && el("settings-only-set").checked;
    ["you", "folder"].forEach(function (scope) {
      var panel = el("settings-" + scope);
      if (!panel) return;
      var count = 0;
      $$(".pref-group", panel).forEach(function (group) {
        var title = words(group.querySelector(".pref-group-title") || group);
        var shown = 0;
        $$(".pref-row", group).forEach(function (row) {
          var text = words(row) + " " + title;
          var hit = wanted.every(function (w) { return text.indexOf(w) >= 0; })
            && !(onlySet && scope === "folder" && !row.classList.contains("is-set"));
          // A row that is no setting (the Agent's button) goes with its group.
          if (row.classList.contains("settings-row-end")) return;
          row.hidden = !hit;
          if (hit) shown += 1;
        });
        $$(".settings-row-end", group).forEach(function (row) { row.hidden = !shown; });
        group.hidden = !shown;
        count += shown;
      });
      var tab = el("settings-scope-" + scope);
      var said = tab && tab.querySelector(".settings-count");
      if (said) {
        var known = scope === "you" || (folder.said && !folder.said.cannot && !folder.said.refused);
        said.hidden = !typed || !known;
        said.textContent = String(count);
        said.setAttribute("aria-label", count + (count === 1 ? " setting found" : " settings found"));
      }
      var none = el("settings-none-" + scope);
      if (!none) {
        none = make("p", "settings-none muted small");
        none.id = "settings-none-" + scope;
        panel.appendChild(none);
      }
      var other = scope === "you" ? "folder" : "you";
      none.hidden = !typed || count > 0 || (scope === "folder" && !known);
      none.textContent = "No setting here has those words.";
      if (!none.hidden) {
        var there = make("button", "link-btn", other === "folder" ? "Look in This folder" : "Look in You");
        there.type = "button";
        there.addEventListener("click", function () { chooseScope(other, true); });
        none.appendChild(document.createTextNode(" "));
        none.appendChild(there);
      }
    });
  }

  /* ---- The theme and the accent ------------------------------------ */
  /* The gear's menu's buttons, copied: one set in the page's markup. Copied
   * as the page is read, before frame.js (loaded after this) wires every
   * theme and accent button and says which is chosen, so these are wired
   * with them. */
  function copyFromThePopup() {
    [["#settings-popup .seg-btn[data-theme]", "settings-page-themes"],
     ["#settings-popup .accent-swatch[data-accent]", "settings-page-accents"]].forEach(function (pair) {
      var into = el(pair[1]);
      if (!into || into.children.length) return;
      $$(pair[0]).forEach(function (button) { into.appendChild(button.cloneNode(true)); });
    });
  }

  /* ---- Display size ------------------------------------------------ */
  function sayDisplaySize() {
    var select = el("setting-display-size");
    var note = el("setting-display-size-note");
    var display = window.FastMDXDisplay;
    if (!select || !display) return;
    var auto = Math.round(display.auto() * 100);
    select.options[0].textContent = "Auto: " + auto + "%";
    select.value = display.chosen();
    var wanted = Math.round(display.wanted() * 100);
    var applied = Math.round(display.zoom() * 100);
    if (!display.works) {
      select.disabled = true;
      note.textContent = "This browser cannot size the page itself: use its own zoom, " +
        "Ctrl or ⌘ with + and −.";
    } else if (applied < wanted) {
      // Held back: the page is never made narrower than a phone's.
      note.textContent = (display.chosen() === "auto" ? "Auto is " + wanted + "% on this screen"
        : wanted + "% chosen") + "; " + applied + "% in a window this narrow, so the page is never " +
        "under " + display.narrowest + " pixels wide.";
    } else if (display.chosen() === "auto") {
      note.textContent = "Everything at once, text included. Auto is " + auto +
        "% on this screen, and chosen again on another.";
    } else {
      note.textContent = "Everything at once, text included.";
    }
  }

  /* ---- The Agent --------------------------------------------------- */
  /* Its engine and mode as the settings popup says them (frame.js and the
   * Agent's own dialog fill those); changed in the Agent's dialog. */
  function mirror(from, to) {
    var source = el(from);
    var target = el(to);
    if (!source || !target) return;
    var copy = function () { target.textContent = source.textContent; };
    copy();
    new MutationObserver(copy).observe(source, { childList: true, characterData: true, subtree: true });
  }

  /* ---- A part of the page ------------------------------------------ */
  function show(part) {
    var input = el("settings-find");
    if (input && input.value) { input.value = ""; found(); }
    go();
    chooseScope("you", false);
    var group = part && document.querySelector('#settings-you [data-settings-part="' + part + '"]');
    if (!group) {
      // The page as a whole: the keyboard and a screen reader on its name.
      setTimeout(function () {
        var heading = document.querySelector('.page[data-page="settings"] .page-title');
        if (!heading || heading.offsetParent === null) return;
        if (!heading.hasAttribute("tabindex")) heading.setAttribute("tabindex", "-1");
        heading.focus({ preventScroll: true });
      }, 0);
      return;
    }
    setTimeout(function () {
      group.scrollIntoView({ block: "start" });
      var first = group.querySelector("select, input, button");
      if (first) first.focus({ preventScroll: true });
      group.classList.remove("settings-part-shown");
      void group.offsetWidth;
      group.classList.add("settings-part-shown");
    }, 0);
  }

  /* ==================================================================== */
  /* This folder                                                           */
  /* ==================================================================== */
  /* `sent` counts the changes sent, and `pending` those not yet answered:
   * a read asked before the last of them, or while one is in flight, may
   * be answered with the file as it was, and is dropped. */
  var folder = { said: null, asking: false, before: {}, sent: 0, pending: 0 };

  function shownValue(field, value) {
    if (value === null || value === undefined) return "";
    if (typeof value === "boolean") return value ? "On" : "Off";
    if (Array.isArray(value)) {
      return value.map(function (v) { return typeof v === "object" ? JSON.stringify(v) : String(v); }).join(", ");
    }
    if (typeof value === "object") return JSON.stringify(value);
    return String(value);
  }

  function theirs(field) {
    if (field.default !== null && field.default !== undefined) return shownValue(field, field.default);
    if (field.unset) return String(field.unset);
    // On, off or as the force field is developed: unset is the last, as
    // New study says it (run-builder.js).
    if (field.control === "tristate") return "as the force field was developed";
    return "none";
  }

  /* Whether the setting's control can say the file's value: a setting
   * that takes words or a list of blocks (the restraints) holds blocks the
   * box cannot, and is shown as the file holds it, changed by hand. */
  function drawable(field, value) {
    if (!field.control || value === undefined || value === null) return Boolean(field.control);
    if (field.control === "list" || field.control === "multiselect") {
      return Array.isArray(value) && value.every(function (v) { return v === null || typeof v !== "object"; });
    }
    return typeof value !== "object";
  }

  function isBool(field) {
    return field.control === "checkbox" || field.control === "tristate";
  }

  /* What a control says, as the file would hold it; undefined where it is
   * left empty (FastMDXplora's), or an Error said beside the setting. */
  function taken(field, control) {
    if (field.control === "multiselect") {
      var ticked = $$("input:checked", control).map(function (b) { return b.value; });
      return ticked.length ? ticked : undefined;
    }
    var text = String(control.value || "").trim();
    if (!text) return undefined;
    if (field.control === "select") return text;
    if (isBool(field)) return text === "true";
    if (field.control === "number") {
      var n = Number(text);
      if (!isFinite(n)) return new Error("A number, please.");
      if (field.type === "int" && Math.floor(n) !== n) return new Error("A whole number, please.");
      return n;
    }
    if (field.control === "list") {
      var items = text.split(",").map(function (x) { return x.trim(); }).filter(Boolean);
      if (field.items === "number") {
        var numbers = items.map(Number);
        if (numbers.some(function (x) { return !isFinite(x); })) return new Error("Numbers, between commas.");
        return numbers;
      }
      return items;
    }
    return text;
  }

  function control(field, value, fixed) {
    var id = "settings-folder-" + field.name.replace(/\./g, "-");
    var node;
    var here = value !== undefined;
    if (!drawable(field, value) || fixed) {
      // A setting of several values, as the file holds them, under the words.
      if (here && value !== null && typeof value === "object") {
        return make("pre", "settings-fixed settings-several mono", JSON.stringify(value, null, 2));
      }
      // Set only by hand, and not set: said beside it, no value of its own.
      if (!here && !fixed && !drawable(field, value)) return null;
      node = make("code", "settings-fixed mono", here ? (value === null ? "FastMDXplora's"
        : shownValue(field, value)) : theirs(field));
      return node;
    }
    if (field.control === "select" || isBool(field)) {
      node = make("select");
      var first = make("option", "", "FastMDXplora's");
      first.value = "";
      node.appendChild(first);
      var choices = isBool(field) ? [["true", "On"], ["false", "Off"]]
        : (field.choices || []).map(function (c) { return [String(c), String(c)]; });
      choices.forEach(function (c) {
        var option = make("option", "", c[1]);
        option.value = c[0];
        node.appendChild(option);
      });
      // `null` in the file is FastMDXplora's, chosen.
      node.value = here && value !== null ? String(value) : "";
    } else if (field.control === "multiselect") {
      node = make("details", "settings-multi");
      var summary = make("summary", "", here && value !== null ? shownValue(field, value) : "FastMDXplora's");
      node.appendChild(summary);
      var list = make("div", "settings-multi-list");
      (field.choices || []).forEach(function (c) {
        var label = make("label", "settings-multi-item");
        var box = make("input");
        box.type = "checkbox";
        box.value = String(c);
        box.checked = here && Array.isArray(value) && value.indexOf(c) >= 0;
        label.appendChild(box);
        label.appendChild(document.createTextNode(String(c)));
        list.appendChild(label);
      });
      node.appendChild(list);
    } else {
      node = make("input");
      node.type = field.control === "number" ? "number" : "text";
      if (field.control === "number") {
        node.step = "any";
        if (field.minimum !== null && field.minimum !== undefined) node.min = String(field.minimum);
        if (field.maximum !== null && field.maximum !== undefined) node.max = String(field.maximum);
      }
      node.spellcheck = false;
      node.autocomplete = "off";
      node.placeholder = field.control === "list"
        ? (field.example ? "e.g. " + field.example : "between commas") : "FastMDXplora's";
      node.value = here ? shownValue(field, value) : "";
    }
    node.id = id;
    node.className = (node.className ? node.className + " " : "") + "settings-control";
    return node;
  }

  function row(field) {
    var said = folder.said;
    var value = Object.prototype.hasOwnProperty.call(said.values, field.name) ? said.values[field.name] : undefined;
    var fixed = !writable();
    var item = make("div", "pref-row settings-field" + (value !== undefined ? " is-set" : ""));
    item.dataset.name = field.name;
    var words = make("span", "settings-words");
    var label = make("label", "settings-label", field.label);
    label.htmlFor = "settings-folder-" + field.name.replace(/\./g, "-");
    if (field.unit) label.appendChild(make("span", "settings-unit", " (" + field.unit + ")"));
    words.appendChild(label);
    words.appendChild(make("small", "", field.summary));
    var theirsSaid = make("small", "settings-theirs", "FastMDXplora's: " + theirs(field));
    theirsSaid.appendChild(make("span", "settings-name mono", " " + field.name));
    words.appendChild(theirsSaid);
    if (field.help && field.help !== field.summary) words.title = field.help;
    item.appendChild(words);

    var side = make("div", "settings-side");
    var input = control(field, value, fixed);
    var several = Boolean(input) && input.tagName === "PRE";
    if (input && !several) side.appendChild(input);
    var drawn = drawable(field, value);
    if (!fixed && drawn) {
      if (value !== undefined) {
        var back = make("button", "link-btn settings-use-theirs", "Use FastMDXplora's");
        back.type = "button";
        back.addEventListener("click", function () { change(field, { unset: [field.name] }); });
        side.appendChild(back);
      }
      // Against the file as last said, not as this row was drawn: Enter
      // sent it, and the row drawn again from the answer took the box away,
      // which fired its change and sent it again, so Undo put back the
      // value just set.
      var commit = function () {
        var now = folder.said.values;
        var held = Object.prototype.hasOwnProperty.call(now, field.name) ? now[field.name] : undefined;
        var wanted = taken(field, input);
        if (wanted instanceof Error) { refused(field.name, wanted.message); return; }
        if (wanted === undefined) {
          if (held === undefined) return;
          change(field, { unset: [field.name] });
          return;
        }
        if (JSON.stringify(wanted) === JSON.stringify(held)) return;
        var body = { set: {} };
        body.set[field.name] = wanted;
        change(field, body);
      };
      if (field.control === "multiselect") input.addEventListener("change", commit);
      else {
        input.addEventListener("change", commit);
        input.addEventListener("keydown", function (e) {
          if (e.key === "Enter" && input.tagName === "INPUT") { e.preventDefault(); commit(); }
        });
      }
    } else if (!drawn && !fixed) {
      side.appendChild(make("small", "settings-by-hand", value === undefined
        ? "Set only by hand in the file" : "Changed in the file by hand"));
    }
    item.appendChild(side);
    if (several) item.appendChild(input);

    if (value !== undefined && fixed) {
      if (said.whys[field.name]) item.appendChild(make("small", "settings-why-said", "Why: " + said.whys[field.name]));
    } else if (value !== undefined) {
      var why = make("input", "settings-why");
      why.type = "text";
      why.placeholder = "Why, saved in the file with it (optional)";
      why.setAttribute("aria-label", "Why " + field.label + " is set so");
      why.value = said.whys[field.name] || "";
      why.addEventListener("change", function () {
        var text = why.value.trim();
        if (text === (said.whys[field.name] || "")) return;
        var body = { why: {} };
        body.why[field.name] = text;
        change(field, body, "why");
      });
      item.appendChild(why);
    }
    var status = make("p", "settings-status small");
    status.setAttribute("role", "status");
    item.appendChild(status);
    return item;
  }

  function writable() {
    var said = folder.said;
    return said && said.ok !== false && !said.refused && !said.comments && !said.cannot;
  }

  function rowOf(name) {
    return $$("#settings-folder-body .settings-field").filter(function (r) { return r.dataset.name === name; })[0];
  }

  function refused(name, reason) {
    var item = rowOf(name);
    if (!item) return;
    var status = item.querySelector(".settings-status");
    sayInto(status, reason);
    status.className = "settings-status small is-refused";
    var input = item.querySelector(".settings-control");
    if (input) input.setAttribute("aria-invalid", "true");
  }

  function fieldOf(name) {
    return (folder.said.fields || []).filter(function (f) { return f.name === name; })[0];
  }

  /* One change written into the file; the row drawn again from what the
   * file now says, with Undo, or the refusal said beside it with what was
   * typed left to be put right. The row is found again when the answer
   * comes: a read answered meanwhile has drawn every row again. */
  function change(field, body, which, undoing) {
    var asked = rowOf(field.name);
    var before = {
      had: Object.prototype.hasOwnProperty.call(folder.said.values, field.name),
      value: folder.said.values[field.name],
      why: folder.said.whys[field.name] || ""
    };
    if (asked) asked.setAttribute("aria-busy", "true");
    folder.sent += 1;
    folder.pending += 1;
    var inFlight = true;
    function answered() { if (inFlight) { inFlight = false; folder.pending -= 1; } }
    fetch("/api/defaults-file", {
      method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body)
    }).then(function (r) { return r.json(); }).then(function (said) {
      answered();
      if (asked) asked.removeAttribute("aria-busy");
      var item = rowOf(field.name);
      if (item) item.removeAttribute("aria-busy");
      if (!said || said.ok === false) {
        refused(field.name, (said && (said.reason || said.error)) || "The file could not be written.");
        return;
      }
      folder.said = said;
      var focused = item && item.contains(document.activeElement)
        ? (document.activeElement.classList.contains("settings-why") ? ".settings-why" : ".settings-control") : "";
      // A list of choices open as one is ticked stays open for the next.
      var open = item && item.querySelector("details.settings-multi[open]");
      var ticked = open && open.contains(document.activeElement) ? document.activeElement.value : null;
      var again = row(field);
      if (item) item.replaceWith(again);
      var list = open && again.querySelector("details.settings-multi");
      if (list) {
        list.open = true;
        focused = "";
        var box = $$("input", list).filter(function (b) { return b.value === ticked; })[0];
        if (box) box.focus();
      }
      sayTheFile();
      found();
      var status = again.querySelector(".settings-status");
      status.className = "settings-status small is-saved";
      status.textContent = undoing ? "Put back. " : (which === "why" ? "Reason saved. " : "Saved in the file. ");
      if (!undoing) {
        var undo = make("button", "link-btn settings-undo", "Undo");
        undo.type = "button";
        undo.addEventListener("click", function () {
          var back = {};
          if (which === "why") { back.why = {}; back.why[field.name] = before.why; }
          else if (before.had) {
            back.set = {}; back.set[field.name] = before.value;
            if (before.why) { back.why = {}; back.why[field.name] = before.why; }
          } else back.unset = [field.name];
          change(fieldOf(field.name) || field, back, which, true);
        });
        status.appendChild(undo);
      }
      var refocus = focused && again.querySelector(focused);
      if (refocus) refocus.focus();
    }).catch(function () {
      answered();
      if (asked) asked.removeAttribute("aria-busy");
      var item = rowOf(field.name);
      if (item) item.removeAttribute("aria-busy");
      refused(field.name, "Could not reach the server.");
    });
  }

  function sayTheFile() {
    var lead = el("settings-folder-lead");
    var said = folder.said;
    if (!lead || !said) return;
    lead.textContent = "";
    lead.appendChild(document.createTextNode(
      "New studies made in this folder start from these, from the GUI, the command line and the Agent alike. "));
    if (said.path) {
      lead.appendChild(document.createTextNode(said.exists ? "Saved in " : "Not there yet; the first one set here writes "));
      lead.appendChild(make("span", "mono settings-path", said.path));
      lead.appendChild(document.createTextNode("."));
    }
    var set = Object.keys(said.values || {}).length;
    var only = el("settings-only-set-count");
    if (only) only.textContent = set ? " (" + set + ")" : "";
  }

  function drawFolder() {
    var body = el("settings-folder-body");
    var said = folder.said;
    if (!body || !said) return;
    body.textContent = "";
    sayTheFile();
    if (said.cannot) {
      body.appendChild(make("p", "settings-cannot", said.cannot));
      found();
      return;
    }
    if (said.refused || said.comments) {
      var note = make("p", "settings-cannot");
      if (said.refused) sayInto(note, "Nothing here can be changed until the file is put right by hand. " + said.refused);
      else note.textContent = "The file has comments, which writing it would lose: change it by hand, or take " +
        "the comments out and change it here.";
      body.appendChild(note);
      if (said.refused) { found(); return; }
    }
    var head = make("div", "settings-folder-head");
    var onlyLabel = make("label", "settings-only");
    var only = make("input");
    only.type = "checkbox";
    only.id = "settings-only-set";
    only.addEventListener("change", found);
    onlyLabel.appendChild(only);
    onlyLabel.appendChild(document.createTextNode(" Only those this file sets"));
    var count = make("span", "", "");
    count.id = "settings-only-set-count";
    onlyLabel.appendChild(count);
    head.appendChild(onlyLabel);
    body.appendChild(head);
    Object.keys(BLOCKS).forEach(function (block) {
      var fields = (said.fields || []).filter(function (f) { return f.block === block; });
      if (!fields.length) return;
      var group = make("div", "pref-group");
      group.dataset.block = block;
      group.appendChild(make("div", "pref-group-title", BLOCKS[block]));
      var rows = make("div", "pref-rows");
      fields.forEach(function (f) { rows.appendChild(row(f)); });
      group.appendChild(rows);
      body.appendChild(group);
    });
    sayTheFile();
    found();
  }

  function loadFolder() {
    folder.asking = true;
    var sent = folder.sent;
    var inFlight = folder.pending > 0;
    function stale() { return folder.said && (inFlight || folder.sent !== sent); }
    fetch("/api/defaults-file").then(function (r) {
      if (r.status === 404) return { cannot: "This server cannot read the folder's defaults yet." };
      return r.json();
    }).then(function (said) {
      folder.asking = false;
      // A change sent since it was asked, or not yet answered when it was:
      // the file as it was, which would draw that change's row as not set.
      // The change's answer says it.
      if (stale()) return;
      if (said && said.ok === false && !said.fields) said.cannot = said.reason || said.error || "Not here.";
      folder.said = said || { cannot: "The folder's defaults could not be read." };
      if (!folder.said.values) folder.said.values = {};
      if (!folder.said.whys) folder.said.whys = {};
      drawFolder();
    }).catch(function () {
      folder.asking = false;
      if (stale()) return;
      folder.said = { cannot: "Could not reach the server.", values: {}, whys: {} };
      drawFolder();
    });
  }

  /* ==================================================================== */
  function start() {
    if (!el("settings-you")) return;
    copyFromThePopup();
    scopes().forEach(function (b) {
      b.addEventListener("click", function () { chooseScope(b.dataset.settingsScope, false); });
      b.addEventListener("keydown", function (e) {
        var all = scopes();
        var at = all.indexOf(b);
        var to = e.key === "ArrowRight" ? all[(at + 1) % all.length]
          : e.key === "ArrowLeft" ? all[(at - 1 + all.length) % all.length]
          : e.key === "Home" ? all[0] : e.key === "End" ? all[all.length - 1] : null;
        if (!to) return;
        e.preventDefault();
        chooseScope(to.dataset.settingsScope, true);
      });
    });
    var input = el("settings-find");
    if (input) {
      input.addEventListener("input", found);
      input.addEventListener("keydown", function (e) {
        if (e.key === "Escape" && input.value) { e.preventDefault(); input.value = ""; found(); }
      });
    }

    var size = el("setting-display-size");
    if (size) {
      size.addEventListener("change", function () {
        if (window.FastMDXDisplay) window.FastMDXDisplay.choose(size.value);
      });
    }
    window.addEventListener("fmx:display-size", sayDisplaySize);
    sayDisplaySize();

    mirror("settings-engine", "settings-page-engine");
    mirror("settings-mode", "settings-page-mode");
    var agent = el("settings-page-agent");
    if (agent) {
      agent.addEventListener("click", function () {
        if (window.FastMDXAgent && window.FastMDXAgent.openSettings) window.FastMDXAgent.openSettings();
      });
    }

    // To a part of the page from a dialog: the dialog closes as it goes.
    document.addEventListener("click", function (e) {
      var link = e.target.closest && e.target.closest("[data-settings-part-link]");
      if (!link) return;
      e.preventDefault();
      var dialog = link.closest(".agent-dialog");
      if (dialog && window.FastMDXDialog) window.FastMDXDialog.close(dialog.id);
      else if (dialog) dialog.hidden = true;
      show(link.getAttribute("data-settings-part-link"));
    });

    var was = read(SCOPE_KEY);
    chooseScope(was === "folder" ? "folder" : "you", false);
    // Read as the page is shown, so the search counts This folder too, and
    // again each time: the file may have been changed by hand meanwhile.
    var reading = function () { if (shownHere() && !folder.asking) loadFolder(); };
    if (window.FastMDXDashboard) window.FastMDXDashboard.on("navigate", reading);
    reading();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();

  window.FastMDXSettings = {
    show: show,
    scope: function (name) { go(); chooseScope(name === "folder" ? "folder" : "you", false); },
    reload: loadFolder,
    found: found
  };
}());
