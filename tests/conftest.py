"""Test configuration and fixtures."""

import json
import os
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

# Add the custom_components directory to the Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

FIXTURES = Path(__file__).parent / "fixtures"
ENV_FILE = Path(__file__).parent.parent / ".env"


def load_env_file(path: Path) -> None:
    """Load KEY=value lines from a local, gitignored .env file. Real environment variables take precedence."""
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip("'\"")
        if value:
            os.environ.setdefault(key.strip(), value)


load_env_file(ENV_FILE)


def load_fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def departures_json() -> Any:
    return load_fixture("departure_mon.json")


@pytest.fixture
def stop_finder_json() -> Any:
    return load_fixture("stop_finder.json")


# Minimal stand-ins for HA classes, used by the stub below so flows, coordinators and
# entities can be unit tested without a full HA install.
class _AbortFlow(Exception):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


class _StubFlow:
    hass = None
    handler = None

    def async_create_entry(self, title="", data=None):
        return {"type": "create_entry", "title": title, "data": data or {}}

    def async_show_form(self, step_id, data_schema=None, errors=None):
        return {"type": "form", "step_id": step_id, "data_schema": data_schema, "errors": errors or {}}

    def async_abort(self, reason):
        return {"type": "abort", "reason": reason}

    def async_show_menu(self, step_id, menu_options):
        return {"type": "menu", "step_id": step_id, "menu_options": menu_options}


class _StubConfigFlow(_StubFlow):
    def __init_subclass__(cls, domain=None, **kwargs):
        super().__init_subclass__(**kwargs)

    current_entries: list = []
    unique_id = None
    reauth_entry = None

    def _async_current_entries(self):
        return self.current_entries

    async def async_set_unique_id(self, unique_id):
        self.unique_id = unique_id

    def _abort_if_unique_id_configured(self):
        for entry in self._async_current_entries():
            if entry.unique_id == self.unique_id:
                raise _AbortFlow("already_configured")

    def _get_reauth_entry(self):
        return self.reauth_entry

    def async_update_reload_and_abort(self, entry, data_updates=None):
        entry.data = {**entry.data, **(data_updates or {})}
        return {"type": "abort", "reason": "reauth_successful"}

    @staticmethod
    def async_get_options_flow(config_entry):
        raise NotImplementedError


class _StubOptionsFlow(_StubFlow):
    @property
    def config_entry(self):
        # Mirrors HA: the entry is looked up by the flow's handler (the entry id)
        return self.hass.config_entries.async_get_known_entry(self.handler)


class _StubCoordinator:
    def __init__(self, hass, logger, *, name, update_interval=None, config_entry=None):
        self.hass = hass
        self.name = name
        self.update_interval = update_interval
        self.config_entry = config_entry
        self.data = None

    def __class_getitem__(cls, item):
        return cls

    async def async_refresh(self):
        self.data = await self._async_update_data()


class _StubCoordinatorEntity:
    hass = None

    def __init__(self, coordinator):
        self.coordinator = coordinator
        self.removers = []
        self.state_writes = 0

    def __class_getitem__(cls, item):
        return cls

    async def async_added_to_hass(self):
        pass

    def async_on_remove(self, remover):
        self.removers.append(remover)

    def async_write_ha_state(self):
        self.state_writes += 1


class _StubSensorEntity:
    pass


class _StubBinarySensorEntity:
    pass


class _UpdateFailed(Exception):
    pass


class _ConfigEntryAuthFailed(Exception):
    pass


# Provide a lightweight homeassistant stub when the full package is unavailable.
# In CI the real package is installed; this fallback enables local development
# without the heavy HA dependency tree.
try:
    import homeassistant  # noqa: F401

    HA_STUBBED = False
except ImportError:
    HA_STUBBED = True
    _ha = MagicMock()
    _ha.core.callback = lambda f: f

    _ha.config_entries.ConfigFlow = _StubConfigFlow
    _ha.config_entries.OptionsFlow = _StubOptionsFlow
    _ha.data_entry_flow.AbortFlow = _AbortFlow
    _ha.components.sensor.SensorEntity = _StubSensorEntity
    _ha.components.sensor.SensorDeviceClass.TIMESTAMP = "timestamp"
    _ha.components.sensor.SensorDeviceClass.ENUM = "enum"
    _ha.components.sensor.SensorDeviceClass.DISTANCE = "distance"
    _ha.components.binary_sensor.BinarySensorEntity = _StubBinarySensorEntity
    _ha.helpers.update_coordinator.DataUpdateCoordinator = _StubCoordinator
    _ha.helpers.update_coordinator.CoordinatorEntity = _StubCoordinatorEntity
    _ha.helpers.update_coordinator.UpdateFailed = _UpdateFailed
    _ha.exceptions.ConfigEntryAuthFailed = _ConfigEntryAuthFailed
    _ha.helpers.device_registry.DeviceInfo = dict

    _ha_modules = {
        "homeassistant": _ha,
        "homeassistant.config_entries": _ha.config_entries,
        "homeassistant.core": _ha.core,
        "homeassistant.data_entry_flow": _ha.data_entry_flow,
        "homeassistant.exceptions": _ha.exceptions,
        "homeassistant.const": _ha.const,
        "homeassistant.helpers": _ha.helpers,
        "homeassistant.helpers.aiohttp_client": _ha.helpers.aiohttp_client,
        "homeassistant.helpers.device_registry": _ha.helpers.device_registry,
        "homeassistant.helpers.entity_platform": _ha.helpers.entity_platform,
        "homeassistant.helpers.update_coordinator": _ha.helpers.update_coordinator,
        "homeassistant.components": _ha.components,
        "homeassistant.components.sensor": _ha.components.sensor,
        "homeassistant.components.binary_sensor": _ha.components.binary_sensor,
        "homeassistant.helpers.event": _ha.helpers.event,
    }
    sys.modules.update(_ha_modules)
