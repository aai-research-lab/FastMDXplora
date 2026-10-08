/* The Config Builder's From a paper (fastmdxplora.paper, gui/paper_view.py):
 * a paper read by the person's AI model, its MD studies listed with how
 * each setting came from the paper's own words, one opened in the builder
 * or the chosen ones downloaded as configs. */
(function () {
  "use strict";

  const SAID = {
    ready: "Ready",
    with_differences: "Runs, with differences",
    needs_you: "Needs you",
    cannot_run: "Cannot run here",
  };
  const LABELS = {
    as_stated: "as stated",
    not_stated: "not stated",
    differs: "differs here",
    needs_you: "needs you",
    not_possible: "not possible here",
  };
  let plans = [];
  let paperTitle = "";

  function el(id) { return document.getElementById(id); }

  function node(tag, className, words) {
    const made = document.createElement(tag);
    if (className) made.className = className;
    if (words !== undefined && words !== null) made.textContent = String(words);
    return made;
  }

  function say(words) {
    const line = el("run-paper-said");
    if (line) line.textContent = words || "";
  }

  function counts(plan) {
    const tally = {};
    (plan.choices || []).forEach((choice) => { tally[choice.label] = (tally[choice.label] || 0) + 1; });
    return Object.keys(LABELS).filter((label) => tally[label])
      .map((label) => `${tally[label]} ${LABELS[label]}`).join(" · ");
  }

  function choicesTable(plan) {
    const details = node("details", "paper-choices");
    details.appendChild(node("summary", null, "How each setting came from the paper"));
    const table = node("table", "paper-choices-table");
    const head = node("tr");
    ["Setting", "", "Why", "The paper's words"].forEach((words) => head.appendChild(node("th", null, words)));
    const thead = node("thead");
    thead.appendChild(head);
    table.appendChild(thead);
    const body = node("tbody");
    (plan.choices || []).forEach((choice) => {
      const row = node("tr");
      row.dataset.label = choice.label;
      row.appendChild(node("td", "mono", choice.setting || choice.field));
      row.appendChild(node("td", `paper-label is-${choice.label}`, LABELS[choice.label] || choice.label));
      row.appendChild(node("td", null, choice.why));
      const said = node("td", "paper-quote");
      if (choice.quote) {
        said.appendChild(node("q", null, choice.quote));
        if (choice.where) said.appendChild(node("span", "muted small", ` (${choice.where})`));
      }
      row.appendChild(said);
      body.appendChild(row);
    });
    table.appendChild(body);
    details.appendChild(table);
    return details;
  }

  function render() {
    const list = el("run-paper-list");
    list.replaceChildren();
    plans.forEach((plan) => {
      const item = node("li", "paper-study");
      item.dataset.state = plan.state;
      item.dataset.study = plan.id;
      const head = node("div", "paper-study-head");
      const pick = document.createElement("input");
      pick.type = "checkbox";
      pick.className = "paper-pick";
      pick.disabled = !plan.config;
      pick.setAttribute("aria-label", `Choose ${plan.id}, ${plan.label}`);
      pick.addEventListener("change", chosenChanged);
      head.appendChild(pick);
      head.appendChild(node("span", "paper-study-id mono", plan.id));
      const what = node("span", "paper-study-label", plan.label);
      head.appendChild(what);
      if (plan.method && plan.method !== "plain") {
        head.appendChild(node("span", "paper-study-method muted small", plan.method.replace(/_/g, " ")));
      }
      head.appendChild(node("span", "paper-study-length muted small mono", plan.length || ""));
      head.appendChild(node("span", `paper-state is-${plan.state}`, SAID[plan.state] || plan.state));
      item.appendChild(head);
      const facts = node("p", "paper-study-facts muted small", counts(plan));
      const claims = (plan.claims || []).length;
      if (claims) facts.textContent += ` · reports ${claims} result${claims === 1 ? "" : "s"} to compare`;
      item.appendChild(facts);
      const needs = (plan.choices || []).filter((choice) => choice.label === "needs_you" || choice.label === "not_possible");
      if (needs.length) {
        const ul = node("ul", "paper-needs");
        needs.forEach((choice) => ul.appendChild(node("li", `is-${choice.label}`, choice.why)));
        item.appendChild(ul);
      }
      item.appendChild(choicesTable(plan));
      if (plan.config) {
        // An everyday button, so its icon, named on hover and to a screen
        // reader, as the Agent card's Open in the builder is.
        const label = `Open ${plan.id} in the builder`;
        const open = window.FastMDXIcons
          ? window.FastMDXIcons.button("config", label, "paper-open")
          : node("button", "ghost-btn paper-open", label);
        open.type = "button";
        open.addEventListener("click", () => openInTheBuilder(plan));
        item.appendChild(open);
      }
      list.appendChild(item);
    });
    el("run-paper-studies").hidden = !plans.length;
    chosenChanged();
  }

  function chosen() {
    const picked = new Set(Array.from(document.querySelectorAll("#run-paper-list .paper-study"))
      .filter((item) => item.querySelector(".paper-pick").checked).map((item) => item.dataset.study));
    return plans.filter((plan) => picked.has(plan.id));
  }

  function chosenChanged() {
    const button = el("run-paper-download");
    const count = chosen().length;
    button.disabled = !count;
    button.textContent = count > 1 ? `Download the ${count} chosen configs` : "Download the chosen config";
  }

  async function read(event) {
    event.preventDefault();
    const source = el("run-paper-source").value.trim();
    if (!source) { el("run-paper-source").focus(); return; }
    const si = el("run-paper-si").value.trim();
    const go = el("run-paper-read");
    go.disabled = true;
    say("Reading the paper. Your AI model is asked a few times, which can take a minute or two.");
    try {
      const response = await fetch("/api/paper/read", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source, si: si ? [si] : [], until_determined: el("run-paper-until").checked }),
      });
      const answer = await response.json();
      if (!answer.ok) {
        say(answer.error || "The paper could not be read.");
        return;
      }
      plans = answer.plans || [];
      paperTitle = answer.title || "";
      const title = el("run-paper-title");
      title.replaceChildren();
      title.appendChild(node("strong", null, paperTitle));
      if (answer.doi) title.appendChild(node("span", "muted small mono", ` doi:${answer.doi}`));
      title.appendChild(node("span", "muted small",
        ` · read by ${answer.model || "your AI model"}${answer.left_out && answer.left_out.length ? `; too long to read whole, left out ${answer.left_out.slice(0, 4).join(", ")}` : ""}`));
      say(`${plans.length} MD stud${plans.length === 1 ? "y" : "ies"} found. Choose one to open in the builder, or several to download.`);
      render();
    } catch (error) {
      say("The server did not answer.");
    } finally {
      go.disabled = false;
    }
  }

  async function openInTheBuilder(plan) {
    const builder = window.FastMDXRun;
    if (!builder) return;
    // Opened without what it still needs, which the opening's check would
    // refuse, and given it back once open: the builder then says the study
    // will not run, and why, until it is supplied.
    const config = JSON.parse(JSON.stringify(plan.config));
    const stillNeeds = config.paper && config.paper.needs;
    if (config.paper) delete config.paper.needs;
    const response = await fetch("/api/load-config", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ config }),
    });
    const loaded = await response.json();
    if (!loaded || loaded.ok === false) {
      say((loaded && loaded.error) || "The study could not be opened in the builder.");
      return;
    }
    await builder.applyLoadedState(loaded.state || loaded, {
      note: `Opened ${plan.id} of ${paperTitle || "the paper"}. Each setting's reason is the paper's words.`,
    });
    const needs = (plan.choices || []).filter((choice) => choice.label === "needs_you");
    if (stillNeeds && stillNeeds.length) {
      const options = builder.state.values[builder.RUN_OPTIONS_KEY] || {};
      if (options.paper && typeof options.paper === "object") {
        options.paper.needs = stillNeeds;
        builder.renderAll();
        builder.saveDraft();
      }
    }
    if (needs.length) {
      say(`${plan.id} opened. It will not run until what it needs is supplied: ${needs.map((c) => c.field.replace(/_/g, " ")).join(", ")}.`);
      offerSupplied(plan);
    } else {
      say(`${plan.id} opened in the builder.`);
    }
  }

  /* The settings a study needed that are still as it was opened: each
   * needs_you choice that names where it is set, compared with the builder. */
  function stillAsOpened(plan) {
    const builder = window.FastMDXRun;
    const plain = (v) => (v === undefined || v === null || v === "") ? ""
      : (typeof v === "object" ? JSON.stringify(v) : String(v));
    const out = [];
    for (const choice of plan.choices || []) {
      if (choice.label !== "needs_you" || !choice.setting) continue;
      let now;
      let then;
      if (choice.setting === "systems") {
        now = ((builder.state.systems || [])[0] || {}).system;
        then = (((plan.config || {}).systems || [])[0] || {}).system;
      } else {
        const [phase, ...rest] = choice.setting.split(".");
        const name = rest.join(".");
        now = ((builder.state.values || {})[phase] || {})[name];
        then = ((plan.config || {})[phase] || {})[name];
      }
      if (plain(now) === plain(then) && !out.includes(choice.setting)) out.push(choice.setting);
    }
    return out;
  }

  /* What a study still needed is said to be supplied: the record that holds
   * it back is cleared, and the builder checks the study again. A setting
   * still as the study was opened is named first, and is said to be
   * supplied only by pressing again. */
  function offerSupplied(plan) {
    const builder = window.FastMDXRun;
    const host = el("run-paper-studies");
    let button = el("run-paper-supplied");
    if (!button) {
      button = node("button", "ghost-btn", "");
      button.type = "button";
      button.id = "run-paper-supplied";
      host.appendChild(button);
    }
    button.hidden = false;
    button.textContent = `I have supplied what ${plan.id} needs`;
    let warned = "";
    button.onclick = () => {
      const unchanged = stillAsOpened(plan);
      const key = unchanged.join(",");
      if (unchanged.length && warned !== key) {
        warned = key;
        button.textContent = `Yes, ${plan.id} has what it needs`;
        say(`Still as the paper left them: ${unchanged.join(", ")}. Set each in the builder, `
          + "or press again if what you supplied is right as it is.");
        return;
      }
      const options = builder.state.values[builder.RUN_OPTIONS_KEY] || {};
      if (options.paper && typeof options.paper === "object") delete options.paper.needs;
      builder.renderAll();
      builder.saveDraft();
      button.hidden = true;
      say(`${plan.id} is checked again as it stands.`);
    };
  }

  async function download() {
    const picked = chosen();
    if (!picked.length) return;
    const response = await fetch("/api/paper/download", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ configs: picked.map((plan) => ({
        id: plan.id, label: plan.label, state: plan.state, choices: plan.choices, config: plan.config })) }),
    });
    if (!response.ok) {
      let why = "";
      try { why = (await response.json()).error || ""; } catch (error) { why = ""; }
      say(why || "The configs could not be made.");
      return;
    }
    const blob = await response.blob();
    const named = /filename="([^"]+)"/.exec(response.headers.get("Content-Disposition") || "");
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = named ? named[1] : "paper-studies.zip";
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
  }

  function attach() {
    const toggle = el("run-paper-toggle");
    const card = el("run-paper-card");
    if (!toggle || !card) return;
    toggle.addEventListener("click", () => {
      card.hidden = !card.hidden;
      toggle.setAttribute("aria-expanded", String(!card.hidden));
      if (!card.hidden) el("run-paper-source").focus();
    });
    card.addEventListener("submit", read);
    el("run-paper-download").addEventListener("click", download);
    el("run-paper-all").addEventListener("click", () => {
      document.querySelectorAll("#run-paper-list .paper-pick").forEach((box) => {
        if (!box.disabled) box.checked = true;
      });
      chosenChanged();
    });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", attach);
  else attach();

  window.FastMDXPaper = { render, plansShown: () => plans };
})();
