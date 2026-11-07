"""NSW Trains: departures, line status and trains passing home, from Transport for NSW."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import TransportNswClient
from .const import CONF_API_KEY, CONF_ENTRY_TYPE, ENTRY_TYPE_HOME, ENTRY_TYPE_LINE, ENTRY_TYPE_STATION
from .coordinator import HomeCoordinator, LineCoordinator, NswTrainsCoordinator

PLATFORMS = ["sensor", "binary_sensor"]

COORDINATORS = {
    ENTRY_TYPE_STATION: NswTrainsCoordinator,
    ENTRY_TYPE_LINE: LineCoordinator,
    ENTRY_TYPE_HOME: HomeCoordinator,
}


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a station, line or home from a config entry."""
    client = TransportNswClient(async_get_clientsession(hass), entry.data[CONF_API_KEY])
    coordinator_class = COORDINATORS[entry.data.get(CONF_ENTRY_TYPE, ENTRY_TYPE_STATION)]
    coordinator = coordinator_class(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Reload when the options flow changes settings
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok: bool = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    return unload_ok
