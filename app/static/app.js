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
  const lo = f.low * 0.985, hi = f.high * 1.015, pos = (p) => `${(((p - lo) / (hi - lo)) * 100).toFixed(1)}%`;
  const left = document.createElement("div");
  const big = document.createElement("div"); big.className = "price-big"; big.textContent = `$${fmt(f.last_price)}`;
  const meta = document.createElement("div"); meta.className = "price-meta"; meta.textContent = `Brent crude, ${f.as_of} (${f.anchored_on})`;
  const pill = document.createElement("span"); pill.className = "pill";
  pill.textContent = `Volatility ${f.daily_vol_pct}%/day (realised ${f.realised_daily_vol_pct}%, options ${f.implied_daily_vol_pct}%)`;
  left.append(big, meta, pill);

  const right = document.createElement("div");
  const t = document.createElement("div"); t.className = "gauge-title";
  const t1 = document.createElement("span"); t1.textContent = "Likely range in 48 hours (80% confidence)";
  t.append(t1);
  const gauge = document.createElement("div"); gauge.className = "gauge";
  const mk = (p, label, cls) => {
    const m = document.createElement("div"); m.className = "mark " + cls; m.style.left = pos(p);
    const b = document.createElement("b"); b.textContent = label; m.append(b); return m;
  };
  gauge.append(mk(f.median, `median $${fmt(f.median)}`, "med"), mk(f.last_price, "now", "last"));
  const ends = document.createElement("div"); ends.className = "ends";
  const end = (v, s) => { const e = document.createElement("span"); e.textContent = `$${fmt(v)}`; const sm = document.createElement("small"); sm.textContent = s; e.append(sm); return e; };
  ends.append(end(f.low, "low (10th percentile)"), end(f.high, "high (90th percentile)"));
  right.append(t, gauge, ends);
  $("stats").replaceChildren(left, right);
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
    `Line: ${historyData.source}, last ${prices.length} trading days. Cone: 80% range two trading days ahead of the ${f.anchored_on}, $${fmt(f.low)} to $${fmt(f.high)}.`;
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
    const kpi = (big, lbl, pct, target) => {
      const d = document.createElement("div"); d.className = "kpi";
      const a = document.createElement("div"); a.className = "big"; a.textContent = big;
      const l = document.createElement("div"); l.className = "lbl"; l.textContent = lbl;
      d.append(a, l);
      if (pct !== undefined) {
        const bar = document.createElement("div"); bar.className = "bar";
        const i = document.createElement("i"); i.style.width = Math.min(pct, 100) + "%"; bar.append(i);
        if (target !== undefined) { const u = document.createElement("u"); u.style.left = target + "%"; bar.append(u); }
        d.append(bar);
      }
      return d;
    };
    $("kpis").replaceChildren(
      kpi(`${b.coverage_blend_pct}%`, "of real outcomes landed inside the 80% range (white line = 80% target)", b.coverage_blend_pct, 80),
      kpi(`${b.mean_blend_width_pct}%`, "average width of the range, as a share of price"),
      kpi(`${b.forecasts}`, `forecasts tested, ${b.from} to ${b.to}`),
      kpi(`${b.mape_no_change_pct}% vs ${b.mape_momentum_pct}%`, "point error: 'price stays the same' vs momentum. Neither wins."),
    );
  } catch (err) {
    $("kpis").textContent = "Backtest unavailable: " + err.message;
  }
}

$("recalc").addEventListener("click", async () => {
  const value = $("price").value;
  try { await loadForecast(value ? Number(value) : undefined); }
  catch (err) { $("chartnote").textContent = err.message; $("chartnote").className = "note err"; }
});

const KEY = "hormuz_chat";
let chat = JSON.parse(sessionStorage.getItem(KEY) || '{"sid":null,"msgs":[]}');
const save = () => sessionStorage.setItem(KEY, JSON.stringify(chat));

function bubble(role, text, r) {
  const d = document.createElement("div"); d.className = "msg " + role;
  d.textContent = text;
  if (r) { const c = document.createElement("div"); c.className = "checks"; d.append(c); showChecks(r, c); }
  $("chatlog").append(d); $("chatlog").scrollTop = $("chatlog").scrollHeight;
  return d;
}

chat.msgs.forEach((m) => bubble(m.role, m.text, m.r));

async function send() {
  const input = $("question"), text = input.value.trim();
  const button = $("brief");
  if (!text || button.disabled) return;
  button.disabled = true; $("newchat").disabled = true; input.value = "";
  bubble("user", text); chat.msgs.push({ role: "user", text });
  const wait = bubble("bot", ""); const spin = document.createElement("span"); spin.className = "spin";
  const status = document.createElement("span");
  const started = Date.now();
  const updateStatus = () => {
    const seconds = Math.floor((Date.now() - started) / 1000);
    status.textContent = `Working… ${seconds}s elapsed. Fresh research takes longer than a chat reply.`;
  };
  updateStatus();
  wait.append(spin, status);
  const progress = setInterval(updateStatus, 1000);
  try {
    const r = await getJSON("/api/brief", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: text, session_id: chat.sid }),
    });
    if (r.session_id) chat.sid = r.session_id;
    wait.remove(); bubble("bot", r.briefing, r);
    chat.msgs.push({ role: "bot", text: r.briefing, r: { blocked: r.blocked, checks: r.checks, trace_id: r.trace_id, mode: r.mode } });
  } catch (err) {
    wait.remove(); bubble("bot err", err.message);
  } finally { clearInterval(progress); save(); button.disabled = false; $("newchat").disabled = false; input.focus(); }
}

$("brief").addEventListener("click", send);
$("question").addEventListener("keydown", (e) => { if (e.key === "Enter") send(); });
$("newchat").addEventListener("click", () => {
  chat = { sid: null, msgs: [] }; save(); $("chatlog").replaceChildren();
});

// Internal pass/fail metrics live on /ops; here only show what a reader must act on.
function showChecks(r, box) {
  const add = (cls, text) => { const s = document.createElement("span"); s.className = "check " + cls; s.textContent = text; box.append(s); };
  if (r.blocked) { add("bad", "🚫 This message was not sent to the model"); return; }
  const c = r.checks || {};
  if (c.range_quoted === false) add("bad", "⚠️ This answer does not quote the computed range: rely on the range shown above");
  if (c.off_range_figures) add("bad", "⚠️ Some prices in this answer are far from the computed range: verify them");
  if (r.mode !== "chat" && c.disclaimer === false) add("bad", "⚠️ Not financial advice: for information only");
}

document.querySelectorAll("#chips .chip").forEach((c) => c.addEventListener("click", () => { $("question").value = c.textContent; send(); }));

window.addEventListener("resize", () => { if (historyData) loadForecast($("price").value || undefined).catch(() => {}); });
init();
