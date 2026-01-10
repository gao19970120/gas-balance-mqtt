"""The Gas Balance MQTT component."""
import logging
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.components.http import StaticPathConfig
from homeassistant.components.frontend import add_extra_js_url

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor"]

# 前端卡片文件路径
CARD_FILENAME = "gas-balance-card.js"

async def async_setup(hass: HomeAssistant, config: dict):
    """Set up the Gas Balance MQTT component."""
    # 注册前端卡片
    await setup_gas_balance_card(hass)
    return True

async def setup_gas_balance_card(hass: HomeAssistant) -> bool:
    """设置燃气卡片前端资源."""
    card_path = '/gas_balance_mqtt-local'
    await hass.http.async_register_static_paths([
        StaticPathConfig(card_path, hass.config.path('custom_components/gas_balance_mqtt/www'), False)
    ])
    _LOGGER.debug(f"register_static_path: {card_path + ':custom_components/gas_balance_mqtt/www'}")
    add_extra_js_url(hass, card_path + f"/{CARD_FILENAME}")
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Set up Gas Balance MQTT from a config entry."""
    # 添加前端资源
    await setup_gas_balance_card(hass)
    
    # Forward the setup to the sensor platform
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    return unload_ok
