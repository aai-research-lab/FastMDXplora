/* Preferences, the citation and a study's own words, as dialogs over
 * whatever page is shown, as the Agent's settings are.
 *
 * FastMDXDialog opens and closes them: Escape, a click on the dimmed page
 * or Close shuts the one on top, focus stays inside while it is open and
 * goes back to what opened it. The preferences are kept in this browser
 * (`fmx.preferences`) and put into their fields before the page reads
 * them, so a reload keeps them; a study's display name and ligand residue
 * are kept by the study's folder (`fmx.study:<folder>`). Nothing here is
 * sent to the server or written into a study.
 */
(function () {
  "use strict";

  function el(id) { return document.getElementById(id); }
  function $$(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }

  var store = {
    read: function (key) {
      try { return JSON.parse(localStorage.getItem(key) || "null"); } catch (e) { return null; }
    },
    write: function (key, value) {
      try {
        if (value === null) localStorage.removeItem(key);
        else localStorage.setItem(key, JSON.stringify(value));
      } catch (e) { /* a private window keeps nothing */ }
    }
  };

  /* ---- Dialogs ------------------------------------------------------ */
  var FOCUSABLE = 'button:not([disabled]), [href], input:not([type="hidden"]):not([disabled]),' +
                  ' select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';
  var openers = {};

  function shown(dialog) { return dialog && !dialog.hidden; }
  function topmost() {
    var open = $$(".agent-dialog").filter(shown);
    return open.length ? open[open.length - 1] : null;
  }
  function focusables(dialog) {
    return $$(FOCUSABLE, dialog).filter(function (n) { return n.offsetParent !== null; });
  }

  function openDialog(id, opener) {
    var dialog = el(id);
    if (!dialog) return null;
    openers[id] = opener || document.activeElement;
    dialog.hidden = false;
    // The first of its fields, or Close where it has none.
    var body = dialog.querySelector(".agent-dialog-body");
    var first = dialog.querySelector("[autofocus]") || (body && focusables(body)[0])
      || focusables(dialog)[0];
    if (first) first.focus();
    return dialog;
  }

  function closeDialog(dialog) {
    if (!dialog || dialog.hidden) return;
    dialog.hidden = true;
    var back = openers[dialog.id];
    delete openers[dialog.id];
    if (back && document.contains(back) && back.offsetParent !== null) back.focus();
  }

  document.addEventListener("keydown", function (e) {
    var dialog = topmost();
    if (!dialog) return;
    if (e.key === "Escape") {
      e.preventDefault();
      e.stopImmediatePropagation();
      closeDialog(dialog);
      return;
    }
    if (e.key !== "Tab") return;
    var all = focusables(dialog);
    if (!all.length) return;
    var first = all[0], last = all[all.length - 1];
    if (!dialog.contains(document.activeElement)) { e.preventDefault(); first.focus(); }
    else if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  }, true);

  document.addEventListener("click", function (e) {
    var target = e.target;
    // A click on the dimmed page around a dialog closes it.
    if (target.classList && target.classList.contains("agent-dialog")) {
      closeDialog(target);
      return;
    }
    var closer = target.closest && target.closest("[data-dialog-close]");
    if (closer) { closeDialog(closer.closest(".agent-dialog")); return; }
    var trigger = target.closest && target.closest("[data-dialog-open]");
    if (trigger) {
      e.preventDefault();
      openDialog(trigger.getAttribute("data-dialog-open"), whoOpened(trigger));
      return;
    }
    var field = target.closest && target.closest("[data-study-field]");
    if (field) openStudyField(field.getAttribute("data-study-field"), whoOpened(field));
  }, true);

  /* An item in a menu goes with the menu: focus goes back to what opened
   * the menu, and the menu is closed. */
  function whoOpened(item) {
    var popup = el("settings-popup");
    if (popup && popup.contains(item)) {
      popup.hidden = true;
      var gear = el("settings-open");
      if (gear) gear.setAttribute("aria-expanded", "false");
      return gear;
    }
    var menu = el("study-menu");
    if (menu && menu.contains(item)) return el("study-card");
    return item;
  }

  window.FastMDXDialog = { open: openDialog, close: function (id) { closeDialog(el(id)); } };

  /* ---- Copy --------------------------------------------------------- */
  async function copyText(text) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (err) {
      // The clipboard needs a secure context, which http://127.0.0.1 is
      // and a remote http:// host is not. Say so rather than pretend.
      return false;
    }
  }
  document.addEventListener("click", function (e) {
    var button = e.target.closest && e.target.closest("[data-copy-from]");
    if (!button) return;
    var source = el(button.getAttribute("data-copy-from"));
    if (!source) return;
    copyText(source.textContent.trim()).then(function (done) {
      button.textContent = done ? "Copied" : "Select and copy";
      setTimeout(function () { button.textContent = "Copy"; }, 2000);
    });
  });

  /* ---- Preferences -------------------------------------------------- */
  var KEY = "fmx.preferences";
  // Each preference: its field, how the field holds it, and its default.
  var PREFERENCES = {
    representation: ["setting-protein-rep", "value", "cartoon"],
    ground: ["setting-ground", "value", "scheme"],
    water: ["setting-show-water", "checked", false],
    ions: ["setting-show-ions", "checked", false],
    spin: ["setting-spin", "checked", false],
    keepCamera: ["setting-preserve-camera", "checked", true],
    pocketCutoff: ["setting-pocket-cutoff", "value", "5"],
    timeFormat: ["setting-time-format", "value", "24h"],
    chartHistory: ["setting-chart-history", "value", "600"]
  };

  function put(values) {
    Object.keys(PREFERENCES).forEach(function (name) {
      var spec = PREFERENCES[name];
      var field = el(spec[0]);
      if (!field || !(name in values)) return;
      if (spec[1] === "checked") field.checked = !!values[name];
      // A value its list no longer offers is left at the default.
      else if (field.tagName !== "SELECT" || $$("option", field).some(function (o) { return o.value === String(values[name]); })) {
        field.value = String(values[name]);
      }
    });
  }

  function taken() {
    var values = {};
    Object.keys(PREFERENCES).forEach(function (name) {
      var spec = PREFERENCES[name];
      var field = el(spec[0]);
      if (field) values[name] = spec[1] === "checked" ? field.checked : field.value;
    });
    return values;
  }

  function defaults() {
    var values = {};
    Object.keys(PREFERENCES).forEach(function (name) { values[name] = PREFERENCES[name][2]; });
    return values;
  }

  function ownFields() {
    return Object.keys(PREFERENCES).map(function (name) { return PREFERENCES[name][0]; });
  }

  // Into their fields before the page reads them (this script's listener
  // is added before the page's own, so it runs first).
  document.addEventListener("DOMContentLoaded", function () {
    var kept = store.read(KEY);
    if (kept && typeof kept === "object") put(kept);

    // Kept once a person changes one, here or in the Viewer (its pocket
    // cutoff is this one), and only then: until something is chosen, the
    // defaults of the release in use apply.
    document.addEventListener("change", function (e) {
      var id = e.target && e.target.id;
      if (ownFields().indexOf(id) >= 0 || id === "pocket-cutoff") {
        setTimeout(function () { store.write(KEY, taken()); }, 0);
      }
    });

    var restore = el("prefs-restore");
    if (restore) {
      restore.addEventListener("click", function () {
        put(defaults());
        store.write(KEY, null);
        var field = el(PREFERENCES.representation[0]);
        if (field) field.dispatchEvent(new Event("change", { bubbles: true }));
        // Writing nothing back: the change just sent would keep them.
        setTimeout(function () { store.write(KEY, null); }, 0);
      });
    }
    if (window.FastMDXDashboard && window.FastMDXDashboard.on) {
      window.FastMDXDashboard.on("run-changed", function (detail) {
        studyFolder = (detail && detail.activeRun) || "";
        putTheStudysWords();
      });
    }
  });

  /* ---- A study's own words ------------------------------------------ */
  var studyFolder = "";
  var STUDY_FIELDS = {
    name: {
      field: "setting-run-name",
      title: "Name for display",
      label: "Name",
      placeholder: "the study's system",
      note: "The name this study is shown under, in this browser. Its folder " +
            "and records keep their own. Empty, it is shown by its system.",
      check: function (text) { return text.length <= 80 ? "" : "At most 80 characters."; },
      tidy: function (text) { return text; }
    },
    ligand: {
      field: "setting-ligand-resname",
      title: "Ligand residue",
      label: "Residue name",
      placeholder: "found from the structure",
      note: "The ligand the Viewer centres on and finds the pocket around, by " +
            "its residue name in the structure (BEN, for example), for a " +
            "structure with more than one. The analyses are not changed. " +
            "Empty, the first ligand the structure has.",
      check: function (text) {
        return !text || /^[A-Z0-9]{1,5}$/.test(text)
          ? "" : "A residue name is 1 to 5 letters or digits.";
      },
      tidy: function (text) { return text.toUpperCase(); }
    }
  };

  function studyKey() { return studyFolder ? "fmx.study:" + studyFolder : ""; }

  function putTheStudysWords() {
    var kept = (studyKey() && store.read(studyKey())) || {};
    var changed = false;
    Object.keys(STUDY_FIELDS).forEach(function (which) {
      var field = el(STUDY_FIELDS[which].field);
      var value = typeof kept[which] === "string" ? kept[which] : "";
      if (field && field.value !== value) { field.value = value; changed = true; }
    });
    if (changed) tellThePage(STUDY_FIELDS.name.field);
  }

  function tellThePage(id) {
    var field = el(id);
    // The page listens for a change on each of its settings' fields.
    if (field) field.dispatchEvent(new Event("change", { bubbles: true }));
  }

  var editing = "";
  function openStudyField(which, opener) {
    var spec = STUDY_FIELDS[which];
    if (!spec) return;
    editing = which;
    el("study-field-title").textContent = spec.title;
    el("study-field-label").textContent = spec.label;
    el("study-field-note").textContent = spec.note;
    var input = el("study-field-input");
    input.placeholder = spec.placeholder;
    input.value = (el(spec.field) || {}).value || "";
    el("study-field-refused").hidden = true;
    openDialog("study-field-dialog", opener);
    input.select();
  }

  function keep(value) {
    var spec = STUDY_FIELDS[editing];
    if (!spec) return;
    var text = spec.tidy(String(value || "").trim());
    var refused = spec.check(text);
    var said = el("study-field-refused");
    if (refused) {
      said.textContent = refused;
      said.hidden = false;
      el("study-field-input").focus();
      return;
    }
    var key = studyKey();
    if (key) {
      var kept = store.read(key) || {};
      if (text) kept[editing] = text; else delete kept[editing];
      store.write(key, Object.keys(kept).length ? kept : null);
    }
    el(spec.field).value = text;
    tellThePage(spec.field);
    closeDialog(el("study-field-dialog"));
  }

  document.addEventListener("DOMContentLoaded", function () {
    var save = el("study-field-save");
    if (!save) return;
    save.addEventListener("click", function () { keep(el("study-field-input").value); });
    el("study-field-clear").addEventListener("click", function () { keep(""); });
    el("study-field-input").addEventListener("keydown", function (e) {
      if (e.key === "Enter") { e.preventDefault(); keep(e.target.value); }
    });
  });
}());
