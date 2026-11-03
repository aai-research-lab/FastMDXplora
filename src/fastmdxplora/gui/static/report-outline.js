/* The report's outline beside it, as an editor's outline beside a file:
 * its sections numbered as the PDF numbers them (2, 2.1), the one being
 * read marked, each a way to it. And each number the report states, on a
 * click, the record it was traced to (report_numbers.py): the file, the
 * place in it and the value as recorded; or the records that could each
 * hold it, none picked; or that no record was traced; and a record it was
 * traced to that holds another value since the report was first read. A record not read (linked out of the study) is named: a number
 * not traced may be one of its, so it is said as not traced, never as one
 * no record holds.
 */
(function () {
  "use strict";

  function byId(id) { return document.getElementById(id); }

  function make(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  var numbers = [];
  var changed = [];
  var headings = [];
  var card = null;

  /* Each h2 and h3 numbered: 1, 2, 2.1, as the PDF's stylesheet counts
   * them (report/pdf.py). The title is not a section. */
  function number(doc) {
    var major = 0, minor = 0;
    headings = [];
    Array.prototype.forEach.call(doc.querySelectorAll("h2, h3"), function (heading, i) {
      if (heading.tagName === "H2") { major += 1; minor = 0; } else { minor += 1; }
      var said = heading.tagName === "H2" ? String(major) : major + "." + minor;
      heading.dataset.section = said;
      if (!heading.id) heading.id = "report-section-" + said.replace(".", "-");
      headings.push({ node: heading, said: said, level: heading.tagName === "H2" ? 2 : 3,
                      words: heading.textContent.replace(/\s+/g, " ").trim(), at: i });
    });
  }

  /* The section gone to from the outline: kept marked while its heading
   * is in sight, though the report's end cannot scroll it to the top. */
  var chosen = null;

  /* The bars held at the top of what scrolls (the page's title bar) are
   * not where a heading gone to may sit: it stops below them. */
  function scroller(node) {
    for (var n = node.parentElement; n; n = n.parentElement) {
      var y = getComputedStyle(n).overflowY;
      if ((y === "auto" || y === "scroll") && n.scrollHeight > n.clientHeight) return n;
    }
    return null;
  }

  function go(heading) {
    chosen = heading;
    var header = document.querySelector('.page[data-page="report"] .page-header');
    var box = scroller(heading);
    if (header && getComputedStyle(header).position === "sticky") {
      var top = box ? box.getBoundingClientRect().top : 0;
      heading.style.scrollMarginTop = Math.max(0, header.getBoundingClientRect().bottom - top + 8) + "px";
    }
    heading.scrollIntoView({ block: "start" });
    mark(heading);
  }

  function mark(current) {
    var outline = byId("report-outline");
    if (!outline) return;
    Array.prototype.forEach.call(outline.querySelectorAll(".outline-item"), function (item) {
      var here = current && item.dataset.target === current.id;
      if (here) item.setAttribute("aria-current", "location");
      else item.removeAttribute("aria-current");
    });
  }

  /* The section being read: the last heading in the top third of the
   * window or above it. */
  var asked = false;
  function follow() {
    if (asked) return;
    asked = true;
    requestAnimationFrame(function () {
      asked = false;
      var doc = byId("report-document");
      var page = document.querySelector('.page[data-page="report"]');
      if (!doc || doc.hidden || !page || page.hidden || !headings.length) return;
      if (chosen) {
        var top = chosen.getBoundingClientRect().top;
        if (top >= 0 && top < window.innerHeight - 40) { mark(chosen); return; }
        chosen = null;
      }
      var reading = null;
      var line = Math.max(140, window.innerHeight / 3);
      headings.forEach(function (h) {
        if (h.node.getBoundingClientRect().top <= line) reading = h.node;
      });
      mark(reading || headings[0].node);
    });
  }
  document.addEventListener("scroll", follow, true);

  /* What became of each number: traced to one record; held by more than
   * one (the candidates said, none picked); not traced where a record was
   * not read; or no record traced. */
  function stateOf(n) {
    if (n.traced) return "traced";
    if (n.candidates && n.candidates.length) return "several";
    if (n.not_read && n.not_read.length) return "unread";
    return "none";
  }

  function counts() {
    var by = { traced: 0, several: 0, unread: 0, none: 0 };
    numbers.forEach(function (n) { by[stateOf(n)] += 1; });
    // Marked: the numbers no record was traced to by these rules, and those
    // more than one holds; not those of a record not read.
    by.marked = by.none + by.several;
    return by;
  }

  /* The records not read, which a number not traced may come from. */
  function unread() {
    var names = [];
    numbers.forEach(function (n) {
      (n.not_read || []).forEach(function (name) {
        if (names.indexOf(name) < 0) names.push(name);
      });
    });
    return names;
  }

  function wereNotRead(names) {
    return names.join(", ") + (names.length === 1 ? " was" : " were") + " not read";
  }

  function render(doc, data) {
    var outline = byId("report-outline");
    numbers = (data && data.numbers) || [];
    changed = (data && data.records_changed) || [];
    close();
    if (doc) {
      Array.prototype.forEach.call(doc.querySelectorAll(".report-number"), function (span) {
        var found = numbers[Number(span.dataset.number)];
        if (found) span.dataset.state = stateOf(found);
      });
    }
    if (!outline) return;
    outline.replaceChildren();
    if (!doc || doc.hidden) { outline.hidden = true; return; }
    number(doc);
    outline.hidden = !headings.length;
    if (!headings.length) return;

    var toggle = make("button", "outline-toggle", "Outline");
    toggle.type = "button";
    toggle.setAttribute("aria-expanded", "false");
    toggle.appendChild(make("span", "muted", " · " + headings.filter(function (h) {
      return h.level === 2;
    }).length + " sections"));
    toggle.addEventListener("click", function () {
      var open = outline.dataset.open !== "true";
      outline.dataset.open = String(open);
      toggle.setAttribute("aria-expanded", String(open));
    });
    outline.appendChild(toggle);

    var body = make("div", "outline-body");
    body.appendChild(make("div", "outline-title", "Outline"));
    var list = make("ol", "outline-list");
    var under = list;
    headings.forEach(function (h) {
      var li = make("li", "outline-entry outline-level-" + h.level);
      var item = make("button", "outline-item");
      item.type = "button";
      item.dataset.target = h.node.id;
      item.appendChild(make("span", "outline-number", h.said));
      item.appendChild(make("span", "outline-words", h.words));
      item.title = h.said + " " + h.words;
      item.addEventListener("click", function () {
        go(h.node);
        if (outline.dataset.open === "true") toggle.click();
        // Focus goes to the section, so the keyboard reads on from there
        // (the folded outline it came from is gone).
        if (!h.node.hasAttribute("tabindex")) h.node.setAttribute("tabindex", "-1");
        h.node.focus({ preventScroll: true });
      });
      li.appendChild(item);
      if (h.level === 2) {
        list.appendChild(li);
        under = make("ol", "outline-list outline-sub");
        li.appendChild(under);
      } else {
        under.appendChild(li);
      }
    });
    Array.prototype.forEach.call(list.querySelectorAll(".outline-sub"), function (sub) {
      if (!sub.childElementCount) sub.remove();
    });
    body.appendChild(list);
    body.appendChild(said(doc));
    outline.appendChild(body);
    follow();
  }

  /* What the report's numbers trace to, in a line under the outline. */
  function said(doc) {
    var box = make("div", "outline-numbers");
    box.id = "report-numbers-said";
    if (!numbers.length) {
      delete doc.dataset.marked;
      box.appendChild(make("p", "muted", "No number in it to trace to a record."));
      return box;
    }
    var n = counts();
    box.appendChild(make("p", "", n.traced + " of " + numbers.length + " numbers traced to a record" +
      (n.several ? ", " + n.several + " held by more than one" : "") + "; click one for it."));
    var notRead = unread();
    if (notRead.length) {
      box.appendChild(make("p", "outline-changed", wereNotRead(notRead) + ", so " + n.unread +
        (n.unread === 1 ? " number is" : " numbers are") + " not traced: " +
        (notRead.length === 1 ? "it" : "they") + " may hold " + (n.unread === 1 ? "it" : "them") + "."));
    }
    // Kept as it was when the report is drawn again, the button built
    // from it.
    if (!n.marked) delete doc.dataset.marked;
    if (n.marked) {
      var on = doc.dataset.marked === "true";
      var words = function (pressed) {
        return pressed ? "Unmark them" : "Mark the " + n.marked + " not traced";
      };
      var show = make("button", "plot-tool outline-mark", words(on));
      show.type = "button";
      show.setAttribute("aria-pressed", String(on));
      show.title = "Mark each number no record was traced to, or more than one record holds" +
        (n.unread ? " (not those of a record not read)" : "");
      show.addEventListener("click", function () {
        var pressed = doc.dataset.marked !== "true";
        doc.dataset.marked = String(pressed);
        show.setAttribute("aria-pressed", String(pressed));
        show.textContent = words(pressed);
        if (pressed) {
          var first = doc.querySelector('.report-number[data-state="none"], .report-number[data-state="several"]');
          if (first) first.scrollIntoView({ block: "center" });
        }
      });
      box.appendChild(show);
    }
    if (changed.length) {
      box.appendChild(make("p", "outline-changed",
        changed.length + (changed.length === 1 ? " record" : " records") +
        " changed since this report was first read here: " + changed.join(", ") +
        ". Write it again to have it say what " + (changed.length === 1 ? "it holds" : "they hold") +
        " now."));
    }
    return box;
  }

  function close() {
    if (card) { card.remove(); card = null; }
    Array.prototype.forEach.call(document.querySelectorAll('.report-number[aria-expanded="true"]'),
      function (n) { n.setAttribute("aria-expanded", "false"); });
  }

  function plain(value) {
    var x = Number(value);
    if (!isFinite(x)) return String(value);
    return String(+x.toPrecision(10));
  }

  /* The record's place, as a path into the file: findings.mean.mean. */
  function where(record) {
    var line = make("div", "number-where");
    line.appendChild(make("code", "", record.file));
    line.appendChild(document.createTextNode(" "));
    line.appendChild(make("code", "", record.key + " = " + plain(record.value) +
      (record.unit ? " " + record.unit : "")));
    return line;
  }

  /* The record opened in the side panel; the card closes, its number
   * keeping the keyboard, as the panel it opens narrows the report. */
  function opener(file, words, span) {
    if (!(window.FastMDXFrame && window.FastMDXFrame.previewPath)) return null;
    var show = make("button", "plot-tool", words);
    show.type = "button";
    show.addEventListener("click", function () {
      close();
      span.focus({ preventScroll: true });
      window.FastMDXFrame.previewPath(file);
    });
    return show;
  }

  function open(span) {
    var found = numbers[Number(span.dataset.number)];
    if (!found) return;
    var wasOpen = span.getAttribute("aria-expanded") === "true";
    close();
    if (wasOpen) return;
    var doc = byId("report-document");
    card = make("div", "report-number-card");
    card.setAttribute("role", "dialog");
    card.setAttribute("aria-label", "Where " + found.said + " comes from");
    card.tabIndex = -1;
    var head = make("div", "number-head");
    head.appendChild(make("b", "", found.said));
    var shut = make("button", "line-btn number-close", "×");
    shut.type = "button";
    shut.setAttribute("aria-label", "Close");
    shut.addEventListener("click", function () { close(); span.focus(); });
    head.appendChild(shut);
    card.appendChild(head);
    var state = stateOf(found);
    card.dataset.traced = String(state === "traced");
    card.dataset.state = state;
    if (state === "traced") {
      var record = found.record;
      card.appendChild(make("p", "", "From the study's record:"));
      card.appendChild(where(record));
      if (record.unit && record.in && record.unit !== record.in) {
        card.appendChild(make("p", "muted", "That is " + plain(record.as_said) + " " + record.in +
          ", as the report gives it."));
      }
      if (found.also && found.also.length) {
        card.appendChild(make("p", "muted", "The same setting is saved in " + found.also.join(", ") + "."));
      }
      var one = opener(record.file, "Open the record", span);
      if (one) card.appendChild(one);
    } else if (state === "several") {
      var many = found.candidates_count || found.candidates.length;
      card.appendChild(make("p", "", "Held by " + many + " records; which one the report took is not " +
        "said, so none is picked:"));
      found.candidates.forEach(function (candidate) {
        card.appendChild(where(candidate));
        var each = opener(candidate.file, "Open " + candidate.file.split("/").slice(-3).join("/"), span);
        if (each) card.appendChild(each);
      });
    } else if (state === "unread") {
      card.appendChild(make("p", "", "Not traced: " + wereNotRead(found.not_read) +
        ", so whether it holds it is not known."));
    } else {
      card.appendChild(make("p", "", "No record traced."));
      card.appendChild(make("p", "muted",
        "A record is taken only where its unit, or what it counts, and the words beside the number " +
        "say it is that one. A threshold of the method, a citation, or a number the report works " +
        "out from more than one record has none."));
    }
    // A record its number was traced to when the report was first read
    // here, holding another value now.
    if (found.changed) {
      var was = found.changed;
      card.appendChild(make("p", "number-stale", "When this report was first read here, " + was.file +
        " held " + was.key + " = " + plain(was.then) + (was.now === null || was.now === undefined
          ? "; it holds no such value now." : "; it holds " + plain(was.now) + " now.") +
        " Write it again to have the report say what the records hold now."));
    }
    // Right after its number, so the keyboard comes to it next; placed by
    // the report, where it is drawn above the text.
    span.after(card);
    var box = doc.getBoundingClientRect(), at = span.getBoundingClientRect();
    var left = Math.max(8, Math.min(at.left - box.left, box.width - card.offsetWidth - 8));
    card.style.left = left + "px";
    card.style.top = (at.bottom - box.top + 6) + "px";
    span.setAttribute("aria-expanded", "true");
    card.scrollIntoView({ block: "nearest", behavior: "instant" });
    card.focus({ preventScroll: true });
    // Tab past its last button closes it, as a menu closes.
    card.addEventListener("focusout", function (event) {
      var to = event.relatedTarget;
      if (to && card && !card.contains(to)) close();
    });
  }

  document.addEventListener("click", function (event) {
    var span = event.target.closest && event.target.closest(".report-number");
    if (span && span.closest("#report-document")) { open(span); return; }
    if (card && !card.contains(event.target)) close();
  });
  document.addEventListener("keydown", function (event) {
    var span = event.target.closest && event.target.closest(".report-number");
    if (span && (event.key === "Enter" || event.key === " ")) {
      event.preventDefault();
      open(span);
    } else if (event.key === "Escape" && card) {
      var from = document.querySelector('.report-number[aria-expanded="true"]');
      close();
      if (from) from.focus();
    }
  });

  window.FastMDXReportOutline = { render: render, close: close };
}());
