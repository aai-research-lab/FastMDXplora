/* FastMDXplora Live Dashboard — dependency-free canvas charts. */

(function () {
  "use strict";

  /* Colours are the theme's, read when a chart is drawn: fixed to the dark
     scheme's, the axis labels and the pale series vanished on Paper. */
  const FALLBACK = {
    cyan: "#2698ba",
    orange: "#ffb86b",
    violet: "#a78bfa",
    silver: "#d8d8dd",
    green: "#67e8a3",
    grid: "rgba(255, 255, 255, 0.06)",
    axis: "#85858f",
  };
  const TOKENS = {
    cyan: "--accent-cyan", orange: "--accent-orange", violet: "--accent-violet",
    silver: "--accent-silver", green: "--accent-green",
    grid: "--border-subtle", axis: "--text-muted",
  };
  function color(name) {
    const token = TOKENS[name];
    const value = token
      ? getComputedStyle(document.documentElement).getPropertyValue(token).trim()
      : "";
    return value || FALLBACK[name] || name;
  }

  const CONFIG = [
    {key: "potential_energy", label: "Potential energy", unit: "kJ/mol", color: "cyan"},
    // Recorded since the beginning -- METRIC_FIELDS carries it and OpenMM's
    // energy.csv is mapped to it -- but never drawn. Potential energy alone
    // does not show whether the integration is holding; drift in the total
    // is the thing that does.
    {key: "total_energy", label: "Total energy", unit: "kJ/mol", color: "green"},
    {key: "temperature", label: "Temperature", unit: "K", color: "orange"},
    {key: "density", label: "Density", unit: "g/mL", color: "violet"},
    {key: "speed", label: "Simulation speed", unit: "ns/day", color: "silver"},
  ];

  const states = new Map();

  /* The number and its trend, together. CONFIG is the single register of
     what a metric is called and what it is measured in, so a series cannot
     be plotted without a current value or given one without a plot. Once
     the run has ended, the number is the production's mean, taken as the
     analyses take theirs (overview_view.py), with its error; while it runs,
     the newest sample. */
  function renderLatest(metrics) {
    const latest = metrics && metrics.length ? metrics[metrics.length - 1] : null;
    const means = clock.means || {};
    const over = window.FastMDXOverview && window.FastMDXOverview.ended();
    CONFIG.forEach((config) => {
      const target = document.querySelector(`[data-chart-value="${config.key}"]`);
      const note = document.querySelector(`[data-chart-note="${config.key}"]`);
      if (!target) return;
      const mean = over ? means[config.key] : null;
      if (mean) {
        // No unit here either: the title carries it.
        target.textContent = mean.number;
        if (note) {
          note.textContent = mean.determined
            ? `production mean, ${Math.round(mean.samples)} independent samples`
            : "production mean, not determined";
          note.title = mean.determined ? "" : (mean.why || "");
        }
        return;
      }
      // An empty cell is a gap, not a zero: Number("") is 0 and finite, so
      // a metric the run has not sampled would have read 0.0000.
      const cell = latest ? latest[config.key] : null;
      let raw = cell === "" || cell == null ? NaN : Number(cell);
      // A speed of 0 is none recorded (a run that failed before it took a
      // step): "0.0000" beside "no speed has been measured here".
      if (config.key === "speed" && raw === 0) raw = NaN;
      // No unit here -- the title beside it already carries one, and the row
      // read "Potential energy (kJ/mol)  -506551 kJ/mol".
      target.textContent = Number.isFinite(raw) ? formatValue(raw) : "—";
      if (note) {
        note.textContent = Number.isFinite(raw) && clock.timed ? "the newest sample" : "";
        note.title = "";
      }
    });
  }

  function formatValue(value) {
    const magnitude = Math.abs(value);
    // Grouped, because -506551 is read one digit at a time and -506,551 is
    // read at a glance -- which is the whole point of a number on a chart.
    if (magnitude >= 10000) return value.toLocaleString(undefined, {maximumFractionDigits: 0});
    if (magnitude >= 100) return value.toFixed(2);
    if (magnitude >= 1) return value.toFixed(3);
    return value.toFixed(4);
  }
  let lastMetrics = [];
  let resizeObserver = null;
  /* The production's clock: where production began on the simulation's
   * clock (ns), so every plot reads time from it, equilibration before 0;
   * the production means and the temperature asked for. From
   * /api/overview, by way of overview.js. */
  const clock = {startNs: null, means: {}, target: null, nptFrom: null, timed: false};
  /* The moment every plot marks, on the production's clock (ns), or the
   * sample pointed at where there is no clock. */
  let crosshair = null;
  let crossFrame = 0;

  document.addEventListener("DOMContentLoaded", init);

  function init() {
    document.querySelectorAll(".chart-canvas").forEach((canvas) => {
      const key = canvas.getAttribute("data-chart");
      const ctx = canvas.getContext("2d");
      if (!key || !ctx) return;
      states.set(key, {
        canvas,
        ctx,
        points: [],
        needsDraw: true,
      });
    });

    if (window.ResizeObserver) {
      resizeObserver = new ResizeObserver(() => drawAll());
      states.forEach((entry) => resizeObserver.observe(entry.canvas));
    }
    window.addEventListener("resize", drawAll);
    document.addEventListener("fmx:theme", drawAll);
    window.addEventListener("dashboard:live-page-opened", () => {
      requestAnimationFrame(() => requestAnimationFrame(drawAll));
    });
    window.addEventListener("fmx:overview", (event) => {
      const thermo = event.detail && event.detail.thermodynamics;
      clock.startNs = thermo && Number.isFinite(thermo.production_start_ns)
        ? thermo.production_start_ns : null;
      clock.means = (thermo && thermo.means) || {};
      clock.target = thermo && Number.isFinite(thermo.target_temperature_K)
        ? thermo.target_temperature_K : null;
      clock.nptFrom = thermo && Number.isFinite(thermo.npt_from_ns) ? thermo.npt_from_ns : null;
      update(lastMetrics);
    });
    window.addEventListener("fmx:crosshair", (event) => {
      crosshair = event.detail ? event.detail.t_ns : null;
      if (crossFrame) return;
      crossFrame = requestAnimationFrame(() => { crossFrame = 0; drawAll(); });
    });
    states.forEach((entry) => {
      entry.canvas.addEventListener("mousemove", (event) => pointAt(entry, event));
      entry.canvas.addEventListener("mouseleave", () => point(null));
    });
    wireChartControls();
  }

  function wireChartControls() {
    const select = document.getElementById("chart-metric-select");
    if (select && !select.options.length) {
      const all = document.createElement("option");
      all.value = "all";
      all.textContent = "All metrics";
      select.appendChild(all);
      CONFIG.forEach((config) => {
        const option = document.createElement("option");
        option.value = config.key;
        option.textContent = config.label;
        select.appendChild(option);
      });
    }
    select?.addEventListener("change", () => {
      const selected = select.value;
      document.querySelectorAll(".chart-row").forEach((row) => {
        const key = row.querySelector(".chart-canvas")?.getAttribute("data-chart");
        row.hidden = selected !== "all" && key !== selected;
      });
      requestAnimationFrame(drawAll);
    });
    document.getElementById("chart-reset")?.addEventListener("click", () => {
      if (select) select.value = "all";
      document.querySelectorAll(".chart-row").forEach((row) => { row.hidden = false; });
      requestAnimationFrame(drawAll);
    });
  }

  function update(metrics) {
    lastMetrics = Array.isArray(metrics) ? metrics : [];
    // On the production's clock where the record has the simulation's time
    // and the production's start is known; by step otherwise, as before.
    const times = lastMetrics.map((row) => numberOrNaN(row.simulation_time_ns));
    clock.timed = clock.startNs != null && times.length > 0 && times.every(Number.isFinite);
    CONFIG.forEach((config) => {
      const entry = states.get(config.key);
      if (!entry) return;
      entry.points = lastMetrics
        .map((row, index) => {
          let value = numberOrNaN(row[config.key]);
          // A speed of 0 is none recorded, as its empty cell says: not a
          // point at 0 on an axis of 1.00e-6.
          if (config.key === "speed" && value === 0) value = NaN;
          const step = Number(row.step);
          return {
            x: clock.timed ? times[index] - clock.startNs : (Number.isFinite(step) ? step : index),
            y: value,
            stage: String(row.stage || "").toLowerCase(),
          };
        })
        .filter((point) => Number.isFinite(point.y));
      entry.needsDraw = true;
    });
    renderLatest(lastMetrics);
    updateEmptyState();
    const key = document.getElementById("chart-key");
    if (key) key.hidden = !clock.timed;
    drawAll();
  }

  /* An empty cell is a gap, not a zero. */
  function numberOrNaN(cell) {
    return cell === "" || cell == null ? NaN : Number(cell);
  }

  function pointAt(entry, event) {
    const bounds = entry.bounds;
    if (!bounds) return;
    const rect = entry.canvas.getBoundingClientRect();
    const area = plotArea(rect);
    const fraction = (event.clientX - rect.left - area.left) / area.width;
    // Only a time is pointed at: a record without one is plotted by step.
    if (!clock.timed || fraction < 0 || fraction > 1) { point(null); return; }
    point(bounds.minX + fraction * (bounds.maxX - bounds.minX));
  }

  /* Every plot on the page marks the moment pointed at (overview.js plots
   * the analyses' series by the same event). */
  function point(x) {
    window.dispatchEvent(new CustomEvent("fmx:crosshair", {detail: {t_ns: x}}));
  }

  function updateEmptyState() {
    const empty = document.getElementById("chart-empty");
    if (!empty) return;
    const anyData = Array.from(states.values()).some((entry) => entry.points.length > 0);
    empty.style.display = anyData ? "none" : "block";
    if (!anyData) {
      // A run that has ended will sample nothing more: said "will appear"
      // of one stopped in minimisation.
      const over = window.FastMDXOverview && window.FastMDXOverview.ended();
      empty.textContent = lastMetrics.length
        ? "Telemetry samples exist, but none contain chartable values."
        : over ? "The run recorded no sample before it ended."
          : "Live telemetry will appear after the first sample.";
    }
  }

  function drawAll() {
    CONFIG.forEach((config) => {
      const entry = states.get(config.key);
      if (entry) drawChart(entry, config);
    });
  }

  function drawChart(entry, config) {
    const canvas = entry.canvas;
    if (!canvas.isConnected || canvas.closest("[hidden]")) {
      entry.needsDraw = true;
      return;
    }
    const rect = canvas.getBoundingClientRect();
    if (rect.width < 40 || rect.height < 40) {
      entry.needsDraw = true;
      return;
    }

    const dpr = Math.max(1, window.devicePixelRatio || 1);
    const pixelWidth = Math.max(1, Math.round(rect.width * dpr));
    const pixelHeight = Math.max(1, Math.round(rect.height * dpr));
    if (canvas.width !== pixelWidth || canvas.height !== pixelHeight) {
      canvas.width = pixelWidth;
      canvas.height = pixelHeight;
    }

    const ctx = entry.ctx;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, rect.width, rect.height);
    drawBackground(ctx, rect);

    const points = entry.points;
    if (!points.length) {
      drawGrid(ctx, rect);
      ctx.fillStyle = color("axis");
      ctx.font = `12px ${monoFont()}`;
      ctx.textAlign = "center";
      ctx.fillText("no data yet", rect.width / 2, rect.height / 2);
      entry.needsDraw = false;
      return;
    }

    // The value axis fits the production, where there is one: the
    // minimised start, thousands of kJ/mol below, flattened the whole run
    // into a line. What lies beyond it is marked at the edge.
    const production = clock.timed ? points.filter((point) => point.x >= 0) : [];
    const fitted = production.length >= 2 ? production : points;
    const fit = fitted.map((point) => point.y);
    // The temperature asked for is on the axis, so a thermostat that is
    // off is seen to be.
    if (config.key === "temperature" && clock.target != null) fit.push(clock.target);
    const {minY, maxY} = valueBounds(fit);
    let minX = Math.min(...points.map((point) => point.x));
    let maxX = Math.max(...points.map((point) => point.x));
    // One sample, or every sample at one moment, spans nothing: the axis
    // reaches to where production began (0), or 1 ps past it, so its
    // ticks have room. Each tick was put where a 1 ns span would put it,
    // all at one place ("04ps").
    if (!(maxX > minX)) {
      if (clock.timed && minX !== 0) { minX = Math.min(minX, 0); maxX = Math.max(maxX, 0); }
      else { minX -= clock.timed ? 0 : 1; maxX += clock.timed ? 0.001 : 1; }
    }
    const bounds = {minY, maxY, minX, maxX};
    entry.bounds = bounds;

    if (clock.timed) drawEquilibration(ctx, rect, bounds, points);
    drawThresholds(ctx, config, rect, bounds);
    drawGrid(ctx, rect);
    drawSeries(ctx, rect, bounds, points, color(config.color));
    drawLabels(ctx, rect, bounds, points.length, config.unit);
    drawCrosshair(ctx, rect, bounds, points, config);
    entry.needsDraw = false;
  }

  /* The equilibration, before 0 on the production's clock, shaded, with
   * where NVT gave way to NPT. */
  function drawEquilibration(ctx, rect, bounds, points) {
    if (bounds.minX >= 0) return;
    const area = plotArea(rect);
    const x = (value) => area.left + area.width * ((value - bounds.minX) / ((bounds.maxX - bounds.minX) || 1));
    ctx.fillStyle = hexToRgba(color("axis"), 0.12);
    ctx.fillRect(area.left, area.top, Math.max(0, x(Math.min(0, bounds.maxX)) - area.left), area.height);
    // Where the run changed NVT for NPT, from the lengths it planned; the
    // first NPT sample came a sampling interval or so after it.
    let change = clock.nptFrom;
    for (let i = 1; change == null && i < points.length; i += 1) {
      const was = points[i - 1].stage, now = points[i].stage;
      if (points[i].x < 0 && was.startsWith("nvt") && now.startsWith("npt")) change = points[i].x;
    }
    bounds.npt = null;
    // Only where the run got there: one that failed in NVT never changed.
    const reached = change != null && points.some((point) => point.x >= change);
    if (reached && change > bounds.minX && change < Math.min(0, bounds.maxX)) {
      bounds.npt = change;
      ctx.strokeStyle = color("grid");
      ctx.setLineDash([3, 3]);
      ctx.beginPath();
      ctx.moveTo(x(change), area.top);
      ctx.lineTo(x(change), area.bottom);
      ctx.stroke();
      ctx.setLineDash([]);
    }
  }

  /* The moment pointed at, here: a line, the nearest sample and its value. */
  function drawCrosshair(ctx, rect, bounds, points, config) {
    if (crosshair == null || crosshair < bounds.minX || crosshair > bounds.maxX) return;
    const area = plotArea(rect);
    const spanX = bounds.maxX - bounds.minX || 1;
    const spanY = bounds.maxY - bounds.minY || 1;
    let near = points[0];
    for (const p of points) if (Math.abs(p.x - crosshair) < Math.abs(near.x - crosshair)) near = p;
    const x = area.left + area.width * ((near.x - bounds.minX) / spanX);
    const y = Math.max(area.top, Math.min(area.bottom,
      area.bottom - area.height * ((near.y - bounds.minY) / spanY)));
    ctx.strokeStyle = color("axis");
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(x, area.top);
    ctx.lineTo(x, area.bottom);
    ctx.stroke();
    ctx.fillStyle = color(config.color);
    ctx.beginPath();
    ctx.arc(x, y, 3, 0, Math.PI * 2);
    ctx.fill();
    const said = `${clock.timed ? sayClock(near.x, bounds) : `step ${near.x}`}  ${formatValue(near.y)}`;
    ctx.font = `10px ${monoFont()}`;
    ctx.fillStyle = color("axis");
    const right = x > area.left + area.width * 0.6;
    ctx.textAlign = right ? "right" : "left";
    ctx.fillText(said, x + (right ? -6 : 6), area.top + 10);
  }

  /* A time on the production's clock, in ps under a nanosecond of
   * production and in ns from there. */
  function sayClock(ns, bounds) {
    const ps = Math.max(Math.abs(bounds.maxX), Math.abs(bounds.minX)) < 1;
    const value = ps ? ns * 1000 : ns;
    const digits = Math.abs(value) >= 100 ? 0 : 1;
    return `${value.toFixed(digits)} ${ps ? "ps" : "ns"}`;
  }

  /* The value axis: the series with a margin, and not below zero for a
   * quantity that cannot be. The speed chart's axis read -0.381 ns/day
   * under a series that starts at 0. */
  function valueBounds(values) {
    let minY = Math.min(...values);
    let maxY = Math.max(...values);
    if (minY === maxY) {
      const padding = Math.max(Math.abs(minY) * 0.02, 1e-6);
      minY = minY >= 0 ? Math.max(0, minY - padding) : minY - padding;
      maxY += padding;
    } else {
      const padding = (maxY - minY) * 0.08;
      minY = minY >= 0 ? Math.max(0, minY - padding) : minY - padding;
      maxY += padding;
    }
    return {minY, maxY};
  }

  function drawBackground(ctx, rect) {
    const gradient = ctx.createLinearGradient(0, 0, 0, rect.height);
    gradient.addColorStop(0, hexToRgba(color("cyan"), 0.035));
    gradient.addColorStop(1, hexToRgba(color("cyan"), 0));
    ctx.fillStyle = gradient;
    ctx.fillRect(0, 0, rect.width, rect.height);
  }

  function drawGrid(ctx, rect) {
    const area = plotArea(rect);
    ctx.strokeStyle = color("grid");
    ctx.lineWidth = 1;
    for (let row = 0; row <= 4; row += 1) {
      const y = area.top + (area.height * row / 4);
      ctx.beginPath();
      ctx.moveTo(area.left, y);
      ctx.lineTo(area.right, y);
      ctx.stroke();
    }
  }

  function drawSeries(ctx, rect, bounds, points, color) {
    const area = plotArea(rect);
    const spanX = bounds.maxX - bounds.minX || 1;
    const spanY = bounds.maxY - bounds.minY || 1;
    const coords = points.map((point, index) => {
      const y = area.bottom - area.height * ((point.y - bounds.minY) / spanY);
      return {
        // Where it was sampled, one sample too: the axis is widened around
        // it, and it sat mid-axis at a moment it was not taken.
        x: area.left + area.width * ((point.x - bounds.minX) / spanX),
        y: Math.max(area.top, Math.min(area.bottom, y)),
        beyond: y > area.bottom ? 1 : y < area.top ? -1 : 0,
        index,
      };
    });
    // Where each sample was placed across the axis, 0 to 1, for `drawn`.
    bounds.placed = coords.map((point) => (point.x - area.left) / (area.width || 1));
    // Each stretch beyond the axis, marked once where it begins.
    ctx.fillStyle = color;
    coords.forEach((point, index) => {
      if (!point.beyond || (index && coords[index - 1].beyond === point.beyond)) return;
      const edge = point.beyond > 0 ? area.bottom : area.top;
      const tip = point.beyond > 0 ? 5 : -5;
      ctx.beginPath();
      ctx.moveTo(point.x - 4, edge - tip);
      ctx.lineTo(point.x + 4, edge - tip);
      ctx.lineTo(point.x, edge);
      ctx.closePath();
      ctx.fill();
    });

    ctx.beginPath();
    coords.forEach((point, index) => {
      if (index === 0) ctx.moveTo(point.x, point.y);
      else ctx.lineTo(point.x, point.y);
    });
    ctx.lineWidth = 2;
    ctx.strokeStyle = color;
    ctx.stroke();

    if (coords.length > 1) {
      ctx.lineTo(coords[coords.length - 1].x, area.bottom);
      ctx.lineTo(coords[0].x, area.bottom);
      ctx.closePath();
      ctx.fillStyle = hexToRgba(color, 0.075);
      ctx.fill();
    } else {
      ctx.beginPath();
      ctx.arc(coords[0].x, coords[0].y, 3, 0, Math.PI * 2);
      ctx.fillStyle = color;
      ctx.fill();
    }
  }

  function drawLabels(ctx, rect, bounds, samples, unit) {
    const area = plotArea(rect);
    ctx.fillStyle = color("axis");
    ctx.font = `10px ${monoFont()}`;
    ctx.textAlign = "left";
    ctx.fillText(formatAxis(bounds.maxY), 6, area.top + 4);
    ctx.fillText(formatAxis(bounds.minY), 6, area.bottom);
    if (!clock.timed) {
      ctx.textAlign = "right";
      ctx.fillText(`${samples} sample${samples === 1 ? "" : "s"}`, rect.width - 12, area.top + 4);
      ctx.fillText(unit, rect.width - 12, area.bottom);
      return;
    }
    // The production's clock under the plot: 0 where production began.
    const ps = Math.max(Math.abs(bounds.maxX), Math.abs(bounds.minX)) < 1;
    const scale = ps ? 1000 : 1;
    const ticks = niceTicks(bounds.minX * scale, bounds.maxX * scale, 5);
    if (bounds.minX < 0 && !ticks.includes(0)) ticks.push(0);
    const spanX = bounds.maxX - bounds.minX || 1;
    // Each label's box, from the last (which carries the unit) back: one
    // that would touch the label after it is left out, as on a narrow card
    // "6" sat under "8 ps".
    const labels = [];
    ticks.sort((a, b) => a - b).forEach((tick, index) => {
      const x = area.left + area.width * ((tick / scale - bounds.minX) / spanX);
      if (x < area.left - 1 || x > area.right + 1) return;
      const align = x < area.left + 12 ? "left" : x > area.right - 24 ? "right" : "center";
      const text = `${+tick.toFixed(3)}${index === ticks.length - 1 ? (ps ? " ps" : " ns") : ""}`;
      const width = ctx.measureText(text).width;
      const left = align === "left" ? x : align === "right" ? x - width : x - width / 2;
      labels.push({tick, x, align, text, left, right: left + width});
    });
    const kept = [];
    for (let i = labels.length - 1; i >= 0; i -= 1) {
      const next = kept[0];
      if (next && labels[i].right + 6 > next.left) continue;
      kept.unshift(labels[i]);
    }
    bounds.ticks = kept.map(({tick, x, left, right}) => ({tick, x, left, right}));
    kept.forEach((label) => {
      ctx.textAlign = label.align;
      ctx.fillText(label.text, label.x, rect.height - 4);
    });
  }

  /* Round values across a span, about `count` of them. */
  function niceTicks(lo, hi, count) {
    const span = hi - lo;
    if (!(span > 0)) return [lo];
    const rough = span / count;
    const power = Math.pow(10, Math.floor(Math.log10(rough)));
    const step = [1, 2, 2.5, 5, 10].map((m) => m * power).find((s) => s >= rough) || 10 * power;
    const ticks = [];
    for (let t = Math.ceil(lo / step) * step; t <= hi + step * 1e-9; t += step) {
      ticks.push(Math.abs(t) < step * 1e-9 ? 0 : t);
    }
    return ticks;
  }

  /* The temperature asked for, dashed, where the run says what it was;
   * otherwise a band of ordinary temperatures, as the density's. */
  function drawThresholds(ctx, config, rect, bounds) {
    if (config.key === "temperature") {
      if (clock.target != null) drawLevel(ctx, rect, bounds, clock.target);
      else drawBand(ctx, rect, bounds, 270, 330, color("green"));
    }
    if (config.key === "density") drawBand(ctx, rect, bounds, 0.98, 1.04, color("green"));
  }

  function drawLevel(ctx, rect, bounds, value) {
    if (value < bounds.minY || value > bounds.maxY) return;
    const area = plotArea(rect);
    const y = area.bottom - area.height * ((value - bounds.minY) / (bounds.maxY - bounds.minY || 1));
    ctx.strokeStyle = color("axis");
    ctx.lineWidth = 1;
    ctx.setLineDash([4, 3]);
    ctx.beginPath();
    ctx.moveTo(area.left, y);
    ctx.lineTo(area.right, y);
    ctx.stroke();
    ctx.setLineDash([]);
  }

  function drawBand(ctx, rect, bounds, low, high, color) {
    const area = plotArea(rect);
    if (high < bounds.minY || low > bounds.maxY) return;
    const span = bounds.maxY - bounds.minY || 1;
    const yLow = area.bottom - area.height * ((Math.max(low, bounds.minY) - bounds.minY) / span);
    const yHigh = area.bottom - area.height * ((Math.min(high, bounds.maxY) - bounds.minY) / span);
    ctx.fillStyle = hexToRgba(color, 0.055);
    ctx.fillRect(area.left, Math.min(yLow, yHigh), area.width, Math.abs(yLow - yHigh));
  }

  function plotArea(rect) {
    const left = 54;
    const right = rect.width - 12;
    const top = 22;
    const bottom = rect.height - 18;
    return {left, right, top, bottom, width: Math.max(1, right - left), height: Math.max(1, bottom - top)};
  }

  /* An axis value short enough for the margin it is written in: grouped
   * and whole from a thousand ("-44,887", where "-44886.57" ran into the
   * plot). */
  function formatAxis(value) {
    const magnitude = Math.abs(value);
    if ((magnitude > 0 && magnitude < 0.001) || magnitude >= 1e6) return value.toExponential(2);
    if (magnitude >= 1000) return value.toLocaleString(undefined, {maximumFractionDigits: 0});
    return value.toFixed(magnitude < 10 ? 3 : 1);
  }

  function hexToRgba(hex, alpha) {
    const match = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex);
    if (!match) return hex;
    return `rgba(${parseInt(match[1], 16)}, ${parseInt(match[2], 16)}, ${parseInt(match[3], 16)}, ${alpha})`;
  }

  function monoFont() {
    return "JetBrains Mono, SFMono-Regular, IBM Plex Mono, Consolas, Menlo, monospace";
  }

  // `drawn` is what a chart last put on its axes (its bounds, ticks and
  // where NPT began), for the tests.
  window.FastMDXCharts = {update, draw: drawAll, valueBounds,
                          drawn: (key) => states.get(key)?.bounds || null};
}());
