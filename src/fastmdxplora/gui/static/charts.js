/* FastMDXplora Live Dashboard — dependency-free canvas charts. */

(function () {
  "use strict";

  /* Colours are the theme's, read when a chart is drawn: fixed to the dark
     scheme's, the axis labels and the pale series vanished on Paper. */
  const FALLBACK = {
    cyan: "#63e6ff",
    orange: "#ffb86b",
    violet: "#a78bfa",
    silver: "#d8d8dd",
    green: "#67e8a3",
    grid: "rgba(255, 255, 255, 0.06)",
    axis: "#777780",
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
     be plotted without a current value or given one without a plot. */
  function renderLatest(metrics) {
    const latest = metrics && metrics.length ? metrics[metrics.length - 1] : null;
    CONFIG.forEach((config) => {
      const target = document.querySelector(`[data-chart-value="${config.key}"]`);
      if (!target) return;
      // An empty cell is a gap, not a zero: Number("") is 0 and finite, so
      // a metric the run has not sampled would have read 0.0000.
      const cell = latest ? latest[config.key] : null;
      const raw = cell === "" || cell == null ? NaN : Number(cell);
      // No unit here -- the title beside it already carries one, and the row
      // read "Potential energy (kJ/mol)  -506551 kJ/mol".
      target.textContent = Number.isFinite(raw) ? formatValue(raw) : "—";
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
    CONFIG.forEach((config) => {
      const entry = states.get(config.key);
      if (!entry) return;
      entry.points = lastMetrics
        .map((row, index) => {
          const value = Number(row[config.key]);
          const step = Number(row.step);
          return {
            x: Number.isFinite(step) ? step : index,
            y: value,
          };
        })
        .filter((point) => Number.isFinite(point.y));
      entry.needsDraw = true;
    });
    renderLatest(lastMetrics);
    updateEmptyState();
    drawAll();
  }

  function updateEmptyState() {
    const empty = document.getElementById("chart-empty");
    if (!empty) return;
    const anyData = Array.from(states.values()).some((entry) => entry.points.length > 0);
    empty.style.display = anyData ? "none" : "block";
    if (!anyData) {
      empty.textContent = lastMetrics.length
        ? "Telemetry samples exist, but none contain chartable values."
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

    const yValues = points.map((point) => point.y);
    let minY = Math.min(...yValues);
    let maxY = Math.max(...yValues);
    if (minY === maxY) {
      const padding = Math.max(Math.abs(minY) * 0.02, 1e-6);
      minY -= padding;
      maxY += padding;
    } else {
      const padding = (maxY - minY) * 0.08;
      minY -= padding;
      maxY += padding;
    }
    const minX = Math.min(...points.map((point) => point.x));
    const maxX = Math.max(...points.map((point) => point.x));
    const bounds = {minY, maxY, minX, maxX};

    drawThresholds(ctx, config, rect, bounds);
    drawGrid(ctx, rect);
    drawSeries(ctx, rect, bounds, points, color(config.color));
    drawLabels(ctx, rect, bounds, points.length, config.unit);
    entry.needsDraw = false;
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
    const coords = points.map((point, index) => ({
      x: area.left + area.width * (
        points.length === 1 ? 0.5 : (point.x - bounds.minX) / spanX
      ),
      y: area.bottom - area.height * ((point.y - bounds.minY) / spanY),
      index,
    }));

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
    ctx.textAlign = "right";
    ctx.fillText(`${samples} sample${samples === 1 ? "" : "s"}`, rect.width - 12, area.top + 4);
    ctx.fillText(unit, rect.width - 12, area.bottom);
  }

  function drawThresholds(ctx, config, rect, bounds) {
    if (config.key === "temperature") drawBand(ctx, rect, bounds, 270, 330, color("green"));
    if (config.key === "density") drawBand(ctx, rect, bounds, 0.98, 1.04, color("green"));
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
    const left = 46;
    const right = rect.width - 12;
    const top = 22;
    const bottom = rect.height - 18;
    return {left, right, top, bottom, width: Math.max(1, right - left), height: Math.max(1, bottom - top)};
  }

  function formatAxis(value) {
    const magnitude = Math.abs(value);
    if ((magnitude > 0 && magnitude < 0.001) || magnitude >= 1e6) return value.toExponential(2);
    return value.toFixed(magnitude < 10 ? 3 : 2);
  }

  function hexToRgba(hex, alpha) {
    const match = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex);
    if (!match) return hex;
    return `rgba(${parseInt(match[1], 16)}, ${parseInt(match[2], 16)}, ${parseInt(match[3], 16)}, ${alpha})`;
  }

  function monoFont() {
    return "JetBrains Mono, SFMono-Regular, IBM Plex Mono, Consolas, Menlo, monospace";
  }

  window.FastMDXCharts = {update, draw: drawAll};
}());
