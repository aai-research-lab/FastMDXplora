/* What would fix the study on screen (fixes_view.py), on the Overview.
 *
 * Each thing that stopped the study, what would fix it, the command that
 * does it and what that costs at the study's own speed. A fix that is this
 * software's own command (a resume, windows run again) can be run from
 * here: pressing Run asks once more with its price in view, and only a
 * second press starts it. Everything is built as elements; what a study
 * recorded is data, never markup. */
(function () {
  "use strict";

  var pending = false;
  var askedAt = 0;
  var RECHECK_MS = 15000;
  var lastKey = "";
  /* Where each fix stands, by all it says, so a drawing again keeps it.
   * The card is drawn again whenever the study changes, the first time a
   * moment after the page loads; a question asked then was drawn away,
   * and Yes with it (CI on a5bcec3a). `asked` holds the fixes whose
   * question is open; `said` what a fix started from here said of it. */
  var asked = {};
  var said = {};
  var CONTROLS = ["fix-run", "fix-confirm", "fix-cancel"];

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  /* Text whose `setting` names are set as code, as the fixes write them. */
  function withCode(host, text) {
    String(text || "").split(/(`[^`]+`)/).forEach(function (part) {
      if (/^`[^`]+`$/.test(part)) host.appendChild(el("code", "", part.slice(1, -1)));
      else if (part) host.appendChild(document.createTextNode(part));
    });
    return host;
  }

  function tell(actions, text) {
    actions.innerHTML = "";
    var line = el("span", "fix-said", text);
    line.setAttribute("role", "status");
    actions.appendChild(line);
  }

  function run(fix, actions) {
    var key = JSON.stringify(fix);
    delete asked[key];
    said[key] = "Starting…";
    tell(actions, said[key]);
    function told(text, started) {
      // A start that failed is offered again when the card is next drawn.
      if (started) said[key] = text;
      else delete said[key];
      // Written where the fix is now, should the card have been drawn again.
      var now = actions.isConnected ? actions : place(key);
      if (now) tell(now, text);
    }
    fetch("/api/fix", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ index: fix.index })
    }).then(function (res) { return res.json(); }).then(function (d) {
      told(d && d.ok ? "Started. It is followed here as it runs." : (d && d.error) || "Could not start it.",
           !!(d && d.ok));
    }).catch(function () {
      told("Could not reach the server to start it.", false);
    });
  }

  /* Asked once more, with the price, before anything starts. */
  function ask(fix, actions) {
    var key = JSON.stringify(fix);
    asked[key] = true;
    actions.innerHTML = "";
    var question = el("span", "fix-said",
      "Run the command above now?" + (fix.price_said ? " It costs " + fix.price_said + "." : ""));
    question.id = "fix-ask-" + fix.index;
    actions.appendChild(question);
    var yes = el("button", "primary-btn fix-confirm", "Yes, run it");
    yes.type = "button";
    yes.setAttribute("aria-describedby", question.id);
    yes.addEventListener("click", function () { run(fix, actions); });
    var no = el("button", "ghost-btn fix-cancel", "Not now");
    no.type = "button";
    no.addEventListener("click", function () {
      delete asked[key];
      offer(fix, actions);
      actions.querySelector(".fix-run").focus();
    });
    actions.appendChild(yes);
    actions.appendChild(no);
  }

  function offer(fix, actions) {
    actions.innerHTML = "";
    var go = el("button", "primary-btn fix-run", "Run it");
    go.type = "button";
    go.addEventListener("click", function () {
      ask(fix, actions);
      actions.querySelector(".fix-confirm").focus();
    });
    actions.appendChild(go);
  }

  /* The actions of the fix drawn now with this key, if it is listed. */
  function place(key) {
    var list = document.getElementById("fixes-list");
    var found = null;
    Array.prototype.forEach.call(list ? list.querySelectorAll(".fix-actions") : [], function (a) {
      if (a.fixKey === key) found = a;
    });
    return found;
  }

  /* Which control had the keyboard, and in which fix, before a drawing. */
  function focusNow(list) {
    var at = document.activeElement;
    if (!at || !list.contains(at)) return null;
    var actions = at.closest(".fix-actions");
    var control = CONTROLS.filter(function (c) { return at.classList.contains(c); })[0];
    return { key: actions ? actions.fixKey : null, control: control || null };
  }

  /* The same control in the same fix, drawn again; else that fix's own
   * button; else the card's title, never a button the person did not
   * choose. Yes is never given the keyboard unless it had it. */
  function focusAgain(card, was) {
    var actions = was.key ? place(was.key) : null;
    var same = actions && was.control ? actions.querySelector("." + was.control) : null;
    var own = actions ? actions.querySelector(".fix-run") : null;
    var to = same || own;
    if (!to) {
      to = card.querySelector(".card-title");
      if (to) to.setAttribute("tabindex", "-1");
    }
    if (to) to.focus();
  }

  function render(data) {
    var card = document.getElementById("fixes-card");
    var list = document.getElementById("fixes-list");
    if (!card || !list) return;
    var fixes = data && data.ok ? data.fixes || [] : [];
    var key = JSON.stringify(fixes);
    if (key === lastKey) return;   // drawn again only when it changed
    lastKey = key;
    var was = focusNow(list);
    var listed = {};
    list.innerHTML = "";
    card.hidden = !fixes.length;
    // A run stopped as asked is not a fault: its card says how to carry it
    // on rather than what would fix it.
    var title = card.querySelector(".card-title");
    var onlyStopped = fixes.length && fixes.every(function (fix) {
      return fix.code === "simulation.run.stopped";
    });
    var named = onlyStopped ? "To carry it on" : "What would fix it";
    if (title) title.textContent = named;
    // The status strip's link to this card is named as the card is.
    var link = document.getElementById("status-fix");
    if (link) link.textContent = named;
    // The sidebar's "What would fix it" leads here, so it is shown only
    // while there is a fix to show: it was shown for every failed run and
    // led to the Overview's top, its card hidden and empty.
    document.documentElement.setAttribute("data-fixes", fixes.length ? "some" : "none");
    fixes.forEach(function (fix) {
      var item = el("li", "fix");
      item.setAttribute("data-code", fix.code || "");
      var why = el("div", "fix-why");
      var where = String(fix.where || "");
      why.appendChild(el("span", "fix-where",
        where.charAt(0).toUpperCase() + where.slice(1) + ": "));
      withCode(why, fix.why || "");
      item.appendChild(why);
      item.appendChild(withCode(el("div", "fix-fix"), fix.fix || ""));
      if (fix.price_said) item.appendChild(el("div", "fix-price", "Costs " + fix.price_said + "."));
      if (fix.command) item.appendChild(el("pre", "fix-command", fix.command));
      if (fix.runnable) {
        var actions = el("div", "fix-actions");
        var fixKey = JSON.stringify(fix);
        actions.fixKey = fixKey;
        listed[fixKey] = true;
        item.appendChild(actions);
        if (said[fixKey]) tell(actions, said[fixKey]);
        else if (asked[fixKey]) ask(fix, actions);
        else offer(fix, actions);
      }
      list.appendChild(item);
    });
    Object.keys(asked).forEach(function (k) { if (!listed[k]) delete asked[k]; });
    Object.keys(said).forEach(function (k) { if (!listed[k]) delete said[k]; });
    if (was && fixes.length) focusAgain(card, was);
  }

  function load() {
    if (pending) return Promise.resolve();
    pending = true;
    askedAt = Date.now();
    return fetch("/api/fixes", { cache: "no-store" })
      .then(function (res) { return res.json(); })
      .then(render)
      .catch(function () { render(null); })
      .then(function () { pending = false; });
  }

  window.addEventListener("dashboard:run-changed", function () { lastKey = ""; load(); });
  window.addEventListener("dashboard:live-page-opened", load);
  window.addEventListener("dashboard:status-updated", function () {
    if (Date.now() - askedAt > RECHECK_MS) load();
  });
  document.addEventListener("DOMContentLoaded", load);

  window.FastMDXFixes = { load: load, render: render };
}());
