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

function stat(key, value, kind = "") {
  const box = document.createElement("div");
  box.className = "stat " + kind;
  const v = document.createElement("div"); v.className = "v"; v.textContent = value;
  const k = document.createElement("div"); k.className = "k"; k.textContent = key;
  box.append(v, k);
  return box;
}

function showStats(f) {
  const grid = $("stats");
  grid.replaceChildren(
    stat(`Brent, ${f.as_of} (${f.anchored_on})`, `$${fmt(f.last_price)}`),
    stat("48h low (10th percentile)", `$${fmt(f.low)}`, "low"),
    stat("48h median", `$${fmt(f.median)}`, "mid"),
    stat("48h high (90th percentile)", `$${fmt(f.high)}`, "high"),
    stat("Daily volatility used (realised " + f.realised_daily_vol_pct + "%, options-implied " + f.implied_daily_vol_pct + "%)", `${f.daily_vol_pct}%`),
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

  ctx.strokeStyle = "rgba(120,150,190,.18)"; ctx.fillStyle = "#93a3b8"; ctx.font = "12px system-ui"; ctx.lineWidth = 1;
  for (let k = 0; k <= 4; k++) {
    const p = lo + ((hi - lo) * k) / 4;
    ctx.beginPath(); ctx.moveTo(pad.l, y(p)); ctx.lineTo(w - pad.r, y(p)); ctx.stroke();
    ctx.fillText(p.toFixed(0), 6, y(p) + 4);
  }
  ctx.fillText(history.dates[history.dates.length - prices.length], pad.l, h - 6);
  ctx.fillText(history.dates[history.dates.length - 1], x(prices.length - 1) - 30, h - 6);

  const grad = ctx.createLinearGradient(0, pad.t, 0, h - pad.b);
  grad.addColorStop(0, "rgba(56,189,248,.28)"); grad.addColorStop(1, "rgba(56,189,248,0)");
  ctx.beginPath();
  prices.forEach((p, i) => (i ? ctx.lineTo(x(i), y(p)) : ctx.moveTo(x(i), y(p))));
  ctx.lineTo(x(prices.length - 1), h - pad.b); ctx.lineTo(x(0), h - pad.b); ctx.closePath();
  ctx.fillStyle = grad; ctx.fill();
  ctx.strokeStyle = "#38bdf8"; ctx.lineWidth = 2; ctx.lineJoin = "round"; ctx.beginPath();
  prices.forEach((p, i) => (i ? ctx.lineTo(x(i), y(p)) : ctx.moveTo(x(i), y(p))));
  ctx.stroke();

  const x0 = x(prices.length - 1), x1 = x(prices.length - 1 + ahead / 3);
  ctx.fillStyle = "rgba(129,140,248,.35)";
  ctx.beginPath(); ctx.moveTo(x0, y(f.last_price)); ctx.lineTo(x1, y(f.high)); ctx.lineTo(x1, y(f.low)); ctx.closePath(); ctx.fill();
  ctx.strokeStyle = "#e6edf3"; ctx.lineWidth = 1.5; ctx.setLineDash([4, 3]);
  ctx.beginPath(); ctx.moveTo(x0, y(f.last_price)); ctx.lineTo(x1, y(f.median)); ctx.stroke(); ctx.setLineDash([]);
  ctx.fillStyle = "#fff"; ctx.beginPath(); ctx.arc(x0, y(f.last_price), 4, 0, 7); ctx.fill();
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
  loadEvidence();
  try {
    const b = await getJSON("/api/backtest");
    const rows = [
      ["Forecasts tested", `${b.forecasts} (${b.from} to ${b.to})`],
      ["80% range: share of outcomes inside (target 80%)", `${b.coverage_data_driven_pct}%`],
      ["Range with options-implied volatility added: share inside", `${b.coverage_blend_pct}%`],
      ["Range without it: share inside, average width", `${b.coverage_data_driven_pct}%, ${b.mean_interval_width_pct}%`],
      ["Old fixed ±3.6% band: share inside", `${b.coverage_fixed_band_pct}%`],
      ["Point error, 'price stays the same'", `${b.mape_no_change_pct}%`],
      ["Point error, momentum rule", `${b.mape_momentum_pct}%`],
      ["Average range width (this model)", `${b.mean_blend_width_pct}% of price`],
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

async function loadEvidence() {
  try {
    const e = await getJSON("/api/evidence");
    const head = document.createElement("tr");
    ["Model (scored on " + e.test_points + " unseen 2-day moves, " + e.period + ")", "Score vs current (lower is better)", "Inside 80% range"].forEach((t) => {
      const th = document.createElement("th"); th.textContent = t; head.append(th);
    });
    const rows = e.models.map((m) => {
      const tr = document.createElement("tr");
      [m.name, `${m.vs_current_pct > 0 ? "+" : ""}${m.vs_current_pct}%`, `${m.coverage80_pct}%`].forEach((t) => {
        const td = document.createElement("td"); td.textContent = t; tr.append(td);
      });
      return tr;
    });
    $("ev").replaceChildren(head, ...rows);
    const g = e.news_tests.find((t) => t.feature === "gpr");
    $("evnote").textContent =
      `News (Geopolitical Risk Index) vs the next 2-day move: correlation ${g.rho_direction} for direction (p=${g.p_direction}) and ${g.rho_size} for size (p=${g.p_size}), i.e. no measurable predictive power. ` +
      `No model beat "no change" on the point forecast (median error ${e.no_change_median_abs_error_pct}% for no change). Only options-implied volatility improved the range.`;
  } catch (err) {
    $("ev").textContent = "Evidence unavailable: " + err.message;
  }
}

$("recalc").addEventListener("click", async () => {
  const value = $("price").value;
  try { await loadForecast(value ? Number(value) : undefined); }
  catch (err) { $("chartnote").textContent = err.message; $("chartnote").className = "note err"; }
});

$("brief").addEventListener("click", async () => {
  const button = $("brief"), out = $("briefing");
  button.disabled = true; out.className = "answer show"; out.replaceChildren();
  const spin = document.createElement("span"); spin.className = "spin";
  out.append(spin, "Researching news, sentiment and prices… this takes 1 to 3 minutes.");
  try {
    const r = await getJSON("/api/brief", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: $("question").value }),
    });
    out.className = "answer show"; out.textContent = r.briefing;
  } catch (err) {
    out.className = "answer show err"; out.textContent = err.message;
  } finally { button.disabled = false; }
});

document.querySelectorAll("#chips .chip").forEach((c) => c.addEventListener("click", () => { $("question").value = c.textContent; $("question").focus(); }));

window.addEventListener("resize", () => { if (historyData) loadForecast($("price").value || undefined).catch(() => {}); });
init();
