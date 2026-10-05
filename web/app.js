let loadedData = null;

const statusElement = document.getElementById("status");

async function loadPredictions() {
  statusElement.textContent = "Loading forecasts…";

  try {
    const response = await fetch("http://localhost:5000/api/predictions");

    if (!response.ok) {
      throw new Error(`Request failed: HTTP ${response.status}`);
    }

    const data = await response.json();
    loadedData = data;
    const locations = Object.keys(data.locations ?? {});

    if (locations.length === 0) {
      statusElement.textContent = "No locations available yet.";
      return;
    }

    console.log("GymCast predictions:", data);

    const locationSelect = document.getElementById("location-select");

    locationSelect.replaceChildren();

    for (const locationName of locations) {
      const option = document.createElement("option");
      option.value = locationName;
      option.textContent = locationName;
      locationSelect.appendChild(option);
    }

    locationSelect.disabled = false;

    locationSelect.onchange = () => {
      renderLocation(data, locationSelect.value);
    };

    renderLocation(data, locationSelect.value);

    statusElement.textContent =
      `Loaded forecasts for ${locations.length} locations.`;
  } catch (error) {
    console.error("Could not load forecasts:", error);
    statusElement.textContent =
      "Could not load forecasts. Check that the Flask API is running.";
  }
}

function renderLocation(data, locationName, now = Date.now()) {
  const predictions = data.locations[locationName];
  const tableBody = document.getElementById("forecast-rows");

  document.getElementById("forecast-heading").textContent =
    `${locationName} — hourly forecast`;

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
  const summary = document.getElementById("quiet-hours-summary");
  summary.textContent = candidates.length
    ? "Quietest fully open hours: " + candidates.slice(0, 3)
      .map((prediction) => `${formatTimestamp(prediction.time)} — ${prediction.predicted_count} people`)
      .join(" · ")
    : "No fully scheduled-open hours are available for a quietest-time comparison.";

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
  return prediction.scheduled_status === "open"
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

// Recheck forecast-row live expiry; current-status freshness comes from the backend snapshot.
setInterval(renderSelectedLocation, 30000);
