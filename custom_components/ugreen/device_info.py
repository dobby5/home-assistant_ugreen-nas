from homeassistant.helpers.device_registry import DeviceInfo
from .const import DOMAIN


def build_device_info(
    key: str,
    model: str | None = None,
    nas_name: str | None = None,
    brand: str | None = None,
    serial: str | None = None,
) -> DeviceInfo:
    """Build DeviceInfo based on the sensor or button key."""

    # Use the NAS name from API, fallback to "UGREEN NAS"
    device_name = nas_name or "UGREEN NAS"

    if key.startswith("disk") and "_pool" in key:
        parts = key.split('_')
        disk_index = parts[0][4:]
        pool_index = parts[1][4:]
        return DeviceInfo(
            identifiers={(DOMAIN, f"ugreen_nas_disk_{pool_index}_{disk_index}")},
            name=f"{device_name} (Pool {pool_index} | Disk {disk_index})",
            manufacturer=brand or "UGREEN",
            model=model,
            serial_number=serial,
            via_device=(DOMAIN, "ugreen_nas"),
        )

    if key.startswith("volume") and "_pool" in key:
        parts = key.split('_')
        volume_index = parts[0][6:]
        pool_index = parts[1][4:]
        return DeviceInfo(
            identifiers={(DOMAIN, f"ugreen_nas_volume_{pool_index}_{volume_index}")},
            name=f"{device_name} (Pool {pool_index} | Volume {volume_index})",
            manufacturer="UGREEN",
            model=model,
            via_device=(DOMAIN, "ugreen_nas"),
        )


    if key.startswith("pool"):
        pool_index = key.split('_')[0][4:]
        return DeviceInfo(
            identifiers={(DOMAIN, f"ugreen_nas_pool_{pool_index}")},
            name=f"{device_name} (Pool {pool_index})",
            manufacturer="UGREEN",
            model=model,
            via_device=(DOMAIN, "ugreen_nas"),
        )


    return DeviceInfo(
        identifiers={(DOMAIN, "ugreen_nas")},
        manufacturer="UGREEN",
        name=device_name,
    )
