import os
import requests
import time
import random
import logging
import paho.mqtt.publish as mqtt_publish
from datetime import datetime, timedelta
from dotenv import load_dotenv
import pytz
import pandas as pd
from entsoe import EntsoePandasClient

# Load environment variables
load_dotenv()

# Configuration
ZAPTEC_API_URL = "https://api.zaptec.com/api/chargers/{charger_id}/settings"
ZAPTEC_API_KEY = os.getenv("ZAPTEC_API_KEY")
CHARGER_ID = os.getenv("ZAPTEC_CHARGER_ID")
ENTSOE_API_KEY = os.getenv("ENTSOE_API_KEY")
MQTT_BROKER = os.getenv("MQTT_BROKER", "192.168.86.54")
WATER_HEATER_TOPIC = "homey/water_heater"

BATTERY_TARGET_KWH = 29  # 50% of a 58 kWh battery
MAX_TOTAL_LOAD = 10000  # Maximum household load in watts
BASE_CHARGING_AMP = 16  # Default charging amperage
CAR_CHARGER_POWER = 3680  # 16A at 230V ~= 3.7 kW
LOCAL_TZ = pytz.timezone("Europe/Oslo")

# Logging setup
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

# Globals
prices = {}
cheapest_schedule = []
current_hour = None


# Fetch day-ahead prices
def fetch_entsoe_prices():
    global prices
    client = EntsoePandasClient(api_key=ENTSOE_API_KEY)
    bidding_zone = '10YNO-2--------T'
    try:
        now = datetime.now(pytz.utc)
        start = pd.Timestamp(now.replace(hour=0, minute=0, second=0))
        end = start + timedelta(days=2)  # Fetch for 36 hours
        prices_series = client.query_day_ahead_prices(bidding_zone, start=start, end=end)
        prices.clear()
        for ts, price in prices_series.items():
            hour = ts.tz_convert(LOCAL_TZ).hour
            day = ts.tz_convert(LOCAL_TZ).day
            prices[f"{day}-{hour}"] = float(price)
        logging.info("Fetched ENTSO-E day-ahead prices successfully.")
    except Exception as e:
        logging.error(f"Error fetching ENTSO-E prices: {e}")


# Plan cheapest charging schedule
def plan_charging_schedule():
    global cheapest_schedule
    kwh_required = BATTERY_TARGET_KWH
    total_hours = int(kwh_required / (CAR_CHARGER_POWER / 1000))  # Estimate required hours

    # Sort prices
    sorted_prices = sorted(prices.items(), key=lambda x: x[1])
    cheapest_schedule = sorted_prices[:total_hours]
    logging.info(f"Planned charging schedule: {cheapest_schedule}")


# Fetch current power usage
def get_current_power_usage(api_base_url="http://192.168.86.34", timeout=5):
    endpoint = f"{api_base_url}/data.json"
    try:
        response = requests.get(endpoint, timeout=timeout)
        response.raise_for_status()
        data = response.json()
        current_power = float(data.get("w", 0.0))
        logging.info(f"Current power usage: {current_power} Watts")
        return current_power
    except Exception as e:
        logging.error(f"Error fetching power usage: {e}")
        return None


# Set Zaptec charging amperage
def set_charging_amperage(amperage):
    url = ZAPTEC_API_URL.format(charger_id=CHARGER_ID)
    headers = {"Authorization": f"Bearer {ZAPTEC_API_KEY}", "Content-Type": "application/json"}
    payload = {"amperage": amperage}
    try:
        requests.put(url, json=payload, headers=headers).raise_for_status()
        logging.info(f"Charging amperage set to {amperage}A successfully.")
    except requests.RequestException as e:
        logging.error(f"Failed to set charging amperage: {e}")


# Control water heater via MQTT
def control_water_heater(state):
    try:
        mqtt_publish.single(WATER_HEATER_TOPIC, payload=state, hostname=MQTT_BROKER)
        logging.info(f"Water heater turned {state}.")
    except Exception as e:
        logging.error(f"Failed to control water heater: {e}")


# Main scheduling and load balancing loop
def main():
    fetch_entsoe_prices()
    plan_charging_schedule()

    while True:
        now = datetime.now(LOCAL_TZ)
        hour_key = f"{now.day}-{now.hour}"
        current_power = get_current_power_usage()

        if hour_key in dict(cheapest_schedule):
            # Calculate available load
            available_capacity = MAX_TOTAL_LOAD - (current_power or 0)
            charging_amp = max(6, min(BASE_CHARGING_AMP, available_capacity // 230))

            # Prioritize car charging and manage water heater
            if available_capacity < 2000:  # Water heater power
                control_water_heater("off")
            else:
                control_water_heater("on")

            set_charging_amperage(charging_amp)
            logging.info(f"Charging active: {charging_amp}A during cheap hour {hour_key}")
        else:
            # Stop charging and ensure water heater is on
            set_charging_amperage(6)
            control_water_heater("on")
            logging.info(f"Not a charging hour. Reducing amperage.")

        # Refresh prices at 2 PM
        if now.hour == 14 and current_hour != 14:
            fetch_entsoe_prices()
            plan_charging_schedule()
            current_hour = 14

        current_hour = now.hour
        time.sleep(60)  # Check every minute


if __name__ == "__main__":
    main()
