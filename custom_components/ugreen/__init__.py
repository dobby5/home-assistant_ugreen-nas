import logging

from datetime import timedelta
from typing import Any
from collections import defaultdict

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.device_registry import async_get as async_get_device_registry, CONNECTION_NETWORK_MAC

from .const import DOMAIN, PLATFORMS
from .utils import get_entity_data_from_api

from .api import UgreenApiClient
from .entities import (
    ALL_NAS_COMMON_CONFIG_ENTITIES,
    ALL_NAS_COMMON_STATE_ENTITIES,
    ALL_NAS_COMMON_BUTTON_ENTITIES
)


_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Setup and start the integration."""


    ### Preparations
    _LOGGER.debug("[UGREEN NAS] Setting up config entry: %s", entry.entry_id)
    hass.data.setdefault(DOMAIN, {})
    session = async_get_clientsession(hass)
    def _cfg(entry: ConfigEntry, key: str, default: Any | None = None):
        return entry.options.get(key, entry.data.get(key, default))
    api = UgreenApiClient(
        ugreen_nas_host=_cfg(entry, "ugreen_host"),
        ugreen_nas_port=int(_cfg(entry, "ugreen_port")),
        username=_cfg(entry, "username"),
        password=_cfg(entry, "password"),
        use_https=bool(_cfg(entry, "use_https", False)),
    )
    keepalive_websocket = api.ws_keepalive(session, lang="de-DE")


    ### Initial authentication
    if not await api.authenticate(session):
        _LOGGER.error("[UGREEN NAS] Initial login failed. Aborting setup.")
        return False


    ### Create global counters for dynamic entities
    dynamic_entity_counts = await api.count_dynamic_entities(session)
    _LOGGER.debug("[UGREEN NAS] Entity counts done: %s", dynamic_entity_counts)


    ### Setup configuration entities (never or slowly changing, 60s polling)
    #   Build the entity list
    config_entities =  list(ALL_NAS_COMMON_CONFIG_ENTITIES)
    config_entities += await api.DISCOVER_NAS_SPECIFIC_CONFIG_ENTITIES(session)
    #   Group entities by endpoint to reduce number of API calls
    config_entities_grouped_by_endpoint = defaultdict(list)
    for entity in config_entities:
        config_entities_grouped_by_endpoint[entity.endpoint].append(entity)
    #   Create the update funktion for the corresponding coordinator
    async def update_configuration_data() -> dict[str, Any]:
        try:
            _LOGGER.debug("[UGREEN NAS] Updating configuration data...")
            endpoint_to_entities = hass.data[DOMAIN][entry.entry_id]["config_entities_grouped_by_endpoint"]
            return await get_entity_data_from_api(api, session, endpoint_to_entities)
        except Exception as err:
            raise UpdateFailed(f"[UGREEN NAS] Configuration entities update error: {err}") from err
    #   Create the coordinator
    config_coordinator = DataUpdateCoordinator( # data polling every 60s
        hass,
        _LOGGER,
        name="ugreen_configuration",
        update_method=update_configuration_data,
        update_interval=timedelta(seconds=60),
    )


    ### Setup state entities (changing rather quickly, 5s polling)
    #   Build the entity list
    state_entities =  list(ALL_NAS_COMMON_STATE_ENTITIES)
    state_entities += await api.DISCOVER_NAS_SPECIFIC_STATE_ENTITIES(session)
    #   Group entities by endpoint to reduce number of API calls
    state_entities_grouped_by_endpoint = defaultdict(list)
    for entity in state_entities: # reduce number of API calls
        state_entities_grouped_by_endpoint[entity.endpoint].append(entity)
    _LOGGER.debug("[UGREEN NAS] List of state entities prepared.")
    #   Create the update funktion for the corresponding coordinator
    async def update_state_data() -> dict[str, Any]: # update for coordinator
        try:
            _LOGGER.debug("[UGREEN NAS] Updating state data...")
            endpoint_to_entities = hass.data[DOMAIN][entry.entry_id]["state_entities_grouped_by_endpoint"]
            return await get_entity_data_from_api(api, session, endpoint_to_entities)
        except Exception as err:
            raise UpdateFailed(f"[UGREEN NAS] State entities update error: {err}") from err
    #   Create the coordinator
    state_coordinator = DataUpdateCoordinator( # data polling every 5s
        hass,
        _LOGGER,
        name="ugreen_state",
        update_method=update_state_data,
        update_interval=timedelta(seconds=entry.options.get("state_interval", 5)),
    )


    ### Create the websocket coordinator to keep API alive if no UGreen App / Web GUI is active
    ws_coordinator = DataUpdateCoordinator( # data polling every 25s
        hass,
        _LOGGER,
        name="ugreen_ws",
        update_method=keepalive_websocket,
        update_interval=timedelta(seconds=25),
    )


    ### Hand over all runtime objects to HA's data container
    hass.data[DOMAIN][entry.entry_id] = {
        "config_coordinator": config_coordinator,
        "config_entities": config_entities,
        "config_entities_grouped_by_endpoint": config_entities_grouped_by_endpoint,
        "state_coordinator": state_coordinator,
        "state_entities": state_entities,
        "state_entities_grouped_by_endpoint": state_entities_grouped_by_endpoint,
        "ws_coordinator": ws_coordinator,
        "button_entities": ALL_NAS_COMMON_BUTTON_ENTITIES,
        "dynamic_entity_counts": dynamic_entity_counts,
        "api": api,
    }


    ### Initial entities refresh
    await config_coordinator.async_config_entry_first_refresh()
    await state_coordinator.async_config_entry_first_refresh()
    await ws_coordinator.async_config_entry_first_refresh()


    ### Device registration in HA - identify through serial # and MAC addresses (fallback)
    try:
        dev_reg = async_get_device_registry(hass)
        sys_info = await api.get(session, "/ugreen/v1/sysinfo/machine/common")
        common  = (sys_info or {}).get("data", {}).get("common", {})
        model   = common.get("model", "Unknown")
        version = common.get("system_version", "Unknown")
        name    = common.get("nas_name", "UGREEN NAS")
        serial  = (common.get("serial") or "").strip()
        macs    = common.get("mac") or []

        # Unique device identifiers - serial# (main identifier, if available)
        identifiers = {(DOMAIN, f"entry:{entry.entry_id}")} 
        if serial:
            identifiers.add((DOMAIN, f"serial:{serial}"))

        # Unique device identifiers - MAC's (lowercase; fallback if no serial# )
        connections = {(CONNECTION_NETWORK_MAC, m.lower()) for m in macs if isinstance(m, str) and m}
        dev_reg.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers=identifiers,
            connections=connections,
            manufacturer="UGREEN",
            name=name,
            model=model,
            sw_version=version,
            serial_number=serial or None,
        )
        _LOGGER.debug("[UGREEN NAS] Device registered: Model=%s, Version=%s", model, version)

        # Make 'model' available for sensor.py and button.py for displaying on child devices
        hass.data[DOMAIN][entry.entry_id]["nas_model"] = model

    except Exception as e:
        _LOGGER.warning("[UGREEN NAS] Device registration failed: %s", e)

    
    ### Finalize it - forward the setups to platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _LOGGER.debug("[UGREEN NAS] Forwarded entry setups to platforms - setup complete.")

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload the integration and stop all background schedulers."""

    data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})

    ### Stop update intervals to prevent any further background work
    for key in ("ws_coordinator", "state_coordinator", "config_coordinator"):
        coord = data.get(key)
        if coord:
            coord.update_interval = None

    ### Unload platforms / entities and clean up data container
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)

    return unload_ok
