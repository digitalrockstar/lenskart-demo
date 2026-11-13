const $ = (id) => document.getElementById(id);
const nf = new Intl.NumberFormat("en-IN");
const money = (value) => {
  if (value >= 10000000) return "₹" + (value / 10000000).toFixed(2) + " Cr";
  if (value >= 100000) return "₹" + (value / 100000).toFixed(1) + " L";
  if (value >= 1000) return "₹" + (value / 1000).toFixed(1) + " K";
  return "₹" + nf.format(Math.round(value || 0));
};
const signed = (value, suffix = "") => (value > 0 ? "+" : "") + value.toFixed(1) + suffix;
const safePctChange = (current, previous) => previous ? ((current / previous) - 1) * 100 : 0;
let selectedDays = 30;
let toastTimer;

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `Request failed (${response.status})`);
  }
  return response.json();
}

function showToast(message) {
  const el = $("toast");
  el.textContent = message;
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), 2600);
}

function deltaLabel(element, value, suffix = "%") {
  element.textContent = signed(value, suffix);
  element.classList.remove("negative", "positive", "neutral");
  element.classList.add(value < 0 ? "negative" : value > 0 ? "positive" : "neutral");
}

function dateTimeLabel(value) {
  if (!value) return "—";
  const parsed = new Date(value.replace(" ", "T"));
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: true });
}

function renderOverview(data) {
  const c = data.current;
  const p = data.previous;
  $("sessionsValue").textContent = nf.format(c.sessions);
  $("ordersValue").textContent = nf.format(c.orders);
  $("conversionValue").textContent = c.conversion_rate.toFixed(2) + "%";
  $("revenueValue").textContent = money(c.revenue);
  deltaLabel($("sessionsDelta"), safePctChange(c.sessions, p.sessions));
  deltaLabel($("ordersDelta"), safePctChange(c.orders, p.orders));
  deltaLabel($("revenueDelta"), safePctChange(c.revenue, p.revenue));
  deltaLabel($("conversionDelta"), data.conversion_delta_pp, " pp");
  $("signalDelta").textContent = signed(data.conversion_delta_pp);
  $("signalCurrent").textContent = c.conversion_rate.toFixed(2) + "%";
  $("signalPrevious").textContent = p.conversion_rate.toFixed(2) + "%";
  $("forecastSessions").textContent = nf.format(data.forecast.sessions) + " visits";
  $("updatedAt").textContent = dateTimeLabel(data.as_of);
  $("funnelOverall").textContent = c.conversion_rate.toFixed(2) + "%";
  $("chartStart").textContent = (selectedDays === 7 ? "7 DAYS AGO" : selectedDays + " DAYS AGO");
  $("chartEnd").textContent = "NOW";
  renderFunnel(data.funnel);
}

function renderFunnel(funnel) {
  const max = funnel[0]?.count || 1;
  $("funnelRows").innerHTML = funnel.map((item, index) => {
    const width = Math.max(0.6, item.count / max * 100);
    const count = nf.format(item.count);
    const rate = index === 0 ? "100%" : item.rate_from_previous.toFixed(1) + "%";
    const drop = index === 0 ? "—" : item.drop_from_previous.toFixed(1) + "%";
    return `
      <div class="funnel-row">
        <div class="funnel-stage"><span class="stage-index">${String(index + 1).padStart(2, "0")}</span><span>${item.stage}</span></div>
        <div class="funnel-volume"><div class="funnel-track"><div class="funnel-fill" style="width:${width}%"></div></div><span class="funnel-number">${count}</span></div>
        <div class="funnel-rate">${rate}</div>
        <div class="funnel-drop">${drop}</div>
      </div>`;
  }).join("");
}

function renderTrend(points) {
  const svg = $("trendChart");
  if (!points.length) {
    svg.innerHTML = '<text x="400" y="115" text-anchor="middle" fill="#9aa2ac" font-size="12">No actual data in selected window</text>';
    return;
  }
  const W = 800, H = 230, left = 34, right = 12, top = 14, bottom = 26;
  const plotW = W - left - right, plotH = H - top - bottom;
  const sessions = points.map(p => p.sessions);
  const orders = points.map(p => p.orders);
  const maxS = Math.max(...sessions, 1) * 1.12;
  const maxO = Math.max(...orders, 1) * 1.12;
  const x = (i) => left + (points.length === 1 ? plotW / 2 : i * plotW / (points.length - 1));
  const yS = (v) => top + plotH - (v / maxS) * plotH;
  const yO = (v) => top + plotH - (v / maxO) * plotH;
  const lineS = points.map((p, i) => `${x(i).toFixed(1)},${yS(p.sessions).toFixed(1)}`).join(" ");
  const lineO = points.map((p, i) => `${x(i).toFixed(1)},${yO(p.orders).toFixed(1)}`).join(" ");
  const area = `${left},${top + plotH} ${lineS} ${left + plotW},${top + plotH}`;
  const grid = [0, 1, 2, 3].map(i => {
    const yy = top + i * plotH / 3;
    return `<line class="chart-gridline" x1="${left}" y1="${yy}" x2="${W-right}" y2="${yy}"/>`;
  }).join("");
  const axisLabels = [0, Math.floor((points.length - 1) / 2), points.length - 1]
    .filter((v, i, a) => a.indexOf(v) === i)
    .map(i => {
      const label = new Date(points[i].date + "T00:00:00").toLocaleDateString("en-IN", { day: "numeric", month: "short" });
      return `<text class="chart-label" x="${x(i)}" y="${H-5}" text-anchor="${i === 0 ? "start" : i === points.length - 1 ? "end" : "middle"}">${label}</text>`;
    }).join("");
  svg.innerHTML = `
    ${grid}
    <polygon class="chart-area" points="${area}"/>
    <polyline class="chart-line-sessions" points="${lineS}"/>
    <polyline class="chart-line-orders" points="${lineO}"/>
    ${axisLabels}
  `;
}

function renderHypotheses(hypotheses, run) {
  $("diagnosisStatus").innerHTML = '<i></i> Last run ' + dateTimeLabel(run?.completed_at);
  $("hypothesisList").innerHTML = hypotheses.map(h => `
    <article class="panel hypothesis-card">
      <div class="hypothesis-top"><span class="rank-tag">#${h.rank} PRIORITY</span><span class="confidence ${h.confidence.toLowerCase()}">${h.confidence} confidence</span></div>
      <div class="hypothesis-category">${h.category}</div>
      <h3>${h.title}</h3>
      <p class="hypothesis-evidence">${h.evidence}</p>
      <div class="hypothesis-confirm"><b>WHAT WOULD CONFIRM THIS</b>${h.confirm_if}</div>
      <div class="hypothesis-bottom"><span class="hypothesis-metric">${h.metric}</span><span class="score-track"><span class="score-fill" style="display:block;width:${h.score}%"></span></span></div>
    </article>
  `).join("");
}

function renderRuns(runs) {
  $("runCount").textContent = runs.length + (runs.length === 1 ? " run" : " runs");
  if (!runs.length) {
    $("runList").innerHTML = '<div class="loading-line">Waiting for the first scheduled run…</div>';
    return;
  }
  $("runList").innerHTML = runs.map(run => `
    <div class="run-row">
      <div class="run-check">✓</div>
      <div><div class="run-title">${run.summary}</div><div class="run-meta">${dateTimeLabel(run.completed_at || run.started_at)} · #${run.id}</div></div>
      <span class="run-trigger">${run.trigger_type.toUpperCase()}</span>
    </div>
  `).join("");
}

async function loadAll() {
  try {
    const [overview, trend, diagnosis, runs] = await Promise.all([
      api(`/api/overview?days=${selectedDays}`),
      api(`/api/trend?days=${selectedDays}`),
      api("/api/diagnostics"),
      api("/api/runs?limit=8"),
    ]);
    renderOverview(overview);
    renderTrend(trend.points);
    renderHypotheses(diagnosis.hypotheses || [], diagnosis);
    renderRuns(runs.runs || []);
  } catch (error) {
    console.error(error);
    showToast("Dashboard refresh failed: " + error.message);
  }
}

async function runDiagnosis() {
  const buttons = [$("runButton"), $("runButtonSecondary")].filter(Boolean);
  buttons.forEach(button => { button.disabled = true; button.dataset.oldText = button.innerHTML; button.textContent = "Analysing…"; });
  try {
    const result = await api("/api/diagnostics/run", { method: "POST" });
    renderHypotheses(result.hypotheses, result);
    const runs = await api("/api/runs?limit=8");
    renderRuns(runs.runs || []);
    showToast("Diagnostic completed and saved to SQLite.");
  } catch (error) {
    showToast("Diagnostic failed: " + error.message);
  } finally {
    buttons.forEach(button => { button.disabled = false; button.innerHTML = button.dataset.oldText; });
  }
}

async function reseed() {
  const button = $("seedButton");
  if (!window.confirm("Reset and regenerate the synthetic 60-day history and 7-day forecast? Existing diagnostic run history will be cleared.")) return;
  button.disabled = true;
  button.textContent = "Seeding…";
  try {
    const result = await api("/api/seed", { method: "POST" });
    showToast(`Seeded ${nf.format(result.actual_rows)} actual and ${nf.format(result.forecast_rows)} forecast rows.`);
    await loadAll();
  } catch (error) {
    showToast("Reseed failed: " + error.message);
  } finally {
    button.disabled = false;
    button.innerHTML = "↻ <span>Reseed data</span>";
  }
}

function setupNavigation() {
  document.querySelectorAll(".nav-item").forEach(button => {
    button.addEventListener("click", () => {
      document.querySelectorAll(".nav-item").forEach(item => item.classList.toggle("active", item === button));
      const target = document.getElementById(button.dataset.target);
      target?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  });
  const sections = ["overview", "funnel", "diagnostics", "agent"].map(id => $(id));
  const observer = new IntersectionObserver(entries => {
    const visible = entries.filter(entry => entry.isIntersecting).sort((a,b) => b.intersectionRatio - a.intersectionRatio)[0];
    if (!visible) return;
    document.querySelectorAll(".nav-item").forEach(item => item.classList.toggle("active", item.dataset.target === visible.target.id));
  }, { rootMargin: "-100px 0px -62% 0px", threshold: [0, .15, .35] });
  sections.forEach(section => observer.observe(section));
}

$("rangeSelect").addEventListener("change", async event => {
  selectedDays = Number(event.target.value);
  await loadAll();
});
$("runButton").addEventListener("click", runDiagnosis);
$("runButtonSecondary").addEventListener("click", runDiagnosis);
$("seedButton").addEventListener("click", reseed);
setupNavigation();
loadAll();
setInterval(loadAll, 15000);
