// Dependency-free rendering checks: node tests/test_frontend.js
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const HOUR = 3600000;
// Values built inside the vm context carry its own Array prototype; compare plain copies.
const plain = (value) => JSON.parse(JSON.stringify(value));

// classList.add accepts several names in a browser; a plain Set keeps only the first.
class ClassList extends Set {
  add(...names) { names.forEach((name) => super.add(name)); return this; }
}

function element(tag) {
  let text = "";
  const node = {
    tag,
    children: [],
    value: "",
    hidden: false,
    checked: false,
    disabled: true,
    style: {},
    classList: new ClassList(),
    attributes: {},
    // Assigning className replaces the class list, as in a browser.
    set className(value) { this.classList = new ClassList(String(value).split(/\s+/).filter(Boolean)); },
    get className() { return [...this.classList].join(" "); },
    get textContent() { return text + this.children.map((child) => child.textContent).join(" "); },
    set textContent(value) { text = String(value); this.children = []; },
    appendChild(child) {
      this.children.push(child);
      if (this.tag === "select" && this.children.length === 1) this.value = child.value;
    },
    // Strings become text nodes, so mixed element/text content renders like the DOM.
    append(...children) {
      children.forEach((child) => this.appendChild(typeof child === "string"
        ? { textContent: child, children: [], contains: () => false } : child));
    },
    // Browsers clamp scrollLeft to 0 once the scrolled content is removed.
    replaceChildren(...children) { text = ""; this.children = []; this.scrollLeft = 0; this.append(...children); },
    querySelector(tag) { return this.children.find((child) => child.tag === tag) ?? null; },
    focus() { element.active = this; this.onfocus?.(); },
    contains(target) { return this === target || this.children.some((child) => child.contains?.(target)); },
    setAttribute(name, value) { this.attributes[name] = String(value); },
  };
  return node;
}

const IDS = [
  "status", "location-select", "location-label", "location-picker", "location-trigger",
  "location-menu", "location-selected", "location-facility", "forecast-heading",
  "current-availability", "forecast-freshness",
  "next-heading", "next-time", "next-level", "next-people", "next-meter", "next-meter-fill",
  "next-note", "coming-up", "coming-up-list",
  "best-window", "best-level", "best-people", "best-explanation", "also-quiet", "also-quiet-list",
  "chart-readout", "forecast-chart", "forecast-rows", "forecast-empty", "show-closed-hours",
  "forecast-origin", "generated-at", "history-headline", "history-summary", "history-chart",
  "history-location",
];

function load(hostname, data) {
  const ids = Object.fromEntries(IDS.map((id) => [id, element(id === "location-select" ? "select" : "div")]));
  const listeners = {};
  const requests = [];
  const state = { interval: null, fetchFails: false, history: null };
  const context = vm.createContext({
    window: { location: { hostname }, innerWidth: 1440,
      addEventListener: (name, handler) => { listeners[`window:${name}`] = handler; } },
    console: { log() {}, error() {} }, Date, Intl,
    document: {
      addEventListener: (name, handler) => { listeners[name] = handler; },
      getElementById: (id) => ids[id], createElement: element,
      createElementNS: (_namespace, tag) => element(tag), visibilityState: "visible",
    },
    fetch: async (url, options) => {
      requests.push({ url, method: options?.method ?? "GET" });
      if (url.includes("/api/evaluation-history")) {
        return state.history ? { ok: true, json: async () => state.history } : { ok: false, status: 404 };
      }
      if (state.fetchFails) return { ok: false, status: 502 };
      return { ok: true, json: async () => data };
    },
    setInterval: (callback) => { state.interval = callback; },
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, "../web/app.js"), "utf8"), context);
  return { ids, listeners, requests, state, context };
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
  const { ids, listeners, requests, state, context } = load(hostname, data);
  await new Promise(setImmediate);

  // ---- loading and the location picker (unchanged behaviour)
  assert.equal(ids["location-select"].children.length, 2);
  assert.equal(ids["location-select"].disabled, false);
  assert.equal(ids["location-select"].hidden, true);
  assert.equal(ids["location-picker"].hidden, false);
  assert.equal(ids.status.textContent, "", "successful load shows no status message");
  const options = () => ids["location-menu"].children.flatMap((group) =>
    group.children.filter((child) => child.attributes.role === "option"));
  assert.equal(options().length, 2);
  const named = context.locationHeading("Marino Center — Second Floor Cardio");
  assert.equal(named.facility, "Marino Center");
  assert.equal(named.name, "Second Floor Cardio");
  assert.equal(context.locationHeading("SquashBusters - 4th Floor").name, "4th Floor");
  assert.equal(context.locationHeading("Marino Center Pool").name, "Marino Center Pool");
  assert.equal(context.locationHeading("Other — Cardio").name, "Other — Cardio");
  const key = (name) => ({ key: name, preventDefault() {} });
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

  // ---- availability labels, rounding and the table (unchanged rules)
  const forecastRows = () => ids["forecast-rows"].children
    .filter((row) => !row.classList.has("day-divider"));
  assert.equal(forecastRows().length, 6, "closed rows hidden by default; partial/unknown retained");
  assert(forecastRows().every((row) => !row.classList.has("availability-closed")));
  ids["show-closed-hours"].checked = true;
  ids["show-closed-hours"].onchange();
  const rows = forecastRows();
  assert.equal(rows.length, data.locations.Marino.length, "all forecast hours remain visible");
  rows.forEach((row, index) => {
    const input = data.locations.Marino[index];
    assert.equal(row.children[1].textContent, String(input.predicted_count));
    assert.equal(row.children[2].children[0].textContent, `${input.predicted_percent}%`);
    // Only fully open upcoming hours inside the recommended window are highlighted.
    assert.equal(row.classList.has("quiet-hour"), index === 3 || index === 4);
  });
  assert(rows[0].classList.has("availability-closed"));
  assert.match(rows[0].children[3].textContent, /closed/);
  assert.match(rows[1].children[3].textContent, /Partially open \(30 of 60 min\)/);
  assert(rows[1].classList.has("availability-partial"));
  assert.equal(rows[2].children[3].textContent, "Hours unknown");
  assert.match(rows[4].children[3].textContent, /special hours/);
  assert.equal(rows[5].children[3].textContent, "Reported closed now");
  assert.equal(rows[7].children[3].textContent, "Hours unknown");
  assert.match(ids["current-availability"].textContent, /differs from the published schedule/);
  assert.equal(ids["current-availability"].attributes["data-state"], "closed");
  context.renderLocation(data, "Marino", now + 15 * 60000);
  assert.equal(forecastRows()[5].children[3].textContent, "Scheduled open", "expired live evidence falls back");
  assert(!forecastRows()[5].classList.has("quiet-hour"), "live-closed row is not fully open");
  assert.equal(context.formatCount(22.6), "23");
  assert.equal(context.formatCount(null), "—");
  assert.equal(context.formatPercent(25.1), "25%");
  context.renderLocation({ locations: { Frac: [prediction("open", 22.6)] } }, "Frac", now);
  assert.equal(forecastRows()[0].children[1].textContent, "23", "table shows whole people");
  assert.equal(forecastRows()[0].children[2].children[0].textContent, "45%");
  assert(forecastRows()[0].children[2].children[1].children[0].classList.has("level-moderate"));

  // ---- current status (unchanged wording)
  context.renderCurrentStatus({ ...live, live_status: "open", facility_status: "closed",
    live_valid_until: undefined, schedule_conflict: false });
  assert.match(ids["current-availability"].textContent, /^Open now · Live GoBoard update at/);
  assert.equal(ids["current-availability"].attributes["data-state"], "open");
  context.renderCurrentStatus({ ...live, live_is_fresh: false });
  assert.match(ids["current-availability"].textContent, /Status at .*scheduled open\. No fresh live confirmation/);
  context.renderCurrentStatus({ ...live, live_is_fresh: undefined });
  assert.equal(ids["current-availability"].textContent, "Current status unavailable");

  // ---- crowd levels
  assert.equal(context.busyLevel(34.9).key, "quiet");
  assert.equal(context.busyLevel(35).key, "moderate");
  assert.equal(context.busyLevel(64.9).key, "moderate");
  assert.equal(context.busyLevel(65).key, "busy");
  assert.equal(context.busyLevel(160).key, "busy");
  assert.equal(context.busyLevel(null), null);

  // ---- quiet windows: consecutive hours within the tolerance of the quietest
  const base = Math.ceil(now / HOUR) * HOUR;
  const hourly = (counts, extra = () => ({})) => counts.map((count, i) => ({
    time: new Date(base + (i + 1) * HOUR).toISOString(), predicted_count: count,
    predicted_percent: count, facility_status: "open", scheduled_status: "open",
    status_source: "published_schedule", scheduled_open_minutes: 60, ...extra(i),
  }));
  const tol = (people) => ({ people, source: "error" });
  let windows = context.quietWindows(hourly([40, 20, 22, 30, 50, 21, 60]), now, tol(3));
  assert.deepEqual(plain(windows.map((w) => w.hours.map((h) => h.predicted_count))), [[20, 22], [21]],
    "best window grows through close neighbours; a separate quiet hour is an alternative");
  assert.equal(windows[0].start, base + 2 * HOUR);
  assert.equal(windows[0].end, base + 4 * HOUR, "window ends when its last hour ends");
  assert.equal(windows[0].minCount, 20);
  assert.equal(windows[0].maxCount, 22);
  windows = context.quietWindows(hourly([10, 50, 50, 30]), now, tol(3));
  assert.equal(windows.length, 1, "an alternative far above the best is not called quiet");
  windows = context.quietWindows(hourly([5, 30, 5]), now, tol(1));
  assert.equal(windows[0].start, base + HOUR, "equal counts keep the earliest-time tie break");
  windows = context.quietWindows(hourly([10, 10, 10], (i) => (i === 1
    ? { facility_status: "closed", scheduled_status: "closed" } : {})), now, tol(5));
  assert.deepEqual(plain(windows.map((w) => w.hours.length)), [1, 1], "a closed hour splits windows");
  const mixed = context.quietWindows(hourly([30, 40]), now, tol(15))[0];
  assert.equal(context.windowLevelText(mixed.levels), "Quiet to moderate");
  assert.equal(context.quietWindows(hourly([5]).map((p) => ({ ...p, time: iso(-30) })), now, tol(5)).length, 0,
    "hours already under way are not recommended");

  // ---- time labels (New York time, minutes only when not on the hour)
  const fixed = Date.parse("2026-10-19T18:20:00-04:00");
  assert.equal(context.formatWindow(Date.parse("2026-10-20T06:00:00-04:00"),
    Date.parse("2026-10-20T10:00:00-04:00"), fixed), "Tomorrow, 6–10 AM");
  assert.equal(context.formatWindow(Date.parse("2026-10-19T22:00:00-04:00"),
    Date.parse("2026-10-20T01:00:00-04:00"), fixed), "Today, 10 PM–1 AM");
  assert.equal(context.formatWindow(Date.parse("2026-10-19T11:00:00-04:00"),
    Date.parse("2026-10-19T14:00:00-04:00"), fixed), "Today, 11 AM–2 PM");
  assert.match(context.formatWindow(Date.parse("2026-10-22T09:03:00-04:00"),
    Date.parse("2026-10-22T10:03:00-04:00"), fixed), /^Thursday, Oct 22, 9:03–10:03 AM$/);
  assert.equal(context.peopleRange(22.6, 31.4), "about 23–31 people");
  assert.equal(context.peopleRange(1.2, 1.4), "about 1 person");

  // ---- right now / next hour card
  const current = { ...hourly([50])[0], time: new Date(base - HOUR).toISOString(), predicted_percent: 55 };
  const day = [current, ...hourly([52, 47, 48, 20])];
  context.renderLocation({ generated_at: iso(-5), locations: { Day: day } }, "Day", now);
  assert.equal(ids["next-heading"].textContent, "Right now", "the hour in progress is the answer");
  assert.match(ids["next-time"].textContent, /^Today, /);
  assert.equal(ids["next-level"].textContent, "Moderate");
  assert.equal(ids["next-people"].textContent, "about 50 people · 55% full");
  assert.equal(ids["next-meter-fill"].style.width, "55%");
  assert(ids["next-meter-fill"].classList.has("level-moderate"));
  assert.equal(ids["coming-up-list"].children.length, 3);
  assert.match(ids["coming-up-list"].children[0].textContent, /Moderate · about 52 people/);
  assert.equal(ids["coming-up"].hidden, false);
  context.renderLocation({ locations: { Ahead: hourly([30, 40]) } }, "Ahead", now);
  assert.equal(ids["next-heading"].textContent, "Next hour", "without a current-hour forecast");
  assert.match(ids["next-note"].textContent, /Part of the quietest stretch/);
  const closedNow = [{ ...current, facility_status: "closed", scheduled_status: "closed" },
    ...hourly([12])];
  context.renderLocation({ locations: { Shut: closedNow } }, "Shut", now);
  assert.equal(ids["next-level"].textContent, "Closed");
  assert(ids["next-level"].classList.has("level-closed"));
  assert.match(ids["next-note"].textContent, /^Opens (today|tomorrow) at /);
  assert.equal(ids["next-people"].hidden, true);
  context.renderLocation({ locations: { NoCapacity: hourly([8]).map((p) => ({ ...p, predicted_percent: null })) } }, "NoCapacity", now);
  assert.equal(ids["next-level"].hidden, true);
  assert.equal(ids["next-people"].textContent, "about 8 people");
  assert.match(ids["next-note"].textContent, /Capacity estimate unavailable/);
  assert.equal(ids["best-level"].hidden, true, "no level without capacity");

  // ---- best time card
  context.renderLocation({ locations: { Day: day } }, "Day", now);
  assert.match(ids["best-window"].textContent, /^Today|^Tomorrow/);
  assert.equal(ids["best-people"].textContent, "about 20 people");
  assert.equal(ids["best-level"].textContent, "Quiet");
  assert.equal(ids["best-explanation"].textContent, "The quietest fully open hour in this forecast.");
  context.renderLocation({ locations: { Close: hourly([40, 20, 22, 30, 50, 21, 60]) } }, "Close", now);
  // These fixtures have capacity 100 (percent equals count), so 5% is 5 people.
  assert.match(ids["best-explanation"].textContent, /within 5 people of the quietest one, so they are about equally quiet/,
    "without history, tolerance is 5% of capacity (min 2)");
  assert.equal(ids["also-quiet"].hidden, false);
  assert.match(ids["also-quiet-list"].textContent, /about 21 people/);
  context.renderLocation({ locations: { Unknown: [prediction("unknown", 1),
    prediction("partial", 2), prediction("closed", 0), prediction("open", null),
    prediction("open", 1, { time: iso(-1) })] } }, "Unknown", now);
  assert.match(ids["best-window"].textContent, /No upcoming fully open hours/);
  assert.equal(ids["best-people"].hidden, true);
  assert.equal(ids["also-quiet"].hidden, true);

  // ---- 24-hour bar chart
  context.renderLocation({ locations: { Day: day } }, "Day", now);
  let bars = ids["forecast-chart"].children.filter((n) => n.classList.has("bar"));
  assert.equal(bars.length, day.length, "current hour stays until it ends");
  assert(bars[0].classList.has("level-moderate") && bars[4].classList.has("level-quiet"));
  assert.match(bars[1].attributes["aria-label"], /about 52 people · 52% full · Moderate/);
  assert.equal(bars[0].attributes["aria-pressed"], "true", "the first hour is selected by default");
  assert.equal(bars[0].tabIndex, 0);
  assert(bars.slice(1).every((bar) => bar.tabIndex === -1), "one tab stop for the whole chart");
  assert.match(ids["chart-readout"].textContent, /about 50 people · 55% full · Moderate/);
  bars[0].onkeydown(key("ArrowRight"));
  assert.equal(element.active, bars[1]);
  assert.equal(bars[1].attributes["aria-pressed"], "true");
  assert.match(ids["chart-readout"].textContent, /about 52 people/);
  bars[1].onkeydown(key("End"));
  assert.equal(element.active, bars[4]);
  // The 30-second timer re-renders the same location from the same data.
  context.renderLocation({ locations: { Day: day } }, "Day", now);
  bars = ids["forecast-chart"].children.filter((n) => n.classList.has("bar"));
  assert.equal(bars[4].attributes["aria-pressed"], "true", "selection survives the timer re-render");
  const bracket = ids["forecast-chart"].children.find((n) => n.classList.has("best-bracket"));
  assert(bracket, "best window is marked on the chart");
  assert.equal(bracket.style.left, `${4 / 5 * 100}%`);
  const withClosed = [...hourly([10, 10]), ...hourly([0, 0, 0]).map((p, i) => ({
    ...p, time: new Date(base + (i + 3) * HOUR).toISOString(),
    facility_status: "closed", scheduled_status: "closed", predicted_count: 30 }))];
  context.renderLocation({ locations: { Night: withClosed } }, "Night", now);
  bars = ids["forecast-chart"].children.filter((n) => n.classList.has("bar"));
  assert(bars[2].classList.has("status-closed") && !bars[2].classList.has("level-quiet"));
  assert.equal(bars[2].children[0].style.height, "6%", "closed hours are a stub, not a crowd");
  assert.match(bars[2].attributes["aria-label"], /Scheduled closed/);
  context.renderLocation({ locations: { Empty: [] } }, "Empty", now);
  assert.match(ids["forecast-chart"].textContent, /No occupancy forecast/);
  assert.equal(ids["chart-readout"].textContent, "");

  // ---- empty table states (unchanged)
  ids["show-closed-hours"].checked = false;
  const closedOnly = { locations: { Closed: [prediction("closed", 0)] } };
  context.renderLocation(closedOnly, "Closed", now);
  assert.equal(forecastRows().length, 0);
  assert.equal(ids["forecast-empty"].hidden, false);
  assert.match(ids["forecast-empty"].textContent, /Show closed hours/);
  ids["show-closed-hours"].checked = true;
  context.renderLocation(closedOnly, "Closed", now);
  assert.equal(forecastRows().length, 1);
  assert.equal(ids["forecast-empty"].hidden, true);

  // ---- timestamps and expiry (unchanged rules)
  assert.match(context.formatTimestamp("2026-10-09T18:00:00Z"), /2:00 PM/);
  assert.match(context.formatTimestamp("2026-10-09T18:03:00Z"), /2:03 PM/,
    "formatter must preserve supplied minutes rather than conceal misalignment");
  for (const stamp of ["2026-11-01T05:00:00Z", "2026-11-01T06:00:00Z"]) {
    assert.match(context.formatTimestamp(stamp), /1:00 AM/);
  }
  assert.match(context.formatTimestamp("2026-03-08T07:00:00Z"), /3:00 AM/);
  const fixedNow = Date.parse("2026-10-05T04:00:00Z");
  const timeline = [
    prediction("open", 1, { time: "2026-10-04T23:00:00-04:00" }),
    prediction("open", 2, { time: "2026-10-05T00:00:00-04:00" }),
    prediction("open", 20, { time: "2026-10-05T01:00:00-04:00" }),
    prediction("open", 10, { time: "2026-10-05T06:00:00Z" }),
  ];
  const timedData = { generated_at: "2026-10-05T03:00:00Z", locations: { Timed: timeline } };
  const unchangedTimeline = JSON.stringify(timedData);
  context.renderLocation(timedData, "Timed", fixedNow);
  assert.equal(ids["next-heading"].textContent, "Right now", "exactly-now start is the hour in progress");
  assert.equal(ids["next-people"].textContent, "about 2 people · 4% full");
  assert.equal(ids["best-people"].textContent, "about 10 people", "past and in-progress hours are not recommended");
  assert.equal(ids["forecast-freshness"].textContent, "Updated 1 hr ago");
  assert.equal(ids["forecast-freshness"].attributes["data-expired"], "false");
  assert.equal(ids["forecast-origin"].textContent, "Unknown", "observation time is not substituted with generation time");
  assert.equal(ids["generated-at"].textContent, context.formatTimestamp(timedData.generated_at));
  context.renderLocation(timedData, "Timed", Date.parse("2026-10-05T08:00:00Z"));
  for (const id of ["next-time", "best-window", "forecast-freshness", "forecast-chart"]) {
    assert.match(ids[id].textContent, /Forecasts have not updated recently/);
  }
  assert.equal(ids["forecast-freshness"].attributes["data-expired"], "true");
  assert(forecastRows().every((row) => !row.classList.has("quiet-hour")));
  assert.equal(forecastRows().length, timeline.length, "complete timeline stays inspectable");
  assert.equal(JSON.stringify(timedData), unchangedTimeline);
  assert.equal(context.isUpcoming({ time: "invalid" }, fixedNow), false);
  assert.equal(context.isUpcoming({ time: "2026-10-05T04:00:00.001Z" }, fixedNow), true);
  context.renderLocation(timedData, "Timed", fixedNow);
  assert.doesNotMatch(ids["forecast-freshness"].textContent, /not updated/, "switching back clears expiry");

  // ---- accuracy
  assert.match(ids["history-headline"].textContent, /not available yet/);
  state.history = {
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
  ids["location-select"].value = "Marino";
  await context.loadEvaluationHistory();
  context.renderEvaluationHistory("Timed");
  assert.equal(ids["history-headline"].textContent, "Typically within about  3 people");
  assert.match(ids["history-summary"].textContent,
    /off by 3\.0 people on average, across 2 past open hours/);
  assert.equal(context.locationMae("Timed"), 3, "horizon 6 only; other horizons are never mixed in");
  const historyChart = ids["history-chart"].children[0];
  assert.equal(historyChart.children.filter((n) => n.tag === "circle").length, 4);
  assert.equal(historyChart.children.filter((n) => n.tag === "path").length, 2);
  assert(historyChart.children.filter((n) => n.tag === "path").every((n) =>
    n.attributes.d.includes("L")), "consecutive scored hours connect");
  state.history.rows[2].fold_cutoff = "fold2";
  context.renderEvaluationHistory("Timed");
  assert(ids["history-chart"].children[0].children.filter((n) => n.tag === "path")
    .every((n) => !n.attributes.d.includes("L")), "fold boundaries stay separate");
  context.renderEvaluationHistory("Missing");
  assert.match(ids["history-headline"].textContent, /No past forecasts to compare/);
  assert.equal(ids["history-chart"].children.length, 0);
  // With history, the window tolerance is this location's error, and the copy says so.
  context.renderLocation({ locations: { Timed: hourly([40, 20, 22, 30, 50, 21, 60]) } }, "Timed", now);
  assert.match(ids["best-explanation"].textContent, /within 3 people of the quietest one\. That is about the forecast's typical error here/);

  // ---- requests and background refresh (unchanged behaviour)
  state.interval();
  assert.equal(ids["forecast-heading"].textContent, "Marino",
    "the timer re-renders the selected location; the request list below proves it did not fetch");
  assert.deepEqual(requests, [
    { url: `${expectedBase}/api/predictions`, method: "GET" },
    { url: `${expectedBase}/api/evaluation-history?horizon=6`, method: "GET" },
    { url: `${expectedBase}/api/evaluation-history?horizon=6`, method: "GET" },
  ], "page reads predictions and history; renders and timers never POST refresh");
  const fullName = "Marino Center — Second Floor Cardio";
  context.renderLocation({ locations: { [fullName]: [prediction("open", 5)] },
    forecast_origin: { [fullName]: iso(-60) }, generated_at: iso(-5) }, fullName, now);
  assert.equal(ids["forecast-heading"].textContent, "Second Floor Cardio");
  assert.equal(ids["location-facility"].textContent, "Marino Center");
  assert.equal(ids["history-location"].textContent, fullName, "history still keys on the full name");
  assert.equal(ids["forecast-origin"].textContent, context.formatTimestamp(iso(-60)));
  context.enhanceLocationPicker(["Marino Center Floor 1", "SquashBusters Courts", "Unmatched long name"]);
  assert.equal(options().length, 3);
  assert.equal(ids["location-menu"].children[0].attributes["aria-label"], "Marino Center");
  assert.equal(ids["location-menu"].children[1].attributes["aria-label"], "SquashBusters");
  assert.equal(ids["location-menu"].children[2].attributes["aria-label"], "Other locations");
  context.enhanceLocationPicker(Object.keys(data.locations));
  ids["location-trigger"].onclick();
  await context.loadPredictions({ background: true });
  assert.equal(ids["location-menu"].hidden, false, "background refresh keeps an open menu open");
  state.fetchFails = true;
  await context.loadPredictions({ background: true });
  assert.equal(ids.status.textContent, "", "failed background refresh keeps the shown forecast");
  await context.loadPredictions();
  assert.match(ids.status.textContent, /temporarily unavailable/);
  assert.doesNotMatch(ids.status.textContent, /Flask/);
  assert.equal(JSON.stringify(data), original, "rendering never mutates predictions");
  console.log(`PASS (${hostname}): API URLs, rendering, windows, chart, history, unchanged values, GET-only loading`);
}

async function run() {
  const html = fs.readFileSync(path.join(__dirname, "../web/index.html"), "utf8");
  const order = ["next-heading", "best-heading", "chart-heading", "detail-heading", "history-heading"]
    .map((id) => html.indexOf(`id="${id}"`));
  assert(order.every((position, i) => position > 0 && (i === 0 || position > order[i - 1])),
    "now and best time lead; chart, table and accuracy follow");
  assert.match(html, /<details id="detail-toggle">/, "hour-by-hour table is collapsed by default");
  assert.match(html, /not affiliated with Northeastern University/);
  for (const id of IDS) assert(html.includes(`id="${id}"`), `index.html is missing #${id}`);

  for (const [hostname, expectedBase] of [
    ["localhost", "http://127.0.0.1:5000"],
    ["127.0.0.1", "http://127.0.0.1:5000"],
    ["gymcast.example", ""],
  ]) {
    await main(hostname, expectedBase);
  }
}

run().catch((error) => {
  console.error(error);
  process.exit(1);
});
