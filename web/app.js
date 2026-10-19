let loadedData = null;

// Local development uses a separate Flask server; production proxies /api on this origin.
const API_BASE = ["localhost", "127.0.0.1"].includes(window.location.hostname)
  ? "http://127.0.0.1:5000" : "";

const EXPIRED_FORECAST =
  "Forecasts have not updated recently, so upcoming hours are unavailable. Check back soon.";

const statusElement = document.getElementById("status");

// The droplet regenerates predictions.json on its own schedule; re-reading it picks that up.
const REFETCH_MS = 10 * 60000;
let lastFetchedAt = 0;

async function loadPredictions({ background = false } = {}) {
  if (!background) statusElement.textContent = "Loading forecasts…";
  lastFetchedAt = Date.now();

  try {
    const response = await fetch(`${API_BASE}/api/predictions`, { method: "GET" });

    if (!response.ok) {
      throw new Error(`Request failed: HTTP ${response.status}`);
    }

    const data = await response.json();
    const locations = Object.keys(data.locations ?? {});
    const previousLocation = document.getElementById("location-select").value;

    // Rebuilding the picker would close an open menu, so only swap data when locations match.
    if (background && loadedData
      && JSON.stringify(Object.keys(loadedData.locations ?? {})) === JSON.stringify(locations)) {
      loadedData = data;
      renderSelectedLocation();
      return;
    }
    loadedData = data;

    if (locations.length === 0) {
      statusElement.textContent = "No locations available yet.";
      return;
    }

    const locationSelect = document.getElementById("location-select");

    locationSelect.replaceChildren();

    for (const locationName of locations) {
      const option = document.createElement("option");
      option.value = locationName;
      option.textContent = locationName;
      locationSelect.appendChild(option);
    }

    if (locations.includes(previousLocation)) locationSelect.value = previousLocation;
    locationSelect.disabled = false;

    locationSelect.onchange = () => {
      syncLocationPicker();
      renderSelectedLocation();
    };

    enhanceLocationPicker(locations);
    renderLocation(data, locationSelect.value);

    statusElement.textContent = "";
  } catch (error) {
    console.error("Could not load forecasts:", error);
    // A failed background refresh keeps the forecast already on screen.
    if (background && loadedData) return;
    statusElement.textContent = "Forecasts are temporarily unavailable. Try again shortly.";
  }
}

// The select remains the source of truth and the fallback if enhancement fails.
let locationOptions = [];
function syncLocationPicker() {
  const selected = document.getElementById("location-select").value;
  document.getElementById("location-selected").textContent = selected;
  for (const option of locationOptions) {
    option.setAttribute("aria-selected", String(option.locationName === selected));
  }
}

function closeLocationPicker(restoreFocus = false) {
  document.getElementById("location-menu").hidden = true;
  document.getElementById("location-trigger").setAttribute("aria-expanded", "false");
  if (restoreFocus) document.getElementById("location-trigger").focus();
}

function openLocationPicker(index) {
  const menu = document.getElementById("location-menu");
  menu.hidden = false;
  document.getElementById("location-trigger").setAttribute("aria-expanded", "true");
  const selected = locationOptions.findIndex((o) =>
    o.locationName === document.getElementById("location-select").value);
  locationOptions[index ?? Math.max(0, selected)]?.focus();
}

function enhanceLocationPicker(locations) {
  const picker = document.getElementById("location-picker");
  const menu = document.getElementById("location-menu");
  const trigger = document.getElementById("location-trigger");
  menu.replaceChildren();
  locationOptions = [];
  const groups = new Map();
  for (const name of locations) {
    const facility = /^marino\b/i.test(name) ? "Marino Center"
      : /^squash\s*busters\b/i.test(name) ? "SquashBusters" : "Other locations";
    if (!groups.has(facility)) groups.set(facility, []);
    groups.get(facility).push(name);
  }
  for (const [facility, names] of groups) {
    const group = document.createElement("div");
    group.setAttribute("role", "group");
    group.setAttribute("aria-label", facility);
    const heading = document.createElement("p");
    heading.classList.add("location-group-label");
    heading.setAttribute("aria-hidden", "true");
    heading.textContent = facility;
    group.appendChild(heading);
    for (const name of names) {
      const option = document.createElement("div");
      option.setAttribute("role", "option");
      option.tabIndex = -1;
      option.classList.add("location-option");
      option.locationName = name;
      const label = document.createElement("span");
      label.textContent = name;
      const check = document.createElement("span");
      check.classList.add("location-check");
      check.setAttribute("aria-hidden", "true");
      check.textContent = "✓";
      option.append(label, check);
      option.onclick = () => {
        const select = document.getElementById("location-select");
        select.value = name;
        select.onchange();
        closeLocationPicker(true);
      };
      option.onkeydown = (event) => {
        const index = locationOptions.indexOf(option);
        let target;
        if (event.key === "ArrowDown") target = (index + 1) % locationOptions.length;
        if (event.key === "ArrowUp") target = (index - 1 + locationOptions.length) % locationOptions.length;
        if (event.key === "Home") target = 0;
        if (event.key === "End") target = locationOptions.length - 1;
        if (target !== undefined) { event.preventDefault(); locationOptions[target].focus(); }
        if (event.key === "Enter" || event.key === " ") { event.preventDefault(); option.onclick(); }
        if (event.key === "Escape") { event.preventDefault(); closeLocationPicker(true); }
        if (event.key === "Tab") closeLocationPicker(true);
        if (event.key.length === 1 && event.key !== " ") {
          const ordered = [...locationOptions.slice(index + 1), ...locationOptions.slice(0, index + 1)];
          ordered.find((o) => o.locationName.toLowerCase().startsWith(event.key.toLowerCase()))?.focus();
        }
      };
      locationOptions.push(option);
      group.appendChild(option);
    }
    menu.appendChild(group);
  }
  trigger.onclick = () => menu.hidden ? openLocationPicker() : closeLocationPicker(true);
  trigger.onkeydown = (event) => {
    if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
      event.preventDefault();
      openLocationPicker(event.key === "Home" ? 0
        : ["ArrowUp", "End"].includes(event.key) ? locationOptions.length - 1 : undefined);
    }
    if (event.key === "Escape") closeLocationPicker(true);
  };
  picker.onfocusout = (event) => {
    if (!picker.contains(event.relatedTarget)) closeLocationPicker();
  };
  syncLocationPicker();
  closeLocationPicker();
  document.getElementById("location-select").hidden = true;
  picker.hidden = false;
  document.getElementById("location-label").htmlFor = "location-trigger";
}

document.addEventListener("pointerdown", (event) => {
  if (!document.getElementById("location-picker").contains(event.target)) closeLocationPicker();
});

function locationHeading(locationName) {
  // Only remove a recognizable prefix with an explicit separator; unmatched names stay intact.
  const match = locationName.match(/^(Marino(?: Center)?|Squash\s*Busters)\s*(?:—|–|-|:)\s*(.+)$/i);
  return match ? { facility: /^marino/i.test(match[1]) ? "Marino Center" : "SquashBusters",
    name: match[2] } : { facility: "Your location", name: locationName };
}

const HOUR_MS = 3600000;
const TIMEZONE = "America/New_York";

// Crowd levels use percent of the location's own capacity, so a studio that
// never fills reads Quiet all day instead of being graded against the gym floor.
const LEVELS = [
  { key: "quiet", label: "Quiet", below: 35 },
  { key: "moderate", label: "Moderate", below: 65 },
  { key: "busy", label: "Busy", below: Infinity },
];

function busyLevel(percent) {
  if (!Number.isFinite(percent)) return null;
  return LEVELS.find((level) => percent < level.below);
}

function renderLocation(data, locationName, now = Date.now()) {
  // Preserve history scroll position during the current-forecast clock updates.
  if (document.getElementById("history-location").textContent !== locationName) {
    renderEvaluationHistory(locationName);
  }
  const predictions = data.locations[locationName];

  const heading = locationHeading(locationName);
  document.getElementById("location-facility").textContent = heading.facility;
  document.getElementById("forecast-heading").textContent = heading.name;
  document.getElementById("forecast-origin").textContent =
    formatTimestamp(data.forecast_origin?.[locationName]);
  document.getElementById("generated-at").textContent = formatTimestamp(data.generated_at);

  renderCurrentStatus(data.current_availability?.[locationName]);
  renderForecastFreshness(data, predictions, now);

  const windows = quietWindows(predictions, now, windowTolerance(locationName, predictions));
  const best = windows[0] ?? null;
  renderBestTimes(windows, predictions, now);
  renderNextHour(predictions, now, best);
  renderForecastChart(predictions, now, best);
  renderForecastTable(predictions, now, best);
}

function renderSelectedLocation() {
  const location = document.getElementById("location-select").value;
  if (loadedData?.locations?.[location]) renderLocation(loadedData, location);
}

function isUpcoming(prediction, now) {
  return Date.parse(prediction.time) > now;
}

function forecastExpired(predictions, now) {
  const timestamps = predictions.map((p) => Date.parse(p.time)).filter(Number.isFinite);
  return timestamps.length > 0 && Math.max(...timestamps) <= now;
}

function renderForecastFreshness(data, predictions, now) {
  const element = document.getElementById("forecast-freshness");
  const expired = forecastExpired(predictions, now);
  element.setAttribute("data-expired", String(expired));
  if (expired) {
    element.textContent = EXPIRED_FORECAST;
    return;
  }
  if (!predictions.some((p) => isUpcoming(p, now))) {
    element.textContent = "No upcoming forecast hours available.";
    return;
  }
  const generated = Date.parse(data.generated_at?.replace(" ", "T"));
  const ageMinutes = Math.floor((now - generated) / 60000);
  element.textContent = Number.isFinite(ageMinutes) && ageMinutes >= 0
    ? `Updated ${ageMinutes < 60 ? `${ageMinutes} min` : `${Math.floor(ageMinutes / 60)} hr`} ago`
    : "Update time unavailable";
}

// ---------------------------------------------------------------- quiet windows

/*
 * How close to the quietest hour still counts as "just as quiet"?
 *
 * The model's average error for this location (from the walk-forward history)
 * is the honest answer: two hours whose forecasts differ by less than that are
 * not distinguishable, so presenting one as "the" quietest hour overstates what
 * the forecast knows. Without history, fall back to 5% of capacity (min 2 people).
 */
function windowTolerance(locationName, predictions) {
  const mae = locationMae(locationName);
  if (Number.isFinite(mae)) return { people: Math.max(1, Math.round(mae)), source: "error" };
  const capacity = predictions.map((p) => p.predicted_count / (p.predicted_percent / 100))
    .find((value) => Number.isFinite(value) && value > 0);
  return { people: Math.max(2, Math.round((capacity ?? 0) * 0.05)), source: "fallback" };
}

/*
 * Group eligible hours into windows, quietest first.
 *
 * Seed with the lowest remaining hour (earliest on ties), then grow the window
 * through directly adjacent eligible hours whose forecast stays within the
 * tolerance of that seed. Seeds that would touch an existing window are skipped,
 * so one stretch is never shown as two.
 */
function quietWindows(predictions, now, tolerance, limit = 3) {
  const hours = predictions
    .filter((prediction) => isQuietHourCandidate(prediction, now))
    .map((prediction) => ({ prediction, start: Date.parse(prediction.time) }))
    .sort((a, b) => a.start - b.start);
  const order = hours.map((_, index) => index).sort((a, b) =>
    hours[a].prediction.predicted_count - hours[b].prediction.predicted_count
    || hours[a].start - hours[b].start);
  const used = new Set();
  const adjacent = (i, j) => j >= 0 && j < hours.length && hours[j].start - hours[i].start === (j - i) * HOUR_MS;
  const windows = [];

  for (const seed of order) {
    if (windows.length >= limit) break;
    if (used.has(seed)) continue;
    const ceiling = hours[seed].prediction.predicted_count + tolerance.people;
    const joins = (from, to) => adjacent(from, to) && !used.has(to)
      && hours[to].prediction.predicted_count <= ceiling;
    let first = seed;
    let last = seed;
    while (joins(first, first - 1)) first -= 1;
    while (joins(last, last + 1)) last += 1;
    const touches = (adjacent(first, first - 1) && used.has(first - 1))
      || (adjacent(last, last + 1) && used.has(last + 1));
    if (touches) continue;
    const members = hours.slice(first, last + 1);
    members.forEach((_, offset) => used.add(first + offset));
    const counts = members.map((member) => member.prediction.predicted_count);
    const percents = members.map((member) => member.prediction.predicted_percent);
    windows.push({
      start: members[0].start,
      end: members[members.length - 1].start + HOUR_MS,
      hours: members.map((member) => member.prediction),
      minCount: Math.min(...counts),
      maxCount: Math.max(...counts),
      // Both ends of the range are shown, so a window is never labelled more optimistically
      // than its busiest hour.
      levels: percents.every(Number.isFinite)
        ? { low: busyLevel(Math.min(...percents)), high: busyLevel(Math.max(...percents)) } : null,
      tolerance,
    });
  }
  // An alternative must meet the same test as hours inside a window: starting within
  // the tolerance of the quietest hour. Anything looser would advertise a merely
  // less-busy stretch as "also quiet".
  const best = windows[0];
  return windows.filter((window) =>
    window === best || window.minCount <= best.minCount + tolerance.people);
}

function windowLevelText(levels) {
  if (!levels) return null;
  return levels.low === levels.high ? levels.low.label
    : `${levels.low.label} to ${levels.high.label.toLowerCase()}`;
}

function inWindow(window, prediction) {
  const start = Date.parse(prediction.time);
  return Boolean(window) && start >= window.start && start < window.end;
}

// --------------------------------------------------------------- time labels

function nyParts(stamp) {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: TIMEZONE, year: "numeric", month: "2-digit", day: "2-digit",
    hour: "numeric", minute: "2-digit", hour12: true, weekday: "short",
  }).formatToParts(new Date(stamp));
  const get = (type) => parts.find((part) => part.type === type)?.value;
  return {
    date: `${get("year")}-${get("month")}-${get("day")}`,
    hour: get("hour"), minute: get("minute"), period: get("dayPeriod"), weekday: get("weekday"),
  };
}

function dayLabel(stamp, now) {
  const day = nyParts(stamp).date;
  if (day === nyParts(now).date) return "Today";
  if (day === nyParts(now + 24 * HOUR_MS).date) return "Tomorrow";
  return new Date(stamp).toLocaleDateString("en-US", {
    timeZone: TIMEZONE, weekday: "long", month: "short", day: "numeric",
  });
}

function clockLabel(parts) {
  return parts.minute === "00" ? parts.hour : `${parts.hour}:${parts.minute}`;
}

// "Today, 6–10 AM" or "Tomorrow, 10 PM–1 AM"; minutes appear only if not on the hour.
function formatWindow(start, end, now) {
  const from = nyParts(start);
  const to = nyParts(end);
  const range = from.period === to.period
    ? `${clockLabel(from)}–${clockLabel(to)} ${to.period}`
    : `${clockLabel(from)} ${from.period}–${clockLabel(to)} ${to.period}`;
  return `${dayLabel(start, now)}, ${range}`;
}

function peopleText(count) {
  const rounded = Math.round(count);
  return `${rounded} ${rounded === 1 ? "person" : "people"}`;
}

function peopleRange(min, max) {
  const low = Math.round(min);
  const high = Math.round(max);
  return low === high ? `about ${peopleText(low)}` : `about ${low}–${high} people`;
}

// ----------------------------------------------------------------- summaries

function setLevelChip(element, level, fallbackText = null, text = null) {
  element.className = "level-chip";
  element.hidden = !level && !fallbackText;
  if (level) {
    element.classList.add(`level-${level.key}`);
    element.textContent = text ?? level.label;
  } else if (fallbackText) {
    element.classList.add("level-closed");
    element.textContent = fallbackText;
  }
}

function renderBestTimes(windows, predictions, now) {
  const best = windows[0];
  const time = document.getElementById("best-window");
  const people = document.getElementById("best-people");
  const explanation = document.getElementById("best-explanation");
  time.textContent = best ? formatWindow(best.start, best.end, now)
    : forecastExpired(predictions, now) ? EXPIRED_FORECAST
      : "No upcoming fully open hours with a forecast.";
  setLevelChip(document.getElementById("best-level"), best?.levels?.low ?? null, null,
    windowLevelText(best?.levels ?? null));
  people.hidden = !best;
  explanation.hidden = !best;
  if (best) {
    people.textContent = peopleRange(best.minCount, best.maxCount);
    explanation.textContent = best.hours.length === 1
      ? "The quietest fully open hour in this forecast."
      : best.tolerance.source === "error"
        ? `Each of these hours is forecast within ${peopleText(best.tolerance.people)} of the quietest one. That is about the forecast's typical error here, so they are about equally quiet.`
        : `Each of these hours is forecast within ${peopleText(best.tolerance.people)} of the quietest one, so they are about equally quiet.`;
  }

  const list = document.getElementById("also-quiet-list");
  list.replaceChildren();
  for (const window of windows.slice(1)) {
    const item = document.createElement("li");
    const when = document.createElement("strong");
    when.textContent = formatWindow(window.start, window.end, now);
    const range = document.createElement("span");
    range.textContent = peopleRange(window.minCount, window.maxCount);
    item.append(when, range);
    list.appendChild(item);
  }
  document.getElementById("also-quiet").hidden = windows.length < 2;
}

function coveringHour(predictions, now) {
  return predictions.find((prediction) => {
    const start = Date.parse(prediction.time);
    return start <= now && now < start + HOUR_MS;
  });
}

function hourSummary(prediction, now) {
  const status = forecastStatus(prediction, now);
  if (status.status === "closed") return { key: "closed", text: "Closed" };
  const level = busyLevel(prediction.predicted_percent);
  const people = Number.isFinite(prediction.predicted_count)
    ? peopleRange(prediction.predicted_count, prediction.predicted_count) : "no estimate";
  return { key: level?.key ?? "unknown", text: level ? `${level.label} · ${people}` : people };
}

// The model also forecasts the hour in progress; that is the "right now" answer.
// Before it exists (or once it has passed) the card falls back to the next hour.
function renderNextHour(predictions, now, best) {
  const ordered = predictions
    .filter((prediction) => Number.isFinite(Date.parse(prediction.time)))
    .sort((a, b) => Date.parse(a.time) - Date.parse(b.time));
  const current = coveringHour(ordered, now);
  const upcoming = ordered.filter((prediction) => isUpcoming(prediction, now));
  const next = current ?? upcoming[0];
  document.getElementById("next-heading").textContent = current ? "Right now" : "Next hour";
  const time = document.getElementById("next-time");
  const people = document.getElementById("next-people");
  const meter = document.getElementById("next-meter");
  const fill = document.getElementById("next-meter-fill");
  const note = document.getElementById("next-note");
  note.textContent = "";
  meter.hidden = true;
  people.hidden = true;

  const later = upcoming.filter((prediction) => prediction !== next).slice(0, 3);
  const list = document.getElementById("coming-up-list");
  list.replaceChildren();
  for (const prediction of later) {
    const summary = hourSummary(prediction, now);
    const item = document.createElement("li");
    const when = document.createElement("strong");
    when.textContent = formatClock(prediction.time);
    const detail = document.createElement("span");
    const dot = document.createElement("i");
    dot.classList.add("dot", `level-${summary.key}`);
    dot.setAttribute("aria-hidden", "true");
    detail.append(dot, summary.text);
    item.append(when, detail);
    list.appendChild(item);
  }
  document.getElementById("coming-up").hidden = later.length === 0;

  if (!next) {
    time.textContent = forecastExpired(predictions, now) ? EXPIRED_FORECAST
      : "No upcoming hours in this forecast.";
    setLevelChip(document.getElementById("next-level"), null);
    return;
  }
  const start = Date.parse(next.time);
  time.textContent = formatWindow(start, start + HOUR_MS, now);
  const status = forecastStatus(next, now);

  if (status.status === "closed") {
    setLevelChip(document.getElementById("next-level"), null, "Closed");
    const opens = upcoming.find((p) => forecastStatus(p, now).status !== "closed");
    note.textContent = opens
      ? `Opens ${dayLabel(Date.parse(opens.time), now).toLowerCase()} at ${formatClock(opens.time)}.`
      : "No opening in this forecast.";
    return;
  }

  const level = busyLevel(next.predicted_percent);
  setLevelChip(document.getElementById("next-level"), level,
    level ? null : status.status === "unknown" ? "Hours unknown" : null);
  people.hidden = !Number.isFinite(next.predicted_count);
  people.textContent = Number.isFinite(next.predicted_percent)
    ? `${peopleRange(next.predicted_count, next.predicted_count)} · ${formatPercent(next.predicted_percent)} full`
    : peopleRange(next.predicted_count, next.predicted_count);
  if (level) {
    meter.hidden = false;
    fill.className = `meter-fill level-${level.key}`;
    fill.style.width = `${Math.max(0, Math.min(100, next.predicted_percent))}%`;
  }
  const notes = [];
  if (status.status !== "open") notes.push(status.label);
  if (!Number.isFinite(next.predicted_percent)) notes.push("Capacity estimate unavailable");
  if (inWindow(best, next)) notes.push("Part of the quietest stretch ahead.");
  note.textContent = notes.join(" · ");
}

// -------------------------------------------------------------- 24-hour chart

let selectedBarTime = null;

function barDescription(prediction, now) {
  const status = forecastStatus(prediction, now);
  const when = `${dayLabel(Date.parse(prediction.time), now)} ${formatClock(prediction.time)}`;
  if (status.status === "closed") return { when, detail: status.label };
  const level = busyLevel(prediction.predicted_percent);
  const parts = [peopleRange(prediction.predicted_count, prediction.predicted_count)];
  if (Number.isFinite(prediction.predicted_percent)) {
    parts.push(`${formatPercent(prediction.predicted_percent)} full`);
  }
  if (level) parts.push(level.label);
  if (status.status !== "open") parts.push(status.label);
  return { when, detail: parts.join(" · ") };
}

function renderForecastChart(predictions, now, best) {
  const container = document.getElementById("forecast-chart");
  const readout = document.getElementById("chart-readout");
  container.replaceChildren();
  // The current hour stays on the chart until it ends; earlier hours drop off.
  const points = predictions
    .filter((p) => Number.isFinite(Date.parse(p.time)) && Date.parse(p.time) + HOUR_MS > now)
    .sort((a, b) => Date.parse(a.time) - Date.parse(b.time));
  if (!points.length) {
    const empty = document.createElement("p");
    empty.classList.add("chart-empty");
    empty.textContent = forecastExpired(predictions, now) ? EXPIRED_FORECAST
      : "No occupancy forecast available to chart.";
    container.appendChild(empty);
    readout.textContent = "";
    return;
  }

  const counts = points.map((p) => p.predicted_count).filter(Number.isFinite);
  const ceiling = Math.max(10, Math.ceil(Math.max(0, ...counts) / 10) * 10);
  const bars = [];
  let previousDay = null;

  points.forEach((prediction, index) => {
    const status = forecastStatus(prediction, now).status;
    const level = busyLevel(prediction.predicted_percent);
    const bar = document.createElement("button");
    bar.type = "button";
    bar.classList.add("bar", `status-${status}`);
    if (level && status !== "closed") bar.classList.add(`level-${level.key}`);
    const fill = document.createElement("span");
    fill.classList.add("bar-fill");
    // Closed hours show a short hatched stub rather than a count nobody can be part of.
    const height = status === "closed" || !Number.isFinite(prediction.predicted_count) ? 6
      : Math.max(2, prediction.predicted_count / ceiling * 100);
    fill.style.height = `${height}%`;
    bar.appendChild(fill);

    const parts = nyParts(Date.parse(prediction.time));
    if (Number(parts.hour) % 3 === 0 && parts.minute === "00") {
      const label = document.createElement("span");
      label.classList.add("bar-label");
      label.setAttribute("aria-hidden", "true");
      label.textContent = `${parts.hour}${parts.period === "AM" ? "a" : "p"}`;
      bar.appendChild(label);
    }
    if (parts.date !== previousDay) {
      if (previousDay !== null) bar.classList.add("day-start");
      const day = document.createElement("span");
      day.classList.add("bar-day");
      day.setAttribute("aria-hidden", "true");
      day.textContent = dayLabel(Date.parse(prediction.time), now);
      bar.appendChild(day);
      previousDay = parts.date;
    }

    const description = barDescription(prediction, now);
    bar.setAttribute("aria-label", `${description.when}: ${description.detail}`);
    bar.onclick = () => selectBar(index);
    bar.onpointerenter = () => selectBar(index);
    bar.onfocus = () => selectBar(index);
    bar.onkeydown = (event) => {
      const target = { ArrowRight: index + 1, ArrowLeft: index - 1, Home: 0, End: bars.length - 1 }[event.key];
      if (target === undefined) return;
      event.preventDefault();
      bars[Math.max(0, Math.min(bars.length - 1, target))].focus();
    };
    bar.prediction = prediction;
    bars.push(bar);
    container.appendChild(bar);
  });

  const bestIndexes = points.map((p, i) => (inWindow(best, p) ? i : -1)).filter((i) => i >= 0);
  if (bestIndexes.length) {
    const bracket = document.createElement("div");
    bracket.classList.add("best-bracket");
    bracket.setAttribute("aria-hidden", "true");
    bracket.style.left = `${bestIndexes[0] / points.length * 100}%`;
    bracket.style.width = `${bestIndexes.length / points.length * 100}%`;
    const label = document.createElement("span");
    label.textContent = "Best time";
    bracket.appendChild(label);
    container.appendChild(bracket);
  }

  function selectBar(index) {
    bars.forEach((bar, i) => {
      bar.setAttribute("aria-pressed", String(i === index));
      // Roving tab stop: one bar in the tab order, arrow keys move between them.
      bar.tabIndex = i === index ? 0 : -1;
    });
    const prediction = bars[index].prediction;
    selectedBarTime = prediction.time;
    const description = barDescription(prediction, now);
    readout.replaceChildren();
    const when = document.createElement("strong");
    when.textContent = description.when;
    readout.append(when, ` · ${description.detail}`);
  }

  // Keep the reader's selection across the 30-second re-render.
  const kept = points.findIndex((p) => p.time === selectedBarTime);
  selectBar(kept >= 0 ? kept : 0);
}

// ------------------------------------------------------------- detail table

function renderForecastTable(predictions, now, best) {
  const tableBody = document.getElementById("forecast-rows");
  tableBody.replaceChildren();
  const showClosed = document.getElementById("show-closed-hours").checked;
  const visible = predictions.filter((prediction) =>
    showClosed || forecastStatus(prediction, now).status !== "closed");
  const empty = document.getElementById("forecast-empty");
  empty.hidden = visible.length > 0;
  empty.textContent = predictions.length
    ? "All forecast hours are closed. Select Show closed hours to view them."
    : "No forecast hours available for this location.";
  let previousDay = null;

  for (const prediction of visible) {
    const day = new Date(prediction.time).toLocaleDateString([], {
      timeZone: TIMEZONE, weekday: "long", month: "short", day: "numeric", year: "numeric",
    });
    if (day !== previousDay) {
      const divider = document.createElement("tr");
      divider.classList.add("day-divider");
      const heading = document.createElement("th");
      heading.colSpan = 4;
      heading.textContent = day;
      divider.appendChild(heading);
      tableBody.appendChild(divider);
      previousDay = day;
    }
    const row = document.createElement("tr");
    // Highlight the recommended window; its hours are all fully open and upcoming.
    if (inWindow(best, prediction) && isQuietHourCandidate(prediction, now)) {
      row.classList.add("quiet-hour");
    }
    for (const value of [formatClock(prediction.time), formatCount(prediction.predicted_count)]) {
      const cell = document.createElement("td");
      cell.textContent = value;
      row.appendChild(cell);
    }
    row.appendChild(createCapacityCell(prediction.predicted_percent));
    const availability = forecastStatus(prediction, now);
    const statusCell = document.createElement("td");
    statusCell.textContent = availability.label;
    statusCell.classList.add("availability-label");
    row.classList.add(`availability-${availability.status}`);
    row.appendChild(statusCell);
    tableBody.appendChild(row);
  }
}

function createCapacityCell(percent) {
  const cell = document.createElement("td");

  if (!Number.isFinite(percent)) {
    cell.textContent = "Unknown";
    return cell;
  }

  const label = document.createElement("span");
  label.textContent = formatPercent(percent);

  const track = document.createElement("div");
  track.classList.add("capacity-track");
  // The adjacent percentage already communicates the value to screen readers.
  track.setAttribute("aria-hidden", "true");

  const fill = document.createElement("div");
  fill.classList.add("capacity-fill", `level-${busyLevel(percent).key}`);
  // Keep the bar within its track while preserving the actual percentage label.
  fill.style.width = `${Math.min(100, Math.max(0, percent))}%`;

  track.appendChild(fill);
  cell.append(label, track);
  return cell;
}

// ------------------------------------------------------------------ formats

function formatTimestamp(value) {
  if (!value) return "Unknown";

  // Forecast origins use a space between the date and time.
  const date = new Date(value.replace(" ", "T"));

  if (Number.isNaN(date.getTime())) return "Unknown";

  return date.toLocaleString([], {
    timeZone: TIMEZONE,
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function formatClock(value) {
  return new Date(value).toLocaleTimeString([], {
    timeZone: TIMEZONE, hour: "numeric", minute: "2-digit",
  });
}

// Display only: the model's error is several people, so decimals overstate precision.
// Ranking and highlighting still use the unrounded values.
function formatCount(value) {
  return Number.isFinite(value) ? String(Math.round(value)) : "—";
}

function formatPercent(value) {
  return Number.isFinite(value) ? `${Math.round(value)}%` : "—";
}

// -------------------------------------------------------------- availability

function liveIsFresh(availability, now) {
  return availability?.status_source === "goboard_live"
    && Date.parse(availability.live_observed_at) <= now
    && now < Date.parse(availability.live_valid_until);
}

function forecastStatus(prediction, now) {
  const expiredLive = prediction.status_source === "goboard_live"
    && !liveIsFresh(prediction, now);
  const value = expiredLive ? prediction.scheduled_status : prediction.facility_status;
  const status = ["open", "closed", "partial", "unknown"].includes(value) ? value : "unknown";
  const source = expiredLive ? prediction.schedule_source : prediction.status_source;
  const labels = {
    open: "Scheduled open",
    closed: "Scheduled closed",
    partial: "Partially open",
    unknown: "Hours unknown",
  };
  let label = labels[status];
  if (status === "partial" && Number.isFinite(prediction.scheduled_open_minutes)) {
    label += ` (${prediction.scheduled_open_minutes} of 60 min)`;
  }
  if (source === "goboard_live" && ["open", "closed"].includes(status)) {
    label = `Reported ${status} now`;
  } else if (source === "schedule_override") {
    label += " (special hours)";
  }
  return { status, label };
}

function isQuietHourCandidate(prediction, now) {
  return isUpcoming(prediction, now)
    && prediction.facility_status === "open"
    && prediction.scheduled_status === "open"
    && forecastStatus(prediction, now).status === "open"
    && Number.isFinite(prediction.predicted_count);
}

function renderCurrentStatus(current) {
  const element = document.getElementById("current-availability");
  element.hidden = !current;
  element.textContent = "";
  element.setAttribute("data-state", "unknown");
  if (!current) return;

  if (current.live_is_fresh === true) {
    const labels = { open: "Open now", closed: "Closed now" };
    const label = labels[current.live_status] ?? "Live status unknown";
    if (labels[current.live_status]) element.setAttribute("data-state", current.live_status);
    const observed = new Date(current.live_observed_at);
    const time = current.live_observed_at && !Number.isNaN(observed.getTime())
      ? observed.toLocaleTimeString([], {
        timeZone: TIMEZONE, hour: "numeric", minute: "2-digit",
      }) : "unknown time";
    const source = current.status_source === "goboard_live" ? "Live GoBoard" : "Live";
    const conflict = current.schedule_conflict
      ? " · Live status differs from the published schedule" : "";
    element.textContent = `${label} · ${source} update at ${time}${conflict}`;
    return;
  }

  if (current.live_is_fresh !== false) {
    element.textContent = "Current status unavailable";
    return;
  }

  // A minute-level schedule snapshot cannot establish current status after time advances.
  const labels = { open: "scheduled open", closed: "scheduled closed", unknown: "hours unknown" };
  const label = labels[current.scheduled_status_now] ?? "hours unknown";
  element.textContent = `Status at ${formatTimestamp(current.evaluated_at)}: ${label}. No fresh live confirmation.`;
}

document.getElementById("show-closed-hours").onchange = renderSelectedLocation;

loadPredictions();

// Advance upcoming recommendations and chart timing every tick; re-read the API less often.
function tick() {
  if (Date.now() - lastFetchedAt >= REFETCH_MS) loadPredictions({ background: true });
  else renderSelectedLocation();
}
setInterval(tick, 30000);
// Phones suspend timers in background tabs, so check again when the page is shown.
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") tick();
});

// ------------------------------------------------------------------ accuracy

// Historical evaluation is independent of current forecasts and their availability.
let evaluationHistory = null;
let historyMessage = "Loading past accuracy…";
const HISTORY_HORIZON = 6;

async function loadEvaluationHistory() {
  try {
    const response = await fetch(`${API_BASE}/api/evaluation-history?horizon=6`, { method: "GET" });
    if (!response.ok) throw new Error(`History unavailable: HTTP ${response.status}`);
    const data = await response.json();
    if (data.evaluation_type !== "walk_forward" || !Array.isArray(data.rows)) {
      throw new Error("Invalid walk-forward history");
    }
    evaluationHistory = data;
  } catch (error) {
    console.error("Could not load evaluation history:", error);
    historyMessage = "Past accuracy is not available yet.";
  }
  renderEvaluationHistory(document.getElementById("location-select").value);
  // Quiet windows use this location's error, which is only known now.
  renderSelectedLocation();
}

function historyRows(locationName) {
  if (!evaluationHistory) return [];
  // Keep the horizon fixed. Never average predictions issued at different lead times.
  return evaluationHistory.rows.filter((row) =>
    row.location_name === locationName && row.horizon_hours === HISTORY_HORIZON
    && Number.isFinite(Date.parse(row.target_time))
    && Number.isFinite(row.actual_count) && Number.isFinite(row.predicted_count))
    .sort((a, b) => Date.parse(a.target_time) - Date.parse(b.target_time));
}

function locationMae(locationName) {
  const rows = historyRows(locationName);
  if (!rows.length) return null;
  return rows.reduce((total, row) =>
    total + Math.abs(row.predicted_count - row.actual_count), 0) / rows.length;
}

function renderEvaluationHistory(locationName) {
  const container = document.getElementById("history-chart");
  const headline = document.getElementById("history-headline");
  const summary = document.getElementById("history-summary");
  container.replaceChildren();
  document.getElementById("history-location").textContent = locationName || "Choose a location";
  summary.textContent = "";
  if (!evaluationHistory) {
    headline.textContent = historyMessage;
    return;
  }
  const rows = historyRows(locationName);
  if (!rows.length) {
    headline.textContent = "No past forecasts to compare for this location yet.";
    return;
  }
  const mae = locationMae(locationName);
  headline.replaceChildren("Typically within about ");
  const amount = document.createElement("span");
  amount.textContent = peopleText(Math.max(1, Math.round(mae)));
  headline.append(amount);
  const scope = evaluationHistory.config?.open_only ? "open hours" : "all hours";
  summary.textContent = `Forecasts made 6 hours ahead for this location were off by `
    + `${mae.toFixed(1)} people on average, across ${rows.length} past ${scope} `
    + `(${formatTimestamp(rows[0].target_time)} – ${formatTimestamp(rows[rows.length - 1].target_time)}).`;
  renderHistoryChart(container, rows);
}

function svgElement(tag, attributes, text) {
  const element = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [name, value] of Object.entries(attributes)) element.setAttribute(name, value);
  if (text !== undefined) element.textContent = text;
  return element;
}

function renderHistoryChart(container, rows) {
  const start = Date.parse(rows[0].target_time);
  const end = Date.parse(rows[rows.length - 1].target_time);
  const duration = Math.max(HOUR_MS, end - start);
  // Give each day room to read; long histories scroll rather than compress hourly peaks.
  const width = Math.max(900, Math.ceil(duration / 86400000) * 100);
  const left = 42, right = width - 24, top = 18, bottom = 190;
  const ceiling = Math.max(20, Math.ceil(Math.max(...rows.flatMap((r) =>
    [r.actual_count, r.predicted_count])) / 20) * 20);
  const floor = Math.min(0, Math.floor(Math.min(...rows.map((r) => r.predicted_count)) / 20) * 20);
  const x = (time) => left + (time - start) / duration * (right - left);
  const y = (count) => bottom - (count - floor) / (ceiling - floor) * (bottom - top);
  const svg = svgElement("svg", {
    viewBox: `0 0 ${width} 240`, role: "img",
    "aria-label": "Past forecasts made 6 hours ahead compared with actual occupancy. Gaps show hours excluded from the comparison.",
  });
  svg.style.width = `${width}px`;
  svg.style.minWidth = "100%";
  for (let tick = 0; tick <= 4; tick++) {
    const count = floor + (ceiling - floor) * tick / 4;
    svg.appendChild(svgElement("line", {
      x1: left, x2: right, y1: y(count), y2: y(count), class: "chart-grid",
    }));
    svg.appendChild(svgElement("text", {
      x: left - 10, y: y(count) + 4, "text-anchor": "end", class: "chart-axis-label",
    }, String(count)));
  }
  for (const [field, className, label] of [
    ["actual_count", "history-actual", "Actual people"],
    ["predicted_count", "history-predicted", "Forecast made 6 hours earlier"],
  ]) {
    let path = "", previous = null;
    for (const row of rows) {
      const time = Date.parse(row.target_time);
      // Missing scored hours and fold boundaries should not imply continuous evidence.
      const connected = previous && time - Date.parse(previous.target_time) === HOUR_MS
        && row.fold_cutoff === previous.fold_cutoff;
      path += `${connected ? "L" : "M"}${x(time)},${y(row[field])} `;
      const dot = svgElement("circle", {
        cx: x(time), cy: y(row[field]), r: 2, class: className,
      });
      dot.appendChild(svgElement("title", {},
        `${formatTimestamp(row.target_time)} · ${label}: ${formatCount(row[field])} people`));
      svg.appendChild(dot);
      previous = row;
    }
    svg.appendChild(svgElement("path", { d: path.trim(), class: `chart-line ${className}` }));
  }
  const tickCount = Math.max(1, Math.floor(width / 150));
  for (let tick = 0; tick <= tickCount; tick++) {
    const time = start + duration * tick / tickCount;
    svg.appendChild(svgElement("text", {
      x: x(time), y: bottom + 25,
      "text-anchor": tick === 0 ? "start" : tick === tickCount ? "end" : "middle",
      class: "chart-axis-label",
    }, formatTimestamp(new Date(time).toISOString())));
  }
  container.appendChild(svg);
}

loadEvaluationHistory();
