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

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  /* Text whose `setting` names are set as code, as the fixes write them. */
  function withCode(host, said) {
    String(said || "").split(/(`[^`]+`)/).forEach(function (part) {
      if (/^`[^`]+`$/.test(part)) host.appendChild(el("code", "", part.slice(1, -1)));
      else if (part) host.appendChild(document.createTextNode(part));
    });
    return host;
  }

  function run(fix, actions) {
    actions.innerHTML = "";
    actions.appendChild(el("span", "fix-said", "Starting…"));
    fetch("/api/fix", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ index: fix.index })
    }).then(function (res) { return res.json(); }).then(function (d) {
      actions.innerHTML = "";
      actions.appendChild(el("span", "fix-said",
        d && d.ok ? "Started. It is followed here as it runs." : (d && d.error) || "Could not start it."));
    }).catch(function () {
      actions.innerHTML = "";
      actions.appendChild(el("span", "fix-said", "Could not reach the server to start it."));
    });
  }

  function offer(fix, actions) {
    actions.innerHTML = "";
    var go = el("button", "primary-btn fix-run", "Run it");
    go.type = "button";
    go.addEventListener("click", function () {
      /* Asked once more, with the price, before anything starts. */
      actions.innerHTML = "";
      actions.appendChild(el("span", "fix-said",
        "Run the command above now?" + (fix.price_said ? " It costs " + fix.price_said + "." : "")));
      var yes = el("button", "primary-btn fix-confirm", "Yes, run it");
      yes.type = "button";
      yes.addEventListener("click", function () { run(fix, actions); });
      var no = el("button", "ghost-btn fix-cancel", "Not now");
      no.type = "button";
      no.addEventListener("click", function () { offer(fix, actions); });
      actions.appendChild(yes);
      actions.appendChild(no);
      yes.focus();
    });
    actions.appendChild(go);
  }

  function render(data) {
    var card = document.getElementById("fixes-card");
    var list = document.getElementById("fixes-list");
    if (!card || !list) return;
    var fixes = data && data.ok ? data.fixes || [] : [];
    var key = JSON.stringify(fixes);
    if (key === lastKey) return;   // drawn again only when it changed
    lastKey = key;
    list.innerHTML = "";
    card.hidden = !fixes.length;
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
        offer(fix, actions);
        item.appendChild(actions);
      }
      list.appendChild(item);
    });
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
