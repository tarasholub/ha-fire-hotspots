# 🔥 Fire Hotspots for Home Assistant

[Українська](README.md)

Satellite fire hotspots for a country and its regions in Home Assistant:
counters, "fire in region" sensors, distance to the nearest hotspot, events for
automations and optional map markers.

Data: [NASA FIRMS](https://firms.modaps.eosdis.nasa.gov/) (VIIRS and MODIS).
Boundaries: [geoBoundaries](https://www.geoboundaries.org/).

> [!WARNING]
> Independent community project, not affiliated with NASA or geoBoundaries.
> Satellites detect thermal anomalies, not confirmed fires, with a delay of up
> to a few hours. Do not rely on this integration as your only safety source.

## Entities

Per selected region (and "Whole country" if selected):

- `sensor` **Hotspots: _region_** — hotspots within the time window; attributes
  `frp_sum` (MW) and `latest_acquired`.
- `binary_sensor` (safety) **Fire: _region_** — unsafe while there is at least one.

Per country:

- `sensor` **Nearest hotspot** (km) — from Home Assistant home to the nearest monitored hotspot.
- `sensor` **Last detection** — acquisition time of the newest monitored hotspot.
- `sensor` **Total fire radiative power** (MW) — summed FRP of all monitored hotspots.
- `event` **New hotspots** — `detected` once per region with new hotspots; attributes `region`, `region_name`, `count`, `detections` (up to 50, nearest first).
- `geo_location` — one map marker per hotspot (off by default).

## Installation

HACS → Custom repositories → `https://github.com/tarasholub/ha-fire-hotspots`
(Integration), install **Fire Hotspots**, restart. Or copy
`custom_components/fire_hotspots` into `config/custom_components/`.

## Configuration

1. Get a free MAP_KEY: <https://firms.modaps.eosdis.nasa.gov/api/map_key/>.
2. Add the **Fire Hotspots** integration, enter the key and pick a country.
   Boundaries are downloaded once and cached in `.storage/fire_hotspots/`.
3. Tick regions and/or "Whole country".

Options: regions, time window (1–96 h, default 24), minimum confidence
(default low), satellite sources (default all four), update interval
(10–180 min, default 30), show on map (default off). The time window,
confidence, interval and map toggle are also exposed as configuration
entities on the device page.

## Automations and map

A ready-made blueprint sends a mobile notification about new hotspots with an
optional distance-from-home filter:

[![Import blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Ftarasholub%2Fha-fire-hotspots%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Ffire_hotspots%2Fnew_hotspots_notify.yaml)

With "Show on map" enabled, a map card shows every hotspot:

```yaml
type: map
geo_location_sources:
  - fire_hotspots
auto_fit: true
```

## Counting

Polls the FIRMS Area API every 30 minutes (configurable), one request per
source; force a refresh with the `homeassistant.update_entity` action. Each
detection is assigned to a region by polygon; detections outside the country
are dropped. Defaults reproduce [SaveEcoBot](https://www.saveecobot.com/analytics/fires)'s
counts (all sources, no confidence filter, no cross-satellite deduplication,
rolling 24 h). "Whole country" is the sum of its regions. Occupied territories
of Ukraine are counted within Ukraine.

Russia and Belarus are not supported as aggressor states in the war against
Ukraine.

## Attribution and licenses

- Code: [MIT](LICENSE).
- Fire data: NASA FIRMS / LANCE. We acknowledge the use of data and/or imagery
  from NASA's Fire Information for Resource Management System (FIRMS), part of
  NASA's Earth Science Data and Information System (ESDIS).
- Boundaries: geoBoundaries (gbOpen), Runfola et al. (2020), *PLoS ONE* 15(4):
  e0231866. Not bundled; each installation downloads them under the license of
  the respective country dataset.
- "NASA" is used only to identify the data source and does not imply
  endorsement.
