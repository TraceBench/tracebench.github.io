import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const source = fs.readFileSync("assets/app.js", "utf8");
assert.ok(!source.includes("click signals to inspect traces"), "homepage should not render the signal-inspection helper text");
assert.ok(!source.includes("plotNoiseCue(state.home)"), "homepage should not render a separate plot-noise cue");
assert.ok(source.includes("plotNoiseCue(state.env)"), "environment pages should keep the plot-noise cue");
assert.ok(!source.includes("<h2 id=\"stats-title\">Statistic row</h2>"), "homepage should not render a visible Statistic row heading");

const context = {
  console,
  navigator: { clipboard: { writeText() {} } },
  setTimeout,
  URL,
  URLSearchParams,
  HTMLSelectElement: class HTMLSelectElement {},
};
context.window = {
  __TRACEBENCH_DISABLE_AUTORUN__: true,
  __TRACEBENCH_ENABLE_TEST_API__: true,
  addEventListener() {},
  location: { pathname: "/results/", search: "" },
  requestAnimationFrame(callback) {
    callback();
  },
};
context.document = {
  addEventListener() {},
  getElementById() {
    return { innerHTML: "" };
  },
};
context.globalThis = context;

vm.runInNewContext(source, context, { filename: "assets/app.js" });

const api = context.window.__TRACEBENCH_TEST__;
assert.ok(api, "app.js should expose test helpers");

api.state.site = {
  paper_url: "https://arxiv.org",
  code_url: "https://github.com/TommasoBendinelli/TraceBench",
  hf_dataset_url: "https://huggingface.co/datasets/eth-siplab/tracebench",
};
const shellMarkup = api.shell("<p>content</p>");
assert.ok(!shellMarkup.includes("Paper (arXiv)"), "paper link should omit the arXiv qualifier");
assert.ok(
  shellMarkup.includes('href="https://arxiv.org" target="_blank" rel="noreferrer">Paper</a>'),
  "paper link should retain its destination and external-link behavior"
);
assert.ok(shellMarkup.includes('data-brand="github"'), "code link should display the GitHub mark");
assert.ok(shellMarkup.includes('data-brand="hugging-face"'), "dataset link should display the Hugging Face mark");
assert.ok(
  shellMarkup.includes('data-brand="github" viewBox="0 0 16 16" aria-hidden="true" focusable="false"'),
  "GitHub mark should be decorative"
);
assert.ok(
  shellMarkup.includes('data-brand="hugging-face" viewBox="0 0 95 88" aria-hidden="true" focusable="false"'),
  "Hugging Face mark should be decorative"
);
assert.ok(shellMarkup.includes("<span>Code</span>"), "code link should retain its accessible text label");
assert.ok(shellMarkup.includes("<span>Hugging Face</span>"), "dataset link should use the official two-word label");
assert.ok(
  shellMarkup.indexOf(">Paper</a>") < shellMarkup.indexOf("<span>Code</span>")
    && shellMarkup.indexOf("<span>Code</span>") < shellMarkup.indexOf("<span>Hugging Face</span>"),
  "external links should retain Paper, Code, Hugging Face order"
);

const environmentArtwork = [
  ["BallDrop", "ball-drop", "BallDrop dynamics schematic from the TraceBench paper."],
  ["BounceBall", "bounce-ball", "BounceBall dynamics schematic from the TraceBench paper."],
  ["MassSlide", "mass-slide", "MassSlide dynamics schematic from the TraceBench paper."],
];
for (const [environmentId, assetName, alt] of environmentArtwork) {
  const markup = api.environmentCardMarkup({
    environment_id: environmentId,
    name: environmentId,
    short_one_line_description: `${environmentId} description`,
  });
  assert.ok(
    markup.includes(`src="/assets/environments/${assetName}.svg"`),
    `${environmentId} card should render its paper schematic`
  );
  assert.ok(markup.includes(`alt="${alt}"`), `${environmentId} schematic should have descriptive alt text`);
}

const unknownEnvironmentMarkup = api.environmentCardMarkup({
  environment_id: "FutureEnvironment",
  name: "FutureEnvironment",
  short_one_line_description: "Future description",
});
assert.ok(!unknownEnvironmentMarkup.includes("env-card-artwork"), "unknown environments should render without broken artwork");

const description = {
  observed_channels: [
    { id: "x", label: "x" },
    { id: "v", label: "v" },
  ],
  prompt_combinations: [
    { task_type: "code", desc_level: "high", training_samples: "multiple", agent_instruction: "code-high-multiple" },
    { task_type: "code", desc_level: "high", training_samples: "one", agent_instruction: "code-high-one" },
    { task_type: "code", desc_level: "high", training_samples: "none", agent_instruction: "code-high-none" },
  ],
  homepage_prompt_combinations: [
    { task_type: "code", desc_level: "high", training_samples: "multiple", agent_instruction: "homepage-code-high-multiple" },
    { task_type: "code", desc_level: "high", training_samples: "one", agent_instruction: "homepage-code-high-one" },
    { task_type: "code", desc_level: "high", training_samples: "none", agent_instruction: "homepage-code-high-none" },
  ],
};
const data = {
  run_id: "sample-run",
  rows: Array.from({ length: 80 }, (_, index) => ({
    time: index * 0.1,
    x: Math.sin(index / 8),
    v: Math.cos(index / 8),
  })),
};

function rmsDelta(leftRows, rightRows, column) {
  const sum = leftRows.reduce((acc, row, index) => {
    const delta = Number(row[column]) - Number(rightRows[index][column]);
    return acc + delta * delta;
  }, 0);
  return Math.sqrt(sum / leftRows.length);
}

const clean = api.dataWithPlotNoise(data, description, { noise: "None" });
assert.equal(JSON.stringify(clean.rows), JSON.stringify(data.rows), "No Noise should preserve plotted values");
assert.notEqual(clean.rows[0], data.rows[0], "No Noise should still return copied row objects");

const low = api.dataWithPlotNoise(data, description, { noise: "Low" });
const lowRepeat = api.dataWithPlotNoise(data, description, { noise: "Low" });
const high = api.dataWithPlotNoise(data, description, { noise: "High" });

assert.deepEqual(low.rows, lowRepeat.rows, "noise should be deterministic for the same run and profile");
assert.deepEqual(low.rows.map(row => row.time), data.rows.map(row => row.time), "noise must not alter time");
assert.ok(rmsDelta(low.rows, data.rows, "x") > 0, "Low Noise should alter signal values");
assert.ok(
  rmsDelta(high.rows, data.rows, "x") > rmsDelta(low.rows, data.rows, "x"),
  "High Noise should perturb the plot more than Low Noise"
);

const promptLowNoise = api.findPrompt(description, {
  taskMode: "Code",
  noise: "Low",
  context: "High",
  examples: "Three Examples",
});
const promptHighNoise = api.findPrompt(description, {
  taskMode: "Code",
  noise: "High",
  context: "High",
  examples: "Three Examples",
});
assert.equal(
  promptLowNoise.agent_instruction,
  promptHighNoise.agent_instruction,
  "noise-only changes must not change prompt selection"
);
assert.equal(promptLowNoise.agent_instruction, "code-high-multiple", "Three Examples should select multiple-example prompt");

const homepagePrompt = api.findPrompt(description, {
  taskMode: "Code",
  noise: "Low",
  context: "High",
  examples: "Three Examples",
}, "homepage_prompt_combinations");
assert.equal(
  homepagePrompt.agent_instruction,
  "homepage-code-high-multiple",
  "homepage prompt selection should use compact homepage prompt combinations"
);

const promptOneExample = api.findPrompt(description, {
  taskMode: "Code",
  noise: "Low",
  context: "High",
  examples: "One Example",
});
assert.equal(promptOneExample.agent_instruction, "code-high-one", "One Example should select one-example prompt");

api.plotPayloads.clear();
api.renderPlot(data, description, { disableZoom: true, showInterventionMarker: false });
const homepagePayload = [...api.plotPayloads.values()].at(-1);
assert.equal(homepagePayload.config.staticPlot, true, "homepage plot should disable interactions");
assert.equal(homepagePayload.config.displayModeBar, false, "homepage plot should hide modebar");
assert.equal(homepagePayload.config.scrollZoom, false, "homepage plot should disable scroll zoom");

api.plotPayloads.clear();
api.renderPlot(data, description, { channelIds: ["x"], primaryChannel: "x", showLegend: false });
const singleChannelPayload = [...api.plotPayloads.values()].at(-1);
assert.equal(singleChannelPayload.traces.length, 1, "channelIds should restrict the rendered traces");
assert.equal(singleChannelPayload.traces[0].name, "x", "channelIds should keep the requested trace");
assert.equal(singleChannelPayload.layout.showlegend, false, "showLegend false should hide the Plotly legend");

api.plotPayloads.clear();
api.renderPlot(data, description);
const environmentPayload = [...api.plotPayloads.values()].at(-1);
assert.equal(environmentPayload.config.displayModeBar, true, "environment plot should expose Plotly modebar");
assert.equal(environmentPayload.config.scrollZoom, true, "environment plot should allow scroll zoom");
assert.notEqual(environmentPayload.config.staticPlot, true, "environment plot should remain interactive");

const interactiveHandlers = new Map();
const relayoutCalls = [];
const interactiveElement = {
  on(eventName, handler) {
    interactiveHandlers.set(eventName, handler);
  },
};
context.document.getElementById = () => interactiveElement;
context.window.Plotly = {
  react() {},
  relayout(element, update) {
    relayoutCalls.push({ element, update });
  },
};
api.mountPlots();

const handleRestyle = interactiveHandlers.get("plotly_restyle");
assert.equal(typeof handleRestyle, "function", "environment plots should react to trace visibility changes");
handleRestyle([{ visible: "legendonly" }, [1]]);
assert.equal(relayoutCalls.length, 1, "single-trace visibility changes should trigger y-axis autorange");
assert.equal(relayoutCalls[0].element, interactiveElement, "autorange should update the mounted plot");
assert.equal(relayoutCalls[0].update["yaxis.autorange"], true, "autorange should be enabled for the y-axis");

handleRestyle([{ "line.width": 3 }, [0]]);
assert.equal(relayoutCalls.length, 1, "unrelated restyles should preserve the current y-axis range");

handleRestyle([{ visible: [true, "legendonly"] }, [0, 1]]);
assert.equal(relayoutCalls.length, 2, "multi-trace visibility changes should trigger y-axis autorange");

api.plotPayloads.clear();
api.renderPlot(data, description, { disableZoom: true, showInterventionMarker: false });
let staticHandlerCount = 0;
context.document.getElementById = () => ({
  on() {
    staticHandlerCount += 1;
  },
});
api.mountPlots();
assert.equal(staticHandlerCount, 0, "static homepage plots should not install visibility handlers");

console.log("noise behavior ok");
