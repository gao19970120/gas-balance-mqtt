import json
import logging
from datetime import timedelta
import voluptuous as vol

from homeassistant.components.sensor import (
    SensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    STATE_UNKNOWN,
    STATE_UNAVAILABLE,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.components import mqtt
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Gas Balance sensor from a config entry."""
    name = entry.data.get("name")
    topic = entry.data.get("topic")
    bill_topic = entry.data.get("bill_topic", "gas/raw/month_bill")

    async_add_entities([GasBalanceSensor(name, topic, bill_topic, entry.data)], True)


class GasBalanceSensor(RestoreEntity, SensorEntity):
    """Representation of a Gas Balance Sensor."""

    def __init__(self, name, topic, bill_topic, config_data):
        """Initialize the sensor."""
        self._name = name
        self._topic = topic
        self._bill_topic = bill_topic
        self._state = None
        
        # Dynamic yearly step configuration
        now = dt_util.now()
        current_year = now.year
        
        # Default values or from config
        yearly_step_2_start = config_data.get("yearly_step_2_start_volume", 400)
        yearly_step_3_start = config_data.get("yearly_step_3_start_volume", 1680)
        year_step_1_price = config_data.get("year_step_1_price", 2.99)
        year_step_2_price = config_data.get("year_step_2_price", 3.44)
        year_step_3_price = config_data.get("year_step_3_price", 4.34)
        
        self._year_step_config = {
            "current_year_step_start_date": f"{current_year}.01.01",
            "current_year_step_end_date": f"{current_year}.12.31",
            "yearly_step_2_start_volume": yearly_step_2_start,
            "yearly_step_3_start_volume": yearly_step_3_start,
            "year_step_1_price": year_step_1_price,
            "year_step_2_price": year_step_2_price,
            "year_step_3_price": year_step_3_price,
        }
        
        self._attributes = {
            "daylist": [],
            "monthlist": [],
            "monthly_bill_source_data": [],
            "yearlist": [],
            "last_record_time": None,
            "cust_name": None,
            "address": None,
            # Initialize with config values
            "current_year_step_start_date": self._year_step_config["current_year_step_start_date"],
            "current_year_step_end_date": self._year_step_config["current_year_step_end_date"],
            "yearly_step_2_start_volume": self._year_step_config["yearly_step_2_start_volume"],
            "yearly_step_3_start_volume": self._year_step_config["yearly_step_3_start_volume"],
            "year_step_1_price": self._year_step_config["year_step_1_price"],
            "year_step_2_price": self._year_step_config["year_step_2_price"],
            "year_step_3_price": self._year_step_config["year_step_3_price"],
            "yearly_step_accumulated_usage": 0.0,
            "split_day": 22, # Default split day
        }
        # Internal state for calculations
        self._last_balance = None
        self._last_update_dt = None

    @property
    def name(self):
        """Return the name of the sensor."""
        return self._name

    @property
    def unique_id(self):
        """Return a unique ID."""
        return f"gas_balance_{self._topic}"

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._state

    @property
    def extra_state_attributes(self):
        """Return the state attributes."""
        return self._attributes

    @property
    def icon(self):
        """Return the icon to use in the frontend."""
        return "mdi:gas-cylinder"

    def _parse_date(self, date_val):
        """Parse date string to datetime object."""
        if not date_val:
            return None
        s = str(date_val).strip()
        if len(s) == 8 and '-' not in s:
            s = f"{s[:4]}-{s[4:6]}-{s[6:]}"
        
        try:
            # Parse as simple date (YYYY-MM-DD)
            # We use dt_util.parse_datetime or just datetime.strptime
            # But here we need comparable datetime objects. 
            # daylist uses "YYYY-MM-DD".
            # Let's return a naive datetime at 00:00:00 or simple date string?
            # daylist comparison uses string comparison in _calculate_yearly_usage.
            # But for date arithmetic (adding days), we need datetime.
            # Let's return datetime object.
            dt = dt_util.parse_datetime(s + " 00:00:00")
            if dt is None:
                # Try manual parsing if parse_datetime fails (e.g. format issues)
                import datetime
                dt = datetime.datetime.strptime(s, "%Y-%m-%d")
                # Make it timezone aware if needed, but daylist usually isn't strict?
                # Actually homeassistant.util.dt returns aware datetimes.
                dt = dt.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
            return dt
        except Exception:
            return None

    def _calculate_natural_month_data(self):
        """Calculate natural month data from monthly_bill_source_data and daylist."""
        source_data = self._attributes.get("monthly_bill_source_data", [])
        daylist = self._attributes.get("daylist", [])
        
        if not source_data:
            return

        # 1. Sort bills by timeCurRecord
        def get_bill_date(bill):
            return self._parse_date(bill.get("timeCurRecord"))
            
        # Filter bills that have a valid timeCurRecord
        valid_bills = [b for b in source_data if get_bill_date(b)]
        valid_bills.sort(key=lambda x: get_bill_date(x).timestamp())
        
        # 2. Group by Target Month
        bills_by_month = {}
        
        for index, bill in enumerate(valid_bills):
            date = get_bill_date(bill)
            # Target month is determined by the record date (e.g. 2025-12-30 -> 2025-12)
            month_str = date.strftime("%Y-%m")
            
            if month_str not in bills_by_month:
                bills_by_month[month_str] = []
            
            # Find previous bill's date (if any) from the GLOBAL sorted list
            prev_bill_date = None
            if index > 0:
                prev_bill_date = get_bill_date(valid_bills[index - 1])
                
            bills_by_month[month_str].append({
                "bill": bill,
                "prev_bill_date": prev_bill_date,
                "curr_bill_date": date
            })
            
        # 3. Calculate Natural Month Data
        natural_month_list = []
        covered_months = set()
        
        for month_str, bills_info in bills_by_month.items():
            total_gas_num = 0.0
            total_gas_cost = 0.0
            
            # Base total from bills
            for info in bills_info:
                bill = info["bill"]
                try:
                    total_gas_num += float(bill.get("monthGasNum", 0) or bill.get("curQty", 0))
                    total_gas_cost += float(bill.get("monthGasCost", 0) or bill.get("gasFee", 0))
                except (ValueError, TypeError):
                    pass
            
            # Corrections
            # We use the range defined by the first bill's previous bill (start) 
            # and the last bill's current date (end).
            
            first_info = bills_info[0]
            last_info = bills_info[-1]
            
            start_date = first_info["prev_bill_date"] # Inclusive start of the bill coverage
            end_date = last_info["curr_bill_date"]   # Exclusive end of the bill coverage (bill record date is usually the reading date)
            
            for day_item in daylist:
                day_str = day_item.get("day", "").split(' ')[0]
                day_date = self._parse_date(day_str)
                if not day_date:
                    continue
                
                try:
                    num = float(day_item.get("dayGasNum", 0))
                    cost = float(day_item.get("dayGasCost", 0))
                except (ValueError, TypeError):
                    continue
                
                is_in_target_month = day_str.startswith(month_str)
                
                if start_date and end_date:
                    if day_date >= start_date and day_date < end_date:
                        # Day is inside the bill's coverage
                        if not is_in_target_month:
                             # But belongs to another month (e.g. Dec) -> Subtract
                             total_gas_num -= num
                             total_gas_cost -= cost
                             
                # 2. Add current month data (if not in bill range but in target month)
                # i.e. Days after the bill reading
                if end_date:
                     if day_date >= end_date:
                         if is_in_target_month:
                             # Belongs to target month but after bill -> Add
                             total_gas_num += num
                             total_gas_cost += cost
                             
            natural_month_list.append({
                "month": month_str,
                "monthGasNum": round(total_gas_num, 2),
                "monthGasCost": round(total_gas_cost, 2)
            })
            covered_months.add(month_str)
        
        # 4. Aggregate daylist for missing months (e.g. current month with no bill yet)
        daylist_by_month = {}
        for day_item in daylist:
             day_str = day_item.get("day", "").split(' ')[0] # "YYYY-MM-DD"
             if len(day_str) < 7: continue
             month_str = day_str[:7] # "YYYY-MM"
             
             if month_str not in daylist_by_month:
                 daylist_by_month[month_str] = {"num": 0.0, "cost": 0.0}
             
             try:
                 daylist_by_month[month_str]["num"] += float(day_item.get("dayGasNum", 0))
                 daylist_by_month[month_str]["cost"] += float(day_item.get("dayGasCost", 0))
             except: pass
        
        for month_str, data in daylist_by_month.items():
            if month_str not in covered_months:
                 natural_month_list.append({
                     "month": month_str,
                     "monthGasNum": round(data["num"], 2),
                     "monthGasCost": round(data["cost"], 2)
                 })
            
        # Sort and update
        natural_month_list.sort(key=lambda x: x["month"])
        self._attributes["monthlist"] = natural_month_list


    def _calculate_usage_from_cost(self, start_usage, cost):
        """Calculate usage from cost based on tiered pricing."""
        # Tier thresholds and prices
        t1_limit = self._year_step_config["yearly_step_2_start_volume"] # 400
        t2_limit = self._year_step_config["yearly_step_3_start_volume"] # 1680
        
        p1 = self._year_step_config["year_step_1_price"]
        p2 = self._year_step_config["year_step_2_price"]
        p3 = self._year_step_config["year_step_3_price"]
        
        usage = 0.0
        remaining_cost = cost
        current_base = start_usage
        
        # Max iterations to prevent infinite loop (though unlikely)
        for _ in range(3):
            if remaining_cost <= 0.001:
                break
                
            # Determine current tier
            current_price = 0.0
            remaining_in_tier = float('inf')
            
            if current_base < t1_limit:
                current_price = p1
                remaining_in_tier = t1_limit - current_base
            elif current_base < t2_limit:
                current_price = p2
                remaining_in_tier = t2_limit - current_base
            else:
                current_price = p3
                remaining_in_tier = float('inf')
            
            if current_price <= 0:
                 # Should not happen, but prevent division by zero
                 break

            # Calculate cost to finish this tier
            cost_to_finish_tier = remaining_in_tier * current_price
            
            if remaining_cost <= cost_to_finish_tier:
                # We can cover the remaining cost in this tier
                usage_in_step = remaining_cost / current_price
                usage += usage_in_step
                remaining_cost = 0
                current_base += usage_in_step
            else:
                # We consume the rest of this tier
                usage += remaining_in_tier
                remaining_cost -= cost_to_finish_tier
                current_base += remaining_in_tier
                
        return usage

    def _migrate_historical_data(self):
        """Migrate historical data from Ele keys to Gas keys."""
        try:
            changed = False
            
            # Migrate monthlist
            if "monthlist" in self._attributes and isinstance(self._attributes["monthlist"], list):
                new_monthlist = []
                for item in self._attributes["monthlist"]:
                    if not isinstance(item, dict):
                        continue
                    
                    new_item = item.copy()
                    item_changed = False
                    if "monthEleNum" in new_item:
                        new_item["monthGasNum"] = new_item.pop("monthEleNum")
                        item_changed = True
                    if "monthEleCost" in new_item:
                        new_item["monthGasCost"] = new_item.pop("monthEleCost")
                        item_changed = True
                    
                    new_monthlist.append(new_item)
                    if item_changed:
                        changed = True
                
                if changed:
                    self._attributes["monthlist"] = new_monthlist

            # Migrate daylist
            if "daylist" in self._attributes and isinstance(self._attributes["daylist"], list):
                new_daylist = []
                for item in self._attributes["daylist"]:
                    if not isinstance(item, dict):
                        continue
                        
                    new_item = item.copy()
                    item_changed = False
                    
                    if "dayEleNum" in new_item:
                        new_item["dayGasNum"] = new_item.pop("dayEleNum")
                        item_changed = True
                    if "dayEleCost" in new_item:
                        new_item["dayGasCost"] = new_item.pop("dayEleCost")
                        item_changed = True
                        
                    new_daylist.append(new_item)
                    if item_changed:
                        changed = True
                
                if changed:
                    self._attributes["daylist"] = new_daylist
            
            if changed:
                _LOGGER.info("Migrated historical data from Ele to Gas keys")
                
        except Exception as e:
            _LOGGER.error("Error migrating historical data: %s", e)

    def _calculate_yearly_usage(self):
        """Calculate yearly accumulated gas usage."""
        try:
            current_year_str = self._year_step_config["current_year_step_start_date"][:4] # "2025"
            split_day = self._attributes.get("split_day", 22)
            split_day_str = str(split_day).zfill(2)
            
            # Initialize yearlist if not present
            if "yearlist" not in self._attributes or not isinstance(self._attributes["yearlist"], list):
                self._attributes["yearlist"] = []

            # Get daylist for adjustments
            day_list = self._attributes.get("daylist")
            if not isinstance(day_list, list):
                day_list = []
                
            # Helper to get usage for a date range
            def get_usage_in_range(start_date_str, end_date_str):
                usage = 0.0
                cost = 0.0
                for day_data in day_list:
                    if not isinstance(day_data, dict): continue
                    day = day_data.get("day", "")
                    if start_date_str <= day <= end_date_str:
                        try:
                            # Using stored dayGasNum/Cost
                            u = float(day_data.get("dayGasNum", 0))
                            c = float(day_data.get("dayGasCost", 0))
                            usage += u
                            cost += c
                        except: pass
                return usage, cost
            
            # --- 1. Calculate Historical Years (Non-Current) from monthlist ---
            # Group monthly data by year
            yearly_data_map = {}
            
            month_list = self._attributes.get("monthlist")
            if not isinstance(month_list, list):
                month_list = []

            for month_data in month_list:
                if not isinstance(month_data, dict):
                    continue
                    
                month_str = month_data.get("month", "") # "2025-12"
                if not month_str or len(month_str) < 4:
                    continue
                
                year_str = month_str[:4]
                
                # Initialize year entry if needed
                if year_str not in yearly_data_map:
                    yearly_data_map[year_str] = {"yearGasNum": 0.0, "yearGasCost": 0.0}
                
                try:
                    usage = float(month_data.get("monthGasNum", 0))
                    cost = float(month_data.get("monthGasCost", 0))
                    yearly_data_map[year_str]["yearGasNum"] += usage
                    yearly_data_map[year_str]["yearGasCost"] += cost
                except (ValueError, TypeError):
                    continue

            # Since monthlist is now Natural Month data, we don't need to do split-day adjustments for historical years.
            # The sum of natural months is the yearly total.

            # --- 2. Calculate Current Year Usage (Detailed Logic) ---
            
            # 2.1 Sum up monthly usage for the current year
            current_year_bill_usage = 0.0
            current_year_bill_cost = 0.0
            latest_bill_month_str = ""
            
            for month_data in month_list:
                month_str = month_data.get("month", "")
                if month_str.startswith(current_year_str):
                    try:
                        usage = float(month_data.get("monthGasNum", 0))
                        cost = float(month_data.get("monthGasCost", 0))
                        current_year_bill_usage += usage
                        current_year_bill_cost += cost
                        
                        if month_str > latest_bill_month_str:
                            latest_bill_month_str = month_str
                    except (ValueError, TypeError):
                        continue
            
            # No deduction needed for Jan since monthlist is natural.

            # 2.2 Add daily usage since the end of the last bill month
            running_usage = current_year_bill_usage
            
            target_date_str = ""
            if latest_bill_month_str:
                # If we have bills up to "2025-01", and it's natural month, it covers up to 2025-01-31.
                # So we start counting from 2025-02-01.
                # Calculate next month start date
                try:
                    y = int(latest_bill_month_str[:4])
                    m = int(latest_bill_month_str[5:7])
                    if m == 12:
                        y += 1
                        m = 1
                    else:
                        m += 1
                    target_date_str = f"{y:04d}-{m:02d}-01"
                except:
                    target_date_str = f"{current_year_str}-01-01"
            else:
                # No bills yet for current year, start from Jan 1
                target_date_str = f"{current_year_str}-01-01"
            
            # Note: day_list is already retrieved above
            
            daily_estimated_usage = 0.0
            daily_estimated_cost = 0.0
            
            for day_data in day_list:
                if not isinstance(day_data, dict):
                    continue

                day_str = day_data.get("day", "")
                
                is_relevant_day = False
                if day_str >= target_date_str:
                     is_relevant_day = True

                if is_relevant_day:
                    try:
                        cost = float(day_data.get("dayGasCost", 0))
                        
                        # Calculate usage based on tiered pricing and current running total
                        # Note: We must recalculate usage here to ensure tiered pricing is correct for the running total
                        # This logic updates dayGasNum in daylist if needed (as per previous logic)
                        
                        # Check if we should re-calculate dayGasNum or trust existing?
                        # Previous logic re-calculated it. Let's stick to that to be safe and consistent.
                        day_usage = self._calculate_usage_from_cost(running_usage, cost)
                        
                        # Update daylist with calculated usage using NEW keys
                        day_data["dayGasNum"] = round(day_usage, 2)
                        day_data["dayGasCost"] = cost
                        
                        daily_estimated_usage += day_usage
                        daily_estimated_cost += cost
                        running_usage += day_usage
                        
                    except (ValueError, TypeError):
                        continue
                else:
                    # For older days, just fill missing data if needed, but don't add to current year total
                    # (This part is preserved from previous logic)
                    if "dayGasNum" not in day_data:
                         try:
                            cost = float(day_data.get("dayGasCost", 0))
                            p1 = self._year_step_config["year_step_1_price"]
                            if p1 > 0:
                                day_data["dayGasNum"] = round(cost / p1, 2)
                            day_data["dayGasCost"] = cost
                         except (ValueError, TypeError):
                            pass

            # Total for current year
            total_current_year_usage = current_year_bill_usage + daily_estimated_usage
            total_current_year_cost = current_year_bill_cost + daily_estimated_cost
            
            # Update attribute for accumulated usage
            self._attributes["yearly_step_accumulated_usage"] = round(total_current_year_usage, 2)
            
            # --- 3. Update Yearlist ---
            # Update current year in map with the precise calculated values
            yearly_data_map[current_year_str] = {
                "yearGasNum": total_current_year_usage,
                "yearGasCost": total_current_year_cost
            }
            
            # Convert map to list
            new_yearlist = []
            for year, data in yearly_data_map.items():
                new_yearlist.append({
                    "year": year,
                    "yearGasNum": round(data["yearGasNum"], 2),
                    "yearGasCost": round(data["yearGasCost"], 2)
                })
            
            # Sort yearlist
            new_yearlist.sort(key=lambda x: x["year"])
            self._attributes["yearlist"] = new_yearlist
            
        except Exception as e:
            _LOGGER.error("Error calculating yearly usage: %s", e)

    async def async_added_to_hass(self):
        """Subscribe to MQTT events and restore state."""
        await super().async_added_to_hass()

        # Restore state if available
        last_state = await self.async_get_last_state()
        if last_state:
            self._state = last_state.state
            # Restore attributes if they exist
            if "daylist" in last_state.attributes:
                self._attributes["daylist"] = last_state.attributes["daylist"]
            if "monthlist" in last_state.attributes:
                self._attributes["monthlist"] = last_state.attributes["monthlist"]
            if "yearlist" in last_state.attributes:
                self._attributes["yearlist"] = last_state.attributes["yearlist"]
            if "split_day" in last_state.attributes:
                self._attributes["split_day"] = last_state.attributes["split_day"]
            if "monthly_bill_source_data" in last_state.attributes:
                self._attributes["monthly_bill_source_data"] = last_state.attributes["monthly_bill_source_data"]
            
            # Try to restore internal calculation values from attributes or state
            # We need these to calculate the next day's usage
            if self._state not in (STATE_UNKNOWN, STATE_UNAVAILABLE) and self._state is not None:
                try:
                    self._last_balance = float(self._state)
                except ValueError:
                    self._last_balance = None
            
            # Restore last update time from the entity's last_updated property
            if last_state.last_updated:
                 self._last_update_dt = dt_util.as_local(last_state.last_updated)

        # Initial calculation after restore
        try:
            self._migrate_historical_data()
            self._calculate_natural_month_data()
            self._calculate_yearly_usage()
        except Exception as e:
            _LOGGER.error("Error during initial data migration/calculation: %s", e)
        
        # Schedule an update to ensure migration is persisted
        self.async_schedule_update_ha_state(True)

        @callback
        def message_received(message):
            """Handle new MQTT messages for balance."""
            try:
                payload = json.loads(message.payload)
                
                # Basic validation
                if "status" in payload and payload["status"] != "1":
                    _LOGGER.warning("Gas data status is not 1, ignoring: %s", payload)
                    return
                
                if "data" not in payload:
                    return

                data = payload["data"]
                new_count_money_str = data.get("newCountMoney")
                
                if new_count_money_str is None:
                    return

                try:
                    current_balance = float(new_count_money_str)
                except ValueError:
                    _LOGGER.error("Invalid balance value: %s", new_count_money_str)
                    return

                # Check if data changed
                if self._state is not None and str(current_balance) == str(self._state):
                    # Data didn't change, do nothing
                    return

                now = dt_util.now()
                
                # --- Daily Cost Calculation Logic ---
                # Logic:
                # If last update < Today 07:00 AND current update > Today 07:30
                # Calculate cost = last_balance - current_balance
                # Date = Yesterday
                
                if self._last_balance is not None and self._last_update_dt is not None:
                    today_0am = now.replace(hour=0, minute=0, second=0, microsecond=0)
                    
                    # Check if we crossed the day boundary
                    # Last update was before today 00:00:00
                    if self._last_update_dt < today_0am:
                        calculation_last_balance = self._last_balance
                        
                        # Handle recharge scenario (current > last)
                        if current_balance > calculation_last_balance:
                            # Add 100s until calculation_last_balance > current_balance
                            # We interpret "until higher" as covering the current balance
                            # to ensure usage calculation is correct even if usage is small
                            while calculation_last_balance < current_balance:
                                calculation_last_balance += 100.0

                        # Calculate cost
                        day_gas_cost = calculation_last_balance - current_balance
                        
                        # Only record if positive usage (or zero), assuming balance goes down
                        # Use round to handle float precision
                        day_gas_cost = round(day_gas_cost, 2)

                        # Note: dayGasNum will be calculated in _calculate_yearly_usage
                        # We initialize it here but it will be updated immediately
                        
                        # Date is previous day
                        yesterday = now - timedelta(days=1)
                        day_str = yesterday.strftime("%Y-%m-%d")
                        
                        # Add to daylist
                        new_entry = {
                            "day": day_str,
                            "dayGasCost": day_gas_cost
                        }
                        
                        # Avoid duplicate entries for the same day if logic triggers multiple times
                        if not self._attributes["daylist"] or self._attributes["daylist"][-1]["day"] != day_str:
                            self._attributes["daylist"].append(new_entry)
                            if len(self._attributes["daylist"]) > 365:
                                self._attributes["daylist"].pop(0)

                # Update State
                self._state = current_balance
                self._last_balance = current_balance
                self._last_update_dt = now
                
                # Update other attributes
                self._attributes["cust_name"] = data.get("custName")
                self._attributes["address"] = data.get("address")
                self._attributes["last_record_time"] = data.get("lastRecordTime")
                
                # Recalculate yearly usage whenever data updates
                self._calculate_yearly_usage()
                self._calculate_natural_month_data()
                self._calculate_yearly_usage()
                
                self.async_write_ha_state()

            except json.JSONDecodeError:
                _LOGGER.error("Failed to decode JSON payload: %s", message.payload)
            except Exception as e:
                _LOGGER.error("Error processing gas message: %s", e)

        @callback
        def bill_message_received(message):
            """Handle new MQTT messages for bills."""
            try:
                payload = json.loads(message.payload)
                
                if "status" in payload and payload["status"] != "1":
                    return
                
                if "data" not in payload:
                    return

                data = payload["data"]
                
                # Update split_day based on timeCurRecord
                time_cur_record = data.get("timeCurRecord")
                if time_cur_record and len(str(time_cur_record)) == 8:
                    try:
                        day_str = str(time_cur_record)[6:8]
                        split_day = int(day_str)
                        if 1 <= split_day <= 28: # Basic validation, avoid issues with Feb 29/30/31
                             self._attributes["split_day"] = split_day
                    except ValueError:
                        pass

                pay_gas_check_list = data.get("payGasCheckList", [])

                if not pay_gas_check_list:
                    return

                # Update monthly_bill_source_data
                current_source_list = self._attributes.get("monthly_bill_source_data", [])
                
                # We need to extract timeCurRecord for each bill if possible
                # The payload structure is data["payGasCheckList"] which is a list of bills
                # data["timeCurRecord"] is the LATEST record time.
                # Does each bill have timeCurRecord?
                # User said: "bill's timeCurRecord". So we assume it's in the bill object.
                # If not, we might be in trouble. But let's proceed assuming it is or we can't do the logic.
                
                updated = False
                
                # Sort incoming bills
                pay_gas_check_list.sort(key=lambda x: x.get("recordMonth", ""), reverse=False)

                for bill in pay_gas_check_list:
                    record_month_raw = bill.get("recordMonth") # e.g., "202512"
                    if not record_month_raw or len(record_month_raw) != 6:
                        continue
                    
                    # Try to find existing bill in source data
                    # What is the unique key? recordMonth is usually unique per bill?
                    # But user said "timeCurRecord" might mismatch month.
                    # Let's use recordMonth as primary key for now to update existing.
                    
                    # Format month for consistent storage if needed, but source data should probably keep raw format?
                    # User said "preserve raw server data". So let's keep it raw.
                    
                    existing_entry = next((item for item in current_source_list if item.get("recordMonth") == record_month_raw), None)
                    
                    if existing_entry:
                        # Update if changed
                        if existing_entry != bill:
                            # Update in place
                            existing_entry.update(bill)
                            updated = True
                    else:
                        current_source_list.append(bill)
                        updated = True

                if updated:
                    self._attributes["monthly_bill_source_data"] = current_source_list
                    
                    # Recalculate natural month data
                    self._calculate_natural_month_data()
                    
                    # Recalculate yearly usage
                    self._calculate_yearly_usage()
                    
                    self.async_write_ha_state()

            except json.JSONDecodeError:
                _LOGGER.error("Failed to decode JSON payload for bill: %s", message.payload)
            except Exception as e:
                _LOGGER.error("Error processing bill message: %s", e)

        await mqtt.async_subscribe(
            self.hass, self._topic, message_received, 1
        )
        
        if self._bill_topic:
            await mqtt.async_subscribe(
                self.hass, self._bill_topic, bill_message_received, 1
            )
