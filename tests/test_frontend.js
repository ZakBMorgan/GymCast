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
    querySelector(tag) { return this.children.find((child) => child.tag === tag) ?? null; },
    focus() { element.active = this; },
    contains(target) { return this === target || this.children.some((child) => child.contains(target)); },
    setAttribute(name, value) { this.attributes[name] = String(value); },
  };
}

async function main(hostname, expectedBase) {
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
    "location-facility", "location-label", "location-picker", "location-trigger", "location-menu", "location-selected",
    "next-capacity-track", "next-capacity-fill", "next-percent-note", "forecast-scroll-hint",
    "recommended-time", "recommended-values", "recommended-explanation", "recommended-count",
    "recommended-percent", "recommended-capacity-track", "recommended-capacity-fill",
    "recommended-percent-note", "next-values", "next-recommendation-note",
  ].map((id) => [id, element(id === "location-select" ? "select" : "div")]));
  let interval;
  const listeners = {};
  const requests = [];
  const context = vm.createContext({
    window: { location: { hostname }, innerWidth: 1440,
      addEventListener: (name, handler) => { listeners[`window:${name}`] = handler; } },
    console: { log() {}, error() {} }, Date,
    document: { addEventListener: (name, handler) => { listeners[name] = handler; }, getElementById: (id) => ids[id], createElement: element,
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
  assert.equal(ids["location-select"].hidden, true);
  assert.equal(ids["location-picker"].hidden, false);
  const options = () => ids["location-menu"].children.flatMap((group) =>
    group.children.filter((child) => child.attributes.role === "option"));
  assert.equal(options().length, 2);
  const named = context.locationHeading("Marino Center — Second Floor Cardio");
  assert.equal(named.facility, "Marino Center");
  assert.equal(named.name, "Second Floor Cardio");
  assert.equal(context.locationHeading("SquashBusters - 4th Floor").name, "4th Floor");
  assert.equal(context.locationHeading("Marino Center Pool").name, "Marino Center Pool");
  assert.equal(context.locationHeading("Other — Cardio").name, "Other — Cardio");
  const key = (key) => ({ key, preventDefault() {} });
  ids["location-trigger"].onclick();
  assert.equal(ids["location-menu"].hidden, false);
  assert.equal(element.active, options()[0]);
  options()[0].onkeydown(key("ArrowDown"));
  assert.equal(element.active, options()[1]);
  options()[1].onkeydown(key("Home"));
  assert.equal(element.active, options()[0]);
  options()[0].onkeydown(key("End"));
  assert.equal(element.active, options()[1]);
  options()[1].onkeydown(key(" "));
  assert.equal(ids["location-select"].value, "Other");
  assert.equal(ids["location-selected"].textContent, "Other");
  assert.equal(options()[1].attributes["aria-selected"], "true");
  assert.equal(ids["forecast-heading"].textContent, "Other");
  assert.equal(ids["history-location"].textContent, "Other");
  assert.equal(ids["current-availability"].hidden, true);
  assert.equal(ids["forecast-chart"].children[0].children.filter((n) => n.tag === "circle").length, 2);
  ids["location-trigger"].onkeydown(key("ArrowUp"));
  options()[1].onkeydown(key("ArrowUp"));
  options()[0].onkeydown(key("Enter"));
  assert.equal(ids["location-select"].value, "Marino");
  ids["location-trigger"].onclick();
  options()[0].onkeydown(key("Escape"));
  assert.equal(ids["location-menu"].hidden, true);
  assert.equal(element.active, ids["location-trigger"]);
  ids["location-trigger"].onclick();
  listeners.pointerdown({ target: ids.status });
  assert.equal(ids["location-menu"].hidden, true);
  ids["location-trigger"].onclick();
  options()[0].onkeydown(key("Tab"));
  assert.equal(ids["location-menu"].hidden, true);
  assert.equal(ids["next-capacity-fill"].style.width, "20%");

  const forecastRows = () => ids["forecast-rows"].children
    .filter((row) => !row.classList.has("day-divider"));
  assert.equal(forecastRows().length, 6, "closed rows hidden by default; partial/unknown retained");
  assert(forecastRows().every((row) => !row.classList.has("availability-closed")));
  assert.equal(ids["forecast-rows"].children.filter((row) => row.classList.has("day-divider")).length, 2);
  assert.equal(ids["next-count"].textContent, "10", "summary skips closed, partial, and unknown hours");
  assert.equal(ids["next-percent"].textContent, "20%");
  assert.equal(ids["next-availability"].textContent, "Scheduled open");
  assert.equal(ids["forecast-scroll-hint"].hidden, true, "desktop has no swipe hint");
  ids["forecast-chart"].clientWidth = 320;
  ids["forecast-chart"].scrollWidth = 760;
  context.window.innerWidth = 390;
  listeners["window:resize"]();
  assert.equal(ids["forecast-scroll-hint"].hidden, false, "mobile chart overflow shows hint");
  ids["forecast-chart"].scrollWidth = 320;
  listeners["window:resize"]();
  assert.equal(ids["forecast-scroll-hint"].hidden, true, "no hint without actual overflow");
  ids["forecast-chart"].scrollWidth = 760;
  context.window.innerWidth = 1440;
  listeners["window:resize"]();
  assert.equal(ids["forecast-scroll-hint"].hidden, true, "wide layout hides hint even with overflow");
  const chart = ids["forecast-chart"].children[0];
  assert.equal(chart.attributes.role, "img");
  assert.equal(chart.children.filter((node) => node.tag === "circle").length, 8);
  assert.equal(chart.children.filter((node) => node.attributes.class === "chart-closed-band").length, 2);
  const summaryBeforeToggle = ids["quiet-hours-summary"].textContent;
  assert.equal((summaryBeforeToggle.match(/10 people/g) || []).length, 1);
  assert.equal(ids["recommended-count"].textContent, "10");
  assert.equal(ids["recommended-percent"].textContent, "20%");
  assert.equal(ids["recommended-values"].hidden, false);
  assert.equal(ids["next-values"].hidden, true, "same-hour metrics are not repeated");
  assert.match(ids["next-recommendation-note"].textContent, /Also the quietest/);
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
  assert.match(ids["quiet-hours-summary"].textContent, /No other quiet hours/);
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
  assert.equal((ranked.match(/ people/g) || []).length, 2);
  assert.equal(ids["recommended-count"].textContent, "10");
  assert(!ranked.includes("10 people"), "primary recommendation is not repeated among alternatives");
  assert(ranked.indexOf("20 people") < ranked.indexOf("30 people"));
  assert(!ranked.includes("40 people"));
  context.renderLocation({ locations: { Empty: [] } }, "Empty", now);
  assert.equal(ids["next-count"].textContent, "—");
  assert.equal(ids["recommended-values"].hidden, true);
  assert.equal(ids["next-values"].hidden, true);
  assert.match(ids["forecast-chart"].textContent, /No occupancy forecast/);
  context.window.innerWidth = 390;
  context.updateForecastScrollHint();
  assert.equal(ids["forecast-scroll-hint"].hidden, true, "empty chart has no swipe hint");
  context.renderForecastChart([prediction("open", 0)], now);
  assert.equal(ids["forecast-chart"].children[0].children.filter((n) => n.tag === "circle").length, 1);
  assert(!JSON.stringify(ids["forecast-chart"].children[0]).includes("NaN"));
  context.renderLocation({ locations: { Ties: [
    prediction("open", 5, { time: iso(180) }),
    prediction("open", 5, { time: iso(120) }),
    prediction("open", 9, { time: iso(60) }),
  ] } }, "Ties", now);
  assert.equal(ids["recommended-time"].textContent, context.formatTimestamp(iso(120)),
    "equal-count recommendations retain the earliest-time tie break");
  assert.equal(ids["next-time"].textContent, context.formatTimestamp(iso(60)),
    "next-open selection stays chronological rather than using the recommendation");
  const sparse = { locations: { Sparse: [
    prediction("open", 8, { predicted_percent: null }),
    prediction("open", 12, { time: iso(120) }),
  ] } };
  context.renderLocation(sparse, "Sparse", now);
  assert.equal(ids["recommended-count"].textContent, "8");
  assert.equal(ids["recommended-percent"].textContent, "—");
  assert.equal(ids["recommended-capacity-track"].hidden, true);
  assert.match(ids["recommended-percent-note"].textContent, /unavailable/);
  context.renderLocation({ locations: { AboveCapacity: [prediction("open", 80, {
    predicted_percent: 160 })] } }, "AboveCapacity", now);
  assert.equal(ids["recommended-percent"].textContent, "160%");
  assert.equal(ids["recommended-capacity-fill"].style.width, "100%");
  assert.match(ids["quiet-hours-summary"].textContent, /No other/);
  context.renderLocation({ locations: { Unknown: [prediction("unknown", 1),
    prediction("partial", 2), prediction("closed", 0),
    prediction("open", null), prediction("open", 1, { time: iso(-1) })] } }, "Unknown", now);
  assert.equal(ids["recommended-values"].hidden, true);
  assert.equal(ids["recommended-count"].textContent, "—");
  assert.equal(ids["recommended-capacity-track"].hidden, true);
  assert.match(ids["recommended-time"].textContent, /No upcoming/);
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
  context.renderForecastOverview([prediction("open", 80, { predicted_percent: 160 })], now);
  assert.equal(ids["next-percent"].textContent, "160%");
  assert.equal(ids["next-capacity-fill"].style.width, "100%");
  context.renderForecastOverview([prediction("open", null, { predicted_percent: null })], now);
  assert.equal(ids["next-count"].textContent, "—");
  assert.equal(ids["next-capacity-track"].hidden, true);
  assert.match(ids["next-percent-note"].textContent, /unavailable/);

  assert.match(context.formatTimestamp("2026-10-09T18:00:00Z"), /2:00 PM/);
  assert.match(context.formatTimestamp("2026-10-09T18:03:00Z"), /2:03 PM/,
    "formatter must preserve supplied minutes rather than conceal misalignment");
  for (const stamp of ["2026-11-01T05:00:00Z", "2026-11-01T06:00:00Z"]) {
    assert.match(context.formatTimestamp(stamp), /1:00 AM/);
  }
  assert.match(context.formatTimestamp("2026-03-08T07:00:00Z"), /3:00 AM/);
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
  assert.equal(ids["recommended-count"].textContent, "10");
  assert.equal(ids["recommended-time"].textContent, context.formatTimestamp(timeline[6].time));
  assert.equal(ids["next-values"].hidden, false, "next hour remains distinct when it differs");
  assert.match(ids["quiet-hours-summary"].textContent, /20 people/);
  assert.equal(forecastRows().length, timeline.length, "complete timeline stays inspectable");
  assert(forecastRows().slice(0, 5).every((row) => !row.classList.has("quiet-hour")));
  assert.equal(ids["forecast-freshness"].textContent, "Generated 1 hr ago");
  assert.equal(ids["forecast-origin"].textContent, "Unknown", "observation time is not substituted with generation time");
  assert.equal(ids["generated-at"].textContent, context.formatTimestamp(timedData.generated_at));
  const timedChart = ids["forecast-chart"].children[0];
  const forecastDots = timedChart.children.filter((n) => n.tag === "circle");
  assert.equal(forecastDots.length, timeline.length, "all current forecast points retained");
  assert(forecastDots.every((n) => n.attributes.class === "chart-point"));
  const forecastPaths = timedChart.children.filter((n) => n.tag === "path");
  assert.equal(forecastPaths.length, 1, "one consistent forecast line");
  assert.equal(forecastPaths[0].attributes.class, "chart-line");
  assert.equal(timedChart.children.filter((n) =>
    n.attributes.class === "chart-closed-band").length, 1);
  assert.equal(timedChart.children.filter((n) =>
    n.attributes.class === "chart-partial-band").length, 1);
  assert.equal(timedChart.children.filter((n) => n.attributes.class === "chart-now").length, 1);
  assert.doesNotMatch(timedChart.textContent, /Elapsed|Upcoming/);
  context.renderLocation(timedData, "Timed", Date.parse("2026-10-05T06:00:00Z"));
  assert.equal(ids["next-count"].textContent, "—");
  assert.equal(ids["next-percent"].textContent, "—");
  for (const id of ["next-time", "recommended-time", "quiet-hours-summary", "forecast-freshness"]) {
    assert.match(ids[id].textContent, /Forecast is out of date/);
  }
  assert(forecastRows().every((row) => !row.classList.has("quiet-hour")));
  assert.equal(ids["recommended-values"].hidden, true);
  assert.equal(ids["recommended-count"].textContent, "—");
  assert.equal(ids["next-values"].hidden, true);
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
  assert.match(ids["history-summary"].textContent, /Average prediction error: 3.00 people \(MAE\) · 2 compared hours/);
  assert.match(ids["history-summary"].textContent, /Historical test during open hours/);
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
  assert.match(ids["history-summary"].textContent, /No past predictions made 6 hours ahead/);
  assert.equal(ids["history-chart"].children.length, 0);
  assert.deepEqual(requests, [
    { url: `${expectedBase}/api/predictions`, method: "GET" },
    { url: `${expectedBase}/api/evaluation-history?horizon=6`, method: "GET" },
  ],
    "page load reads predictions; renders, toggles, and timers never POST refresh");
  const fullName = "Marino Center — Second Floor Cardio";
  context.renderLocation({ locations: { [fullName]: [prediction("open", 5)] },
    forecast_origin: { [fullName]: iso(-60) }, generated_at: iso(-5) }, fullName, now);
  assert.equal(ids["forecast-heading"].textContent, "Second Floor Cardio");
  assert.equal(ids["location-facility"].textContent, "Marino Center");
  assert.equal(ids["history-location"].textContent, fullName, "history still keys on the full name");
  assert.equal(ids["forecast-origin"].textContent, context.formatTimestamp(iso(-60)));
  assert.equal(ids["generated-at"].textContent, context.formatTimestamp(iso(-5)));
  context.enhanceLocationPicker(["Marino Center Floor 1", "SquashBusters Courts", "Unmatched long name"]);
  assert.equal(options().length, 3);
  assert.equal(ids["location-menu"].children[0].attributes["aria-label"], "Marino Center");
  assert.equal(ids["location-menu"].children[1].attributes["aria-label"], "SquashBusters");
  assert.equal(ids["location-menu"].children[2].attributes["aria-label"], "Other locations");
  assert.equal(JSON.stringify(data), original, "rendering never mutates predictions");
  console.log(`PASS (${hostname}): API URLs, rendering, history, unchanged values, and GET-only loading`);
}

async function run() {
  const html = fs.readFileSync(path.join(__dirname, "../web/index.html"), "utf8");
  assert(html.indexOf('id="recommended-heading"') < html.indexOf('id="next-heading"'));
  assert(html.indexOf('id="next-heading"') < html.indexOf('id="best-times-heading"'));
  assert.match(html, /Past predictions vs\. actual occupancy/);
  assert.match(html, /Average prediction error \(MAE\)/);

  for (const [hostname, expectedBase] of [
    ["localhost", "http://127.0.0.1:5000"],
    ["127.0.0.1", "http://127.0.0.1:5000"],
    ["gymcast.example", ""],
  ]) {
    await main(hostname, expectedBase);
  }
}

run().catch((error) => { console.error(error); process.exitCode = 1; });
