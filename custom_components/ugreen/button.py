import logging
from homeassistant.components.button import ButtonEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity, DataUpdateCoordinator
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from .device_info import build_device_info
from .const import DOMAIN
from .api import UgreenApiClient
from .entities import UgreenEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback
) -> None:
    """Set up UGREEN NAS buttons based on a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]["config_coordinator"]
    entities: list[UgreenEntity] = hass.data[DOMAIN][entry.entry_id]["button_entities"]
    api = hass.data[DOMAIN][entry.entry_id]["api"]

    nas_model = hass.data[DOMAIN][entry.entry_id].get("nas_model")
    nas_name = hass.data[DOMAIN][entry.entry_id].get("nas_name")

    button_entities = [
        UgreenNasButton(entry.entry_id, coordinator, entity, api, nas_model, nas_name)
        for entity in entities
    ]

    async_add_entities(button_entities)


class UgreenNasButton(CoordinatorEntity, ButtonEntity):
    """Representation of a UGREEN NAS button."""

    def __init__(self, entry_id: str, coordinator: DataUpdateCoordinator,
        endpoint: UgreenEntity,
        api: UgreenApiClient,
        nas_model: 'str | None' = None,
        nas_name: 'str | None' = None
    )-> None:
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._endpoint = endpoint
        self._key = endpoint.description.key
        self._api = api

        device_name = nas_name or "UGREEN NAS"
        self._attr_name = f"{device_name} {endpoint.description.name}"
        self._attr_unique_id = f"{entry_id}_{endpoint.description.key}"
        self._attr_icon = endpoint.description.icon
        self._attr_native_unit_of_measurement = endpoint.description.unit_of_measurement

        self._attr_device_info = build_device_info(self._key, nas_model, nas_name)

    async def async_press(self) -> None:
        """Perform the button action."""
        try:
            session = async_get_clientsession(self.hass)
            await self._api.authenticate(session)

            method = str(self._endpoint.request_method or "GET").upper()
            path = self._endpoint.endpoint
    
            if method == "POST":
                await self._api.post(session, path)
            elif method == "GET":
                await self._api.get(session, path)
            else:
                _LOGGER.warning("Unsupported method: %s", method)

        except Exception as e:
            _LOGGER.error("Error pressing button %s: %s", self._key, e)
    
    def _handle_coordinator_update(self) -> None:
        """Update the button value from the coordinator."""
        self._attr_press = self.async_press
        super()._handle_coordinator_update()
