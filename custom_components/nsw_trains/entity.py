"""Base entity and device info shared by the sensor platforms."""

from typing import TypeVar

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity, DataUpdateCoordinator

from .const import ATTRIBUTION, DOMAIN, MODEL_NAME, SHORT_ATTRIBUTION

CoordinatorT = TypeVar("CoordinatorT", bound=DataUpdateCoordinator)


def _device_info(identifier: str, name: str, model: str) -> DeviceInfo:
    return DeviceInfo(
        entry_type=DeviceEntryType.SERVICE,
        identifiers={(DOMAIN, identifier)},
        manufacturer=SHORT_ATTRIBUTION,
        model=model,
        name=name,
    )


def station_device_info(stop_id: str, stop_name: str) -> DeviceInfo:
    return _device_info(stop_id, stop_name, MODEL_NAME)


def line_device_info(line: str, line_name: str) -> DeviceInfo:
    return _device_info(f"line_{line}", line_name, "Line")


def home_device_info(entry_id: str, title: str) -> DeviceInfo:
    return _device_info(f"home_{entry_id}", title, "Trains passing")


class NswTrainsEntity(CoordinatorEntity[CoordinatorT]):
    _attr_has_entity_name = True
    _attr_attribution = ATTRIBUTION

    def __init__(self, coordinator: CoordinatorT, device_info: DeviceInfo) -> None:
        super().__init__(coordinator)
        self._attr_device_info = device_info
