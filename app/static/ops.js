"use strict";

const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e; };

async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(r.statusText);
  return r.json();
}

function panel(title, value, sub, cls) {
  const p = el("div", "panel");
  p.append(el("div", "t", title), el("div", "n " + (cls || ""), value), el("div", "s", sub || ""));
  return p;
}

function hbar(label, value, max, color) {
  const row = el("div", "hbar");
  const track = el("div", "track"); const i = el("i"); i.style.width = (max ? (100 * value) / max : 0) + "%"; i.style.background = color; track.append(i);
  row.append(el("span", "", label), track, el("b", "", String(value)));
  return row;
}

const GUARD_LABELS = {
  prompt_injection_blocked: ["🚫 Prompt injection blocked", "#fb7185"],
  personal_data_blocked: ["🔒 Personal data blocked", "#fb7185"],
  research_injection_removed: ["🧹 Injection removed from web research", "#fbbf24"],
  secret_redacted: ["🔑 Secret redacted", "#fbbf24"],
  research_validation_failed: ["📉 Research field failed validation", "#fbbf24"],
  tool_output_rejected: ["🧮 Tool output rejected", "#fbbf24"],
  rate_limited: ["⏳ Rate limited", "#38bdf8"],
};

function renderSummary(s) {
  const ok = s.outcomes.ok || 0;
  const blocked = s.outcomes.blocked || 0;
  const accepted = s.runs - blocked;
  const rate = accepted ? Math.round((100 * ok) / accepted) : null;
  const fmtMs = (v) => (v == null ? "n/a" : (v / 1000).toFixed(1) + " s");
  $("panels").replaceChildren(
    panel("🤖 Agent runs", s.runs, "since instance start", "blue"),
    panel("✅ Completed", rate == null ? "n/a" : rate + "%", `${ok} of ${accepted} accepted runs (blocked ones excluded)`, rate == null ? "" : rate >= 80 ? "green" : "amber"),
    panel("🚫 Blocked", blocked, "stopped by guardrails", blocked ? "red" : ""),
    panel("⏱️ Latency p50 / p95", `${fmtMs(s.latency_ms.p50)} / ${fmtMs(s.latency_ms.p95)}`, "completed runs"),
    panel("🧮 Quotes computed range", s.range_quoted_rate == null ? "n/a" : s.range_quoted_rate + "%", `${s.graded_runs} graded answers`, s.range_quoted_rate == null ? "" : s.range_quoted_rate >= 90 ? "green" : "amber"),
    panel("📌 Disclaimer present", s.disclaimer_rate == null ? "n/a" : s.disclaimer_rate + "%", "completed answers", s.disclaimer_rate === 100 ? "green" : ""),
  );
  const events = Object.entries(s.guardrail_events);
  const g = $("gevents");
  if (!events.length) { g.replaceChildren(el("p", "note", "No guardrail events yet. Try asking the agent to “ignore all previous instructions” on the dashboard and watch this panel.")); return; }
  const max = Math.max(...events.map(([, v]) => v));
  g.replaceChildren(...events.map(([k, v]) => { const [label, color] = GUARD_LABELS[k] || [k, "#38bdf8"]; return hbar(label, v, max, color); }));
}

function renderTraces(traces) {
  const spark = $("spark");
  if (!traces.length) {
    spark.replaceChildren(); $("sparknote").textContent = "No runs yet: ask a question on the dashboard.";
    $("traces").replaceChildren(el("tr", "", "")); $("traces").firstChild.append(el("td", "note", "No traces yet."));
    $("gantt").replaceChildren(); return;
  }
  const chrono = [...traces].reverse();
  const max = Math.max(...chrono.map((t) => t.duration_ms), 1);
  spark.replaceChildren(...chrono.map((t) => { const b = el("i", t.outcome === "ok" ? "" : t.outcome); b.style.height = Math.max(4, (90 * t.duration_ms) / max) + "px"; b.title = `${t.id}: ${t.outcome}, ${t.duration_ms} ms`; return b; }));
  $("sparknote").textContent = "Blue = completed, red = blocked, amber = error, timeout or rate limit.";

  const head = el("tr");
  ["Trace", "Time (UTC)", "Outcome", "Duration", "Guardrail events", "Answer checks"].forEach((h) => head.append(el("th", "", h)));
  const rows = traces.map((t) => {
    const tr = el("tr", "pick");
    const out = el("td"); out.append(el("span", "tag " + t.outcome, t.outcome));
    const ge = Object.entries(t.guardrails).map(([k, v]) => `${(GUARD_LABELS[k] || [k])[0]} ×${v}`).join(", ") || "none";
    const c = t.checks || {};
    const chk = t.outcome !== "ok" ? "n/a" : [c.range_quoted === true ? "🧮 range quoted" : c.range_quoted === false ? "⚠️ range not quoted" : "ℹ️ no range tool", c.disclaimer ? "📌" : "⚠️ no disclaimer"].join(" · ");
    tr.append(el("td", "", t.id), el("td", "", t.at), out, el("td", "", (t.duration_ms / 1000).toFixed(2) + " s"), el("td", "", ge), el("td", "", chk));
    tr.addEventListener("click", () => renderGantt(t));
    return tr;
  });
  $("traces").replaceChildren(head, ...rows);
  if (!$("gantt").dataset.id) renderGantt(traces[0]);
}

function renderGantt(t) {
  const g = $("gantt"); g.dataset.id = t.id;
  g.replaceChildren(el("h3", "", `Trace ${t.id}: ${t.outcome}, ${(t.duration_ms / 1000).toFixed(2)} s`));
  if (!t.spans.length) { g.append(el("p", "note", "No agent ran: the request was stopped before any model call.")); return; }
  const total = Math.max(t.duration_ms, ...t.spans.map((s) => s.end_ms), 1);
  t.spans.forEach((s) => {
    const row = el("div", "grow");
    const label = el("span", "", s.agent + (s.tools.length ? ` 🔧 ${s.tools.join(", ")}` : ""));
    const track = el("div", "gtrack"); const bar = el("div", "gbar");
    bar.style.left = (100 * s.start_ms) / total + "%";
    bar.style.width = Math.max(1, (100 * (s.end_ms - s.start_ms)) / total) + "%";
    bar.title = `${s.start_ms} to ${s.end_ms} ms`;
    track.append(bar); row.append(label, track); g.append(row);
  });
}

async function renderEval() {
  try {
    const q = await getJSON("/api/quality");
    const box = $("safety");
    box.replaceChildren(...q.checks.map((c) => hbar(`${c.passed === c.total ? "✅" : "❌"} ${c.check}`, c.passed, c.total, c.passed === c.total ? "#34d399" : "#fb7185")),
      el("p", "note", `${q.passed} of ${q.total} attack-corpus cases handled correctly. The same corpus gates every code push in CI.`));
  } catch (e) { $("safety").textContent = "Unavailable: " + e.message; }
  try {
    const b = await getJSON("/api/backtest");
    const box = $("fc");
    box.replaceChildren(
      hbar("🎯 Outcomes inside 80% range", Math.round(b.coverage_blend_pct), 100, "#38bdf8"),
      hbar("📏 Avg range width (% of price)", Math.round(b.mean_blend_width_pct), 20, "#818cf8"),
      el("p", "note", `${b.forecasts} walk-forward forecasts, ${b.from} to ${b.to}. Coverage ${b.coverage_blend_pct}% against an 80% target. Point error: price-stays-the-same ${b.mape_no_change_pct}% vs momentum ${b.mape_momentum_pct}%.`));
  } catch (e) { $("fc").textContent = "Unavailable: " + e.message; }
  try {
    const e = await getJSON("/api/evidence");
    const head = el("tr");
    ["Model (" + e.test_points + " unseen 2-day moves)", "Score vs baseline (lower is better)", "Inside 80% range"].forEach((h) => head.append(el("th", "", h)));
    const rows = e.models.map((m) => {
      const tr = el("tr");
      const good = m.vs_current_pct < 0;
      tr.append(el("td", "", m.name), el("td", good ? "green" : "", `${m.vs_current_pct > 0 ? "+" : ""}${m.vs_current_pct}%`), el("td", "", `${m.coverage80_pct}%`));
      return tr;
    });
    $("ev").replaceChildren(head, ...rows);
    const gp = e.news_tests.find((t) => t.feature === "gpr");
    $("evnote").textContent = `📰 The Geopolitical Risk news index has no predictive power for the next 2-day move (direction p=${gp.p_direction}, size p=${gp.p_size}). No model beat "price stays the same" for the point forecast. Only options-implied volatility improved the range.`;
  } catch (err) { $("evnote").textContent = "Unavailable: " + err.message; }
}

async function refresh() {
  try {
    const [s, t] = await Promise.all([getJSON("/api/ops"), getJSON("/api/traces")]);
    renderSummary(s); renderTraces(t);
  } catch (e) { $("panels").textContent = "Could not load metrics: " + e.message; }
}

refresh(); renderEval();
setInterval(refresh, 10000);
