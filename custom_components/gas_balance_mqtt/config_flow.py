import voluptuous as vol
from homeassistant import config_entries
from .const import DOMAIN

class GasBalanceConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Gas Balance MQTT."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        errors = {}

        if user_input is not None:
            # Validate input if necessary, for now we just create the entry
            return self.async_create_entry(title=user_input["name"], data=user_input)

        data_schema = vol.Schema({
            vol.Required("name", default="Gas Balance"): str,
            vol.Required("topic", default="gas/raw/balance"): str,
            vol.Required("bill_topic", default="gas/raw/month_bill"): str,
            vol.Optional("yearly_step_2_start_volume", default=400): int,
            vol.Optional("yearly_step_3_start_volume", default=1680): int,
            vol.Optional("year_step_1_price", default=2.99): float,
            vol.Optional("year_step_2_price", default=3.44): float,
            vol.Optional("year_step_3_price", default=4.34): float,
        })

        return self.async_show_form(
            step_id="user",
            data_schema=data_schema,
            errors=errors
        )
