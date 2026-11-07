# NSW Trains Integration for Home Assistant

[![GitHub Release][releases-shield]][releases]
[![GitHub Activity][commits-shield]][commits]
[![License][license-shield]](LICENSE)
[![hacs][hacsbadge]][hacs]

A Home Assistant custom integration for NSW trains, using real-time data from the Transport for NSW Open Data Hub:

- **Station departures**: upcoming Sydney Trains, NSW TrainLink and Sydney Metro departures from any station
- **Line status**: on-time running, delays, alerts and trackwork for a line such as T4 or the South Coast Line
- **Trains passing home**: when the next train passes your house in each direction, and where the nearest train is

> This is an unofficial community project. It is not affiliated with or endorsed by Transport for NSW.

## Features

### Station departures

- **Next departures**: Up to 5 sensors per station for the next, second, third… departure, with real-time estimates
- **Departure details**: Line, destination, platform, scheduled and estimated time, minutes late and minutes until departure
- **Service alerts**: Trackwork, lift outages and other alerts affecting upcoming departures
- **Platform filter**: Only show departures from one platform, e.g. the one you take into the city
- **Metro toggle**: Include or exclude Sydney Metro services
- **Automatic updates**: Refreshes every minute

### Line status

- **Status**: Good service, trackwork, delays or disruption
- **On time**: Share of trains running right now that are on time (within 5 minutes, or 6 on intercity lines), with the late ones listed
- **Alerts**: The same alerts as the line's page on transportnsw.info (e.g. [T4](https://transportnsw.info/alerts/route/020T4)), plus upcoming trackwork
- **Next trackwork**: When the next planned trackwork starts

### Trains passing home

- **Next train in each direction**: Predicted time the next train passes your house, from live departure times at the stations either side
- **Train passing**: On while a train is going past
- **Nearest train**: Distance to the closest train by live GPS, shown on the map card

## Getting an API key

1. Sign up at the [Transport for NSW Open Data Hub](https://opendata.transport.nsw.gov.au/)
2. Create an application and give it access to the **Trip Planner APIs** and the **Public Transport - Realtime** APIs (vehicle positions and trip updates, for line status and trains passing home)
3. Copy the API key

The free tier allows far more requests than this integration makes. Network-wide feeds are fetched once and shared, however many lines you add.

## Installation

### HACS (Recommended)

1. Open HACS in your Home Assistant instance
2. Click the three dots menu (⋮) in the top right corner and select "Custom repositories"
3. Add this repository URL: `https://github.com/tmyroadctfig/nsw_trains` with category "Integration"
4. Find "NSW Trains" in HACS and click "Download"
5. Restart Home Assistant

### Manual Installation

1. Download the latest release from the [releases page][releases]
2. Copy the `custom_components/nsw_trains` folder to your Home Assistant `custom_components` directory
3. Restart Home Assistant

## Configuration

Go to **Settings** → **Devices & Services** → **Add Integration**, search for **NSW Trains**, and choose what to add. Add the integration again for each station, line or location; the API key is pre-filled after the first.

- **Departures from a station**: enter part of a station name, e.g. `Central`, then pick the station
- **Line status**: pick the line, e.g. South Coast Line
- **Trains passing home**: confirm the location (defaults to your Home Assistant home), then the stations on either side. The two nearest are suggested.

### Options

Click **Configure** on a station to change:

- **Number of departure sensors** (1–5, default 3)
- **Include Sydney Metro services** (default on)
- **Only show departures from platform**: a platform number such as `16`, or blank for all

Click **Configure** on trains passing home to change how close a train's GPS position must be to count as passing (default 300 m).

## Station sensors

For a station named "Central Station, Sydney":

| Entity | State |
| --- | --- |
| `sensor.central_station_sydney_next_departure` | Time the next service leaves (real-time estimate when available) |
| `sensor.central_station_sydney_departure_2`, `_3`… | Following departures |
| `sensor.central_station_sydney_service_alerts` | Number of alerts affecting upcoming departures |

Departure sensor attributes:

| Attribute | Example |
| --- | --- |
| `line` | `T1` |
| `line_name` | `T1 North Shore & Western Line` |
| `destination` | `Emu Plains` |
| `platform` | `16` |
| `scheduled` / `estimated` | ISO timestamps (`estimated` is `null` without real-time data) |
| `delay_minutes` | `2` (negative when early, `null` without real-time data) |
| `realtime` | `true` |
| `due_in_minutes` | `4` |

The alerts sensor has an `alerts` attribute listing each alert's `title`, `priority` and `url`.

Cancelled services are left out.

## Line status sensors

For the South Coast Line:

| Entity | State |
| --- | --- |
| `sensor.south_coast_line_status` | `good_service`, `trackwork`, `delays` or `disruption`; a `reason` attribute says why |
| `sensor.south_coast_line_on_time` | % of trains running now that are on time |
| `sensor.south_coast_line_alerts` | Number of alerts in effect now |
| `sensor.south_coast_line_next_trackwork` | When the next planned trackwork starts |

The status is the most serious that applies:

- **disruption**: a high priority alert is in effect
- **delays**: fewer than 80% of running trains are on time, or one is 15+ minutes late
- **trackwork**: trackwork is in effect somewhere on the line
- **good_service**: none of the above

The on time sensor's attributes list `late_trains` (e.g. `13:59 Kiama Station to Bondi Junction Station`, 8.9 minutes late), `trains_running`, `average_delay_minutes`, `max_delay_minutes` and `cancelled_trips` (cancelled trips on the line today). The alerts sensor lists `alerts` and `upcoming_trackwork`, each with `title`, `type`, `priority`, `start`, `end` and `url`.

Trackwork affects part of a line, so the status can show trackwork for work far from your stations; check the alert titles.

## Trains passing home sensors

For a house between Austinmer and Thirroul:

| Entity | State |
| --- | --- |
| `sensor.home_next_train_towards_austinmer` | When the next northbound train passes |
| `sensor.home_next_train_towards_thirroul` | When the next southbound train passes |
| `binary_sensor.home_train_passing` | On while a train is passing |
| `sensor.home_nearest_train` | Distance in metres to the closest train by GPS |

Pass times are interpolated between the train's live departure times at the two stations, based on how far along the line between them the house is. The train passing sensor turns on within a minute either side of a predicted pass, or when a GPS position under 90 seconds old is within the passing distance.

Limitations:

- Only trains that stop at both stations are predicted, so express trains and freight trains aren't included
- GPS positions are often a minute or two old, so the nearest train sensor shows roughly where trains are rather than exactly when one passes

## Automation Examples

### Running late

```yaml
automation:
  - alias: "Train running late"
    trigger:
      - platform: template
        value_template: "{{ state_attr('sensor.central_station_sydney_next_departure', 'delay_minutes') | int(0) >= 5 }}"
    action:
      - service: notify.mobile_app_your_phone
        data:
          message: >
            The {{ state_attr('sensor.central_station_sydney_next_departure', 'line') }} to
            {{ state_attr('sensor.central_station_sydney_next_departure', 'destination') }} is running
            {{ state_attr('sensor.central_station_sydney_next_departure', 'delay_minutes') }} minutes late
```

### Train going past

```yaml
automation:
  - alias: "Wave at the train"
    trigger:
      - platform: state
        entity_id: binary_sensor.home_train_passing
        to: "on"
    action:
      - service: light.turn_on
        target:
          entity_id: light.front_window
        data:
          flash: short
```

### Line status changes

```yaml
automation:
  - alias: "South Coast Line delays"
    trigger:
      - platform: state
        entity_id: sensor.south_coast_line_status
        to: ["delays", "disruption"]
    action:
      - service: notify.mobile_app_your_phone
        data:
          message: "South Coast Line: {{ state_attr('sensor.south_coast_line_status', 'reason') }}"
```

### Departure board card

```yaml
type: entities
title: Central
entities:
  - entity: sensor.central_station_sydney_next_departure
    format: relative
  - entity: sensor.central_station_sydney_departure_2
    format: relative
  - entity: sensor.central_station_sydney_departure_3
    format: relative
  - entity: sensor.central_station_sydney_service_alerts
```

## Data Source

Data comes from the Transport for NSW Trip Planner API (`departure_mon`, `stop_finder`, `coord` and `add_info`) and the Sydney Trains GTFS-realtime feeds (vehicle positions and trip updates), licensed under [CC BY 4.0](https://opendata.transport.nsw.gov.au/data-licence).

## Troubleshooting

If the API key stops working, Home Assistant shows a repair notice asking for a new one.

To enable debug logging:

```yaml
logger:
  default: warning
  logs:
    custom_components.nsw_trains: debug
```

## Development

```bash
pip install -r requirements-test.txt

# Unit tests
pytest -m unit

# Integration tests against the live API
TFNSW_API_KEY=... pytest -m integration

# Code quality
flake8 custom_components/ tests/
mypy custom_components/nsw_trains/
black custom_components/ tests/
isort custom_components/ tests/
```

## License

This project is licensed under the Apache License 2.0 - see the [LICENSE](LICENSE) file for details.

---

[releases-shield]: https://img.shields.io/github/release/tmyroadctfig/nsw_trains.svg?style=for-the-badge
[commits-shield]: https://img.shields.io/github/commit-activity/y/tmyroadctfig/nsw_trains.svg?style=for-the-badge
[license-shield]: https://img.shields.io/github/license/tmyroadctfig/nsw_trains.svg?style=for-the-badge&cacheSeconds=3600
[hacsbadge]: https://img.shields.io/badge/HACS-Custom-orange.svg?style=for-the-badge

[releases]: https://github.com/tmyroadctfig/nsw_trains/releases
[commits]: https://github.com/tmyroadctfig/nsw_trains/commits/main
[hacs]: https://github.com/hacs/integration
