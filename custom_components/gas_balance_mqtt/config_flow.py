import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from .const import DOMAIN


def _date_to_md(value, fallback):
    """Convert a stored date value to MM-DD."""
    value = str(value or "").replace(".", "-").strip()
    if len(value) == 10:
        return value[5:10]
    if len(value) == 5:
        return value
    return fallback


def _validate_md(value):
    """Validate MM-DD date string."""
    value = str(value or "").strip()
    try:
        month_str, day_str = value.split("-", 1)
        month = int(month_str)
        day = int(day_str)
    except (ValueError, AttributeError):
        return False

    month_days = {
        1: 31, 2: 29, 3: 31, 4: 30, 5: 31, 6: 30,
        7: 31, 8: 31, 9: 30, 10: 31, 11: 30, 12: 31,
    }
    return month in month_days and 1 <= day <= month_days[month]


def _validate_input(user_input):
    """Validate config/options input."""
    errors = {}
    for key in ("tier_cycle_start_md", "tier_cycle_end_md"):
        if not _validate_md(user_input.get(key)):
            errors[key] = "invalid_date"
    return errors


def _build_schema(defaults=None):
    """Build config/options form schema."""
    defaults = defaults or {}
    default_start = _date_to_md(
        defaults.get("tier_cycle_start_md", defaults.get("current_year_step_start_date")),
        "01-01",
    )
    default_end = _date_to_md(
        defaults.get("tier_cycle_end_md", defaults.get("current_year_step_end_date")),
        "12-31",
    )

    return vol.Schema({
        vol.Required("name", default=defaults.get("name", "Gas Balance")): str,
        vol.Required("topic", default=defaults.get("topic", "gas/raw/balance")): str,
        vol.Required("bill_topic", default=defaults.get("bill_topic", "gas/raw/month_bill")): str,
        vol.Optional(
            "tier_cycle_start_md",
            default=default_start,
        ): str,
        vol.Optional(
            "tier_cycle_end_md",
            default=default_end,
        ): str,
        vol.Optional(
            "yearly_step_2_start_volume",
            default=defaults.get("yearly_step_2_start_volume", 400),
        ): int,
        vol.Optional(
            "yearly_step_3_start_volume",
            default=defaults.get("yearly_step_3_start_volume", 1680),
        ): int,
        vol.Optional(
            "year_step_1_price",
            default=defaults.get("year_step_1_price", 2.99),
        ): float,
        vol.Optional(
            "year_step_2_price",
            default=defaults.get("year_step_2_price", 3.44),
        ): float,
        vol.Optional(
            "year_step_3_price",
            default=defaults.get("year_step_3_price", 4.34),
        ): float,
    })

class GasBalanceConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Gas Balance MQTT."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow for this handler."""
        return GasBalanceOptionsFlow(config_entry)

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        errors = {}

        if user_input is not None:
            errors = _validate_input(user_input)
            if not errors:
                return self.async_create_entry(title=user_input["name"], data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=_build_schema(),
            errors=errors
        )


class GasBalanceOptionsFlow(config_entries.OptionsFlow):
    """Handle options for Gas Balance MQTT."""

    def __init__(self, config_entry):
        """Initialize options flow."""
        self._config_entry = config_entry

    async def async_step_init(self, user_input=None):
        """Manage integration options."""
        if user_input is not None:
            errors = _validate_input(user_input)
            if not errors:
                return self.async_create_entry(title="", data=user_input)
        else:
            errors = {}

        defaults = {**self._config_entry.data, **self._config_entry.options}

        return self.async_show_form(
            step_id="init",
            data_schema=_build_schema(defaults),
            errors=errors,
        )
