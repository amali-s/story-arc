// Rendering only. All judgments come from the backend (System One answers) and
// all policy (intensity, color, thresholds) is composed server-side in compose.py.

const FEELINGS = ["joy", "rage", "despair", "relief"];
const STAGES = [
  ["search", "Searching Wikipedia"],
  ["plot", "Fetching the plot summary"],
  ["characters", "Finding the main characters"],
  ["journeys", "Analyzing segments"],
];
const SVG_NS = "http://www.w3.org/2000/svg";

const $ = (id) => document.getElementById(id);
const statusEl = $("status");
const chartEl = $("chart");
const tooltip = $("tooltip");

let current = null; // last analysis, kept so resizes re-render without refetching
let active = 0; // which character's journey is drawn
let busy = false;

const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
const motionOK = () => !reducedMotion.matches;

// Replays a CSS entrance animation on an element that is already on screen.
function replay(node, cls) {
  node.classList.remove(cls);
  void node.offsetWidth;
  node.classList.add(cls);
}

// ---------------------------------------------------------------------------
// Theme: Auto (follows the system), Light, or Dark
// ---------------------------------------------------------------------------

const THEME_KEY = "story-arc-theme";

function currentTheme() {
  return document.documentElement.dataset.theme || "auto";
}

function markTheme(instant = false) {
  const choice = currentTheme();
  const buttons = document.querySelectorAll("[data-theme-choice]");
  buttons.forEach((b) => {
    const on = b.dataset.themeChoice === choice;
    b.setAttribute("aria-checked", String(on));
    b.tabIndex = on ? 0 : -1;
  });
  const on = document.querySelector(`[data-theme-choice="${choice}"]`);
  const pill = document.querySelector(".theme-pill");
  if (instant) pill.style.transition = "none";
  pill.style.transform = `translateX(${on.offsetLeft}px) scaleX(${on.offsetWidth})`;
  if (instant) { void pill.offsetWidth; pill.style.transition = ""; }
}

let themeShift = 0;
function setTheme(choice) {
  if (choice === currentTheme()) return;
  const root = document.documentElement;
  if (motionOK()) {
    root.classList.add("theme-shift");
    clearTimeout(themeShift);
    themeShift = setTimeout(() => root.classList.remove("theme-shift"), 450);
  }
  if (choice === "auto") delete root.dataset.theme;
  else root.dataset.theme = choice;
  try {
    if (choice === "auto") localStorage.removeItem(THEME_KEY);
    else localStorage.setItem(THEME_KEY, choice);
  } catch {}
  markTheme();
}

document.querySelectorAll("[data-theme-choice]").forEach((b) =>
  b.addEventListener("click", () => setTheme(b.dataset.themeChoice)));
// Arrow keys move between options, as in any radio group.
document.querySelector(".theme-switch").addEventListener("keydown", (e) => {
  const order = ["auto", "light", "dark"];
  const dir = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[e.key];
  if (!dir) return;
  e.preventDefault();
  const next = order[(order.indexOf(currentTheme()) + dir + order.length) % order.length];
  setTheme(next);
  document.querySelector(`[data-theme-choice="${next}"]`).focus();
});
markTheme(true);
document.fonts.ready.then(() => markTheme(true));

// ---------------------------------------------------------------------------
// Tabs
// ---------------------------------------------------------------------------

function selectTab(which) {
  const isSearch = which === "search";
  $("tab-search").setAttribute("aria-selected", String(isSearch));
  $("tab-text").setAttribute("aria-selected", String(!isSearch));
  $("pane-search").hidden = !isSearch;
  $("pane-text").hidden = isSearch;
  moveTabInk();
}

// One underline that slides between tabs, so the eye follows the change.
function moveTabInk(instant = false) {
  const ink = document.querySelector(".tab-ink");
  const tab = document.querySelector('.tabs [aria-selected="true"]');
  if (instant) ink.style.transition = "none";
  ink.style.transform = `translateX(${tab.offsetLeft}px) scaleX(${tab.offsetWidth})`;
  if (instant) { void ink.offsetWidth; ink.style.transition = ""; }
}
moveTabInk(true);
window.addEventListener("resize", () => { moveTabInk(true); markTheme(true); });
document.fonts.ready.then(() => moveTabInk(true));
$("tab-search").addEventListener("click", () => selectTab("search"));
$("tab-text").addEventListener("click", () => selectTab("text"));

$("sample").addEventListener("click", () => {
  $("title").value = "The Keeper of Kell";
  $("story").value = window.SAMPLE_STORY;
});

// ---------------------------------------------------------------------------
// Stage indicator
// ---------------------------------------------------------------------------

function resetStages(skip = []) {
  $("stages").replaceChildren(
    ...STAGES.filter(([id]) => !skip.includes(id)).map(([id, label], i) => {
      const li = document.createElement("li");
      li.style.setProperty("--i", i);
      li.dataset.stage = id;
      li.dataset.state = "pending";
      li.innerHTML = `<span class="tick" aria-hidden="true"></span><span class="what">${label}</span><span class="detail"></span>`;
      return li;
    })
  );
}

function setStage(id, state, detail = "") {
  const li = $("stages").querySelector(`[data-stage="${id}"]`);
  if (!li) return;
  li.dataset.state = state;
  li.querySelector(".detail").textContent = detail;
}

function setStatus(text, isError = false) {
  statusEl.textContent = text;
  statusEl.classList.toggle("error", isError);
}

function failStage(id, message) {
  setStage(id, "error");
  setStatus(message, true);
}

// ---------------------------------------------------------------------------
// API
// ---------------------------------------------------------------------------

function errorText(body) {
  if (typeof body.detail === "string") return body.detail;
  if (Array.isArray(body.detail)) return body.detail.map((d) => d.msg).join("; ");
  return "";
}

async function api(path, options) {
  const res = await fetch(path, options);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(errorText(body) || `Request failed (${res.status})`);
  return body;
}

const post = (path, source) =>
  api(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(source),
  });

// ---------------------------------------------------------------------------
// Search
// ---------------------------------------------------------------------------

$("search-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const query = $("query").value.trim();
  if (!query || busy) return;

  busy = true;
  $("search-btn").disabled = true;
  $("results").replaceChildren();
  resetStages();
  setStage("search", "active");
  setStatus("");
  try {
    const body = await api(`/api/search?q=${encodeURIComponent(query)}`);
    if (!body.results.length) {
      failStage("search", `No Wikipedia articles found for “${query}”. Try a different title.`);
      return;
    }
    setStage("search", "done", `${body.results.length} results`);
    showResults(body.results);
    setStatus("Pick the article that matches the story you mean.");
  } catch (err) {
    failStage("search", err.message);
  } finally {
    busy = false;
    $("search-btn").disabled = false;
  }
});

function showResults(results) {
  $("results").replaceChildren(
    ...results.map((r, i) => {
      const li = document.createElement("li");
      li.style.setProperty("--i", i);
      const button = document.createElement("button");
      button.type = "button";
      button.className = "result";
      // A year with no media type ("1966") reads as noise, so the compact form
      // needs a type; otherwise fall back to the raw short description. Only
      // the type is capitalized: a fallback description is a sentence, and
      // title-casing it gives "Character In The Novel The Great Gatsby".
      const kind = r.media_type
        ? [r.media_type[0].toUpperCase() + r.media_type.slice(1), r.year].filter(Boolean).join(" · ")
        : r.description;
      button.innerHTML = `<span class="r-title">${esc(r.title)}</span>
        ${kind ? `<span class="r-meta">${esc(kind)}</span>` : ""}`;
      button.addEventListener("click", () => {
        $("results").querySelectorAll(".result").forEach((b) => b.classList.remove("chosen"));
        button.classList.add("chosen");
        run({ kind: "wikipedia", pageid: r.pageid }, ["search"]);
      });
      li.appendChild(button);
      return li;
    })
  );
}

// ---------------------------------------------------------------------------
// Paste-text path
// ---------------------------------------------------------------------------

$("analyze").addEventListener("click", () => {
  const story = $("story").value;
  if (!story.trim() || busy) return;
  run({ kind: "text", story, title: $("title").value.trim() || "Pasted text" }, ["search"]);
});

// ---------------------------------------------------------------------------
// Pipeline: plot -> characters -> journeys, one stage at a time
// ---------------------------------------------------------------------------

async function run(source, skip) {
  busy = true;
  $("analyze").disabled = true;
  $("search-btn").disabled = true;
  resetStages(skip);
  setStatus("");
  chartEl.classList.add("loading");

  try {
    setStage("plot", "active");
    const doc = await post("/api/plot", source);
    setStage("plot", "done", `${doc.segments.length} segments`);

    setStage("characters", "active");
    const found = await post("/api/characters", source);
    setStage("characters", "done", found.characters.map((c) => c.name).join(", "));

    setStage("journeys", "active");
    const body = await post("/api/journeys", source);
    setStage("journeys", "done", `${body.points.length} segments`);

    current = body;
    active = 0;
    drawn = null;
    chartEl.classList.remove("loading");
    renderAll();

    const n = body.points.length;
    const calls = body.api_calls;
    setStatus(
      `${n} segment${n === 1 ? "" : "s"} × ${body.characters.length} characters · ` +
        `${calls} new System One call${calls === 1 ? "" : "s"}, ${n + 1 - calls} from cache`
    );
  } catch (err) {
    const stage = $("stages").querySelector('[data-state="active"]');
    failStage(stage ? stage.dataset.stage : "journeys", err.message);
    chartEl.classList.remove("loading");
  } finally {
    busy = false;
    $("analyze").disabled = false;
    $("search-btn").disabled = false;
  }
}

// ---------------------------------------------------------------------------
// Header, source line, character toggle
// ---------------------------------------------------------------------------

function renderAll() {
  const { characters, document: doc, note } = current;

  const source = $("source");
  source.innerHTML = doc.url
    ? `Plot summary from <a href="${esc(doc.url)}" target="_blank" rel="noopener noreferrer">${esc(doc.title)}</a>
       · section “${esc(doc.section)}” · ${esc(doc.attribution)}`
    : `Pasted text · ${doc.segments.length} paragraphs`;
  source.hidden = false;

  const noteEl = $("note");
  noteEl.textContent = note || "";
  noteEl.hidden = !note;

  const toggle = $("toggle");
  toggle.replaceChildren(
    ...characters.map((c, k) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "chip";
      button.style.setProperty("--i", k);
      button.setAttribute("aria-pressed", String(k === active));
      button.innerHTML = `<span class="chip-name">${esc(c.name)}</span><span class="chip-meta">${c.mentions} mentions</span>`;
      button.title =
        `is a character ${c.is_character.toFixed(2)} · drives the plot ${c.is_main_character.toFixed(2)} · rank score ${c.score.toFixed(2)}`;
      button.addEventListener("click", () => {
        if (k === active) return;
        active = k;
        toggle.querySelectorAll(".chip").forEach((b, j) => b.setAttribute("aria-pressed", String(j === k)));
        render("morph");
      });
      return button;
    })
  );
  toggle.hidden = false;
  replay(toggle, "enter");

  render("draw");
}

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

// The composed values for the character currently shown.
const shown = (point) => point.composed[active];

// ---------------------------------------------------------------------------
// Motion helpers. The chart has two moves: a first "draw" that reads the story
// left to right, and a "morph" that bends one character's line into another's.
// ---------------------------------------------------------------------------

const easeInOut = (t) => (t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2);
const clamp01 = (t) => Math.min(1, Math.max(0, t));
const lerp = (a, b, t) => a + (b - a) * t;
function mixHex(a, b, t) {
  if (a === b || t >= 1) return b;
  const pa = parseInt(a.slice(1), 16), pb = parseInt(b.slice(1), 16);
  const ch = (v, sh) => (v >> sh) & 255;
  const out = [16, 8, 0].map((sh) => Math.round(lerp(ch(pa, sh), ch(pb, sh), t)));
  return `#${out.map((v) => v.toString(16).padStart(2, "0")).join("")}`;
}

// What the line looks like, in value space, for the character shown.
const targetState = () =>
  current.points.map((p) => {
    const c = shown(p);
    return { v: c.intensity, color: c.color, dashed: c.intensity_uncertain };
  });

let drawn = null; // the state currently on screen, mid-animation or not
let motion = 0; // rAF id of the running chart animation

function tween(duration, step) {
  cancelAnimationFrame(motion);
  const t0 = performance.now();
  const frame = (now) => {
    const t = (now - t0) / duration;
    step(Math.min(t, 1), now - t0);
    if (t < 1) motion = requestAnimationFrame(frame);
  };
  motion = requestAnimationFrame(frame);
}

function render(mode = "static") {
  if (!current) return;
  cancelAnimationFrame(motion);
  if (!motionOK()) mode = "static";
  if (mode === "morph" && !drawn) mode = "draw";

  const { points, characters } = current;
  const who = characters[active].name;
  const title = $("chart-title");
  title.textContent = `${who}'s journey`;
  if (mode !== "static") replay(title, "swap");
  const models = [...new Set(points.map((p) => p.model))].join(", ");
  $("meta").textContent = `${points.length} segments · model ${models}`;

  const width = Math.max(chartEl.clientWidth, 300);
  const compact = width < 560;
  const height = compact ? 320 : 460;
  const pad = { top: 22, right: 20, bottom: 50, left: compact ? 38 : 52 };
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;
  const n = points.length;
  const xAt = (i) => pad.left + (n === 1 ? plotW / 2 : (i / (n - 1)) * plotW);
  const yAt = (v) => pad.top + (1 - v) * plotH;
  const xs = points.map((_, i) => xAt(i));

  hideTooltip(true);
  chartEl.replaceChildren();
  const svg = el("svg", { viewBox: `0 0 ${width} ${height}`, role: "img",
    "aria-label": `Line chart of ${who}'s action intensity across ${n} story segments` });
  chartEl.appendChild(svg);
  const defs = el("defs", {}, svg);

  // Axes and frame: staged first on a fresh draw, so the stage is set before the actor enters.
  const axis = el("g", { class: mode === "draw" ? "axis" : "axis static" }, svg);
  const right = width - pad.right;
  const bottom = yAt(0);
  for (const v of [0.25, 0.5, 0.75, 1]) {
    el("line", { x1: pad.left, x2: right, y1: yAt(v), y2: yAt(v) }, axis);
  }
  for (const v of [0, 0.25, 0.5, 0.75, 1]) {
    const t = el("text", { x: pad.left - 10, y: yAt(v) + 4, "text-anchor": "end" }, axis);
    t.textContent = v.toFixed(v % 1 ? 2 : 0);
  }
  const frame = el("g", { class: "frame" }, axis);
  const inset = 5;
  el("rect", { class: "outer", x: pad.left - inset, y: pad.top - inset,
    width: plotW + inset * 2, height: plotH + inset * 2 }, frame);
  for (const x of [pad.left, right]) el("line", { class: "edge", x1: x, x2: x, y1: pad.top, y2: bottom }, frame);
  el("line", { class: "baseline", x1: pad.left, x2: right, y1: bottom, y2: bottom }, frame);
  el("line", { class: "fillet", x1: pad.left - inset, x2: right + inset, y1: bottom + inset, y2: bottom + inset }, frame);
  // Acanthus volutes nestle into the top corners, under the line.
  const vs = compact ? 34 : 50;
  const volute = (x, flip) => el("use", { href: "#volute", class: mode === "draw" ? "volute unfurl" : "volute",
    x: 0, y: 0, width: vs, height: vs,
    transform: `translate(${x} ${pad.top - inset}) scale(${flip ? -1 : 1} 1)` }, frame);
  volute(pad.left - inset, false);
  volute(right + inset, true);

  const step = Math.ceil(n / Math.max(1, Math.floor(plotW / 34)));
  points.forEach((p, i) => {
    if (i % step && i !== n - 1) return;
    el("line", { class: "tick", x1: xAt(i), x2: xAt(i), y1: bottom + inset, y2: bottom + inset + 4 }, frame);
    const t = el("text", { x: xAt(i), y: bottom + inset + 18, "text-anchor": "middle" }, axis);
    t.textContent = i + 1;
  });
  const xt = el("text", { x: pad.left + plotW / 2, y: height - 6, "text-anchor": "middle", class: "axis-title" }, axis);
  xt.textContent = "Story time, by segment  →";
  if (!compact) {
    const yt = el("text", { x: 10, y: pad.top + plotH / 2, "text-anchor": "middle", class: "axis-title",
      transform: `rotate(-90 10 ${pad.top + plotH / 2})` }, axis);
    yt.textContent = "Action intensity";
  }

  // Line: each piece i→i+1 is split at its midpoint so the half nearest a point
  // takes that point's dash; color is a gradient between the two.
  // The elements are built once here and updated in place by paint().
  const clip = el("clipPath", { id: "reveal" }, defs);
  const clipRect = el("rect", { x: 0, y: 0, width: width + 20, height }, clip);
  const line = el("g", { fill: "none", "stroke-width": 3.5, "stroke-linecap": "round",
    "clip-path": "url(#reveal)" }, svg);
  const pieces = [];
  for (let i = 0; i < n - 1; i++) {
    const g = el("linearGradient", { id: `g${i}`, gradientUnits: "userSpaceOnUse",
      x1: xs[i], x2: xs[i + 1], y1: 0, y2: 0 }, defs);
    pieces.push({
      stops: [el("stop", { offset: "0" }, g), el("stop", { offset: "1" }, g)],
      halves: [0, 1].map(() => el("path", { stroke: `url(#g${i})` }, line)),
    });
  }

  const guide = el("line", { class: "guide", x1: 0, x2: 0, y1: pad.top, y2: pad.top + plotH }, svg);
  const dots = points.map(() => el("circle", { class: "dot", r: 5 }, svg));

  function paint(state) {
    drawn = state;
    const ys = state.map((s) => yAt(s.v));
    if (n > 1) {
      const m = monotoneTangents(xs, ys);
      pieces.forEach((piece, i) => {
        piece.stops[0].setAttribute("stop-color", state[i].color);
        piece.stops[1].setAttribute("stop-color", state[i + 1].color);
        const d = splitPiece(xs[i], ys[i], xs[i + 1], ys[i + 1], m[i], m[i + 1]);
        piece.halves.forEach((path, h) => {
          const s = state[i + h];
          path.setAttribute("d", d[h]);
          path.setAttribute("stroke-dasharray", s.dashed ? "6 6" : "none");
        });
      });
    }
    dots.forEach((dot, i) => {
      dot.setAttribute("cx", xs[i]);
      dot.setAttribute("cy", ys[i]);
      dot.setAttribute("fill", state[i].color);
    });
  }

  const target = targetState();

  if (mode === "draw") {
    // The line is revealed in story order, dots landing as the pen reaches them.
    paint(target);
    dots.forEach((d) => (d.style.visibility = "hidden"));
    clipRect.setAttribute("width", 0);
    const duration = Math.min(2200, 700 + n * 45);
    const delay = 250; // let the axes settle first
    let landed = 0;
    tween(duration + delay, (_, ms) => {
      const t = easeInOut(clamp01((ms - delay) / duration));
      const x = lerp(pad.left - 6, width - pad.right + 6, t);
      clipRect.setAttribute("width", t >= 1 ? width + 20 : x);
      while (landed < n && (xs[landed] <= x || t >= 1)) {
        dots[landed].style.visibility = "";
        dots[landed].classList.add("pop");
        landed++;
      }
    });
  } else if (mode === "morph") {
    // Each point starts a beat after the one before it, so the change sweeps
    // through story time instead of snapping all at once.
    const from = drawn.length === n ? drawn : target;
    const each = 520;
    const lag = Math.min(28, 420 / Math.max(1, n));
    tween(each + lag * (n - 1), (_, ms) => {
      paint(target.map((to, i) => {
        const a = from[i];
        const t = easeInOut(clamp01((ms - i * lag) / each));
        return {
          v: lerp(a.v, to.v, t),
          color: mixHex(a.color, to.color, t),
          dashed: t < 0.5 ? a.dashed : to.dashed,
        };
      }));
    });
  } else {
    paint(target);
  }

  // Hover/focus targets
  const colW = n === 1 ? plotW : plotW / (n - 1);
  points.forEach((p, i) => {
    const hit = el("rect", { class: "hit", x: xs[i] - colW / 2, y: pad.top, width: colW,
      height: plotH, tabindex: 0, "aria-label": ariaFor(p, who) }, svg);
    const show = () => {
      guide.style.transform = `translateX(${xs[i]}px)`;
      if (!guide.classList.contains("on")) { void guide.getBoundingClientRect(); guide.classList.add("on"); }
      dots.forEach((d, j) => d.classList.toggle("on", j === i));
      showTooltip(p, dots[i].getBoundingClientRect());
    };
    hit.addEventListener("mouseenter", show);
    hit.addEventListener("focus", show);
    hit.addEventListener("blur", () => { dots[i].classList.remove("on"); });
  });
  // Leaving the plot hides the guide; moving between columns glides it.
  svg.addEventListener("mouseleave", () => {
    guide.classList.remove("on");
    dots.forEach((d) => d.classList.remove("on"));
    hideTooltip();
  });
  svg.addEventListener("focusout", (e) => {
    if (svg.contains(e.relatedTarget)) return;
    guide.classList.remove("on");
    hideTooltip();
  });
}

// Only a width change needs a redraw; the chart's own height changing (empty
// state -> chart) must not cut a running animation short.
let resizeFrame = 0;
let chartWidth = chartEl.clientWidth;
new ResizeObserver(() => {
  if (chartEl.clientWidth === chartWidth) return;
  chartWidth = chartEl.clientWidth;
  cancelAnimationFrame(resizeFrame);
  resizeFrame = requestAnimationFrame(() => current && render());
}).observe(chartEl);

function ariaFor(p, who) {
  const f = p.answers[`narrated_feeling_${active}`];
  return `Segment ${p.index + 1} for ${who}: intensity ${shown(p).intensity.toFixed(2)}, feeling ${f.choice} at confidence ${f.confidence.toFixed(2)}`;
}

// ---------------------------------------------------------------------------
// Tooltip: why TypeSafe scored the point this way, for this character
// ---------------------------------------------------------------------------

const pct = (v) => `${Math.round(v * 100)}%`;
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

function row(label, value, display, isTop = false, i = 0) {
  return `<div class="row${isTop ? " is-top" : ""}"><span>${label}</span>
    <span class="bar"><b style="width:${(value * 100).toFixed(1)}%;--i:${i}"></b></span>
    <span class="num">${display}</span></div>`;
}

function showTooltip(p, anchor) {
  const c = shown(p);
  const who = current.characters[active].name;
  const feel = p.answers[`narrated_feeling_${active}`];
  const excerpt = p.text.length > 220 ? `${p.text.slice(0, 220).trimEnd()}…` : p.text;
  const tags = [
    c.stage_clamped && "stage-setting",
    c.intensity_uncertain && "uncertain intensity",
    c.offstage && "offstage",
  ].filter(Boolean).map((t) => `<span class="tag">${t}</span>`).join("");

  tooltip.innerHTML = `
    <div class="tt-head"><span class="tt-swatch" style="background:${c.color}"></span>
      Segment ${p.index + 1} · ${esc(who)}<div class="tt-tags">${tags}</div></div>
    <p class="tt-excerpt">“${esc(excerpt)}”</p>

    <div class="tt-section"><h4><span>Feeling around ${esc(who)}</span><span>confidence ${feel.confidence.toFixed(2)}</span></h4>
      ${FEELINGS.map((f, i) => row(
        `<i class="key" data-feeling="${f}"></i>${f}`,
        feel.probabilities[f] ?? 0, pct(feel.probabilities[f] ?? 0), f === feel.choice, i)).join("")}
    </div>

    <div class="note">Pacing features (code): avg sentence ${p.features.avg_sentence_length} words,
      ${pct(p.features.short_sentence_share)} short, ${p.features.exclamations}! ${p.features.questions}?,
      dialogue ${pct(p.features.dialogue_ratio)} → ${c.pacing.value.toFixed(2)}</div>`;

  // Already open: glide to the new point. Closed (or closing): pop in in place.
  const entering = tooltip.hidden || tooltip.classList.contains("leave");
  clearTimeout(tooltipTimer);
  tooltip.classList.remove("leave");
  tooltip.classList.toggle("jump", entering);
  tooltip.hidden = false;
  const tw = tooltip.offsetWidth, th = tooltip.offsetHeight;
  const vw = window.innerWidth, vh = window.innerHeight;
  let left = anchor.right + 14;
  const flipped = left + tw > vw - 12;
  if (flipped) left = anchor.left - tw - 14;
  left = Math.max(12, Math.min(left, vw - tw - 12));
  const top = Math.max(12, Math.min(anchor.top - th / 2, vh - th - 12));
  tooltip.style.setProperty("--tt-origin", flipped ? "right center" : "left center");
  tooltip.style.left = `${left}px`;
  tooltip.style.top = `${top}px`;
  if (entering) replay(tooltip, "enter");
}

let tooltipTimer = 0;
function hideTooltip(instant = false) {
  clearTimeout(tooltipTimer);
  if (tooltip.hidden) return;
  if (instant || !motionOK()) {
    tooltip.hidden = true;
    tooltip.classList.remove("enter", "leave");
    return;
  }
  tooltip.classList.remove("enter");
  tooltip.classList.add("leave");
  tooltipTimer = setTimeout(() => {
    tooltip.hidden = true;
    tooltip.classList.remove("leave");
  }, 130);
}

window.addEventListener("scroll", () => hideTooltip(true), { passive: true });
