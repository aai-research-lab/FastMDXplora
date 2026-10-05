/* The scheme before the first paint, as frame.js chooses it: Light or
 * Dark where one was chosen (Graphite and Ink, chosen before, are Dark;
 * Paper is Light), otherwise the computer's. Loaded in the page's head, so
 * a light computer's page does not open dark and turn light. */
(function () {
  var kept = null, former = { graphite: "dark", ink: "dark", paper: "light" };
  try { kept = localStorage.getItem("fmx.theme"); } catch (e) { /* private mode */ }
  kept = former[kept] || kept;
  if (kept !== "light" && kept !== "dark") {
    try { kept = window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark"; }
    catch (e) { kept = "dark"; }
  }
  document.documentElement.dataset.theme = kept;
})();
