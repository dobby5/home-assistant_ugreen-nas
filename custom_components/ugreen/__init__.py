"""The UGREEN NAS integration."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any
from collections import defaultdict

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.device_registry import (
    async_get as async_get_device_registry,
    CONNECTION_NETWORK_MAC,
)

from .const import (
    DOMAIN,
    PLATFORMS,
    DEFAULT_SCAN_INTERVAL_CONFIG,
    DEFAULT_SCAN_INTERVAL_STATE,
    DEFAULT_SCAN_INTERVAL_WS,
)
from .utils import get_entity_data_from_api
from .api import UgreenApiClient
from .entities import (
    ALL_NAS_COMMON_CONFIG_ENTITIES,
    ALL_NAS_COMMON_STATE_ENTITIES,
    ALL_NAS_COMMON_BUTTON_ENTITIES,
)

_LOGGER = logging.getLogger(__name__)


def _get_config_value(entry: ConfigEntry, key: str, default: Any | None = None) -> Any:
    """Get configuration value from options or data with fallback."""
    return entry.options.get(key, entry.data.get(key, default))


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up UGREEN NAS from a config entry."""
    _LOGGER.info("[UGREEN NAS] Setting up integration for entry: %s", entry.entry_id)

    # Initialize domain data structure
    hass.data.setdefault(DOMAIN, {})

    # Get HTTP session
    session = async_get_clientsession(hass)

    # Initialize API client
    api = UgreenApiClient(
        ugreen_nas_host=_get_config_value(entry, "ugreen_host"),
        ugreen_nas_port=int(_get_config_value(entry, "ugreen_port", 9999)),
        username=_get_config_value(entry, "username"),
        password=_get_config_value(entry, "password"),
        use_https=bool(_get_config_value(entry, "use_https", False)),
    )

    # Authenticate
    try:
        if not await api.authenticate(session):
            raise ConfigEntryNotReady("Authentication failed")
    except Exception as err:
        _LOGGER.error("[UGREEN NAS] Authentication failed: %s", err)
        raise ConfigEntryNotReady(f"Authentication failed: {err}") from err

    _LOGGER.info("[UGREEN NAS] Successfully authenticated")

    # Get dynamic entity counts
    try:
        dynamic_entity_counts = await api.count_dynamic_entities(session)
        _LOGGER.debug("[UGREEN NAS] Dynamic entity counts: %s", dynamic_entity_counts)
    except Exception as err:
        _LOGGER.warning(
            "[UGREEN NAS] Failed to count dynamic entities, using defaults: %s", err
        )
        dynamic_entity_counts = {}

    # Build configuration entities
    config_entities = list(ALL_NAS_COMMON_CONFIG_ENTITIES)
    try:
        config_entities += await api.DISCOVER_NAS_SPECIFIC_CONFIG_ENTITIES(session)
    except Exception as err:
        _LOGGER.warning("[UGREEN NAS] Failed to discover NAS-specific config entities: %s", err)

    # Group configuration entities by endpoint
    config_entities_grouped = defaultdict(list)
    for entity in config_entities:
        config_entities_grouped[entity.endpoint].append(entity)

    # Build state entities
    state_entities = list(ALL_NAS_COMMON_STATE_ENTITIES)
    try:
        state_entities += await api.DISCOVER_NAS_SPECIFIC_STATE_ENTITIES(session)
    except Exception as err:
        _LOGGER.warning("[UGREEN NAS] Failed to discover NAS-specific state entities: %s", err)

    # Group state entities by endpoint
    state_entities_grouped = defaultdict(list)
    for entity in state_entities:
        state_entities_grouped[entity.endpoint].append(entity)

    # Create update functions
    async def update_config_data() -> dict[str, Any]:
        """Update configuration data."""
        try:
            endpoint_to_entities = hass.data[DOMAIN][entry.entry_id][
                "config_entities_grouped_by_endpoint"
            ]
            return await get_entity_data_from_api(api, session, endpoint_to_entities)
        except Exception as err:
            _LOGGER.debug("[UGREEN NAS] Config update error: %s", err)
            raise UpdateFailed(f"Config update failed: {err}") from err

    async def update_state_data() -> dict[str, Any]:
        """Update state data."""
        try:
            endpoint_to_entities = hass.data[DOMAIN][entry.entry_id][
                "state_entities_grouped_by_endpoint"
            ]
            return await get_entity_data_from_api(api, session, endpoint_to_entities)
        except Exception as err:
            _LOGGER.debug("[UGREEN NAS] State update error: %s", err)
            raise UpdateFailed(f"State update failed: {err}") from err

    # Create coordinators
    config_coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=f"{DOMAIN}_config",
        update_method=update_config_data,
        update_interval=timedelta(
            seconds=_get_config_value(entry, "config_interval", DEFAULT_SCAN_INTERVAL_CONFIG)
        ),
    )

    state_coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=f"{DOMAIN}_state",
        update_method=update_state_data,
        update_interval=timedelta(
            seconds=_get_config_value(entry, "state_interval", DEFAULT_SCAN_INTERVAL_STATE)
        ),
    )

    ws_coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=f"{DOMAIN}_ws",
        update_method=api.ws_keepalive(session, lang="en"),
        update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL_WS),
    )

    # Store runtime data
    hass.data[DOMAIN][entry.entry_id] = {
        "api": api,
        "config_coordinator": config_coordinator,
        "config_entities": config_entities,
        "config_entities_grouped_by_endpoint": config_entities_grouped,
        "state_coordinator": state_coordinator,
        "state_entities": state_entities,
        "state_entities_grouped_by_endpoint": state_entities_grouped,
        "ws_coordinator": ws_coordinator,
        "button_entities": ALL_NAS_COMMON_BUTTON_ENTITIES,
        "dynamic_entity_counts": dynamic_entity_counts,
    }

    # Perform first refresh
    try:
        await config_coordinator.async_config_entry_first_refresh()
        await state_coordinator.async_config_entry_first_refresh()
        await ws_coordinator.async_config_entry_first_refresh()
    except Exception as err:
        _LOGGER.error("[UGREEN NAS] Initial refresh failed: %s", err)
        raise ConfigEntryNotReady(f"Initial refresh failed: {err}") from err

    # Register device
    await _async_register_device(hass, entry, api, session)

    # Forward setup to platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register options update listener
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    _LOGGER.info("[UGREEN NAS] Setup completed successfully")
    return True


async def _async_register_device(
    hass: HomeAssistant,
    entry: ConfigEntry,
    api: UgreenApiClient,
    session: Any,
) -> None:
    """Register device in Home Assistant."""
    try:
        sys_info = await api.get(session, "/ugreen/v1/sysinfo/machine/common")
        common = (sys_info or {}).get("data", {}).get("common", {})

        model = common.get("model", "Unknown")
        version = common.get("system_version", "Unknown")
        name = common.get("nas_name", "UGREEN NAS")
        serial = (common.get("serial") or "").strip()
        macs = common.get("mac") or []

        # Build unique identifiers
        identifiers = {(DOMAIN, f"entry:{entry.entry_id}")}
        if serial:
            identifiers.add((DOMAIN, f"serial:{serial}"))

        # Build connections (MAC addresses)
        connections = {
            (CONNECTION_NETWORK_MAC, mac.lower())
            for mac in macs
            if isinstance(mac, str) and mac
        }

        # Register device
        dev_reg = async_get_device_registry(hass)
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

        # Store device info for entities
        hass.data[DOMAIN][entry.entry_id]["nas_model"] = model
        hass.data[DOMAIN][entry.entry_id]["nas_name"] = name

        _LOGGER.info(
            "[UGREEN NAS] Device registered: %s (Model: %s, Version: %s)",
            name,
            model,
            version,
        )

    except Exception as err:
        _LOGGER.warning("[UGREEN NAS] Device registration failed: %s", err)


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry when options change."""
    _LOGGER.info("[UGREEN NAS] Reloading integration due to options change")
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.info("[UGREEN NAS] Unloading integration")

    # Get entry data
    entry_data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})

    # Stop all coordinators
    for coordinator_key in ("config_coordinator", "state_coordinator", "ws_coordinator"):
        coordinator = entry_data.get(coordinator_key)
        if coordinator:
            coordinator.update_interval = None

    # Unload platforms
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    # Clean up stored data
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        _LOGGER.info("[UGREEN NAS] Successfully unloaded")
    else:
        _LOGGER.warning("[UGREEN NAS] Failed to unload some platforms")

    return unload_ok
