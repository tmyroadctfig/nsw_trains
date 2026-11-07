from datetime import timedelta
from typing import Final

DOMAIN: Final = "nsw_trains"

SHORT_ATTRIBUTION: Final = "Transport for NSW"
ATTRIBUTION: Final = "Data provided by Transport for NSW"
MODEL_NAME: Final = "Station"

CONF_API_KEY: Final = "api_key"
CONF_STOP_ID: Final = "stop_id"
CONF_STOP_NAME: Final = "stop_name"
CONF_SEARCH: Final = "search"

# Options
CONF_DEPARTURE_COUNT: Final = "departure_count"
CONF_INCLUDE_METRO: Final = "include_metro"
CONF_PLATFORM: Final = "platform"

DEFAULT_DEPARTURE_COUNT: Final = 3
MAX_DEPARTURE_COUNT: Final = 5
DEFAULT_INCLUDE_METRO: Final = True

UPDATE_INTERVAL: Final = timedelta(seconds=60)

HEADERS: Final = {"User-Agent": "home-assistant/tmyroadctfig/nsw_trains"}

# Trip Planner API product classes (modes of transport)
PRODUCT_CLASS_TRAIN: Final = 1
PRODUCT_CLASS_METRO: Final = 2
# Every non-rail class, excluded server side to keep departure responses small:
# light rail, bus, coach, ferry, school bus
NON_RAIL_PRODUCT_CLASSES: Final = (4, 5, 7, 9, 11)

# Config entry types. Entries created before types existed are stations.
CONF_ENTRY_TYPE: Final = "entry_type"
ENTRY_TYPE_STATION: Final = "station"
ENTRY_TYPE_LINE: Final = "line"
ENTRY_TYPE_HOME: Final = "home"

# Line status entries
CONF_LINE: Final = "line"

# Rail lines that can be tracked: code -> name. Codes match Trip Planner line names and alert line ids.
LINES: Final = {
    "T1": "T1 North Shore & Western Line",
    "T2": "T2 Leppington & Inner West Line",
    "T3": "T3 Liverpool & Inner West Line",
    "T4": "T4 Eastern Suburbs & Illawarra Line",
    "T5": "T5 Cumberland Line",
    "T6": "T6 Lidcombe & Bankstown Line",
    "T7": "T7 Olympic Park Line",
    "T8": "T8 Airport & South Line",
    "T9": "T9 Northern Line",
    "BMT": "Blue Mountains Line",
    "CCN": "Central Coast & Newcastle Line",
    "HUN": "Hunter Line",
    "SCO": "South Coast Line",
    "SHL": "Southern Highlands Line",
}

# Transport for NSW counts a train as on time within 5 minutes of the timetable (6 for intercity lines)
ON_TIME_MINUTES: Final = 5
INTERCITY_ON_TIME_MINUTES: Final = 6
INTERCITY_LINES: Final = frozenset({"BMT", "CCN", "HUN", "SCO", "SHL"})

LINE_STATUS_GOOD: Final = "good_service"
LINE_STATUS_TRACKWORK: Final = "trackwork"
LINE_STATUS_DELAYS: Final = "delays"
LINE_STATUS_DISRUPTION: Final = "disruption"
LINE_STATUSES: Final = [LINE_STATUS_GOOD, LINE_STATUS_TRACKWORK, LINE_STATUS_DELAYS, LINE_STATUS_DISRUPTION]

# Home (trains passing) entries
CONF_LATITUDE: Final = "latitude"
CONF_LONGITUDE: Final = "longitude"
CONF_STATION_A: Final = "station_a"
CONF_STATION_B: Final = "station_b"
CONF_PASSING_RADIUS: Final = "passing_radius"
DEFAULT_PASSING_RADIUS: Final = 300
NEARBY_STATION_RADIUS: Final = 8000

# How long each shared feed is reused before fetching again
ALERTS_MAX_AGE: Final = timedelta(minutes=5)
REALTIME_MAX_AGE: Final = timedelta(seconds=30)
HOME_UPDATE_INTERVAL: Final = timedelta(seconds=30)
# Ignore GPS positions older than this: parked trains can report positions that are hours old
VEHICLE_MAX_AGE: Final = timedelta(minutes=5)
# A GPS position must be this fresh to say a train is passing right now
PASSING_GPS_MAX_AGE: Final = timedelta(seconds=90)
# A predicted pass this close to now counts as the train passing
PASSING_WINDOW: Final = timedelta(seconds=60)
