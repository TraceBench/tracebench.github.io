
const DATA_ROOT = "/public/data";
const DEFAULT_SCOPE = { task_mode: "Code", noise: "Low", context: "High", examples: "Three Examples" };
const TASK_MODE_OPTIONS = [
  { value: "Code", label: "CODE" },
  { value: "Direct", label: "DIRECT" },
];
const NOISE_OPTIONS = [
  { value: "None", label: "NO NOISE" },
  { value: "Low", label: "LOW NOISE" },
  { value: "High", label: "HIGH NOISE" },
];
const CONTEXT_OPTIONS = [
  { value: "None", label: "NO CONTEXT" },
  { value: "High", label: "HIGH CONTEXT" },
];
const EXAMPLE_OPTIONS = [
  { value: "None", label: "NO EXAMPLES" },
  { value: "One Example", label: "ONE EXAMPLE" },
  { value: "Three Examples", label: "MULTIPLE EXAMPLES" },
];
const NOISE_RULES = { rollingWindowPoints: 11, sigmaFloorRatio: 0.0001 };
const PLOT_NOISE_PROFILES = {
  None: { adaptive: 0.0 },
  Low: { adaptive: 0.002 },
  High: { adaptive: 0.008 },
};

const state = {
  site: null,
  summary: null,
  leaderboard: null,
  envDescriptions: {},
  envData: {},
  homeData: null,
  submissionDetails: {},
  home: { taskMode: "Code", noise: "None", context: "High", examples: "Three Examples", reveal: false },
  results: {
    scope: { task_mode: "Code", noise: "Low", context: "High", examples: "Three Examples" },
    compare: false,
    comparisonScope: { task_mode: "Direct", noise: "High", context: "High", examples: "Three Examples" },
    sortKey: "score",
    sortDir: "desc"
  },
  env: { taskMode: "Code", noise: "Low", context: "High", examples: "Three Examples", sampleIndex: {} }
};

const app = document.getElementById("app");
const plotPayloads = new Map();
let plotCounter = 0;

async function loadJson(path) {
  const response = await fetch(path, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`Could not load ${path}`);
  }
  return response.json();
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function siteValue(key, fallback = "") {
  return state.site?.[key] || fallback;
}

function siteHref(key) {
  return escapeHtml(siteValue(key, "#"));
}

function normalizePath(pathname) {
  if (!pathname || pathname === "/index.html") return "/";
  if (pathname.includes(".")) return pathname;
  return pathname.endsWith("/") ? pathname : `${pathname}/`;
}

function titleCase(value) {
  return String(value ?? "")
    .replace(/[-_]+/g, " ")
    .trim()
    .replace(/\b\w/g, char => char.toUpperCase());
}

function noiseLabel(value) {
  const normalized = titleCase(value || "None");
  return normalized === "High" || normalized === "Low" ? `${normalized} Noise` : "No Noise";
}

function noiseProfile(value) {
  const normalized = titleCase(value || "None");
  return PLOT_NOISE_PROFILES[normalized] ? normalized : "None";
}

function hashString(value) {
  let h = 2166136261 >>> 0;
  for (const ch of String(value)) {
    h ^= ch.charCodeAt(0);
    h = Math.imul(h, 16777619) >>> 0;
  }
  return h >>> 0;
}

function seedForRun(baseSeed, runId) {
  return (Number(baseSeed || 0) ^ hashString(String(runId || ""))) >>> 0;
}

function makeRng(seed) {
  let t = Number(seed || 0) >>> 0;
  return () => {
    t = (t + 0x6D2B79F5) >>> 0;
    let result = Math.imul(t ^ (t >>> 15), 1 | t);
    result ^= result + Math.imul(result ^ (result >>> 7), 61 | result);
    return ((result ^ (result >>> 14)) >>> 0) / 4294967296;
  };
}

function gaussianSample(rng) {
  let u = 0.0;
  let v = 0.0;
  while (u === 0.0) u = rng();
  while (v === 0.0) v = rng();
  return Math.sqrt(-2.0 * Math.log(u)) * Math.cos(2.0 * Math.PI * v);
}

function rollingRms(values, windowPoints = NOISE_RULES.rollingWindowPoints) {
  const n = values.length;
  if (!n) return [];
  let w = Math.max(1, Math.trunc(Number(windowPoints) || NOISE_RULES.rollingWindowPoints));
  if (w % 2 === 0) w += 1;
  const half = Math.floor(w / 2);
  return values.map((_, index) => {
    const start = Math.max(0, index - half);
    const end = Math.min(n, index + half + 1);
    let sum = 0.0;
    let count = 0;
    for (let i = start; i < end; i += 1) {
      const value = Number(values[i]);
      if (!Number.isFinite(value)) continue;
      sum += value * value;
      count += 1;
    }
    return count ? Math.sqrt(sum / count) : 0.0;
  });
}

function applyAdaptiveAndBaseNoise(values, { adaptive = 0.0, base = 0.0, abs = 0.0, seedKey, seed = 0 } = {}) {
  const arr = values.map(value => Number(value));
  const finite = arr.filter(Number.isFinite);
  if (!arr.length || (!adaptive && !base && !abs) || !finite.length) return arr;

  const localRms = rollingRms(arr);
  const globalRms = Math.sqrt(finite.reduce((sum, value) => sum + value * value, 0.0) / finite.length);
  const sigmaFloor = NOISE_RULES.sigmaFloorRatio * globalRms;
  const sigmaBase = base > 0.0 ? base * globalRms : 0.0;
  const rng = makeRng(hashString(`${seedKey}:${Math.trunc(Number(seed) || 0)}`));

  return arr.map((value, index) => {
    if (!Number.isFinite(value)) return value;
    const sigmaAdaptive = adaptive * Math.max(localRms[index], sigmaFloor);
    const sigma = Math.hypot(sigmaAdaptive, sigmaBase, abs);
    if (!Number.isFinite(sigma) || sigma <= 0.0) return value;
    return value + gaussianSample(rng) * sigma;
  });
}

function normalizeScope(scope = {}) {
  const rawTask = scope.task_mode ?? scope.taskMode ?? DEFAULT_SCOPE.task_mode;
  const rawNoise = scope.noise ?? DEFAULT_SCOPE.noise;
  const rawContext = scope.context ?? DEFAULT_SCOPE.context;
  const rawExamples = scope.examples ?? DEFAULT_SCOPE.examples;
  return {
    task_mode: titleCase(rawTask),
    noise: titleCase(rawNoise),
    context: typeof rawContext === "boolean" ? (rawContext ? "High" : "None") : titleCase(rawContext),
    examples: typeof rawExamples === "boolean" ? (rawExamples ? "Three Examples" : "None") : titleCase(rawExamples),
  };
}

function canonicalScope() {
  return normalizeScope(state.summary?.canonical_scope || state.leaderboard?.canonical_scope || DEFAULT_SCOPE);
}

function scopeFromSearch(fallback) {
  const params = new URLSearchParams(window.location.search);
  return normalizeScope({
    task_mode: params.get("task_mode") || fallback.task_mode,
    noise: params.get("noise") || fallback.noise,
    context: params.get("context") || fallback.context,
    examples: params.get("examples") || fallback.examples,
  });
}

function scopeSearch(scope) {
  const normalized = normalizeScope(scope);
  const params = new URLSearchParams();
  params.set("task_mode", normalized.task_mode);
  params.set("noise", normalized.noise);
  params.set("context", normalized.context);
  params.set("examples", normalized.examples);
  return params.toString();
}

function replaceScopeSearch() {
  const route = routeInfo();
  if (route.name !== "results" && route.name !== "result-detail") return;
  const next = `${normalizePath(window.location.pathname)}?${scopeSearch(state.results.scope)}`;
  window.history.replaceState({}, "", next);
}

function routeInfo() {
  const path = normalizePath(window.location.pathname);
  if (path === "/") return { name: "home", path };
  if (path === "/results/") return { name: "results", path };
  if (path.startsWith("/results/")) {
    const parts = path.split("/").filter(Boolean);
    return { name: "result-detail", path, submissionId: parts[1] };
  }
  if (path === "/environments/") return { name: "environments", path };
  if (path.startsWith("/environments/")) {
    const parts = path.split("/").filter(Boolean);
    return { name: "environment-detail", path, environmentId: parts[1] };
  }
  if (path === "/get-started/") return { name: "get-started", path };
  if (path === "/contributors/") return { name: "contributors", path };
  return { name: "not-found", path };
}

function navActive(name) {
  const current = routeInfo().name;
  if (name === "home") return current === "home";
  if (name === "results") return current === "results" || current === "result-detail";
  if (name === "environments") return current === "environments" || current === "environment-detail";
  return current === name;
}

function shell(content) {
  return `
    <header class="site-header">
      <div class="header-inner">
        <a class="wordmark" href="/" data-link>TraceBench</a>
        <nav class="nav-links" aria-label="Primary navigation">
          <a href="/" data-link class="${navActive("home") ? "active" : ""}">Home</a>
          <a href="/results/" data-link class="${navActive("results") ? "active" : ""}">Results</a>
          <a href="/environments/" data-link class="${navActive("environments") ? "active" : ""}">Environments</a>
          <a href="/get-started/" data-link class="${navActive("get-started") ? "active" : ""}">Get Started</a>
          <a href="/contributors/" data-link class="${navActive("contributors") ? "active" : ""}">Contributors</a>
          <a class="external" href="${siteHref("paper_url")}" target="_blank" rel="noreferrer">Paper (arXiv)</a>
          <a class="external" href="${siteHref("code_url")}" target="_blank" rel="noreferrer">Code</a>
          <a class="external" href="${siteHref("hf_dataset_url")}" target="_blank" rel="noreferrer">Hugging Face</a>
        </nav>
      </div>
    </header>
    <main class="main-shell">${content}</main>
  `;
}

function commit(content) {
  app.innerHTML = shell(content);
  mountPlots();
}

function navigate(path) {
  const url = new URL(path, window.location.origin);
  const normalized = normalizePath(url.pathname);
  const next = `${normalized}${url.search}`;
  if (next !== `${window.location.pathname}${window.location.search}`) {
    window.history.pushState({}, "", next);
  }
  render();
  window.scrollTo({ top: 0, behavior: "auto" });
}

function scopeEquals(a, b) {
  const left = normalizeScope(a);
  const right = normalizeScope(b);
  return left.task_mode === right.task_mode && left.noise === right.noise && left.context === right.context && left.examples === right.examples;
}

function scopeLabel(scope) {
  const normalized = normalizeScope(scope);
  return `${normalized.task_mode}, ${normalized.noise} Noise, ${normalized.context} Context, ${normalized.examples}`;
}

function leaderboardRows() {
  if (Array.isArray(state.leaderboard?.rows)) {
    return state.leaderboard.rows.map(row => ({ ...row, scope: normalizeScope(row.scope), submission_id: row.submission_id || row.id }));
  }
  if (Array.isArray(state.leaderboard?.conditions)) {
    return state.leaderboard.conditions.map(row => ({
      rank: row.rank,
      submission_id: row.submission_id || row.id,
      agent: row.agent,
      model: row.model,
      score: row.score,
      date: row.date,
      submitter: row.submitter,
      scope: normalizeScope(row.scope || row.condition),
      complete: true,
      links: row.links || {},
    }));
  }
  return [];
}

function leaderboardFilters() {
  if (state.leaderboard?.filters) return state.leaderboard.filters;
  const rows = leaderboardRows();
  const values = key => [...new Set(rows.map(row => row.scope[key]))].filter(Boolean);
  return {
    task_mode: values("task_mode").length ? values("task_mode") : ["Code", "Direct"],
    noise: values("noise").length ? values("noise") : ["Low", "High"],
    context: values("context").length ? values("context") : ["High", "None"],
    examples: values("examples").length ? values("examples") : ["None", "One Example", "Three Examples"],
  };
}

function scoreForSubmission(submissionId, scope) {
  const row = leaderboardRows().find(r => r.submission_id === submissionId && scopeEquals(r.scope, scope));
  return row ? row.score : null;
}

function formatScore(score) {
  if (score === null || score === undefined || Number.isNaN(score)) return "—";
  return Number(score).toFixed(3);
}

function selectField(prefix, key, value, options) {
  return `
    <div class="field">
      <label for="${prefix}-${key}">${key.replace("_", " ")}</label>
      <select id="${prefix}-${key}" data-action="set-scope" data-prefix="${prefix}" data-key="${key}">
        ${options.map(option => `<option value="${escapeHtml(option)}" ${option === value ? "selected" : ""}>${escapeHtml(option)}</option>`).join("")}
      </select>
    </div>
  `;
}

function scopeSelectors(prefix, scope) {
  const filters = leaderboardFilters();
  return `
    <div class="selector-grid">
      ${selectField(prefix, "task_mode", scope.task_mode, filters.task_mode)}
      ${selectField(prefix, "noise", scope.noise, filters.noise)}
      ${selectField(prefix, "context", scope.context, filters.context)}
      ${selectField(prefix, "examples", scope.examples, filters.examples)}
    </div>
  `;
}

async function getEnvironmentDescription(environmentId) {
  if (!state.envDescriptions[environmentId]) {
    state.envDescriptions[environmentId] = await loadJson(`${DATA_ROOT}/environments/${environmentId}/description.json`);
  }
  return state.envDescriptions[environmentId];
}

async function getEnvironmentData(environmentId, sampleIndex = 0) {
  const key = `${environmentId}:${sampleIndex}`;
  if (!state.envData[key]) {
    state.envData[key] = await loadJson(`${DATA_ROOT}/environments/${environmentId}/data_${sampleIndex}.json`);
  }
  return state.envData[key];
}

async function getHomepageData() {
  if (!state.homeData) {
    state.homeData = await loadJson(`${DATA_ROOT}/environments/data_main_page.json`);
  }
  return state.homeData;
}

async function getSubmissionDetail(submissionId) {
  if (!state.submissionDetails[submissionId]) {
    state.submissionDetails[submissionId] = await loadJson(`${DATA_ROOT}/submissions/${submissionId}.json`);
  }
  return state.submissionDetails[submissionId];
}

function observedSignalIds(description, data) {
  const channelDescriptions = description.observed_channels || description.observedChannels || [];
  const fromDescription = channelDescriptions.map(ch => ch.id || ch).filter(Boolean);
  const fromData = Array.isArray(data.columns) ? data.columns : Object.keys(data.rows?.[0] || {});
  return [...new Set((fromDescription.length ? fromDescription : fromData).map(String))]
    .filter(id => id !== "time");
}

function dataWithPlotNoise(data, description, controls) {
  const profileName = noiseProfile(controls?.noise);
  const profile = PLOT_NOISE_PROFILES[profileName] || PLOT_NOISE_PROFILES.None;
  const rows = Array.isArray(data.rows) ? data.rows : [];
  if (!rows.length || profileName === "None") {
    return { ...data, rows: rows.map(row => ({ ...row })), plot_noise_profile: profileName };
  }

  const noisyRows = rows.map(row => ({ ...row }));
  const runId = String(data.run_id || data.source || "website-sample");
  const derivedSeed = seedForRun(0, runId);
  for (const column of observedSignalIds(description, data)) {
    if (!rows.some(row => Number.isFinite(Number(row[column])))) continue;
    const values = rows.map(row => Number(row[column]));
    const noisyValues = applyAdaptiveAndBaseNoise(values, {
      adaptive: profile.adaptive,
      seedKey: `signal:${runId}:${column}`,
      seed: derivedSeed,
    });
    noisyValues.forEach((value, index) => {
      if (Number.isFinite(value)) noisyRows[index][column] = value;
    });
  }
  return { ...data, rows: noisyRows, plot_noise_profile: profileName };
}

function plotNoiseCue(controls) {
  return `<div class="plot-noise-cue">Plot noise: ${escapeHtml(noiseLabel(controls?.noise))}</div>`;
}

function trainingSamplesKey(examples) {
  if (examples === "None") return "none";
  if (examples === "One Example") return "one";
  return "multiple";
}

function renderPlot(data, description, options = {}) {
  const rows = data.rows || [];
  if (!rows.length) return `<div class="plot-wrap"><p class="muted">No sample rows available.</p></div>`;
  const showInterventionMarker = options.showInterventionMarker !== false;

  const channelDescriptions = description.observed_channels || (description.observedChannels || []).map(item => (
    typeof item === "string" ? { id: item, label: item, unit: "" } : item
  ));
  const channels = channelDescriptions
    .map(ch => ch.id || ch)
    .filter(id => id !== "time" && rows.some(r => typeof r[id] === "number"));
  const allowedChannelIds = Array.isArray(options.channelIds) ? new Set(options.channelIds.map(String)) : null;
  const limitedChannels = channels
    .filter(id => !allowedChannelIds || allowedChannelIds.has(String(id)))
    .slice(0, 4);
  const x = rows.map(row => Number(row.time));
  const colors = ["#1f5fbf", "#6c757d", "#d97917", "#2f7d55"];
  const traces = [];

  limitedChannels.forEach((channel, index) => {
    const channelMeta = channelDescriptions.find(ch => (ch.id || ch) === channel) || { label: channel, unit: "" };
    const values = rows.map(row => Number(row[channel]));
    const isPrimary = !options.primaryChannel || channel === options.primaryChannel;
    traces.push({
      x,
      y: values,
      mode: "lines",
      name: `${channelMeta.label}${channelMeta.unit ? ` (${channelMeta.unit})` : ""}`,
      line: { color: colors[index] || colors[0], width: 2 },
      visible: isPrimary ? true : "legendonly",
      hovertemplate: "time=%{x:.3f}s<br>%{y:.4f}<extra>%{fullData.name}</extra>",
    });
  });

  const plotId = `plotly-${++plotCounter}`;
  const disableZoom = options.disableZoom === true;
  plotPayloads.set(plotId, {
    traces,
    layout: {
      autosize: true,
      height: 430,
      margin: { l: 58, r: 24, t: 24, b: 54 },
      paper_bgcolor: "#ffffff",
      plot_bgcolor: "#ffffff",
      font: { family: "Inter, system-ui, sans-serif", color: "#111111", size: 12 },
      xaxis: { title: "time", showgrid: true, gridcolor: "#eef1f4", zeroline: false },
      yaxis: { title: options.yAxisTitle || "observed channels", showgrid: true, gridcolor: "#eef1f4", zeroline: false },
      legend: { orientation: "h", x: 0, y: 1.14 },
      showlegend: options.showLegend !== false,
      shapes: showInterventionMarker ? [{
        type: "line",
        x0: Number(data.intervention_time),
        x1: Number(data.intervention_time),
        y0: 0,
        y1: 1,
        yref: "paper",
        line: { color: "#d97917", width: 2, dash: "dash" },
      }] : [],
      annotations: showInterventionMarker ? [{
        x: Number(data.intervention_time),
        y: 1,
        yref: "paper",
        text: `intervention t=${escapeHtml(data.intervention_time)}s`,
        showarrow: false,
        xanchor: "left",
        yanchor: "bottom",
        font: { color: "#5f6368", size: 12 },
      }] : [],
    },
    config: disableZoom
      ? {
          responsive: true,
          displayModeBar: false,
          staticPlot: true,
          scrollZoom: false,
          doubleClick: false,
        }
      : {
          responsive: true,
          displayModeBar: true,
          scrollZoom: true,
          doubleClick: "reset",
          modeBarButtonsToRemove: ["lasso2d", "select2d"],
          displaylogo: false,
        },
  });

  const ariaLabel = showInterventionMarker
    ? "Time-series Plotly plot with intervention marker"
    : "Time-series Plotly plot";
  return `<div class="plot-wrap plotly-wrap"><div id="${plotId}" class="plotly-host" role="img" aria-label="${ariaLabel}"></div></div>`;
}

function mountPlots() {
  if (!plotPayloads.size) return;
  window.requestAnimationFrame(() => {
    for (const [plotId, payload] of plotPayloads.entries()) {
      const element = document.getElementById(plotId);
      if (!element) continue;
      if (!window.Plotly) {
        element.innerHTML = `<p class="muted">Plotly could not be loaded.</p>`;
        continue;
      }
      window.Plotly.react(element, payload.traces, payload.layout, payload.config);
    }
    plotPayloads.clear();
  });
}

function findPrompt(description, controls, promptField = "prompt_combinations") {
  const taskMode = controls.taskMode || "Code";
  const taskType = taskMode.toLowerCase();
  const descLevel = controls.context === "High" ? "high" : "none";
  const trainingSamples = trainingSamplesKey(controls.examples);
  const legacyTrainingSamples = trainingSamples === "none" ? "none" : ">0";
  const candidates = description[promptField] || description.prompt_combinations || [];
  return candidates.find(p => p.task_type === taskType && p.desc_level === descLevel && p.training_samples === trainingSamples)
      || candidates.find(p => p.task_type === taskType && p.desc_level === descLevel && p.training_samples === legacyTrainingSamples)
      || candidates.find(p => p.task_type === taskType && p.training_samples === trainingSamples)
      || candidates.find(p => p.task_type === taskType && p.training_samples === legacyTrainingSamples)
      || candidates.find(p => p.task_type === taskType)
      || candidates[0];
}

function optionButtons(prefix, key, activeValue, options) {
  return options.map(option => {
    return `<button class="toggle-button ${option.value === activeValue ? "active" : ""}" data-action="set-${prefix}-control" data-key="${key}" data-value="${escapeHtml(option.value)}">${escapeHtml(option.label)}</button>`;
  }).join("");
}

function taskControls(prefix, controls, sampleControls = "") {
  return `
    <div class="control-stack">
      <div class="control-row">
        <span class="control-label">Task:</span>
        ${optionButtons(prefix, "taskMode", controls.taskMode, TASK_MODE_OPTIONS)}
      </div>
      <div class="control-row">
        <span class="control-label">Noise:</span>
        ${optionButtons(prefix, "noise", controls.noise, NOISE_OPTIONS)}
      </div>
      <div class="control-row">
        <span class="control-label">Context:</span>
        ${optionButtons(prefix, "context", controls.context, CONTEXT_OPTIONS)}
      </div>
      <div class="control-row">
        <span class="control-label">Examples:</span>
        ${optionButtons(prefix, "examples", controls.examples, EXAMPLE_OPTIONS)}
      </div>
      ${sampleControls}
    </div>
  `;
}

async function renderHome() {
  const description = await getEnvironmentDescription("BallDrop");
  const data = await getHomepageData();
  const plotData = dataWithPlotNoise(data, description, state.home);
  const prompt = findPrompt(description, state.home, "homepage_prompt_combinations");

  const stats = [
    ["3 physical simulators", "BallDrop, BounceBall, MassSlide"],
    ["4 experimental conditions", "Noise, Task output format, Context, Number of training examples"],
  ];

  const content = `
    <section class="hero">
      <h1>TraceBench: A Controllable Benchmark for Agentic Root-Cause Analysis of Time Series</h1>
      <p>A benchmark for evaluating whether tool-using agents can perform evidence-grounded analysis of multivariate time series.</p>
      <p>Tasks are generated from physical simulators with known interventions. Agents must combine textual descriptions, noisy observations, and optional labeled examples to identify what changed, or that no intervention occurred.</p>
    </section>

    <section class="section first-home-section" aria-labelledby="example-title">
      <div class="section-header">
        <h2 id="example-title">Illustrative Example</h2>
      </div>
      <div class="example-stack">
        <section class="subsection ball-drop-section" aria-labelledby="data-simulation-title">
          <h3 id="data-simulation-title">Data Simulation</h3>
          <img class="ball-drop-gif" src="/assets/media/ball-drop-bounce.gif" alt="Animated bouncing ball from the BallDrop simulator" />
        </section>

        <section class="subsection" aria-labelledby="question-title">
          <h3 id="question-title">Question</h3>
          <p class="page-subtitle">A compact BallDrop task with controls for answer interface and benchmark condition axes.</p>
          <div class="panel">
            ${taskControls("home", state.home)}
            ${renderPlot(plotData, description, { channelIds: ["Position"], primaryChannel: "Position", yAxisTitle: "ball height", showInterventionMarker: false, disableZoom: true, showLegend: false })}
            <div class="prompt-panel">
              <pre>${escapeHtml(prompt.agent_instruction)}</pre>
              <button class="reveal-button" data-action="toggle-reveal">reveal answer</button>
              ${state.home.reveal ? `<div class="reveal-answer"><strong>Correct answer:</strong> ${escapeHtml(data.answer || data.intervention_parameter || "no intervention")} changed at the intervention marker.</div>` : ""}
            </div>
          </div>
        </section>
      </div>
    </section>

    <section class="section">
      <div class="stats-row">
        ${stats.map(([value, meaning]) => `<div class="stat-cell"><span class="stat-value">${value}</span><span class="stat-meaning">${meaning}</span></div>`).join("")}
      </div>
    </section>

    <section class="section" aria-labelledby="principles-title">
      <div class="section-header">
        <h2 id="principles-title">Design principles</h2>
      </div>
      <div class="feature-list">
        <div class="feature-item"><strong>Physics-grounded generation.</strong><p>Samples are produced by simulators rather than by static datasets, giving reliable intervention labels and controllable task variants.</p></div>
        <div class="feature-item"><strong>Evidence-grounded agent evaluation.</strong><p>Correct answers require inspecting the observed signals; textual context alone is not sufficient.</p></div>
        <div class="feature-item"><strong>Controlled difficulty axes.</strong><p>TraceBench varies textual context, observation noise, labeled examples, and answer interface in matched experimental conditions.</p></div>
      </div>
    </section>
  `;
  commit(content);
}

function sortRows(rows) {
  const { sortKey, sortDir } = state.results;
  const direction = sortDir === "asc" ? 1 : -1;
  return [...rows].sort((a, b) => {
    let av = a[sortKey];
    let bv = b[sortKey];
    if (sortKey === "score") { av = Number(av); bv = Number(bv); }
    if (sortKey === "rank") { av = Number(av); bv = Number(bv); }
    if (av < bv) return -1 * direction;
    if (av > bv) return 1 * direction;
    return 0;
  });
}

function renderLeaderboardTable(rows) {
  const sorted = sortRows(rows);
  const headers = [
    ["rank", "rank"],
    ["agent", "agent"],
    ["model", "model"],
    ["score", "score"],
    ["date", "date"],
    ["submitter", "submitter"],
  ];
  const sortMark = key => state.results.sortKey === key ? (state.results.sortDir === "asc" ? " ↑" : " ↓") : "";
  return `
    <div class="table-wrap">
      <table aria-label="TraceBench leaderboard">
        <thead><tr>${headers.map(([key, label]) => `<th><button data-action="sort" data-key="${key}">${label}${sortMark(key)}</button></th>`).join("")}</tr></thead>
        <tbody>
          ${sorted.map(row => {
            const comparisonScore = state.results.compare ? scoreForSubmission(row.submission_id, state.results.comparisonScope) : null;
            const scoreHtml = state.results.compare
              ? `<span>${formatScore(row.score)}</span> <span class="score-compare">→ ${formatScore(comparisonScore)}</span>`
              : formatScore(row.score);
            return `<tr class="clickable-row" data-nav="/results/${encodeURIComponent(row.submission_id)}/?${scopeSearch(state.results.scope)}">
              <td>${row.rank ?? ""}</td>
              <td><strong>${escapeHtml(row.agent)}</strong></td>
              <td>${escapeHtml(row.model)}</td>
              <td class="score-cell">${scoreHtml}</td>
              <td>${escapeHtml(row.date)}</td>
              <td>${escapeHtml(row.submitter)}</td>
            </tr>`;
          }).join("")}
        </tbody>
      </table>
    </div>
  `;
}

function renderResultsToolbar() {
  return `
    <div class="toolbar">
      <div>
        <p class="small muted">Selected condition: ${escapeHtml(scopeLabel(state.results.scope))}</p>
        ${scopeSelectors("primary", state.results.scope)}
      </div>
      <div class="compare-area">
        <button class="compare-button ${state.results.compare ? "active" : ""}" data-action="toggle-compare">Compare</button>
      </div>
      ${state.results.compare ? `<div class="compare-selectors"><p class="small muted">Comparison condition: ${escapeHtml(scopeLabel(state.results.comparisonScope))}</p>${scopeSelectors("comparison", state.results.comparisonScope)}</div>` : ""}
    </div>
  `;
}

function renderResults() {
  replaceScopeSearch();
  const rows = leaderboardRows().filter(row => row.complete !== false && scopeEquals(row.scope, state.results.scope));
  const content = `
    <section>
      <h1 class="page-title">Results</h1>
      <p class="page-subtitle">Filter the public leaderboard by task mode and controllable condition axes. The default scope is Code, Low Noise, High Context, Three Examples.</p>
      ${renderResultsToolbar()}
      <p class="small muted">Only entries with five distinct required seeds for each simulator are shown. With three simulators, each displayed public cell covers 15 runs.</p>
      ${renderLeaderboardTable(rows)}
    </section>
  `;
  commit(content);
}

function normalizeSubmissionDetail(detail, submissionId) {
  if (Array.isArray(detail.condition_results)) {
    return {
      ...detail,
      condition_results: detail.condition_results.map(item => ({ ...item, scope: normalizeScope(item.scope) })),
      per_seed_results: (detail.per_seed_results || []).map(item => ({ ...item, scope: normalizeScope(item.scope) })),
    };
  }

  const record = detail.record || leaderboardRows().find(row => row.submission_id === submissionId) || {};
  const baseScope = normalizeScope(record.scope || record.condition || state.results.scope);
  const perCondition = (detail.perCondition || []).map(item => ({
    scope: normalizeScope(item.scope || item.condition || baseScope),
    score: item.score ?? item.overall,
    complete: true,
    distinct_seed_count: Array.isArray(item.seedIds) ? item.seedIds.length : Number(String(item.seeds || "0").split("/", 1)[0]) || 0,
    seeds_by_simulator: item.condition?.simulator ? { [item.condition.simulator]: item.seedIds || [] } : {},
  }));
  const links = detail.links || {};
  return {
    submission_id: record.submission_id || record.id || submissionId,
    agent: record.agent || submissionId,
    model: record.model || "",
    date: record.date || "unknown",
    submitter: record.submitter || "",
    canonical_score: record.score ?? record.overall,
    seed_coverage: {
      required_seeds_per_simulator: (detail.coverage?.requiredSeeds || []).length || 5,
      distinct_required_seeds: perCondition[0]?.seeds_by_simulator || {},
      public_cells_complete: detail.coverage?.complete !== false,
    },
    condition_results: perCondition.length ? perCondition : [{
      scope: baseScope,
      score: record.score ?? record.overall,
      complete: true,
      distinct_seed_count: detail.coverage?.evaluationUnits || 0,
      seeds_by_simulator: {},
    }],
    per_seed_results: (detail.seedResults || []).map(item => ({
      scope: baseScope,
      simulator: record.condition?.simulator || baseScope.simulator || "",
      seed: item.seed,
      question_id: item.questionSlug || item.question_id || "",
      score: item.score,
      trajectory_url: item.trajectory || links.trajectories || "#",
      artifact_url: links.artifacts || "#",
    })),
    downloads: {
      trajectory_archive: links.trajectories || "#",
      complete_results_table: links.results || "#",
      benchmark_dataset: siteValue("hf_dataset_url", "#"),
    },
  };
}

async function renderResultDetail(submissionId) {
  let detail;
  try {
    detail = normalizeSubmissionDetail(await getSubmissionDetail(submissionId), submissionId);
  } catch (error) {
    renderNotFound(`No submission detail JSON exists for “${submissionId}”.`);
    return;
  }
  replaceScopeSearch();
  const activeCondition = detail.condition_results.find(item => scopeEquals(item.scope, state.results.scope));
  const filteredSeeds = detail.per_seed_results.filter(item => scopeEquals(item.scope, state.results.scope));
  const complete = detail.seed_coverage.public_cells_complete;
  const conditionRows = detail.condition_results
    .filter(item => item.scope.task_mode === state.results.scope.task_mode)
    .sort((a, b) => b.score - a.score)
    .slice(0, 12);
  const content = `
    <section>
      <div class="detail-header">
        <div class="detail-title">
          <h1 class="page-title">${escapeHtml(detail.agent)}</h1>
          <p class="page-subtitle">Submission detail for ${escapeHtml(detail.model)} under the active leaderboard filter.</p>
        </div>
        <div class="flat-panel">
          <div class="meta-grid">
            <div class="meta-item"><span>agent</span><strong>${escapeHtml(detail.agent)}</strong></div>
            <div class="meta-item"><span>model</span><strong>${escapeHtml(detail.model)}</strong></div>
            <div class="meta-item"><span>date</span><strong>${escapeHtml(detail.date)}</strong></div>
            <div class="meta-item"><span>submitter</span><strong>${escapeHtml(detail.submitter)}</strong></div>
            <div class="meta-item"><span>displayed score</span><strong>${formatScore(activeCondition?.score)}</strong></div>
            <div class="meta-item"><span>scope</span><strong>${escapeHtml(scopeLabel(state.results.scope))}</strong></div>
          </div>
        </div>
      </div>

      ${renderResultsToolbar()}

      <section class="section">
        <div class="section-header">
          <h2>Seed coverage</h2>
          <p>${complete ? `<span class="badge ok">complete public cell</span>` : `<span class="badge warn">incomplete</span>`} All displayed public cells contain exactly five distinct required seeds for each simulator.</p>
        </div>
        <div>${Object.entries(detail.seed_coverage.distinct_required_seeds).map(([sim, seeds]) => `<span class="badge ok">${escapeHtml(sim)}: ${seeds.length} seeds</span>`).join("")}</div>
      </section>

      <section class="section">
        <div class="section-header">
          <h2>Per-condition results</h2>
          <p class="muted">Rows below show condition-level scores for this submission within the selected task mode.</p>
        </div>
        <div class="table-wrap">
          <table>
            <thead><tr><th>task mode</th><th>noise</th><th>context</th><th>examples</th><th>score</th><th>seeds</th></tr></thead>
            <tbody>${conditionRows.map(item => `<tr>
              <td>${escapeHtml(item.scope.task_mode)}</td><td>${escapeHtml(item.scope.noise)}</td><td>${escapeHtml(item.scope.context)}</td><td>${escapeHtml(item.scope.examples)}</td><td class="score-cell">${formatScore(item.score)}</td><td>${item.distinct_seed_count}</td>
            </tr>`).join("")}</tbody>
          </table>
        </div>
      </section>

      <section class="section">
        <div class="section-header">
          <h2>Per-seed results</h2>
          <p class="muted">The active scope expands to one public run for each seed and simulator.</p>
        </div>
        <div class="table-wrap">
          <table>
            <thead><tr><th>simulator</th><th>seed</th><th>question id</th><th>score</th><th>trajectory</th><th>artifact</th></tr></thead>
            <tbody>${filteredSeeds.map(item => `<tr>
              <td>${escapeHtml(item.simulator)}</td><td>${item.seed}</td><td>${escapeHtml(item.question_id)}</td><td class="score-cell">${formatScore(item.score)}</td><td><a href="${item.trajectory_url}" target="_blank" rel="noreferrer">trajectory</a></td><td><a href="${item.artifact_url}" target="_blank" rel="noreferrer">artifact</a></td>
            </tr>`).join("")}</tbody>
          </table>
        </div>
      </section>

      <section class="section">
        <div class="section-header"><h2>Downloads</h2></div>
        <p class="muted">The trajectory archive contains raw scores, ATIF trajectories, and generated artifacts for ${Number(detail.archive_contract?.run_count || detail.per_seed_results.length)} runs.</p>
        <p><a href="${detail.downloads.trajectory_archive}" target="_blank" rel="noreferrer">Trajectory archive</a> · <a href="${detail.downloads.complete_results_table}" target="_blank" rel="noreferrer">Complete results table</a> · <a href="${detail.downloads.benchmark_dataset}" target="_blank" rel="noreferrer">Benchmark dataset</a></p>
      </section>
    </section>
  `;
  commit(content);
}

async function renderEnvironments() {
  const descriptions = await Promise.all(state.summary.simulators.map(id => getEnvironmentDescription(id)));
  const content = `
    <section>
      <h1 class="page-title">Environments</h1>
      <p class="page-subtitle">Each simulator exposes browser-ready metadata, plotted samples, prompt variants, and benchmark download links.</p>
      <div class="card-grid">
        ${descriptions.map(desc => {
          const environmentId = desc.environment_id || desc.sourceName || desc.name;
          return `<a class="env-card" href="/environments/${escapeHtml(environmentId)}/" data-link>
          <div><strong>${escapeHtml(desc.name)}</strong><p>${escapeHtml(desc.short_one_line_description)}</p></div><span>Open environment</span>
        </a>`;
        }).join("")}
      </div>
    </section>
  `;
  commit(content);
}

async function renderEnvironmentDetail(environmentId) {
  let description;
  try {
    description = await getEnvironmentDescription(environmentId);
  } catch (error) {
    renderNotFound(`No environment data exists for “${environmentId}”.`);
    return;
  }
  const sampleCount = description.sample_count || 1;
  const selectedSample = state.env.sampleIndex[environmentId] || 1;
  const data = await getEnvironmentData(environmentId, Math.min(selectedSample, sampleCount));
  const plotData = dataWithPlotNoise(data, description, state.env);
  const prompt = findPrompt(description, state.env);
  const sampleControls = `
    <div class="control-row">
      <span class="control-label">Sample:</span>
      <select data-action="set-env-sample" data-environment="${escapeHtml(environmentId)}">
        ${Array.from({ length: sampleCount }, (_, index) => {
          const sampleNumber = index + 1;
          return `<option value="${sampleNumber}" ${sampleNumber === selectedSample ? "selected" : ""}>${sampleNumber}</option>`;
        }).join("")}
      </select>
    </div>`;
  const content = `
    <section>
      <h1 class="page-title">${escapeHtml(description.name)}</h1>
      <p class="page-subtitle">${escapeHtml(description.short_one_line_description)}</p>
      <div class="panel">
        ${taskControls("env", state.env, sampleControls)}
        ${plotNoiseCue(state.env)}
        ${renderPlot(plotData, description)}
      </div>
      <div class="prompt-panel">
        <h2>Agent prompt</h2>
        <pre>${escapeHtml(prompt.agent_instruction)}</pre>
      </div>
      <section class="section">
        <div class="section-header"><h2>Simulator details</h2></div>
        <div class="meta-grid">
          <div class="meta-item"><span>observed channels</span><strong>${(description.observed_channels || description.observedChannels || []).map(ch => escapeHtml(ch.id || ch)).join(", ")}</strong></div>
          <div class="meta-item"><span>candidate parameters</span><strong>${(description.candidate_parameters || description.candidateParameters || []).map(escapeHtml).join(", ")}</strong></div>
          <div class="meta-item"><span>intervention parameter in current sample</span><strong>${escapeHtml(data.intervention_parameter)}</strong></div>
          <div class="meta-item"><span>download</span><strong><a href="${description.download_link}" target="_blank" rel="noreferrer">question bundle</a></strong></div>
        </div>
      </section>
    </section>
  `;
  commit(content);
}

function renderGetStarted() {
  const quickStart = `# Clone and install
git clone ${siteValue("code_url", "<TRACEBENCH_PUBLIC_REPOSITORY_URL>")}
cd TraceBench
python -m venv env
source env/bin/activate
pip install -e .

# Launch local explorer to explore the data locally
bash web_model_explorer/start.sh

# Run one agentic evaluation
python workflows/rollout/question_run_orchestrator.py \\
  --tasks-dir TraceBench_questions \\
  --model BallDrop \\
  --agent-id <AGENT_ID> \\
  --row-slug <ROW_SLUG>`;

  const bundle = `<submission_id>/
  seed_coverage.json
  <question_id>/
    scores.json
    atif_trajectory.json
    artifact/
      scripts/
      plots/
      logs/
      generated_files/
  <question_id>/
    scores.json
    atif_trajectory.json
    artifact/
      ...`;

  const content = `
    <section>
      <h1 class="page-title">Get Started and Submit</h1>
      <p class="page-subtitle">Run TraceBench locally, inspect the data explorer, and submit one structured agent result bundle through GitHub.</p>
      <div class="code-shell">
        <div class="code-shell-header"><span>Run locally</span><button class="copy-button" data-action="copy-code" data-copy-target="quick-start-code">copy</button></div>
        <pre id="quick-start-code" class="code-block">${escapeHtml(quickStart)}</pre>
      </div>

      <section class="section">
        <div class="section-header"><h2>Submit a new result</h2></div>
        <p>To submit a new result, open a GitHub pull request with one structured submission bundle. The bundle should include submission metadata, seed coverage, one folder for each submitted question/run, the corresponding <code>scores.json</code> and <code>atif_trajectory.json</code> files, and the artifacts produced during the run.</p>
        <div class="flow-list">
          <div class="flow-item"><strong>1. Package one submission.</strong><p>Scope the pull request to one agent/submission so validation and publication stay atomic.</p></div>
          <div class="flow-item"><strong>2. Validate and preprocess.</strong><p>The importer checks seed coverage and generates compact public leaderboard records.</p></div>
          <div class="flow-item"><strong>3. Publish canonical data.</strong><p>Accepted bundles are uploaded to Hugging Face, then website JSON is regenerated from that state.</p></div>
        </div>
        <div class="code-shell">
          <div class="code-shell-header"><span>Raw submission bundle schema</span><button class="copy-button" data-action="copy-code" data-copy-target="bundle-code">copy</button></div>
          <pre id="bundle-code" class="code-block">${escapeHtml(bundle)}</pre>
        </div>
      </section>
    </section>
  `;
  commit(content);
}

function renderContributors() {
  const contributors = Array.isArray(state.site?.contributors) ? state.site.contributors : [];
  const contact = state.site?.contact || {};
  const citation = siteValue("citation", "Citation coming soon.");
  const contributorHtml = contributors.length
    ? contributors.map(contributor => {
      const name = escapeHtml(contributor.name || "");
      const affiliation = escapeHtml(contributor.affiliation || "");
      const url = contributor.url ? escapeHtml(contributor.url) : "";
      const nameHtml = url ? `<a href="${url}" target="_blank" rel="noreferrer">${name}</a>` : name;
      return `<p><strong>${nameHtml}</strong>${affiliation ? `, ${affiliation}` : ""}</p>`;
    }).join("")
    : `<p><strong>TraceBench contributors to be announced</strong></p>`;
  const contactLinks = [
    contact.url ? [contact.label || "Contact", contact.url] : null,
    siteValue("code_url") ? ["Repository", siteValue("code_url")] : null,
    siteValue("affiliation_url") ? ["Affiliation", siteValue("affiliation_url")] : null,
  ].filter(Boolean);
  const content = `
    <section>
      <h1 class="page-title">Contributors, Citation, and Contact</h1>
      <p class="page-subtitle">Project contributors, citable metadata, and collaboration links.</p>

      <section class="section">
        <div class="section-header"><h2>Contributors</h2></div>
        ${contributorHtml}
      </section>

      <section class="section">
        <div class="section-header"><h2>Citation</h2></div>
        <div class="code-shell dark-code">
          <pre class="code-block">${escapeHtml(citation)}</pre>
        </div>
      </section>

      <section class="section">
        <div class="section-header"><h2>Contact</h2></div>
        <p>Questions, feedback, or collaboration.</p>
        <div class="contact-row">
          ${contactLinks.map(([label, url]) => `<a href="${escapeHtml(url)}" target="_blank" rel="noreferrer">${escapeHtml(label)}</a>`).join("")}
        </div>
      </section>
    </section>
  `;
  commit(content);
}

function renderNotFound(message = "The requested route is not part of this website.") {
  const content = `
    <section class="error-box">
      <h1 class="page-title">Page not found</h1>
      <p class="page-subtitle">${escapeHtml(message)}</p>
      <p><a href="/" data-link>Return home</a></p>
    </section>
  `;
  commit(content);
}

async function render() {
  try {
    const route = routeInfo();
    if (!state.site) {
      state.site = await loadJson("/site.json");
    }
    if (!state.summary || !state.leaderboard) {
      state.summary = await loadJson(`${DATA_ROOT}/summary.json`);
      state.leaderboard = await loadJson(`${DATA_ROOT}/leaderboard.json`);
      state.results.scope = scopeFromSearch(canonicalScope());
    }
    if (route.name === "results" || route.name === "result-detail") {
      state.results.scope = scopeFromSearch(state.results.scope);
    }

    if (route.name === "home") return await renderHome();
    if (route.name === "results") return renderResults();
    if (route.name === "result-detail") return await renderResultDetail(route.submissionId);
    if (route.name === "environments") return await renderEnvironments();
    if (route.name === "environment-detail") return await renderEnvironmentDetail(route.environmentId);
    if (route.name === "get-started") return renderGetStarted();
    if (route.name === "contributors") return renderContributors();
    return renderNotFound();
  } catch (error) {
    console.error(error);
    app.innerHTML = shell(`<section class="error-box"><h1 class="page-title">Load error</h1><p class="page-subtitle">${escapeHtml(error.message)}</p><p class="muted">Serve this directory from the repository root so root-relative assets and data paths resolve correctly.</p></section>`);
  }
}

if (window.__TRACEBENCH_ENABLE_TEST_API__) {
  window.__TRACEBENCH_TEST__ = {
    applyAdaptiveAndBaseNoise,
    dataWithPlotNoise,
    findPrompt,
    hashString,
    noiseProfile,
    plotPayloads,
    renderPlot,
    seedForRun,
    trainingSamplesKey,
  };
}

document.addEventListener("click", event => {
  const link = event.target.closest("a[data-link]");
  if (link) {
    const url = new URL(link.href, window.location.origin);
    if (url.origin === window.location.origin) {
      event.preventDefault();
      navigate(url.pathname);
      return;
    }
  }

  const navRow = event.target.closest("tr[data-nav]");
  if (navRow && !event.target.closest("a, button, select")) {
    navigate(navRow.dataset.nav);
    return;
  }

  const button = event.target.closest("button[data-action]");
  if (!button) return;
  const action = button.dataset.action;

  if (action === "set-home-control") {
    const key = button.dataset.key;
    if (key && key in state.home) {
      state.home[key] = button.dataset.value;
    }
    state.home.reveal = false;
    render();
  } else if (action === "toggle-reveal") {
    state.home.reveal = !state.home.reveal;
    render();
  } else if (action === "toggle-compare") {
    state.results.compare = !state.results.compare;
    render();
  } else if (action === "sort") {
    const key = button.dataset.key;
    if (state.results.sortKey === key) {
      state.results.sortDir = state.results.sortDir === "asc" ? "desc" : "asc";
    } else {
      state.results.sortKey = key;
      state.results.sortDir = key === "score" || key === "rank" ? "desc" : "asc";
    }
    render();
  } else if (action === "set-env-control") {
    const key = button.dataset.key;
    if (key && key in state.env) {
      state.env[key] = button.dataset.value;
    }
    render();
  } else if (action === "copy-code") {
    const target = document.getElementById(button.dataset.copyTarget);
    if (target) {
      navigator.clipboard?.writeText(target.textContent || "");
      button.textContent = "copied";
      setTimeout(() => { button.textContent = "copy"; }, 900);
    }
  }
});

document.addEventListener("change", event => {
  const target = event.target;
  if (!(target instanceof HTMLSelectElement)) return;

  if (target.dataset.action === "set-scope") {
    const prefix = target.dataset.prefix;
    const key = target.dataset.key;
    if (prefix === "primary") {
      state.results.scope[key] = target.value;
      replaceScopeSearch();
    } else if (prefix === "comparison") {
      state.results.comparisonScope[key] = target.value;
    }
    render();
  } else if (target.dataset.action === "set-env-sample") {
    state.env.sampleIndex[target.dataset.environment] = Number(target.value);
    render();
  }
});

window.addEventListener("popstate", render);
if (!window.__TRACEBENCH_DISABLE_AUTORUN__) {
  render();
}
