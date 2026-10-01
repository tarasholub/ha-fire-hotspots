# AI Coding Agents Guide

## Workflow

- Plan first. Agree on the plan with the maintainer before changing code.
- Keep replies concise. Ukrainian for discussion, English for code and commits.
- Run `scripts/lint` and `scripts/test` before finishing.
- Conventional commits.

## Project Overview

Home Assistant custom integration **Fire Hotspots** (`custom_components/fire_hotspots`). Polls the NASA FIRMS area API for a chosen country, assigns detections to regions using geoBoundaries polygons, and exposes per-region counters and safety binary sensors, a nearest-hotspot distance sensor, a new-hotspots event entity, and optional geo_location map markers.

### Code structure

- `api.py` — Home Assistant agnostic FIRMS client and CSV parser (VIIRS and MODIS); typed errors for auth, rate limit and connection.
- `boundaries.py` — Home Assistant agnostic geoBoundaries download (ADM1, falls back to ADM0) pinned to a commit, Douglas-Peucker simplification, compact JSON cache, point-in-polygon lookup, antimeridian-aware bboxes, Ukrainian region names and alphabet sorting.
- `countries.py` — ISO2 → ISO3 and en/uk country names; `EXCLUDED_COUNTRIES` (RU, BY) on principle.
- `helpers.py` — HA glue: cache dir `.storage/fire_hotspots/`, load-or-download boundaries.
- `coordinator.py` — `Settings` from options; `query_boxes` (one union box, two across the antimeridian); pure `process()` run in an executor (window, confidence, region, distance from HA home, counts, new detections grouped by region); seen ids persisted with `Store` and a scope fingerprint.
- `config_flow.py` — user (MAP_KEY + country) → boundaries download with progress → regions (checkboxes, "whole country" first); reauth; options with reload.
- `entity.py` — base entities, unique id scheme, stale entity cleanup helpers.
- `sensor.py`, `binary_sensor.py`, `event.py`, `geo_location.py` — platforms.
- `diagnostics.py` — redacts MAP_KEY, no home distances.

## Rules

- Never log or expose the MAP_KEY (it is part of the request URL).
- Keep `api.py` and `boundaries.py` free of Home Assistant imports.
- No I/O in entity properties; CPU-heavy geometry runs in an executor.
- Defaults must keep matching SaveEcoBot's counting (all sources, no confidence filter, no deduplication, 24 h).
- Occupied territories of Ukraine are part of Ukraine; do not add Russia or Belarus.
- Bump `SOURCE_COMMIT` and `CACHE_VERSION` together when updating boundaries.
