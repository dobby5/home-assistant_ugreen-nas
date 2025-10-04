import logging
from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.typing import StateType
from homeassistant.helpers.update_coordinator import CoordinatorEntity, DataUpdateCoordinator
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from datetime import date, datetime
from decimal import Decimal

from .device_info import build_device_info
from .const import DOMAIN
from .entities import UgreenEntity
from .utils import determine_unit, format_sensor_value

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback
) -> None:
    """Set up UGREEN NAS sensors based on a config entry."""
    config_coordinator = hass.data[DOMAIN][entry.entry_id]["config_coordinator"]
    config_entities = hass.data[DOMAIN][entry.entry_id]["config_entities"]
    state_coordinator = hass.data[DOMAIN][entry.entry_id]["state_coordinator"]
    state_entities = hass.data[DOMAIN][entry.entry_id]["state_entities"]
    nas_model = hass.data[DOMAIN][entry.entry_id].get("nas_model")
    nas_name = hass.data[DOMAIN][entry.entry_id].get("nas_name")

    # Configuration sensors (60s)
    config_sensors = [
        UgreenNasSensor(entry.entry_id, config_coordinator, entity, nas_model, nas_name)
        for entity in config_entities
    ]

    # State sensors (5s)
    state_sensors = [
        UgreenNasSensor(entry.entry_id, state_coordinator, entity, nas_model, nas_name)
        for entity in state_entities
    ]

    async_add_entities(config_sensors + state_sensors)

class UgreenNasSensor(CoordinatorEntity, SensorEntity):
    """Representation of a UGREEN NAS sensor."""

    def __init__(self, entry_id: str, coordinator: DataUpdateCoordinator, endpoint: UgreenEntity, nas_model: 'str | None' = None, nas_name: 'str | None' = None) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._endpoint = endpoint
        self._key = endpoint.description.key

        device_name = nas_name or "UGREEN NAS"
        self._attr_name = f"{device_name} {endpoint.description.name}"
        self._attr_unique_id = f"{entry_id}_{endpoint.description.key}"
        self._attr_icon = endpoint.description.icon

        # Extract brand and serial for disk devices
        brand = None
        serial = None
        model = nas_model

        if "disk" in self._key and "_pool" in self._key:
            # Extract base key (e.g., "disk0_pool0" from "disk0_pool0_temperature")
            key_parts = self._key.split('_')
            base_key = f"{key_parts[0]}_{key_parts[1]}"

            # Get brand and serial from coordinator data
            brand_key = f"{base_key}_brand"
            serial_key = f"{base_key}_serial"
            model_key = f"{base_key}_model"

            brand = self.coordinator.data.get(brand_key)
            serial = self.coordinator.data.get(serial_key)
            disk_model = self.coordinator.data.get(model_key)

            # Use disk model if available, otherwise nas_model
            if disk_model:
                model = str(disk_model)

        self._attr_device_info = build_device_info(
            self._key,
            model=model,
            nas_name=nas_name,
            brand=str(brand) if brand else None,
            serial=str(serial) if serial else None
        )

    @property
    def native_value(self) -> StateType | date | datetime | Decimal:
        """Return the formatted value of the sensor."""
        raw = self.coordinator.data.get(self._key)
        return format_sensor_value(raw, self._endpoint)

    @property
    def extra_state_attributes(self):
        base_attrs = super().extra_state_attributes or {}
        base_attrs.update({
            "nas_device_type": "UGREEN NAS",
            "nas_part_category": self._endpoint.nas_part_category,
        })
        return base_attrs

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Return the unit, dynamically determined."""
        raw = self.coordinator.data.get(self._key)
        unit = self._endpoint.description.unit_of_measurement or ""

        if self._endpoint.description.unit_of_measurement in ("B/s", "kB/s", "MB/s", "GB/s", "TB/s", "PB/s"):
            return determine_unit(raw, unit, True)
        elif self._endpoint.description.unit_of_measurement in ("B", "kB", "MB", "GB", "TB", "PB"):
            return determine_unit(raw, unit, False)

        return self._endpoint.description.unit_of_measurement

    def _handle_coordinator_update(self) -> None:
        """Update the sensor value from the coordinator."""
        self._attr_native_value = self.native_value
        self._attr_native_unit_of_measurement = self.native_unit_of_measurement
        super()._handle_coordinator_update()
