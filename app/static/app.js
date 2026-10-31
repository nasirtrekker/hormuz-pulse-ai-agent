"use strict";

const $ = (id) => document.getElementById(id);
const fmt = (n) => Number(n).toLocaleString("en-GB", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

async function getJSON(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) {
    let detail = response.statusText;
    try { detail = (await response.json()).detail || detail; } catch (_) { /* keep status text */ }
    throw new Error(detail);
  }
  return response.json();
}

function stat(key, value) {
  const box = document.createElement("div");
  box.className = "stat";
  const v = document.createElement("div"); v.className = "v"; v.textContent = value;
  const k = document.createElement("div"); k.className = "k"; k.textContent = key;
  box.append(v, k);
  return box;
}

function showStats(f) {
  const grid = $("stats");
  grid.replaceChildren(
    stat(`Brent, ${f.as_of} (${f.anchored_on})`, `$${fmt(f.last_price)}`),
    stat("48h low (10th percentile)", `$${fmt(f.low)}`),
    stat("48h median", `$${fmt(f.median)}`),
    stat("48h high (90th percentile)", `$${fmt(f.high)}`),
    stat("Daily volatility, last 20 days", `${f.daily_vol_pct}%`),
  );
}

function drawChart(history, f) {
  const canvas = $("chart");
  const ratio = window.devicePixelRatio || 1;
  const w = canvas.clientWidth, h = canvas.clientHeight;
  canvas.width = w * ratio; canvas.height = h * ratio;
  const ctx = canvas.getContext("2d");
  ctx.scale(ratio, ratio);
  ctx.clearRect(0, 0, w, h);

  const prices = history.prices.slice(-260); // about the last year
  const ahead = 30; // room on the right for the forecast cone
  const lo = Math.min(...prices, f.low) * 0.97, hi = Math.max(...prices, f.high) * 1.03;
  const pad = { l: 48, r: 10, t: 10, b: 22 };
  const x = (i) => pad.l + (i / (prices.length - 1 + ahead)) * (w - pad.l - pad.r);
  const y = (p) => pad.t + (1 - (p - lo) / (hi - lo)) * (h - pad.t - pad.b);

  ctx.strokeStyle = "#2a3544"; ctx.fillStyle = "#8b98a8"; ctx.font = "12px system-ui"; ctx.lineWidth = 1;
  for (let k = 0; k <= 4; k++) {
    const p = lo + ((hi - lo) * k) / 4;
    ctx.beginPath(); ctx.moveTo(pad.l, y(p)); ctx.lineTo(w - pad.r, y(p)); ctx.stroke();
    ctx.fillText(p.toFixed(0), 6, y(p) + 4);
  }
  ctx.fillText(history.dates[history.dates.length - prices.length], pad.l, h - 6);
  ctx.fillText(history.dates[history.dates.length - 1], x(prices.length - 1) - 30, h - 6);

  ctx.strokeStyle = "#4cc2ff"; ctx.lineWidth = 2; ctx.beginPath();
  prices.forEach((p, i) => (i ? ctx.lineTo(x(i), y(p)) : ctx.moveTo(x(i), y(p))));
  ctx.stroke();

  const x0 = x(prices.length - 1), x1 = x(prices.length - 1 + ahead / 3);
  ctx.fillStyle = "rgba(76,194,255,.22)";
  ctx.beginPath(); ctx.moveTo(x0, y(f.last_price)); ctx.lineTo(x1, y(f.high)); ctx.lineTo(x1, y(f.low)); ctx.closePath(); ctx.fill();
  ctx.strokeStyle = "#e6edf3"; ctx.lineWidth = 1.5; ctx.setLineDash([4, 3]);
  ctx.beginPath(); ctx.moveTo(x0, y(f.last_price)); ctx.lineTo(x1, y(f.median)); ctx.stroke(); ctx.setLineDash([]);
  $("chartnote").textContent =
    `Line: last ${prices.length} trading days. Shaded cone: 80% range two trading days ahead ($${fmt(f.low)} to $${fmt(f.high)}).`;
}

let historyData = null;
async function loadForecast(price) {
  const f = await getJSON("/api/forecast" + (price ? `?price=${encodeURIComponent(price)}` : ""));
  showStats(f);
  if (historyData) drawChart(historyData, f);
  return f;
}

async function init() {
  try {
    historyData = await getJSON("/api/history?years=3");
    await loadForecast();
  } catch (err) {
    $("stats").textContent = "Could not load data: " + err.message;
    $("stats").className = "err";
  }
  try {
    const b = await getJSON("/api/backtest");
    const rows = [
      ["Forecasts tested", `${b.forecasts} (${b.from} to ${b.to})`],
      ["80% range: share of outcomes inside (target 80%)", `${b.coverage_data_driven_pct}%`],
      ["Old fixed ±3.6% band: share inside", `${b.coverage_fixed_band_pct}%`],
      ["Point error, 'price stays the same'", `${b.mape_no_change_pct}%`],
      ["Point error, momentum rule", `${b.mape_momentum_pct}%`],
      ["Average range width", `${b.mean_interval_width_pct}% of price`],
    ];
    $("bt").replaceChildren(...rows.map(([k, v]) => {
      const tr = document.createElement("tr");
      const a = document.createElement("td"); a.textContent = k;
      const c = document.createElement("td"); c.textContent = v;
      tr.append(a, c); return tr;
    }));
  } catch (err) {
    $("bt").textContent = "Backtest unavailable: " + err.message;
  }
}

$("recalc").addEventListener("click", async () => {
  const value = $("price").value;
  try { await loadForecast(value ? Number(value) : undefined); }
  catch (err) { $("chartnote").textContent = err.message; $("chartnote").className = "note err"; }
});

$("brief").addEventListener("click", async () => {
  const button = $("brief"), out = $("briefing");
  button.disabled = true; out.className = ""; out.textContent = "Researching news, sentiment and prices… this takes 1 to 3 minutes.";
  try {
    const r = await getJSON("/api/brief", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: $("question").value }),
    });
    out.textContent = r.briefing;
  } catch (err) {
    out.className = "err"; out.textContent = err.message;
  } finally { button.disabled = false; }
});

window.addEventListener("resize", () => { if (historyData) loadForecast($("price").value || undefined).catch(() => {}); });
init();
