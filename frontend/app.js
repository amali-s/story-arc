// Rendering only. All judgments come from the backend (System One answers) and
// all policy (intensity, color, thresholds) is composed server-side in compose.py.

const FEELINGS = ["joy", "rage", "despair", "relief"];
const NOULS = [
  ["character_is_focal", "Character focal"],
  ["sets_the_stage", "Sets the stage"],
  ["physical_action", "Physical action"],
  ["active_conflict", "Active conflict"],
  ["stakes_raised", "Stakes raised"],
  ["urgent_pacing", "Urgent pacing"],
];
const SVG_NS = "http://www.w3.org/2000/svg";

const $ = (id) => document.getElementById(id);
const form = $("form");
const statusEl = $("status");
const chartEl = $("chart");
const tooltip = $("tooltip");
let current = null; // last analysis, kept so resizes re-render without refetching

$("sample").addEventListener("click", () => {
  $("character").value = window.SAMPLE_CHARACTER;
  $("story").value = window.SAMPLE_STORY;
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const story = $("story").value;
  const character = $("character").value.trim();
  if (!story.trim() || !character) return;

  $("analyze").disabled = true;
  setStatus("Asking System One about each segment…");
  try {
    const res = await fetch("/api/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ story, character }),
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(errorText(body) || `Request failed (${res.status})`);
    current = body;
    render();
    const n = body.points.length;
    setStatus(
      `${n} segment${n === 1 ? "" : "s"} · ${body.api_calls} new System One call${body.api_calls === 1 ? "" : "s"}, ${n - body.api_calls} from cache`
    );
  } catch (err) {
    setStatus(err.message, true);
  } finally {
    $("analyze").disabled = false;
  }
});

function errorText(body) {
  if (typeof body.detail === "string") return body.detail;
  if (Array.isArray(body.detail)) return body.detail.map((d) => d.msg).join("; ");
  return "";
}

function setStatus(text, isError = false) {
  statusEl.textContent = text;
  statusEl.classList.toggle("error", isError);
}

let resizeFrame = 0;
new ResizeObserver(() => {
  cancelAnimationFrame(resizeFrame);
  resizeFrame = requestAnimationFrame(() => current && render());
}).observe(chartEl);

// ---------------------------------------------------------------------------
// Chart
// ---------------------------------------------------------------------------

function el(name, attrs = {}, parent) {
  const node = document.createElementNS(SVG_NS, name);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (parent) parent.appendChild(node);
  return node;
}

// Fritsch–Carlson monotone cubic tangents: smooth, but never overshoots, so
// flat stretches stay flat and sudden jumps stay sharp.
function monotoneTangents(xs, ys) {
  const n = xs.length;
  const d = [];
  for (let i = 0; i < n - 1; i++) d.push((ys[i + 1] - ys[i]) / (xs[i + 1] - xs[i]));
  const m = new Array(n);
  m[0] = d[0];
  m[n - 1] = d[n - 2];
  for (let i = 1; i < n - 1; i++) m[i] = d[i - 1] * d[i] <= 0 ? 0 : (d[i - 1] + d[i]) / 2;
  for (let i = 0; i < n - 1; i++) {
    if (d[i] === 0) { m[i] = 0; m[i + 1] = 0; continue; }
    const a = m[i] / d[i];
    const b = m[i + 1] / d[i];
    const s = a * a + b * b;
    if (s > 9) {
      const t = 3 / Math.sqrt(s);
      m[i] = t * a * d[i];
      m[i + 1] = t * b * d[i];
    }
  }
  return m;
}

// Split one Hermite piece (as a cubic Bézier) at t = 0.5 into two halves.
function splitPiece(x0, y0, x1, y1, m0, m1) {
  const h = x1 - x0;
  const p0 = [x0, y0];
  const p1 = [x0 + h / 3, y0 + (m0 * h) / 3];
  const p2 = [x1 - h / 3, y1 - (m1 * h) / 3];
  const p3 = [x1, y1];
  const mid = (a, b) => [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
  const a = mid(p0, p1), b = mid(p1, p2), c = mid(p2, p3);
  const ab = mid(a, b), bc = mid(b, c), m = mid(ab, bc);
  const f = (p) => `${p[0].toFixed(2)},${p[1].toFixed(2)}`;
  return [
    `M${f(p0)}C${f(a)} ${f(ab)} ${f(m)}`,
    `M${f(m)}C${f(bc)} ${f(c)} ${f(p3)}`,
  ];
}

function render() {
  const { points, character } = current;
  $("chart-title").textContent = `${character}'s journey`;
  const models = [...new Set(points.map((p) => p.model))].join(", ");
  $("meta").textContent = `${points.length} segments · model ${models}`;

  const width = Math.max(chartEl.clientWidth, 300);
  const compact = width < 560;
  const height = compact ? 280 : 380;
  const pad = { top: 18, right: 16, bottom: 44, left: compact ? 34 : 46 };
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;
  const n = points.length;
  const xAt = (i) => pad.left + (n === 1 ? plotW / 2 : (i / (n - 1)) * plotW);
  const yAt = (v) => pad.top + (1 - v) * plotH;

  tooltip.hidden = true;
  chartEl.replaceChildren();
  const svg = el("svg", { viewBox: `0 0 ${width} ${height}`, role: "img",
    "aria-label": `Line chart of ${character}'s action intensity across ${n} story segments` });
  chartEl.appendChild(svg);
  const defs = el("defs", {}, svg);

  // Axes
  const axis = el("g", { class: "axis" }, svg);
  for (const v of [0, 0.25, 0.5, 0.75, 1]) {
    el("line", { x1: pad.left, x2: width - pad.right, y1: yAt(v), y2: yAt(v),
      class: v === 0 ? "baseline" : "" }, axis);
    const t = el("text", { x: pad.left - 8, y: yAt(v) + 4, "text-anchor": "end" }, axis);
    t.textContent = v.toFixed(v % 1 ? 2 : 0);
  }
  const step = Math.ceil(n / Math.max(1, Math.floor(plotW / 34)));
  points.forEach((p, i) => {
    if (i % step && i !== n - 1) return;
    const t = el("text", { x: xAt(i), y: height - pad.bottom + 18, "text-anchor": "middle" }, axis);
    t.textContent = i + 1;
  });
  const xt = el("text", { x: pad.left + plotW / 2, y: height - 6, "text-anchor": "middle", class: "axis-title" }, axis);
  xt.textContent = "Story time (segment) →";
  if (!compact) {
    const yt = el("text", { x: 12, y: pad.top + plotH / 2, "text-anchor": "middle", class: "axis-title",
      transform: `rotate(-90 12 ${pad.top + plotH / 2})` }, axis);
    yt.textContent = "Action intensity";
  }

  // Line: each piece i→i+1 is split at its midpoint so the half nearest a point
  // takes that point's dash and opacity; color is a gradient between the two.
  const xs = points.map((_, i) => xAt(i));
  const ys = points.map((p) => yAt(p.composed.intensity));
  const line = el("g", { fill: "none", "stroke-width": 3.5, "stroke-linecap": "round" }, svg);
  if (n > 1) {
    const m = monotoneTangents(xs, ys);
    for (let i = 0; i < n - 1; i++) {
      const gid = `g${i}`;
      const g = el("linearGradient", { id: gid, gradientUnits: "userSpaceOnUse",
        x1: xs[i], x2: xs[i + 1], y1: 0, y2: 0 }, defs);
      el("stop", { offset: "0", "stop-color": points[i].composed.color }, g);
      el("stop", { offset: "1", "stop-color": points[i + 1].composed.color }, g);
      const halves = splitPiece(xs[i], ys[i], xs[i + 1], ys[i + 1], m[i], m[i + 1]);
      [i, i + 1].forEach((owner, h) => {
        const c = points[owner].composed;
        el("path", { d: halves[h], stroke: `url(#${gid})`, "stroke-opacity": c.opacity,
          "stroke-dasharray": c.intensity_uncertain ? "6 6" : "none" }, line);
      });
    }
  }

  // Guide, dots, and hover/focus targets
  const guide = el("line", { class: "guide", y1: pad.top, y2: pad.top + plotH }, svg);
  const dots = points.map((p, i) =>
    el("circle", { class: "dot", cx: xs[i], cy: ys[i], r: 5, fill: p.composed.color,
      "fill-opacity": p.composed.opacity }, svg));
  const colW = n === 1 ? plotW : plotW / (n - 1);
  points.forEach((p, i) => {
    const hit = el("rect", { class: "hit", x: xs[i] - colW / 2, y: pad.top, width: colW,
      height: plotH, tabindex: 0, "aria-label": ariaFor(p) }, svg);
    const show = () => {
      guide.setAttribute("x1", xs[i]); guide.setAttribute("x2", xs[i]);
      guide.classList.add("on"); dots[i].classList.add("on");
      showTooltip(p, dots[i].getBoundingClientRect());
    };
    const hide = () => { guide.classList.remove("on"); dots[i].classList.remove("on"); tooltip.hidden = true; };
    hit.addEventListener("mouseenter", show);
    hit.addEventListener("focus", show);
    hit.addEventListener("mouseleave", hide);
    hit.addEventListener("blur", hide);
  });
}

function topFeeling(p) {
  return p.answers.narrated_feeling.choice;
}

function ariaFor(p) {
  const f = p.answers.narrated_feeling;
  return `Segment ${p.index + 1}: intensity ${p.composed.intensity.toFixed(2)}, feeling ${f.choice} at confidence ${f.confidence.toFixed(2)}`;
}

// ---------------------------------------------------------------------------
// Tooltip: why TypeSafe scored the point this way
// ---------------------------------------------------------------------------

const pct = (v) => `${Math.round(v * 100)}%`;
const esc = (s) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

function row(label, value, display, isTop = false) {
  return `<div class="row${isTop ? " is-top" : ""}"><span>${label}</span>
    <span class="bar"><b style="width:${(value * 100).toFixed(1)}%"></b></span>
    <span class="num">${display}</span></div>`;
}

function showTooltip(p, anchor) {
  const c = p.composed;
  const feel = p.answers.narrated_feeling;
  const act = p.answers.action_intensity;
  const excerpt = p.text.length > 220 ? `${p.text.slice(0, 220).trimEnd()}…` : p.text;
  const tags = [
    c.stage_clamped && "stage-setting",
    c.intensity_uncertain && "uncertain intensity",
    c.offstage && "offstage",
  ].filter(Boolean).map((t) => `<span class="tag">${t}</span>`).join("");

  tooltip.innerHTML = `
    <div class="tt-head"><span class="tt-swatch" style="background:${c.color}"></span>
      Segment ${p.index + 1}<div class="tt-tags">${tags}</div></div>
    <p class="tt-excerpt">“${esc(excerpt)}”</p>

    <div class="tt-section"><h4><span>Feeling in the narration</span><span>confidence ${feel.confidence.toFixed(2)}</span></h4>
      ${FEELINGS.map((f) => row(
        `<i class="key" data-feeling="${f}"></i>${f}`,
        feel.probabilities[f] ?? 0, pct(feel.probabilities[f] ?? 0), f === topFeeling(p))).join("")}
    </div>

    <div class="tt-section"><h4><span>Intensity</span><span>${c.intensity.toFixed(2)}</span></h4>
      ${row("Action score", act.score / 4, `${act.score.toFixed(2)}/4`)}
      <div class="note">Action score confidence ${act.confidence.toFixed(2)}${c.intensity_uncertain ? " (below threshold, shown dashed)" : ""}.
      ${c.stage_clamped ? ` Flattened from ${c.raw_intensity.toFixed(2)} because the passage mainly sets the stage.` : ""}</div>
    </div>

    <div class="tt-section"><h4><span>Yes/no judgments (Noul)</span><span>P(yes)</span></h4>
      ${NOULS.map(([k, label]) => row(label, p.answers[k].noul, p.answers[k].noul.toFixed(2))).join("")}
    </div>

    <div class="note">Pacing features (code): avg sentence ${p.features.avg_sentence_length} words,
      ${pct(p.features.short_sentence_share)} short, ${p.features.exclamations}! ${p.features.questions}?,
      dialogue ${pct(p.features.dialogue_ratio)} → ${c.pacing.value.toFixed(2)}</div>`;

  tooltip.hidden = false;
  const tw = tooltip.offsetWidth, th = tooltip.offsetHeight;
  const vw = window.innerWidth, vh = window.innerHeight;
  let left = anchor.right + 14;
  if (left + tw > vw - 12) left = anchor.left - tw - 14;
  left = Math.max(12, Math.min(left, vw - tw - 12));
  const top = Math.max(12, Math.min(anchor.top - th / 2, vh - th - 12));
  tooltip.style.left = `${left}px`;
  tooltip.style.top = `${top}px`;
}

window.addEventListener("scroll", () => { tooltip.hidden = true; }, { passive: true });
