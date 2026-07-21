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

console.log("noise behavior ok");
