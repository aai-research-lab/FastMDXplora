/* New study and the workspace's defaults file (IMDE 2.9). A file that is
 * refused (a setting of one study in it, a value the schema refuses) fills
 * nothing, and the form offers FastMDXplora's own values meanwhile. Only
 * Settings' This folder said so: New study showed FastMDXplora's values
 * with nothing said, so a lab's file refused for one setting dropped all
 * its values silently, and the study was refused only once it started.
 * The refusal `/api/schema` gives (`defaults_refused`) is said above the
 * form each time New study is opened, with the way to This folder.
 *
 * The form is drawn from the defaults read when it was first drawn
 * (run-builder.js): put right, refused or changed in This folder since,
 * the fields still said the old ones beside a note that said the new. A
 * read that differs from the form's is said as that, above the form, with
 * Read the defaults again, which draws the form from the file as it is.
 * A file refused since the form was drawn from it is said as refused, as
 * on opening: the start refuses it, so it is not one a study runs with.
 *
 * A phone's page scrolls as a whole, and New study was come back to where
 * This folder was left, the note far above: it is brought into view as it
 * is said, where it is not in view already. */
(function () {
  "use strict";

  var asking = null;

  function el(id) { return document.getElementById(id); }

  function make(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  /* Words with `names` in them, the names set as code. */
  function words(into, text) {
    String(text).split("`").forEach(function (part, i) {
      if (part) into.appendChild(i % 2 ? make("code", "mono", part) : document.createTextNode(part));
    });
  }

  /* `drawnFromFile`: the form's fields say the file's values, read before
   * it was refused, and Read the defaults again offers FastMDXplora's. */
  function say(note, refused, drawnFromFile) {
    note.textContent = "";
    if (!refused) { note.hidden = true; return; }
    note.appendChild(make("b", "", "Your defaults are not used. "));
    words(note, refused);
    note.appendChild(document.createTextNode(drawnFromFile
      ? " This form was drawn with the file's values; read the defaults again to offer"
        + " FastMDXplora's own. "
      : " This form offers FastMDXplora's own values until the file is put right. "));
    var link = make("a", "", "This folder in Settings");
    link.href = "#settings";
    link.addEventListener("click", function (e) {
      if (!window.FastMDXSettings) return;
      e.preventDefault();
      window.FastMDXSettings.scope("folder");
    });
    note.appendChild(link);
    if (drawnFromFile) note.appendChild(againButton(note));
    note.hidden = false;
  }

  function fieldsOf(schema) {
    var lists = Object.keys(schema.phases || {}).sort().map(function (phase) {
      return schema.phases[phase].fields;
    }).concat([schema.run_options, schema.execution_options]);
    return [].concat.apply([], lists.map(function (fields) { return fields || []; }));
  }

  /* What a schema says of the folder's defaults: each setting's default
   * and where it is from, and the refusal of the file. */
  function defaultsOf(schema) {
    if (!schema) return "";
    return JSON.stringify([schema.defaults_refused || ""].concat(fieldsOf(schema).map(function (f) {
      return [f.name, f.default, f.default_from || ""];
    })));
  }

  /* Whether any of a schema's defaults is the folder's file's. */
  function fromFile(schema) {
    return fieldsOf(schema).some(function (f) { return Boolean(f.default_from); });
  }

  function builder() { return window.FastMDXRun && window.FastMDXRun.schema ? window.FastMDXRun : null; }

  /* The form drawn again from the file as it is now, and what that says. */
  function again(note, button) {
    button.disabled = true;
    builder().schema(true).then(function (schema) {
      say(note, schema && schema.defaults_refused);
      var title = document.querySelector('.page[data-page="run"] .page-title');
      var to = note.hidden ? title : note;
      if (to) { to.tabIndex = -1; to.focus(); }
    }).catch(function () { button.disabled = false; });
  }

  function againButton(note) {
    var button = make("button", "ghost-btn builder-defaults-again", "Read the defaults again");
    button.type = "button";
    button.addEventListener("click", function () { again(note, button); });
    return button;
  }

  function changed(note) {
    note.textContent = "";
    note.appendChild(make("b", "", "Your defaults changed since this form was drawn. "));
    note.appendChild(document.createTextNode(
      "Its fields say the defaults it was drawn with, and a study runs with the file as it is now."));
    note.appendChild(againButton(note));
    note.hidden = false;
  }

  /* Shown, and not out of view or under the bar kept at a phone's top. */
  function seen(note) {
    if (note.hidden || !note.getClientRects().length) return true;
    var r = note.getBoundingClientRect();
    var at = document.elementFromPoint(r.left + r.width / 2, r.top + Math.min(r.height, 20) / 2);
    return Boolean(at && note.contains(at));
  }

  function read() {
    var note = el("run-defaults-refused");
    if (!note || asking) return;
    var form = builder();
    asking = Promise.all([
      fetch("/api/schema").then(function (response) { return response.json(); }),
      form ? form.schema() : null
    ]).then(function (both) {
      var now = both[0];
      var drawn = both[1];
      var refused = now && now.defaults_refused;
      var differs = drawn && defaultsOf(drawn) !== defaultsOf(now);
      if (differs && !refused) changed(note);
      else say(note, refused, differs && fromFile(drawn));
      if (!seen(note)) note.scrollIntoView({ block: "center" });
    }).catch(function () { /* the form says it could not read the settings */ })
      .then(function () { asking = null; });
  }

  function start() {
    if (window.FastMDXDashboard) {
      window.FastMDXDashboard.on("navigate", function (detail) {
        if (detail && detail.page === "run") read();
      });
    }
    if (document.documentElement.dataset.page === "run") read();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
}());
