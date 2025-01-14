"""Provides diagnostics for ZHA."""
from __future__ import annotations

import dataclasses
from importlib.metadata import version
from typing import Any

from zigpy.config import CONF_NWK_EXTENDED_PAN_ID
from zigpy.profiles import PROFILES
from zigpy.types import Channels
from zigpy.zcl import Cluster

from homeassistant.components.diagnostics.util import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ID, CONF_NAME, CONF_UNIQUE_ID
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .core.const import (
    ATTR_ATTRIBUTE_NAME,
    ATTR_DEVICE_TYPE,
    ATTR_IEEE,
    ATTR_IN_CLUSTERS,
    ATTR_OUT_CLUSTERS,
    ATTR_PROFILE_ID,
    ATTR_VALUE,
    CONF_ALARM_MASTER_CODE,
    UNKNOWN,
)
from .core.device import ZHADevice
from .core.helpers import async_get_zha_device, get_zha_data, get_zha_gateway

KEYS_TO_REDACT = {
    ATTR_IEEE,
    CONF_UNIQUE_ID,
    CONF_ALARM_MASTER_CODE,
    "network_key",
    CONF_NWK_EXTENDED_PAN_ID,
    "partner_ieee",
}

ATTRIBUTES = "attributes"
CLUSTER_DETAILS = "cluster_details"
UNSUPPORTED_ATTRIBUTES = "unsupported_attributes"


def shallow_asdict(obj: Any) -> dict:
    """Return a shallow copy of a dataclass as a dict."""
    if hasattr(obj, "__dataclass_fields__"):
        result = {}

        for field in dataclasses.fields(obj):
            result[field.name] = shallow_asdict(getattr(obj, field.name))

        return result
    if hasattr(obj, "as_dict"):
        return obj.as_dict()
    return obj


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, config_entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    zha_data = get_zha_data(hass)
    app = get_zha_gateway(hass).application_controller

    energy_scan = await app.energy_scan(
        channels=Channels.ALL_CHANNELS, duration_exp=4, count=1
    )

    return async_redact_data(
        {
            "config": zha_data.yaml_config,
            "config_entry": config_entry.as_dict(),
            "application_state": shallow_asdict(app.state),
            "energy_scan": {
                channel: 100 * energy / 255 for channel, energy in energy_scan.items()
            },
            "versions": {
                "bellows": version("bellows"),
                "zigpy": version("zigpy"),
                "zigpy_deconz": version("zigpy-deconz"),
                "zigpy_xbee": version("zigpy-xbee"),
                "zigpy_znp": version("zigpy_znp"),
                "zigpy_zigate": version("zigpy-zigate"),
                "zhaquirks": version("zha-quirks"),
            },
        },
        KEYS_TO_REDACT,
    )


async def async_get_device_diagnostics(
    hass: HomeAssistant, device: dr.DeviceEntry
) -> dict[str, Any]:
    """Return diagnostics for a device."""
    zha_device: ZHADevice = async_get_zha_device(hass, device.id)
    device_info: dict[str, Any] = zha_device.zha_device_info
    device_info[CLUSTER_DETAILS] = get_endpoint_cluster_attr_data(zha_device)
    return async_redact_data(device_info, KEYS_TO_REDACT)


def get_endpoint_cluster_attr_data(zha_device: ZHADevice) -> dict:
    """Return endpoint cluster attribute data."""
    cluster_details = {}
    for ep_id, endpoint in zha_device.device.endpoints.items():
        if ep_id == 0:
            continue
        endpoint_key = (
            f"{PROFILES.get(endpoint.profile_id).DeviceType(endpoint.device_type).name}"
            if PROFILES.get(endpoint.profile_id) is not None
            and endpoint.device_type is not None
            else UNKNOWN
        )
        cluster_details[ep_id] = {
            ATTR_DEVICE_TYPE: {
                CONF_NAME: endpoint_key,
                CONF_ID: endpoint.device_type,
            },
            ATTR_PROFILE_ID: endpoint.profile_id,
            ATTR_IN_CLUSTERS: {
                f"0x{cluster_id:04x}": {
                    "endpoint_attribute": cluster.ep_attribute,
                    **get_cluster_attr_data(cluster),
                }
                for cluster_id, cluster in endpoint.in_clusters.items()
            },
            ATTR_OUT_CLUSTERS: {
                f"0x{cluster_id:04x}": {
                    "endpoint_attribute": cluster.ep_attribute,
                    **get_cluster_attr_data(cluster),
                }
                for cluster_id, cluster in endpoint.out_clusters.items()
            },
        }
    return cluster_details


def get_cluster_attr_data(cluster: Cluster) -> dict:
    """Return cluster attribute data."""
    return {
        ATTRIBUTES: {
            f"0x{attr_id:04x}": {
                ATTR_ATTRIBUTE_NAME: attr_def.name,
                ATTR_VALUE: attr_value,
            }
            for attr_id, attr_def in cluster.attributes.items()
            if (attr_value := cluster.get(attr_def.name)) is not None
        },
        UNSUPPORTED_ATTRIBUTES: {
            f"0x{cluster.find_attribute(u_attr).id:04x}": {
                ATTR_ATTRIBUTE_NAME: cluster.find_attribute(u_attr).name
            }
            for u_attr in cluster.unsupported_attributes
        },
    }
