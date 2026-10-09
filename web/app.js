let loadedData = null;

// Local development uses a separate Flask server; production proxies /api on this origin.
const API_BASE = ["localhost", "127.0.0.1"].includes(window.location.hostname)
  ? "http://127.0.0.1:5000" : "";

const EXPIRED_FORECAST =
  "Forecast is out of date. Refresh prediction data to see upcoming hours.";

const statusElement = document.getElementById("status");

async function loadPredictions() {
  statusElement.textContent = "Loading forecasts…";

  try {
    const response = await fetch(`${API_BASE}/api/predictions`, { method: "GET" });

    if (!response.ok) {
      throw new Error(`Request failed: HTTP ${response.status}`);
    }

    const data = await response.json();
    loadedData = data;
    const locations = Object.keys(data.locations ?? {});
    const previousLocation = document.getElementById("location-select").value;

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
      renderLocation(data, locationSelect.value);
    };

    enhanceLocationPicker(locations);
    renderLocation(data, locationSelect.value);

    statusElement.textContent =
      `Loaded forecasts for ${locations.length} locations.`;
  } catch (error) {
    console.error("Could not load forecasts:", error);
    statusElement.textContent =
      "Could not load forecasts. Check that the Flask API is running.";
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

function renderLocation(data, locationName, now = Date.now()) {
  // Preserve history scroll position during the current-forecast clock updates.
  if (document.getElementById("history-location").textContent !== locationName) {
    renderEvaluationHistory(locationName);
  }
  const predictions = data.locations[locationName];
  const tableBody = document.getElementById("forecast-rows");

  const heading = locationHeading(locationName);
  document.getElementById("location-facility").textContent = heading.facility;
  document.getElementById("forecast-heading").textContent = heading.name;

  const origin = data.forecast_origin?.[locationName];

  document.getElementById("forecast-origin").textContent = formatTimestamp(origin);

  document.getElementById("generated-at").textContent =
    formatTimestamp(data.generated_at);

  tableBody.replaceChildren();

  renderCurrentStatus(data.current_availability?.[locationName]);

  const candidates = predictions
    .filter((prediction) => isQuietHourCandidate(prediction, now))
    .sort((a, b) => a.predicted_count - b.predicted_count
      || Date.parse(a.time) - Date.parse(b.time));
  const counts = candidates.map((prediction) => prediction.predicted_count);
  renderForecastFreshness(data, predictions, now);
  renderBestTimes(candidates, predictions, now);
  renderForecastOverview(predictions, now, candidates[0]);
  renderForecastChart(predictions, now);

  const lowestCount = counts.length > 0 ? Math.min(...counts) : null;

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
    const date = new Date(prediction.time);
    const day = date.toLocaleDateString([], {
      timeZone: "America/New_York", weekday: "long", month: "short", day: "numeric", year: "numeric",
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

    if (isQuietHourCandidate(prediction, now) && prediction.predicted_count === lowestCount) {
      row.classList.add("quiet-hour");
    }

    const values = [
      new Date(prediction.time).toLocaleString([], {
        timeZone: "America/New_York",
        hour: "numeric",
        minute: "2-digit",
      }),
      prediction.predicted_count,
    ];

    for (const value of values) {
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

function isUpcoming(prediction, now) {
  return Date.parse(prediction.time) > now;
}

function forecastExpired(predictions, now) {
  const timestamps = predictions.map((p) => Date.parse(p.time)).filter(Number.isFinite);
  return timestamps.length > 0 && Math.max(...timestamps) <= now;
}

function renderForecastFreshness(data, predictions, now) {
  const element = document.getElementById("forecast-freshness");
  element.setAttribute("data-expired", String(forecastExpired(predictions, now)));
  if (forecastExpired(predictions, now)) {
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
    ? `Generated ${ageMinutes < 60 ? `${ageMinutes} min` : `${Math.floor(ageMinutes / 60)} hr`} ago`
    : "Generation time unavailable or ahead of the browser clock.";
}

function renderBestTimes(candidates, predictions, now) {
  const best = candidates[0];
  const hasPercent = Number.isFinite(best?.predicted_percent);
  document.getElementById("recommended-time").textContent = best
    ? formatTimestamp(best.time)
    : forecastExpired(predictions, now) ? EXPIRED_FORECAST
      : "No upcoming fully open hours with an occupancy estimate are available.";
  document.getElementById("recommended-values").hidden = !best;
  document.getElementById("recommended-explanation").hidden = !best;
  document.getElementById("recommended-count").textContent = best?.predicted_count ?? "—";
  document.getElementById("recommended-percent").textContent = hasPercent
    ? `${best.predicted_percent}%` : "—";
  document.getElementById("recommended-capacity-track").hidden = !hasPercent;
  document.getElementById("recommended-capacity-fill").style.width =
    `${hasPercent ? Math.max(0, Math.min(100, best.predicted_percent)) : 0}%`;
  document.getElementById("recommended-percent-note").textContent = best && !hasPercent
    ? "Capacity estimate unavailable" : "";

  const summary = document.getElementById("quiet-hours-summary");
  summary.replaceChildren();
  if (candidates.length < 2) {
    const empty = document.createElement("li");
    empty.classList.add("empty-state");
    empty.textContent = forecastExpired(predictions, now) ? EXPIRED_FORECAST
      : best ? "No other fully open upcoming hours to compare."
        : "No other quiet hours are available in this forecast.";
    summary.appendChild(empty);
    return;
  }
  // The first ranked hour is featured above; retain the next two eligible alternatives.
  for (const prediction of candidates.slice(1, 3)) {
    const item = document.createElement("li");
    const time = document.createElement("strong");
    time.textContent = formatTimestamp(prediction.time);
    const count = document.createElement("span");
    count.textContent = `${prediction.predicted_count} people expected`;
    const percent = document.createElement("span");
    percent.textContent = Number.isFinite(prediction.predicted_percent)
      ? `${prediction.predicted_percent}% capacity used` : "Capacity estimate unavailable";
    item.append(time, count, percent);
    summary.appendChild(item);
  }
}

function renderForecastOverview(predictions, now, quietest = null) {
  // Select a copy so summary ordering never changes the chart/table data.
  const next = predictions
    .filter((prediction) => isUpcoming(prediction, now) && prediction.facility_status === "open")
    .sort((a, b) => Date.parse(a.time) - Date.parse(b.time))[0];
  const sameHour = Boolean(next && quietest && Date.parse(next.time) === Date.parse(quietest.time));
  document.getElementById("next-values").hidden = !next || sameHour;
  document.getElementById("next-recommendation-note").hidden = !sameHour;
  document.getElementById("next-recommendation-note").textContent = sameHour
    ? "Also the quietest hour recommended above." : "";
  document.getElementById("next-count").textContent =
    Number.isFinite(next?.predicted_count) ? next.predicted_count : "—";
  document.getElementById("next-percent").textContent =
    Number.isFinite(next?.predicted_percent) ? `${next.predicted_percent}%` : "—";
  const percent = next?.predicted_percent;
  const hasPercent = Number.isFinite(percent);
  document.getElementById("next-capacity-track").hidden = !hasPercent;
  document.getElementById("next-capacity-fill").style.width =
    `${hasPercent ? Math.max(0, Math.min(100, percent)) : 0}%`;
  document.getElementById("next-percent-note").textContent = next && !hasPercent
    ? "Capacity estimate unavailable" : "";
  document.getElementById("next-time").textContent = next
    ? formatTimestamp(next.time)
    : forecastExpired(predictions, now) ? EXPIRED_FORECAST
      : "No fully open upcoming hours in this forecast";
  document.getElementById("next-availability").textContent = next
    ? forecastStatus(next, now).label : "—";
}

function svgElement(tag, attributes, text) {
  const element = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [name, value] of Object.entries(attributes)) element.setAttribute(name, value);
  if (text !== undefined) element.textContent = text;
  return element;
}

function updateForecastScrollHint() {
  const chart = document.getElementById("forecast-chart");
  document.getElementById("forecast-scroll-hint").hidden = !(
    window.innerWidth <= 540 && chart.querySelector("svg")
    && chart.clientWidth > 0 && chart.scrollWidth > chart.clientWidth + 1
  );
}

window.addEventListener("resize", updateForecastScrollHint);
// Observe container changes as well as viewport resizes (including embedded layouts).
if (typeof ResizeObserver !== "undefined") {
  new ResizeObserver(updateForecastScrollHint).observe(document.getElementById("forecast-chart"));
}

function renderForecastChart(predictions, now) {
  const container = document.getElementById("forecast-chart");
  container.replaceChildren();
  const points = predictions
    .filter((p) => Number.isFinite(Date.parse(p.time)))
    .slice().sort((a, b) => Date.parse(a.time) - Date.parse(b.time));
  const counts = points.map((p) => p.predicted_count).filter(Number.isFinite);
  if (!counts.length) {
    const empty = document.createElement("p");
    empty.classList.add("empty-state");
    empty.textContent = "No occupancy forecast available to chart.";
    container.appendChild(empty);
    updateForecastScrollHint();
    return;
  }

  // Fixed SVG coordinates scale with the container. X positions use elapsed time,
  // not row numbers, so missing hours remain gaps on the timeline.
  const width = 900;
  const left = 42;
  const right = 874;
  const top = 18;
  const bottom = 190;
  const start = Date.parse(points[0].time);
  const end = Date.parse(points[points.length - 1].time);
  const duration = end - start + 3600000;
  const ceiling = Math.max(20, Math.ceil(Math.max(...counts) / 20) * 20);
  const x = (stamp) => left + (stamp - start) / duration * (right - left);
  const y = (count) => bottom - count / ceiling * (bottom - top);
  const svg = svgElement("svg", {
    viewBox: `0 0 ${width} 240`, role: "img",
    "aria-label": "Predicted occupancy across all forecast hours, including closed hours. Exact values and availability are in the table below.",
  });

  for (const p of points) {
    const status = forecastStatus(p, now).status;
    if (status !== "closed" && status !== "partial") continue;
    const hourStart = Date.parse(p.time);
    svg.appendChild(svgElement("rect", {
      x: x(hourStart), y: top,
      width: x(hourStart + 3600000) - x(hourStart),
      height: bottom - top, class: `chart-${status}-band`,
    }));
  }
  for (let tick = 0; tick <= 4; tick++) {
    const count = ceiling * tick / 4;
    svg.appendChild(svgElement("line", { x1: left, x2: right, y1: y(count), y2: y(count), class: "chart-grid" }));
    svg.appendChild(svgElement("text", { x: left - 10, y: y(count) + 4, "text-anchor": "end", class: "chart-axis-label" }, String(count)));
  }

  let path = "";
  let previousTime = null;
  for (const p of points) {
    if (!Number.isFinite(p.predicted_count)) {
      previousTime = null;
      continue;
    }
    const stamp = Date.parse(p.time);
    const connected = previousTime !== null && stamp - previousTime <= 3600000;
    path += `${connected ? "L" : "M"}${x(stamp)},${y(p.predicted_count)} `;
    previousTime = stamp;
  }
  svg.appendChild(svgElement("path", { d: path.trim(), class: "chart-line" }));
  for (const p of points) {
    if (!Number.isFinite(p.predicted_count)) continue;
    const dot = svgElement("circle", {
      cx: x(Date.parse(p.time)), cy: y(p.predicted_count), r: 3,
      class: "chart-point",
    });
    dot.appendChild(svgElement("title", {},
      `${formatTimestamp(p.time)}: ${p.predicted_count} people predicted · ${forecastStatus(p, now).label}`));
    svg.appendChild(dot);
  }

  if (now >= start && now <= start + duration) {
    svg.appendChild(svgElement("line", {
      x1: x(now), x2: x(now), y1: top, y2: bottom, class: "chart-now",
    }));
    svg.appendChild(svgElement("text", {
      x: x(now), y: top - 6, "text-anchor": "middle", class: "chart-axis-label",
    }, "Now"));
  }

  const ticks = [...new Set([0, Math.round((points.length - 1) / 3),
    Math.round(2 * (points.length - 1) / 3), points.length - 1])];
  for (const index of ticks) {
    const p = points[index];
    const date = new Date(p.time);
    const anchor = index === 0 ? "start" : index === points.length - 1 ? "end" : "middle";
    const attrs = { x: x(Date.parse(p.time)), "text-anchor": anchor, class: "chart-axis-label" };
    svg.appendChild(svgElement("text", { ...attrs, y: bottom + 22 }, date.toLocaleTimeString([], {
      timeZone: "America/New_York", hour: "numeric", minute: "2-digit",
    })));
    svg.appendChild(svgElement("text", { ...attrs, y: bottom + 39 }, date.toLocaleDateString([], {
      timeZone: "America/New_York", month: "short", day: "numeric",
    })));
  }
  container.appendChild(svg);
  updateForecastScrollHint();
}

function createCapacityCell(percent) {
  const cell = document.createElement("td");

  if (!Number.isFinite(percent)) {
    cell.textContent = "Unknown";
    return cell;
  }

  const label = document.createElement("span");
  label.textContent = `${percent}%`;

  const track = document.createElement("div");
  track.classList.add("capacity-track");
  // The adjacent percentage already communicates the value to screen readers.
  track.setAttribute("aria-hidden", "true");

  const fill = document.createElement("div");
  fill.classList.add("capacity-fill");
  // Keep the bar within its track while preserving the actual percentage label.
  fill.style.width = `${Math.min(100, Math.max(0, percent))}%`;

  track.appendChild(fill);
  cell.append(label, track);
  return cell;
}

function formatTimestamp(value) {
  if (!value) return "Unknown";

  // Forecast origins use a space between the date and time.
  const date = new Date(value.replace(" ", "T"));

  if (Number.isNaN(date.getTime())) return "Unknown";

  return date.toLocaleString([], {
    timeZone: "America/New_York",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

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
  if (!current) return;

  if (current.live_is_fresh === true) {
    const labels = { open: "Open now", closed: "Closed now" };
    const label = labels[current.live_status] ?? "Live status unknown";
    const observed = new Date(current.live_observed_at);
    const time = current.live_observed_at && !Number.isNaN(observed.getTime())
      ? observed.toLocaleTimeString([], {
        timeZone: "America/New_York", hour: "numeric", minute: "2-digit",
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

function renderSelectedLocation() {
  const location = document.getElementById("location-select").value;
  if (loadedData?.locations?.[location]) renderLocation(loadedData, location);
}

document.getElementById("show-closed-hours").onchange = renderSelectedLocation;

loadPredictions();

// Advance upcoming recommendations and chart timing without fetching or refreshing data.
// Current-status freshness still comes from the backend snapshot.
setInterval(renderSelectedLocation, 30000);

// Historical evaluation is independent of current forecasts and their availability.
let evaluationHistory = null;
let historyMessage = "Loading past prediction accuracy…";
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
    historyMessage = "Past prediction accuracy is not available yet.";
  }
  renderEvaluationHistory(document.getElementById("location-select").value);
}

function renderEvaluationHistory(locationName) {
  const container = document.getElementById("history-chart");
  const summary = document.getElementById("history-summary");
  container.replaceChildren();
  document.getElementById("history-location").textContent = locationName || "Choose a location";
  if (!evaluationHistory) {
    summary.textContent = historyMessage;
    return;
  }
  // Keep the horizon fixed. Never average predictions issued at different lead times.
  const rows = evaluationHistory.rows.filter((row) =>
    row.location_name === locationName && row.horizon_hours === HISTORY_HORIZON
    && Number.isFinite(Date.parse(row.target_time))
    && Number.isFinite(row.actual_count) && Number.isFinite(row.predicted_count))
    .sort((a, b) => Date.parse(a.target_time) - Date.parse(b.target_time));
  if (!rows.length) {
    summary.textContent = "No past predictions made 6 hours ahead are available for this location.";
    return;
  }
  const mae = rows.reduce((total, row) =>
    total + Math.abs(row.predicted_count - row.actual_count), 0) / rows.length;
  const scope = evaluationHistory.config?.open_only
    ? "Historical test during open hours (including hours with unknown status)"
    : "Historical test across all hours";
  summary.textContent = `Average prediction error: ${mae.toFixed(2)} people (MAE) · ${rows.length} compared hours · ${formatTimestamp(rows[0].target_time)} – ${formatTimestamp(rows[rows.length - 1].target_time)} · ${scope}. Only hours compared across all methods are included.`;
  renderHistoryChart(container, rows);
}

function renderHistoryChart(container, rows) {
  const start = Date.parse(rows[0].target_time);
  const end = Date.parse(rows[rows.length - 1].target_time);
  const duration = Math.max(3600000, end - start);
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
    "aria-label": "Past predictions vs. actual occupancy, forecasts made 6 hours ahead. Gaps show hours excluded from the comparison.",
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
    ["actual_count", "history-actual", "Actual observed occupancy"],
    ["predicted_count", "history-predicted", "Prediction made 6 hours ahead"],
  ]) {
    let path = "", previous = null;
    for (const row of rows) {
      const time = Date.parse(row.target_time);
      // Missing scored hours and fold boundaries should not imply continuous evidence.
      const connected = previous && time - Date.parse(previous.target_time) === 3600000
        && row.fold_cutoff === previous.fold_cutoff;
      path += `${connected ? "L" : "M"}${x(time)},${y(row[field])} `;
      const dot = svgElement("circle", {
        cx: x(time), cy: y(row[field]), r: 2, class: className,
      });
      dot.appendChild(svgElement("title", {},
        `${formatTimestamp(row.target_time)} · ${label}: ${row[field]} people`));
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
