// Dependency-free rendering checks: node tests/test_frontend.js
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function element(tag) {
  let text = "";
  return {
    tag,
    children: [],
    value: "",
    hidden: false,
    checked: false,
    disabled: true,
    style: {},
    classList: new Set(),
    attributes: {},
    get textContent() { return text + this.children.map((child) => child.textContent).join(" "); },
    set textContent(value) { text = String(value); this.children = []; },
    appendChild(child) {
      this.children.push(child);
      if (this.tag === "select" && this.children.length === 1) this.value = child.value;
    },
    append(...children) { children.forEach((child) => this.appendChild(child)); },
    replaceChildren() { text = ""; this.children = []; },
    setAttribute(name, value) { this.attributes[name] = String(value); },
  };
}

async function main() {
  const now = Date.now();
  const iso = (minutes) => new Date(now + minutes * 60000).toISOString();
  const prediction = (status, count, extra = {}) => ({
    time: iso(60), predicted_count: count, predicted_percent: count * 2,
    facility_status: status, scheduled_status: status,
    status_source: "published_schedule", scheduled_open_minutes: 60, ...extra,
  });
  const live = {
    facility_status: "closed", status_source: "goboard_live",
    live_is_fresh: true, live_status: "closed",
    scheduled_status: "open", scheduled_status_now: "open",
    schedule_source: "published_schedule", evaluated_at: iso(0),
    live_observed_at: iso(-1), live_valid_until: iso(14), schedule_conflict: true,
  };
  const data = {
    generated_at: iso(0), forecast_origin: { Marino: iso(-60) },
    current_availability: { Marino: live },
    locations: {
      Marino: [
        prediction("closed", 0),
        prediction("partial", 1, { scheduled_open_minutes: 30 }),
        prediction("unknown", 2),
        prediction("open", 10),
        prediction("open", 10, { status_source: "schedule_override" }),
        prediction("closed", 3, live),
        prediction("open", 4, { scheduled_status: "partial" }),
        prediction(undefined, 5, { time: iso(1500) }),
      ],
      Other: [prediction("closed", 70), prediction("unknown", 12)],
    },
  };
  const original = JSON.stringify(data);
  const ids = Object.fromEntries([
    "status", "location-select", "forecast-rows", "forecast-heading", "forecast-origin",
    "generated-at", "quiet-hours-summary", "current-availability",
    "show-closed-hours", "forecast-empty", "next-count", "next-percent",
    "next-time", "next-availability", "forecast-chart", "forecast-freshness",
    "history-chart", "history-summary", "history-location",
  ].map((id) => [id, element(id === "location-select" ? "select" : "div")]));
  let interval;
  const requests = [];
  const context = vm.createContext({
    console: { log() {}, error() {} }, Date,
    document: { getElementById: (id) => ids[id], createElement: element,
      createElementNS: (_namespace, tag) => element(tag) },
    fetch: async (url, options) => {
      requests.push({ url, method: options?.method ?? "GET" });
      if (url.includes("/api/evaluation-history")) return { ok: false, status: 404 };
      return { ok: true, json: async () => data };
    },
    setInterval: (callback) => { interval = callback; },
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, "../web/app.js"), "utf8"), context);
  await new Promise(setImmediate);
  assert.equal(ids["location-select"].children.length, 2);
  assert.equal(ids["location-select"].disabled, false);
  const forecastRows = () => ids["forecast-rows"].children
    .filter((row) => !row.classList.has("day-divider"));
  assert.equal(forecastRows().length, 6, "closed rows hidden by default; partial/unknown retained");
  assert(forecastRows().every((row) => !row.classList.has("availability-closed")));
  assert.equal(ids["forecast-rows"].children.filter((row) => row.classList.has("day-divider")).length, 2);
  assert.equal(ids["next-count"].textContent, "10", "summary skips closed, partial, and unknown hours");
  assert.equal(ids["next-percent"].textContent, "20%");
  assert.equal(ids["next-availability"].textContent, "Scheduled open");
  const chart = ids["forecast-chart"].children[0];
  assert.equal(chart.attributes.role, "img");
  assert.equal(chart.children.filter((node) => node.tag === "circle").length, 8);
  assert.equal(chart.children.filter((node) => node.attributes.class === "chart-closed-band").length, 2);
  const summaryBeforeToggle = ids["quiet-hours-summary"].textContent;
  assert.equal((summaryBeforeToggle.match(/10 people/g) || []).length, 2);
  assert.doesNotMatch(summaryBeforeToggle, /(?:^|[^0-9])(?:0|1|2|3|4|5) people/);
  ids["show-closed-hours"].checked = true;
  ids["show-closed-hours"].onchange();
  assert.equal(ids["quiet-hours-summary"].textContent, summaryBeforeToggle);
  const rows = forecastRows();
  assert.equal(rows.length, data.locations.Marino.length, "all forecast hours remain visible");
  rows.forEach((row, index) => {
    const input = data.locations.Marino[index];
    assert.equal(row.children[1].textContent, String(input.predicted_count));
    assert.equal(row.children[2].children[0].textContent, `${input.predicted_percent}%`);
    assert.equal(row.classList.has("quiet-hour"), index === 3 || index === 4);
  });
  assert(rows[0].classList.has("availability-closed"));
  assert.match(rows[0].children[3].textContent, /closed/);
  assert.match(rows[1].children[3].textContent, /Partially open \(30 of 60 min\)/);
  assert(rows[1].classList.has("availability-partial"));
  assert.equal(rows[2].children[3].textContent, "Hours unknown");
  assert.match(rows[4].children[3].textContent, /special hours/);
  assert.equal(rows[7].children[3].textContent, "Hours unknown");
  assert.match(ids["current-availability"].textContent, /differs from the published schedule/);

  context.renderLocation(data, "Marino", now + 15 * 60000);
  assert.equal(forecastRows()[5].children[3].textContent, "Scheduled open");
  assert(!forecastRows()[5].classList.has("quiet-hour"), "live-closed row is not fully open");
  assert.match(ids["current-availability"].textContent, /Closed now · Live GoBoard update/);
  // Current status obeys the backend flag, independent of forecast-row expiry and status.
  context.renderCurrentStatus({ ...live, live_status: "open", facility_status: "closed",
    live_valid_until: undefined, schedule_conflict: false });
  assert.match(ids["current-availability"].textContent, /^Open now · Live GoBoard update at/);
  assert.doesNotMatch(ids["current-availability"].textContent, /No fresh|differs/);
  context.renderCurrentStatus({ ...live, live_is_fresh: false });
  assert.match(ids["current-availability"].textContent, /Status at .*scheduled open/);
  assert.match(ids["current-availability"].textContent, /No fresh live confirmation/);
  context.renderCurrentStatus({ ...live, live_is_fresh: undefined });
  assert.equal(ids["current-availability"].textContent, "Current status unavailable");

  ids["location-select"].value = "Other";
  ids["location-select"].onchange();
  assert.equal(forecastRows().length, 2);
  assert(forecastRows().every((row) => !row.classList.has("quiet-hour")));
  assert.match(ids["quiet-hours-summary"].textContent, /No fully scheduled-open/);
  assert.equal(ids["current-availability"].hidden, true);
  interval();
  assert.equal(forecastRows().length, 2, "timer respects selected location");
  ids["show-closed-hours"].checked = false;
  ids["show-closed-hours"].onchange();
  assert.equal(forecastRows().length, 1, "toggle works for the selected location");
  interval();
  assert.equal(forecastRows().length, 1, "timer preserves the toggle setting");
  const closedOnly = { locations: { Closed: [prediction("closed", 0)] } };
  context.renderLocation(closedOnly, "Closed", now);
  assert.equal(forecastRows().length, 0);
  assert.equal(ids["forecast-empty"].hidden, false);
  assert.match(ids["forecast-empty"].textContent, /Show closed hours/);
  ids["show-closed-hours"].checked = true;
  context.renderLocation(closedOnly, "Closed", now);
  assert.equal(forecastRows().length, 1);
  assert.equal(ids["forecast-empty"].hidden, true);
  context.renderLocation({ locations: { Ranked: [
    prediction("open", 40), prediction("open", 20), prediction("open", 30),
    prediction("open", 10), prediction("partial", 1),
  ] } }, "Ranked", now);
  const ranked = ids["quiet-hours-summary"].textContent;
  assert.equal((ranked.match(/ people/g) || []).length, 3);
  assert(ranked.indexOf("10 people") < ranked.indexOf("20 people"));
  assert(ranked.indexOf("20 people") < ranked.indexOf("30 people"));
  assert(!ranked.includes("40 people"));
  context.renderLocation({ locations: { Empty: [] } }, "Empty", now);
  assert.equal(ids["next-count"].textContent, "—");
  assert.match(ids["forecast-chart"].textContent, /No occupancy forecast/);
  context.renderForecastChart([prediction("open", 0)], now);
  assert.equal(ids["forecast-chart"].children[0].children.filter((n) => n.tag === "circle").length, 1);
  assert(!JSON.stringify(ids["forecast-chart"].children[0]).includes("NaN"));
  const unordered = [
    prediction("open", 40, { time: iso(240) }),
    prediction("closed", 0, { time: iso(30) }),
    prediction("partial", 1, { time: iso(60) }),
    prediction("unknown", 2, { time: iso(90) }),
    prediction("open", 12, { time: iso(120) }),
  ];
  const unorderedBefore = JSON.stringify(unordered);
  context.renderForecastOverview(unordered, now);
  assert.equal(ids["next-count"].textContent, "12");
  assert.equal(ids["next-percent"].textContent, "24%");
  assert.equal(JSON.stringify(unordered), unorderedBefore);
  context.renderForecastOverview(unordered.filter((p) => p.facility_status !== "open"), now);
  assert.equal(ids["next-count"].textContent, "—");
  assert.equal(ids["next-percent"].textContent, "—");
  assert.equal(ids["next-time"].textContent, "No fully open upcoming hours in this forecast");
  // Fixed clock and offset-bearing timestamps verify comparisons are chronological.
  const fixedNow = Date.parse("2026-10-05T04:00:00Z");
  const timeline = [
    prediction("open", 1, { time: "2026-10-04T23:00:00-04:00" }),
    prediction("open", 2, { time: "2026-10-05T00:00:00-04:00" }),
    prediction("partial", 3, { time: "2026-10-05T00:15:00-04:00" }),
    prediction("unknown", 4, { time: "2026-10-05T00:30:00-04:00" }),
    prediction("closed", 0, { time: "2026-10-05T00:45:00-04:00" }),
    prediction("open", 20, { time: "2026-10-05T01:00:00-04:00" }),
    prediction("open", 10, { time: "2026-10-05T06:00:00Z" }),
  ];
  const timedData = { generated_at: "2026-10-05T03:00:00Z", locations: { Timed: timeline } };
  const unchangedTimeline = JSON.stringify(timedData);
  context.renderLocation(timedData, "Timed", fixedNow);
  assert.equal(ids["next-count"].textContent, "20", "skip past and exactly-now rows");
  assert.match(ids["quiet-hours-summary"].textContent, /10 people.*20 people/);
  assert.equal(forecastRows().length, timeline.length, "complete timeline stays inspectable");
  assert(forecastRows().slice(0, 5).every((row) => !row.classList.has("quiet-hour")));
  assert.match(ids["forecast-freshness"].textContent, /Generated 1 hr ago/);
  const timedChart = ids["forecast-chart"].children[0];
  assert.equal(timedChart.children.filter((n) =>
    n.tag === "circle" && n.attributes.class.includes("chart-elapsed")).length, 2);
  assert.equal(timedChart.children.filter((n) => n.attributes.class === "chart-now").length, 1);
  assert.match(timedChart.textContent, /Elapsed.*Upcoming/);
  context.renderLocation(timedData, "Timed", Date.parse("2026-10-05T06:00:00Z"));
  assert.equal(ids["next-count"].textContent, "—");
  assert.equal(ids["next-percent"].textContent, "—");
  for (const id of ["next-time", "quiet-hours-summary", "forecast-freshness"]) {
    assert.match(ids[id].textContent, /Forecast is out of date/);
  }
  assert(forecastRows().every((row) => !row.classList.has("quiet-hour")));
  assert.equal(forecastRows().length, timeline.length);
  assert.equal(JSON.stringify(timedData), unchangedTimeline);
  assert.equal(context.isUpcoming({ time: "invalid" }, fixedNow), false);
  assert.equal(context.isUpcoming({ time: "2026-10-05T04:00:00.001Z" }, fixedNow), true);
  // Switching back clears the expiry message.
  context.renderLocation(timedData, "Timed", fixedNow);
  assert.doesNotMatch(ids["forecast-freshness"].textContent, /out of date/);
  assert.match(ids["history-summary"].textContent, /not available yet/);
  context.historyFixture = {
    evaluation_type: "walk_forward", config: { open_only: true },
    rows: [
      { location_name: "Timed", horizon_hours: 1, target_time: iso(-120),
        actual_count: 10, predicted_count: 1000 },
      { location_name: "Timed", horizon_hours: 6, target_time: iso(-120),
        actual_count: 10, predicted_count: 12, fold_cutoff: "fold1" },
      { location_name: "Timed", horizon_hours: 6, target_time: iso(-60),
        actual_count: 20, predicted_count: 16, fold_cutoff: "fold1" },
      { location_name: "Other", horizon_hours: 6, target_time: iso(-60),
        actual_count: 50, predicted_count: 500 },
    ],
  };
  vm.runInContext("evaluationHistory = historyFixture", context);
  context.renderEvaluationHistory("Timed");
  assert.match(ids["history-summary"].textContent, /MAE 3.00 occupants · 2 scored/);
  assert.match(ids["history-summary"].textContent, /Historical open-hours/);
  const historyChart = ids["history-chart"].children[0];
  assert.equal(historyChart.children.filter((n) => n.tag === "circle").length, 4);
  assert.equal(historyChart.children.filter((n) => n.tag === "path").length, 2);
  assert(historyChart.children.filter((n) => n.tag === "path").every((n) =>
    n.attributes.d.includes("L")), "consecutive scored hours connect");
  context.historyFixture.rows[2].fold_cutoff = "fold2";
  context.renderEvaluationHistory("Timed");
  assert(ids["history-chart"].children[0].children.filter((n) => n.tag === "path")
    .every((n) => !n.attributes.d.includes("L")), "fold boundaries stay separate");
  context.renderEvaluationHistory("Missing");
  assert.match(ids["history-summary"].textContent, /No scored 6-hour-ahead/);
  assert.equal(ids["history-chart"].children.length, 0);
  assert.deepEqual(requests, [
    { url: "http://localhost:5000/api/predictions", method: "GET" },
    { url: "http://localhost:5000/api/evaluation-history?horizon=6", method: "GET" },
  ],
    "page load reads predictions; renders, toggles, and timers never POST refresh");
  assert.equal(JSON.stringify(data), original, "rendering never mutates predictions");
  console.log("PASS: availability rendering, unchanged values, highlighting, live expiry, and selector");
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
