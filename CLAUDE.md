# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## NSW Trains — Developer Notes

This is a Home Assistant custom integration (HACS-compatible) that polls Transport for NSW APIs. Three config entry types (`entry_type` in entry data; entries without it are stations): **station** (departures and alerts), **line** (status, on-time %, alerts, trackwork) and **home** (predicted times trains pass a location, nearest train by GPS, a "train passing" binary sensor). Structure and tooling mirror the sibling `nsw_air_quality` project.

## Commands

A local venv lives in `.venv` (Python 3.12; HA itself is not installed, see the conftest shim below).

```bash
# Run unit tests (no network required)
.venv/bin/python -m pytest -m unit

# Run a single test file or test function
.venv/bin/python -m pytest tests/test_api.py
.venv/bin/python -m pytest tests/test_api.py::test_departure_fields

# Coverage
.venv/bin/python -m pytest -m unit --cov=custom_components/nsw_trains --cov-report=term-missing

# Integration tests (live API, skipped unless TFNSW_API_KEY is set — in the environment or in the gitignored .env,
# which tests/conftest.py loads; .env.example is the template)
TFNSW_API_KEY=... .venv/bin/python -m pytest -m integration --timeout=60

# Lint / type check / format
.venv/bin/python -m flake8 custom_components/ tests/
.venv/bin/python -m mypy custom_components/nsw_trains/
.venv/bin/python -m black custom_components/ tests/
.venv/bin/python -m isort custom_components/ tests/
```

## Working Principles

- **Leave things better than you found them.** When touching an area, fix nearby small issues and add missing tests for code you're relying on, as long as it stays within the scope of the change.
- **Every behaviour change comes with tests.** Use TDD: write a failing test, confirm it fails, make the minimal change, run the full unit suite.

Mark each test with `@pytest.mark.unit` (no external calls) or `@pytest.mark.integration` (calls the live API). CI runs unit tests on PRs; integration tests run on push to main using the `TFNSW_API_KEY` repository secret.

## Architecture

**Config Flow → Coordinator → Entities**. `__init__.py` picks the coordinator from `COORDINATORS` by entry type and forwards to both `sensor` and `binary_sensor` (the binary sensor platform only adds entities for home entries).

1. `api.py` — `TransportNswClient` for all endpoints. Auth header `Authorization: apikey <key>`; 401/403 → `InvalidApiKey`, other failures (including undecodable protobuf) → `TransportNswError`. Pure parsers return frozen dataclasses:
   - `parse_stops` (`stop_finder`), `parse_nearby_stations` (`coord`), `parse_departures` (`departure_mon`), `parse_line_alerts` (`add_info`).
   - Departures are filtered to product classes 1 (train) and 2 (metro), cancelled ones dropped, sorted by real-time estimate. `Departure.trip_id` is `properties.RealtimeTripId`, which equals the GTFS-realtime trip id and is the same at every station.
   - `add_info` can't filter by line server side (only `filterMOTType=1`), so all train alerts are fetched (~160 KB gzipped) and filtered by line code. Line ids look like `nsw:020T4: :H:` / `nsw:02SCO: :R:` → `alert_line_code` gives `T4` / `SCO`; these match transportnsw.info route URLs (`/alerts/route/020T4`).
2. `gtfs.py` — decodes the Sydney Trains GTFS-realtime feeds (`/v2/gtfs/vehiclepos/sydneytrains`, `/v2/gtfs/realtime/sydneytrains`; these include intercity lines like SCO). `ROUTE_PREFIX_LINES` maps GTFS route id prefixes to line codes (from static `routes.txt`: `ESI_*`→T4, `SCO_*`→SCO, `NSN_*`/`WST_*`→T1 …); `RTTA_*` (out of service) is excluded. Trip updates carry only delays, no absolute times, and list remaining stops, so a trip's delay is the first stop delay.
3. `line_status.py` — `compute_line_status`: running trains = fresh (≤5 min) GPS position + live delay; on time = delay < 5 min (6 for intercity). Status precedence: disruption (active high/veryHigh alert) > delays (<80% on time or a train 15+ min late) > trackwork (active trackwork alert) > good_service. `tripMessage` alerts ("Running late", some open since 2024) are excluded.
4. `passing.py` — `fraction_along` projects the home onto the straight line between the two stations; `predict_passes` matches trips by id across both stations' departures and interpolates the pass time; `nearest_train` ignores stale GPS.
5. `coordinator.py` — `NswTrainsCoordinator` (station, 60s), `LineCoordinator` (60s), `HomeCoordinator` (30s). Network-wide feeds go through `feeds.FeedCache` (one per HA instance in `hass.data[DOMAIN]`) so multiple entries share fetches: alerts 5 min, vehicles/delays 30s, home station departures 60s. Home departures are requested from 15 min ago, because `departure_mon` drops trains that have left, and a train between the stations has left one of them.
6. `sensor.py`, `binary_sensor.py`, `entity.py` — entities per entry type, one service device per station/line/home. `TrainPassingSensor` re-evaluates every 10s locally since a predicted pass arrives without new data.
7. `config_flow.py` — menu (`station_search` / `line` / `home`). Home: location defaults to `hass.config`, nearest two stations suggested. Unique ids: stop id, `line_<code>`, `home_<sorted stop ids>`. Options: station (count, metro, platform) and home (GPS passing radius); lines have none. `reauth_confirm` for rejected keys.

Product class codes: 1 train, 2 metro, 4 light rail, 5 bus, 7 coach, 9 ferry, 11 school bus.

Response quirks, verified against the live API: `stop_finder` lists a stop's product classes under `modes`; `coord` uses `productClasses` and names everything "undefined, undefined" (real name in `properties.STOP_NAME_WITH_PLACE`); a stop event's platform is `location.properties.platformName`; cancellation is `TRIP_CANCELLED` in `realtimeStatus`; GPS positions are typically ~2 min old and parked trains report positions hours old. Stop IDs: Central `200060`, Austinmer `251540`, Thirroul `251550`, Coledale `251530`.

### conftest.py shim

`tests/conftest.py` injects a stub `homeassistant` package when the real one isn't installed, with minimal stand-ins for `ConfigFlow`/`OptionsFlow` (including `async_show_menu`), `DataUpdateCoordinator`/`CoordinatorEntity`, `SensorEntity`, `BinarySensorEntity`, `UpdateFailed`, `ConfigEntryAuthFailed` and `AbortFlow`. It also loads the gitignored `.env`. Stub entities don't implement HA's `name`/`unique_id` properties, so tests assert on `_attr_*`. If a test fails because a newly imported HA symbol is missing from the stub, add it there. API response fixtures live in `tests/fixtures/`; GTFS-realtime feeds are built in code with `tests/helpers.py` (`vehicle_feed`, `trip_update_feed`).

## Code Style

- Line length: 127 characters (Black + flake8).
- Python 3.12+, type hints throughout.
- isort configured for Black compatibility (known first-party: `custom_components`).
