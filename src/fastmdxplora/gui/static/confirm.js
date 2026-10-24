/* One confirmation, for anything that destroys (IMDE 2.10).
 *
 * Stopping a run, stopping a job on another machine and clearing the
 * scratch were each asked in a way of their own (a line with two buttons,
 * another such line, a dialog). They ask with this one now, beside what
 * was pressed: what it is, what goes and what is saved, the choice that
 * does nothing focused, Escape for it. What can be undone asks nothing and
 * offers Undo instead.
 *
 * FastMDXConfirm.ask(host, {
 *   title,            "Stop the run?"
 *   detail,           what it does: words, or a promise of
 *                     {detail, list, ready, refused} when it must be read
 *                     first (the scratch: what it would clear)
 *   list,             lines under it
 *   yes,              "Yes, stop it"
 *   yesClass, noClass classes a page's tests and styles know its buttons by
 * }) -> a promise of true (yes) or false (Not now, Escape).
 */
(function () {
  "use strict";

  function make(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  function ask(host, options) {
    var o = options || {};
    return new Promise(function (resolve) {
      if (!host) { resolve(false); return; }
      host.hidden = false;
      host.replaceChildren();
      var box = make("div", "fmx-ask");
      box.setAttribute("role", "group");
      box.setAttribute("aria-label", o.title || "Are you sure?");
      var title = make("b", "fmx-ask-title", o.title || "Are you sure?");
      var detail = make("span", "fmx-ask-detail");
      var list = make("ul", "fmx-ask-list");
      list.hidden = true;
      // Read as one line: "Stop the run? It ends at its next frame ..."
      box.append(title, document.createTextNode(" "), detail, list);
      var answers = make("span", "fmx-ask-answers");
      var yes = make("button", "danger-btn fmx-ask-yes" + (o.yesClass ? " " + o.yesClass : ""), o.yes || "Yes");
      yes.type = "button";
      var no = make("button", "ghost-btn fmx-ask-no" + (o.noClass ? " " + o.noClass : ""), o.no || "Not now");
      no.type = "button";
      answers.append(yes, no);
      box.appendChild(answers);
      host.appendChild(box);

      function said(text, lines) {
        detail.textContent = text || "";
        list.replaceChildren();
        (lines || []).forEach(function (line) { list.appendChild(make("li", "", line)); });
        list.hidden = !(lines || []).length;
      }
      var answered = false;
      function answer(value) {
        if (answered) return;
        answered = true;
        resolve(value);
      }
      yes.addEventListener("click", function () { answer(true); });
      no.addEventListener("click", function () { answer(false); });
      box.addEventListener("keydown", function (event) {
        if (event.key === "Escape") {
          event.preventDefault();
          event.stopPropagation();
          answer(false);
        }
      });
      if (o.detail && typeof o.detail.then === "function") {
        // Read first: nothing can be said yes to until it is known.
        yes.disabled = true;
        said("Looking at what it would do…");
        o.detail.then(function (read) {
          if (answered) return;
          read = read || {};
          said(read.detail || "", read.list);
          yes.disabled = !read.ready;
          if (read.refused) yes.hidden = true;
        }, function () {
          if (answered) return;
          said("The server did not answer.");
          yes.hidden = true;
        });
      } else {
        said(o.detail || "", o.list);
      }
      // The answer that does nothing has the keyboard: Enter after a
      // command did what it asked about with no further thought.
      no.focus();
    });
  }

  /* The ask's place emptied and hidden, as before it was asked. */
  function done(host) {
    if (!host) return;
    host.replaceChildren();
    host.hidden = true;
  }

  window.FastMDXConfirm = { ask: ask, done: done };
}());
