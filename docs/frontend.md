# Frontend

Dependency-free HTML/CSS/JavaScript, served alongside the Flask API.
Redesigned October 19, 2026.

## Layout

Two cards lead: *Right now* (the forecast for the hour in progress, or the next
hour when none exists, plus the next three hours) and *Best time to go*.

The best time is a window: consecutive fully open upcoming hours forecast within
the location's 6-hour-ahead MAE of the quietest one (5% of capacity, min 2
people, until history loads), with alternatives only if they start within the
same band.

Crowd levels are percent of capacity: Quiet < 35%, Moderate < 65%, Busy
otherwise; windows spanning levels say so ("Quiet to moderate").

A 24-hour HTML bar chart (roving tab stop, arrow keys, tap/hover readout) shows
closed hours as hatched stubs and brackets the best window. The hour-by-hour
table is collapsed by default; the accuracy section leads with "Typically within
about N people".

Dark mode follows the system setting; colors pass WCAG AA (text 4.5:1, bars 3:1)
in both themes.

## Rules

Recommendations need scheduled and effective status fully open and a strictly
future start; data is never mutated; loading is GET-only with a 10-minute
background refetch.

The historical accuracy chart shows actuals versus fixed 6-hour walk-forward
predictions for the selected location, with series-specific MAE, scope and date
range. Unscored hours and fold boundaries remain chart gaps.

## Tests

- `node tests/test_frontend.js` covers the rules.
- `node tests/preview_frontend.cjs` (needs Chrome) checks real layout at
  1440/375/390/430 px.
- iOS Safari and screen readers are untested.